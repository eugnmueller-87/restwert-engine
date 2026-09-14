"""The 14 gold KPIs of the device cycle, one page each (SPEC_v0.2 section 8.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every KPI reads DuckDB tables only (``silver.*``, ``gold.*``, ``bronze.*``)
and returns a ``KpiValue``. A missing table, a missing column, an empty
window or a zero denominator ends in ``not_measurable`` with a note, never
in 0. Trailing windows are ``kpi.registry.trailing_window(as_of, 12)``:
``add_months(as_of, -12) + 1 day`` to ``as_of``, both inclusive.

Choices where the spec is silent:

- "closed in the window" means ``is_closed`` and ``closed_date`` inside the
  window; a closed device without ``closed_date`` is excluded and counted in
  the note.
- ``KPI_PUR_PRICE_PROTECTION_CAPTURE`` counts credited value from
  ``price_protection_credit_eur`` on serials with status ``claimed`` and
  missed value from ``price_protection_claimable_eur`` on status ``missed``;
  ``open`` and ``not_applicable`` serials are outside the denominator.
- ``KPI_RSL_REALISED_VS_RECORD`` needs an estimate of record above zero on
  the serial; sales without one are excluded and counted.
- ``KPI_LEV_ADDITIVE_EUR_PA`` reads ``gold.levers_summary`` and sums
  ``eur_fleet_per_year`` over the additive levers only, because the
  non-additive ones must never be totalled (docs/LEVERS.md).
"""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd

from restwert.config import KpiTargets
from restwert.gold.registry import (
    KpiValue,
    fmt_window,
    load_table,
    make_breakdown,
    ok,
    ratio,
    register_gold,
    to_datetime,
    to_numeric,
    trailing_window,
    window_mask,
)

DEVICE_LEDGER = "silver.device_ledger"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _breakdowns(frame: pd.DataFrame, dims: tuple[str, ...], num_col: str, den_col: str | None, transform=None) -> pd.DataFrame | None:
    """Concatenate one ratio breakdown per dimension (sum(num) / sum(den), count when den is None)."""

    parts: list[pd.DataFrame] = []
    for dim in dims:
        if dim not in frame.columns:
            continue
        rows = []
        for key, grp in frame.groupby(frame[dim].astype(object).where(frame[dim].notna(), "(none)").astype(str), sort=True):
            num = float(grp[num_col].sum())
            den = float(grp[den_col].sum()) if den_col else float(len(grp))
            value = ratio(num, den)
            if transform is not None and value is not None:
                value = transform(value)
            rows.append({"dimension_value": key, "value": value, "numerator": num, "denominator": den, "n": int(len(grp))})
        bd = make_breakdown(dim, rows)
        if bd is not None:
            parts.append(bd)
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


def _median_breakdown(frame: pd.DataFrame, dim: str, col: str) -> pd.DataFrame | None:
    if dim not in frame.columns:
        return None
    rows = []
    for key, grp in frame.groupby(frame[dim].astype(object).where(frame[dim].notna(), "(none)").astype(str), sort=True):
        rows.append({"dimension_value": key, "value": float(grp[col].median()), "numerator": None, "denominator": None, "n": int(len(grp))})
    return make_breakdown(dim, rows)


def _ledger(con: duckdb.DuckDBPyConnection, required: tuple[str, ...]) -> tuple[pd.DataFrame | None, str]:
    frame, note = load_table(con, DEVICE_LEDGER, required)
    if frame is None:
        return None, note
    to_datetime(frame, ["received_at", "purchase_date", "closed_date", "sale_date", "credited_at", "order_date"])
    to_numeric(
        frame,
        [
            "purchase_price", "rrp_net_eur", "landed_cost", "price_protection_credit_eur", "price_protection_claimable_eur",
            "tco_eur", "holding_cost_eur", "estimate_rv_lease_end", "anchor_rv_lease_end", "resale_gross",
            "estimate_rv_of_record", "days_return_to_cash", "lifecycle_result_eur", "term_months",
        ],
    )
    return frame, ""


def _closed_in_window(frame: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, int, tuple[date, date]]:
    start, end = trailing_window(as_of, 12)
    closed = frame[frame["is_closed"].map(lambda v: bool(v) if v is not None and v == v else False)]
    undated = int(closed["closed_date"].isna().sum())
    sel = closed[window_mask(closed["closed_date"], start, end)].copy()
    return sel, undated, (start, end)


# ---------------------------------------------------------------------------
# 0 Data
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_DATA_CHAIN_COMPLETE",
    name="Share of serials with a complete timestamp chain",
    page="0 Data",
    definition=(
        "Serials whose timestamp chain (ordered, received, staged, shipped, returned, wiped, graded, sellable, sold, "
        "credited) has every step expected for their lifecycle status, in order, over all serials in silver.serial_timeline."
    ),
    formula_text="count(serial_timeline where chain_complete) / count(serial_timeline)",
    source_tables=("silver.serial_timeline",),
    unit="ratio",
    direction="up",
)
def data_chain_complete(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Complete chains over all serials; breakdown by lifecycle status and by first missing step."""

    tl, note = load_table(con, "silver.serial_timeline", ("serial", "chain_complete", "lifecycle_status"))
    if tl is None:
        return KpiValue.not_measurable(note)
    tl["complete"] = tl["chain_complete"].map(lambda v: 1.0 if v is not None and v == v and bool(v) else 0.0)
    n = int(len(tl))
    numerator = float(tl["complete"].sum())
    parts = [_breakdowns(tl, ("lifecycle_status",), "complete", None)]
    incomplete = tl[tl["complete"] == 0.0].copy()
    if not incomplete.empty and "first_missing_step" in incomplete.columns:
        incomplete["one"] = 1.0
        rows = []
        for key, grp in incomplete.groupby(incomplete["first_missing_step"].fillna("(none)").astype(str), sort=True):
            rows.append({"dimension_value": key, "numerator": float(len(grp)), "denominator": float(n), "n": int(len(grp))})
        parts.append(make_breakdown("first_missing_step", rows))
    breakdown = pd.concat([p for p in parts if p is not None], ignore_index=True) if any(p is not None for p in parts) else None
    return ok(ratio(numerator, n), numerator, float(n), n, note=f"{int(numerator)} of {n} serials with a complete chain", breakdown=breakdown)


@register_gold(
    kpi_id="KPI_DATA_UNRESOLVED_SHARE",
    name="Share of landing rows that could not be resolved",
    page="0 Data",
    definition=(
        "Rows of every ingested landing file that could not be typed or key-resolved (bronze.unresolved with reason "
        "codes other than duplicate_conflict) over all rows read, as recorded in bronze.deliveries.n_unresolved. "
        "Conflicting duplicates are counted under duplicates_conflict, not here; the breakdown lists them separately "
        "for reading. A source row that is re-delivered under a new file is recorded once per delivery."
    ),
    formula_text="sum(deliveries.n_unresolved) / sum(deliveries.rows_read)",
    source_tables=("bronze.deliveries", "bronze.unresolved"),
    unit="ratio",
    direction="down",
)
def data_unresolved_share(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Unresolved over read; breakdown by feed and by reason code."""

    deliveries, note = load_table(con, "bronze.deliveries", ("feed", "rows_read", "n_unresolved"))
    if deliveries is None:
        return KpiValue.not_measurable(note)
    to_numeric(deliveries, ["rows_read", "n_unresolved"])
    numerator = float(deliveries["n_unresolved"].sum())
    denominator = float(deliveries["rows_read"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("no landing rows read")
    parts = [_breakdowns(deliveries, ("feed",), "n_unresolved", "rows_read")]
    unresolved, _ = load_table(con, "bronze.unresolved", ("reason_code",), allow_empty=True)
    if unresolved is not None and not unresolved.empty:
        # the reason codes that are in the headline (conflicting duplicates are duplicates, not unresolved rows)
        reasons = unresolved[unresolved["reason_code"].astype(str) != "duplicate_conflict"]
        rows = []
        for key, grp in reasons.groupby(reasons["reason_code"].astype(str), sort=True):
            rows.append({"dimension_value": key, "numerator": float(len(grp)), "denominator": denominator, "n": int(len(grp))})
        if rows:
            parts.append(make_breakdown("reason_code", rows))
        n_conf = int((unresolved["reason_code"].astype(str) == "duplicate_conflict").sum())
        if n_conf:
            parts.append(make_breakdown("duplicates_conflict_not_in_headline", [
                {"dimension_value": "duplicate_conflict", "numerator": float(n_conf), "denominator": denominator, "n": n_conf}
            ]))
    breakdown = pd.concat([p for p in parts if p is not None], ignore_index=True) if any(p is not None for p in parts) else None
    n = int(len(deliveries))
    return ok(value, numerator, denominator, n, note=f"{int(numerator)} unresolved of {int(denominator)} rows read in {n} deliveries", breakdown=breakdown)


@register_gold(
    kpi_id="KPI_DATA_RECONCILED",
    name="Share of serials reconciled to device_pnl to the cent",
    page="0 Data",
    definition=(
        "Serials whose every silver.reconciliation row (ledger value vs v0.1 device_pnl value per field) is ok, over all "
        "serials with a reconciliation row. The pipeline refuses to finish on a failure, so anything below one is a finding."
    ),
    formula_text="count(distinct serial where every reconciliation row ok) / count(distinct serial)",
    source_tables=("silver.reconciliation",),
    unit="ratio",
    direction="one",
)
def data_reconciled(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Serials with every field ok; breakdown by field."""

    rec, note = load_table(con, "silver.reconciliation", ("serial", "field", "ok"))
    if rec is None:
        return KpiValue.not_measurable(note)
    rec["ok_f"] = rec["ok"].map(lambda v: 1.0 if v is not None and v == v and bool(v) else 0.0)
    per_serial = rec.groupby(rec["serial"].astype(str))["ok_f"].min()
    n = int(len(per_serial))
    numerator = float(per_serial.sum())
    breakdown = _breakdowns(rec, ("field",), "ok_f", None)
    return ok(ratio(numerator, n), numerator, float(n), n, note=f"{int(numerator)} of {n} serials reconciled on every field", breakdown=breakdown)


# ---------------------------------------------------------------------------
# 1 Purchase
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_PUR_DISCOUNT_VS_RRP",
    name="Purchase discount vs net launch RRP",
    page="1 Purchase",
    definition=(
        "One minus the paid unit price net of price protection credits over the net launch RRP of the variant (public "
        "catalogue RRP without VAT), summed over serials received in the trailing 12 months."
    ),
    formula_text="1 - sum(device_ledger.purchase_price - price_protection_credit_eur) / sum(device_ledger.rrp_net_eur), received_at in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="up",
)
def pur_discount_vs_rrp(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Discount vs net RRP; breakdown by manufacturer and supplier role."""

    dl, note = _ledger(con, ("received_at", "purchase_price", "rrp_net_eur"))
    if dl is None:
        return KpiValue.not_measurable(note)
    start, end = trailing_window(as_of, 12)
    sel = dl[window_mask(dl["received_at"], start, end) & dl["purchase_price"].notna() & dl["rrp_net_eur"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no serial received between {fmt_window(start, end)} with price and net RRP")
    credit = sel["price_protection_credit_eur"].fillna(0.0) if "price_protection_credit_eur" in sel.columns else 0.0
    sel["effective_price"] = sel["purchase_price"] - credit
    numerator = float(sel["effective_price"].sum())
    denominator = float(sel["rrp_net_eur"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("net RRP sum is zero")
    breakdown = _breakdowns(sel, ("oem", "supplier_role"), "effective_price", "rrp_net_eur", transform=lambda r: 1.0 - r)
    return ok(1.0 - value, numerator, denominator, n, note=f"{n} serials; paid {numerator:,.2f} EUR net of price protection credits against {denominator:,.2f} EUR net RRP; window {fmt_window(start, end)}", breakdown=breakdown)


@register_gold(
    kpi_id="KPI_PUR_LANDED_VS_RRP",
    name="Landed cost vs net launch RRP",
    page="1 Purchase",
    definition=(
        "Landed cost (unit price plus allocated freight and duty, net of price protection credits) over the net launch "
        "RRP, summed over serials received in the trailing 12 months."
    ),
    formula_text="sum(device_ledger.landed_cost - price_protection_credit_eur) / sum(device_ledger.rrp_net_eur), received_at in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="down",
)
def pur_landed_vs_rrp(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Landed over net RRP; breakdown by manufacturer."""

    dl, note = _ledger(con, ("received_at", "landed_cost", "rrp_net_eur"))
    if dl is None:
        return KpiValue.not_measurable(note)
    start, end = trailing_window(as_of, 12)
    sel = dl[window_mask(dl["received_at"], start, end) & dl["landed_cost"].notna() & dl["rrp_net_eur"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no serial received between {fmt_window(start, end)} with landed cost and net RRP")
    credit = sel["price_protection_credit_eur"].fillna(0.0) if "price_protection_credit_eur" in sel.columns else 0.0
    sel["effective_landed"] = sel["landed_cost"] - credit
    numerator = float(sel["effective_landed"].sum())
    denominator = float(sel["rrp_net_eur"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("net RRP sum is zero")
    return ok(value, numerator, denominator, n, note=f"{n} serials; landed cost net of price protection credits; window {fmt_window(start, end)}", breakdown=_breakdowns(sel, ("oem",), "effective_landed", "rrp_net_eur"))


@register_gold(
    kpi_id="KPI_PUR_PRICE_PROTECTION_CAPTURE",
    name="Price protection captured",
    page="1 Purchase",
    definition=(
        "Credited price protection value over credited plus missed value on serials received in the trailing 12 months. "
        "A serial is missed when a price drop fell inside its protection window and no credit note arrived before the "
        "claim window closed; open claims are outside the denominator."
    ),
    formula_text=(
        "sum(price_protection_credit_eur where status = claimed) / (that + sum(price_protection_claimable_eur where "
        "status = missed)), received_at in trailing 12 months"
    ),
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="up",
)
def pur_price_protection_capture(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Credited over credited plus missed; breakdown by manufacturer."""

    dl, note = _ledger(con, ("received_at", "price_protection_status"))
    if dl is None:
        return KpiValue.not_measurable(note)
    for col in ("price_protection_credit_eur", "price_protection_claimable_eur"):
        if col not in dl.columns:
            return KpiValue.not_measurable(f"table {DEVICE_LEDGER} lacks column {col}")
    start, end = trailing_window(as_of, 12)
    status = dl["price_protection_status"].astype(object).fillna("").astype(str)
    sel = dl[window_mask(dl["received_at"], start, end) & status.isin(["claimed", "missed"])].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no serial with a claimed or missed price protection between {fmt_window(start, end)}")
    st = sel["price_protection_status"].astype(str)
    sel["credited"] = np.where(st == "claimed", sel["price_protection_credit_eur"].fillna(0.0), 0.0)
    sel["missed"] = np.where(st == "missed", sel["price_protection_claimable_eur"].fillna(0.0), 0.0)
    sel["at_stake"] = sel["credited"] + sel["missed"]
    numerator = float(sel["credited"].sum())
    denominator = float(sel["at_stake"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("no price protection value at stake (credited and missed both zero)")
    n_open = int((status == "open").sum())
    return ok(
        value, numerator, denominator, n,
        note=f"credited {numerator:,.2f} EUR of {denominator:,.2f} EUR at stake on {n} serials; {n_open} open claims outside the denominator; window {fmt_window(start, end)}",
        breakdown=_breakdowns(sel, ("oem",), "credited", "at_stake"),
    )


# ---------------------------------------------------------------------------
# 2 TCO
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_TCO_PER_CLOSED_DEVICE",
    name="Total cost of ownership per closed device",
    page="2 TCO",
    definition=(
        "Mean total cost of ownership (landed cost plus every cost line of the life: staging, shipping, repair, "
        "logistics, wipe and grading, refurbishment, holding, channel fee) over devices whose cycle closed in the "
        "trailing 12 months."
    ),
    formula_text="sum(device_ledger.tco_eur) / count(*), is_closed and closed_date in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="EUR",
    direction="down",
)
def tco_per_closed_device(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Mean TCO of closed devices; breakdown by catalogue family and manufacturer."""

    dl, note = _ledger(con, ("is_closed", "closed_date", "tco_eur"))
    if dl is None:
        return KpiValue.not_measurable(note)
    sel, undated, (start, end) = _closed_in_window(dl, as_of)
    sel = sel[sel["tco_eur"].notna()]
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no device closed between {fmt_window(start, end)} with a TCO ({undated} closed without closed_date)")
    numerator = float(sel["tco_eur"].sum())
    return ok(
        ratio(numerator, n), numerator, float(n), n,
        note=f"{n} closed devices; {undated} closed without closed_date excluded; window {fmt_window(start, end)}",
        breakdown=_breakdowns(sel, ("catalogue_family", "oem"), "tco_eur", None),
    )


@register_gold(
    kpi_id="KPI_TCO_ESTIMATE_SHARE",
    name="Share of TCO that is an estimate",
    page="2 TCO",
    definition=(
        "Every estimated cost line (holding cost: stock days times holding_cost_per_day_eur, owner CFO; the channel fee "
        "until the credit note arrives; the PO price until the unit invoice arrives) over total cost of ownership, on "
        "devices closed in the trailing 12 months. The rest of the TCO is transactions with a source reference."
    ),
    formula_text="sum(device_ledger.tco_eur - device_ledger.tco_transactional_eur) / sum(device_ledger.tco_eur), closed in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="down",
)
def tco_estimate_share(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Estimated cost lines over TCO; breakdown by catalogue family."""

    dl, note = _ledger(con, ("is_closed", "closed_date", "tco_eur", "tco_transactional_eur"))
    if dl is None:
        return KpiValue.not_measurable(note)
    to_numeric(dl, ["tco_transactional_eur"])
    sel, undated, (start, end) = _closed_in_window(dl, as_of)
    sel = sel[sel["tco_eur"].notna() & sel["tco_transactional_eur"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no device closed between {fmt_window(start, end)} with a TCO and a transactional TCO")
    sel["estimate_eur"] = (sel["tco_eur"] - sel["tco_transactional_eur"]).clip(lower=0.0)
    numerator = float(sel["estimate_eur"].sum())
    denominator = float(sel["tco_eur"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("TCO sum is zero")
    return ok(value, numerator, denominator, n, note=f"{n} closed devices; estimates are holding cost and, until the credit note or the unit invoice arrives, the channel fee or the PO price; window {fmt_window(start, end)}", breakdown=_breakdowns(sel, ("catalogue_family",), "estimate_eur", "tco_eur"))


# ---------------------------------------------------------------------------
# 3 Residual estimate
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_RES_ESTIMATE_VS_ANCHOR",
    name="Fleet estimate vs public anchor at lease end",
    page="3 Residual estimate",
    definition=(
        "Sum of the fleet model's residual value estimate at lease end over the sum of the public-anchor curve value "
        "(marketplace asks, an upper bound) for the same serials, rented serials with an anchor only. The fleet model "
        "decides, the anchor only advises; a ratio far from one asks a human to look, it changes nothing by itself."
    ),
    formula_text="sum(device_ledger.estimate_rv_lease_end) / sum(device_ledger.anchor_rv_lease_end), lifecycle_status = rented and anchor not null",
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="one",
)
def res_estimate_vs_anchor(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Estimate over anchor on rented serials; breakdown by catalogue family and manufacturer."""

    dl, note = _ledger(con, ("lifecycle_status", "estimate_rv_lease_end", "anchor_rv_lease_end"))
    if dl is None:
        return KpiValue.not_measurable(note)
    rented = dl[dl["lifecycle_status"].astype(str) == "rented"]
    sel = rented[rented["estimate_rv_lease_end"].notna() & rented["anchor_rv_lease_end"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no rented serial with both an estimate and an anchor ({len(rented)} rented)")
    numerator = float(sel["estimate_rv_lease_end"].sum())
    denominator = float(sel["anchor_rv_lease_end"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("anchor sum is zero")
    return ok(
        value, numerator, denominator, n,
        note=f"{n} of {len(rented)} rented serials carry an anchor; the anchor is a public ask (upper bound), not a realised price",
        breakdown=_breakdowns(sel, ("catalogue_family", "oem"), "estimate_rv_lease_end", "anchor_rv_lease_end"),
    )


# ---------------------------------------------------------------------------
# 4 Resale
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_RSL_REALISED_VS_RECORD",
    name="Realised residual value vs estimate of record",
    page="4 Resale",
    definition=(
        "Gross resale price over the residual value estimate that was on record when the device came back, summed over "
        "sales in the trailing 12 months, as-is sales excluded (they carry no comparable estimate)."
    ),
    formula_text="sum(device_ledger.resale_gross) / sum(device_ledger.estimate_rv_of_record), sale_date in trailing 12 months, resale_channel != as_is",
    source_tables=("silver.device_ledger",),
    unit="ratio",
    direction="one",
)
def rsl_realised_vs_record(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Realised over record; breakdown by channel and manufacturer."""

    dl, note = _ledger(con, ("sale_date", "resale_gross", "estimate_rv_of_record"))
    if dl is None:
        return KpiValue.not_measurable(note)
    start, end = trailing_window(as_of, 12)
    sales = dl[window_mask(dl["sale_date"], start, end) & dl["resale_gross"].notna()]
    if "resale_channel" in sales.columns:
        sales = sales[sales["resale_channel"].astype(str) != "as_is"]
    without = sales["estimate_rv_of_record"].isna() | (sales["estimate_rv_of_record"] <= 0)
    sel = sales[~without].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no non-as-is sale with an estimate of record between {fmt_window(start, end)} ({int(without.sum())} without)")
    numerator = float(sel["resale_gross"].sum())
    denominator = float(sel["estimate_rv_of_record"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("estimate of record sum is zero")
    return ok(
        value, numerator, denominator, n,
        note=f"{n} sales; {int(without.sum())} without an estimate of record excluded; window {fmt_window(start, end)}",
        breakdown=_breakdowns(sel, ("resale_channel", "oem"), "resale_gross", "estimate_rv_of_record"),
    )


@register_gold(
    kpi_id="KPI_RSL_DAYS_RETURN_TO_CASH",
    name="Median days from return to cash",
    page="4 Resale",
    definition="Median of days from the return receipt to the credit note over sales credited in the trailing 12 months.",
    formula_text="median(device_ledger.days_return_to_cash), credited_at in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="days",
    direction="down",
)
def rsl_days_return_to_cash(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Median days; breakdown (medians) by channel."""

    dl, note = _ledger(con, ("credited_at", "days_return_to_cash"))
    if dl is None:
        return KpiValue.not_measurable(note)
    start, end = trailing_window(as_of, 12)
    sel = dl[window_mask(dl["credited_at"], start, end) & dl["days_return_to_cash"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no credited sale with days_return_to_cash between {fmt_window(start, end)}")
    value = float(sel["days_return_to_cash"].median())
    return ok(value, None, None, n, note=f"median over {n} credited sales; window {fmt_window(start, end)}", breakdown=_median_breakdown(sel, "resale_channel", "days_return_to_cash"))


# ---------------------------------------------------------------------------
# 5 Result
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_RSLT_CLOSED_PER_DEVICE",
    name="Lifecycle result per closed device",
    page="5 Result",
    definition=(
        "Mean lifecycle result (rental revenue plus realised residual value plus price protection credits minus landed "
        "cost and every cost line) over devices whose cycle closed in the trailing 12 months. Closed cycles only; the "
        "two open-cycle numbers are never added to it."
    ),
    formula_text="sum(device_ledger.lifecycle_result_eur) / count(*), is_closed and closed_date in trailing 12 months",
    source_tables=("silver.device_ledger",),
    unit="EUR",
    direction="up",
)
def rslt_closed_per_device(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Mean closed result; breakdown by manufacturer, catalogue family and term."""

    dl, note = _ledger(con, ("is_closed", "closed_date", "lifecycle_result_eur"))
    if dl is None:
        return KpiValue.not_measurable(note)
    sel, undated, (start, end) = _closed_in_window(dl, as_of)
    sel = sel[sel["lifecycle_result_eur"].notna()].copy()
    n = int(len(sel))
    if n == 0:
        return KpiValue.not_measurable(f"no device closed between {fmt_window(start, end)} with a result ({undated} closed without closed_date)")
    if "term_months" in sel.columns:
        sel["term_months"] = sel["term_months"].map(lambda v: "(none)" if v is None or v != v else str(int(v)))
    numerator = float(sel["lifecycle_result_eur"].sum())
    n_loss = int((sel["lifecycle_result_eur"] < 0).sum())
    return ok(
        ratio(numerator, n), numerator, float(n), n,
        note=f"{n} closed devices, {n_loss} with a loss; {undated} closed without closed_date excluded; window {fmt_window(start, end)}",
        breakdown=_breakdowns(sel, ("oem", "catalogue_family", "term_months"), "lifecycle_result_eur", None),
    )


# ---------------------------------------------------------------------------
# 6 Levers
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_LEV_ADDITIVE_EUR_PA",
    name="Money on the table per year, additive levers",
    page="6 Levers",
    definition=(
        "Sum of the yearly fleet EUR of the additive levers (purchase discount vs RRP, price protection) in "
        "gold.levers_summary. Non-additive levers are shown per lever and never totalled."
    ),
    formula_text="sum(levers_summary.eur_fleet_per_year where additive)",
    source_tables=("gold.levers_summary",),
    unit="EUR",
    direction="down",
)
def lev_additive_eur_pa(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Yearly EUR of additive levers; breakdown per lever (every lever, additive or not, for reading)."""

    ls, note = load_table(con, "gold.levers_summary", ("lever_id", "additive", "eur_fleet_per_year"))
    if ls is None:
        return KpiValue.not_measurable(note)
    to_numeric(ls, ["eur_fleet_per_year", "n_attributed"])
    additive = ls[ls["additive"].map(lambda v: bool(v) if v is not None and v == v else False)].copy()
    if additive.empty:
        return KpiValue.not_measurable("levers_summary has no additive lever")
    attributed = additive[additive["eur_fleet_per_year"].notna()]
    n = int(len(attributed))
    if n == 0:
        return KpiValue.not_measurable("no additive lever attributed (below lever_reference_min_n or no data)")
    numerator = float(attributed["eur_fleet_per_year"].sum())
    rows = []
    for _, row in ls.sort_values("lever_id").iterrows():
        v = row["eur_fleet_per_year"]
        rows.append(
            {
                "dimension_value": f"{row['lever_id']}{'' if bool(row['additive']) else ' (not additive)'}",
                "value": None if v is None or v != v else float(v),
                "numerator": None if v is None or v != v else float(v),
                "denominator": None,
                "n": int(row["n_attributed"]) if "n_attributed" in ls.columns and row["n_attributed"] == row["n_attributed"] else 0,
            }
        )
    return ok(numerator, numerator, None, n, note=f"{n} additive levers attributed; non-additive levers listed per lever only", breakdown=make_breakdown("lever_id", rows))


# ---------------------------------------------------------------------------
# 7 Contracts
# ---------------------------------------------------------------------------


@register_gold(
    kpi_id="KPI_CTR_COVERAGE_BY_OEM",
    name="Share of hardware spend under a contract in force",
    page="7 Contracts",
    definition=(
        "Received unit value of purchase order lines whose header contract was in force at the order date over all "
        "received unit value, trailing 12 months, attributed to the manufacturer of the device (a reseller purchase "
        "order counts for the manufacturer it carries)."
    ),
    formula_text="sum(contract_coverage_by_oem.spend_under_contract) / sum(contract_coverage_by_oem.spend_total)",
    source_tables=("gold.contract_coverage_by_oem",),
    unit="ratio",
    direction="up",
)
def ctr_coverage_by_oem(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Coverage of hardware spend; breakdown by manufacturer."""

    cov, note = load_table(con, "gold.contract_coverage_by_oem", ("oem", "spend_total", "spend_under_contract"))
    if cov is None:
        return KpiValue.not_measurable(note)
    to_numeric(cov, ["spend_total", "spend_under_contract", "n_units"])
    sel = cov[cov["spend_total"].notna() & (cov["spend_total"] > 0)].copy()
    if sel.empty:
        return KpiValue.not_measurable("no received unit value in the trailing 12 months")
    numerator = float(sel["spend_under_contract"].fillna(0.0).sum())
    denominator = float(sel["spend_total"].sum())
    n = int(sel["n_units"].fillna(0).sum()) if "n_units" in sel.columns else int(len(sel))
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("spend total is zero")
    sel["spend_under_contract"] = sel["spend_under_contract"].fillna(0.0)
    return ok(value, numerator, denominator, n, note=f"{numerator:,.2f} of {denominator:,.2f} EUR received unit value under a contract in force, {len(sel)} manufacturers, {n} units", breakdown=_breakdowns(sel, ("oem",), "spend_under_contract", "spend_total"))


__all__ = ["DEVICE_LEDGER"]
