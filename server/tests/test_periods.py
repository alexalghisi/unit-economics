from datetime import date

import pytest
from ue_api.billing.periods import add_months, current_period


def test_add_months_clamps_to_month_end() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


@pytest.mark.parametrize(
    ("started", "today", "expected"),
    [
        (date(2026, 1, 10), date(2026, 3, 9), (date(2026, 2, 10), date(2026, 3, 10))),
        (date(2026, 1, 10), date(2026, 3, 10), (date(2026, 3, 10), date(2026, 4, 10))),
        # Anchored on the 31st: short months end early but the anchor comes back.
        (date(2026, 1, 31), date(2026, 3, 15), (date(2026, 2, 28), date(2026, 3, 31))),
        (date(2026, 5, 1), date(2026, 4, 1), (date(2026, 5, 1), date(2026, 6, 1))),
    ],
)
def test_current_period(started: date, today: date, expected: tuple[date, date]) -> None:
    assert current_period(started, today) == expected
