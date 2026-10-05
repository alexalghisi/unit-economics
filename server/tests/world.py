"""A small, hand-checkable account used by the economics, simulator and webhook tests.

Product "Pro": $30 flat + $5 per 1M tokens. Over the last 30 days:
  light   1M gpt-small input tokens on chat      cost  $1   revenue $35   margin  +$34
  heavy   10M gpt-large input tokens on agent    cost $100  revenue $80   margin  -$20
  idle    no usage                               cost  $0   revenue $30   margin  +$30
  freebie 250k gpt-small tokens, no subscription cost  $0.25 revenue $0   margin  -$0.25
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.billing.pricing import FlatPrice, PerUnitPrice
from ue_api.clock import utcnow
from ue_api.rollups.service import rollup_chunk

from tests.billing_factories import subscribe, tokens_meter
from tests.factories import Org, add_price, days_ago, ingest_events, llm_event


@dataclass(frozen=True)
class World:
    org: Org
    product_id: uuid.UUID
    meter_id: uuid.UUID


def pro_prices(meter_id: uuid.UUID, per_mtok: str = "5") -> list[FlatPrice | PerUnitPrice]:
    return [
        FlatPrice(amount=Decimal(30)),
        PerUnitPrice(meter_id=meter_id, unit_amount=Decimal(per_mtok), per=1_000_000),
    ]


async def build_world(session: AsyncSession, org: Org) -> World:
    await add_price(session, org.id, "gpt-small", "1", "2")
    await add_price(session, org.id, "gpt-large", "10", "30")
    meter = await tokens_meter(session, org.id)
    started = utcnow().date() - timedelta(days=10)
    product = await subscribe(
        session, org.id, ["light", "heavy", "idle"], list(pro_prices(meter.id)), started_on=started
    )
    events = [
        *(llm_event("light", "chat", "gpt-small", 100_000, 0, at=days_ago(i + 0.5)) for i in range(10)),
        *(llm_event("heavy", "agent", "gpt-large", 1_000_000, 0, at=days_ago(i + 0.5)) for i in range(10)),
        llm_event("freebie", "chat", "gpt-small", 250_000, 0, at=days_ago(3)),
        # Older than the 30-day window: must not count.
        llm_event("heavy", "agent", "gpt-large", 5_000_000, 0, at=days_ago(45)),
    ]
    await ingest_events(session, org.id, events)
    await rollup_chunk(session, org.id, 10_000)
    return World(org=org, product_id=product, meter_id=meter.id)


def today() -> date:
    return utcnow().date()
