"""Recommerce KPIs (SPEC 7.2, area "Recommerce").

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``KPI_REC_RV_REALISATION`` "Residual value realisation vs forecast"
- ``KPI_REC_MARGIN_MIX_CHANNEL`` "Margin mix by channel"
- ``KPI_REC_DAYS_TO_SALE`` "Days to sale"
- ``KPI_REC_GRADING_ACCURACY`` "Grading accuracy"

Choices where the spec is silent:
- Margin mix: the KPI keeps the name the KPI tree asks for, but the figure it
  splits is NET RECOVERY (channel contribution before asset cost): gross price
  minus channel fees, refurbishment and return logistics. No book value or
  landed cost is deducted, so every channel including As Is can look positive
  here; that is stated in the definition, the formula and the note. The tile
  value is the share of the largest channel's net recovery in the total. The
  breakdown (dimension ``channel``) carries ``value`` = share, ``numerator`` =
  channel net recovery in EUR, ``denominator`` = total, ``n`` = units sold; the
  per-unit figure is ``numerator / n``. The note also lists the mean lifecycle
  margin per channel (asset cost included, from ``device_pnl.lifecycle_margin``)
  so a reader sees the true margin next to the recovery. A total of zero or
  below makes the share meaningless, so it is not measurable then.
- Grading "optimism" means the declared pre-return grade was better than the
  inspected grade (A better than B better than C better than D).
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

_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


@register(
    kpi_id="KPI_REC_RV_REALISATION",
    name="Residual value realisation vs forecast",
    area="Recommerce",
    definition=(
        "Realised gross resale price over the forecast of record (the marketplace-baseline forecast in force before "
        "the return) for sales in the trailing 3 months with a forecast, As-Is excluded. Below 1 means the forecast "
        "was overstated; the ratio also reflects channel mix against the marketplace baseline."
    ),
    formula_text=(
        "sum(resale.price) / sum(rv_forecast_of_record.forecast_rv) for sales in trailing 3 months with a non-missing "
        "forecast and channel != 'as_is'"
    ),
    source_tables=("resale", "rv_forecast_of_record"),
    measurable_from="a forecast run in force before the return of at least one device sold in the window",
    unit="ratio",
    direction="one",
)
def residual_value_realisation(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Realised over forecast, breakdown by channel."""

    resale, note = load_table(con, "resale", ("serial", "sale_date", "price", "channel"))
    if resale is None:
        return KpiValue.not_measurable(note)
    record, note = load_table(con, "rv_forecast_of_record", ("serial", "forecast_rv", "is_missing"))
    if record is None:
        return KpiValue.not_measurable(note)
    to_datetime(resale, ["sale_date"])
    to_numeric(resale, ["price"])
    to_numeric(record, ["forecast_rv"])
    start, end = trailing_window(as_of, 3)
    sales = resale[window_mask(resale["sale_date"], start, end) & (resale["channel"].astype(str) != "as_is")]
    n_as_is = int((window_mask(resale["sale_date"], start, end) & (resale["channel"].astype(str) == "as_is")).sum())
    if sales.empty:
        return KpiValue.not_measurable(f"no non-As-Is sale between {fmt_window(start, end)}")
    usable = record[~as_bool(record["is_missing"]) & record["forecast_rv"].notna()][["serial", "forecast_rv"]]
    joined = sales.merge(usable, on="serial", how="inner")
    n = int(len(joined))
    n_without = int(len(sales)) - n
    if n == 0:
        return KpiValue.not_measurable(
            f"{len(sales)} sale(s) {fmt_window(start, end)} but none has a forecast of record (no run before the return)"
        )
    numerator = float(joined["price"].sum())
    denominator = float(joined["forecast_rv"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("forecast sum is zero")
    breakdown = ratio_breakdown(joined, "channel", "price", "forecast_rv")
    return ok(
        value,
        numerator,
        denominator,
        n,
        note=(
            f"{n} sales with forecast, {n_without} without, {n_as_is} As-Is excluded; realised {numerator:,.2f} EUR vs "
            f"forecast {denominator:,.2f} EUR; window {fmt_window(start, end)}"
        ),
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_REC_MARGIN_MIX_CHANNEL",
    name="Margin mix by channel",
    area="Recommerce",
    definition=(
        "Net recovery per resale channel, i.e. channel contribution BEFORE asset cost: gross price minus channel "
        "fees, refurbishment and return logistics, over sales in the trailing 12 months. It is not a margin in the "
        "P&L sense: the device's book value or landed cost is not deducted, so every channel including As Is can be "
        "positive here. The tile shows the share of the largest channel in the total net recovery; the breakdown "
        "carries share, EUR and units per channel (per unit = numerator / n). The note adds the mean lifecycle margin "
        "per channel (asset cost included) from device_pnl so the true margin stands next to the recovery."
    ),
    formula_text=(
        "per channel sum(resale_price_gross - resale_fees - refurb_cost - return_logistics_cost) over sales in "
        "trailing 12 months (net recovery before asset cost); value = share of the largest channel in the total; "
        "note: mean(lifecycle_margin) per channel over the same sales"
    ),
    source_tables=("device_pnl",),
    measurable_from="sold devices with resale_channel, fees, refurbishment and return logistics cost per serial",
    unit="ratio",
    direction="up",
)
def margin_mix_by_channel(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Largest channel's share of total net recovery (before asset cost); full mix in the breakdown."""

    pnl, note = load_table(con, "device_pnl", ("resale_channel", "sale_date", "resale_price_gross"))
    if pnl is None:
        return KpiValue.not_measurable(note)
    to_datetime(pnl, ["sale_date"])
    to_numeric(pnl, ["resale_price_gross", "resale_fees", "refurb_cost", "return_logistics_cost", "lifecycle_margin"])
    start, end = trailing_window(as_of, 12)
    sold = pnl[window_mask(pnl["sale_date"], start, end) & pnl["resale_channel"].notna() & pnl["resale_price_gross"].notna()].copy()
    n = int(len(sold))
    if n == 0:
        return KpiValue.not_measurable(f"no sale between {fmt_window(start, end)}")
    for col in ("resale_fees", "refurb_cost", "return_logistics_cost"):
        if col not in sold.columns:
            sold[col] = 0.0
    sold["net_margin"] = (
        sold["resale_price_gross"] - sold["resale_fees"].fillna(0) - sold["refurb_cost"].fillna(0) - sold["return_logistics_cost"].fillna(0)
    )
    total = float(sold["net_margin"].sum())
    if total <= 0:
        return KpiValue.not_measurable(f"total net recovery {total:,.2f} EUR is not positive; share undefined")
    per_channel = sold.groupby(sold["resale_channel"].astype(str), sort=True)["net_margin"].agg(["sum", "count"])
    lifecycle = (
        sold.groupby(sold["resale_channel"].astype(str), sort=True)["lifecycle_margin"].mean()
        if "lifecycle_margin" in sold.columns
        else pd.Series(dtype=float)
    )
    rows = []
    per_unit_notes = []
    margin_notes = []
    for channel, agg in per_channel.iterrows():
        margin = float(agg["sum"])
        units = int(agg["count"])
        rows.append({"dimension_value": channel, "value": margin / total, "numerator": margin, "denominator": total, "n": units})
        per_unit_notes.append(f"{channel} {margin / units:,.2f} EUR/unit ({units})")
        lm = lifecycle.get(channel) if len(lifecycle) else None
        if lm is not None and pd.notna(lm):
            margin_notes.append(f"{channel} {float(lm):,.2f} EUR/unit")
    largest = per_channel["sum"].idxmax()
    numerator = float(per_channel.loc[largest, "sum"])
    note = (
        f"net recovery BEFORE asset cost (not a P&L margin): largest channel {largest}; total {total:,.2f} EUR "
        f"over {n} sales; per unit: " + ", ".join(per_unit_notes)
    )
    if margin_notes:
        note += "; mean lifecycle margin per channel (asset cost included): " + ", ".join(margin_notes)
    return ok(
        numerator / total,
        numerator,
        total,
        n,
        note=note,
        breakdown=make_breakdown("channel", rows),
    )


@register(
    kpi_id="KPI_REC_DAYS_TO_SALE",
    name="Days to sale",
    area="Recommerce",
    definition="Mean days from the sellable date (end of refurbishment) to the sale for sales in the trailing 3 months.",
    formula_text="mean(resale.sale_date - device_pnl.sellable_date) for sales in trailing 3 months",
    source_tables=("resale", "device_pnl"),
    measurable_from="a sellable_date per sold serial (refurbishment end_date)",
    unit="days",
    direction="down",
)
def days_to_sale(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Mean days to sale, breakdown by channel."""

    resale, note = load_table(con, "resale", ("serial", "sale_date"))
    if resale is None:
        return KpiValue.not_measurable(note)
    pnl, note = load_table(con, "device_pnl", ("serial", "sellable_date"))
    if pnl is None:
        return KpiValue.not_measurable(note)
    to_datetime(resale, ["sale_date"])
    to_datetime(pnl, ["sellable_date"])
    start, end = trailing_window(as_of, 3)
    sales = resale[window_mask(resale["sale_date"], start, end)]
    if sales.empty:
        return KpiValue.not_measurable(f"no sale between {fmt_window(start, end)}")
    joined = sales.merge(pnl[["serial", "sellable_date"]], on="serial", how="inner")
    joined = joined[joined["sellable_date"].notna()].copy()
    n = int(len(joined))
    if n == 0:
        return KpiValue.not_measurable(f"{len(sales)} sale(s) but none has a sellable_date in device_pnl")
    joined["days"] = (joined["sale_date"] - joined["sellable_date"]).dt.days.astype(float)
    numerator = float(joined["days"].sum())
    denominator = float(n)
    breakdown = ratio_breakdown(joined, "channel", "days", None) if "channel" in joined.columns else None
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        n,
        note=f"{n} sales {fmt_window(start, end)}; median {joined['days'].median():.1f} days",
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_REC_GRADING_ACCURACY",
    name="Grading accuracy",
    area="Recommerce",
    definition=(
        "Share of returns in the trailing 6 months where the grade declared before return equals the grade found "
        "at inspection. The note carries the optimism share (declared better than inspected); the breakdown is the "
        "confusion matrix with cells like 'A>B' (declared>inspected)."
    ),
    formula_text=(
        "count(grade_pre_return = grade_inspected) / count(returns with both not null) for return_date in trailing 6 months"
    ),
    source_tables=("events",),
    measurable_from="pre-return grade capture exists",
    unit="ratio",
    direction="up",
)
def grading_accuracy(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Exact-match share of declared vs inspected grade; confusion cells in the breakdown."""

    events, note = load_table(con, "events", ("event_type", "return_date", "grade_pre_return", "grade_inspected"))
    if events is None:
        return KpiValue.not_measurable(note)
    to_datetime(events, ["return_date"])
    start, end = trailing_window(as_of, 6)
    returns = events[
        (events["event_type"].astype(str) == "return")
        & window_mask(events["return_date"], start, end)
        & events["grade_pre_return"].notna()
        & events["grade_inspected"].notna()
    ].copy()
    n = int(len(returns))
    if n == 0:
        return KpiValue.not_measurable(f"no return with both grades between {fmt_window(start, end)}")
    pre = returns["grade_pre_return"].astype(str)
    insp = returns["grade_inspected"].astype(str)
    match = (pre == insp)
    optimistic = pre.map(_GRADE_RANK) < insp.map(_GRADE_RANK)
    numerator = float(match.sum())
    denominator = float(n)
    cells = (pre + ">" + insp).value_counts().sort_index()
    rows = [
        {"dimension_value": cell, "value": int(count) / n, "numerator": float(count), "denominator": denominator, "n": int(count)}
        for cell, count in cells.items()
    ]
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        n,
        note=(
            f"{int(numerator)} of {n} returns graded as declared; optimism share (declared better than inspected) "
            f"{float(optimistic.sum()) / n:.4f}; window {fmt_window(start, end)}"
        ),
        breakdown=make_breakdown("confusion", rows),
    )
