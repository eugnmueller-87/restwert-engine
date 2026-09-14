"""Inventory KPIs (SPEC 7.2, area "Inventory").

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``KPI_INV_WEEKS_OF_COVER`` "Weeks of cover"
- ``KPI_INV_AGING_90`` "Aging >90 days"
- ``KPI_INV_AGING_180`` "Aging >180 days"
- ``KPI_INV_WIP_DAYS`` "WIP days return-to-sellable"
- ``KPI_INV_WRITE_DOWN_PCT`` "Write-down in % of book value"

Choices where the spec is silent:
- "trailing 13 weeks" is the 91 days ending at as_of, inclusive.
- WIP days pair each refurbishment with the latest return event of the same
  serial whose return_date is on or before the refurbishment end_date.
- The write-down KPI is measurable only after a decision run has evaluated
  aging (a decision_log row with rule_id R03 at or before as_of). With such a
  run and an empty ledger the honest value is 0: nothing was written down.
  Without any run there is no evidence either way, so it is not measurable.
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.config import KpiTargets
from restwert.kpi.registry import (
    KpiValue,
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

_IN_STOCK = "in_stock"


def _in_stock(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["lifecycle_status"].astype(str) == _IN_STOCK].copy()


@register(
    kpi_id="KPI_INV_WEEKS_OF_COVER",
    name="Weeks of cover",
    area="Inventory",
    definition=(
        "Sellable stock (devices in_stock at as_of) against the weekly sales run-rate of the trailing 13 weeks. "
        "Measures cover against resale, not against new rentals."
    ),
    formula_text=(
        "count(device_pnl where lifecycle_status='in_stock') / (count(resale where sale_date in trailing 13 weeks) / 13)"
    ),
    source_tables=("device_pnl", "resale"),
    measurable_from="a device_pnl run and at least one sale in the trailing 13 weeks",
    unit="weeks",
    direction="down",
)
def weeks_of_cover(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Stock units over weekly sales run-rate, breakdown by model_family."""

    pnl, note = load_table(con, "device_pnl", ("lifecycle_status",))
    if pnl is None:
        return KpiValue.not_measurable(note)
    resale, note = load_table(con, "resale", ("sale_date",))
    if resale is None:
        return KpiValue.not_measurable(note)
    to_datetime(resale, ["sale_date"])
    start = as_of - timedelta(days=90)
    sales = resale[window_mask(resale["sale_date"], start, as_of)].copy()
    stock = _in_stock(pnl)
    n_stock = int(len(stock))
    n_sales = int(len(sales))
    if n_sales == 0:
        return KpiValue.not_measurable(f"no sale between {fmt_window(start, as_of)}: run-rate is zero")
    run_rate = n_sales / 13.0
    value = n_stock / run_rate
    rows = []
    if "model_family" in stock.columns and "serial" in sales.columns and "model_family" in pnl.columns:
        fam_of = pnl.set_index("serial")["model_family"] if "serial" in pnl.columns else None
        sales_fam = sales["serial"].map(fam_of) if fam_of is not None else pd.Series(index=sales.index, dtype=object)
        families = sorted(set(stock["model_family"].dropna().astype(str)) | set(sales_fam.dropna().astype(str)))
        for fam in families:
            s_n = int((stock["model_family"].astype(str) == fam).sum())
            sl_n = int((sales_fam.astype(str) == fam).sum())
            rows.append(
                {
                    "dimension_value": fam,
                    "value": (s_n / (sl_n / 13.0)) if sl_n else None,
                    "numerator": float(s_n),
                    "denominator": sl_n / 13.0,
                    "n": s_n,
                }
            )
    return ok(
        value,
        float(n_stock),
        run_rate,
        n_stock,
        note=f"{n_stock} devices in stock; {n_sales} sales in the 13 weeks {fmt_window(start, as_of)} = {run_rate:.2f} per week",
        breakdown=make_breakdown("model_family", rows),
    )


def _aging(con: duckdb.DuckDBPyConnection, as_of: date, days: int) -> KpiValue:
    pnl, note = load_table(con, "device_pnl", ("lifecycle_status", "days_in_stock"))
    if pnl is None:
        return KpiValue.not_measurable(note)
    to_numeric(pnl, ["days_in_stock", "book_value"])
    stock = _in_stock(pnl)
    stock = stock[stock["days_in_stock"].notna()]
    n = int(len(stock))
    if n == 0:
        return KpiValue.not_measurable("no device in_stock with days_in_stock at as_of")
    aged = stock[stock["days_in_stock"] > days]
    numerator = float(len(aged))
    denominator = float(n)
    book_aged = float(aged["book_value"].fillna(0).sum()) if "book_value" in aged.columns else float("nan")
    book_total = float(stock["book_value"].fillna(0).sum()) if "book_value" in stock.columns else float("nan")
    stock = stock.assign(aged=(stock["days_in_stock"] > days).astype(float))
    breakdown = ratio_breakdown(stock, "model_family", "aged", None)
    note = f"{int(numerator)} of {n} in-stock devices older than {days} days"
    if np.isfinite(book_aged):
        note += f"; book value of aged stock {book_aged:,.2f} EUR of {book_total:,.2f} EUR in stock"
    return ok(ratio(numerator, denominator), numerator, denominator, n, note=note, breakdown=breakdown)


@register(
    kpi_id="KPI_INV_AGING_90",
    name="Aging >90 days",
    area="Inventory",
    definition=(
        "Share of sellable stock that has been in stock for more than 90 days since its sellable date; the EUR book "
        "value of the aged units is reported in the note. Strictly greater than, matching decision rule R03."
    ),
    formula_text="count(in_stock with days_in_stock > 90) / count(in_stock); numerator also reported as EUR book value in note",
    source_tables=("device_pnl",),
    measurable_from="a device_pnl run with sellable dates per serial",
    unit="ratio",
    direction="down",
)
def aging_90(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Share of stock older than 90 days."""

    return _aging(con, as_of, 90)


@register(
    kpi_id="KPI_INV_AGING_180",
    name="Aging >180 days",
    area="Inventory",
    definition=(
        "Share of sellable stock that has been in stock for more than 180 days since its sellable date; the EUR "
        "book value of the aged units is reported in the note."
    ),
    formula_text="count(in_stock with days_in_stock > 180) / count(in_stock); numerator also reported as EUR book value in note",
    source_tables=("device_pnl",),
    measurable_from="a device_pnl run with sellable dates per serial",
    unit="ratio",
    direction="down",
)
def aging_180(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Share of stock older than 180 days."""

    return _aging(con, as_of, 180)


@register(
    kpi_id="KPI_INV_WIP_DAYS",
    name="WIP days return-to-sellable",
    area="Inventory",
    definition=(
        "Mean days from the customer's return to the end of refurbishment (the sellable date) for refurbishments "
        "finished in the trailing 6 months. Median and p90 travel in the note."
    ),
    formula_text=(
        "mean(refurbishment.end_date - return.return_date) for refurbishments with end_date in trailing 6 months "
        "(median and p90 in note)"
    ),
    source_tables=("events", "refurbishment"),
    measurable_from="return events with return_date and refurbishment rows with end_date per serial",
    unit="days",
    direction="down",
)
def wip_days_return_to_sellable(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Mean return-to-sellable days, breakdown by model_family when devices are available."""

    events, note = load_table(con, "events", ("serial", "event_type", "return_date"))
    if events is None:
        return KpiValue.not_measurable(note)
    refurb, note = load_table(con, "refurbishment", ("serial", "end_date"))
    if refurb is None:
        return KpiValue.not_measurable(note)
    to_datetime(events, ["return_date"])
    to_datetime(refurb, ["end_date", "start_date"])
    start, end = trailing_window(as_of, 6)
    returns = events[(events["event_type"].astype(str) == "return") & events["return_date"].notna()][["serial", "return_date"]]
    done = refurb[window_mask(refurb["end_date"], start, end)][["serial", "end_date"]]
    if done.empty or returns.empty:
        return KpiValue.not_measurable(f"no refurbishment finished between {fmt_window(start, end)} with a matching return")
    pairs = done.merge(returns, on="serial", how="inner")
    pairs = pairs[pairs["return_date"] <= pairs["end_date"]]
    if pairs.empty:
        return KpiValue.not_measurable("no refurbishment has a return event on or before its end_date")
    latest = pairs.sort_values("return_date").groupby(["serial", "end_date"], as_index=False).last()
    latest["wip_days"] = (latest["end_date"] - latest["return_date"]).dt.days.astype(float)
    n = int(len(latest))
    numerator = float(latest["wip_days"].sum())
    denominator = float(n)
    median = float(latest["wip_days"].median())
    p90 = float(latest["wip_days"].quantile(0.9))
    breakdown = None
    if db.table_exists(con, "devices"):
        devices = db.read_df(con, "SELECT serial, model_family FROM devices")
        if not devices.empty:
            latest = latest.merge(devices, on="serial", how="left")
            breakdown = ratio_breakdown(latest, "model_family", "wip_days", None)
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        n,
        note=f"{n} refurbishments finished {fmt_window(start, end)}; median {median:.1f} days, p90 {p90:.1f} days",
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_INV_WRITE_DOWN_PCT",
    name="Write-down in % of book value",
    area="Inventory",
    definition=(
        "Write-downs booked by the aging rule (R03) in the latest ledger run at or before as_of, relative to the "
        "book value of sellable stock. Book value is a management view (assumptions.yaml, owner CFO), not IFRS 16 / HGB."
    ),
    formula_text=(
        "sum(write_down_ledger.amount where as_of = latest ledger as_of <= as_of) / "
        "sum(device_pnl.book_value where lifecycle_status='in_stock')"
    ),
    source_tables=("write_down_ledger", "device_pnl"),
    measurable_from=(
        "a decision run has booked the ledger; policy is a management view (assumptions.yaml owner CFO)"
    ),
    unit="ratio",
    direction="down",
)
def write_down_pct_of_book_value(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Latest ledger run's write-downs over in-stock book value."""

    pnl, note = load_table(con, "device_pnl", ("lifecycle_status", "book_value"))
    if pnl is None:
        return KpiValue.not_measurable(note)
    to_numeric(pnl, ["book_value"])
    stock = _in_stock(pnl)
    denominator = float(stock["book_value"].fillna(0).sum())
    if len(stock) == 0 or denominator == 0:
        return KpiValue.not_measurable("no in-stock book value at as_of")
    if not db.table_exists(con, "write_down_ledger"):
        return KpiValue.not_measurable("table write_down_ledger does not exist")
    if not db.table_exists(con, "decision_log"):
        return KpiValue.not_measurable("no decision run yet (decision_log missing): aging rule R03 has not been evaluated")
    log = db.read_df(con, "SELECT count(*) AS n FROM decision_log WHERE rule_id = 'R03' AND as_of <= ?", [as_of])
    if int(log["n"].iloc[0]) == 0:
        return KpiValue.not_measurable("no decision run has evaluated aging rule R03 at or before as_of")
    ledger = db.read_df(con, "SELECT serial, as_of, amount FROM write_down_ledger WHERE as_of <= ?", [as_of])
    to_datetime(ledger, ["as_of"])
    to_numeric(ledger, ["amount"])
    if ledger.empty:
        return ok(
            0.0,
            0.0,
            denominator,
            int(len(stock)),
            note=f"R03 evaluated, no write-down booked at or before {as_of.isoformat()}; in-stock book value {denominator:,.2f} EUR",
        )
    latest_as_of = ledger["as_of"].max()
    current = ledger[ledger["as_of"] == latest_as_of]
    numerator = float(current["amount"].fillna(0).sum())
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        int(len(stock)),
        note=(
            f"ledger run {pd.Timestamp(latest_as_of).date().isoformat()}: {len(current)} write-down(s) totalling "
            f"{numerator:,.2f} EUR against {denominator:,.2f} EUR in-stock book value"
        ),
    )
