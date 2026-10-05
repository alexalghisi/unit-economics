"""A local webhook receiver with fault injection, for delivery tests."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Received:
    headers: dict[str, str]
    body: bytes


@dataclass
class Receiver:
    url: str
    fail_first: int = 0
    always_fail: bool = False
    requests: list[Received] = field(default_factory=list)


@contextmanager
def webhook_receiver(fail_first: int = 0, always_fail: bool = False) -> Iterator[Receiver]:
    state = Receiver(url="", fail_first=fail_first, always_fail=always_fail)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            state.requests.append(Received({k.lower(): v for k, v in self.headers.items()}, body))
            failing = state.always_fail or len(state.requests) <= state.fail_first
            self.send_response(503 if failing else 204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_address[1]}/hooks"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
