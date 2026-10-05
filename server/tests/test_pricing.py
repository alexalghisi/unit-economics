import uuid
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError
from ue_api.billing.pricing import (
    CreditsPrice,
    FlatPrice,
    PerUnitPrice,
    Tier,
    TieredPrice,
    evaluate,
    price_adapter,
)

METER = uuid.uuid4()
D = Decimal


def tiered(mode: Literal["graduated", "volume"]) -> TieredPrice:
    return TieredPrice(
        meter_id=METER,
        mode=mode,
        tiers=[
            Tier(up_to=D(1000), unit_amount=D("0.10")),
            Tier(up_to=D(5000), unit_amount=D("0.08"), flat_amount=D(5)),
            Tier(up_to=None, unit_amount=D("0.05")),
        ],
    )


def test_flat_ignores_usage() -> None:
    assert evaluate(FlatPrice(amount=D("49")), D(10**9)) == D(49)


def test_per_unit_with_package_size() -> None:
    price = PerUnitPrice(meter_id=METER, unit_amount=D("4"), per=1_000_000)
    assert evaluate(price, D(2_500_000)) == D(10)


def test_graduated_tiers_charge_each_slice_at_its_rate() -> None:
    price = tiered("graduated")
    assert evaluate(price, D(0)) == D(0)
    assert evaluate(price, D(1000)) == D(100)
    # 1000 * 0.10 + (5 + 4000 * 0.08) + 1000 * 0.05
    assert evaluate(price, D(6000)) == D(100) + D(325) + D(50)


def test_volume_tiers_charge_everything_at_the_reached_rate() -> None:
    price = tiered("volume")
    assert evaluate(price, D(1000)) == D(100)
    assert evaluate(price, D(1001)) == D(5) + D("80.08")
    assert evaluate(price, D(6000)) == D(300)


def test_credits_include_units_then_bill_overage() -> None:
    price = CreditsPrice(
        meter_id=METER, amount=D(20), included_units=D(1_000_000), overage_unit_amount=D(3), per=1_000_000
    )
    assert evaluate(price, D(400_000)) == D(20)
    assert evaluate(price, D(1_500_000)) == D("21.5")


def test_amounts_stay_exact_decimals() -> None:
    price = PerUnitPrice(meter_id=METER, unit_amount=D("0.1"))
    total = sum((evaluate(price, D(1)) for _ in range(10)), D(0))
    assert total == D("1.0")  # 0.1 * 10 in binary floats is 0.9999999999999999


@pytest.mark.parametrize(
    "tiers",
    [
        [{"up_to": 100, "unit_amount": "1"}],
        [{"up_to": None, "unit_amount": "1"}, {"up_to": None, "unit_amount": "1"}],
        [
            {"up_to": 100, "unit_amount": "1"},
            {"up_to": 50, "unit_amount": "1"},
            {"up_to": None, "unit_amount": "1"},
        ],
    ],
)
def test_invalid_tier_layouts_are_rejected(tiers: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        price_adapter.validate_python(
            {"kind": "tiered", "meter_id": str(METER), "mode": "graduated", "tiers": tiers}
        )


def test_negative_amounts_are_rejected() -> None:
    with pytest.raises(ValidationError):
        price_adapter.validate_python({"kind": "flat", "amount": "-1"})


def test_specs_round_trip_through_json() -> None:
    price = tiered("graduated")
    assert price_adapter.validate_python(price.model_dump(mode="json")) == price
