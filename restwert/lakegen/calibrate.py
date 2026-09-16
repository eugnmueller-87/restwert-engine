"""Truth calibration of the lake generator (SPEC_v0.2 section 5.3, decision D12).

The synthetic resale price of a device is derived from the PUBLIC anchor curves in
``outputs/market_curves.csv`` (fitted on refurbisher asks, realisation vs launch RRP):

    q_public   = exp(intercept + slope_per_month * age + offset[grade])
    true_price = rrp_net * min(q_public, q_young_cap) * ask_to_realised
                 * channel_mult[channel] * exp(noise)
                 clipped to [ratio_min * rrp_net, ratio_max * rrp_net], rounded to cents

The ``(family_oem, "<Family> / <oem>", marketplace)`` row is used when its
``fit_quality`` is ``ok``, else the ``(family, "<Family>", marketplace)`` row, else the
``default_curve`` of ``truth_v2`` (only when the curves file is missing; the run
summary and ``SYNTHETIC.md`` say so). Grade B is the fitted reference (offset 0),
A and C come from the row (NaN -> 0), D takes ``grade_d_offset_default``: when this was
written the public rows carried no grade D fit; since 16.09.2026 several rows carry a
``grade_D_offset``, and the configured value is still the one used, so the grade D discount of
the synthetic fleet stays a named assumption rather than a fit on few grade D asks. No successor step.

Calibrated to public asks; the haircut and the cap are design parameters, not
market facts. The forecaster never reads this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from restwert.lakegen.config import MARKET_CURVES_CSV, TruthV2

CURVE_COLUMNS: tuple[str, ...] = (
    "group_kind", "group", "population", "n", "age_min", "age_max", "intercept", "slope_per_month",
    "monthly_depreciation_pct", "grade_A_offset", "grade_C_offset", "grade_D_offset", "mape_in_sample",
    "q_12", "q_24", "q_36", "fit_quality",
)
POPULATION: str = "marketplace"


@dataclass(frozen=True)
class TruthCurve:
    """One log-linear realisation curve with its provenance."""

    group: str
    source: Literal["family_oem", "family", "default"]
    fit_quality: str
    n: int
    intercept: float
    slope_per_month: float
    age_min: float
    age_max: float
    grade_offsets: dict[str, float]   # A, B (0), C, D


def load_curves(path: Path = MARKET_CURVES_CSV) -> pd.DataFrame:
    """Read ``outputs/market_curves.csv`` (17 columns); an empty frame when the file is missing."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=list(CURVE_COLUMNS))
    df = pd.read_csv(path, comment="#", encoding="utf-8")
    for c in CURVE_COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[list(CURVE_COLUMNS)]


def _f(value, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else x


def _row_curve(row: pd.Series, source: Literal["family_oem", "family"], cfg: TruthV2) -> TruthCurve:
    return TruthCurve(
        group=str(row["group"]),
        source=source,
        fit_quality=str(row["fit_quality"]),
        n=int(_f(row["n"], 0)),
        intercept=_f(row["intercept"]),
        slope_per_month=_f(row["slope_per_month"]),
        age_min=_f(row["age_min"]),
        age_max=_f(row["age_max"]),
        grade_offsets={
            "A": _f(row["grade_A_offset"]),
            "B": 0.0,
            "C": _f(row["grade_C_offset"]),
            "D": float(cfg.grade_d_offset_default),
        },
    )


def default_curve(cfg: TruthV2) -> TruthCurve:
    """The configured fallback curve, used only when ``market_curves.csv`` is missing."""
    dc = cfg.default_curve
    return TruthCurve(
        group="default",
        source="default",
        fit_quality="default",
        n=0,
        intercept=float(dc["intercept"]),
        slope_per_month=float(dc["slope_per_month"]),
        age_min=0.0,
        age_max=0.0,
        grade_offsets={
            "A": float(dc.get("grade_A_offset", 0.0)),
            "B": 0.0,
            "C": float(dc.get("grade_C_offset", 0.0)),
            "D": float(cfg.grade_d_offset_default),
        },
    )


def truth_curve(curves: pd.DataFrame, catalogue_family: str, oem: str, cfg: TruthV2) -> TruthCurve:
    """family_oem row when ``fit_quality == ok``, else the family row, else the default curve."""
    if curves is not None and len(curves):
        mkt = curves[curves["population"].astype(str) == POPULATION]
        hit = mkt[(mkt["group_kind"].astype(str) == "family_oem") & (mkt["group"].astype(str) == f"{catalogue_family} / {oem}")]
        if len(hit) and str(hit.iloc[0]["fit_quality"]) == "ok":
            return _row_curve(hit.iloc[0], "family_oem", cfg)
        fam = mkt[(mkt["group_kind"].astype(str) == "family") & (mkt["group"].astype(str) == str(catalogue_family))]
        if len(fam) and str(fam.iloc[0]["fit_quality"]) == "ok":
            return _row_curve(fam.iloc[0], "family", cfg)
    return default_curve(cfg)


def q_public(curve: TruthCurve, age_months: float, grade: str) -> float:
    """``exp(intercept + slope * age + offset[grade])``: the public ask as a share of gross launch RRP."""
    offset = curve.grade_offsets.get(str(grade), 0.0)
    return math.exp(curve.intercept + curve.slope_per_month * float(age_months) + offset)


def true_price(
    rrp_net: float,
    curve: TruthCurve,
    age_months: float,
    grade: str,
    channel: str,
    channel_mult: dict[str, float],
    cfg: TruthV2,
    noise: float,
) -> float:
    """The synthetic realised gross price (net EUR) per decision D12; ``noise`` is drawn by the caller."""
    q = min(q_public(curve, age_months, grade), float(cfg.q_young_cap))
    ratio = q * float(cfg.ask_to_realised) * float(channel_mult[channel]) * math.exp(float(noise))
    ratio = min(max(ratio, float(cfg.ratio_min)), float(cfg.ratio_max))
    return round(float(rrp_net) * ratio, 2)


__all__ = [
    "CURVE_COLUMNS",
    "POPULATION",
    "TruthCurve",
    "load_curves",
    "default_curve",
    "truth_curve",
    "q_public",
    "true_price",
]
