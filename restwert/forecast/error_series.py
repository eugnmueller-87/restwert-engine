"""Forecast of record and the monthly "Residual value forecast error" (SPEC 5.5).

Forecast of record
------------------
For every serial with a return event, the forecast that was in force when the device
came back: the run with ``as_of`` STRICTLY before ``return_date`` (a return on the 1st
uses the previous month end, never a same-day run). Grade is the inspected grade when
known, the target date is ``return_date + expected_return_to_sale_days`` and the channel
is the marketplace baseline. The row also stores the run's channel factors
(``channel_factor_employee_buyout``, ``channel_factor_b2b_wholesale``) so the same
forecast can later be read at the channel that was actually used. Runs are immutable, so
this table is rebuilt identically on every rerun.

Error series
------------
Realised gross resale price against that forecast, per month of sale and family (plus
``'*'`` for all families). As-Is sales are excluded and counted. Months with fewer than
``MIN_ROWS_FOR_METRICS`` (10) forecasted sales keep their counts and carry NULL metrics.

Two views of the same month, because the marketplace baseline is not the channel mix:

* ``mape`` / ``bias``: the BUSINESS view. Realised price over every non-As-Is channel
  against the marketplace-baseline forecast. A buyout or wholesale discount shows up here
  as "forecast too high". This is the number leadership sees; its definition says so.
* ``mape_channel_adjusted`` / ``bias_channel_adjusted``: the MODEL view. The same forecast
  multiplied by the run's factor for the channel actually used. This is what ADV02
  (``forecast_calibration``) tests, because a channel discount is not a model error.
* ``n_marketplace`` / ``mape_marketplace`` / ``bias_marketplace``: marketplace sales only,
  where both views coincide.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert import db
from restwert.forecast.backtest import bias as bias_fn
from restwert.forecast.backtest import mae as mae_fn
from restwert.forecast.backtest import mape as mape_fn
from restwert.forecast.backtest import wape as wape_fn
from restwert.forecast.features import as_date, n_launches_since, to_datetime_series
from restwert.forecast.model import ResidualValueModel, predict_ratio
from restwert.forecast.registry import channel_factors, load_all_runs, pick_run_before

MIN_ROWS_FOR_METRICS = 10

RECORD_COLUMNS = [
    "serial",
    "return_date",
    "run_id",
    "run_as_of",
    "grade_used",
    "target_date",
    "months_since_launch",
    "n_launches_since",
    "forecast_rv_ratio",
    "forecast_rv",
    "channel_factor_employee_buyout",
    "channel_factor_b2b_wholesale",
    "is_missing",
    "missing_reason",
]

ERROR_COLUMNS = [
    "month",
    "model_family",
    "n_sales",
    "n_with_forecast",
    "n_excluded_as_is",
    "mape",
    "bias",
    "wape",
    "mae_eur",
    "realisation_ratio",
    "sum_realised",
    "sum_forecast",
    "mape_channel_adjusted",
    "bias_channel_adjusted",
    "sum_forecast_channel_adjusted",
    "n_marketplace",
    "mape_marketplace",
    "bias_marketplace",
    "run_ids_used",
]


def _returns(events: pd.DataFrame | None) -> pd.DataFrame:
    """Latest return event per serial (by return_date)."""
    cols = ["serial", "return_date", "grade_pre_return", "grade_inspected"]
    if events is None or len(events) == 0 or "event_type" not in events.columns:
        return pd.DataFrame(columns=cols)
    ret = events[events["event_type"] == "return"].copy()
    for c in cols:
        if c not in ret.columns:
            ret[c] = None
    ret["return_date"] = to_datetime_series(ret["return_date"])
    ret = ret.dropna(subset=["return_date"])
    ret = ret.sort_values(["serial", "return_date"]).drop_duplicates("serial", keep="last")
    return ret[cols].reset_index(drop=True)


def forecast_of_record_frame(
    events: pd.DataFrame | None,
    devices: pd.DataFrame,
    catalogue: pd.DataFrame,
    runs: list[ResidualValueModel],
    a,
    families_cfg,
) -> pd.DataFrame:
    """Pure version of :func:`build_forecast_of_record` over frames and a run list."""
    ret = _returns(events)
    if len(ret) == 0:
        return pd.DataFrame(columns=RECORD_COLUMNS)
    runs = sorted(runs, key=lambda r: (r.as_of, r.run_id))
    dev = devices[["serial", "model_family", "model", "storage_gb", "purchase_price", "launch_date"]].copy()
    dev["launch_date"] = to_datetime_series(dev["launch_date"])
    dev["purchase_price"] = pd.to_numeric(dev["purchase_price"], errors="coerce").astype(float)
    base = None
    if catalogue is not None and len(catalogue) and "base_storage_gb" in catalogue.columns:
        base = catalogue.set_index("model")["base_storage_gb"].astype(float)
    df = ret.merge(dev, on="serial", how="inner")
    rows = []
    for r in df.itertuples(index=False):
        return_date = r.return_date.date()
        rvm = pick_run_before(runs, return_date)
        row = {
            "serial": r.serial,
            "return_date": pd.Timestamp(return_date),
            "run_id": None,
            "run_as_of": pd.NaT,
            "grade_used": None,
            "target_date": pd.NaT,
            "months_since_launch": np.nan,
            "n_launches_since": None,
            "forecast_rv_ratio": np.nan,
            "forecast_rv": np.nan,
            "channel_factor_employee_buyout": np.nan,
            "channel_factor_b2b_wholesale": np.nan,
            "is_missing": True,
            "missing_reason": None,
        }
        if rvm is None:
            row["missing_reason"] = "no run before return_date"
            rows.append(row)
            continue
        if pd.isna(r.launch_date) or not np.isfinite(r.purchase_price) or r.purchase_price <= 0:
            row["missing_reason"] = "device without launch_date or purchase_price"
            rows.append(row)
            continue
        fam = str(r.model_family)
        grade = r.grade_inspected if pd.notna(r.grade_inspected) else None
        if grade is None:
            grade = r.grade_pre_return if pd.notna(r.grade_pre_return) else None
        if grade is None:
            grade = str(a.get("expected_grade_at_return", fam))
        days = int(a.get("expected_return_to_sale_days", fam))
        target = return_date + timedelta(days=days)
        launch = r.launch_date.date()
        m = (target - launch).days / 30.4375
        n = n_launches_since(catalogue, fam, launch, target, rvm.as_of, families_cfg)
        base_gb = float(base.get(r.model, np.nan)) if base is not None else np.nan
        if not np.isfinite(base_gb):
            base_gb = float(families_cfg[fam].base_storage_gb) if families_cfg and fam in families_cfg else float(r.storage_gb)
        ratio = predict_ratio(
            rvm,
            family=fam,
            months_since_launch=m,
            n_launches_since=n,
            grade=str(grade),
            storage_gb=int(r.storage_gb),
            base_storage_gb=int(base_gb),
            channel="marketplace",
        )
        factors = channel_factors(rvm, fam)
        row.update(
            {
                "run_id": rvm.run_id,
                "run_as_of": pd.Timestamp(rvm.as_of),
                "grade_used": str(grade),
                "target_date": pd.Timestamp(target),
                "months_since_launch": float(m),
                "n_launches_since": int(n),
                "forecast_rv_ratio": float(ratio),
                "forecast_rv": round(float(ratio) * float(r.purchase_price), 2),
                "channel_factor_employee_buyout": float(factors["employee_buyout"]),
                "channel_factor_b2b_wholesale": float(factors["b2b_wholesale"]),
                "is_missing": False,
                "missing_reason": None,
            }
        )
        rows.append(row)
    out = pd.DataFrame(rows)[RECORD_COLUMNS]
    out["run_as_of"] = pd.to_datetime(out["run_as_of"])
    out["target_date"] = pd.to_datetime(out["target_date"])
    out["n_launches_since"] = out["n_launches_since"].astype("Int64")
    return out.sort_values("serial").reset_index(drop=True)


def build_forecast_of_record(con, a, families_cfg) -> pd.DataFrame:
    """``rv_forecast_of_record`` from the DB: return events, devices, catalogue and all runs."""
    events = db.read_df(con, "SELECT * FROM events WHERE event_type = 'return'") if db.table_exists(con, "events") else None
    devices = db.read_df(con, "SELECT * FROM devices")
    catalogue = db.read_df(con, "SELECT * FROM model_catalogue") if db.table_exists(con, "model_catalogue") else pd.DataFrame()
    runs = load_all_runs(con)
    return forecast_of_record_frame(events, devices, catalogue, runs, a, families_cfg)


def _month_rows(month: pd.Timestamp, fam: str, grp: pd.DataFrame, min_rows: int) -> dict:
    as_is = grp["channel"] == "as_is"
    sales = grp[~as_is]
    with_fc = sales[sales["has_forecast"]]
    n_with = int(len(with_fc))
    mkt = with_fc[with_fc["channel"] == "marketplace"]
    n_mkt = int(len(mkt))
    row = {
        "month": month,
        "model_family": fam,
        "n_sales": int(len(sales)),
        "n_with_forecast": n_with,
        "n_excluded_as_is": int(as_is.sum()),
        "mape": None,
        "bias": None,
        "wape": None,
        "mae_eur": None,
        "realisation_ratio": None,
        "sum_realised": round(float(with_fc["price"].sum()), 2) if n_with else None,
        "sum_forecast": round(float(with_fc["forecast_rv"].sum()), 2) if n_with else None,
        "mape_channel_adjusted": None,
        "bias_channel_adjusted": None,
        "sum_forecast_channel_adjusted": round(float(with_fc["forecast_rv_at_channel"].sum()), 2) if n_with else None,
        "n_marketplace": n_mkt,
        "mape_marketplace": None,
        "bias_marketplace": None,
        "run_ids_used": None,
    }
    if n_with:
        ids = sorted(with_fc["run_id"].astype(str).unique())
        row["run_ids_used"] = f"{ids[0]}..{ids[-1]}"
    if n_with >= min_rows:
        a = with_fc["price"].to_numpy(dtype=float)
        p = with_fc["forecast_rv"].to_numpy(dtype=float)
        p_ch = with_fc["forecast_rv_at_channel"].to_numpy(dtype=float)
        sf = float(p.sum())
        row.update(
            {
                "mape": mape_fn(a, p),
                "bias": bias_fn(a, p),
                "wape": wape_fn(a, p),
                "mae_eur": mae_fn(a, p),
                "realisation_ratio": float(a.sum() / sf - 1.0) if sf else None,
                "mape_channel_adjusted": mape_fn(a, p_ch),
                "bias_channel_adjusted": bias_fn(a, p_ch),
            }
        )
    if n_mkt >= min_rows:
        a_m = mkt["price"].to_numpy(dtype=float)
        p_m = mkt["forecast_rv"].to_numpy(dtype=float)
        row.update({"mape_marketplace": mape_fn(a_m, p_m), "bias_marketplace": bias_fn(a_m, p_m)})
    return row


def build_error_series(
    resale: pd.DataFrame,
    devices: pd.DataFrame,
    record: pd.DataFrame,
    min_rows: int = MIN_ROWS_FOR_METRICS,
) -> pd.DataFrame:
    """``rv_forecast_error_monthly`` from sales joined to the forecast of record on serial.

    Months with fewer than ``min_rows`` forecasted sales keep their counts and carry NULL
    metrics (production default 10; tests lower it to check the arithmetic by hand).
    Deterministic: same inputs give byte-identical rows (no timestamps, fixed sort).

    ``forecast_rv_at_channel = forecast_rv x channel factor of the channel actually used``
    (marketplace 1.0; a record without factor columns, or an As-Is sale, keeps 1.0), which
    feeds the channel-adjusted columns.
    """
    if resale is None or len(resale) == 0:
        return pd.DataFrame(columns=ERROR_COLUMNS)
    rs = resale[["serial", "channel", "sale_date", "price"]].copy()
    rs["sale_date"] = to_datetime_series(rs["sale_date"])
    rs["price"] = pd.to_numeric(rs["price"], errors="coerce").astype(float)
    rs = rs.merge(devices[["serial", "model_family"]], on="serial", how="inner")
    rec_cols = ["serial", "run_id", "forecast_rv", "is_missing"]
    factor_cols = ["channel_factor_employee_buyout", "channel_factor_b2b_wholesale"]
    if record is not None and len(record):
        rec = record[[c for c in rec_cols + factor_cols if c in record.columns]].copy()
    else:
        rec = pd.DataFrame(columns=rec_cols)
    for c in factor_cols:
        if c not in rec.columns:
            rec[c] = np.nan
    rs = rs.merge(rec, on="serial", how="left")
    rs["forecast_rv"] = pd.to_numeric(rs["forecast_rv"], errors="coerce").astype(float)
    rs["has_forecast"] = (
        rs["is_missing"].fillna(True).astype(bool).eq(False) & rs["forecast_rv"].notna() & (rs["forecast_rv"] > 0)
    )
    f_emp = pd.to_numeric(rs["channel_factor_employee_buyout"], errors="coerce").astype(float).fillna(1.0)
    f_b2b = pd.to_numeric(rs["channel_factor_b2b_wholesale"], errors="coerce").astype(float).fillna(1.0)
    ch = rs["channel"].astype(str)
    factor = np.where(ch == "employee_buyout", f_emp, np.where(ch == "b2b_wholesale", f_b2b, 1.0))
    rs["forecast_rv_at_channel"] = (rs["forecast_rv"] * factor).round(2)
    rs["month"] = rs["sale_date"].dt.to_period("M").dt.to_timestamp()
    rows = []
    for month, fam_grp in rs.groupby("month", sort=True):
        for fam, grp in fam_grp.groupby("model_family", sort=True):
            rows.append(_month_rows(month, str(fam), grp, min_rows))
        rows.append(_month_rows(month, "*", fam_grp, min_rows))
    out = pd.DataFrame(rows, columns=ERROR_COLUMNS)
    for c in (
        "mape", "bias", "wape", "mae_eur", "realisation_ratio", "sum_realised", "sum_forecast",
        "mape_channel_adjusted", "bias_channel_adjusted", "sum_forecast_channel_adjusted",
        "mape_marketplace", "bias_marketplace",
    ):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    out["n_marketplace"] = out["n_marketplace"].astype(int)
    return out.sort_values(["month", "model_family"]).reset_index(drop=True)


def latest_complete_month_error(error_series: pd.DataFrame, as_of: date) -> pd.Series | None:
    """The ``'*'`` row of the latest complete month (``month < month_floor(as_of)``) with metrics."""
    if error_series is None or len(error_series) == 0:
        return None
    df = error_series[error_series["model_family"] == "*"].copy()
    df["month"] = to_datetime_series(df["month"])
    floor = pd.Timestamp(as_date(as_of)).to_period("M").to_timestamp()
    df = df[(df["month"] < floor) & df["mape"].notna()].sort_values("month")
    return df.iloc[-1] if len(df) else None
