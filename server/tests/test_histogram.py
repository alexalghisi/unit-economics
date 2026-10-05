import math
import random
from decimal import Decimal

from ue_api.rollups.histogram import bucket_midpoint, bucket_of, percentiles


def test_bucket_bounds() -> None:
    assert bucket_of(Decimal(0)) == 0
    assert bucket_of(Decimal("0.000000009")) == 0
    assert bucket_of(Decimal("0.00000001")) == 1
    assert bucket_of(Decimal("0.0000001")) == 21  # one decade up
    for b in (1, 40, 133):
        assert bucket_of(bucket_midpoint(b)) == b


def test_histogram_percentiles_are_within_six_percent_of_exact() -> None:
    rng = random.Random(7)
    costs = sorted(Decimal(str(round(rng.lognormvariate(-7, 1.4), 10))) for _ in range(20_000))
    counts: dict[int, int] = {}
    for c in costs:
        counts[bucket_of(c)] = counts.get(bucket_of(c), 0) + 1
    approx = percentiles(counts, (0.5, 0.9, 0.99))
    for q, value in approx.items():
        exact = costs[math.ceil(q * len(costs)) - 1]
        assert value is not None
        assert abs(value - exact) / exact < Decimal("0.06"), (q, value, exact)


def test_empty_histogram_has_no_percentiles() -> None:
    assert percentiles({}, (0.5,)) == {0.5: None}
