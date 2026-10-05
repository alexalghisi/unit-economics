"""Log-scale cost buckets shared by the rollup SQL and the percentile reader.

Bucket 0 holds free requests (below $1e-8). Bucket b >= 1 covers
[1e-8 * 10^((b-1)/20), 1e-8 * 10^(b/20)): twenty buckets per decade, so a percentile read
from the histogram is within about 6% of the exact value.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal

BUCKETS_PER_DECADE = 20
FLOOR_USD = Decimal("1e-8")

BUCKET_SQL = (
    "CASE WHEN {col} < 0.00000001 THEN 0 "
    f"ELSE floor(log({{col}} * 100000000) * {BUCKETS_PER_DECADE})::int + 1 END"
)


def bucket_of(cost: Decimal) -> int:
    if cost < FLOOR_USD:
        return 0
    return (
        int(((cost / FLOOR_USD).log10() * BUCKETS_PER_DECADE).to_integral_value(rounding="ROUND_FLOOR")) + 1
    )


def bucket_midpoint(bucket: int) -> Decimal:
    if bucket <= 0:
        return Decimal(0)
    exponent = (Decimal(bucket) - Decimal("0.5")) / BUCKETS_PER_DECADE
    return FLOOR_USD * Decimal(10) ** exponent


def percentiles(counts: Mapping[int, int], qs: Iterable[float]) -> dict[float, Decimal | None]:
    """Nearest-rank percentiles over a bucket histogram."""
    total = sum(counts.values())
    ordered = sorted(counts.items())
    out: dict[float, Decimal | None] = {}
    for q in qs:
        if total == 0:
            out[q] = None
            continue
        rank = max(1, -(-int(q * total * 1000) // 1000))  # ceil without float drift for q*total
        running = 0
        for bucket, n in ordered:
            running += n
            if running >= rank:
                out[q] = bucket_midpoint(bucket)
                break
    return out
