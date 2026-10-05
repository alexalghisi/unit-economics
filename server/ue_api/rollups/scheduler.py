"""Wakes the rollup worker after ingestion without one message per request.

Ingestion commits, then sets `rollup:pending:<org>` with NX and only enqueues a message
if it was the one to set it. The worker deletes the flag *before* claiming events. Any
event committed before the delete is visible to the claim that follows it; any event
committed after it finds the flag gone and enqueues a fresh message. So under load there
is at most one message in flight per org, and no wake-up is ever lost.
"""

import uuid
from collections.abc import Callable

import redis

FLAG_TTL_MS = 60_000


def flag_key(org_id: uuid.UUID | str) -> str:
    return f"ue:rollup:pending:{org_id}"


class RedisRollupScheduler:
    def __init__(self, client: redis.Redis, enqueue: Callable[[str], None]) -> None:
        self._redis = client
        self._enqueue = enqueue

    def notify(self, org_id: uuid.UUID) -> None:
        # The TTL only matters if a worker dies between the flag and the claim; the
        # periodic sweep covers that case anyway.
        if self._redis.set(flag_key(org_id), b"1", nx=True, px=FLAG_TTL_MS):
            self._enqueue(str(org_id))

    def clear(self, org_id: uuid.UUID | str) -> None:
        self._redis.delete(flag_key(org_id))
