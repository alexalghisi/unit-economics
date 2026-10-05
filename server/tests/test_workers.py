"""Actors on Dramatiq's StubBroker with a real worker, real Postgres and real Redis."""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
import redis
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api import workers
from ue_api.broker import broker
from ue_api.ingestion.service import ingest
from ue_api.models import Event
from ue_api.rollups.scheduler import flag_key
from ue_api.rollups.service import ChunkResult
from ue_api.settings import get_settings

from tests.factories import LIMITS, Org, add_price, ingest_events, llm_event, now
from tests.test_rollups import assert_rollups_match_events, random_events


@pytest.fixture
def stub_worker(redis_client: redis.Redis) -> Iterator[StubBroker]:
    assert isinstance(broker, StubBroker)
    broker.flush_all()
    worker = Worker(broker, worker_timeout=50, worker_threads=2)
    worker.start()
    yield broker
    worker.stop()
    broker.flush_all()


async def drain(stub: StubBroker) -> None:
    for _ in range(3):  # actors enqueue follow-ups on other queues
        for queue in list(stub.queues):
            await asyncio.to_thread(stub.join, queue, fail_fast=True)


async def pending(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count()).select_from(Event).where(Event.rolled_up_at.is_(None)))
    return count or 0


async def test_ingestion_through_the_real_scheduler_gets_rolled_up(
    stub_worker: StubBroker, session: AsyncSession, org: Org, redis_client: redis.Redis
) -> None:
    scheduler = workers.rollup_scheduler()
    for _ in range(5):
        await ingest(session, org.id, [llm_event("c1")], now=now(), limits=LIMITS, scheduler=scheduler)
    await drain(stub_worker)
    assert await pending(session) == 0
    assert redis_client.get(flag_key(org.id)) is None


async def test_a_full_chunk_re_enqueues_until_the_backlog_is_gone(
    stub_worker: StubBroker, session: AsyncSession, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rollup_chunk_size", 100)
    await add_price(session, org.id, "gpt-small", "0.15", "0.60")
    await add_price(session, org.id, "gpt-large", "2.50", "10")
    events = random_events(450)
    for i in range(0, 450, 150):
        await ingest_events(session, org.id, events[i : i + 150])
    workers.rollup_org.send(str(org.id))
    await drain(stub_worker)
    assert await pending(session) == 0
    await assert_rollups_match_events(session)


async def test_redelivery_after_a_crash_past_commit_does_not_double_count(
    stub_worker: StubBroker, session: AsyncSession, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker commits, then dies before acking. The broker redelivers the message."""
    await add_price(session, org.id, "gpt-small", "0.15", "0.60")
    await add_price(session, org.id, "gpt-large", "2.50", "10")
    await ingest_events(session, org.id, random_events(300))
    crashes: list[ChunkResult] = []

    def crash_once(org_id: uuid.UUID, result: ChunkResult) -> None:
        if not crashes:
            crashes.append(result)
            raise ConnectionResetError("worker lost its broker connection after commit")

    monkeypatch.setattr(workers, "after_chunk_committed", crash_once)
    monkeypatch.setitem(workers.rollup_org.options, "min_backoff", 10)
    monkeypatch.setitem(workers.rollup_org.options, "max_backoff", 20)
    workers.rollup_org.send(str(org.id))
    await drain(stub_worker)
    assert len(crashes) == 1
    assert crashes[0].events == 300  # the first attempt did commit its work
    await assert_rollups_match_events(session)


async def test_duplicate_messages_are_harmless(
    stub_worker: StubBroker, session: AsyncSession, org: Org
) -> None:
    await add_price(session, org.id, "gpt-small", "0.15", "0.60")
    await add_price(session, org.id, "gpt-large", "2.50", "10")
    await ingest_events(session, org.id, random_events(400, seed=9))
    for _ in range(5):
        workers.rollup_org.send(str(org.id))
    await drain(stub_worker)
    await assert_rollups_match_events(session)


async def test_sweep_catches_events_whose_wake_up_was_lost(
    stub_worker: StubBroker, session: AsyncSession, org: Org
) -> None:
    await ingest_events(
        session, org.id, [llm_event("c1"), llm_event("c2")]
    )  # RecordingScheduler: nothing sent
    assert await pending(session) == 2
    workers.sweep_pending.send()
    await drain(stub_worker)
    assert await pending(session) == 0
