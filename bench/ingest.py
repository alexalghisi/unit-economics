"""Ingestion throughput and rollup lag, end to end.

Starts the API (uvicorn) and a Dramatiq worker against a scratch database, then:
  1. pushes batches as fast as N concurrent clients can (throughput),
  2. sends a steady stream at a fixed rate and measures, per event, the time from commit
     to being in the daily aggregates (rollup lag).

    python bench/ingest.py [--seconds 20] [--concurrency 8] [--batch 500] [--rate 2000]
"""

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import summary_ms

ROOT = Path(__file__).resolve().parents[1]
DB = "po_ue_bench"
DSN = f"postgresql://synckit:synckit@localhost:5432/{DB}"
PORT = 8131
FEATURES = [("chat", "gpt-4o-mini"), ("search", "text-embedding-3-small"), ("agent", "gpt-4o")]


def event(rng: random.Random) -> dict[str, object]:
    feature, model = rng.choice(FEATURES)
    return {
        "kind": "llm_usage",
        "idempotency_key": uuid.uuid4().hex,
        "customer_id": f"cus_{rng.randint(1, 300):04d}",
        "feature": feature,
        "model": model,
        "input_tokens": rng.randint(100, 9000),
        "output_tokens": rng.randint(0, 1500),
        "cached_tokens": rng.randint(0, 500),
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"latency_ms": rng.randint(200, 3000)},
    }


async def prepare() -> str:
    admin = await asyncpg.connect("postgresql://synckit:synckit@localhost:5432/postgres")
    await admin.execute(f"DROP DATABASE IF EXISTS {DB}")
    await admin.execute(f"CREATE DATABASE {DB}")
    await admin.close()
    env = {**os.environ, "UE_DATABASE_URL": f"postgresql+asyncpg://synckit:synckit@localhost:5432/{DB}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "server", env=env, check=True
    )
    code = (
        "import asyncio\n"
        "from ue_api.db import sessionmaker, dispose_engines\n"
        "from ue_api.auth import create_api_key\n"
        "from ue_api.models import Organization\n"
        "from ue_api.costs.service import add_price_version\n"
        "from datetime import datetime, UTC\n"
        "from decimal import Decimal as D\n"
        "async def main():\n"
        "    async with sessionmaker()() as s:\n"
        "        org = Organization(name='bench'); s.add(org); await s.flush()\n"
        "        for m, i, o in [('gpt-4o-mini','0.15','0.6'),('text-embedding-3-small','0.02','0'),('gpt-4o','2.5','10')]:\n"
        "            await add_price_version(s, org.id, model=m, effective_from=datetime(2026,1,1,tzinfo=UTC),"
        " input_per_mtok=D(i), output_per_mtok=D(o), cached_per_mtok=D(i), now=datetime.now(UTC))\n"
        "        key = await create_api_key(s, org.id, 'bench'); await s.commit(); print(key)\n"
        "    await dispose_engines()\n"
        "asyncio.run(main())\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT / "server", env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def start(namespace: str) -> list[subprocess.Popen[bytes]]:
    env = {
        **os.environ,
        "UE_DATABASE_URL": f"postgresql+asyncpg://synckit:synckit@localhost:5432/{DB}",
        "UE_REDIS_URL": "redis://localhost:6379/13",
        "UE_REDIS_NAMESPACE": namespace,
    }
    bin_dir = Path(sys.executable).parent
    api = subprocess.Popen(
        [str(bin_dir / "uvicorn"), "ue_api.app:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT / "server",
        env=env,
    )
    worker = subprocess.Popen(
        [str(bin_dir / "dramatiq"), "ue_api.workers", "--processes", "1", "--threads", "2"],
        cwd=ROOT / "server",
        env=env,
        stderr=subprocess.DEVNULL,
    )
    return [api, worker]


async def wait_ready(client: httpx.AsyncClient) -> None:
    for _ in range(100):
        try:
            if (await client.get("/healthz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError("API did not start")


async def throughput(
    client: httpx.AsyncClient, seconds: float, concurrency: int, batch: int
) -> dict[str, object]:
    latencies: list[float] = []
    sent = 0
    deadline = time.perf_counter() + seconds
    rng = random.Random(1)

    async def worker() -> None:
        nonlocal sent
        while time.perf_counter() < deadline:
            events = [event(rng) for _ in range(batch)]
            t = time.perf_counter()
            r = await client.post("/v1/events/ingest", json={"events": events})
            latencies.append((time.perf_counter() - t) * 1000)
            assert r.status_code == 200 and r.json()["accepted"] == batch, r.text[:200]
            sent += batch

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
        "events": sent,
        "seconds": round(elapsed, 2),
        "events_per_s": round(sent / elapsed),
        "request": summary_ms(latencies),
    }


async def steady(client: httpx.AsyncClient, seconds: float, rate: int, batch: int) -> datetime:
    rng = random.Random(2)
    interval = batch / rate
    started = datetime.now(UTC)
    next_at = time.perf_counter()
    end = next_at + seconds
    while next_at < end:
        await client.post("/v1/events/ingest", json={"events": [event(rng) for _ in range(batch)]})
        next_at += interval
        await asyncio.sleep(max(0.0, next_at - time.perf_counter()))
    return started


async def lag_since(since: datetime, timeout: float = 120) -> dict[str, float]:
    conn = await asyncpg.connect(DSN)
    t = time.perf_counter()
    while await conn.fetchval("SELECT count(*) FROM events WHERE rolled_up_at IS NULL") > 0:
        if time.perf_counter() - t > timeout:
            raise RuntimeError("rollups did not catch up")
        await asyncio.sleep(0.2)
    rows = await conn.fetch(
        "SELECT extract(epoch FROM rolled_up_at - ingested_at) * 1000 AS ms FROM events WHERE ingested_at >= $1",
        since,
    )
    await conn.close()
    return summary_ms([float(r["ms"]) for r in rows])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--rate", type=int, default=2000, help="events/s for the lag phase")
    args = parser.parse_args()
    key = await prepare()
    procs = start(f"ue-bench-{uuid.uuid4().hex[:6]}")
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{PORT}", headers={"authorization": f"Bearer {key}"}, timeout=60
        ) as client:
            await wait_ready(client)
            await asyncio.sleep(2)  # let the worker boot
            tput = await throughput(client, args.seconds, args.concurrency, args.batch)
            print("throughput:", json.dumps(tput))
            catch_up = await lag_since(datetime.fromtimestamp(0, UTC))
            print("rollup lag while saturated:", json.dumps(catch_up))
            since = await steady(client, args.seconds, args.rate, 100)
            lag = await lag_since(since)
            print(f"rollup lag at {args.rate} events/s:", json.dumps(lag))
        result = {
            "machine": f"{os.cpu_count()} vCPU, shared; API, worker, Postgres, Redis and the load generator on one box",
            "throughput": {**tput, "concurrency": args.concurrency, "batch": args.batch},
            "rollup_lag_saturated": catch_up,
            "rollup_lag_steady": {**lag, "rate_events_per_s": args.rate},
        }
        (ROOT / "bench" / "results" / "ingest.json").write_text(json.dumps(result, indent=2) + "\n")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(15)


if __name__ == "__main__":
    asyncio.run(main())
