import uuid
from datetime import UTC, datetime
from decimal import Decimal

from ue_api.costs.pricebook import PriceBook, PriceVersion, TokenUsage, token_cost


def version(model: str, start: datetime, inp: str, out: str, cached: str = "0") -> PriceVersion:
    return PriceVersion(uuid.uuid4(), model, start, Decimal(inp), Decimal(out), Decimal(cached))


JAN = datetime(2026, 1, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)


def test_resolves_the_version_in_effect_at_the_event_time() -> None:
    old, new = version("m", JAN, "3", "15"), version("m", MAR, "2", "10")
    book = PriceBook.of([new, old])
    assert book.resolve("m", datetime(2026, 2, 1, tzinfo=UTC)) is old
    assert book.resolve("m", MAR) is new
    assert book.resolve("m", datetime(2025, 12, 31, tzinfo=UTC)) is None
    assert book.resolve("unknown", MAR) is None
    assert book.latest("m") is new


def test_token_cost_bills_cached_tokens_at_their_own_rate() -> None:
    v = version("m", JAN, "3", "15", "0.30")
    cost = token_cost(v, TokenUsage(input_tokens=1_000, output_tokens=200, cached_tokens=10_000))
    # (3 * 1000 + 15 * 200 + 0.3 * 10000) / 1e6
    assert cost == Decimal("0.0090000000")


def test_token_cost_keeps_sub_cent_precision() -> None:
    v = version("m", JAN, "0.15", "0.60")
    assert token_cost(v, TokenUsage(1, 1, 0)) == Decimal("0.0000007500")
