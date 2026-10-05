"""Pricing what-ifs: replay a window of real usage under candidate prices.

The window's usage is aggregated once, in SQL, for every meter either price list needs;
both the current and the candidate prices are then evaluated on those aggregates. Cost
does not depend on pricing, so it is shared by both sides.
"""

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.billing.pricing import AnyPrice
from ue_api.billing.service import (
    NotFoundError,
    customer_names,
    load_plans,
    meters_by_id,
    validate_meter_refs,
)
from ue_api.economics.engine import Cell, Window, load_usage, meters_needed, price_usage
from ue_api.money import ZERO, quantize_display
from ue_api.simulator.schemas import SimulatedCustomer, SimulationResult, Timings


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 2)


async def simulate(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, prices: list[AnyPrice], window: Window
) -> SimulationResult:
    started = time.perf_counter()
    await validate_meter_refs(session, org_id, prices)
    plans = [p for p in await load_plans(session, org_id) if p.product_id == product_id]
    if not plans:
        raise NotFoundError(f"no active subscriptions on product {product_id}")
    candidate = {product_id: prices}
    meters = await meters_by_id(session, org_id)
    needed = meters_needed(plans) | meters_needed(plans, candidate)
    data = await load_usage(session, org_id, window, None, meters, needed)
    names = await customer_names(session, org_id)
    queried = time.perf_counter()

    before = price_usage(data, plans, window).by_customer()
    after = price_usage(data, plans, window, candidate).by_customer()
    items: list[SimulatedCustomer] = []
    for plan in plans:
        cid = plan.customer_external_id
        b = before.get(cid, Cell(cid, None))
        a = after.get(cid, Cell(cid, None))
        change = None
        if b.margin >= ZERO > a.margin:
            change = "became_unprofitable"
        elif b.margin < ZERO <= a.margin:
            change = "became_profitable"
        items.append(
            SimulatedCustomer(
                customer_id=cid,
                name=names.get(cid),
                cost=quantize_display(b.cost),
                revenue_before=quantize_display(b.revenue),
                revenue_after=quantize_display(a.revenue),
                revenue_delta=quantize_display(a.revenue - b.revenue),
                margin_before=quantize_display(b.margin),
                margin_after=quantize_display(a.margin),
                change=change,
            )
        )
    items.sort(key=lambda i: (i.margin_after, i.customer_id))
    priced = time.perf_counter()

    revenue_before = sum((i.revenue_before for i in items), ZERO)
    revenue_after = sum((i.revenue_after for i in items), ZERO)
    return SimulationResult(
        product_id=product_id,
        start=window.start,
        end=window.end,
        customers=len(items),
        revenue_before=revenue_before,
        revenue_after=revenue_after,
        revenue_delta=revenue_after - revenue_before,
        margin_before=sum((i.margin_before for i in items), ZERO),
        margin_after=sum((i.margin_after for i in items), ZERO),
        unprofitable_before=sum(i.margin_before < ZERO for i in items),
        unprofitable_after=sum(i.margin_after < ZERO for i in items),
        became_unprofitable=sum(i.change == "became_unprofitable" for i in items),
        became_profitable=sum(i.change == "became_profitable" for i in items),
        items=items,
        timings=Timings(
            query_ms=round((queried - started) * 1000, 2),
            pricing_ms=round((priced - queried) * 1000, 2),
            total_ms=_ms(started),
        ),
    )
