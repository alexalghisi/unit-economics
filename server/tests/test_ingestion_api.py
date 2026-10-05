from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.models import Event

from tests.conftest import auth
from tests.factories import Org, RecordingScheduler, add_price, days_ago, llm_event, meter_event, now


async def test_rejects_missing_or_unknown_keys(client: httpx.AsyncClient, org: Org) -> None:
    body = {"events": [llm_event("c1")]}
    assert (await client.post("/v1/events/ingest", json=body)).status_code == 401
    bad = await client.post("/v1/events/ingest", json=body, headers=auth("ue_sk_not-a-real-key"))
    assert bad.status_code == 401


async def test_mixed_batch_reports_each_item(
    client: httpx.AsyncClient, org: Org, session: AsyncSession, scheduler: RecordingScheduler
) -> None:
    await add_price(session, org.id, "gpt-small", "0.15", "0.60")
    events = [
        llm_event("c1", key="a"),
        {**llm_event("c1"), "input_tokens": -5},
        llm_event("c2", key="a"),  # same key later in the batch
        meter_event("c1", "document.processed", pages=12),
        {"kind": "llm_usage", "customer_id": "c1"},
        llm_event("c1", at=now() + timedelta(hours=1)),
    ]
    res = await client.post("/v1/events/ingest", json={"events": events}, headers=auth(org.api_key))
    assert res.status_code == 200
    body = res.json()
    assert [r["status"] for r in body["results"]] == [
        "accepted",
        "invalid",
        "duplicate",
        "accepted",
        "invalid",
        "invalid",
    ]
    assert body["results"][1]["errors"] == [
        {
            "loc": ["llm_usage", "input_tokens"],
            "msg": "Input should be greater than or equal to 0",
            "type": "greater_than_equal",
        }
    ]
    missing = {tuple(e["loc"]) for e in body["results"][4]["errors"]}
    assert ("llm_usage", "idempotency_key") in missing
    assert body["results"][5]["errors"][0]["type"] == "timestamp_future"
    assert (body["accepted"], body["duplicates"], body["invalid"]) == (2, 1, 3)
    assert await session.scalar(select(func.count()).select_from(Event)) == 2
    assert scheduler.notified == [org.id]


async def test_retrying_a_batch_is_idempotent(
    client: httpx.AsyncClient, org: Org, session: AsyncSession, scheduler: RecordingScheduler
) -> None:
    events = [llm_event("c1", key=f"k{i}") for i in range(3)]
    first = await client.post("/v1/events/ingest", json={"events": events}, headers=auth(org.api_key))
    again = await client.post("/v1/events/ingest", json={"events": events}, headers=auth(org.api_key))
    assert first.json()["accepted"] == 3
    assert again.json()["duplicates"] == 3
    assert await session.scalar(select(func.count()).select_from(Event)) == 3
    # Nothing new was written by the retry, so the rollup worker isn't woken for it.
    assert scheduler.notified == [org.id]


async def test_idempotency_keys_are_scoped_per_org(
    client: httpx.AsyncClient, org: Org, session: AsyncSession
) -> None:
    from tests.factories import create_org

    other = await create_org(session, "Other")
    for key in (org.api_key, other.api_key):
        res = await client.post(
            "/v1/events/ingest", json={"events": [llm_event("c1", key="same")]}, headers=auth(key)
        )
        assert res.json()["accepted"] == 1


async def test_cost_is_priced_at_ingestion_with_the_version_in_effect(
    client: httpx.AsyncClient, org: Org, session: AsyncSession
) -> None:
    await add_price(session, org.id, "gpt-small", "1", "2", "0.5", effective_from=days_ago(30))
    # Announced a month ago, effective five days ago.
    await add_price(
        session, org.id, "gpt-small", "10", "20", "5", effective_from=days_ago(5), as_of=days_ago(29)
    )
    events = [
        llm_event("c1", input_tokens=1_000_000, output_tokens=0, at=days_ago(10), key="old"),
        llm_event("c1", input_tokens=1_000_000, output_tokens=0, at=days_ago(1), key="new"),
        llm_event("c1", model="mystery-model", key="unpriced"),
    ]
    res = await client.post("/v1/events/ingest", json={"events": events}, headers=auth(org.api_key))
    assert "no price for model 'mystery-model'" in res.json()["results"][2]["warnings"][0]
    costs = dict((await session.execute(select(Event.idempotency_key, Event.cost_usd))).all())
    assert costs == {"old": Decimal(1), "new": Decimal(10), "unpriced": Decimal(0)}


async def test_events_outside_retention_are_rejected(client: httpx.AsyncClient, org: Org) -> None:
    res = await client.post(
        "/v1/events/ingest", json={"events": [llm_event("c1", at=days_ago(91))]}, headers=auth(org.api_key)
    )
    assert res.json()["results"][0]["errors"][0]["type"] == "timestamp_too_old"


async def test_oversized_batches_are_refused(client: httpx.AsyncClient, org: Org) -> None:
    events = [llm_event("c1") for _ in range(1001)]
    res = await client.post("/v1/events/ingest", json={"events": events}, headers=auth(org.api_key))
    assert res.status_code == 413
