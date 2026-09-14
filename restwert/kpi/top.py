"""Top KPIs (SPEC 7.2, area "Top").

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``KPI_TOP_LIFECYCLE_MARGIN`` "Lifecycle margin per device": the one number
  leadership steers by. Read from ``device_pnl`` (module 2 writes it).
- ``KPI_TOP_RV_FORECAST_ERROR`` "Residual value forecast error": the monthly
  MAPE of the forecast of record against realised gross prices, read from
  ``rv_forecast_error_monthly`` (module 3 writes it). Bias, WAPE and n travel
  in the note so a reader sees the direction of the error, not only its size.
  The tile is the BUSINESS view (marketplace-baseline forecast against every
  non-As-Is channel, so channel discounts count as error); the note carries the
  MODEL view next to it (channel-adjusted MAPE and bias) so the two are never
  confused. The definition says so; the README says the same.

Choice where the spec is silent: for the forecast error the ``numerator`` is
``mape * n_with_forecast`` (the sum of absolute percentage errors) and the
``denominator`` is ``n_with_forecast``, so the tile's fraction reproduces the
MAPE and stays consistent with every other KPI's numerator / denominator
convention.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from restwert.config import KpiTargets
from restwert.dates import month_floor
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


@register(
    kpi_id="KPI_TOP_LIFECYCLE_MARGIN",
    name="Lifecycle margin per device",
    area="Top",
    definition=(
        "Average lifecycle margin over every device lifecycle closed (sold or scrapped) in the trailing "
        "12 months. Lifecycle margin = rental revenue - (landed cost - realised residual value) - service "
        "and logistics cost, with channel fees inside the service block and the residual value gross."
    ),
    formula_text="sum(device_pnl.lifecycle_margin) / count(*) where is_closed and closed_date in trailing 12 months",
    source_tables=("device_pnl",),
    measurable_from=(
        "the first closed lifecycle with a complete cost trail; in real data requires repairs booked per serial"
    ),
    unit="EUR",
    direction="up",
    min_n=30,
)
def lifecycle_margin_per_device(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Mean lifecycle margin of lifecycles closed in the trailing 12 months, by family."""

    frame, note = load_table(con, "device_pnl", ("lifecycle_margin", "is_closed", "closed_date"))
    if frame is None:
        return KpiValue.not_measurable(note)
    to_datetime(frame, ["closed_date"])
    to_numeric(frame, ["lifecycle_margin"])
    start, end = trailing_window(as_of, 12)
    mask = as_bool(frame["is_closed"]) & window_mask(frame["closed_date"], start, end) & frame["lifecycle_margin"].notna()
    closed = frame[mask]
    n = int(len(closed))
    if n == 0:
        return KpiValue.not_measurable(f"no closed lifecycle with a margin between {fmt_window(start, end)}")
    numerator = float(closed["lifecycle_margin"].sum())
    denominator = float(n)
    breakdown = ratio_breakdown(closed, "model_family", "lifecycle_margin", None)
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        n,
        note=f"{n} closed lifecycles {fmt_window(start, end)}; sum margin {numerator:,.2f} EUR",
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_TOP_RV_FORECAST_ERROR",
    name="Residual value forecast error",
    area="Top",
    definition=(
        "Mean absolute percentage error of the residual value forecast of record (the run in force before the "
        "device was returned, marketplace baseline, grade at return, expected sale date) against realised gross "
        "resale prices over every non-As-Is channel, all families, for the latest complete month. As-Is sales are "
        "excluded. This is the business view: a buyout or wholesale discount against the marketplace baseline "
        "counts as forecast error, so the number contains channel mix as well as model error. The model view "
        "(the same forecast multiplied by the run's factor for the channel actually used: mape_channel_adjusted, "
        "bias_channel_adjusted) and the marketplace-only error travel in the note; the recalibration advisory "
        "ADV02 tests the model view. On synthetic data this measures recovery of a synthetic curve, not market "
        "accuracy, and the backtest (conditional on realised channel, grade and sale date) reports a smaller "
        "error by design."
    ),
    formula_text=(
        "rv_forecast_error_monthly.mape for the latest complete month (month < month_floor(as_of)) with model_family='*'"
    ),
    source_tables=("rv_forecast_error_monthly",),
    measurable_from=(
        "the first month with a forecast run in force before a return and >= 10 sales with a forecast"
    ),
    unit="ratio",
    direction="down",
    min_n=10,
)
def residual_value_forecast_error(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """MAPE of the latest complete month; bias, WAPE and counts in the note; per family in the breakdown."""

    frame, note = load_table(con, "rv_forecast_error_monthly", ("month", "model_family", "mape", "n_with_forecast"))
    if frame is None:
        return KpiValue.not_measurable(note)
    to_datetime(frame, ["month"])
    to_numeric(
        frame,
        [
            "mape", "bias", "wape", "mae_eur", "realisation_ratio", "n_with_forecast", "n_sales", "n_excluded_as_is",
            "mape_channel_adjusted", "bias_channel_adjusted", "n_marketplace", "mape_marketplace", "bias_marketplace",
        ],
    )
    complete = frame[frame["month"] < pd.Timestamp(month_floor(as_of))]
    overall = complete[complete["model_family"] == "*"].dropna(subset=["month"])
    if overall.empty:
        return KpiValue.not_measurable("no complete month with an all-families error row before as_of")
    latest_month = overall["month"].max()
    row = overall[overall["month"] == latest_month].iloc[0]
    n = int(row["n_with_forecast"]) if pd.notna(row["n_with_forecast"]) else 0
    month_label = pd.Timestamp(latest_month).strftime("%Y-%m")
    if pd.isna(row["mape"]) or n == 0:
        return KpiValue.not_measurable(
            f"month {month_label}: MAPE not computed (n_with_forecast={n}, below the 10-sales floor of the error series)"
        )
    mape = float(row["mape"])
    numerator = mape * n
    denominator = float(n)
    bias = row.get("bias")
    wape = row.get("wape")
    n_sales = row.get("n_sales")
    n_as_is = row.get("n_excluded_as_is")
    parts = [f"month {month_label}", f"MAPE {mape:.4f} (business view: marketplace baseline vs all channels)"]
    if pd.notna(bias):
        parts.append(f"bias {float(bias):+.4f} (positive = forecast too high; includes channel mix)")
    if pd.notna(wape):
        parts.append(f"WAPE {float(wape):.4f}")
    mape_ch = row.get("mape_channel_adjusted")
    bias_ch = row.get("bias_channel_adjusted")
    if pd.notna(mape_ch):
        parts.append(f"model view channel-adjusted MAPE {float(mape_ch):.4f}")
    if pd.notna(bias_ch):
        parts.append(f"channel-adjusted bias {float(bias_ch):+.4f}")
    mape_m = row.get("mape_marketplace")
    bias_m = row.get("bias_marketplace")
    n_m = row.get("n_marketplace")
    if pd.notna(mape_m):
        parts.append(f"marketplace only MAPE {float(mape_m):.4f}")
    if pd.notna(bias_m):
        parts.append(f"marketplace only bias {float(bias_m):+.4f}")
    if pd.notna(n_m):
        parts.append(f"n_marketplace {int(n_m)}")
    parts.append(f"n_with_forecast {n}")
    if pd.notna(n_sales):
        parts.append(f"n_sales {int(n_sales)}")
    if pd.notna(n_as_is):
        parts.append(f"as_is excluded {int(n_as_is)}")
    families = complete[(complete["month"] == latest_month) & (complete["model_family"] != "*")]
    rows = []
    for _, fam in families.iterrows():
        fam_n = int(fam["n_with_forecast"]) if pd.notna(fam["n_with_forecast"]) else 0
        fam_mape = None if pd.isna(fam["mape"]) else float(fam["mape"])
        rows.append(
            {
                "dimension_value": fam["model_family"],
                "value": fam_mape,
                "numerator": None if fam_mape is None else fam_mape * fam_n,
                "denominator": float(fam_n),
                "n": fam_n,
            }
        )
    return ok(mape, numerator, denominator, n, note="; ".join(parts), breakdown=make_breakdown("model_family", rows))
