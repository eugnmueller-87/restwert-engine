"""Book value, management view (spec section 4.2).

Straight line from ``landed_cost`` down to the planned residual value over the family's
depreciation horizon, floored at the planned residual value. This is a management view for
steering the fleet, not an IFRS 16 or HGB valuation. Write-downs on top of the straight
line come only from decision rule R03 and are booked in ``write_down_ledger`` by module 4;
``lifecycle.build_device_pnl`` subtracts them.

Formulas (spec section 9)::

    planned_rv      = landed_cost * planned_rv_ratio[family]
    book_value_sl   = max(landed - (landed - planned_rv) * min(m, D) / D, planned_rv)
                      with m = months_between(purchase_date, as_of), D = depreciation_months[family]
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from restwert.dates import months_between

if TYPE_CHECKING:  # pragma: no cover - typing only
    from restwert.config import Assumptions


def planned_rv(landed_cost: float, family: str, a: "Assumptions") -> float:
    """Planned residual value in EUR: ``landed_cost * a.get("planned_rv_ratio", family)``.

    Raises ``KeyError`` (from ``Assumptions.get``) when the family has no planned ratio,
    so an unknown family never silently gets a zero residual.
    """
    ratio = float(a.get("planned_rv_ratio", family))
    return round(float(landed_cost) * ratio, 2)


def straight_line_book_value(
    landed_cost: float,
    planned_rv_eur: float,
    purchase_date: date,
    depreciation_months: int,
    as_of: date,
) -> float:
    """Straight-line book value at ``as_of``, rounded to 2 decimals.

    ``m = min(months_between(purchase_date, as_of), depreciation_months)``; the value is
    ``landed - (landed - planned) * m / D`` floored at ``planned_rv_eur``. Equals
    ``landed_cost`` at ``m = 0`` and ``planned_rv_eur`` at and beyond ``D`` months.
    A non-positive ``depreciation_months`` is treated as fully depreciated (planned value).
    """
    landed = float(landed_cost)
    planned = float(planned_rv_eur)
    d = int(depreciation_months)
    if d <= 0:
        return round(max(planned, 0.0), 2)
    m = min(months_between(purchase_date, as_of), d)
    value = landed - (landed - planned) * m / d
    return round(max(value, planned), 2)
