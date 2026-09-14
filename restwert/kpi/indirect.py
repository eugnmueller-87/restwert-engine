"""Indirect procurement KPIs (SPEC 7.2, area "Indirect").

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``KPI_IND_SAVINGS_CONFIRMED`` "Savings vs plan confirmed by controlling"
- ``KPI_IND_SPEND_UNDER_MGMT`` "Share of spend under management"
- ``KPI_IND_PO_RATE`` "PO rate"
- ``KPI_IND_MAVERICK_SHARE`` "Maverick share"
- ``KPI_IND_ACTIVE_SUPPLIERS`` "Active suppliers"

Choices where the spec is silent:
- The fiscal year is the calendar year of ``as_of``. The savings plan comes
  from ``config/kpi_targets.yaml`` (owner recorded there), never from a table.
- Unconfirmed savings never enter the numerator; the claimed total (all saving
  rows of the year) is spelled out in the note only.
- Only ``saving_type = 'hard_price_reduction'`` counts against the plan. Cost
  avoidance and rebates are confirmed savings too, but they are reported
  separately in the note and the breakdown (dimension ``saving_type``) so a
  negotiated price reduction and an avoided increase are never added up as one
  number. Rows without a ``saving_type`` (real data without the column) count
  as hard and are flagged as "untyped" in the note.
- "Active suppliers" is a count, so ``numerator`` = the count and
  ``denominator`` is ``None``; the breakdown counts distinct suppliers per
  category, purchase orders counted under category ``hardware``.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from restwert.config import KpiTargets
from restwert.kpi.registry import (
    KpiValue,
    as_bool,
    fmt_window,
    load_table,
    make_breakdown,
    ok,
    ratio,
    ratio_breakdown,
    register,
    to_datetime,
    to_numeric,
    trailing_window,
    window_mask,
)


def _indirect_window(con: duckdb.DuckDBPyConnection, as_of: date) -> tuple[pd.DataFrame | None, str, tuple[date, date]]:
    frame, note = load_table(con, "indirect_spend", ("invoice_date", "amount", "has_po", "has_contract"))
    start, end = trailing_window(as_of, 12)
    if frame is None:
        return None, note, (start, end)
    to_datetime(frame, ["invoice_date"])
    to_numeric(frame, ["amount", "saving"])
    sel = frame[window_mask(frame["invoice_date"], start, end) & frame["amount"].notna()].copy()
    if sel.empty:
        return None, f"no indirect spend between {fmt_window(start, end)}", (start, end)
    sel["has_po"] = as_bool(sel["has_po"])
    sel["has_contract"] = as_bool(sel["has_contract"])
    return sel, "", (start, end)


def _share(con: duckdb.DuckDBPyConnection, as_of: date, flag_fn, label: str) -> KpiValue:
    sel, note, (start, end) = _indirect_window(con, as_of)
    if sel is None:
        return KpiValue.not_measurable(note)
    sel["flagged_amount"] = sel["amount"].where(flag_fn(sel), 0.0)
    numerator = float(sel["flagged_amount"].sum())
    denominator = float(sel["amount"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("total indirect spend in window is zero")
    breakdown = ratio_breakdown(sel, "category", "flagged_amount", "amount")
    return ok(
        value,
        numerator,
        denominator,
        int(len(sel)),
        note=f"{label}: {numerator:,.2f} of {denominator:,.2f} EUR over {len(sel)} invoices; window {fmt_window(start, end)}",
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_IND_SAVINGS_CONFIRMED",
    name="Savings vs plan confirmed by controlling",
    area="Indirect",
    definition=(
        "Hard savings (saving_type = hard_price_reduction: a negotiated price below the baseline_amount) on indirect "
        "invoices of the fiscal year (calendar year of as_of) that controlling has confirmed, against the savings "
        "plan for that year in config/kpi_targets.yaml. Claimed but unconfirmed savings never enter the numerator; "
        "confirmed cost avoidance and rebates are reported separately in the note and breakdown, never added to "
        "hard savings. Rows without a saving_type count as hard and are flagged as untyped. On the shipped synthetic "
        "data the plan is a placeholder scaled to the generator's run-rate (see kpi_targets.yaml)."
    ),
    formula_text=(
        "sum(indirect_spend.saving where saving_confirmed_by_controlling and coalesce(saving_type, 'hard_price_reduction') "
        "= 'hard_price_reduction' and invoice_date in fiscal year of as_of) / targets.savings_plan_eur[year]; "
        "confirmed cost_avoidance and rebate totals and the claimed total in the note, never in the numerator"
    ),
    source_tables=("indirect_spend", "config/kpi_targets.yaml"),
    measurable_from=(
        "a savings plan for the year in kpi_targets.yaml, a controlling confirmation flag and a saving_type per saving "
        "(baseline_amount documents the price before the saving)"
    ),
    unit="ratio",
    direction="up",
)
def savings_confirmed_vs_plan(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Confirmed savings over plan; claimed total in the note only."""

    year = as_of.year
    plan = targets.savings_plan_eur.get(year) if targets is not None else None
    if plan is None:
        plan = targets.savings_plan_eur.get(str(year)) if targets is not None else None  # tolerate str keys
    if plan is None or float(plan) <= 0:
        return KpiValue.not_measurable(f"no savings plan for {year} in kpi_targets.yaml")
    frame, note = load_table(con, "indirect_spend", ("invoice_date", "saving", "saving_confirmed_by_controlling"))
    if frame is None:
        return KpiValue.not_measurable(note)
    to_datetime(frame, ["invoice_date"])
    to_numeric(frame, ["saving"])
    in_year = frame[(frame["invoice_date"].dt.year == year) & frame["saving"].notna()].copy()
    if in_year.empty:
        return KpiValue.not_measurable(f"no indirect invoice dated in {year}")
    in_year["confirmed"] = as_bool(in_year["saving_confirmed_by_controlling"])
    if "saving_type" in in_year.columns:
        typed = in_year["saving_type"].astype(object).where(in_year["saving_type"].notna(), None)
    else:
        typed = pd.Series([None] * len(in_year), index=in_year.index, dtype=object)
    in_year["untyped"] = typed.isna()
    in_year["saving_type_eff"] = typed.fillna("hard_price_reduction").astype(str)
    claimed_rows = in_year[in_year["saving"] > 0]
    confirmed_all = claimed_rows[claimed_rows["confirmed"]]
    confirmed_rows = confirmed_all[confirmed_all["saving_type_eff"] == "hard_price_reduction"]
    numerator = float(confirmed_rows["saving"].sum())
    claimed = float(claimed_rows["saving"].sum())
    denominator = float(plan)
    by_type = confirmed_all.groupby("saving_type_eff")["saving"].agg(["sum", "count"])
    avoidance = float(by_type.loc["cost_avoidance", "sum"]) if "cost_avoidance" in by_type.index else 0.0
    rebate = float(by_type.loc["rebate", "sum"]) if "rebate" in by_type.index else 0.0
    n_untyped = int(confirmed_rows["untyped"].sum())
    breakdown = None
    if not confirmed_all.empty:
        rows = []
        for stype, grp in confirmed_all.groupby("saving_type_eff", sort=True):
            s = float(grp["saving"].sum())
            rows.append({"dimension_value": str(stype), "value": s / denominator, "numerator": s, "denominator": denominator, "n": int(len(grp))})
        breakdown = make_breakdown("saving_type", rows)
    note = (
        f"fiscal year {year}: confirmed hard savings {numerator:,.2f} EUR of plan {denominator:,.2f} EUR "
        f"(owner {targets.savings_plan_owner}); confirmed cost avoidance {avoidance:,.2f} EUR and rebates "
        f"{rebate:,.2f} EUR reported separately, not in the numerator; claimed total {claimed:,.2f} EUR over "
        f"{len(claimed_rows)} rows, of which {len(confirmed_all)} confirmed"
    )
    if n_untyped:
        note += f"; {n_untyped} confirmed row(s) without saving_type counted as hard (untyped)"
    return ok(
        numerator / denominator,
        numerator,
        denominator,
        int(len(confirmed_rows)),
        note=note,
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_IND_SPEND_UNDER_MGMT",
    name="Share of spend under management",
    area="Indirect",
    definition="Share of indirect spend in the trailing 12 months that carries a purchase order or a contract.",
    formula_text="sum(amount where has_po or has_contract) / sum(amount), trailing 12 months",
    source_tables=("indirect_spend",),
    measurable_from="has_po and has_contract flags per invoice",
    unit="ratio",
    direction="up",
)
def share_of_spend_under_management(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Spend with PO or contract over total, breakdown by category."""

    return _share(con, as_of, lambda f: f["has_po"] | f["has_contract"], "spend under management")


@register(
    kpi_id="KPI_IND_PO_RATE",
    name="PO rate",
    area="Indirect",
    definition="Share of indirect spend in the trailing 12 months that was ordered through a purchase order.",
    formula_text="sum(amount where has_po) / sum(amount), trailing 12 months",
    source_tables=("indirect_spend",),
    measurable_from="has_po flag per invoice",
    unit="ratio",
    direction="up",
)
def po_rate(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Spend with PO over total, breakdown by category."""

    return _share(con, as_of, lambda f: f["has_po"], "PO-backed spend")


@register(
    kpi_id="KPI_IND_MAVERICK_SHARE",
    name="Maverick share",
    area="Indirect",
    definition="Share of indirect spend in the trailing 12 months with neither a purchase order nor a contract.",
    formula_text="sum(amount where not has_po and not has_contract) / sum(amount), trailing 12 months",
    source_tables=("indirect_spend",),
    measurable_from="has_po and has_contract flags per invoice",
    unit="ratio",
    direction="down",
)
def maverick_share(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Spend without PO and without contract over total, breakdown by category."""

    return _share(con, as_of, lambda f: ~f["has_po"] & ~f["has_contract"], "maverick spend")


@register(
    kpi_id="KPI_IND_ACTIVE_SUPPLIERS",
    name="Active suppliers",
    area="Indirect",
    definition=(
        "Number of distinct suppliers with indirect spend above zero or a purchase order delivered in the trailing "
        "12 months. A count, so the denominator is empty; the breakdown counts suppliers per category "
        "(purchase orders under 'hardware')."
    ),
    formula_text="count(distinct supplier) with indirect_spend amount > 0 or a PO delivered in trailing 12 months",
    source_tables=("indirect_spend", "purchase_orders"),
    measurable_from="supplier names on invoices and purchase orders",
    unit="count",
    direction="down",
)
def active_suppliers(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Distinct active suppliers across indirect spend and hardware POs."""

    start, end = trailing_window(as_of, 12)
    ind, ind_note = load_table(con, "indirect_spend", ("supplier", "invoice_date", "amount"), allow_empty=True)
    po, po_note = load_table(con, "purchase_orders", ("supplier", "delivered_date"), allow_empty=True)
    if ind is None and po is None:
        return KpiValue.not_measurable(f"{ind_note}; {po_note}")
    suppliers: set[str] = set()
    per_category: dict[str, set[str]] = {}
    if ind is not None and not ind.empty:
        to_datetime(ind, ["invoice_date"])
        to_numeric(ind, ["amount"])
        sel = ind[window_mask(ind["invoice_date"], start, end) & (ind["amount"] > 0) & ind["supplier"].notna()]
        suppliers |= set(sel["supplier"].astype(str))
        cat_col = sel["category"].astype(str) if "category" in sel.columns else pd.Series("indirect", index=sel.index)
        for category, grp in sel.groupby(cat_col, sort=True):
            per_category.setdefault(str(category), set()).update(grp["supplier"].astype(str))
    if po is not None and not po.empty:
        to_datetime(po, ["delivered_date"])
        sel = po[window_mask(po["delivered_date"], start, end) & po["supplier"].notna()]
        suppliers |= set(sel["supplier"].astype(str))
        if not sel.empty:
            per_category.setdefault("hardware", set()).update(sel["supplier"].astype(str))
    n = len(suppliers)
    if n == 0:
        return KpiValue.not_measurable(f"no supplier with spend or delivery between {fmt_window(start, end)}")
    rows = [
        {"dimension_value": category, "value": float(len(names)), "numerator": float(len(names)), "denominator": None, "n": len(names)}
        for category, names in sorted(per_category.items())
    ]
    return ok(
        float(n),
        float(n),
        None,
        n,
        note=f"{n} distinct suppliers active {fmt_window(start, end)}; a supplier serving several categories is counted once in the total",
        breakdown=make_breakdown("category", rows),
    )
