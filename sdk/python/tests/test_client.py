import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from unit_economics import UnitEconomics, Usage, openai_usage


@dataclass
class FakeIngest:
    """An in-memory ingestion endpoint with fault injection and server-side dedup."""

    faults: list[int | type[httpx.TransportError]] = field(default_factory=list)
    retry_after: str | None = None
    stored: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[list[dict[str, Any]]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        events = json.loads(request.content)["events"]
        with self.lock:
            self.requests.append(events)
            if self.faults:
                fault = self.faults.pop(0)
                if isinstance(fault, int):
                    headers = {"retry-after": self.retry_after} if self.retry_after else {}
                    return httpx.Response(fault, headers=headers, text="injected")
                # A timeout after the server already stored the batch: the worst case for retries.
                self._store(events)
                raise fault("injected", request=request)
            return httpx.Response(200, json=self._store(events))

    def _store(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for i, e in enumerate(events):
            if e.get("customer_id") == "":
                results.append(
                    {"index": i, "status": "invalid", "errors": [{"loc": ["customer_id"], "msg": "empty"}]}
                )
            elif e["idempotency_key"] in self.stored:
                results.append({"index": i, "status": "duplicate"})
            else:
                self.stored[e["idempotency_key"]] = e
                results.append({"index": i, "status": "accepted"})

        def count(status: str) -> int:
            return sum(r["status"] == status for r in results)

        return {
            "accepted": count("accepted"),
            "duplicates": count("duplicate"),
            "invalid": count("invalid"),
            "results": results,
        }


def client(fake: FakeIngest, sleeps: list[float] | None = None, **kwargs: Any) -> UnitEconomics:
    record: Callable[[float], None] = sleeps.append if sleeps is not None else lambda _: None
    return UnitEconomics(
        "ue_sk_test", transport=httpx.MockTransport(fake), sleep=record, flush_interval=60, **kwargs
    )


def track(ue: UnitEconomics, n: int, customer: str = "acme") -> None:
    for _ in range(n):
        ue.track_llm(customer_id=customer, feature="chat", model="m", input_tokens=10, output_tokens=5)


def test_events_are_batched_by_size() -> None:
    fake = FakeIngest()
    with client(fake, batch_size=50) as ue:
        track(ue, 120)
        result = ue.flush()
    assert [len(r) for r in fake.requests] == [50, 50, 20]
    assert result.accepted == 120


def test_retries_5xx_with_backoff_and_the_same_keys() -> None:
    fake = FakeIngest(faults=[503, 502])
    sleeps: list[float] = []
    with client(fake, sleeps) as ue:
        track(ue, 10)
        result = ue.flush()
    assert len(fake.requests) == 3
    assert fake.requests[0] == fake.requests[2]  # identical payload, identical idempotency keys
    assert len(sleeps) == 2
    assert result.accepted == 10
    assert result.failed == 0


def test_timeout_after_the_server_stored_the_batch_does_not_double_count() -> None:
    fake = FakeIngest(faults=[httpx.ReadTimeout])
    with client(fake) as ue:
        track(ue, 5)
        result = ue.flush()
    assert len(fake.stored) == 5
    assert (result.accepted, result.duplicates) == (0, 5)


def test_honours_retry_after() -> None:
    fake = FakeIngest(faults=[429], retry_after="7")
    sleeps: list[float] = []
    with client(fake, sleeps) as ue:
        track(ue, 1)
        ue.flush()
    assert sleeps == [7.0]


def test_client_errors_are_not_retried() -> None:
    fake = FakeIngest(faults=[401])
    errors: list[tuple[str, int]] = []
    with client(fake, on_error=lambda msg, events: errors.append((msg, len(events)))) as ue:
        track(ue, 3)
        result = ue.flush()
    assert len(fake.requests) == 1
    assert result.failed == 3
    assert errors[0][0].startswith("HTTP 401")


def test_gives_up_after_max_retries() -> None:
    fake = FakeIngest(faults=[500] * 10)
    with client(fake, max_retries=3) as ue:
        track(ue, 2)
        result = ue.flush()
    assert len(fake.requests) == 4
    assert result.failed == 2


def test_invalid_items_are_reported_with_the_event() -> None:
    fake = FakeIngest()
    errors: list[tuple[str, list[dict[str, Any]]]] = []
    with client(fake, on_error=lambda msg, events: errors.append((msg, events))) as ue:
        track(ue, 1)
        track(ue, 1, customer="")
        result = ue.flush()
    assert result.invalid == 1
    assert errors[0][1][0]["customer_id"] == ""


def test_full_queue_drops_instead_of_blocking() -> None:
    gate = threading.Event()
    fake = FakeIngest()

    def slow(request: httpx.Request) -> httpx.Response:
        gate.wait(5)
        return fake(request)

    ue = UnitEconomics(
        "ue_sk_test", transport=httpx.MockTransport(slow), batch_size=1, max_queue=5, flush_interval=60
    )
    track(ue, 50)
    gate.set()
    result = ue.close()
    assert result.dropped > 0
    assert result.accepted + result.dropped == 50


def test_llm_call_records_usage_latency_and_errors() -> None:
    fake = FakeIngest()
    with client(fake) as ue:
        with ue.llm_call(customer_id="acme", feature="chat", model="m", metadata={"route": "/ask"}) as call:
            call.record(Usage(100, 20, 300))

        def failing_request() -> None:
            with ue.llm_call(customer_id="acme", feature="chat", model="m") as call:
                call.record(Usage(10, 0))
                raise TimeoutError

        with pytest.raises(TimeoutError):
            failing_request()
        with ue.llm_call(customer_id="acme", feature="chat", model="m"):
            pass  # nothing recorded: no event
        ue.flush()
    first, second = fake.requests[0]
    assert (first["input_tokens"], first["cached_tokens"], first["metadata"]["route"]) == (100, 300, "/ask")
    assert first["metadata"]["latency_ms"] >= 0
    assert second["metadata"]["error"] == "TimeoutError"
    assert len(fake.requests[0]) == 2


def test_metered_decorator_with_openai_style_responses() -> None:
    fake = FakeIngest()
    with client(fake) as ue:

        @ue.metered(
            feature="summarize",
            model="gpt-4o-mini",
            customer_id=lambda doc, tenant: tenant,
            usage=openai_usage,
        )
        def summarize(doc: str, tenant: str) -> dict[str, Any]:
            return {
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 80,
                    "prompt_tokens_details": {"cached_tokens": 1000},
                }
            }

        summarize("text", tenant="globex")
        ue.flush()
    event = fake.requests[0][0]
    assert (event["customer_id"], event["input_tokens"], event["cached_tokens"], event["output_tokens"]) == (
        "globex",
        200,
        1000,
        80,
    )


def test_closed_client_refuses_new_events() -> None:
    ue = client(FakeIngest())
    ue.close()
    with pytest.raises(RuntimeError):
        track(ue, 1)
