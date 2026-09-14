"""Single source of date arithmetic (SPEC.md section 2.3).

Every module that counts months, finds month ends or reasons about launch
dates goes through these functions, so that the definition of "a month" is
the same in the P&L, the forecast and the decision rules.

Design choices where the spec is silent:

* ``months_between`` counts completed calendar months: the month counter
  advances only when the day of month has been reached again. This is why
  2024-01-31 to 2024-02-29 is 0 (February has no 31st).
* ``next_launch_date`` and ``expected_launch_steps`` use ONLY the calendar
  rule ``first_launch + k * launch_cadence_months``. They never look at the
  model catalogue, which is what makes them safe as a leakage-free source of
  future launches.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Mapping

DAYS_PER_MONTH: float = 30.4375


def months_between(start: date, end: date) -> int:
    """Completed calendar months from ``start`` to ``end``; 0 if ``end <= start``.

    >>> months_between(date(2024, 1, 31), date(2024, 2, 29))
    0
    >>> months_between(date(2024, 1, 15), date(2024, 3, 15))
    2
    """
    if end <= start:
        return 0
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        m -= 1
    return max(m, 0)


def months_between_float(start: date, end: date) -> float:
    """Fractional months: ``(end - start).days / DAYS_PER_MONTH``. May be negative."""
    return (end - start).days / DAYS_PER_MONTH


def month_floor(d: date) -> date:
    """First day of the month of ``d``."""
    return date(d.year, d.month, 1)


def month_end(d: date) -> date:
    """Last day of the month of ``d``."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    """Calendar month addition with day clamping (Jan 31 + 1 month = Feb 28/29)."""
    total = d.year * 12 + (d.month - 1) + n
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_ends(start: date, end: date) -> list[date]:
    """Every month end ``me`` with ``start <= me <= end`` in ascending order."""
    out: list[date] = []
    if end < start:
        return out
    cur = month_end(start)
    while cur <= end:
        out.append(cur)
        cur = month_end(add_months(month_floor(cur), 1))
    return out


def quarter_label(d: date) -> str:
    """``'YYYY-Qn'`` label of the quarter containing ``d``."""
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def days_between(start: date, end: date) -> int:
    """``(end - start).days``; negative when ``end`` is before ``start``."""
    return (end - start).days


def _family_cfg(family: str, families_cfg: Mapping[str, Any]) -> Any:
    try:
        return families_cfg[family]
    except KeyError as exc:
        raise KeyError(f"unknown family {family!r}") from exc


def next_launch_date(family: str, after: date, families_cfg: Mapping[str, Any]) -> date | None:
    """First calendar-rule launch date strictly after ``after``.

    Calendar rule only: ``first_launch + k * launch_cadence_months`` for the
    smallest ``k >= 0`` that lands after ``after``. Returns ``None`` when the
    family has no cadence (``launch_cadence_months`` is null).
    """
    fc = _family_cfg(family, families_cfg)
    cadence = fc.launch_cadence_months
    first: date = fc.first_launch
    if cadence is None or cadence <= 0:
        return None
    if after < first:
        return first
    # start close to the answer to avoid long loops on far-away dates
    k = max(0, months_between(first, after) // cadence - 1)
    d = add_months(first, k * cadence)
    while d <= after:
        k += 1
        d = add_months(first, k * cadence)
    return d


def expected_launch_steps(family: str, start: date, end: date, families_cfg: Mapping[str, Any]) -> int:
    """Number of calendar-rule launch dates ``d`` with ``start < d <= end``.

    0 if ``end <= start`` or the family has no cadence.
    """
    if end <= start:
        return 0
    fc = _family_cfg(family, families_cfg)
    if fc.launch_cadence_months is None:
        return 0
    n = 0
    d = next_launch_date(family, start, families_cfg)
    while d is not None and d <= end:
        n += 1
        d = next_launch_date(family, d, families_cfg)
    return n


__all__ = [
    "DAYS_PER_MONTH",
    "months_between",
    "months_between_float",
    "month_floor",
    "month_end",
    "month_ends",
    "add_months",
    "quarter_label",
    "days_between",
    "next_launch_date",
    "expected_launch_steps",
]
