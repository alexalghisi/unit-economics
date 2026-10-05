import uuid
from decimal import Decimal

from ue_api.billing.pricing import CreditsPrice, FlatPrice
from ue_api.economics.engine import allocate, run_rate_amount


def test_allocate_preserves_the_total_exactly() -> None:
    parts = allocate(Decimal(100), {"a": Decimal(1), "b": Decimal(1), "c": Decimal(1)})
    assert sum(parts.values()) == Decimal(100)
    assert abs(parts["a"] - parts["b"]) < Decimal("1e-20")


def test_allocate_skips_zero_weights_and_handles_no_usage() -> None:
    assert allocate(Decimal(10), {"a": Decimal(0), "b": Decimal(3)}) == {"b": Decimal(10)}
    assert allocate(Decimal(10), {"a": Decimal(0)}) == {}


def test_run_rate_equals_the_invoice_for_a_30_day_window() -> None:
    price = CreditsPrice(
        meter_id=uuid.uuid4(), amount=Decimal(20), included_units=Decimal(100), overage_unit_amount=Decimal(1)
    )
    assert run_rate_amount(price, Decimal(150), 30) == Decimal(70)


def test_run_rate_scales_included_credits_with_the_window() -> None:
    price = CreditsPrice(
        meter_id=uuid.uuid4(), amount=Decimal(20), included_units=Decimal(100), overage_unit_amount=Decimal(1)
    )
    # 60 days with 150 units is 75 units a month: inside the allowance, so two months of the fee.
    assert run_rate_amount(price, Decimal(150), 60) == Decimal(40)
    assert run_rate_amount(FlatPrice(amount=Decimal(30)), Decimal(0), 7) == Decimal(7)
