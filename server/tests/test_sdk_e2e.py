"""The Python SDK against the real API served by uvicorn, with a flaky proxy in between."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ue_api.app import create_app
from ue_api.ingestion.router import get_rollup_scheduler
from ue_api.models import Event
from unit_economics import UnitEconomics, Usage

from tests.factories import Org, RecordingScheduler


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def api_url() -> Iterator[str]:
    app = create_app()
    scheduler = RecordingScheduler()
    app.dependency_overrides[get_rollup_scheduler] = lambda: scheduler
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


class DropEveryThirdResponse(httpx.HTTPTransport):
    """Forwards to the real server but loses every third response after it was processed."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        response = super().handle_request(request)
        if self.calls % 3 == 0:
            response.read()
            raise httpx.ReadTimeout("response lost", request=request)
        return response


async def test_sdk_delivers_exactly_once_through_lost_responses(
    api_url: str, org: Org, session: AsyncSession
) -> None:
    transport = DropEveryThirdResponse()

    def run() -> None:
        with UnitEconomics(
            org.api_key, base_url=api_url, batch_size=100, transport=transport, sleep=lambda _: None
        ) as ue:
            for i in range(1_000):
                with ue.llm_call(customer_id=f"c{i % 7}", feature="chat", model="m") as call:
                    call.record(Usage(100, 10))

    await asyncio.to_thread(run)
    assert transport.calls > 10
    assert await session.scalar(select(func.count()).select_from(Event)) == 1_000
