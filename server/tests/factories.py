"""Test data builders. They go through the same services the API uses."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.auth import create_api_key
from ue_api.costs.service import add_price_version
from ue_api.ingestion.schemas import IngestResponse
from ue_api.ingestion.service import IngestLimits, ingest
from ue_api.models import Organization

LIMITS = IngestLimits(max_age=timedelta(days=90), max_future_skew=timedelta(minutes=5))


@dataclass
class RecordingScheduler:
    notified: list[uuid.UUID] = field(default_factory=list)

    def notify(self, org_id: uuid.UUID) -> None:
        self.notified.append(org_id)


@dataclass(frozen=True)
class Org:
    id: uuid.UUID
    api_key: str


async def create_org(session: AsyncSession, name: str = "Acme AI") -> Org:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    key = await create_api_key(session, org.id, "test")
    await session.commit()
    return Org(id=org.id, api_key=key)


def now() -> datetime:
    return datetime.now(UTC)


def days_ago(n: float) -> datetime:
    return now() - timedelta(days=n)


async def add_price(
    session: AsyncSession,
    org_id: uuid.UUID,
    model: str,
    input_per_mtok: str,
    output_per_mtok: str,
    cached_per_mtok: str = "0",
    effective_from: datetime | None = None,
    as_of: datetime | None = None,
) -> None:
    await add_price_version(
        session,
        org_id,
        model=model,
        effective_from=effective_from or datetime(2026, 1, 1, tzinfo=UTC),
        input_per_mtok=Decimal(input_per_mtok),
        output_per_mtok=Decimal(output_per_mtok),
        cached_per_mtok=Decimal(cached_per_mtok),
        now=as_of or now(),
    )
    await session.commit()


def llm_event(
    customer: str,
    feature: str = "chat",
    model: str = "gpt-small",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cached_tokens: int = 0,
    at: datetime | None = None,
    key: str | None = None,
    **metadata: Any,
) -> dict[str, object]:
    return {
        "kind": "llm_usage",
        "idempotency_key": key or uuid.uuid4().hex,
        "customer_id": customer,
        "feature": feature,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "timestamp": (at or now()).isoformat(),
        "metadata": metadata,
    }


def meter_event(
    customer: str, name: str, at: datetime | None = None, key: str | None = None, **metadata: Any
) -> dict[str, object]:
    return {
        "kind": "event",
        "name": name,
        "idempotency_key": key or uuid.uuid4().hex,
        "customer_id": customer,
        "timestamp": (at or now()).isoformat(),
        "metadata": metadata,
    }


async def ingest_events(
    session: AsyncSession, org_id: uuid.UUID, events: list[dict[str, object]]
) -> IngestResponse:
    return await ingest(session, org_id, events, now=now(), limits=LIMITS, scheduler=RecordingScheduler())
