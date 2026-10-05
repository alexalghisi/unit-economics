from __future__ import annotations

import functools
import logging
import queue
import random
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, ParamSpec, TypeVar

import httpx

from unit_economics.usage import LlmCall, Usage

log = logging.getLogger("unit_economics")

P = ParamSpec("P")
R = TypeVar("R")
Event = dict[str, Any]
Metadata = dict[str, str | int | float | bool | None]

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class FlushResult:
    accepted: int = 0
    duplicates: int = 0
    invalid: int = 0
    failed: int = 0
    dropped: int = 0


class _Flush:
    def __init__(self) -> None:
        self.done = threading.Event()


class UnitEconomics:
    """Batches usage events and sends them in the background.

    Every event gets its idempotency key when it is tracked, not when it is sent, so a
    batch that is retried after a timeout (and may already have been stored) is
    deduplicated by the server instead of being counted twice.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://localhost:8000",
        batch_size: int = 200,
        flush_interval: float = 1.0,
        max_queue: int = 50_000,
        max_retries: int = 6,
        backoff_base: float = 0.25,
        backoff_max: float = 20.0,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        on_error: Callable[[str, list[Event]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"authorization": f"Bearer {api_key}", "user-agent": "unit-economics-python/0.1"},
            timeout=timeout,
            transport=transport,
        )
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._on_error = on_error or self._log_error
        self._sleep = sleep
        self._queue: queue.Queue[Event | _Flush | None] = queue.Queue(maxsize=max_queue)
        self._stats = FlushResult()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="unit-economics", daemon=True)
        self._thread.start()

    # -- tracking -------------------------------------------------------------------------

    def track_llm(
        self,
        *,
        customer_id: str,
        feature: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        metadata: Metadata | None = None,
        timestamp: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        return self._enqueue(
            {
                "kind": "llm_usage",
                "customer_id": customer_id,
                "feature": feature,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "metadata": metadata or {},
            },
            timestamp,
            idempotency_key,
        )

    def track(
        self,
        name: str,
        *,
        customer_id: str,
        feature: str = "",
        metadata: Metadata | None = None,
        timestamp: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        event = {
            "kind": "event",
            "name": name,
            "customer_id": customer_id,
            "feature": feature,
            "metadata": metadata or {},
        }
        return self._enqueue(event, timestamp, idempotency_key)

    @contextmanager
    def llm_call(
        self, *, customer_id: str, feature: str, model: str, metadata: Metadata | None = None
    ) -> Iterator[LlmCall]:
        """Record one LLM request around the code that makes it.

        >>> with ue.llm_call(customer_id="acme", feature="chat", model="gpt-4o-mini") as call:
        ...     response = openai.chat.completions.create(...)
        ...     call.record(response=response)

        Latency is added to the metadata. If the block raises, the event is still recorded
        (with `error`) when usage was recorded before the failure, and the exception propagates.
        """
        call = LlmCall(customer_id=customer_id, feature=feature, model=model, metadata=dict(metadata or {}))
        try:
            yield call
        except BaseException as exc:
            call.metadata["error"] = type(exc).__name__
            raise
        finally:
            if call.usage is not None:
                call.metadata["latency_ms"] = round((time.perf_counter() - call.started) * 1000, 1)
                self.track_llm(
                    customer_id=call.customer_id,
                    feature=call.feature,
                    model=call.model,
                    input_tokens=call.usage.input_tokens,
                    output_tokens=call.usage.output_tokens,
                    cached_tokens=call.usage.cached_tokens,
                    metadata=call.metadata,
                )

    def metered(
        self,
        *,
        feature: str,
        model: str,
        customer_id: Callable[..., str] | str,
        usage: Callable[[Any], Usage],
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorator form of `llm_call`: `usage` extracts token counts from the return value
        (`openai_usage` for OpenAI-compatible clients); `customer_id` is a string or a
        function of the decorated call's arguments."""

        def decorate(fn: Callable[P, R]) -> Callable[P, R]:
            @functools.wraps(fn)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                cid = customer_id(*args, **kwargs) if callable(customer_id) else customer_id
                with self.llm_call(customer_id=cid, feature=feature, model=model) as call:
                    result = fn(*args, **kwargs)
                    call.record(usage(result))
                    return result

            return wrapper

        return decorate

    # -- lifecycle ------------------------------------------------------------------------

    def flush(self, timeout: float | None = None) -> FlushResult:
        """Block until everything tracked so far has been sent (or given up on)."""
        marker = _Flush()
        self._queue.put(marker)
        marker.done.wait(timeout)
        return self._snapshot()

    def _snapshot(self) -> FlushResult:
        with self._lock:
            s = self._stats
            return FlushResult(s.accepted, s.duplicates, s.invalid, s.failed, s.dropped)

    def close(self, timeout: float | None = 30.0) -> FlushResult:
        if self._closed:
            return self._snapshot()
        result = self.flush(timeout)
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout)
        self._http.close()
        return result

    def __enter__(self) -> UnitEconomics:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    # -- internals ------------------------------------------------------------------------

    def _enqueue(self, event: Event, timestamp: datetime | None, key: str | None) -> str:
        if self._closed:
            raise RuntimeError("client is closed")
        event["idempotency_key"] = key or uuid.uuid4().hex
        event["timestamp"] = (timestamp or datetime.now(UTC)).isoformat()
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Never block the caller's request path on telemetry.
            with self._lock:
                self._stats.dropped += 1
            log.warning("unit-economics queue full, dropping event %s", event["idempotency_key"])
        return str(event["idempotency_key"])

    def _run(self) -> None:
        batch: list[Event] = []
        deadline = time.monotonic() + self._flush_interval
        while True:
            try:
                item = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                item = _Flush()  # interval elapsed
                timer = True
            else:
                timer = False
            if isinstance(item, dict):
                batch.append(item)
                if len(batch) >= self._batch_size:
                    self._send(batch)
                    batch = []
                continue
            if batch:
                self._send(batch)
                batch = []
            deadline = time.monotonic() + self._flush_interval
            if item is None:
                return
            if not timer:
                item.done.set()

    def _send(self, batch: list[Event]) -> None:
        attempt = 0
        while True:
            retry_after: float | None = None
            try:
                response = self._http.post("/v1/events/ingest", json={"events": batch})
            except httpx.TransportError as exc:
                error = f"{type(exc).__name__}: {exc}"
            else:
                if response.is_success:
                    self._record(response.json(), batch)
                    return
                error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in RETRYABLE_STATUS:
                    self._fail(error, batch)
                    return
                retry_after = _retry_after(response)
            if attempt >= self._max_retries:
                self._fail(error, batch)
                return
            delay = retry_after if retry_after is not None else self._backoff(attempt)
            log.info("ingest attempt %d failed (%s); retrying in %.2fs", attempt + 1, error, delay)
            self._sleep(delay)
            attempt += 1

    def _backoff(self, attempt: int) -> float:
        # Full jitter: spreads out clients that failed together.
        return random.uniform(0, min(self._backoff_max, self._backoff_base * 2**attempt))

    def _record(self, body: dict[str, Any], batch: list[Event]) -> None:
        with self._lock:
            self._stats.accepted += body.get("accepted", 0)
            self._stats.duplicates += body.get("duplicates", 0)
            self._stats.invalid += body.get("invalid", 0)
        invalid = [r for r in body.get("results", []) if r.get("status") == "invalid"]
        if invalid:
            self._on_error(
                "; ".join(
                    f"#{r['index']}: {e['loc']} {e['msg']}" for r in invalid for e in r.get("errors", [])
                ),
                [batch[r["index"]] for r in invalid],
            )

    def _fail(self, error: str, batch: list[Event]) -> None:
        with self._lock:
            self._stats.failed += len(batch)
        self._on_error(error, batch)

    @staticmethod
    def _log_error(error: str, events: list[Event]) -> None:
        log.error("unit-economics: %d events not ingested: %s", len(events), error)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
