import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ue_api.billing.pricing import PriceSpec


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    days: Annotated[int, Field(ge=1, le=90)] = 30
    prices: Annotated[list[PriceSpec], Field(min_length=1, max_length=10)]


class SimulatedCustomer(BaseModel):
    customer_id: str
    name: str | None
    cost: Decimal
    revenue_before: Decimal
    revenue_after: Decimal
    revenue_delta: Decimal
    margin_before: Decimal
    margin_after: Decimal
    change: Literal["became_unprofitable", "became_profitable"] | None


class Timings(BaseModel):
    query_ms: float
    pricing_ms: float
    total_ms: float


class SimulationResult(BaseModel):
    product_id: uuid.UUID
    start: date
    end: date
    customers: int
    revenue_before: Decimal
    revenue_after: Decimal
    revenue_delta: Decimal
    margin_before: Decimal
    margin_after: Decimal
    unprofitable_before: int
    unprofitable_after: int
    became_unprofitable: int
    became_profitable: int
    items: list[SimulatedCustomer]
    timings: Timings
