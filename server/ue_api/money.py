"""Decimal helpers. Money and token prices never touch binary floats."""

from decimal import ROUND_HALF_EVEN, Decimal

ZERO = Decimal(0)
MILLION = Decimal(1_000_000)
COST_EXPONENT = Decimal("0.0000000001")  # matches events.cost_usd numeric(20, 10)
DISPLAY_EXPONENT = Decimal("0.000001")


def quantize_cost(value: Decimal) -> Decimal:
    return value.quantize(COST_EXPONENT, rounding=ROUND_HALF_EVEN)


def quantize_display(value: Decimal) -> Decimal:
    return value.quantize(DISPLAY_EXPONENT, rounding=ROUND_HALF_EVEN)


def margin_pct(revenue: Decimal, cost: Decimal) -> Decimal | None:
    if revenue == ZERO:
        return None
    return ((revenue - cost) / revenue * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
