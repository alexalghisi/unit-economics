"""Meters: a filtered aggregation over events, compiled to SQL.

A meter is answered from the daily rollups whenever it can be (count or sum of a token
column, filtered on feature/model); only meters that need event metadata or a distinct
count fall back to scanning raw events.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Date, Numeric, Select, cast, distinct, func, literal, select
from sqlalchemy.sql.elements import ColumnElement, SQLCoreOperations

from ue_api.clock import day_start
from ue_api.models import Event, UsageDaily

Dim = Literal["customer_id", "feature", "model", "day"]
TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cached_tokens", "total_tokens", "cost_usd")
COLUMN_FIELDS = ("feature", "model")
FieldRef = Annotated[
    str, StringConstraints(pattern=r"^(feature|model|customer_id|metadata\.[A-Za-z0-9_-]{1,64})$")
]


class MeterFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: FieldRef
    op: Literal["eq", "ne", "in"]
    value: str | list[str]

    @model_validator(mode="after")
    def _value_shape(self) -> Self:
        if (self.op == "in") != isinstance(self.value, list):
            raise ValueError("`in` takes a list of values, `eq`/`ne` a single value")
        return self


class MeterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    event_name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    aggregation: Literal["count", "sum", "unique"]
    property: (
        FieldRef
        | Literal["input_tokens", "output_tokens", "cached_tokens", "total_tokens", "cost_usd"]
        | None
    ) = None
    filters: Annotated[list[MeterFilter], Field(max_length=10)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _property_matches_aggregation(self) -> Self:
        if self.aggregation == "count" and self.property is not None:
            raise ValueError("count meters take no property")
        if self.aggregation != "count" and self.property is None:
            raise ValueError(f"{self.aggregation} meters need a property")
        if self.aggregation == "sum" and self.property in (*COLUMN_FIELDS, "customer_id"):
            raise ValueError("sum needs a numeric property: a token column, cost_usd or metadata.<key>")
        return self


class Meter(MeterDefinition):
    id: uuid.UUID


def from_rollups(meter: MeterDefinition) -> bool:
    filters_ok = all(f.field in COLUMN_FIELDS for f in meter.filters)
    if meter.aggregation == "count":
        return filters_ok
    return meter.aggregation == "sum" and meter.property in TOKEN_COLUMNS and filters_ok


def _rollup_column(name: str) -> SQLCoreOperations[Any]:
    u = UsageDaily
    if name == "total_tokens":
        return u.input_tokens + u.output_tokens + u.cached_tokens
    return {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cached_tokens": u.cached_tokens,
        "cost_usd": u.cost_usd,
    }[name]


def _event_field(name: str) -> SQLCoreOperations[Any]:
    if name.startswith("metadata."):
        value: SQLCoreOperations[Any] = Event.event_metadata[name.removeprefix("metadata.")].astext
        return value
    return {"feature": Event.feature, "model": Event.model, "customer_id": Event.customer_id}[name]


def _event_number(name: str) -> SQLCoreOperations[Any]:
    if name.startswith("metadata."):
        return cast(Event.event_metadata[name.removeprefix("metadata.")].astext, Numeric)
    if name == "total_tokens":
        return Event.input_tokens + Event.output_tokens + Event.cached_tokens
    return {
        "input_tokens": Event.input_tokens,
        "output_tokens": Event.output_tokens,
        "cached_tokens": Event.cached_tokens,
        "cost_usd": Event.cost_usd,
    }[name]


def _condition(column: SQLCoreOperations[Any], f: MeterFilter) -> ColumnElement[bool]:
    if f.op == "in":
        assert isinstance(f.value, list)
        return column.in_(f.value)
    return column == f.value if f.op == "eq" else column != f.value


def quantity_query(
    org_id: uuid.UUID,
    meter: MeterDefinition,
    start: date,
    end: date,
    dims: Sequence[Dim],
    customer_id: str | None = None,
) -> Select[Any]:
    """Meter quantity per `dims` over the UTC days [start, end). The last column is `quantity`."""
    if from_rollups(meter):
        u = UsageDaily
        dim_cols = [getattr(u, d) for d in dims]
        agg: ColumnElement[Any] = (
            func.sum(u.requests)
            if meter.aggregation == "count"
            else func.sum(_rollup_column(meter.property or ""))
        )
        conditions = [u.org_id == org_id, u.day >= start, u.day < end, u.event_name == meter.event_name]
        conditions += [_condition(getattr(u, f.field), f) for f in meter.filters]
        if customer_id is not None:
            conditions.append(u.customer_id == customer_id)
    else:
        e = Event
        day = cast(func.timezone("UTC", e.timestamp), Date)
        dim_cols = [day if d == "day" else getattr(e, d) for d in dims]
        if meter.aggregation == "count":
            agg = func.count()
        elif meter.aggregation == "sum":
            agg = func.sum(_event_number(meter.property or ""))
        else:
            agg = func.count(distinct(_event_field(meter.property or "")))
        conditions = [
            e.org_id == org_id,
            e.timestamp >= day_start(start),
            e.timestamp < day_start(end),
            e.name == meter.event_name,
        ]
        conditions += [_condition(_event_field(f.field), f) for f in meter.filters]
        if customer_id is not None:
            conditions.append(e.customer_id == customer_id)
    labelled = [c.label(d) for c, d in zip(dim_cols, dims, strict=True)]
    query = select(*labelled, func.coalesce(agg, literal(0)).label("quantity")).where(*conditions)
    return query.group_by(*dim_cols) if dims else query


def usage_query(
    org_id: uuid.UUID, start: date, end: date, dims: Sequence[Dim], customer_id: str | None = None
) -> Select[Any]:
    """LLM cost and request count per `dims` over [start, end), from the rollups."""
    u = UsageDaily
    dim_cols = [getattr(u, d).label(d) for d in dims]
    llm = u.event_name == "llm_usage"
    query = select(
        *dim_cols,
        func.coalesce(func.sum(u.cost_usd), 0).label("cost"),
        func.coalesce(func.sum(u.requests).filter(llm), 0).label("llm_requests"),
        func.coalesce(func.sum(u.requests), 0).label("events"),
        func.coalesce(func.sum(u.input_tokens + u.output_tokens + u.cached_tokens), 0).label("tokens"),
    ).where(u.org_id == org_id, u.day >= start, u.day < end)
    if customer_id is not None:
        query = query.where(u.customer_id == customer_id)
    return query.group_by(*[getattr(u, d) for d in dims]) if dims else query
