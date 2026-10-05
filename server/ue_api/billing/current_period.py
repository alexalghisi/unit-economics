import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.billing.meters import quantity_query, usage_query
from ue_api.billing.periods import current_period
from ue_api.billing.pricing import evaluate, meter_of
from ue_api.billing.schemas import CurrentPeriod, PriceLine
from ue_api.billing.service import load_plans, meters_by_id
from ue_api.money import ZERO, quantize_display


async def current_period_revenue(
    session: AsyncSession, org_id: uuid.UUID, customer_external_id: str, today: date
) -> CurrentPeriod | None:
    """What the customer owes so far this billing period, priced exactly as the invoice will be."""
    plans = await load_plans(session, org_id, customer_external_id)
    if not plans:
        return None
    plan = plans[0]
    start, end = current_period(plan.started_on, today)
    until = min(end, today + timedelta(days=1))
    meters = await meters_by_id(session, org_id)
    lines: list[PriceLine] = []
    for price in plan.prices:
        meter_id = meter_of(price)
        quantity = ZERO
        if meter_id is not None:
            query = quantity_query(org_id, meters[meter_id], start, until, [], customer_external_id)
            quantity = Decimal((await session.execute(query)).scalar_one())
        lines.append(
            PriceLine(
                kind=price.kind,
                meter_id=meter_id,
                quantity=quantity,
                amount=quantize_display(evaluate(price, quantity)),
            )
        )
    usage = (await session.execute(usage_query(org_id, start, until, [], customer_external_id))).one()
    return CurrentPeriod(
        customer_external_id=customer_external_id,
        product_id=plan.product_id,
        period_start=start,
        period_end=end,
        lines=lines,
        total=sum((line.amount for line in lines), ZERO),
        llm_cost=quantize_display(Decimal(usage.cost)),
    )
