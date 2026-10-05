import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class Amounts(BaseModel):
    revenue: Decimal
    cost: Decimal
    margin: Decimal
    margin_pct: Decimal | None
    llm_requests: int
    tokens: int


class CustomerMargin(Amounts):
    customer_id: str
    name: str | None
    product_id: uuid.UUID | None
    cost_per_request: Decimal | None


class CostPercentiles(BaseModel):
    p50: Decimal | None
    p90: Decimal | None
    p99: Decimal | None


class FeatureMargin(Amounts):
    feature: str
    cost_per_request: CostPercentiles


class ModelUsage(Amounts):
    model: str


class DayPoint(BaseModel):
    day: date
    revenue: Decimal
    cost: Decimal
    margin: Decimal


class Summary(Amounts):
    start: date
    end: date
    customers: int
    unprofitable_customers: int


class CustomerList(BaseModel):
    start: date
    end: date
    total: int
    items: list[CustomerMargin]


class CustomerDetail(BaseModel):
    start: date
    end: date
    customer: CustomerMargin
    features: list[FeatureMargin]
    models: list[ModelUsage]
    days: list[DayPoint]
    cost_per_request: CostPercentiles
