"""Revenue, LLM cost and margin per customer, optionally broken down by feature or day.

Usage is aggregated in SQL from the daily rollups (a few hundred to a few thousand rows
whatever the event volume); prices are then evaluated per customer in Python with
Decimal. Nothing here iterates over raw events.

Revenue over a window is a 30-day run rate: each customer's meter quantity is scaled to
30 days, priced like an invoice would be (so tiers and included credits behave as they do
on a real bill), and scaled back to the window. For a 30-day window that is exactly the
invoice amount.

Breakdowns allocate each price line's revenue in proportion to that line's meter
quantity in each feature/day; flat fees follow the customer's event count. Cost needs no
allocation, it is recorded per event.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.billing.meters import Dim, Meter, quantity_query, usage_query
from ue_api.billing.pricing import AnyPrice, evaluate, meter_of
from ue_api.billing.service import CustomerPlan, load_plans, meters_by_id
from ue_api.money import ZERO

RUN_RATE_DAYS = Decimal(30)
Breakdown = Literal["feature", "model", "day"]
Key = str | date | None


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(slots=True)
class Cell:
    customer_id: str
    key: Key
    revenue: Decimal = ZERO
    cost: Decimal = ZERO
    llm_requests: int = 0
    events: int = 0
    tokens: int = 0

    @property
    def margin(self) -> Decimal:
        return self.revenue - self.cost


@dataclass(slots=True)
class Ledger:
    cells: dict[tuple[str, Key], Cell] = field(default_factory=dict)
    plans: dict[str, CustomerPlan] = field(default_factory=dict)

    def cell(self, customer_id: str, key: Key) -> Cell:
        found = self.cells.get((customer_id, key))
        if found is None:
            found = self.cells[(customer_id, key)] = Cell(customer_id, key)
        return found

    def by_customer(self) -> dict[str, Cell]:
        out: dict[str, Cell] = {}
        for c in self.cells.values():
            _add(out.setdefault(c.customer_id, Cell(c.customer_id, None)), c)
        return out

    def by_key(self) -> dict[Key, Cell]:
        out: dict[Key, Cell] = {}
        for c in self.cells.values():
            _add(out.setdefault(c.key, Cell("*", c.key)), c)
        return out


def _add(acc: Cell, c: Cell) -> None:
    acc.revenue += c.revenue
    acc.cost += c.cost
    acc.llm_requests += c.llm_requests
    acc.events += c.events
    acc.tokens += c.tokens


def run_rate_amount(price: AnyPrice, quantity: Decimal, days: int) -> Decimal:
    scale = RUN_RATE_DAYS / days
    return evaluate(price, quantity * scale) / scale


def allocate[K](amount: Decimal, weights: Mapping[K, Decimal]) -> dict[K, Decimal]:
    """Split `amount` proportionally to `weights`; the parts always add up to `amount`."""
    total = sum(weights.values(), ZERO)
    if not weights or total <= ZERO:
        return {}
    parts = {k: amount * w / total for k, w in weights.items() if w > ZERO}
    largest = max(parts, key=lambda k: weights[k])
    parts[largest] += amount - sum(parts.values(), ZERO)
    return parts


@dataclass(slots=True)
class UsageData:
    """Aggregated usage for a window: what pricing needs, already out of the database."""

    breakdown: Breakdown | None
    quantities: dict[uuid.UUID, dict[str, dict[Key, Decimal]]]
    usage: dict[tuple[str, Key], Cell]
    event_weights: dict[str, dict[Key, Decimal]]


async def load_usage(
    session: AsyncSession,
    org_id: uuid.UUID,
    window: Window,
    breakdown: Breakdown | None,
    meters: Mapping[uuid.UUID, Meter],
    meter_ids: Iterable[uuid.UUID],
    customer: str | None = None,
) -> UsageData:
    dims: list[Dim] = ["customer_id"]
    if breakdown:
        dims.append(breakdown)
    quantities: dict[uuid.UUID, dict[str, dict[Key, Decimal]]] = {}
    for meter_id in set(meter_ids):
        per_customer: dict[str, dict[Key, Decimal]] = defaultdict(dict)
        query = quantity_query(org_id, meters[meter_id], window.start, window.end, dims, customer)
        for row in await session.execute(query):
            per_customer[row[0]][row[1] if breakdown else None] = Decimal(row[-1])
        quantities[meter_id] = per_customer

    usage: dict[tuple[str, Key], Cell] = {}
    event_weights: dict[str, dict[Key, Decimal]] = defaultdict(dict)
    for row in await session.execute(usage_query(org_id, window.start, window.end, dims, customer)):
        key: Key = row[1] if breakdown else None
        usage[(row[0], key)] = Cell(
            row[0],
            key,
            cost=Decimal(row.cost),
            llm_requests=int(row.llm_requests),
            events=int(row.events),
            tokens=int(row.tokens),
        )
        event_weights[row[0]][key] = Decimal(row.events)
    return UsageData(breakdown, quantities, usage, event_weights)


def price_usage(
    data: UsageData,
    plans: Sequence[CustomerPlan],
    window: Window,
    price_overrides: Mapping[uuid.UUID, Sequence[AnyPrice]] | None = None,
) -> Ledger:
    """Pure: turn aggregated usage into revenue per customer (and per breakdown key)."""
    overrides = price_overrides or {}
    ledger = Ledger(plans={p.customer_external_id: p for p in plans})
    for k, u in data.usage.items():
        ledger.cells[k] = Cell(u.customer_id, u.key, ZERO, u.cost, u.llm_requests, u.events, u.tokens)
    for plan in plans:
        cid = plan.customer_external_id
        for price in overrides.get(plan.product_id, plan.prices):
            meter_id = meter_of(price)
            weights = data.quantities[meter_id].get(cid, {}) if meter_id else data.event_weights.get(cid, {})
            amount = run_rate_amount(price, sum(weights.values(), ZERO) if meter_id else ZERO, window.days)
            if amount == ZERO:
                continue
            parts: Mapping[Key, Decimal] = allocate(amount, weights) if data.breakdown else {None: amount}
            if not parts:
                # Revenue with nothing to attribute it to, e.g. a flat fee on an idle account.
                parts = {window.start if data.breakdown == "day" else "": amount}
            for key, part in parts.items():
                ledger.cell(cid, key).revenue += part
    return ledger


def meters_needed(
    plans: Iterable[CustomerPlan], price_overrides: Mapping[uuid.UUID, Sequence[AnyPrice]] | None = None
) -> set[uuid.UUID]:
    overrides = price_overrides or {}
    return {
        m
        for p in plans
        for price in overrides.get(p.product_id, p.prices)
        if (m := meter_of(price)) is not None
    }


async def build_ledger(
    session: AsyncSession,
    org_id: uuid.UUID,
    window: Window,
    breakdown: Breakdown | None = None,
    *,
    customer: str | None = None,
    plans: Sequence[CustomerPlan] | None = None,
) -> Ledger:
    if plans is None:
        plans = await load_plans(session, org_id, customer)
    meters = await meters_by_id(session, org_id)
    data = await load_usage(session, org_id, window, breakdown, meters, meters_needed(plans), customer)
    return price_usage(data, plans, window)
