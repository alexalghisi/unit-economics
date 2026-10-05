"""Price definitions and how a period's usage turns into an amount owed.

Everything here is pure and Decimal-only, so the same functions price an invoice, the
dashboard and the simulator.
"""

import uuid
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ue_api.money import ZERO

Amount = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=10)]
PackageSize = Annotated[int, Field(ge=1, le=1_000_000_000)]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FlatPrice(_Spec):
    kind: Literal["flat"] = "flat"
    amount: Amount


class PerUnitPrice(_Spec):
    kind: Literal["per_unit"] = "per_unit"
    meter_id: uuid.UUID
    unit_amount: Amount
    # Price is per `per` units, e.g. unit_amount=4, per=1_000_000 is "$4 per 1M tokens".
    per: PackageSize = 1


class Tier(_Spec):
    up_to: Annotated[Decimal, Field(gt=0)] | None
    unit_amount: Amount
    flat_amount: Amount = ZERO


class TieredPrice(_Spec):
    kind: Literal["tiered"] = "tiered"
    meter_id: uuid.UUID
    mode: Literal["graduated", "volume"]
    tiers: Annotated[list[Tier], Field(min_length=1, max_length=20)]
    per: PackageSize = 1

    @model_validator(mode="after")
    def _tiers_ascend(self) -> Self:
        if self.tiers[-1].up_to is not None:
            raise ValueError("the last tier must be open-ended (up_to: null)")
        bounds = [t.up_to for t in self.tiers[:-1]]
        if any(b is None for b in bounds):
            raise ValueError("only the last tier may be open-ended")
        if any(b is not None and a is not None and b <= a for a, b in pairwise(bounds)):
            raise ValueError("tier bounds must be strictly increasing")
        return self


class CreditsPrice(_Spec):
    """A prepaid allowance: `amount` buys `included_units` per period, overage is metered."""

    kind: Literal["credits"] = "credits"
    meter_id: uuid.UUID
    amount: Amount
    included_units: Annotated[Decimal, Field(ge=0)]
    overage_unit_amount: Amount
    per: PackageSize = 1


PriceSpec = Annotated[FlatPrice | PerUnitPrice | TieredPrice | CreditsPrice, Field(discriminator="kind")]
price_adapter: TypeAdapter[FlatPrice | PerUnitPrice | TieredPrice | CreditsPrice] = TypeAdapter(PriceSpec)
AnyPrice = FlatPrice | PerUnitPrice | TieredPrice | CreditsPrice


def meter_of(price: AnyPrice) -> uuid.UUID | None:
    return None if isinstance(price, FlatPrice) else price.meter_id


def evaluate(price: AnyPrice, quantity: Decimal) -> Decimal:
    """Amount owed for one billing period with `quantity` units of the price's meter."""
    match price:
        case FlatPrice():
            return price.amount
        case PerUnitPrice():
            return quantity * price.unit_amount / price.per
        case CreditsPrice():
            overage = max(ZERO, quantity - price.included_units)
            return price.amount + overage * price.overage_unit_amount / price.per
        case TieredPrice(mode="volume"):
            tier = next(t for t in price.tiers if t.up_to is None or quantity <= t.up_to)
            return tier.flat_amount + quantity * tier.unit_amount / price.per
        case TieredPrice():
            total, lower = ZERO, ZERO
            for tier in price.tiers:
                upper = quantity if tier.up_to is None else min(quantity, tier.up_to)
                in_tier = upper - lower
                if in_tier <= ZERO:
                    break
                total += tier.flat_amount + in_tier * tier.unit_amount / price.per
                if tier.up_to is None or quantity <= tier.up_to:
                    break
                lower = tier.up_to
            return total
    raise AssertionError(f"unhandled price {price!r}")  # pragma: no cover
