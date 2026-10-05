"""Versioned LLM token prices and per-event cost computation."""

import bisect
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ue_api.money import MILLION, quantize_cost


@dataclass(frozen=True, slots=True)
class PriceVersion:
    id: uuid.UUID
    model: str
    effective_from: datetime
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_per_mtok: Decimal


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int


def token_cost(version: PriceVersion, usage: TokenUsage) -> Decimal:
    """Cost of one request. `input_tokens` excludes cached tokens, which bill at their own rate."""
    total = (
        version.input_per_mtok * usage.input_tokens
        + version.output_per_mtok * usage.output_tokens
        + version.cached_per_mtok * usage.cached_tokens
    )
    return quantize_cost(total / MILLION)


@dataclass(slots=True)
class PriceBook:
    """All price versions of an organization, indexed for "price in effect at time t"."""

    _starts: dict[str, list[datetime]] = field(default_factory=dict)
    _versions: dict[str, list[PriceVersion]] = field(default_factory=dict)

    @classmethod
    def of(cls, versions: Iterable[PriceVersion]) -> "PriceBook":
        book = cls()
        for v in sorted(versions, key=lambda v: (v.model, v.effective_from)):
            book._starts.setdefault(v.model, []).append(v.effective_from)
            book._versions.setdefault(v.model, []).append(v)
        return book

    def resolve(self, model: str, at: datetime) -> PriceVersion | None:
        starts = self._starts.get(model)
        if not starts:
            return None
        i = bisect.bisect_right(starts, at) - 1
        return self._versions[model][i] if i >= 0 else None

    def latest(self, model: str) -> PriceVersion | None:
        versions = self._versions.get(model)
        return versions[-1] if versions else None
