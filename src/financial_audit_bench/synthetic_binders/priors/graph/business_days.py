"""Utility file for the US business-day posting calendar."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _observed(day: date) -> date:
    """Fixed-date holidays close the adjacent weekday when they fall on a
    weekend (federal observance)."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def us_federal_holidays(year: int) -> frozenset[date]:
    """Observed US federal holidays plus an authored post-Thanksgiving closure."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return frozenset(
        (
            _observed(date(year, 1, 1)),  # New Year's Day
            _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
            _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
            _nth_weekday(year, 6, 0, 1) - timedelta(days=7),  # Memorial Day
            _observed(date(year, 6, 19)),  # Juneteenth
            _observed(date(year, 7, 4)),  # Independence Day
            _nth_weekday(year, 9, 0, 1),  # Labor Day
            _nth_weekday(year, 10, 0, 2),  # Columbus Day
            _observed(date(year, 11, 11)),  # Veterans Day
            thanksgiving,
            thanksgiving + timedelta(days=1),
            _observed(date(year, 12, 25)),  # Christmas Day
        )
    )


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_federal_holidays(day.year)


def add_business_days(day: date, count: int) -> date:
    """Step ``count`` business days forward (or backward when negative)."""
    step = timedelta(days=1 if count >= 0 else -1)
    stepped = day
    for _ in range(abs(count)):
        stepped += step
        while not is_business_day(stepped):
            stepped += step
    return stepped


def prior_business_day(day: date) -> date:
    """The nearest prior business day, walking forward instead when the
    snap would leave the month (a drawn Jan 1 posts Jan 2, not Dec 31)."""
    snapped = day
    while not is_business_day(snapped):
        snapped -= timedelta(days=1)
    if snapped.month != day.month:
        snapped = day
        while not is_business_day(snapped):
            snapped += timedelta(days=1)
    return snapped
