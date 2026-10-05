import calendar
from datetime import date


def add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    """Shift by whole months, clamping to the month's last day (Jan 31 + 1 month = Feb 28/29)."""
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    day = min(anchor_day or d.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def current_period(started_on: date, today: date) -> tuple[date, date]:
    """The monthly billing period containing `today`, anchored on the subscription start day."""
    if today < started_on:
        return started_on, add_months(started_on, 1, started_on.day)
    months = (today.year - started_on.year) * 12 + today.month - started_on.month
    start = add_months(started_on, months, started_on.day)
    if start > today:
        months -= 1
        start = add_months(started_on, months, started_on.day)
    return start, add_months(started_on, months + 1, started_on.day)
