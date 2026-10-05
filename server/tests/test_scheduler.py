import uuid

import redis
from ue_api.rollups.scheduler import RedisRollupScheduler, flag_key


def test_a_burst_of_commits_sends_one_message(redis_client: redis.Redis) -> None:
    sent: list[str] = []
    scheduler = RedisRollupScheduler(redis_client, sent.append)
    org = uuid.uuid4()
    for _ in range(50):
        scheduler.notify(org)
    assert sent == [str(org)]


def test_clearing_the_flag_lets_the_next_commit_wake_the_worker(redis_client: redis.Redis) -> None:
    sent: list[str] = []
    scheduler = RedisRollupScheduler(redis_client, sent.append)
    org = uuid.uuid4()
    scheduler.notify(org)
    scheduler.clear(org)  # what the worker does right before it claims events
    scheduler.notify(org)
    assert sent == [str(org), str(org)]
    assert redis_client.pttl(flag_key(org)) > 0


def test_orgs_are_debounced_independently(redis_client: redis.Redis) -> None:
    sent: list[str] = []
    scheduler = RedisRollupScheduler(redis_client, sent.append)
    a, b = uuid.uuid4(), uuid.uuid4()
    scheduler.notify(a)
    scheduler.notify(b)
    assert sorted(sent) == sorted([str(a), str(b)])
