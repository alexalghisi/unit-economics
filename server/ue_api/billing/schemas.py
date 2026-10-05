import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from ue_api.billing.meters import Meter, MeterDefinition
from ue_api.billing.pricing import PriceSpec

__all__ = ["Meter", "MeterDefinition"]

Name = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
TokenPrice = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


class ModelPriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Name
    effective_from: datetime
    input_per_mtok: TokenPrice
    output_per_mtok: TokenPrice
    cached_per_mtok: TokenPrice


class ModelPriceOut(ModelPriceIn):
    id: uuid.UUID


class CustomerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: Name
    name: Name
    email: EmailStr | None = None


class CustomerOut(CustomerIn):
    id: uuid.UUID


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    prices: Annotated[list[PriceSpec], Field(min_length=1, max_length=10)]


class ProductOut(ProductIn):
    id: uuid.UUID


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_external_id: Name
    product_id: uuid.UUID
    started_on: date


class SubscriptionOut(SubscriptionIn):
    id: uuid.UUID


class PriceLine(BaseModel):
    kind: str
    meter_id: uuid.UUID | None
    quantity: Decimal
    amount: Decimal


class CurrentPeriod(BaseModel):
    customer_external_id: str
    product_id: uuid.UUID
    period_start: date
    period_end: date
    lines: list[PriceLine]
    total: Decimal
    llm_cost: Decimal
