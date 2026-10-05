import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

import orjson
from pydantic import ValidationError
from sqlalchemy import DateTime, Numeric, Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.costs.pricebook import PriceBook, TokenUsage, token_cost
from ue_api.costs.service import load_pricebook
from ue_api.ingestion.schemas import (
    LLM_USAGE,
    IngestResponse,
    ItemError,
    ItemResult,
    LlmUsageEvent,
    MeterEvent,
    event_adapter,
)
from ue_api.money import ZERO


class RollupScheduler(Protocol):
    def notify(self, org_id: uuid.UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class IngestLimits:
    max_age: timedelta
    max_future_skew: timedelta


@dataclass(slots=True)
class _Row:
    index: int
    key: str
    customer_id: str
    name: str
    feature: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost: Decimal
    price_id: uuid.UUID | None
    metadata: str
    timestamp: datetime


_INSERT = text(
    """
    INSERT INTO events (org_id, idempotency_key, customer_id, name, feature, model, input_tokens,
                        output_tokens, cached_tokens, cost_usd, model_price_id, metadata, timestamp)
    SELECT :org_id, k, c, n, f, m, it, ot, ct, cost, pid, md::jsonb, ts
    FROM unnest(:keys, :customers, :names, :features, :models, :inputs, :outputs, :cached,
                :costs, :price_ids, :metadata, :timestamps)
         AS t(k, c, n, f, m, it, ot, ct, cost, pid, md, ts)
    ON CONFLICT (org_id, idempotency_key) DO NOTHING
    RETURNING idempotency_key
    """
).bindparams(
    bindparam("org_id", type_=UUID(as_uuid=True)),
    bindparam("keys", type_=ARRAY(Text)),
    bindparam("customers", type_=ARRAY(Text)),
    bindparam("names", type_=ARRAY(Text)),
    bindparam("features", type_=ARRAY(Text)),
    bindparam("models", type_=ARRAY(Text)),
    bindparam("inputs", type_=ARRAY(BIGINT)),
    bindparam("outputs", type_=ARRAY(BIGINT)),
    bindparam("cached", type_=ARRAY(BIGINT)),
    bindparam("costs", type_=ARRAY(Numeric(20, 10))),
    bindparam("price_ids", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("metadata", type_=ARRAY(Text)),
    bindparam("timestamps", type_=ARRAY(DateTime(timezone=True))),
)


def _error(loc: list[str | int], msg: str, type_: str) -> ItemError:
    return ItemError(loc=loc, msg=msg, type=type_)


def _validate(
    index: int, raw: dict[str, object], now: datetime, limits: IngestLimits
) -> LlmUsageEvent | MeterEvent | ItemResult:
    try:
        event = event_adapter.validate_python(raw)
    except ValidationError as exc:
        errors = [
            _error([str(p) if not isinstance(p, int) else p for p in e["loc"]], e["msg"], e["type"])
            for e in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        key = raw.get("idempotency_key")
        return ItemResult(
            index=index,
            status="invalid",
            idempotency_key=key if isinstance(key, str) else None,
            errors=errors,
        )
    if event.timestamp - now > limits.max_future_skew:
        return ItemResult(
            index=index,
            status="invalid",
            idempotency_key=event.idempotency_key,
            errors=[_error(["timestamp"], "timestamp is in the future", "timestamp_future")],
        )
    if now - event.timestamp > limits.max_age:
        return ItemResult(
            index=index,
            status="invalid",
            idempotency_key=event.idempotency_key,
            errors=[
                _error(["timestamp"], "timestamp is older than the retention window", "timestamp_too_old")
            ],
        )
    return event


def _to_row(index: int, event: LlmUsageEvent | MeterEvent, book: PriceBook) -> tuple[_Row, list[str]]:
    warnings: list[str] = []
    metadata = orjson.dumps(event.metadata).decode()
    if isinstance(event, LlmUsageEvent):
        version = book.resolve(event.model, event.timestamp)
        if version is None:
            # Usage is never dropped for lack of a price; it is stored unpriced and visible.
            warnings.append(f"no price for model {event.model!r} at {event.timestamp.isoformat()}")
        usage = TokenUsage(event.input_tokens, event.output_tokens, event.cached_tokens)
        return _Row(
            index=index,
            key=event.idempotency_key,
            customer_id=event.customer_id,
            name=LLM_USAGE,
            feature=event.feature,
            model=event.model,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cached_tokens=event.cached_tokens,
            cost=token_cost(version, usage) if version else ZERO,
            price_id=version.id if version else None,
            metadata=metadata,
            timestamp=event.timestamp,
        ), warnings
    return _Row(
        index=index,
        key=event.idempotency_key,
        customer_id=event.customer_id,
        name=event.name,
        feature=event.feature,
        model="",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        cost=ZERO,
        price_id=None,
        metadata=metadata,
        timestamp=event.timestamp,
    ), warnings


async def ingest(
    session: AsyncSession,
    org_id: uuid.UUID,
    raw_events: list[dict[str, object]],
    *,
    now: datetime,
    limits: IngestLimits,
    scheduler: RollupScheduler,
) -> IngestResponse:
    results: dict[int, ItemResult] = {}
    rows: list[_Row] = []
    seen: set[str] = set()
    book = await load_pricebook(session, org_id)

    for index, raw in enumerate(raw_events):
        validated = _validate(index, raw, now, limits)
        if isinstance(validated, ItemResult):
            results[index] = validated
            continue
        if validated.idempotency_key in seen:
            results[index] = ItemResult(
                index=index, status="duplicate", idempotency_key=validated.idempotency_key
            )
            continue
        seen.add(validated.idempotency_key)
        row, warnings = _to_row(index, validated, book)
        rows.append(row)
        results[index] = ItemResult(
            index=index, status="accepted", idempotency_key=row.key, warnings=warnings or None
        )

    if rows:
        inserted = set(
            (
                await session.scalars(
                    _INSERT,
                    {
                        "org_id": org_id,
                        "keys": [r.key for r in rows],
                        "customers": [r.customer_id for r in rows],
                        "names": [r.name for r in rows],
                        "features": [r.feature for r in rows],
                        "models": [r.model for r in rows],
                        "inputs": [r.input_tokens for r in rows],
                        "outputs": [r.output_tokens for r in rows],
                        "cached": [r.cached_tokens for r in rows],
                        "costs": [r.cost for r in rows],
                        "price_ids": [r.price_id for r in rows],
                        "metadata": [r.metadata for r in rows],
                        "timestamps": [r.timestamp for r in rows],
                    },
                )
            ).all()
        )
        await session.commit()
        for r in rows:
            if r.key not in inserted:
                results[r.index] = ItemResult(index=r.index, status="duplicate", idempotency_key=r.key)
        if inserted:
            # After commit, so the rollup worker can always see what it was woken up for.
            scheduler.notify(org_id)

    ordered = [results[i] for i in range(len(raw_events))]
    return IngestResponse(
        accepted=sum(r.status == "accepted" for r in ordered),
        duplicates=sum(r.status == "duplicate" for r in ordered),
        invalid=sum(r.status == "invalid" for r in ordered),
        results=ordered,
    )
