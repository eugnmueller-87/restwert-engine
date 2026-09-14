"""Metrics and the time-split backtest with leakage guard (SPEC 5.4).

One split: train = sales with ``sale_date <= cutoff``, test = sales after the cutoff.
Test features are built with ``as_of = cutoff``, so launches after the cutoff enter only
through the calendar rule, never through the catalogue. :func:`assert_no_leakage` is
called on every split and raises with the offending values; it also recomputes the test
launch steps from a catalogue truncated at the cutoff and asserts equality.

What the backtest measures (and what it does not)
-------------------------------------------------
The backtest is a CONDITIONAL test of the pricing model: every test row carries the
realised channel, the grade at sale and the actual sale date, i.e. the features a pricing
decision would know at the moment of sale. The monthly error series
(``rv_forecast_error_monthly``) is the OPERATIONAL forecast made at return time: grade at
return, expected sale date, marketplace baseline. The backtest therefore reports a
smaller error than the error series; the two are not meant to agree, and the README and
the dashboard say which is which.

Sign convention: ``bias`` positive means the forecast is too high, i.e. the collateral
value of the fleet is overstated. That is the direction leadership must not miss.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from restwert.forecast.features import (
    TRAINING_COLUMNS,
    as_date,
    as_is_frame,
    build_training_frame,
    months_since,
    n_launches_since_vec,
    to_datetime_series,
    _base_storage_lookup,
    _device_columns,
)
from restwert.forecast.model import ResidualValueModel, fit, predict_ratio_frame

MIN_TEST_ROWS_PER_FAMILY = 1


# --------------------------------------------------------------------------------------
# metrics (EUR scale; a = actual, p = prediction)
# --------------------------------------------------------------------------------------
def _pair(actual, pred) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(pred, dtype=float)
    ok = np.isfinite(a) & np.isfinite(p) & (a != 0)
    return a[ok], p[ok]


def mape(actual: np.ndarray, pred: np.ndarray) -> float:
    """mean(|pred - actual| / actual)."""
    a, p = _pair(actual, pred)
    return float(np.mean(np.abs(p - a) / np.abs(a))) if len(a) else float("nan")


def bias(actual: np.ndarray, pred: np.ndarray) -> float:
    """mean((pred - actual) / actual); positive = forecast too high = collateral overstated."""
    a, p = _pair(actual, pred)
    return float(np.mean((p - a) / a)) if len(a) else float("nan")


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    """sum|pred - actual| / sum(actual)."""
    a, p = _pair(actual, pred)
    s = float(np.sum(np.abs(a)))
    return float(np.sum(np.abs(p - a)) / s) if len(a) and s else float("nan")


def mae(actual: np.ndarray, pred: np.ndarray) -> float:
    a, p = _pair(actual, pred)
    return float(np.mean(np.abs(p - a))) if len(a) else float("nan")


def rmse_log(actual: np.ndarray, pred: np.ndarray) -> float:
    a, p = _pair(actual, pred)
    ok = (a > 0) & (p > 0)
    if not ok.any():
        return float("nan")
    d = np.log(p[ok]) - np.log(a[ok])
    return float(np.sqrt(np.mean(d * d)))


# --------------------------------------------------------------------------------------
# leakage guard
# --------------------------------------------------------------------------------------
def assert_no_leakage(
    train: pd.DataFrame,
    test: pd.DataFrame,
    cutoff: date,
    catalogue: pd.DataFrame | None,
    families_cfg=None,
) -> None:
    """train.sale_date.max() <= cutoff < test.sale_date.min(); no serial in both.

    Raises ``AssertionError`` naming the offending values. When ``catalogue`` and
    ``families_cfg`` are given and the test frame carries ``launch_date``,
    ``n_launches_since`` is recomputed from the catalogue truncated to
    ``launch_date <= cutoff`` and must equal the value in ``test``: a catalogue row after
    the cutoff must never have changed a test feature. This turns the by-construction
    guarantee of the feature builder into a check that fails loudly if the clamp in
    ``features.n_launches_since_vec`` is ever weakened.
    """
    cutoff_ts = pd.Timestamp(as_date(cutoff))
    if (
        catalogue is not None
        and len(catalogue)
        and families_cfg is not None
        and len(test)
        and {"launch_date", "n_launches_since", "model_family", "sale_date"}.issubset(test.columns)
    ):
        truncated = catalogue[to_datetime_series(catalogue["launch_date"]) <= cutoff_ts]
        recomputed = n_launches_since_vec(
            truncated, test["model_family"], test["launch_date"], test["sale_date"], as_date(cutoff), families_cfg
        )
        got = test["n_launches_since"].to_numpy(dtype=int)
        if not np.array_equal(recomputed, got):
            bad = np.flatnonzero(recomputed != got)[:5]
            raise AssertionError(
                f"leakage: n_launches_since differs when catalogue rows after {cutoff_ts.date()} are removed "
                f"(rows {bad.tolist()})"
            )
    if len(train):
        tmax = to_datetime_series(train["sale_date"]).max()
        if tmax > cutoff_ts:
            raise AssertionError(f"leakage: train max sale_date {tmax.date()} > cutoff {cutoff_ts.date()}")
    if len(test):
        tmin = to_datetime_series(test["sale_date"]).min()
        if tmin <= cutoff_ts:
            raise AssertionError(f"leakage: test min sale_date {tmin.date()} <= cutoff {cutoff_ts.date()}")
    if len(train) and len(test):
        shared = set(train["serial"]) & set(test["serial"])
        if shared:
            raise AssertionError(f"leakage: serials in train and test: {sorted(shared)[:5]}")


# --------------------------------------------------------------------------------------
# backtest
# --------------------------------------------------------------------------------------
@dataclass
class BacktestResult:
    backtest_id: str
    cutoff: date
    model_family: str
    n_train: int
    n_test: int
    mape: float
    bias: float
    wape: float
    mae_eur: float
    rmse_log: float
    train_max_sale_date: date
    test_min_sale_date: date


def build_test_frame(
    resale: pd.DataFrame,
    devices: pd.DataFrame,
    catalogue: pd.DataFrame,
    cutoff: date,
    families_cfg,
) -> pd.DataFrame:
    """Sales with ``sale_date > cutoff`` (non As-Is, positive prices), features as knowable at the cutoff."""
    cutoff = as_date(cutoff)
    if resale is None or len(resale) == 0:
        return pd.DataFrame(columns=TRAINING_COLUMNS)
    df = resale.merge(_device_columns(devices), on="serial", how="inner")
    df["sale_date"] = to_datetime_series(df["sale_date"])
    df["launch_date"] = to_datetime_series(df["launch_date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce").astype(float)
    df["purchase_price"] = pd.to_numeric(df["purchase_price"], errors="coerce").astype(float)
    df = df[
        (df["sale_date"] > pd.Timestamp(cutoff))
        & (df["price"] > 0)
        & (df["purchase_price"] > 0)
        & (df["channel"] != "as_is")
    ].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=TRAINING_COLUMNS)
    # base storage: only catalogue rows known at the cutoff
    cat_known = catalogue
    if catalogue is not None and len(catalogue):
        cat_known = catalogue[to_datetime_series(catalogue["launch_date"]) <= pd.Timestamp(cutoff)]
    base = _base_storage_lookup(cat_known, devices)
    df["base_storage_gb"] = df["model"].map(base).fillna(df["storage_gb"]).astype(float)
    df["storage_gb"] = df["storage_gb"].astype(float)
    df["grade"] = df["grade_at_sale"].astype(str)
    df["rv_ratio"] = df["price"] / df["purchase_price"]
    df["y"] = np.log(df["rv_ratio"])
    df["months_since_launch"] = months_since(df["launch_date"], df["sale_date"])
    df["n_launches_since"] = n_launches_since_vec(
        catalogue, df["model_family"], df["launch_date"], df["sale_date"], cutoff, families_cfg
    )
    df["log_storage"] = np.log(df["storage_gb"] / df["base_storage_gb"])
    return df[TRAINING_COLUMNS].sort_values(["sale_date", "serial"]).reset_index(drop=True)


def backtest_frames(
    resale: pd.DataFrame,
    devices: pd.DataFrame,
    catalogue: pd.DataFrame,
    events: pd.DataFrame | None,
    a,
    families_cfg,
    cutoff: date,
    min_n_per_family: int = 50,
) -> tuple[pd.DataFrame, pd.DataFrame, ResidualValueModel]:
    """(train, test with ``pred_ratio`` and ``pred_price`` columns, fitted model).

    The model is fitted at ``as_of = cutoff`` under the run id ``bt-<cutoff>`` and is
    NOT saved to ``forecast_runs``.
    """
    cutoff = as_date(cutoff)
    train = build_training_frame(resale, devices, catalogue, events, cutoff, families_cfg)
    test = build_test_frame(resale, devices, catalogue, cutoff, families_cfg)
    assert_no_leakage(train, test, cutoff, catalogue, families_cfg)
    rvm = fit(train, as_is_frame(resale, devices, cutoff), cutoff, f"bt-{cutoff:%Y-%m-%d}", a, min_n_per_family)
    test = test.copy()
    test["pred_ratio"] = predict_ratio_frame(rvm, test) if len(test) else np.zeros(0)
    test["pred_price"] = test["pred_ratio"] * test["purchase_price"]
    return train, test, rvm


def _metrics(fam: str, train: pd.DataFrame, test: pd.DataFrame, cutoff: date, backtest_id: str) -> BacktestResult:
    a = test["price"].to_numpy(dtype=float)
    p = test["pred_price"].to_numpy(dtype=float)
    return BacktestResult(
        backtest_id=backtest_id,
        cutoff=cutoff,
        model_family=fam,
        n_train=int(len(train)),
        n_test=int(len(test)),
        mape=mape(a, p),
        bias=bias(a, p),
        wape=wape(a, p),
        mae_eur=mae(a, p),
        rmse_log=rmse_log(a, p),
        train_max_sale_date=to_datetime_series(train["sale_date"]).max().date() if len(train) else cutoff,
        test_min_sale_date=to_datetime_series(test["sale_date"]).min().date(),
    )


def time_split_backtest(
    resale: pd.DataFrame,
    devices: pd.DataFrame,
    catalogue: pd.DataFrame,
    events: pd.DataFrame | None,
    a,
    families_cfg,
    cutoff: date,
    min_n_per_family: int = 50,
    backtest_id: str | None = None,
) -> list[BacktestResult]:
    """One time split at ``cutoff``; metrics on EUR price per family and ``'*'`` overall.

    Families without test rows are skipped, so every reported metric is finite. An
    empty test set returns an empty list.
    """
    cutoff = as_date(cutoff)
    train, test, _ = backtest_frames(resale, devices, catalogue, events, a, families_cfg, cutoff, min_n_per_family)
    if len(test) == 0:
        return []
    if backtest_id is None:
        backtest_id = f"bt-{cutoff:%Y-%m-%d}-{uuid.uuid4().hex[:6]}"
    out: list[BacktestResult] = []
    for fam in sorted(pd.unique(test["model_family"])):
        te = test[test["model_family"] == fam]
        tr = train[train["model_family"] == fam]
        if len(te) < MIN_TEST_ROWS_PER_FAMILY:
            continue
        out.append(_metrics(str(fam), tr, te, cutoff, backtest_id))
    out.append(_metrics("*", train, test, cutoff, backtest_id))
    return out


def results_to_frame(results: list[BacktestResult], run_at: datetime | None = None) -> pd.DataFrame:
    """``backtest_result`` rows."""
    cols = [
        "backtest_id",
        "run_at",
        "cutoff",
        "model_family",
        "n_train",
        "n_test",
        "mape",
        "bias",
        "wape",
        "mae_eur",
        "rmse_log",
        "train_max_sale_date",
        "test_min_sale_date",
    ]
    if not results:
        return pd.DataFrame(columns=cols)
    run_at = run_at or datetime.now(timezone.utc).replace(tzinfo=None)
    df = pd.DataFrame([asdict(r) for r in results])
    df["run_at"] = pd.Timestamp(run_at)
    for c in ("cutoff", "train_max_sale_date", "test_min_sale_date"):
        df[c] = pd.to_datetime(df[c])
    return df[cols]
