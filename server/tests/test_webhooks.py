from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import orjson
import pytest
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from standardwebhooks.webhooks import Webhook
from ue_api import workers
from ue_api.billing.pricing import FlatPrice
from ue_api.broker import broker
from ue_api.economics.service import window_for
from ue_api.models import WebhookDelivery
from ue_api.webhooks.alerts import evaluate_margin_alerts
from ue_api.webhooks.schemas import EndpointIn
from ue_api.webhooks.service import create_endpoint

from tests.billing_factories import subscribe
from tests.conftest import auth
from tests.factories import Org
from tests.receiver import webhook_receiver
from tests.test_workers import drain
from tests.world import World, build_world, today


@pytest.fixture
async def world(session: AsyncSession, org: Org) -> World:
    return await build_world(session, org)


@pytest.fixture
def stub_worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubBroker]:
    assert isinstance(broker, StubBroker)
    monkeypatch.setitem(workers.deliver_webhook.options, "min_backoff", 10)
    monkeypatch.setitem(workers.deliver_webhook.options, "max_backoff", 20)
    broker.flush_all()
    worker = Worker(broker, worker_timeout=50, worker_threads=2)
    worker.start()
    yield broker
    worker.stop()
    broker.flush_all()


def now() -> datetime:
    return datetime.now(UTC)


async def test_alerts_fire_once_per_crossing(session: AsyncSession, world: World) -> None:
    endpoint = await create_endpoint(
        session, world.org.id, EndpointIn.model_validate({"url": "https://example.com/hooks"})
    )
    await session.commit()
    window = window_for(30, today())

    first = await evaluate_margin_alerts(session, world.org.id, window, now())
    payloads = {
        d.payload["data"]["customer_id"]: d
        for d in await session.scalars(select(WebhookDelivery).where(WebhookDelivery.id.in_(first)))
    }
    assert set(payloads) == {"heavy", "freebie"}
    heavy = payloads["heavy"]
    assert heavy.event_type == "customer.margin_negative"
    assert heavy.endpoint_id == endpoint.id
    assert heavy.payload["data"]["margin"] == "-20.000000"

    assert await evaluate_margin_alerts(session, world.org.id, window, now()) == []

    # Heavy moves to an enterprise plan that covers its usage.
    await subscribe(
        session, world.org.id, ["heavy"], [FlatPrice(amount=Decimal(500))], product_name="Enterprise"
    )
    recovered = await evaluate_margin_alerts(session, world.org.id, window, now())
    row = await session.get(WebhookDelivery, recovered[0])
    assert row is not None
    assert row.event_type == "customer.margin_recovered"
    assert len(recovered) == 1


async def test_delivery_retries_with_a_stable_id_and_valid_signatures(
    stub_worker: StubBroker, session: AsyncSession, world: World
) -> None:
    with webhook_receiver(fail_first=2) as receiver:
        endpoint = await create_endpoint(
            session, world.org.id, EndpointIn.model_validate({"url": receiver.url})
        )
        await session.commit()
        workers.evaluate_alerts.send(str(world.org.id))
        await drain(stub_worker)

    by_customer: dict[str, list[str]] = {}
    for req in receiver.requests:
        Webhook(endpoint.secret).verify(req.body, req.headers)  # raises on a bad signature
        customer = orjson.loads(req.body)["data"]["customer_id"]
        by_customer.setdefault(customer, []).append(req.headers["webhook-id"])
    assert len(receiver.requests) == 4  # two failures, then one success per customer
    assert all(len(set(ids)) == 1 for ids in by_customer.values())

    deliveries = (await session.scalars(select(WebhookDelivery))).all()
    assert {d.status for d in deliveries} == {"succeeded"}
    assert sum(d.attempts for d in deliveries) == 4


async def test_delivery_gives_up_after_max_retries(
    stub_worker: StubBroker, session: AsyncSession, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ue_api.settings import get_settings

    monkeypatch.setattr(get_settings(), "webhook_max_retries", 2)
    monkeypatch.setitem(workers.deliver_webhook.options, "max_retries", 2)
    with webhook_receiver(always_fail=True) as receiver:
        await create_endpoint(
            session,
            world.org.id,
            EndpointIn.model_validate({"url": receiver.url, "event_types": ["customer.margin_negative"]}),
        )
        await session.commit()
        workers.evaluate_alerts.send(str(world.org.id))
        await drain(stub_worker)

    deliveries = (await session.scalars(select(WebhookDelivery))).all()
    assert len(deliveries) == 2
    assert {(d.status, d.attempts, d.last_status_code) for d in deliveries} == {("failed", 3, 503)}
    assert len(receiver.requests) == 6


async def test_endpoint_api_shows_the_secret_once(client: httpx.AsyncClient, world: World) -> None:
    created = await client.post(
        "/v1/webhooks/endpoints", json={"url": "https://example.com/h"}, headers=auth(world.org.api_key)
    )
    assert created.status_code == 201
    assert created.json()["secret"].startswith("whsec_")
    listed = await client.get("/v1/webhooks/endpoints", headers=auth(world.org.api_key))
    assert "secret" not in listed.json()[0]
    deliveries = await client.get(
        f"/v1/webhooks/endpoints/{created.json()['id']}/deliveries", headers=auth(world.org.api_key)
    )
    assert deliveries.json() == []
