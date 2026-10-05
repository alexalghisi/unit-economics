import statistics


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def summary_ms(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50_ms": round(pct(values, 0.5), 1),
        "p95_ms": round(pct(values, 0.95), 1),
        "max_ms": round(max(values), 1) if values else float("nan"),
        "mean_ms": round(statistics.fmean(values), 1) if values else float("nan"),
    }
