import os

# Must be set before ue_api is imported: the broker is chosen at import time.
os.environ.setdefault("UE_BROKER", "stub")
os.environ.setdefault("UE_DATABASE_URL", "postgresql+asyncpg://synckit:synckit@localhost:5432/po_ue_test")
os.environ.setdefault("UE_REDIS_URL", "redis://localhost:6379/14")
os.environ.setdefault("UE_REDIS_NAMESPACE", "ue-test")

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.app import create_app
from ue_api.db import get_engine, sessionmaker
from ue_api.ingestion.router import get_rollup_scheduler
from ue_api.settings import get_settings

from tests.factories import Org, RecordingScheduler, create_org

SERVER_DIR = Path(__file__).resolve().parents[1]
TABLES = (
    "events, usage_daily, cost_histogram_daily, margin_alert_state, webhook_deliveries, webhook_endpoints, "
    "subscriptions, prices, products, meters, model_prices, customers, api_keys, organizations"
)


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = get_settings().database_url
    # Alembic's env.py runs its own event loop; give it a thread of its own.
    import asyncio

    await asyncio.to_thread(command.upgrade, cfg, "head")


@pytest.fixture(autouse=True)
async def _clean(_schema: None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(get_settings().redis_url)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as s:
        yield s


@pytest.fixture
async def org(session: AsyncSession) -> Org:
    return await create_org(session)


@pytest.fixture
def scheduler() -> RecordingScheduler:
    return RecordingScheduler()


@pytest.fixture
async def client(scheduler: RecordingScheduler) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_rollup_scheduler] = lambda: scheduler
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def auth(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


@pytest.fixture
def org_id(org: Org) -> uuid.UUID:
    return org.id
