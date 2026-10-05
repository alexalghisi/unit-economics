import asyncio
import random
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.db import sessionmaker
from ue_api.models import CostHistogramDaily, Event, UsageDaily
from ue_api.rollups.service import rollup_chunk

from tests.factories import Org, add_price, days_ago, ingest_events, llm_event, meter_event

TRUTH = text(
    """
    SELECT (timestamp AT TIME ZONE 'UTC')::date AS day, customer_id, name, feature, model,
           count(*) AS requests, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
           sum(cached_tokens) AS cached_tokens, sum(cost_usd) AS cost_usd
    FROM events GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5
    """
)
ROLLED = text(
    """
    SELECT day, customer_id, event_name, feature, model, requests, input_tokens, output_tokens,
           cached_tokens, cost_usd
    FROM usage_daily ORDER BY 1, 2, 3, 4, 5
    """
)


def random_events(n: int, seed: int = 1) -> list[dict[str, object]]:
    rng = random.Random(seed)
    out: list[dict[str, object]] = []
    for _ in range(n):
        at = days_ago(rng.uniform(0, 20))
        customer = f"c{rng.randint(1, 8)}"
        if rng.random() < 0.15:
            out.append(meter_event(customer, "document.processed", at=at, pages=rng.randint(1, 30)))
        else:
            out.append(
                llm_event(
                    customer,
                    feature=rng.choice(["chat", "search", "summarize"]),
                    model=rng.choice(["gpt-small", "gpt-large"]),
                    input_tokens=rng.randint(10, 20_000),
                    output_tokens=rng.randint(0, 4_000),
                    cached_tokens=rng.randint(0, 5_000),
                    at=at,
                )
            )
    return out


async def seed(session: AsyncSession, org: Org, n: int, seed_: int = 1) -> None:
    await add_price(session, org.id, "gpt-small", "0.15", "0.60", "0.075")
    await add_price(session, org.id, "gpt-large", "2.50", "10", "1.25")
    events = random_events(n, seed_)
    for i in range(0, n, 500):
        await ingest_events(session, org.id, events[i : i + 500])


async def assert_rollups_match_events(session: AsyncSession) -> None:
    truth = (await session.execute(TRUTH)).all()
    rolled = (await session.execute(ROLLED)).all()
    assert [tuple(r) for r in rolled] == [tuple(r) for r in truth]


async def test_rollups_equal_a_full_recount(session: AsyncSession, org: Org) -> None:
    await seed(session, org, 2_000)
    result = await rollup_chunk(session, org.id, limit=10_000)
    assert result.events == 2_000
    await assert_rollups_match_events(session)
    hist_total = await session.scalar(select(func.sum(CostHistogramDaily.requests)))
    llm_events = await session.scalar(
        select(func.count()).select_from(Event).where(Event.name == "llm_usage")
    )
    assert hist_total == llm_events


async def test_running_again_counts_nothing_twice(session: AsyncSession, org: Org) -> None:
    await seed(session, org, 500)
    await rollup_chunk(session, org.id, limit=10_000)
    again = await rollup_chunk(session, org.id, limit=10_000)
    assert again.events == 0
    await assert_rollups_match_events(session)


async def test_chunks_drain_the_backlog_exactly(session: AsyncSession, org: Org) -> None:
    await seed(session, org, 1_000)
    sizes = []
    while (r := await rollup_chunk(session, org.id, limit=300)).events:
        sizes.append(r.events)
    assert sizes == [300, 300, 300, 100]
    await assert_rollups_match_events(session)


async def test_late_events_land_in_their_own_day(session: AsyncSession, org: Org) -> None:
    await add_price(session, org.id, "gpt-small", "1", "1")
    await ingest_events(
        session, org.id, [llm_event("c1", at=days_ago(0.01), input_tokens=100, output_tokens=0)]
    )
    await rollup_chunk(session, org.id, limit=100)
    late_day = days_ago(12)
    await ingest_events(session, org.id, [llm_event("c1", at=late_day, input_tokens=7, output_tokens=0)])
    result = await rollup_chunk(session, org.id, limit=100)
    assert result.oldest_day == late_day.date()
    row = (await session.execute(select(UsageDaily).where(UsageDaily.day == late_day.date()))).scalar_one()
    assert (row.requests, row.input_tokens) == (1, 7)
    await assert_rollups_match_events(session)


async def test_concurrent_workers_share_the_backlog_without_double_counting(
    session: AsyncSession, org: Org
) -> None:
    await seed(session, org, 3_000, seed_=3)

    async def worker() -> int:
        total = 0
        async with sessionmaker()() as s:
            while (r := await rollup_chunk(s, org.id, limit=250)).events:
                total += r.events
        return total

    counts = await asyncio.gather(*(worker() for _ in range(4)))
    assert sum(counts) == 3_000
    assert sum(1 for c in counts if c > 0) > 1  # the work really was shared
    await assert_rollups_match_events(session)


async def test_rollup_is_scoped_to_one_org(session: AsyncSession, org: Org) -> None:
    from tests.factories import create_org

    other = await create_org(session, "Other")
    await ingest_events(session, org.id, [llm_event("c1")])
    await ingest_events(session, other.id, [llm_event("c1")])
    assert (await rollup_chunk(session, org.id, 100)).events == 1
    pending = await session.scalar(
        select(func.count()).select_from(Event).where(Event.rolled_up_at.is_(None))
    )
    assert pending == 1


async def test_cost_sums_stay_exact(session: AsyncSession, org: Org) -> None:
    await add_price(session, org.id, "m", "0.1", "0")
    await ingest_events(
        session, org.id, [llm_event("c1", model="m", input_tokens=1, output_tokens=0) for _ in range(10)]
    )
    await rollup_chunk(session, org.id, 100)
    assert await session.scalar(select(UsageDaily.cost_usd)) == Decimal("0.0000010000")
