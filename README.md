# unit-economics

An AI product can grow its revenue and lose money doing it. Every request has a real LLM cost, a handful of customers can account for most of it, and a flat plan or a generous credit allowance can quietly turn them unprofitable. This project tracks usage, revenue and LLM cost per customer and per feature as events arrive, and lets you replay real usage under a different price before you change it.

![The Python SDK sending events, the dashboard, and the pricing simulator updating as the Pro plan's overage price changes](docs/preview.gif)

It is a FastAPI service with Dramatiq workers on Postgres and Redis, a typed Python SDK (plus a small TypeScript one), and a Next.js dashboard. The demo data is 300 customers and 2.1M events over 60 days, with four models, a provider price cut three weeks ago and a dozen accounts leaning on an expensive agent feature.

## How it works

### Ingestion

`POST /v1/events/ingest` takes up to 1,000 events per request: LLM usage (`customer_id`, `feature`, `model`, input/output/cached tokens) or generic meter events (`document.processed` with metadata). The code is in `server/ue_api/ingestion/`.

- **One bad event doesn't reject the batch.** Each item is validated on its own against a Pydantic v2 discriminated union. The response has one result per item, in order: `accepted`, `duplicate`, or `invalid` with Pydantic-style `loc`/`msg`/`type` errors. Timestamps more than 5 minutes in the future or older than the 90-day retention are rejected per item too.
- **Idempotent.** `(org_id, idempotency_key)` is unique. The batch goes in as a single `INSERT … SELECT FROM unnest(…) ON CONFLICT DO NOTHING RETURNING`, so a retried batch, or a duplicate key inside one batch, is reported as `duplicate` and counted once. Both SDKs assign the key when an event is tracked, not when it's sent, so retrying after a timeout is safe. `test_sdk_e2e.py` runs the SDK against the real API through a transport that drops every third response after the server has processed it. All 1,000 events land exactly once.
- **API keys** are `ue_sk_` plus 32 random bytes, stored as SHA-256 (`auth.py`). With that much entropy a KDF adds nothing, and lookup stays a single indexed equality.

### LLM cost: versioned prices, Decimal only

`model_prices` is append-only: a price change is a new row with a later `effective_from` (`costs/pricebook.py`). Each event is priced at ingestion with the version in effect at the event's timestamp, and the cost and the version id are stored on the event (`numeric(20,10)`). A later price change therefore never rewrites history. The API refuses to backdate a version (409), because the new version would disagree with costs already recorded. Only a model's first version may start in the past. Cached tokens are billed at their own rate. Events for a model with no price are stored with zero cost and a warning in the response, rather than dropped. Money never touches a float: `Decimal` in Python, `numeric` in Postgres, decimal strings in JSON.

### Rollups that survive at-least-once delivery

A Dramatiq actor folds events into `usage_daily` (per org, day, customer, event, feature, model) and `cost_histogram_daily`. The whole step is one SQL statement (`rollups/sql.py`):

1. claim up to 20k pending events with `FOR UPDATE SKIP LOCKED`,
2. set `rolled_up_at` on them,
3. `INSERT … ON CONFLICT DO UPDATE SET requests = requests + excluded.requests, …`

Either all of it commits or none of it does. If a worker crashes mid-way, the events stay pending. If a worker commits and then dies before acking, the redelivered message finds nothing left to claim. Late events need no special case: an event from 12 days ago lands in its own day's row. Several workers can drain the same org at once: `SKIP LOCKED` keeps their chunks disjoint, and the upserts run in a fixed `ORDER BY`, so two workers never deadlock on aggregate rows.

The tests compare the rollups with a full `GROUP BY` recount of the raw events after each of these cases (`test_rollups.py`, `test_workers.py`, `test_redis_broker.py`):

- running a chunk twice
- draining in uneven chunks
- four concurrent workers
- a late event
- five duplicate messages
- a worker that raises after its transaction committed, so Dramatiq retries the message
- a real `dramatiq` worker process on the Redis broker, with an extra redundant message for every batch

**Waking the worker** (`rollups/scheduler.py`). After committing, ingestion sets `ue:rollup:pending:<org>` with `SET NX` and only enqueues a message if it set the flag. The actor deletes the flag *before* it claims events. An event committed before the delete is visible to the claim that follows. An event committed after it finds the flag gone and enqueues a new message. So a burst of 1,000 requests produces about one message and still loses no wake-up. `python -m ue_api.scheduler` sends a sweep every 30 s as a safety net for a crash between commit and enqueue.

**Cost-per-request percentiles** come from the histogram: 20 log-scale buckets per decade, which keeps them within about 6% of the exact value (`rollups/histogram.py`, tested against exact percentiles on 20k log-normal costs). That gives feature-level p50/p90/p99 from a few thousand rows instead of a million events. The customer drill-down uses an exact `percentile_disc` over that customer's events.

### Meters and pricing

- **Meters** (`billing/meters.py`) are `count`, `sum` or `unique` over events, with `eq`/`ne`/`in` filters on `feature`, `model`, `customer_id` or `metadata.<key>`. A meter is compiled to SQLAlchemy Core: field names come from a whitelist and values are bound. A meter answerable from the rollups (count, or a sum of a token column, filtered on feature/model) reads `usage_daily`. Only metadata filters and distinct counts scan raw events. A test checks that both paths give the same result.
- **Prices** (`billing/pricing.py`) come in four types. All are pure, Decimal-only functions of a period's quantity:
  - `flat`
  - `per_unit`, e.g. "$3 per 1M tokens"
  - `tiered`, graduated or volume, with optional flat amounts per tier
  - `credits`: a prepaid amount includes N units, and overage is metered
- **Subscriptions** bill monthly from the day they started, clamped to month ends (Jan 31 → Feb 28 → Mar 31). `GET /v1/customers/{id}/current-period` prices the period so far exactly as the invoice would.

### Unit economics

`economics/engine.py` works in two steps. First it aggregates usage in SQL from the rollups: one query per meter the plans need, plus one for cost and request counts. Then it prices each customer in Python. That step loops over a few hundred aggregate rows, never over events.

- **Revenue over a window is a 30-day run rate.** Each customer's quantity is scaled to 30 days and priced like an invoice, so tiers and included credits behave as they would on a real bill. The amount is then scaled back to the window. For a 30-day window that is exactly the invoice amount.
- **Revenue per feature or per day** allocates each price line in proportion to its meter's quantity in that feature or day. Flat fees follow the customer's event count. Allocation keeps exact totals, and a test checks that the feature and daily breakdowns add up to the summary to the cent.
- **Endpoints:** `/v1/economics/summary`, `/customers` (sortable), `/customers/unprofitable`, `/customers/{id}` (by feature, by model, by day, cost percentiles), `/features` (margin and cost percentiles), `/timeseries`.

### Pricing simulator

`POST /v1/simulations` takes a product, a window (1–90 days) and a candidate list of prices. It aggregates the window's usage once, for every meter either price list needs, prices it under the current and the candidate prices, and returns:

- per-customer revenue before and after, the delta and the margin before and after
- which customers become unprofitable or profitable
- how long the SQL and the pricing took

Nothing is persisted. The dashboard calls it 250 ms after you stop typing and keeps the previous result on screen while the new one loads.

### Webhooks

When a customer's 30-day margin crosses zero, the alert actor writes a `customer.margin_negative` (or `customer.margin_recovered`) delivery row per subscribed endpoint. The last known sign is stored, so a customer who stays unprofitable is reported once. Deliveries follow [Standard Webhooks](https://www.standardwebhooks.com) (`webhooks/signing.py`):

- headers `webhook-id`, `webhook-timestamp` and `webhook-signature: v1,<base64 HMAC-SHA256 of id.timestamp.body>`
- `whsec_` secrets
- verification with a 5-minute tolerance and several signatures accepted during a secret rotation

Tests check the signatures both ways against the reference `standardwebhooks` library. Delivery is a Dramatiq actor with exponential backoff (1 s to 1 h, 8 retries). `webhook-id` stays the same across retries so receivers can deduplicate, no transaction is held open during the HTTP call, and the last attempt marks the delivery `failed`. The tests use a local receiver that fails on demand.

### SDKs

**Python** (`sdk/python/unit_economics`). A background thread batches events by size (200) or interval (1 s) and retries 408/409/425/429/5xx and network errors with full-jitter backoff, honouring `Retry-After`. It never retries other 4xx. When its queue is full it drops events instead of blocking your request path, and counts the drops. Per-item invalid results reach an `on_error` callback with the offending events.

```python
with UnitEconomics(api_key) as ue:
    with ue.llm_call(customer_id="acme", feature="chat", model="gpt-4o-mini") as call:
        response = client.chat.completions.create(...)
        call.record(response=response)        # reads OpenAI-style usage, splits out cached tokens

    @ue.metered(feature="summarize", model="gpt-4o", customer_id=lambda doc, tenant: tenant, usage=openai_usage)
    def summarize(doc: str, tenant: str): ...
```

`llm_call` adds `latency_ms` to the event's metadata. If the block raises after usage was recorded, the event is still sent, with an `error` field.

**TypeScript** (`sdk/ts`, one file, no runtime dependencies). It has the same batching, retry and idempotency rules, built on `fetch` with `keepalive`, for browsers and Node.

### Dashboard

`web/` is Next.js (App Router), TypeScript (`strict`, `noUncheckedIndexedAccess`), Tailwind v4, shadcn/ui-style components (`cva` + `tailwind-merge`) and TanStack Query.

- **Pages:** an overview (KPIs, revenue vs cost per day, margin by feature with cost percentiles, a searchable and sortable customer table), a customer drill-down with the current billing period, and the simulator with an editor for all four price types, including adding and removing tiers.
- **Charts** are small hand-written SVG components: a crosshair tooltip, direct labels pushed apart when they collide, one axis, and light and dark palettes.
- **The API key stays on the server.** The browser talks to a Next.js route handler that adds the organization's key before forwarding, so the key never reaches the client.
- **The selected window is in the URL.**

## Numbers

Measured on this machine: a shared 2-vCPU VM running the API, the worker, Postgres 16, Redis and the load generator at the same time. The figures are what one box like this does, not a ceiling.

| | |
| --- | --- |
| Ingestion, 1 uvicorn process, 8 clients × 500-event batches | **15,100 events/s**; request p50 253 ms, p95 367 ms |
| Rollup lag (commit → in the aggregates) while ingestion is saturated | p50 283 ms, p95 553 ms, max 0.9 s |
| Rollup lag at a steady 2,000 events/s | p50 29 ms, p95 63 ms |
| Seed rollup of 2.09M events, one process, 50k-event chunks | 116 s (18,000 events/s) |
| Simulator, Pro plan (134 customers), 30 days, on 2.09M events | server p50 44 ms, p95 62 ms |
| Simulator, any plan, 60 days | server p50 47–79 ms, p95 ≤ 94 ms |
| Dashboard endpoints, 30 days | summary 46 ms, customers 49 ms, features 73 ms, drill-down 19 ms (p50) |
| Daily timeseries, 60 days | 337 ms p50 (prices every customer for every day) |

The raw results are in `bench/results/`. Reproduce them with `python bench/ingest.py` (it starts its own API and worker on a scratch database) and `python bench/simulator.py` (against the seeded database and a running API).

## Tech stack

Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, Dramatiq (Redis broker, AsyncIO middleware), Postgres 16, Redis, httpx. Next.js 16, React 19, TypeScript, Tailwind CSS 4, TanStack Query, Vitest. pytest, mypy `--strict`, ruff.

## Running it

You need Postgres and Redis running locally.

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -e sdk/python
export UE_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/unit_economics
export UE_REDIS_URL=redis://localhost:6379/0

(cd server && alembic upgrade head)
python scripts/seed.py                         # ~3 min: 300 customers, ~2.1M events; writes web/.env.local

(cd server && uvicorn ue_api.app:app --port 8130)   # API, docs at /docs
(cd server && dramatiq ue_api.workers)             # rollups, alerts, webhook deliveries
(cd server && python -m ue_api.scheduler)          # periodic sweep

cd web && npm ci && npm run build && npm start     # http://localhost:3130
```

Then send some usage of your own: `UE_API_KEY=… python examples/quickstart.py`. To regenerate the preview, run `pip install -e ".[preview]"`, then `python scripts/preview.py`.

## Tests

```bash
pytest                            # 98 tests: unit, Postgres, Redis, StubBroker workers,
                                  #           a real dramatiq worker process, SDK end to end
mypy && ruff check .
cd web && npm run typecheck && npm test        # 17 tests
cd sdk/ts && npm run typecheck && npm test     # 5 tests
```

The Python tests expect Postgres and Redis on localhost. They create the schema in `po_ue_test` (override with `UE_DATABASE_URL`) and use Redis database 14.

## Layout

```
server/ue_api/
  ingestion/      schemas, batch insert, router
  costs/          versioned model prices, per-event cost
  rollups/        the claim-and-aggregate SQL, debounced scheduler, cost histogram
  billing/        meters → SQL, price types, periods, products and subscriptions
  economics/      ledger engine (aggregate in SQL, price in Python), API
  simulator/      what-if pricing
  webhooks/       Standard Webhooks signing, margin alerts, delivery
  workers.py      Dramatiq actors;  scheduler.py  periodic sweep
server/migrations Alembic
server/tests/     pytest
sdk/python/       unit_economics client
sdk/ts/           TypeScript client
web/              Next.js dashboard
scripts/          seed.py, preview.py
bench/            ingestion/lag and simulator benchmarks, results
examples/         quickstart.py
```

## Known limitations

- `events` is a single table. At real volume it should be partitioned by month, which means the idempotency key has to be unique per partition or tracked separately.
- The run rate assumes every subscription covered the whole window. Plan changes inside a window are priced as if the current plan applied throughout.
- Single currency (USD) and one active subscription per customer.
- Webhook signing secrets are stored as-is, because HMAC needs the raw secret. Encrypting them at rest is left to the deployment.
- The dashboard uses one organization key from the server environment. There are no user accounts.
- No request rate limiting on ingestion.

## License

MIT
