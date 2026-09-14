"""Residual value model, method ``loglinear_step_v1`` (SPEC 5.2).

    ln(rv_ratio) = b0 + b1*months_since_launch + b2*n_launches_since
                 + b3*grade_B + b4*grade_C + b5*grade_D + b6*ln(storage/base)
                 + b7*ch_employee_buyout + b8*ch_b2b_wholesale + e

Fitted per family with ``numpy.linalg.lstsq``. No hinge, no recency weighting, no
scikit-learn. Families with fewer than ``min_n_per_family`` training rows use ONE
pooled fit over all rows with family dummies (``fit_quality = 'pooled'``). When the
whole training set is below ``min_n_per_family`` the model falls back to the planned
residual value ratio from ``config/assumptions.yaml`` (``fit_quality = 'none'``): the
intercept is set to ``ln(planned_rv_ratio)`` and every other coefficient to 0, so the
generic prediction path returns the planned ratio.

The point forecast is the MEDIAN (``exp(mu)``), not the mean. The bias metric in the
backtest and the error series reveals the median/mean gap; nobody corrects for it
silently.

Unsupported design columns
--------------------------
A dummy column with zero variance in a family's training set (every grade-D sale goes
As Is, so ``grade_D`` is all zero after the As-Is exclusion) has NO identified
coefficient: ``lstsq`` silently returns 0 for it, which would make a grade-D forecast
equal to the grade-A line. ``fit`` detects such columns per family (and for the pooled
fit), stores them in ``ResidualValueModel.unsupported`` and adds a note. A prediction for
an unsupported grade returns the family's As-Is ratio instead of the grade-A line, and
``fit_quality_for`` labels it ``'unsupported_grade'``; the band collapses to the point.
"""

from __future__ import annotations

import json
import math
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from restwert.forecast.features import (
    FEATURE_NAMES,
    POOLED_EXTRA,
    design_matrix,
    feature_row,
)

METHOD = "loglinear_step_v1"
RATIO_MIN = 0.02
RATIO_MAX = 0.95
BAND_Z = 1.28  # 80 % band on the log scale
POOLED_KEY = "pooled"
DEFAULT_FAMILIES: tuple[str, ...] = ("iphone_like", "android_like", "laptop_like")
MIN_AS_IS_ROWS = 10


class ResidualValueModel(BaseModel):
    """Serialisable fit result. One instance per forecast run (all families)."""

    run_id: str
    as_of: date
    method: str = METHOD
    coefficients: dict[str, dict[str, float]]  # family -> feature -> coef; 'pooled' holds the pooled fit
    sigma_log: dict[str, float]
    n_train: dict[str, int]
    fit_quality: dict[str, str]  # 'family' | 'pooled' | 'none'
    as_is_ratio: dict[str, float]
    feature_names: list[str]
    notes: list[str] = Field(default_factory=list)
    unsupported: dict[str, list[str]] = Field(default_factory=dict)  # family (or 'pooled') -> design columns with zero variance

    def families(self) -> list[str]:
        return [f for f in self.fit_quality]


# --------------------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------------------
def _ols(X: np.ndarray, y: np.ndarray, names: list[str]) -> tuple[dict[str, float], float]:
    """Least squares; sigma from residuals with ddof = number of features (floor at 1)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(len(y) - len(names), 1)
    sigma = float(math.sqrt(float(resid @ resid) / dof))
    coefs = {n: float(b) for n, b in zip(names, beta)}
    return coefs, sigma


def _zero_variance_columns(X: np.ndarray, names: list[str]) -> list[str]:
    """Names of dummy / feature columns (never the intercept) that are constant in ``X``.

    A constant column cannot be told apart from the intercept, so its coefficient is not
    identified. For the grade and channel dummies this means "no such rows in training".
    """
    if X.shape[0] == 0:
        return [n for n in names if n != "intercept"]
    out: list[str] = []
    for j, n in enumerate(names):
        if n == "intercept":
            continue
        col = X[:, j]
        if np.all(col == col[0]):
            out.append(n)
    return out


def unsupported_grades(rvm: ResidualValueModel, family: str) -> set[str]:
    """Grades whose dummy had no training support for ``family`` (via the pooled fit when pooled)."""
    fq = rvm.fit_quality.get(family)
    key = POOLED_KEY if (fq == "pooled" and POOLED_KEY in rvm.coefficients) else family
    if key not in rvm.coefficients and POOLED_KEY in rvm.coefficients:
        key = POOLED_KEY
    cols = rvm.unsupported.get(key, [])
    return {c.split("_", 1)[1] for c in cols if c.startswith("grade_")}


def fit_quality_for(rvm: ResidualValueModel, family: str, grade: str) -> str:
    """``fit_quality`` label for a (family, grade) forecast; ``'unsupported_grade'`` when the
    grade had no training support and the As-Is ratio was returned instead of the regression."""
    if str(grade) in unsupported_grades(rvm, family):
        return "unsupported_grade"
    return rvm.fit_quality.get(family, "none")


def _as_is_ratios(as_is: pd.DataFrame | None, families: list[str], a) -> dict[str, float]:
    out: dict[str, float] = {}
    for fam in families:
        n = 0
        med = float("nan")
        if as_is is not None and len(as_is):
            sub = as_is.loc[as_is["model_family"] == fam, "rv_ratio"].astype(float).dropna()
            n = len(sub)
            if n:
                med = float(sub.median())
        if n >= MIN_AS_IS_ROWS and np.isfinite(med):
            out[fam] = med
        else:
            out[fam] = float(a.get("as_is_ratio_fallback", fam))
    return out


def fit(
    train: pd.DataFrame,
    as_is: pd.DataFrame | None,
    as_of: date,
    run_id: str,
    a,
    min_n_per_family: int = 50,
) -> ResidualValueModel:
    """Fit ``loglinear_step_v1`` on a training frame from ``features.build_training_frame``.

    ``a`` is the ``Assumptions`` object (``planned_rv_ratio`` and ``as_is_ratio_fallback``
    are read from it).
    """
    train = train if train is not None else pd.DataFrame()
    fam_in_data = [str(f) for f in pd.unique(train["model_family"])] if len(train) else []
    families = list(DEFAULT_FAMILIES) + [f for f in fam_in_data if f not in DEFAULT_FAMILIES]

    coefficients: dict[str, dict[str, float]] = {}
    sigma_log: dict[str, float] = {}
    n_train: dict[str, int] = {}
    fit_quality: dict[str, str] = {}
    notes: list[str] = []
    feature_names = list(FEATURE_NAMES)

    total = len(train)
    if total < min_n_per_family:
        notes.append(
            f"only {total} training rows (< {min_n_per_family}); every family uses the planned "
            "residual value ratio from assumptions.yaml (fit_quality = none)"
        )
        for fam in families:
            planned = float(a.get("planned_rv_ratio", fam))
            planned = min(max(planned, RATIO_MIN), RATIO_MAX)
            coefs = {n: 0.0 for n in FEATURE_NAMES}
            coefs["intercept"] = math.log(planned)
            coefficients[fam] = coefs
            sigma_log[fam] = 0.0
            n_train[fam] = int((train["model_family"] == fam).sum()) if total else 0
            fit_quality[fam] = "none"
        return ResidualValueModel(
            run_id=run_id,
            as_of=as_of,
            method=METHOD,
            coefficients=coefficients,
            sigma_log=sigma_log,
            n_train=n_train,
            fit_quality=fit_quality,
            as_is_ratio=_as_is_ratios(as_is, families, a),
            feature_names=feature_names,
            notes=notes,
        )

    need_pooled = False
    unsupported: dict[str, list[str]] = {}
    for fam in families:
        sub = train[train["model_family"] == fam]
        n_train[fam] = int(len(sub))
        if len(sub) >= min_n_per_family:
            X, names = design_matrix(sub, pooled=False)
            coefs, sigma = _ols(X, sub["y"].to_numpy(dtype=float), names)
            dead = _zero_variance_columns(X, names)
            if dead:
                for n in dead:
                    coefs[n] = 0.0  # not identified; predictions for these dummies never use it
                unsupported[fam] = dead
                notes.append(
                    f"{fam}: no training support for {', '.join(dead)}; a forecast for such a grade "
                    "returns the family As-Is ratio (fit_quality unsupported_grade)"
                )
            coefficients[fam] = coefs
            sigma_log[fam] = sigma
            fit_quality[fam] = "family"
        else:
            need_pooled = True
            fit_quality[fam] = "pooled"

    if need_pooled:
        X, names = design_matrix(train, pooled=True)
        coefs, sigma = _ols(X, train["y"].to_numpy(dtype=float), names)
        dead = _zero_variance_columns(X, names)
        if dead:
            for n in dead:
                coefs[n] = 0.0
            unsupported[POOLED_KEY] = dead
            notes.append(f"pooled: no training support for {', '.join(dead)}")
        coefficients[POOLED_KEY] = coefs
        sigma_log[POOLED_KEY] = sigma
        n_train[POOLED_KEY] = int(total)
        feature_names = list(FEATURE_NAMES) + list(POOLED_EXTRA)
        for fam in families:
            if fit_quality[fam] == "pooled":
                sigma_log[fam] = sigma
                notes.append(f"{fam}: {n_train[fam]} rows < {min_n_per_family}, pooled fit with family dummy")

    return ResidualValueModel(
        run_id=run_id,
        as_of=as_of,
        method=METHOD,
        coefficients=coefficients,
        sigma_log=sigma_log,
        n_train=n_train,
        fit_quality=fit_quality,
        as_is_ratio=_as_is_ratios(as_is, families, a),
        feature_names=feature_names,
        notes=notes,
        unsupported=unsupported,
    )


# --------------------------------------------------------------------------------------
# predict
# --------------------------------------------------------------------------------------
def _coef_set(rvm: ResidualValueModel, family: str) -> tuple[dict[str, float], bool]:
    """(coefficients, pooled) for a family. Unknown family: pooled if present, else the
    first family with a fit (documented fallback, keeps prediction total)."""
    fq = rvm.fit_quality.get(family)
    if fq == "pooled" and POOLED_KEY in rvm.coefficients:
        return rvm.coefficients[POOLED_KEY], True
    if family in rvm.coefficients:
        return rvm.coefficients[family], False
    if POOLED_KEY in rvm.coefficients:
        return rvm.coefficients[POOLED_KEY], True
    first = next(iter(rvm.coefficients))
    return rvm.coefficients[first], False


def _coef_vector(coefs: dict[str, float], pooled: bool) -> np.ndarray:
    names = list(FEATURE_NAMES) + (list(POOLED_EXTRA) if pooled else [])
    return np.asarray([coefs.get(n, 0.0) for n in names], dtype=float)


def _mu(
    rvm: ResidualValueModel,
    *,
    family: str,
    months_since_launch: float,
    n_launches_since: int,
    grade: str,
    storage_gb: int,
    base_storage_gb: int,
    channel: str,
) -> float:
    coefs, pooled = _coef_set(rvm, family)
    base = float(base_storage_gb) if base_storage_gb and base_storage_gb > 0 else float(storage_gb)
    stor = float(storage_gb) if storage_gb and storage_gb > 0 else base
    log_storage = math.log(stor / base) if base > 0 and stor > 0 else 0.0
    x = feature_row(
        months_since_launch=months_since_launch,
        n_launches_since=n_launches_since,
        grade=grade,
        log_storage=log_storage,
        channel=channel,
        family=family,
        pooled=pooled,
    )
    return float(x @ _coef_vector(coefs, pooled))


def clip_ratio(r: float) -> float:
    return float(min(max(r, RATIO_MIN), RATIO_MAX))


def predict_ratio(
    rvm: ResidualValueModel,
    *,
    family: str,
    months_since_launch: float,
    n_launches_since: int,
    grade: str,
    storage_gb: int,
    base_storage_gb: int,
    channel: str = "marketplace",
) -> float:
    """Median residual value ratio on purchase price, clipped to [0.02, 0.95].

    Channel ``as_is`` returns the family's As-Is ratio (median of realised As-Is sales or
    the assumptions fallback) and never goes through the regression. A grade without
    training support (see ``unsupported_grades``) returns the same As-Is ratio instead of
    the grade-A line the unidentified coefficient would produce.
    """
    if channel == "as_is" or str(grade) in unsupported_grades(rvm, family):
        return float(rvm.as_is_ratio.get(family, next(iter(rvm.as_is_ratio.values()), RATIO_MIN)))
    mu = _mu(
        rvm,
        family=family,
        months_since_launch=months_since_launch,
        n_launches_since=n_launches_since,
        grade=grade,
        storage_gb=storage_gb,
        base_storage_gb=base_storage_gb,
        channel=channel,
    )
    return clip_ratio(math.exp(mu))


def predict_band(
    rvm: ResidualValueModel,
    *,
    family: str,
    months_since_launch: float,
    n_launches_since: int,
    grade: str,
    storage_gb: int,
    base_storage_gb: int,
    channel: str = "marketplace",
) -> tuple[float, float, float]:
    """(low, point, high): ``exp(mu -/+ 1.28 * sigma_log)``, each clipped to [0.02, 0.95].
    As-Is and unsupported grades collapse the band to the point."""
    if channel == "as_is" or str(grade) in unsupported_grades(rvm, family):
        r = predict_ratio(
            rvm,
            family=family,
            months_since_launch=months_since_launch,
            n_launches_since=n_launches_since,
            grade=grade,
            storage_gb=storage_gb,
            base_storage_gb=base_storage_gb,
            channel=channel,
        )
        return r, r, r
    mu = _mu(
        rvm,
        family=family,
        months_since_launch=months_since_launch,
        n_launches_since=n_launches_since,
        grade=grade,
        storage_gb=storage_gb,
        base_storage_gb=base_storage_gb,
        channel=channel,
    )
    sigma = float(rvm.sigma_log.get(family, 0.0))
    return (
        clip_ratio(math.exp(mu - BAND_Z * sigma)),
        clip_ratio(math.exp(mu)),
        clip_ratio(math.exp(mu + BAND_Z * sigma)),
    )


def channel_factor(rvm: ResidualValueModel, family: str, channel: str) -> float:
    """Multiplier vs the marketplace baseline: ``exp(coef ch_<channel>)``; 1.0 marketplace; NaN as_is."""
    if channel == "marketplace":
        return 1.0
    if channel == "as_is":
        return float("nan")
    coefs, _ = _coef_set(rvm, family)
    return float(math.exp(coefs.get(f"ch_{channel}", 0.0)))


def predict_ratio_frame(rvm: ResidualValueModel, df: pd.DataFrame, channel: str = "marketplace") -> np.ndarray:
    """Vectorised :func:`predict_ratio` over a frame with columns model_family,
    months_since_launch, n_launches_since, grade, storage_gb, base_storage_gb (and
    optionally channel; the ``channel`` argument is used when the column is absent)."""
    n = len(df)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    work = df.copy()
    if "channel" not in work.columns:
        work["channel"] = channel
    work["grade"] = work["grade"].astype(str)
    stor = work["storage_gb"].to_numpy(dtype=float)
    base = work["base_storage_gb"].to_numpy(dtype=float)
    base = np.where(np.isfinite(base) & (base > 0), base, stor)
    stor = np.where(np.isfinite(stor) & (stor > 0), stor, base)
    with np.errstate(divide="ignore", invalid="ignore"):
        work["log_storage"] = np.where((base > 0) & (stor > 0), np.log(stor / base), 0.0)
    fam = work["model_family"].astype(str).to_numpy()
    ch = work["channel"].astype(str).to_numpy()
    grade = work["grade"].to_numpy()
    for f in pd.unique(fam):
        mask = fam == f
        coefs, pooled = _coef_set(rvm, f)
        X, _ = design_matrix(work[mask], pooled=pooled)
        mu = X @ _coef_vector(coefs, pooled)
        r = np.clip(np.exp(mu), RATIO_MIN, RATIO_MAX)
        as_is_mask = ch[mask] == "as_is"
        dead = unsupported_grades(rvm, f)
        if dead:
            as_is_mask = as_is_mask | np.isin(grade[mask], list(dead))
        if as_is_mask.any():
            r = np.where(as_is_mask, rvm.as_is_ratio.get(f, RATIO_MIN), r)
        out[mask] = r
    return out


# --------------------------------------------------------------------------------------
# (de)serialise
# --------------------------------------------------------------------------------------
def to_json(rvm: ResidualValueModel) -> dict[str, str]:
    """JSON strings for the ``forecast_runs`` *_json columns."""
    return {
        "n_train_json": json.dumps(rvm.n_train, sort_keys=True),
        "fit_quality_json": json.dumps(rvm.fit_quality, sort_keys=True),
        "coefficients_json": json.dumps(rvm.coefficients, sort_keys=True),
        "sigma_log_json": json.dumps(rvm.sigma_log, sort_keys=True),
        "as_is_ratio_json": json.dumps(rvm.as_is_ratio, sort_keys=True),
        "feature_names_json": json.dumps(rvm.feature_names),
        "unsupported_json": json.dumps(rvm.unsupported, sort_keys=True),
    }


def from_run_row(row: pd.Series) -> ResidualValueModel:
    """Rebuild a model from one ``forecast_runs`` row."""
    as_of = row["as_of"]
    if isinstance(as_of, pd.Timestamp):
        as_of = as_of.date()
    elif isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    elif hasattr(as_of, "date") and not isinstance(as_of, date):
        as_of = as_of.date()
    return ResidualValueModel(
        run_id=str(row["run_id"]),
        as_of=as_of,
        method=str(row.get("method", METHOD)),
        coefficients=json.loads(row["coefficients_json"]),
        sigma_log=json.loads(row["sigma_log_json"]),
        n_train={k: int(v) for k, v in json.loads(row["n_train_json"]).items()},
        fit_quality=json.loads(row["fit_quality_json"]),
        as_is_ratio=json.loads(row["as_is_ratio_json"]),
        feature_names=json.loads(row["feature_names_json"]),
        unsupported=json.loads(row["unsupported_json"]) if _present(row, "unsupported_json") else {},
    )


def _present(row: pd.Series, key: str) -> bool:
    if key not in row.index:
        return False
    v = row[key]
    return v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip() != ""
