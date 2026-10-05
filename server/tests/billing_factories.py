import uuid
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.billing import service as billing
from ue_api.billing.meters import Meter, MeterDefinition
from ue_api.billing.pricing import AnyPrice
from ue_api.billing.schemas import CustomerIn, ProductIn, SubscriptionIn


async def create_meter(session: AsyncSession, org_id: uuid.UUID, **definition: Any) -> Meter:
    meter = await billing.create_meter(session, org_id, MeterDefinition.model_validate(definition))
    await session.commit()
    return meter


async def tokens_meter(session: AsyncSession, org_id: uuid.UUID) -> Meter:
    return await create_meter(
        session,
        org_id,
        slug="tokens",
        name="Tokens",
        event_name="llm_usage",
        aggregation="sum",
        property="total_tokens",
    )


async def subscribe(
    session: AsyncSession,
    org_id: uuid.UUID,
    customers: list[str],
    prices: list[AnyPrice],
    started_on: date | None = None,
    product_name: str = "Pro",
) -> uuid.UUID:
    product = await billing.create_product(session, org_id, ProductIn(name=product_name, prices=prices))
    for cid in customers:
        await billing.upsert_customer(session, org_id, CustomerIn(external_id=cid, name=cid.title()))
        await billing.create_subscription(
            session,
            org_id,
            SubscriptionIn(
                customer_external_id=cid, product_id=product.id, started_on=started_on or date(2026, 1, 1)
            ),
        )
    await session.commit()
    return product.id
