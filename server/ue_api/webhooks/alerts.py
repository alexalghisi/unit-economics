"""Margin alerts: emit a webhook when a customer's margin crosses zero, in either direction.

The last known sign per customer is stored, so a customer who stays unprofitable is
reported once, not on every rollup.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.economics.engine import Window, build_ledger
from ue_api.models import MarginAlertState
from ue_api.money import ZERO, quantize_display
from ue_api.webhooks.service import enqueue_event


async def evaluate_margin_alerts(
    session: AsyncSession, org_id: uuid.UUID, window: Window, now: datetime
) -> list[str]:
    ledger = await build_ledger(session, org_id, window)
    known = {
        r.customer_id: r.negative
        for r in await session.scalars(select(MarginAlertState).where(MarginAlertState.org_id == org_id))
    }
    delivery_ids: list[str] = []
    changed: list[dict[str, object]] = []
    for cid, cell in sorted(ledger.by_customer().items()):
        negative = cell.margin < ZERO
        if known.get(cid, False) == negative:
            continue
        changed.append({"org_id": org_id, "customer_id": cid, "negative": negative, "updated_at": now})
        event_type = "customer.margin_negative" if negative else "customer.margin_recovered"
        data = {
            "customer_id": cid,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "revenue": str(quantize_display(cell.revenue)),
            "llm_cost": str(quantize_display(cell.cost)),
            "margin": str(quantize_display(cell.margin)),
        }
        delivery_ids += await enqueue_event(session, org_id, event_type, data, now)
    if changed:
        stmt = insert(MarginAlertState).values(changed)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[MarginAlertState.org_id, MarginAlertState.customer_id],
                set_={"negative": stmt.excluded.negative, "updated_at": stmt.excluded.updated_at},
            )
        )
    await session.commit()
    return delivery_ids
