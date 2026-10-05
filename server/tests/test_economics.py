from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.billing.current_period import current_period_revenue
from ue_api.economics import service
from ue_api.economics.service import window_for

from tests.conftest import auth
from tests.factories import Org
from tests.world import World, build_world, today

D = Decimal


@pytest.fixture
async def world(session: AsyncSession, org: Org) -> World:
    return await build_world(session, org)


async def test_margin_per_customer(session: AsyncSession, world: World) -> None:
    result = await service.customers(session, world.org.id, window_for(30, today()))
    rows = {c.customer_id: (c.revenue, c.cost, c.margin) for c in result.items}
    assert rows == {
        "heavy": (D(80), D(100), D(-20)),
        "freebie": (D(0), D("0.25"), D("-0.25")),
        "idle": (D(30), D(0), D(30)),
        "light": (D(35), D(1), D(34)),
    }
    assert [c.customer_id for c in result.items] == ["heavy", "freebie", "idle", "light"]
    heavy = result.items[0]
    assert heavy.margin_pct == D("-25.00")
    assert heavy.cost_per_request == D(10)


async def test_unprofitable_customers(client: httpx.AsyncClient, world: World) -> None:
    res = await client.get("/v1/economics/customers/unprofitable?days=30", headers=auth(world.org.api_key))
    assert [c["customer_id"] for c in res.json()["items"]] == ["heavy", "freebie"]
    assert res.json()["items"][0]["margin"] == "-20.000000"


async def test_margin_per_feature_allocates_revenue_by_usage(session: AsyncSession, world: World) -> None:
    features = {f.feature: f for f in await service.features(session, world.org.id, window_for(30, today()))}
    assert (features["agent"].revenue, features["agent"].cost) == (D(80), D(100))
    assert (features["chat"].revenue, features["chat"].cost) == (D(35), D("1.25"))
    # The idle customer's flat fee has no usage to follow.
    assert features[""].revenue == D(30)
    assert features["agent"].cost_per_request.p50 is not None
    assert abs(features["agent"].cost_per_request.p50 - D(10)) / D(10) < D("0.06")


async def test_breakdowns_add_up_to_the_summary(session: AsyncSession, world: World) -> None:
    window = window_for(30, today())
    summary = await service.summary(session, world.org.id, window)
    days = await service.timeseries(session, world.org.id, window)
    features = await service.features(session, world.org.id, window)
    assert len(days) == 30
    assert (summary.revenue, summary.cost) == (D(145), D("101.25"))
    assert sum(d.revenue for d in days) == summary.revenue
    assert sum(d.cost for d in days) == summary.cost
    assert sum(f.revenue for f in features) == summary.revenue
    assert summary.unprofitable_customers == 2


async def test_shorter_windows_use_a_run_rate(session: AsyncSession, world: World) -> None:
    result = await service.customers(session, world.org.id, window_for(15, today()))
    idle = next(c for c in result.items if c.customer_id == "idle")
    assert idle.revenue == D(15)  # half a month of the flat fee


async def test_customer_drill_down(client: httpx.AsyncClient, world: World) -> None:
    res = await client.get("/v1/economics/customers/heavy?days=30", headers=auth(world.org.api_key))
    body = res.json()
    assert body["customer"]["margin"] == "-20.000000"
    assert [m["model"] for m in body["models"]] == ["gpt-large"]
    assert [f["feature"] for f in body["features"]] == ["agent"]
    assert body["cost_per_request"]["p50"] == "10.000000"
    assert sum(D(d["cost"]) for d in body["days"]) == D(100)
    missing = await client.get("/v1/economics/customers/nobody", headers=auth(world.org.api_key))
    assert missing.status_code == 404


async def test_current_period_revenue_is_priced_like_the_invoice(session: AsyncSession, world: World) -> None:
    period = await current_period_revenue(session, world.org.id, "heavy", today())
    assert period is not None
    assert period.period_start == today() - timedelta(days=10)
    assert [line.amount for line in period.lines] == [D(30), D(50)]
    assert period.total == D(80)
    assert period.llm_cost == D(100)


async def test_endpoints_are_scoped_to_the_callers_org(
    client: httpx.AsyncClient, session: AsyncSession, world: World
) -> None:
    from tests.factories import create_org

    other = await create_org(session, "Other")
    res = await client.get("/v1/economics/customers?days=30", headers=auth(other.api_key))
    assert res.json()["items"] == []
