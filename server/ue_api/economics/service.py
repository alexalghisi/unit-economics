import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.billing.service import customer_names
from ue_api.clock import day_start
from ue_api.economics.engine import Cell, Ledger, Window, build_ledger
from ue_api.economics.schemas import (
    Amounts,
    CostPercentiles,
    CustomerDetail,
    CustomerList,
    CustomerMargin,
    DayPoint,
    FeatureMargin,
    ModelUsage,
    Summary,
)
from ue_api.models import CostHistogramDaily
from ue_api.money import ZERO, margin_pct, quantize_display
from ue_api.rollups.histogram import percentiles

CustomerSort = Literal["margin", "-margin", "revenue", "-revenue", "cost", "-cost"]
QUANTILES = (0.5, 0.9, 0.99)


def _amounts(c: Cell) -> dict[str, Any]:
    return {
        "revenue": quantize_display(c.revenue),
        "cost": quantize_display(c.cost),
        "margin": quantize_display(c.margin),
        "margin_pct": margin_pct(c.revenue, c.cost),
        "llm_requests": c.llm_requests,
        "tokens": c.tokens,
    }


def _customer_margin(c: Cell, ledger: Ledger, names: dict[str, str]) -> CustomerMargin:
    plan = ledger.plans.get(c.customer_id)
    return CustomerMargin(
        customer_id=c.customer_id,
        name=names.get(c.customer_id),
        product_id=plan.product_id if plan else None,
        cost_per_request=quantize_display(c.cost / c.llm_requests) if c.llm_requests else None,
        **_amounts(c),
    )


def _ensure_subscribed(ledger: Ledger) -> dict[str, Cell]:
    cells = ledger.by_customer()
    for cid in ledger.plans:
        cells.setdefault(cid, Cell(cid, None))
    return cells


def _sort_key(sort: CustomerSort) -> tuple[str, bool]:
    return sort.removeprefix("-"), sort.startswith("-")


async def customers(
    session: AsyncSession,
    org_id: uuid.UUID,
    window: Window,
    *,
    sort: CustomerSort = "margin",
    limit: int = 50,
    offset: int = 0,
    only_unprofitable: bool = False,
) -> CustomerList:
    ledger = await build_ledger(session, org_id, window)
    names = await customer_names(session, org_id)
    cells = list(_ensure_subscribed(ledger).values())
    if only_unprofitable:
        cells = [c for c in cells if c.margin < ZERO]
    attr, desc = _sort_key(sort)
    cells.sort(key=lambda c: (getattr(c, attr), c.customer_id), reverse=desc)
    page = cells[offset : offset + limit]
    return CustomerList(
        start=window.start,
        end=window.end,
        total=len(cells),
        items=[_customer_margin(c, ledger, names) for c in page],
    )


async def summary(session: AsyncSession, org_id: uuid.UUID, window: Window) -> Summary:
    ledger = await build_ledger(session, org_id, window)
    per_customer = _ensure_subscribed(ledger)
    total = Cell("*", None)
    for c in per_customer.values():
        total.revenue += c.revenue
        total.cost += c.cost
        total.llm_requests += c.llm_requests
        total.tokens += c.tokens
    return Summary(
        start=window.start,
        end=window.end,
        customers=len(per_customer),
        unprofitable_customers=sum(c.margin < ZERO for c in per_customer.values()),
        **_amounts(total),
    )


async def feature_percentiles(
    session: AsyncSession, org_id: uuid.UUID, window: Window
) -> dict[str, CostPercentiles]:
    h = CostHistogramDaily
    rows = await session.execute(
        select(h.feature, h.bucket, func.sum(h.requests))
        .where(h.org_id == org_id, h.day >= window.start, h.day < window.end)
        .group_by(h.feature, h.bucket)
    )
    hist: dict[str, dict[int, int]] = defaultdict(dict)
    for feature, bucket, n in rows:
        hist[feature][bucket] = int(n)
    return {f: _percentiles(counts) for f, counts in hist.items()}


def _percentiles(counts: dict[int, int]) -> CostPercentiles:
    p = percentiles(counts, QUANTILES)
    q = {k: quantize_display(v) if v is not None else None for k, v in p.items()}
    return CostPercentiles(p50=q[0.5], p90=q[0.9], p99=q[0.99])


async def features(session: AsyncSession, org_id: uuid.UUID, window: Window) -> list[FeatureMargin]:
    ledger = await build_ledger(session, org_id, window, "feature")
    pct = await feature_percentiles(session, org_id, window)
    empty = CostPercentiles(p50=None, p90=None, p99=None)
    out = [
        FeatureMargin(feature=str(k or ""), cost_per_request=pct.get(str(k or ""), empty), **_amounts(c))
        for k, c in ledger.by_key().items()
    ]
    return sorted(out, key=lambda f: f.margin)


def _days(ledger: Ledger, window: Window) -> list[DayPoint]:
    by_day = ledger.by_key()
    points = []
    for i in range(window.days):
        d = window.start + timedelta(days=i)
        c = by_day.get(d, Cell("*", d))
        points.append(
            DayPoint(
                day=d,
                revenue=quantize_display(c.revenue),
                cost=quantize_display(c.cost),
                margin=quantize_display(c.margin),
            )
        )
    return points


async def timeseries(session: AsyncSession, org_id: uuid.UUID, window: Window) -> list[DayPoint]:
    return _days(await build_ledger(session, org_id, window, "day"), window)


_EXACT_PERCENTILES = text(
    """
    SELECT percentile_disc(ARRAY[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY cost_usd)
    FROM events
    WHERE org_id = :org_id AND customer_id = :customer_id AND name = 'llm_usage'
      AND timestamp >= :start AND timestamp < :end
    """
)


async def customer_detail(
    session: AsyncSession, org_id: uuid.UUID, customer_id: str, window: Window
) -> CustomerDetail | None:
    by_feature = await build_ledger(session, org_id, window, "feature", customer=customer_id)
    if not by_feature.cells and not by_feature.plans:
        return None
    # The plan is loaded once and shared by the three breakdowns.
    plans = list(by_feature.plans.values())
    by_model = await build_ledger(session, org_id, window, "model", customer=customer_id, plans=plans)
    by_day = await build_ledger(session, org_id, window, "day", customer=customer_id, plans=plans)
    names = await customer_names(session, org_id)
    total = by_feature.by_customer().get(customer_id, Cell(customer_id, None))
    exact = (
        await session.execute(
            _EXACT_PERCENTILES,
            {
                "org_id": org_id,
                "customer_id": customer_id,
                "start": day_start(window.start),
                "end": day_start(window.end),
            },
        )
    ).scalar_one()
    p = [quantize_display(Decimal(v)) for v in exact] if exact else [None, None, None]
    return CustomerDetail(
        start=window.start,
        end=window.end,
        customer=_customer_margin(total, by_feature, names),
        features=sorted(
            (
                FeatureMargin(
                    feature=str(k or ""),
                    cost_per_request=CostPercentiles(p50=None, p90=None, p99=None),
                    **_amounts(c),
                )
                for k, c in by_feature.by_key().items()
            ),
            key=lambda f: f.margin,
        ),
        models=sorted(
            (ModelUsage(model=str(k or ""), **_amounts(c)) for k, c in by_model.by_key().items()),
            key=lambda m: m.cost,
            reverse=True,
        ),
        days=_days(by_day, window),
        cost_per_request=CostPercentiles(p50=p[0], p90=p[1], p99=p[2]),
    )


def amounts_of(c: Cell) -> Amounts:
    return Amounts(**_amounts(c))


def window_for(days: int, today: date) -> Window:
    end = today + timedelta(days=1)
    return Window(start=end - timedelta(days=days), end=end)
