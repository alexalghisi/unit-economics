from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from ue_api.billing.router import router as billing
from ue_api.db import dispose_engines
from ue_api.economics.router import router as economics
from ue_api.ingestion.router import router as ingestion
from ue_api.simulator.router import router as simulator
from ue_api.webhooks.router import router as webhooks


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engines()


def create_app() -> FastAPI:
    app = FastAPI(title="unit-economics", version="0.1.0", lifespan=lifespan)
    v1 = APIRouter(prefix="/v1")
    for router in (ingestion, billing, economics, simulator, webhooks):
        v1.include_router(router)
    app.include_router(v1)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
