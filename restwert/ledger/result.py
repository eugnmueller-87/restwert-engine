"""Pure scalar result formulas of the ledger (SPEC_v0.2 6.2). Hand-testable, no I/O.

Closed cycle::

    lifecycle_result_eur  = round(SUM(amount_eur), 2)                       [sold, scrapped]
    result_v01_basis_eur  = SUM(amount_eur) excluding V01_BRIDGE_LINE_TYPES  (= v0.1 lifecycle_margin)

Open cycle, two numbers that are never added (decision D13)::

    result_if_liquidated_today   = sum_lines_to_date + estimate_rv_today x (1 - fee_pct_mkt) - fee_fixed_mkt
    result_projected_at_lease_end = sum_lines_to_date + remaining_contracted_rent
                                    + estimate_rv_lease_end x (1 - fee_pct_mkt) - fee_fixed_mkt
                                    - expected_remaining_cost

``result_if_liquidated_today`` answers "what if every open device were sold today at the
fleet model's marketplace estimate"; remaining rent and the value at lease end are NOT in
it. ``result_projected_at_lease_end`` is the projection to the planned end of the rental
(label ``projected at lease end``) or, for a returned device, to its sale (label
``projected at sale``).

``anchor_rv`` is the public-anchor sanity check: the marketplace ask curve of the same
catalogue family and manufacturer (``bronze.mkt_curves``) evaluated at the same age and
grade. It is NULL outside the fitted age range, and it enters no result, no rule and no
lever: the fleet model decides, the anchor advises.
"""

from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from restwert.dates import DAYS_PER_MONTH, months_between_float
from restwert.ledger.lines import V01_BRIDGE_LINE_TYPES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from restwert.config import Assumptions


PROJECTED_LABEL_LEASE_END = "projected at lease end"
PROJECTED_LABEL_SALE = "projected at sale"
LIQUIDATION_LABEL = (
    "if every open device were sold today at the fleet model's marketplace estimate; "
    "remaining rent and the value at lease end are NOT in this number"
)

#: Grade offsets read from a curve row: column per grade; B is the reference (0).
_GRADE_OFFSET_COLUMNS: dict[str, str | None] = {
    "A": "grade_A_offset",
    "B": None,
    "C": "grade_C_offset",
    "D": "grade_D_offset",
}


def _r2(v: float) -> float:
    return round(float(v) + 0.0, 2)


def _sum_amounts(lines_of_serial: pd.DataFrame) -> float:
    if lines_of_serial is None or len(lines_of_serial) == 0:
        return 0.0
    return float(pd.to_numeric(lines_of_serial["amount_eur"], errors="coerce").fillna(0.0).sum())


def result_closed(lines_of_serial: pd.DataFrame) -> float:
    """``round(sum(amount_eur), 2)`` over the lines of one serial (signed amounts)."""
    return _r2(_sum_amounts(lines_of_serial))


def result_v01_basis(lines_of_serial: pd.DataFrame) -> float:
    """The sum excluding the v0.1 bridge types (staging, outbound, wipe_grading, holding, pp credit).

    Equals v0.1 ``pnl.lifecycle.lifecycle_margin`` on the same transactions.
    """
    if lines_of_serial is None or len(lines_of_serial) == 0:
        return 0.0
    keep = ~lines_of_serial["line_type"].astype(str).isin(V01_BRIDGE_LINE_TYPES)
    return _r2(_sum_amounts(lines_of_serial[keep]))


def result_if_liquidated_today(
    sum_lines_to_date: float, estimate_rv_today: float, fee_pct: float, fee_fixed: float
) -> float:
    """``sum_lines_to_date + estimate_rv_today x (1 - fee_pct) - fee_fixed`` (marketplace fees)."""
    return _r2(float(sum_lines_to_date) + float(estimate_rv_today) * (1.0 - float(fee_pct)) - float(fee_fixed))


def result_projected_at_lease_end(
    sum_lines_to_date: float,
    remaining_rent: float,
    estimate_rv_lease_end: float,
    fee_pct: float,
    fee_fixed: float,
    expected_remaining_cost: float,
) -> float:
    """``sum_lines + remaining_rent + estimate_rv_lease_end x (1 - fee_pct) - fee_fixed - expected_remaining_cost``."""
    return _r2(
        float(sum_lines_to_date)
        + float(remaining_rent)
        + float(estimate_rv_lease_end) * (1.0 - float(fee_pct))
        - float(fee_fixed)
        - float(expected_remaining_cost)
    )


def remaining_contracted_rent(monthly_rate: float | None, term_months: int | None, months_billed_latest: int, active: bool) -> tuple[float, int]:
    """``(monthly_rate x months_remaining, months_remaining)`` with ``months_remaining = max(term - billed, 0)``; 0 unless active."""
    if not active or monthly_rate is None or term_months is None:
        return 0.0, 0
    months_remaining = max(int(term_months) - int(months_billed_latest), 0)
    return _r2(float(monthly_rate) * months_remaining), months_remaining


def estimate_months_at_lease_end(launch_date: date, end_date: date, return_to_sale_days: float) -> int:
    """``round(months_between_float(launch, end) + return_to_sale_days / DAYS_PER_MONTH)`` (Python round)."""
    return int(round(months_between_float(launch_date, end_date) + float(return_to_sale_days) / DAYS_PER_MONTH))


# --------------------------------------------------------------------------------------
# expected remaining cost of an open cycle
# --------------------------------------------------------------------------------------

def _a(a: "Assumptions", key: str, family: str, default: float) -> float:
    try:
        return float(a.get(key, family))
    except (KeyError, TypeError, ValueError):
        return default


def expected_remaining_cost(
    status: str,
    family: str,
    months_remaining: int,
    has_finished_work_order: bool,
    inputs: dict[str, Any],
    a: "Assumptions",
    days_since_return: int = 0,
    mdm_enrolled: bool = False,
) -> tuple[float, str]:
    """Expected cost still to come for one open device, with its inputs source.

    ``inputs`` is the family's realised statistics as ``pnl.tco._family_realised_inputs``
    returns them (``n_damage``, ``n_repair``, ``sum_repair_cost``, ``device_years``,
    ``n_refurb``, ``sum_refurb_cost``, ``logistics_by_device``) plus ``wipe_grading_mean``
    and ``n_wipe`` (fleet mean of the family's wipe_grading lines) and ``min_n``. Every
    input below ``min_n`` falls back to the owner-named assumption; the second value says
    ``realised``, ``assumptions`` or ``mixed`` (``none`` when nothing is expected).

    The holding term uses the same stock phases the ledger books
    (``lines.HOLDING_PHASES``): the return and sale phases together are expected to last
    ``expected_return_to_sale_days[family]``, and ``days_since_return`` (return receipt to
    ``as_of``, already booked as holding lines) is deducted, floored at 0. A projected result
    and a closed result therefore share one holding basis.

    * rented: ``damage_rate_pa x (months_remaining / 12) x repair_share x mean_repair_cost
      + logistics + refurb + wipe_grading_mean + holding_cost_per_day x expected_return_to_sale_days
      + months_remaining x (support_cost_per_device_month_eur + mdm_cost_per_device_month_eur
      if mdm_enrolled)``: the two team-cost allocations the ledger books on every future rental
      invoice, so a projected result and a closed result share one allocation basis
    * awaiting_return: the same without the repair term
    * wip: ``refurb (unless a work order finished) + holding x max(0, expected_return_to_sale_days - days_since_return)``
    * in_stock: ``holding x max(0, expected_return_to_sale_days - days_since_return)``
    """
    status = (status or "").lower()
    if status not in ("rented", "awaiting_return", "wip", "in_stock"):
        return 0.0, "none"
    min_n = int(inputs.get("min_n", 30))
    sources: list[str] = []
    holding = _scalar(a, "holding_cost_per_day_eur", 0.0)
    rts_days = _a(a, "expected_return_to_sale_days", family, 35.0)
    total = 0.0

    if status == "rented":
        n_repair = int(inputs.get("n_repair", 0))
        n_damage = int(inputs.get("n_damage", 0))
        device_years = float(inputs.get("device_years", 0.0))
        if n_repair >= min_n and n_damage > 0 and device_years > 0:
            damage_rate = n_damage / device_years
            repair_share = n_repair / n_damage
            mean_repair = float(inputs.get("sum_repair_cost", 0.0)) / n_repair
            sources.append("realised")
        else:
            damage_rate = _a(a, "damage_rate_pa_fallback", family, 0.10)
            repair_share = _a(a, "repair_share_fallback", family, 0.70)
            mean_repair = _a(a, "repair_cost_fallback_eur", family, 150.0)
            sources.append("assumptions")
        total += damage_rate * (max(int(months_remaining), 0) / 12.0) * repair_share * mean_repair

    if status in ("rented", "awaiting_return"):
        log_by_dev: dict = inputs.get("logistics_by_device", {}) or {}
        if len(log_by_dev) >= min_n:
            total += sum(log_by_dev.values()) / len(log_by_dev)
            sources.append("realised")
        else:
            total += _a(a, "logistics_cost_fallback_eur", family, 12.0)
            sources.append("assumptions")
        wipe_mean = inputs.get("wipe_grading_mean")
        if wipe_mean is not None and int(inputs.get("n_wipe", 0)) >= min_n:
            total += float(wipe_mean)
            sources.append("realised")
        else:
            total += _a(a, "logistics_cost_fallback_eur", family, 12.0) / 2.0
            sources.append("assumptions")

    if status in ("rented", "awaiting_return", "wip") and not (status == "wip" and has_finished_work_order):
        n_refurb = int(inputs.get("n_refurb", 0))
        if n_refurb >= min_n:
            total += float(inputs.get("sum_refurb_cost", 0.0)) / n_refurb
            sources.append("realised")
        else:
            total += _a(a, "refurb_cost_fallback_eur", family, 40.0)
            sources.append("assumptions")

    if status == "rented":
        months_left = max(int(months_remaining), 0)
        allocation = _scalar(a, "support_cost_per_device_month_eur", 0.0)
        if mdm_enrolled:
            allocation += _scalar(a, "mdm_cost_per_device_month_eur", 0.0)
        if months_left > 0 and allocation > 0:
            total += months_left * allocation
            sources.append("assumptions")

    if status in ("rented", "awaiting_return"):
        total += holding * rts_days
        sources.append("assumptions")
    elif status in ("wip", "in_stock"):
        # the return and sale phases already booked up to as_of are deducted, never charged twice
        total += holding * max(0.0, float(rts_days) - max(int(days_since_return or 0), 0))
        sources.append("assumptions")

    uniq = set(sources)
    source = "realised" if uniq == {"realised"} else "assumptions" if uniq == {"assumptions"} else "mixed"
    return _r2(total), source


def _scalar(a: "Assumptions", key: str, default: float) -> float:
    try:
        return float(a.get(key))
    except (KeyError, TypeError, ValueError):
        return default


# --------------------------------------------------------------------------------------
# public anchor
# --------------------------------------------------------------------------------------

def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def select_anchor_curve(mkt_curves: pd.DataFrame | None, catalogue_family: str | None, oem: str | None) -> pd.Series | None:
    """The marketplace curve row for (family, oem): ``family_oem`` when ``fit_quality == ok``, else ``family``.

    Mirrors ``lakegen.calibrate.truth_curve`` without the default curve: when neither row
    exists the anchor is simply absent (NULL), never invented.
    """
    if mkt_curves is None or len(mkt_curves) == 0 or not catalogue_family:
        return None
    needed = {"group_kind", "group", "population", "fit_quality"}
    if not needed.issubset(mkt_curves.columns):
        return None
    mkt = mkt_curves[mkt_curves["population"].astype(str) == "marketplace"]
    if oem:
        row = mkt[(mkt["group_kind"].astype(str) == "family_oem") & (mkt["group"].astype(str) == f"{catalogue_family} / {oem}")]
        if len(row) and str(row["fit_quality"].iloc[0]) == "ok":
            return row.iloc[0]
    row = mkt[(mkt["group_kind"].astype(str) == "family") & (mkt["group"].astype(str) == str(catalogue_family))]
    if len(row):
        return row.iloc[0]
    return None


def anchor_rv(
    rrp_net: float | None,
    curve_row: pd.Series | None,
    age_months: float | None,
    grade: str | None,
    grade_d_default: float,
) -> tuple[float | None, str | None, str | None]:
    """``rrp_net x exp(intercept + slope x age + offset[grade])`` from a ``bronze.mkt_curves`` row.

    Returns ``(value, curve_group, fit_quality)``; the value is ``None`` when the row is
    absent, the inputs are missing or ``age_months`` lies outside ``[age_min, age_max]``
    of the row (no extrapolation for an advisory number). Grade B offset is 0, grade D
    takes ``grade_d_default`` (the public rows carry no grade D fit).
    """
    if curve_row is None:
        return None, None, None
    group = str(curve_row.get("group")) if curve_row.get("group") is not None else None
    fit = str(curve_row.get("fit_quality")) if curve_row.get("fit_quality") is not None else None
    rrp = _f(rrp_net)
    age = _f(age_months)
    intercept = _f(curve_row.get("intercept"))
    slope = _f(curve_row.get("slope_per_month"))
    age_min = _f(curve_row.get("age_min"))
    age_max = _f(curve_row.get("age_max"))
    if rrp is None or age is None or intercept is None or slope is None:
        return None, group, fit
    if age_min is not None and age < age_min:
        return None, group, fit
    if age_max is not None and age > age_max:
        return None, group, fit
    g = (grade or "B").upper()
    col = _GRADE_OFFSET_COLUMNS.get(g, None)
    if g == "D":
        offset = float(grade_d_default)
    elif col is None:
        offset = 0.0
    else:
        offset = _f(curve_row.get(col)) or 0.0
    value = rrp * math.exp(intercept + slope * age + offset)
    return _r2(value), group, fit


__all__ = [
    "PROJECTED_LABEL_LEASE_END",
    "PROJECTED_LABEL_SALE",
    "LIQUIDATION_LABEL",
    "result_closed",
    "result_v01_basis",
    "result_if_liquidated_today",
    "result_projected_at_lease_end",
    "remaining_contracted_rent",
    "estimate_months_at_lease_end",
    "expected_remaining_cost",
    "select_anchor_curve",
    "anchor_rv",
]
