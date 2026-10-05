import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.billing.pricing import AnyPrice, CreditsPrice, FlatPrice, Tier, TieredPrice
from ue_api.economics.service import window_for
from ue_api.simulator.service import simulate

from tests.conftest import auth
from tests.factories import Org
from tests.world import World, build_world, pro_prices, today

D = Decimal


@pytest.fixture
async def world(session: AsyncSession, org: Org) -> World:
    return await build_world(session, org)


async def run(
    session: AsyncSession, world: World, prices: list[AnyPrice]
) -> dict[str, tuple[Decimal, Decimal]]:
    result = await simulate(session, world.org.id, world.product_id, prices, window_for(30, today()))
    return {i.customer_id: (i.revenue_after, i.margin_after) for i in result.items}


async def test_current_prices_change_nothing(session: AsyncSession, world: World) -> None:
    result = await simulate(
        session, world.org.id, world.product_id, list(pro_prices(world.meter_id)), window_for(30, today())
    )
    assert result.revenue_delta == 0
    assert (result.became_profitable, result.became_unprofitable) == (0, 0)
    assert result.unprofitable_before == result.unprofitable_after == 1
    # Only the product's subscribers are in scope; "freebie" has no subscription.
    assert {i.customer_id for i in result.items} == {"light", "heavy", "idle"}


async def test_raising_the_unit_price_flips_the_heavy_user(session: AsyncSession, world: World) -> None:
    result = await simulate(
        session,
        world.org.id,
        world.product_id,
        list(pro_prices(world.meter_id, "15")),
        window_for(30, today()),
    )
    rows = {i.customer_id: i for i in result.items}
    assert rows["heavy"].revenue_after == D(180)
    assert rows["heavy"].change == "became_profitable"
    assert rows["light"].revenue_delta == D(10)
    assert result.became_profitable == 1
    assert result.revenue_delta == D(110)


async def test_included_credits(session: AsyncSession, world: World) -> None:
    credits = CreditsPrice(
        meter_id=world.meter_id,
        amount=D(30),
        included_units=D(5_000_000),
        overage_unit_amount=D(12),
        per=1_000_000,
    )
    assert await run(session, world, [credits]) == {
        "light": (D(30), D(29)),
        "heavy": (D(90), D(-10)),
        "idle": (D(30), D(30)),
    }


async def test_graduated_tier(session: AsyncSession, world: World) -> None:
    tiered = TieredPrice(
        meter_id=world.meter_id,
        mode="graduated",
        per=1_000_000,
        tiers=[Tier(up_to=D(2_000_000), unit_amount=D(20)), Tier(up_to=None, unit_amount=D(12))],
    )
    rows = await run(session, world, [FlatPrice(amount=D(0)), tiered])
    assert rows["light"] == (D(20), D(19))
    assert rows["heavy"] == (D(40 + 96), D(36))


async def test_simulation_endpoint(client: httpx.AsyncClient, world: World) -> None:
    body = {
        "product_id": str(world.product_id),
        "days": 30,
        "prices": [
            {"kind": "flat", "amount": "30"},
            {"kind": "per_unit", "meter_id": str(world.meter_id), "unit_amount": "15", "per": 1_000_000},
        ],
    }
    res = await client.post("/v1/simulations", json=body, headers=auth(world.org.api_key))
    assert res.status_code == 200
    out = res.json()
    assert out["became_profitable"] == 1
    assert out["revenue_delta"] == "110.000000"
    assert set(out["timings"]) == {"query_ms", "pricing_ms", "total_ms"}

    body["prices"] = [{"kind": "per_unit", "meter_id": str(uuid.uuid4()), "unit_amount": "1"}]
    unknown = await client.post("/v1/simulations", json=body, headers=auth(world.org.api_key))
    assert unknown.status_code == 422
    assert "unknown meter" in unknown.json()["detail"]
