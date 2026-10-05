"""Dramatiq broker setup. Import this before any module that declares actors."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware.asyncio import AsyncIO

from ue_api.settings import get_settings


def _build() -> dramatiq.Broker:
    settings = get_settings()
    broker: dramatiq.Broker
    if settings.broker == "stub":
        broker = StubBroker()
        broker.emit_after("process_boot")
    else:
        broker = RedisBroker(url=settings.redis_url, namespace=settings.redis_namespace)  # type: ignore[no-untyped-call]
    broker.add_middleware(AsyncIO())
    return broker


broker = _build()
dramatiq.set_broker(broker)
