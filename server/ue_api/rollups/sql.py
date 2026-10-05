from sqlalchemy import TextClause, text

from ue_api.ingestion.schemas import LLM_USAGE
from ue_api.rollups.histogram import BUCKET_SQL

# One statement, one transaction: claim a chunk of pending events, mark them, and fold
# them into the daily aggregates. Either all of it commits or none of it does, so a worker
# that dies mid-way leaves the events pending and a redelivered message picks them up
# again without counting anything twice. SKIP LOCKED lets several workers drain the same
# org without waiting on each other; the ORDER BY on the upserts keeps their row-lock
# order consistent so they can't deadlock on the aggregate rows.
# Only module constants are interpolated below; every value is a bind parameter.
_ROLLUP_SQL = f"""
    WITH claimed AS (
        SELECT id FROM events
        WHERE org_id = :org_id AND rolled_up_at IS NULL
        ORDER BY id
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    ), marked AS (
        UPDATE events e SET rolled_up_at = clock_timestamp()
        FROM claimed WHERE e.id = claimed.id
        RETURNING e.org_id, (e.timestamp AT TIME ZONE 'UTC')::date AS day, e.customer_id, e.name,
                  e.feature, e.model, e.input_tokens, e.output_tokens, e.cached_tokens, e.cost_usd,
                  e.ingested_at
    ), daily AS (
        INSERT INTO usage_daily AS u (org_id, day, customer_id, event_name, feature, model, requests,
                                      input_tokens, output_tokens, cached_tokens, cost_usd, updated_at)
        SELECT org_id, day, customer_id, name, feature, model, count(*), sum(input_tokens),
               sum(output_tokens), sum(cached_tokens), sum(cost_usd), now()
        FROM marked
        GROUP BY org_id, day, customer_id, name, feature, model
        ORDER BY day, customer_id, name, feature, model
        ON CONFLICT (org_id, day, customer_id, event_name, feature, model) DO UPDATE SET
            requests = u.requests + excluded.requests,
            input_tokens = u.input_tokens + excluded.input_tokens,
            output_tokens = u.output_tokens + excluded.output_tokens,
            cached_tokens = u.cached_tokens + excluded.cached_tokens,
            cost_usd = u.cost_usd + excluded.cost_usd,
            updated_at = now()
        RETURNING 1
    ), hist AS (
        INSERT INTO cost_histogram_daily AS h (org_id, feature, day, bucket, requests)
        SELECT org_id, feature, day, {BUCKET_SQL.format(col="cost_usd")} AS bucket, count(*)
        FROM marked
        WHERE name = '{LLM_USAGE}'
        GROUP BY 1, 2, 3, 4
        ORDER BY 2, 3, 4
        ON CONFLICT (org_id, feature, day, bucket) DO UPDATE SET requests = h.requests + excluded.requests
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM marked) AS events,
           (SELECT count(*) FROM daily) AS daily_rows,
           (SELECT min(day) FROM marked) AS oldest_day,
           (SELECT max(clock_timestamp() - ingested_at) FROM marked) AS max_lag
    """  # noqa: S608
ROLLUP_CHUNK: TextClause = text(_ROLLUP_SQL)

PENDING_ORGS: TextClause = text("SELECT DISTINCT org_id FROM events WHERE rolled_up_at IS NULL")
