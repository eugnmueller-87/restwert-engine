"""Fit realisation curves on the public anchors: ln(q) = a + b * age + grade offsets.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Same functional family as the fleet model (``restwert.forecast.model``): log-linear in
model age, grade as an additive offset in log space, ordinary least squares. Two
populations are fitted separately and never mixed: marketplace asks (grades A to D,
grade B is the reference) and buy-back or trade-in bids (TRADEIN). Groups with fewer
than ``min_n`` anchors or less than ``min_age_span`` months of age spread are reported
with their counts but no curve; a thin fit is labelled, not hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from restwert.market.anchors import GRADE_ORDER

MARKET_GRADES = ["A", "B", "C", "D"]
REFERENCE_GRADE = "B"
HORIZONS = (12, 24, 36, 48)   # the four contract terms of config.TERM_MONTHS


@dataclass
class CurveFit:
    group_kind: str
    group: str
    population: str  # "marketplace" or "tradein"
    n: int
    age_min: float
    age_max: float
    intercept: float | None = None
    slope_per_month: float | None = None
    grade_offsets: dict[str, float] = field(default_factory=dict)
    mape_in_sample: float | None = None
    predictions: dict[int, float] = field(default_factory=dict)
    fit_quality: str = "ok"  # ok | thin | no_fit

    def predict(self, age_months: float, grade: str = REFERENCE_GRADE) -> float | None:
        if self.intercept is None or self.slope_per_month is None:
            return None
        off = self.grade_offsets.get(grade, 0.0)
        return float(math.exp(self.intercept + self.slope_per_month * age_months + off))


def _design(df: pd.DataFrame, with_grades: bool) -> tuple[np.ndarray, list[str]]:
    cols = ["intercept", "age"]
    X = [np.ones(len(df)), df["age_months"].to_numpy(dtype=float)]
    if with_grades:
        for g in MARKET_GRADES:
            if g == REFERENCE_GRADE:
                continue
            if (df["grade"] == g).any():
                X.append((df["grade"] == g).to_numpy(dtype=float))
                cols.append(f"grade_{g}")
    return np.column_stack(X), cols


def fit_one(df: pd.DataFrame, group_kind: str, group: str, population: str, min_n: int, min_age_span: float) -> CurveFit:
    """Fit one population of one group; returns counts only when the data cannot carry a slope."""
    n = int(len(df))
    age_min = float(df["age_months"].min()) if n else float("nan")
    age_max = float(df["age_months"].max()) if n else float("nan")
    fit = CurveFit(group_kind, group, population, n, round(age_min, 1) if n else age_min, round(age_max, 1) if n else age_max)
    if n < min_n:
        fit.fit_quality = "no_fit"
        return fit
    if (age_max - age_min) < min_age_span:
        fit.fit_quality = "no_fit"
        return fit
    y = np.log(df["realisation"].to_numpy(dtype=float))
    X, cols = _design(df, with_grades=(population == "marketplace"))
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fit.intercept = float(beta[0])
    fit.slope_per_month = float(beta[1])
    fit.grade_offsets = {c.replace("grade_", ""): float(b) for c, b in zip(cols[2:], beta[2:])}
    pred = np.exp(X @ beta)
    q = df["realisation"].to_numpy(dtype=float)
    fit.mape_in_sample = round(float(np.mean(np.abs(pred - q) / q)), 4)
    fit.predictions = {h: round(fit.predict(h), 4) for h in HORIZONS}
    if n < 2 * min_n or fit.slope_per_month > 0:
        fit.fit_quality = "thin"
    return fit


def fit_curves(anchors: pd.DataFrame, min_n: int = 6, min_age_span: float = 6.0) -> pd.DataFrame:
    """Curves per family, per family and oem, for marketplace and trade-in populations.

    Returns one row per (group_kind, group, population) with n, age range, slope, monthly
    depreciation, grade offsets, in-sample MAPE, predicted realisation at 12, 24, 36 and 48
    months (``HORIZONS``, the contract terms; grade B for marketplace) and fit_quality.
    """
    if anchors is None or anchors.empty:
        return pd.DataFrame(columns=_COLUMNS)
    a = anchors[anchors["grade"].isin(GRADE_ORDER)].copy()
    a["population"] = np.where(a["grade"] == "TRADEIN", "tradein", "marketplace")
    groups: list[tuple[str, str, pd.DataFrame]] = [("all", "all", a)]
    for fam, g in a.groupby("family", sort=True):
        groups.append(("family", str(fam), g))
    for (fam, oem), g in a.groupby(["family", "oem"], sort=True):
        groups.append(("family_oem", f"{fam} / {oem}", g))
    rows = []
    for kind, name, g in groups:
        for pop, gp in g.groupby("population", sort=True):
            f = fit_one(gp, kind, name, str(pop), min_n, min_age_span)
            rows.append(_row(f))
    return pd.DataFrame(rows, columns=_COLUMNS)


_COLUMNS = [
    "group_kind", "group", "population", "n", "age_min", "age_max", "intercept", "slope_per_month",
    "monthly_depreciation_pct", "grade_A_offset", "grade_C_offset", "grade_D_offset", "mape_in_sample",
    "q_12", "q_24", "q_36", "q_48", "fit_quality",
]


def _row(f: CurveFit) -> dict:
    dep = None if f.slope_per_month is None else round(1.0 - math.exp(f.slope_per_month), 4)
    return {
        "group_kind": f.group_kind,
        "group": f.group,
        "population": f.population,
        "n": f.n,
        "age_min": f.age_min,
        "age_max": f.age_max,
        "intercept": None if f.intercept is None else round(f.intercept, 4),
        "slope_per_month": None if f.slope_per_month is None else round(f.slope_per_month, 5),
        "monthly_depreciation_pct": dep,
        "grade_A_offset": f.grade_offsets.get("A"),
        "grade_C_offset": f.grade_offsets.get("C"),
        "grade_D_offset": f.grade_offsets.get("D"),
        "mape_in_sample": f.mape_in_sample,
        "q_12": f.predictions.get(12),
        "q_24": f.predictions.get(24),
        "q_36": f.predictions.get(36),
        "q_48": f.predictions.get(48),
        "fit_quality": f.fit_quality,
    }


__all__ = ["CurveFit", "fit_one", "fit_curves", "HORIZONS", "MARKET_GRADES", "REFERENCE_GRADE"]
