"""Dramatiq actors. Run with: dramatiq ue_api.workers"""

import logging
import uuid
from collections.abc import Callable

import dramatiq
import redis

from ue_api.broker import broker
from ue_api.clock import utcnow
from ue_api.db import sessionmaker
from ue_api.rollups.scheduler import RedisRollupScheduler
from ue_api.rollups.service import ChunkResult, rollup_chunk
from ue_api.rollups.sql import PENDING_ORGS
from ue_api.settings import get_settings

log = logging.getLogger("ue_api.workers")

_redis: redis.Redis | None = None


def redis_client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_settings().redis_url)
    return _redis


def rollup_scheduler() -> RedisRollupScheduler:
    return RedisRollupScheduler(redis_client(), _enqueue_rollup)


def _enqueue_rollup(org_id: str) -> None:
    rollup_org.send(org_id)


# Test seam: lets tests simulate a worker that crashes after its transaction committed but
# before the broker saw the ack, which is exactly when a message gets delivered twice.
after_chunk_committed: Callable[[uuid.UUID, ChunkResult], None] = lambda org, result: None  # noqa: E731


@dramatiq.actor(queue_name="rollups", max_retries=10, min_backoff=500, max_backoff=60_000)
async def rollup_org(org_id: str) -> None:
    org = uuid.UUID(org_id)
    settings = get_settings()
    # Clear the wake-up flag *before* claiming; see rollups/scheduler.py for why.
    rollup_scheduler().clear(org)
    async with sessionmaker()() as session:
        result = await rollup_chunk(session, org, settings.rollup_chunk_size)
    after_chunk_committed(org, result)
    if result.events:
        log.info("rolled up %d events for %s (oldest day %s)", result.events, org, result.oldest_day)
    if result.events == settings.rollup_chunk_size:
        # More is pending. Re-enqueue instead of looping so other orgs get their turn.
        rollup_org.send(org_id)


@dramatiq.actor(queue_name="rollups", max_retries=3)
async def sweep_pending() -> None:
    """Safety net for wake-ups lost to a crash between commit and enqueue."""
    async with sessionmaker()() as session:
        orgs = (await session.scalars(PENDING_ORGS)).all()
    for org in orgs:
        rollup_org.send(str(org))
