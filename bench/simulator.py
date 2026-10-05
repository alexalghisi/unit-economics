"""Latency of the pricing simulator and the dashboard endpoints on the seeded account.

Run scripts/seed.py first and keep the API running (uvicorn on UE_API_URL).

    python bench/simulator.py [--runs 30]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import summary_ms

ROOT = Path(__file__).resolve().parents[1]


def env() -> dict[str, str]:
    pairs = (
        line.split("=", 1) for line in (ROOT / "web" / ".env.local").read_text().splitlines() if "=" in line
    )
    return dict(pairs)


def candidate(prices: list[dict[str, object]], factor: float) -> list[dict[str, object]]:
    """Scale every metered amount: a realistic "what if usage got more expensive" edit."""
    out = []
    for p in prices:
        q = dict(p)
        for k in ("unit_amount", "overage_unit_amount"):
            if k in q:
                q[k] = f"{float(str(q[k])) * factor:.4f}"
        if q["kind"] == "tiered":
            q["tiers"] = [{**t, "unit_amount": f"{float(t['unit_amount']) * factor:.4f}"} for t in q["tiers"]]  # type: ignore[attr-defined]
        out.append(q)
    return out


async def timed(
    client: httpx.AsyncClient, method: str, url: str, runs: int, **kw: object
) -> dict[str, float]:
    ms: list[float] = []
    for _ in range(runs):
        t = time.perf_counter()
        r = await client.request(method, url, **kw)  # type: ignore[arg-type]
        ms.append((time.perf_counter() - t) * 1000)
        r.raise_for_status()
    return summary_ms(ms)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()
    cfg = env()
    conn = await asyncpg.connect("postgresql://synckit:synckit@localhost:5432/po_ue")
    events = await conn.fetchval("SELECT count(*) FROM events")
    rollup_rows = await conn.fetchval("SELECT count(*) FROM usage_daily")
    await conn.close()
    results: dict[str, object] = {
        "events": events,
        "usage_daily_rows": rollup_rows,
        "simulations": {},
        "endpoints": {},
    }
    async with httpx.AsyncClient(
        base_url=cfg["UE_API_URL"], headers={"authorization": f"Bearer {cfg['UE_API_KEY']}"}, timeout=60
    ) as client:
        products = (await client.get("/v1/products")).json()
        for product in products:
            for days in (30, 60):
                body = {
                    "product_id": product["id"],
                    "days": days,
                    "prices": candidate(product["prices"], 1.4),
                }
                server_ms: list[float] = []
                client_ms: list[float] = []
                for i in range(args.runs + 2):
                    t = time.perf_counter()
                    r = await client.post("/v1/simulations", json=body)
                    elapsed = (time.perf_counter() - t) * 1000
                    r.raise_for_status()
                    if i >= 2:  # warm-up
                        client_ms.append(elapsed)
                        server_ms.append(r.json()["timings"]["total_ms"])
                key = f"{product['name']} ({r.json()['customers']} customers), {days}d"
                results["simulations"][key] = {"server": summary_ms(server_ms), "http": summary_ms(client_ms)}  # type: ignore[index]
                print(key, results["simulations"][key])  # type: ignore[index]
        for name, url in [
            ("summary 30d", "/v1/economics/summary?days=30"),
            ("customers 30d", "/v1/economics/customers?days=30&limit=500"),
            ("features 30d", "/v1/economics/features?days=30"),
            ("timeseries 60d", "/v1/economics/timeseries?days=60"),
            ("customer drill-down 30d", "/v1/economics/customers/cus_0001?days=30"),
        ]:
            results["endpoints"][name] = await timed(client, "GET", url, args.runs)  # type: ignore[index]
            print(name, results["endpoints"][name])  # type: ignore[index]
    (ROOT / "bench" / "results" / "simulator.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
