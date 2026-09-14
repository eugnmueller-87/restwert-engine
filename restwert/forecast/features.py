"""Target, features and the launch-step leakage guard (SPEC 5.1).

The target is ``y = ln(rv_ratio)`` with ``rv_ratio = resale.price / devices.purchase_price``
(gross price, marketplace-referenced). Features are months since launch, the number of
newer generations launched since the device's own generation, grade dummies, log
storage and channel dummies.

Leakage guard
-------------
``n_launches_since`` never counts catalogue rows dated after ``as_of``. Launches after
``as_of`` come from the calendar rule in ``restwert.dates`` only. This makes a forecast
made at ``as_of`` reproducible from what was knowable at ``as_of``.

Date columns in the frames may arrive as ``datetime.date`` objects, ``datetime64`` or
ISO strings; every function normalises through :func:`to_datetime_series` first.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import numpy as np
import pandas as pd

from restwert.dates import DAYS_PER_MONTH, expected_launch_steps

FEATURE_NAMES: list[str] = [
    "intercept",
    "months_since_launch",
    "n_launches_since",
    "grade_B",
    "grade_C",
    "grade_D",
    "log_storage",
    "ch_employee_buyout",
    "ch_b2b_wholesale",
]
POOLED_EXTRA: list[str] = ["fam_android_like", "fam_laptop_like"]

TRAINING_COLUMNS: list[str] = [
    "serial",
    "model_family",
    "model",
    "sale_date",
    "launch_date",
    "channel",
    "grade",
    "storage_gb",
    "base_storage_gb",
    "purchase_price",
    "price",
    "rv_ratio",
    "y",
    "months_since_launch",
    "n_launches_since",
    "log_storage",
]


# --------------------------------------------------------------------------------------
# date helpers
# --------------------------------------------------------------------------------------
def to_datetime_series(s: pd.Series | Iterable) -> pd.Series:
    """Normalise a column of dates (date objects, datetime64, strings) to datetime64[ns]."""
    return pd.to_datetime(pd.Series(s), errors="coerce")


def as_date(x) -> date | None:
    """Coerce a scalar (date, datetime, Timestamp, str, NaT/None) to ``datetime.date``."""
    if x is None:
        return None
    if isinstance(x, pd.Timestamp):
        return None if pd.isna(x) else x.date()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, (np.datetime64, str)):
        ts = pd.Timestamp(x)
        return None if pd.isna(ts) else ts.date()
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(x).date()


def months_since(start: pd.Series, end: pd.Series) -> pd.Series:
    """Vectorised ``months_between_float``: (end - start).days / DAYS_PER_MONTH."""
    return (to_datetime_series(end).values - to_datetime_series(start).values) / np.timedelta64(
        1, "D"
    ) / DAYS_PER_MONTH


# --------------------------------------------------------------------------------------
# launch steps
# --------------------------------------------------------------------------------------
def _family_launch_dates(catalogue: pd.DataFrame, family: str) -> np.ndarray:
    """Sorted datetime64[ns] launch dates of one family from the catalogue."""
    if catalogue is None or len(catalogue) == 0:
        return np.array([], dtype="datetime64[ns]")
    sub = catalogue.loc[catalogue["model_family"] == family, "launch_date"]
    return np.sort(to_datetime_series(sub).dropna().values.astype("datetime64[ns]"))


def observed_launch_steps(catalogue: pd.DataFrame, family: str, launch_date: date, until: date) -> int:
    """Count catalogue rows of ``family`` with ``launch_date > device launch`` and ``<= until``."""
    ld = np.datetime64(pd.Timestamp(launch_date), "ns")
    ut = np.datetime64(pd.Timestamp(until), "ns")
    if ut <= ld:
        return 0
    arr = _family_launch_dates(catalogue, family)
    return int(np.searchsorted(arr, ut, side="right") - np.searchsorted(arr, ld, side="right"))


def n_launches_since(
    catalogue: pd.DataFrame,
    family: str,
    launch_date: date,
    target_date: date,
    as_of: date,
    families_cfg,
) -> int:
    """Launch steps between a device's generation and ``target_date``, knowable at ``as_of``.

    ``observed_launch_steps(..., until=min(target_date, as_of))`` plus, when
    ``target_date > as_of``, ``expected_launch_steps(family, max(as_of, launch_date),
    target_date)`` from the calendar rule. Catalogue rows after ``as_of`` are never
    counted: THE leakage guard.
    """
    launch_date = as_date(launch_date)
    target_date = as_date(target_date)
    as_of = as_date(as_of)
    observed = observed_launch_steps(catalogue, family, launch_date, min(target_date, as_of))
    future = 0
    if target_date > as_of and families_cfg is not None:
        future = int(expected_launch_steps(family, max(as_of, launch_date), target_date, families_cfg))
    return observed + future


def observed_launch_steps_vec(
    catalogue: pd.DataFrame, family: pd.Series, launch_date: pd.Series, until: pd.Series
) -> np.ndarray:
    """Vectorised :func:`observed_launch_steps` for one column of rows (mixed families)."""
    fam = pd.Series(family).to_numpy()
    ld = to_datetime_series(launch_date).values.astype("datetime64[ns]")
    ut = to_datetime_series(until).values.astype("datetime64[ns]")
    out = np.zeros(len(fam), dtype=int)
    for f in pd.unique(fam):
        mask = fam == f
        arr = _family_launch_dates(catalogue, f)
        if len(arr) == 0:
            continue
        hi = np.searchsorted(arr, ut[mask], side="right")
        lo = np.searchsorted(arr, ld[mask], side="right")
        steps = hi - lo
        steps[ut[mask] <= ld[mask]] = 0
        out[mask] = np.maximum(steps, 0)
    return out


def n_launches_since_vec(
    catalogue: pd.DataFrame,
    family: pd.Series,
    launch_date: pd.Series,
    target_date: pd.Series,
    as_of: date,
    families_cfg,
) -> np.ndarray:
    """Vectorised :func:`n_launches_since`. Rows with ``target_date > as_of`` use the calendar rule."""
    as_of_ts = pd.Timestamp(as_of)
    tgt = to_datetime_series(target_date)
    until = tgt.where(tgt <= as_of_ts, as_of_ts)
    out = observed_launch_steps_vec(catalogue, family, launch_date, until)
    if families_cfg is None:
        return out
    fam = pd.Series(family).to_numpy()
    ld_dates = to_datetime_series(launch_date).dt.date.to_numpy()
    tgt_dates = tgt.dt.date.to_numpy()
    future_mask = (tgt > as_of_ts).to_numpy()
    # the calendar rule depends only on (family, start, target): the grid repeats every (model, month)
    # once per grade, so the steps are computed once per distinct key and reused (same numbers, fewer walks)
    memo: dict[tuple[str, date, date], int] = {}
    for i in np.flatnonzero(future_mask):
        start = max(as_of, ld_dates[i])
        key = (str(fam[i]), start, tgt_dates[i])
        steps = memo.get(key)
        if steps is None:
            steps = int(expected_launch_steps(key[0], start, key[2], families_cfg))
            memo[key] = steps
        out[i] += steps
    return out


# --------------------------------------------------------------------------------------
# training frame
# --------------------------------------------------------------------------------------
def _base_storage_lookup(catalogue: pd.DataFrame, devices: pd.DataFrame) -> pd.Series:
    """base_storage_gb per model from the catalogue; falls back to the family minimum storage."""
    if catalogue is not None and len(catalogue) and "base_storage_gb" in catalogue.columns:
        return catalogue.set_index("model")["base_storage_gb"].astype(float)
    fam_min = devices.groupby("model_family")["storage_gb"].min()
    return devices.drop_duplicates("model").set_index("model")["model_family"].map(fam_min).astype(float)


def _device_columns(devices: pd.DataFrame) -> pd.DataFrame:
    cols = ["serial", "model_family", "model", "storage_gb", "purchase_price", "launch_date"]
    return devices[cols].copy()


def build_training_frame(
    resale: pd.DataFrame,
    devices: pd.DataFrame,
    catalogue: pd.DataFrame,
    events: pd.DataFrame | None,
    as_of: date,
    families_cfg,
) -> pd.DataFrame:
    """Rows: sales with ``sale_date <= as_of``, price > 0, purchase_price > 0, channel != as_is.

    ``events`` is accepted for interface stability (grade at sale is already on the
    resale row in v0.1) and not used. ``n_launches_since`` is built with
    ``target_date = sale_date`` and this ``as_of``, so nothing after ``as_of`` leaks in.
    """
    as_of = as_date(as_of)
    if resale is None or len(resale) == 0:
        return pd.DataFrame(columns=TRAINING_COLUMNS)
    df = resale.merge(_device_columns(devices), on="serial", how="inner", suffixes=("", "_dev"))
    df["sale_date"] = to_datetime_series(df["sale_date"])
    df["launch_date"] = to_datetime_series(df["launch_date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce").astype(float)
    df["purchase_price"] = pd.to_numeric(df["purchase_price"], errors="coerce").astype(float)
    df = df[
        (df["sale_date"] <= pd.Timestamp(as_of))
        & (df["price"] > 0)
        & (df["purchase_price"] > 0)
        & (df["channel"] != "as_is")
    ].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=TRAINING_COLUMNS)
    base = _base_storage_lookup(catalogue, devices)
    df["base_storage_gb"] = df["model"].map(base)
    df["base_storage_gb"] = df["base_storage_gb"].fillna(df["storage_gb"]).astype(float)
    df["storage_gb"] = df["storage_gb"].astype(float)
    df["grade"] = df["grade_at_sale"].astype(str)
    df["rv_ratio"] = df["price"] / df["purchase_price"]
    df["y"] = np.log(df["rv_ratio"])
    df["months_since_launch"] = months_since(df["launch_date"], df["sale_date"])
    df["n_launches_since"] = n_launches_since_vec(
        catalogue, df["model_family"], df["launch_date"], df["sale_date"], as_of, families_cfg
    )
    df["log_storage"] = np.log(df["storage_gb"] / df["base_storage_gb"])
    df = df[TRAINING_COLUMNS].sort_values(["sale_date", "serial"]).reset_index(drop=True)
    return df


def as_is_frame(resale: pd.DataFrame, devices: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """As-Is sales with ``sale_date <= as_of`` and their ``rv_ratio``, per family."""
    cols = ["serial", "model_family", "sale_date", "price", "purchase_price", "rv_ratio"]
    if resale is None or len(resale) == 0:
        return pd.DataFrame(columns=cols)
    df = resale[resale["channel"] == "as_is"].merge(_device_columns(devices), on="serial", how="inner")
    df["sale_date"] = to_datetime_series(df["sale_date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce").astype(float)
    df["purchase_price"] = pd.to_numeric(df["purchase_price"], errors="coerce").astype(float)
    df = df[(df["sale_date"] <= pd.Timestamp(as_date(as_of))) & (df["purchase_price"] > 0)].copy()
    df["rv_ratio"] = df["price"] / df["purchase_price"]
    return df[cols].reset_index(drop=True)


# --------------------------------------------------------------------------------------
# design matrix
# --------------------------------------------------------------------------------------
def design_matrix(df: pd.DataFrame, pooled: bool = False) -> tuple[np.ndarray, list[str]]:
    """Design matrix in ``FEATURE_NAMES`` order (plus ``POOLED_EXTRA`` when pooled)."""
    n = len(df)
    grade = df["grade"].astype(str).to_numpy() if n else np.array([], dtype=str)
    channel = df["channel"].astype(str).to_numpy() if n else np.array([], dtype=str)
    cols = [
        np.ones(n),
        df["months_since_launch"].to_numpy(dtype=float) if n else np.zeros(0),
        df["n_launches_since"].to_numpy(dtype=float) if n else np.zeros(0),
        (grade == "B").astype(float),
        (grade == "C").astype(float),
        (grade == "D").astype(float),
        df["log_storage"].to_numpy(dtype=float) if n else np.zeros(0),
        (channel == "employee_buyout").astype(float),
        (channel == "b2b_wholesale").astype(float),
    ]
    names = list(FEATURE_NAMES)
    if pooled:
        fam = df["model_family"].astype(str).to_numpy() if n else np.array([], dtype=str)
        cols.append((fam == "android_like").astype(float))
        cols.append((fam == "laptop_like").astype(float))
        names = names + list(POOLED_EXTRA)
    X = np.column_stack(cols) if n else np.zeros((0, len(names)))
    return X, names


def feature_row(
    *,
    months_since_launch: float,
    n_launches_since: int,
    grade: str,
    log_storage: float,
    channel: str,
    family: str,
    pooled: bool,
) -> np.ndarray:
    """One feature vector in ``FEATURE_NAMES`` (+ ``POOLED_EXTRA``) order."""
    row = [
        1.0,
        float(months_since_launch),
        float(n_launches_since),
        1.0 if grade == "B" else 0.0,
        1.0 if grade == "C" else 0.0,
        1.0 if grade == "D" else 0.0,
        float(log_storage),
        1.0 if channel == "employee_buyout" else 0.0,
        1.0 if channel == "b2b_wholesale" else 0.0,
    ]
    if pooled:
        row.append(1.0 if family == "android_like" else 0.0)
        row.append(1.0 if family == "laptop_like" else 0.0)
    return np.asarray(row, dtype=float)
