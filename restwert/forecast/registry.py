"""Forecast run persistence and the two rebuilt forecast tables (SPEC 5.3).

``forecast_runs`` is IMMUTABLE: :func:`save_run` only appends and raises on a duplicate
``run_id``. That is what makes the forecast of record reproducible: the error leadership
saw in March is still the March error after every rerun.

``rv_forecast_grid`` (model x grade x months since launch) and ``rv_forecast_current``
(one row per unsold device) are rebuilt from the latest run by ``run.run_forecast``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from restwert import db
from restwert.dates import add_months
from restwert.forecast.features import (
    as_date,
    months_since,
    n_launches_since,
    n_launches_since_vec,
    to_datetime_series,
)
from restwert.forecast.model import (
    BAND_Z,
    RATIO_MAX,
    RATIO_MIN,
    ResidualValueModel,
    channel_factor,
    fit_quality_for,
    from_run_row,
    predict_ratio_frame,
    to_json,
)

#: Grid horizon in months since launch (0 to 120). Derived from the shipped device ledger, not
#: from a purchase rule: the earlier horizons (72, then 84 "for a device bought 24 months after
#: launch and rented 48 months") assumed a purchase age the generation draw's older pool does
#: not respect. Measured on ``silver.device_ledger`` of ``data/restwert.duckdb`` (2026-09-14,
#: DuckDB read only, catalogue round 4): ``max(estimate_months_at_lease_end) = 110`` (before
#: round 4 it was 94, and 24 open serials at 85 to 94 carried ``estimate_rv_source =
#: 'grid_clipped'`` on the 84-month grid), with ``max(months from launch to purchase) = 63``
#: and the longest term 48. 110 plus 6 months reserve = 116, rounded up to a full year = 120. The device ledger and the TCO read the grid
#: at ``months since launch at purchase + term + return-to-sale days``; a value beyond the
#: horizon is clipped and flagged, never silently extrapolated.
GRID_MONTHS: range = range(0, 121)

FORECAST_RUNS_COLUMNS = [
    "run_id",
    "as_of",
    "method",
    "created_at",
    "n_train_total",
    "n_train_json",
    "fit_quality_json",
    "coefficients_json",
    "sigma_log_json",
    "as_is_ratio_json",
    "feature_names_json",
    "unsupported_json",
]

GRID_COLUMNS = [
    "run_id",
    "model",
    "model_family",
    "grade",
    "months_since_launch",
    "n_launches_since",
    "storage_gb",
    "forecast_rv_ratio",
    "ratio_low",
    "ratio_high",
    "list_price",
    "forecast_rv_on_list",
    "n_train",
    "fit_quality",
]

CURRENT_COLUMNS = [
    "serial",
    "as_of",
    "run_id",
    "model",
    "model_family",
    "grade_used",
    "grade_source",
    "months_since_launch",
    "n_launches_since",
    "forecast_rv_ratio",
    "forecast_rv",
    "forecast_rv_employee_buyout",
    "forecast_rv_b2b_wholesale",
    "forecast_rv_as_is",
    "forecast_rv_grade_b",
    "fit_quality",
    "n_train",
]

GRADES = ("A", "B", "C", "D")


# --------------------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------------------
def make_run_id(as_of: date) -> str:
    return f"rv-{as_date(as_of):%Y-%m-%d}"


def run_exists(con, run_id: str) -> bool:
    if not db.table_exists(con, "forecast_runs"):
        return False
    df = db.read_df(con, "SELECT count(*) AS n FROM forecast_runs WHERE run_id = ?", [run_id])
    return int(df["n"].iloc[0]) > 0


def run_to_frame(rvm: ResidualValueModel) -> pd.DataFrame:
    """One ``forecast_runs`` row for a model."""
    js = to_json(rvm)
    fam_n = {k: v for k, v in rvm.n_train.items() if k != "pooled"}
    row = {
        "run_id": rvm.run_id,
        "as_of": pd.Timestamp(rvm.as_of),
        "method": rvm.method,
        "created_at": pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)),
        "n_train_total": int(sum(fam_n.values())),
        **js,
    }
    return pd.DataFrame([row])[FORECAST_RUNS_COLUMNS]


def save_run(con, rvm: ResidualValueModel) -> str:
    """Append the run to ``forecast_runs``. Raises ``ValueError`` if ``run_id`` exists."""
    if run_exists(con, rvm.run_id):
        raise ValueError(f"forecast run {rvm.run_id} already exists; forecast_runs is append-only")
    db.append_rows(con, "forecast_runs", run_to_frame(rvm))
    return rvm.run_id


def load_run(con, run_id: str) -> ResidualValueModel:
    df = db.read_df(con, "SELECT * FROM forecast_runs WHERE run_id = ?", [run_id])
    if len(df) == 0:
        raise KeyError(f"forecast run {run_id} not found")
    return from_run_row(df.iloc[0])


def load_all_runs(con) -> list[ResidualValueModel]:
    """Every run, ordered by ``as_of`` then ``created_at`` (oldest first)."""
    if not db.table_exists(con, "forecast_runs"):
        return []
    df = db.read_df(con, "SELECT * FROM forecast_runs ORDER BY as_of, created_at, run_id")
    return [from_run_row(r) for _, r in df.iterrows()]


def latest_run(con, as_of: date | None = None) -> ResidualValueModel | None:
    """Latest run with ``forecast_runs.as_of <= as_of`` (or the latest of all)."""
    if not db.table_exists(con, "forecast_runs"):
        return None
    if as_of is None:
        df = db.read_df(con, "SELECT * FROM forecast_runs ORDER BY as_of DESC, created_at DESC LIMIT 1")
    else:
        df = db.read_df(
            con,
            "SELECT * FROM forecast_runs WHERE as_of <= ? ORDER BY as_of DESC, created_at DESC LIMIT 1",
            [as_date(as_of)],
        )
    return from_run_row(df.iloc[0]) if len(df) else None


def run_before(con, d: date) -> ResidualValueModel | None:
    """Latest run with ``forecast_runs.as_of < d`` (STRICT). Forecast-of-record lookup."""
    if not db.table_exists(con, "forecast_runs"):
        return None
    df = db.read_df(
        con,
        "SELECT * FROM forecast_runs WHERE as_of < ? ORDER BY as_of DESC, created_at DESC LIMIT 1",
        [as_date(d)],
    )
    return from_run_row(df.iloc[0]) if len(df) else None


def pick_run_before(runs: list[ResidualValueModel], d: date) -> ResidualValueModel | None:
    """In-memory :func:`run_before` over a list sorted by ``as_of`` ascending."""
    d = as_date(d)
    best = None
    for r in runs:
        if r.as_of < d:
            best = r
        else:
            break
    return best


# --------------------------------------------------------------------------------------
# grid
# --------------------------------------------------------------------------------------
def build_grid(
    rvm: ResidualValueModel,
    catalogue: pd.DataFrame,
    months: range = GRID_MONTHS,
    families_cfg=None,
) -> pd.DataFrame:
    """``rv_forecast_grid`` rows for every catalogue model x grade x months since launch.

    Storage is the model's ``base_storage_gb``; ``n_launches_since`` counts catalogue
    launches up to ``rvm.as_of`` and, beyond that, the calendar rule from ``families_cfg``
    (when ``families_cfg`` is None only observed launches count; ``run_forecast`` always
    passes it). Target month ``m`` maps to ``add_months(launch_date, m)``.
    """
    if catalogue is None or len(catalogue) == 0:
        return pd.DataFrame(columns=GRID_COLUMNS)
    cat = catalogue.copy()
    cat["launch_date"] = to_datetime_series(cat["launch_date"])
    rows = []
    for _, c in cat.iterrows():
        ld = c["launch_date"].date()
        for g in GRADES:
            for m in months:
                rows.append(
                    {
                        "model": c["model"],
                        "model_family": c["model_family"],
                        "grade": g,
                        "months_since_launch": int(m),
                        "target_date": pd.Timestamp(add_months(ld, int(m))),
                        "launch_date": c["launch_date"],
                        "storage_gb": int(c["base_storage_gb"]),
                        "base_storage_gb": int(c["base_storage_gb"]),
                        "list_price": float(c["list_price"]) if "list_price" in c and pd.notna(c["list_price"]) else np.nan,
                    }
                )
    grid = pd.DataFrame(rows)
    grid["n_launches_since"] = n_launches_since_vec(
        catalogue, grid["model_family"], grid["launch_date"], grid["target_date"], rvm.as_of, families_cfg
    )
    ratio = predict_ratio_frame(rvm, grid, channel="marketplace")
    sigma = grid["model_family"].map(lambda f: float(rvm.sigma_log.get(f, 0.0))).to_numpy()
    mu = np.log(ratio)
    grid["forecast_rv_ratio"] = ratio
    grid["ratio_low"] = np.clip(np.exp(mu - BAND_Z * sigma), RATIO_MIN, RATIO_MAX)
    grid["ratio_high"] = np.clip(np.exp(mu + BAND_Z * sigma), RATIO_MIN, RATIO_MAX)
    grid["forecast_rv_on_list"] = (grid["forecast_rv_ratio"] * grid["list_price"]).round(2)
    grid["n_train"] = grid["model_family"].map(lambda f: int(rvm.n_train.get(f, 0)))
    grid["fit_quality"] = [fit_quality_for(rvm, f, g) for f, g in zip(grid["model_family"], grid["grade"])]
    grid["run_id"] = rvm.run_id
    grid = grid[GRID_COLUMNS].sort_values(["model", "grade", "months_since_launch"]).reset_index(drop=True)
    return grid


def grid_lookup(grid: pd.DataFrame, model: str, grade: str, months: int) -> float | None:
    """Convenience: ``forecast_rv_ratio`` for (model, grade, months clipped to the grid range)."""
    sub = grid[(grid["model"] == model) & (grid["grade"] == grade)]
    if len(sub) == 0:
        return None
    m = int(min(max(months, sub["months_since_launch"].min()), sub["months_since_launch"].max()))
    hit = sub[sub["months_since_launch"] == m]
    return float(hit["forecast_rv_ratio"].iloc[0]) if len(hit) else None


# --------------------------------------------------------------------------------------
# current
# --------------------------------------------------------------------------------------
def _latest_return_per_serial(events: pd.DataFrame | None, as_of: date) -> pd.DataFrame:
    """Latest return event (by return_date) per serial with ``return_date <= as_of``."""
    cols = ["serial", "return_date", "grade_pre_return", "grade_inspected", "wipe_certificate"]
    if events is None or len(events) == 0 or "event_type" not in events.columns:
        return pd.DataFrame(columns=cols)
    ret = events[events["event_type"] == "return"].copy()
    if len(ret) == 0:
        return pd.DataFrame(columns=cols)
    ret["return_date"] = to_datetime_series(ret["return_date"])
    ret = ret[ret["return_date"] <= pd.Timestamp(as_date(as_of))]
    ret = ret.sort_values(["serial", "return_date"]).drop_duplicates("serial", keep="last")
    for c in cols:
        if c not in ret.columns:
            ret[c] = None
    return ret[cols].reset_index(drop=True)


def _refurb_grade_out(refurb: pd.DataFrame | None, as_of: date) -> pd.DataFrame:
    cols = ["serial", "grade_out"]
    if refurb is None or len(refurb) == 0:
        return pd.DataFrame(columns=cols)
    rf = refurb.copy()
    rf["end_date"] = to_datetime_series(rf["end_date"])
    rf = rf[rf["end_date"] <= pd.Timestamp(as_date(as_of))]
    rf = rf.sort_values(["serial", "end_date"]).drop_duplicates("serial", keep="last")
    return rf[cols].reset_index(drop=True)


def build_current(
    rvm: ResidualValueModel,
    devices: pd.DataFrame,
    events: pd.DataFrame | None,
    refurb: pd.DataFrame | None,
    resale: pd.DataFrame | None,
    catalogue: pd.DataFrame,
    a,
    families_cfg,
    as_of: date,
) -> pd.DataFrame:
    """``rv_forecast_current``: one row per device without a sale at or before ``as_of``.

    grade_used: refurbishment ``grade_out`` (source 'inspected'), else the return event's
    ``grade_inspected`` ('inspected'), else ``grade_pre_return`` ('pre_return'), else the
    family's ``expected_grade_at_return`` from assumptions ('expected').
    """
    as_of = as_date(as_of)
    if devices is None or len(devices) == 0:
        return pd.DataFrame(columns=CURRENT_COLUMNS)
    dev = devices[["serial", "model_family", "model", "storage_gb", "purchase_price", "launch_date"]].copy()
    dev["launch_date"] = to_datetime_series(dev["launch_date"])
    dev["purchase_price"] = pd.to_numeric(dev["purchase_price"], errors="coerce").astype(float)

    sold = set()
    if resale is not None and len(resale):
        rs = resale.copy()
        rs["sale_date"] = to_datetime_series(rs["sale_date"])
        sold = set(rs.loc[rs["sale_date"] <= pd.Timestamp(as_of), "serial"])
    dev = dev[~dev["serial"].isin(sold)].copy()
    if len(dev) == 0:
        return pd.DataFrame(columns=CURRENT_COLUMNS)

    ret = _latest_return_per_serial(events, as_of)
    rf = _refurb_grade_out(refurb, as_of)
    dev = dev.merge(rf, on="serial", how="left").merge(
        ret[["serial", "grade_pre_return", "grade_inspected"]], on="serial", how="left"
    )
    expected = dev["model_family"].map(lambda f: str(a.get("expected_grade_at_return", f)))
    grade_used = dev["grade_out"].where(dev["grade_out"].notna(), dev["grade_inspected"])
    source = np.where(dev["grade_out"].notna() | dev["grade_inspected"].notna(), "inspected", None)
    grade_used = grade_used.where(grade_used.notna(), dev["grade_pre_return"])
    source = np.where(pd.isna(source) & dev["grade_pre_return"].notna(), "pre_return", source)
    grade_used = grade_used.where(grade_used.notna(), expected)
    source = np.where(pd.isna(source), "expected", source)
    dev["grade_used"] = grade_used.astype(str)
    dev["grade_source"] = source

    base = None
    if catalogue is not None and len(catalogue) and "base_storage_gb" in catalogue.columns:
        base = catalogue.set_index("model")["base_storage_gb"].astype(float)
    dev["base_storage_gb"] = dev["model"].map(base) if base is not None else np.nan
    if families_cfg is not None:
        fam_base = dev["model_family"].map(
            lambda f: float(families_cfg[f].base_storage_gb) if f in families_cfg else np.nan
        )
        dev["base_storage_gb"] = dev["base_storage_gb"].fillna(fam_base)
    dev["base_storage_gb"] = dev["base_storage_gb"].fillna(dev["storage_gb"].astype(float))

    dev["months_since_launch"] = months_since(dev["launch_date"], pd.Series([pd.Timestamp(as_of)] * len(dev)))
    dev["n_launches_since"] = n_launches_since_vec(
        catalogue, dev["model_family"], dev["launch_date"], pd.Series([pd.Timestamp(as_of)] * len(dev)), as_of, families_cfg
    )
    pred_in = dev.rename(columns={"grade_used": "grade"})
    ratio = predict_ratio_frame(rvm, pred_in, channel="marketplace")
    ratio_b = predict_ratio_frame(rvm, pred_in.assign(grade="B"), channel="marketplace")
    dev["forecast_rv_ratio"] = ratio
    dev["forecast_rv"] = (ratio * dev["purchase_price"]).round(2)
    emp = dev["model_family"].map(lambda f: channel_factor(rvm, f, "employee_buyout")).to_numpy()
    b2b = dev["model_family"].map(lambda f: channel_factor(rvm, f, "b2b_wholesale")).to_numpy()
    as_is = dev["model_family"].map(lambda f: float(rvm.as_is_ratio.get(f, RATIO_MIN))).to_numpy()
    dev["forecast_rv_employee_buyout"] = (dev["forecast_rv"] * emp).round(2)
    dev["forecast_rv_b2b_wholesale"] = (dev["forecast_rv"] * b2b).round(2)
    dev["forecast_rv_as_is"] = (as_is * dev["purchase_price"]).round(2)
    dev["forecast_rv_grade_b"] = (ratio_b * dev["purchase_price"]).round(2)
    dev["fit_quality"] = [fit_quality_for(rvm, f, g) for f, g in zip(dev["model_family"], dev["grade_used"])]
    dev["n_train"] = dev["model_family"].map(lambda f: int(rvm.n_train.get(f, 0)))
    dev["as_of"] = pd.Timestamp(as_of)
    dev["run_id"] = rvm.run_id
    return dev[CURRENT_COLUMNS].sort_values("serial").reset_index(drop=True)


def channel_factors(rvm: ResidualValueModel, family: str) -> dict[str, float]:
    """Channel multipliers of a run for one family: marketplace 1.0, buyout and b2b from the fit."""
    return {
        "marketplace": 1.0,
        "employee_buyout": channel_factor(rvm, family, "employee_buyout"),
        "b2b_wholesale": channel_factor(rvm, family, "b2b_wholesale"),
    }


def predict_at(
    rvm: ResidualValueModel,
    *,
    catalogue: pd.DataFrame,
    family: str,
    launch_date: date,
    target_date: date,
    grade: str,
    storage_gb: int,
    base_storage_gb: int,
    families_cfg,
    channel: str = "marketplace",
    n_launches_override: int | None = None,
) -> tuple[float, float, int]:
    """(ratio, months_since_launch, n_launches_since) for one device at a target date,
    knowable at ``rvm.as_of``. Used by the forecast of record and the advisories."""
    from restwert.forecast.model import predict_ratio  # local import keeps module graph flat

    launch_date = as_date(launch_date)
    target_date = as_date(target_date)
    m = (target_date - launch_date).days / 30.4375
    n = (
        n_launches_override
        if n_launches_override is not None
        else n_launches_since(catalogue, family, launch_date, target_date, rvm.as_of, families_cfg)
    )
    r = predict_ratio(
        rvm,
        family=family,
        months_since_launch=m,
        n_launches_since=n,
        grade=grade,
        storage_gb=storage_gb,
        base_storage_gb=base_storage_gb,
        channel=channel,
    )
    return r, m, int(n)


def runs_summary(con) -> pd.DataFrame:
    """Small helper for dashboards: run_id, as_of, n_train_total, fit quality per family."""
    if not db.table_exists(con, "forecast_runs"):
        return pd.DataFrame(columns=["run_id", "as_of", "n_train_total", "fit_quality"])
    df = db.read_df(con, "SELECT run_id, as_of, n_train_total, fit_quality_json FROM forecast_runs ORDER BY as_of")
    df["fit_quality"] = df["fit_quality_json"].map(lambda s: json.dumps(json.loads(s), sort_keys=True))
    return df.drop(columns=["fit_quality_json"])
