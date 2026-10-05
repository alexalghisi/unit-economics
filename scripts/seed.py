"""Seed a realistic account: 300 customers, ~2.5M events over 60 days.

    python scripts/seed.py [--events-per-day 42000] [--days 60]

Events are bulk-loaded with COPY (going through HTTP would take far longer and is what
bench/ingest.py measures), but they are priced with the same PriceBook the API uses and
rolled up with the same SQL the worker runs. Writes web/.env.local with the API key.
"""

import argparse
import asyncio
import math
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import orjson
from sqlalchemy import text
from ue_api.auth import create_api_key
from ue_api.billing import service as billing
from ue_api.billing.meters import MeterDefinition, MeterFilter
from ue_api.billing.pricing import AnyPrice, CreditsPrice, FlatPrice, PerUnitPrice, Tier, TieredPrice
from ue_api.billing.schemas import CustomerIn, ProductIn, SubscriptionIn
from ue_api.costs.pricebook import PriceBook, PriceVersion, TokenUsage, token_cost
from ue_api.costs.service import add_price_version
from ue_api.db import dispose_engines, sessionmaker
from ue_api.models import Organization
from ue_api.rollups.service import rollup_chunk
from ue_api.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
MTOK = 1_000_000


@dataclass(frozen=True)
class Feature:
    name: str
    model: str
    input_mean: int
    output_mean: int
    cache_share: float


FEATURES = [
    Feature("chat", "gpt-4o-mini", 2_200, 450, 0.35),
    Feature("search", "text-embedding-3-small", 600, 0, 0.0),
    Feature("summarize", "llama-3.1-70b", 6_000, 700, 0.0),
    Feature("autocomplete", "gpt-4o-mini", 900, 60, 0.6),
    Feature("agent", "gpt-4o", 9_000, 1_400, 0.25),
]


@dataclass
class Profile:
    customer: str
    plan: str
    daily_requests: float
    mix: list[float]  # weights over FEATURES
    trend: float  # growth over the period, 1.0 = flat
    docs_per_day: float


COMPANY_A = [
    "Blue",
    "North",
    "Silver",
    "Quiet",
    "Bright",
    "Cedar",
    "Atlas",
    "Copper",
    "Lumen",
    "Harbor",
    "Pine",
    "Orbit",
    "Signal",
    "Granite",
    "Maple",
    "Echo",
    "Vector",
    "Juniper",
    "Summit",
    "Nimbus",
    "Tidal",
    "Kestrel",
    "Meridian",
    "Basalt",
    "Fable",
    "Halcyon",
    "Indigo",
    "Larch",
]
COMPANY_B = [
    "Labs",
    "Health",
    "Legal",
    "Analytics",
    "Robotics",
    "Studio",
    "Finance",
    "Logistics",
    "Learning",
    "Systems",
    "Works",
    "Bio",
    "Media",
    "Cloud",
    "Security",
]


def profiles(rng: random.Random, n: int = 300) -> list[Profile]:
    out = []
    for i in range(n):
        roll = rng.random()
        plan = "free" if roll < 0.07 else "starter" if roll < 0.47 else "pro" if roll < 0.87 else "scale"
        base = {"free": 12, "starter": 35, "pro": 80, "scale": 320}[plan]
        daily = base * rng.lognormvariate(0, 0.6)
        mix = [
            rng.uniform(0.5, 3),
            rng.uniform(0.2, 2),
            rng.uniform(0, 1),
            rng.uniform(0, 2),
            rng.uniform(0, 0.25),
        ]
        out.append(Profile(f"cus_{i + 1:04d}", plan, daily, mix, rng.uniform(0.8, 1.6), rng.uniform(0, 6)))
    # The interesting ones: a handful of accounts that lean on the expensive agent feature.
    for p in rng.sample([p for p in out if p.plan in ("pro", "starter")], 12):
        p.mix = [0.5, 0.2, 0.2, 0.2, 4.0]
        p.daily_requests *= rng.uniform(2.5, 4.5)
        p.trend = rng.uniform(1.3, 2.2)
    return out


def company_names(rng: random.Random, n: int) -> list[str]:
    names = [f"{a} {b}" for a in COMPANY_A for b in COMPANY_B]
    rng.shuffle(names)
    return names[:n]


async def setup_catalog(org_id: uuid.UUID, now: datetime) -> tuple[dict[str, uuid.UUID], PriceBook]:
    start = now - timedelta(days=120)
    versions = [
        ("gpt-4o-mini", start, "0.15", "0.60", "0.075"),
        ("gpt-4o", start, "2.50", "10.00", "1.25"),
        ("llama-3.1-70b", start, "0.88", "0.88", "0.88"),
        ("text-embedding-3-small", start, "0.02", "0", "0"),
        # A provider price change three weeks ago: older events keep the old cost.
        ("gpt-4o", now - timedelta(days=21), "2.00", "8.00", "1.00"),
    ]
    async with sessionmaker()() as s:
        book: list[PriceVersion] = []
        for model, effective, i, o, c in versions:
            book.append(
                await add_price_version(
                    s,
                    org_id,
                    model=model,
                    effective_from=effective,
                    input_per_mtok=D(i),
                    output_per_mtok=D(o),
                    cached_per_mtok=D(c),
                    now=start,
                )
            )
        meters = {}
        for definition in (
            MeterDefinition(
                slug="tokens",
                name="LLM tokens",
                event_name="llm_usage",
                aggregation="sum",
                property="total_tokens",
            ),
            MeterDefinition(
                slug="requests", name="LLM requests", event_name="llm_usage", aggregation="count"
            ),
            MeterDefinition(
                slug="agent-runs",
                name="Agent runs",
                event_name="llm_usage",
                aggregation="count",
                filters=[MeterFilter(field="feature", op="eq", value="agent")],
            ),
            MeterDefinition(
                slug="documents",
                name="Documents processed",
                event_name="document.processed",
                aggregation="sum",
                property="metadata.pages",
            ),
        ):
            meters[definition.slug] = (await billing.create_meter(s, org_id, definition)).id
        await s.commit()
    return meters, PriceBook.of(book)


def plans(meters: dict[str, uuid.UUID]) -> dict[str, tuple[str, list[AnyPrice]]]:
    tokens = meters["tokens"]
    return {
        "free": ("Free", [FlatPrice(amount=D(0))]),
        "starter": (
            "Starter",
            [FlatPrice(amount=D(19)), PerUnitPrice(meter_id=tokens, unit_amount=D(3), per=MTOK)],
        ),
        "pro": (
            "Pro",
            [
                CreditsPrice(
                    meter_id=tokens,
                    amount=D(99),
                    included_units=D(40 * MTOK),
                    overage_unit_amount=D("2"),
                    per=MTOK,
                )
            ],
        ),
        "scale": (
            "Scale",
            [
                FlatPrice(amount=D(499)),
                TieredPrice(
                    meter_id=tokens,
                    mode="graduated",
                    per=MTOK,
                    tiers=[
                        Tier(up_to=D(100 * MTOK), unit_amount=D("1.8")),
                        Tier(up_to=None, unit_amount=D("1.2")),
                    ],
                ),
            ],
        ),
    }


async def setup_customers(
    org_id: uuid.UUID, people: list[Profile], meters: dict[str, uuid.UUID], rng: random.Random, today: date
) -> None:
    names = company_names(rng, len(people))
    async with sessionmaker()() as s:
        products = {}
        for key, (name, prices) in plans(meters).items():
            products[key] = (await billing.create_product(s, org_id, ProductIn(name=name, prices=prices))).id
        for p, name in zip(people, names, strict=True):
            await billing.upsert_customer(s, org_id, CustomerIn(external_id=p.customer, name=name))
            await billing.create_subscription(
                s,
                org_id,
                SubscriptionIn(
                    customer_external_id=p.customer,
                    product_id=products[p.plan],
                    started_on=today - timedelta(days=rng.randint(10, 200)),
                ),
            )
        await s.commit()


def day_events(
    org_id: uuid.UUID,
    day: date,
    day_index: int,
    days: int,
    people: list[Profile],
    scale: float,
    book: PriceBook,
    rng: random.Random,
    now: datetime,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    weekday = 0.75 if day.weekday() >= 5 else 1.0
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    span = 86_400 if day < now.date() else max(1, int((now - midnight).total_seconds()) - 60)
    for p in people:
        progress = day_index / max(1, days - 1)
        expected = p.daily_requests * scale * weekday * (1 + (p.trend - 1) * progress) * span / 86_400
        n = int(rng.gauss(expected, math.sqrt(expected)) if expected > 20 else rng.random() * 2 * expected)
        weights = p.mix
        for f in rng.choices(FEATURES, weights=weights, k=max(0, n)):
            ts = midnight + timedelta(seconds=rng.uniform(0, span))
            inp = max(1, int(rng.lognormvariate(math.log(f.input_mean), 0.7)))
            out = int(rng.lognormvariate(math.log(f.output_mean), 0.6)) if f.output_mean else 0
            cached = int(inp * f.cache_share * rng.random())
            version = book.resolve(f.model, ts)
            assert version is not None
            cost = token_cost(version, TokenUsage(inp - cached, out, cached))
            rows.append(
                (
                    org_id,
                    uuid.uuid4().hex,
                    p.customer,
                    "llm_usage",
                    f.name,
                    f.model,
                    inp - cached,
                    out,
                    cached,
                    cost,
                    version.id,
                    orjson.dumps({"latency_ms": int(rng.lognormvariate(6.5, 0.5))}).decode(),
                    ts,
                    ts + timedelta(seconds=rng.uniform(0.2, 3)),
                )
            )
        for _ in range(int(rng.random() * 2 * p.docs_per_day)):
            ts = midnight + timedelta(seconds=rng.uniform(0, span))
            meta = {"pages": rng.randint(1, 40), "workspace": f"w{rng.randint(1, 5)}"}
            rows.append(
                (
                    org_id,
                    uuid.uuid4().hex,
                    p.customer,
                    "document.processed",
                    "",
                    "",
                    0,
                    0,
                    0,
                    D(0),
                    None,
                    orjson.dumps(meta).decode(),
                    ts,
                    ts + timedelta(seconds=1),
                )
            )
    rows.sort(key=lambda r: r[12])
    return rows


COLUMNS = [
    "org_id",
    "idempotency_key",
    "customer_id",
    "name",
    "feature",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cost_usd",
    "model_price_id",
    "metadata",
    "timestamp",
    "ingested_at",
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--customers", type=int, default=300)
    parser.add_argument("--scale", type=float, default=1.0, help="multiplier on request volume")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    now = datetime.now(UTC)
    today = now.date()
    t0 = time.perf_counter()

    async with sessionmaker()() as s:
        await s.execute(
            text(
                "TRUNCATE organizations, events, usage_daily, cost_histogram_daily, margin_alert_state "
                "RESTART IDENTITY CASCADE"
            )
        )
        org = Organization(name="Northwind AI (demo)")
        s.add(org)
        await s.flush()
        api_key = await create_api_key(s, org.id, "demo")
        await s.commit()
        org_id = org.id

    meters, book = await setup_catalog(org_id, now)
    people = profiles(rng, args.customers)
    await setup_customers(org_id, people, meters, rng, today)

    dsn = get_settings().database_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    # COPY speaks the binary protocol; jsonb's binary form is a version byte and the text.
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: b"\x01" + v.encode(),
        decoder=lambda v: v[1:].decode(),
        schema="pg_catalog",
        format="binary",
    )
    total = 0
    for i in range(args.days):
        day = today - timedelta(days=args.days - 1 - i)
        rows = day_events(org_id, day, i, args.days, people, args.scale, book, rng, now)
        await conn.copy_records_to_table("events", records=rows, columns=COLUMNS)
        total += len(rows)
        print(f"\r{day}  {total:>10,} events", end="", flush=True)
    await conn.close()
    loaded = time.perf_counter()
    print(f"\nloaded {total:,} events in {loaded - t0:.1f}s")

    async with sessionmaker()() as s:
        rolled = 0
        while (r := await rollup_chunk(s, org_id, 50_000)).events:
            rolled += r.events
        await s.execute(text("ANALYZE"))
        await s.commit()
    done = time.perf_counter()
    print(f"rolled up {rolled:,} events in {done - loaded:.1f}s ({rolled / (done - loaded):,.0f} events/s)")

    env = ROOT / "web" / ".env.local"
    env.write_text(f"UE_API_URL=http://127.0.0.1:8130\nUE_API_KEY={api_key}\n")
    print(f"API key written to {env.relative_to(ROOT)}")
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
