from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth
from tests.factories import Org, add_price, days_ago, now


async def test_backdated_price_changes_are_refused(
    client: httpx.AsyncClient, org: Org, session: AsyncSession
) -> None:
    await add_price(session, org.id, "gpt-small", "1", "2", effective_from=days_ago(30))
    body = {
        "model": "gpt-small",
        "effective_from": days_ago(1).isoformat(),
        "input_per_mtok": "0.5",
        "output_per_mtok": "1",
        "cached_per_mtok": "0",
    }
    res = await client.post("/v1/model-prices", json=body, headers=auth(org.api_key))
    assert res.status_code == 409
    body["effective_from"] = (now() + timedelta(days=1)).isoformat()
    assert (await client.post("/v1/model-prices", json=body, headers=auth(org.api_key))).status_code == 201


async def test_price_versions_are_listed_in_order(
    client: httpx.AsyncClient, org: Org, session: AsyncSession
) -> None:
    await add_price(session, org.id, "gpt-small", "1", "2", effective_from=days_ago(30))
    await add_price(session, org.id, "gpt-small", "3", "4", effective_from=now() + timedelta(days=3))
    res = await client.get("/v1/model-prices", headers=auth(org.api_key))
    assert [p["input_per_mtok"] for p in res.json()] == ["1.000000", "3.000000"]
