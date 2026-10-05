"""End to end on the production broker: Redis, a real `dramatiq` worker process, Postgres."""

import asyncio
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis
from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.ingestion.service import ingest
from ue_api.models import Event
from ue_api.rollups.scheduler import RedisRollupScheduler
from ue_api.settings import get_settings

from tests.factories import LIMITS, Org, add_price, now
from tests.test_rollups import assert_rollups_match_events, random_events

pytestmark = pytest.mark.integration
SERVER_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def redis_broker(redis_client: redis.Redis) -> Iterator[tuple[RedisBroker, str]]:
    namespace = f"ue-it-{uuid.uuid4().hex[:8]}"
    env = {
        **os.environ,
        "UE_BROKER": "redis",
        "UE_REDIS_NAMESPACE": namespace,
        "PYTHONPATH": str(SERVER_DIR),
    }
    dramatiq_bin = Path(sys.executable).parent / "dramatiq"
    proc = subprocess.Popen(
        [str(dramatiq_bin), "ue_api.workers", "--processes", "1", "--threads", "2"],
        env=env,
        cwd=SERVER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    broker = RedisBroker(url=get_settings().redis_url, namespace=namespace)  # type: ignore[no-untyped-call]
    try:
        yield broker, namespace
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        broker.close()


def enqueue_rollup(broker: RedisBroker, org_id: str) -> None:
    # The test process has its actors on the stub broker, so it talks to Redis with a raw message,
    # the same bytes `rollup_org.send` would produce.
    broker.enqueue(
        Message(queue_name="rollups", actor_name="rollup_org", args=(org_id,), kwargs={}, options={})
    )


async def wait_until_drained(session: AsyncSession, limit_s: float = 45) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < limit_s:
        left = await session.scalar(
            select(func.count()).select_from(Event).where(Event.rolled_up_at.is_(None))
        )
        if left == 0:
            return time.perf_counter() - started
        await asyncio.sleep(0.1)
    raise AssertionError(f"{left} events still pending after {limit_s}s")


async def test_worker_process_rolls_up_with_duplicate_deliveries(
    redis_broker: tuple[RedisBroker, str], redis_client: redis.Redis, session: AsyncSession, org: Org
) -> None:
    broker, _ = redis_broker
    await add_price(session, org.id, "gpt-small", "0.15", "0.60")
    await add_price(session, org.id, "gpt-large", "2.50", "10")
    scheduler = RedisRollupScheduler(redis_client, lambda org_id: enqueue_rollup(broker, org_id))
    events = random_events(1_500, seed=21)
    for i in range(0, len(events), 100):
        await ingest(session, org.id, events[i : i + 100], now=now(), limits=LIMITS, scheduler=scheduler)
        enqueue_rollup(broker, str(org.id))  # an extra, redundant delivery for every batch
    await wait_until_drained(session)
    await assert_rollups_match_events(session)
