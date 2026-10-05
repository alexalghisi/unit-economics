from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.billing.meters import MeterDefinition, from_rollups, quantity_query
from ue_api.clock import utcnow
from ue_api.rollups.service import rollup_chunk

from tests.billing_factories import create_meter
from tests.factories import Org, add_price, days_ago, ingest_events, llm_event, meter_event


async def quantities(session: AsyncSession, org: Org, definition: MeterDefinition) -> dict[str, int]:
    today = utcnow().date()
    query = quantity_query(
        org.id, definition, today - timedelta(days=30), today + timedelta(days=1), ["customer_id"]
    )
    return {row[0]: int(row[1]) for row in await session.execute(query)}


@pytest.fixture
async def usage(session: AsyncSession, org: Org) -> None:
    await add_price(session, org.id, "gpt-small", "1", "1")
    await ingest_events(
        session,
        org.id,
        [
            llm_event(
                "c1", feature="chat", input_tokens=100, output_tokens=10, cached_tokens=5, at=days_ago(2)
            ),
            llm_event("c1", feature="search", input_tokens=50, output_tokens=5, at=days_ago(1)),
            llm_event(
                "c2", feature="chat", input_tokens=1, output_tokens=1, at=days_ago(40)
            ),  # outside window
            meter_event("c1", "document.processed", pages=10, workspace="w1"),
            meter_event("c1", "document.processed", pages=5, workspace="w2"),
            meter_event("c1", "document.processed", pages=2, workspace="w1"),
            meter_event("c2", "document.processed", pages=7, workspace="w9"),
        ],
    )
    await rollup_chunk(session, org.id, 1000)


@pytest.mark.parametrize(
    ("definition", "rollups", "expected"),
    [
        ({"aggregation": "count", "event_name": "llm_usage"}, True, {"c1": 2}),
        (
            {"aggregation": "sum", "event_name": "llm_usage", "property": "total_tokens"},
            True,
            {"c1": 170},
        ),
        (
            {
                "aggregation": "sum",
                "event_name": "llm_usage",
                "property": "input_tokens",
                "filters": [{"field": "feature", "op": "eq", "value": "chat"}],
            },
            True,
            {"c1": 100},
        ),
        (
            {"aggregation": "sum", "event_name": "document.processed", "property": "metadata.pages"},
            False,
            {"c1": 17, "c2": 7},
        ),
        (
            {"aggregation": "unique", "event_name": "document.processed", "property": "metadata.workspace"},
            False,
            {"c1": 2, "c2": 1},
        ),
        (
            {
                "aggregation": "count",
                "event_name": "document.processed",
                "filters": [{"field": "metadata.workspace", "op": "in", "value": ["w1", "w9"]}],
            },
            False,
            {"c1": 2, "c2": 1},
        ),
    ],
)
async def test_meter_quantities(
    session: AsyncSession,
    org: Org,
    usage: None,
    definition: dict[str, object],
    rollups: bool,
    expected: dict[str, int],
) -> None:
    meter = MeterDefinition.model_validate({"slug": "m", "name": "m", **definition})
    assert from_rollups(meter) is rollups
    assert await quantities(session, org, meter) == expected


async def test_rollup_and_raw_paths_agree(session: AsyncSession, org: Org, usage: None) -> None:
    rolled = MeterDefinition(
        slug="a", name="a", event_name="llm_usage", aggregation="sum", property="output_tokens"
    )
    # A no-op customer_id filter pushes the same meter onto the raw-events path.
    raw = MeterDefinition.model_validate(
        {**rolled.model_dump(), "filters": [{"field": "customer_id", "op": "ne", "value": "nobody"}]}
    )
    assert not from_rollups(raw)
    assert await quantities(session, org, rolled) == await quantities(session, org, raw)


@pytest.mark.parametrize(
    "bad",
    [
        {"aggregation": "count", "property": "input_tokens"},
        {"aggregation": "sum"},
        {"aggregation": "sum", "property": "feature"},
        {"aggregation": "count", "filters": [{"field": "metadata.x", "op": "in", "value": "not-a-list"}]},
        {
            "aggregation": "count",
            "filters": [{"field": "metadata.x'; drop table events", "op": "eq", "value": "1"}],
        },
    ],
)
def test_invalid_meters_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MeterDefinition.model_validate({"slug": "m", "name": "m", "event_name": "llm_usage", **bad})


async def test_meter_slugs_are_unique_per_org(session: AsyncSession, org: Org) -> None:
    from ue_api.billing.service import ConflictError

    await create_meter(session, org.id, slug="tokens", name="T", event_name="llm_usage", aggregation="count")
    with pytest.raises(ConflictError):
        await create_meter(
            session, org.id, slug="tokens", name="T", event_name="llm_usage", aggregation="count"
        )
