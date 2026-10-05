from datetime import UTC, date, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def window_bounds(days: int, today: date | None = None) -> tuple[date, date]:
    """The last `days` whole UTC days including today, as a half-open [start, end) range."""
    end = (today or utcnow().date()) + timedelta(days=1)
    return end - timedelta(days=days), end


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)
