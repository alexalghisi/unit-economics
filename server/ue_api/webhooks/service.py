import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import orjson
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.models import WebhookDelivery, WebhookEndpoint
from ue_api.webhooks import signing
from ue_api.webhooks.schemas import DeliveryOut, EndpointCreated, EndpointIn, EndpointOut


class DeliveryFailedError(Exception):
    """A delivery attempt failed and should be retried."""


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    attempts: int
    status_code: int | None = None


async def create_endpoint(session: AsyncSession, org_id: uuid.UUID, endpoint: EndpointIn) -> EndpointCreated:
    row = WebhookEndpoint(
        org_id=org_id,
        url=str(endpoint.url),
        secret=signing.generate_secret(),
        event_types=list(endpoint.event_types),
    )
    session.add(row)
    await session.flush()
    return EndpointCreated(
        id=row.id, url=row.url, event_types=row.event_types, disabled=False, secret=row.secret
    )


async def list_endpoints(session: AsyncSession, org_id: uuid.UUID) -> list[EndpointOut]:
    rows = await session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.org_id == org_id))
    return [EndpointOut(id=r.id, url=r.url, event_types=r.event_types, disabled=r.disabled) for r in rows]


async def list_deliveries(
    session: AsyncSession, org_id: uuid.UUID, endpoint_id: uuid.UUID, limit: int = 50
) -> list[DeliveryOut]:
    rows = await session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.org_id == org_id, WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return [
        DeliveryOut(
            id=r.id,
            event_type=r.event_type,
            status=r.status,
            attempts=r.attempts,
            last_status_code=r.last_status_code,
            last_error=r.last_error,
            created_at=r.created_at,
            delivered_at=r.delivered_at,
        )
        for r in rows
    ]


async def enqueue_event(
    session: AsyncSession, org_id: uuid.UUID, event_type: str, data: dict[str, Any], now: datetime
) -> list[str]:
    """Write one delivery per subscribed endpoint (an outbox); the caller commits, then sends."""
    endpoints = await session.scalars(
        select(WebhookEndpoint).where(
            WebhookEndpoint.org_id == org_id,
            WebhookEndpoint.disabled.is_(False),
            WebhookEndpoint.event_types.contains([event_type]),
        )
    )
    payload = {"type": event_type, "timestamp": now.isoformat(), "data": data}
    ids = []
    for endpoint in endpoints:
        delivery = WebhookDelivery(
            id=f"msg_{uuid.uuid4().hex}",
            org_id=org_id,
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
        )
        session.add(delivery)
        ids.append(delivery.id)
    await session.flush()
    return ids


async def attempt_delivery(
    session: AsyncSession, http: httpx.AsyncClient, delivery_id: str, now: datetime, *, final_attempt: bool
) -> Outcome:
    """Send one delivery. Raises DeliveryFailedError when the attempt failed and may be retried."""
    row = (
        await session.execute(
            select(WebhookDelivery, WebhookEndpoint)
            .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(WebhookDelivery.id == delivery_id)
        )
    ).one_or_none()
    if row is None:
        return Outcome("missing", 0)
    delivery, endpoint = row
    if delivery.status != "pending":
        # Redelivered message for something already settled: at-least-once, not at-most-twice.
        return Outcome(delivery.status, delivery.attempts)
    attempts, url, secret, body = (
        delivery.attempts,
        endpoint.url,
        endpoint.secret,
        orjson.dumps(delivery.payload),
    )
    await session.rollback()  # don't hold a transaction open across the HTTP call

    status_code: int | None = None
    error: str | None = None
    try:
        response = await http.post(url, content=body, headers=signing.headers(secret, delivery_id, now, body))
        status_code = response.status_code
        if not response.is_success:
            error = f"HTTP {status_code}"
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]

    succeeded = error is None
    status = "succeeded" if succeeded else ("failed" if final_attempt else "pending")
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(
            attempts=WebhookDelivery.attempts + 1,
            status=status,
            last_status_code=status_code,
            last_error=error,
            delivered_at=now if succeeded else None,
        )
    )
    await session.commit()
    if status == "pending":
        raise DeliveryFailedError(f"{delivery_id}: {error}")
    return Outcome(status, attempts + 1, status_code)
