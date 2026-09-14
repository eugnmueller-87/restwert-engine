"""References for the levers (spec v0.2 section 7.1).

Every reference here is one of three things, and every function says which:

* a row set of the fleet itself (a percentile or a median over the provider's own devices),
* the forecast grid (``rv_forecast_grid``, read through ``forecast.registry.grid_lookup``),
* the R02 channel filter, re-used so that the channel lever and the channel rule agree.

None of them is an external benchmark. Every fleet reference carries the group size ``n`` so
the caller can refuse it below ``lever_reference_min_n``.

Choices where the spec is silent:

* ``p75`` is pandas ``quantile(0.75)`` with linear interpolation (numpy default).
* The 6-month age bucket is ``floor(months_since_launch_at_sale / 6)``, labelled
  ``"24-30"`` for months 24 (inclusive) to 30 (exclusive).
* Grade at sale is ``grade_at_sale`` when the frame carries it (merged from the recommerce
  order), else ``grade_out`` of the refurbishment, else ``grade_inspected``, else ``grade_used``.
* Months since launch at a date are rounded to the nearest integer before the grid is read.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from restwert.config import Thresholds
from restwert.dates import months_between_float
from restwert.decisions.rules import EPS, SALE_CHANNELS
from restwert.forecast.registry import grid_lookup

#: Column names of the reference frames.
DISCOUNT_REF_COLUMNS: tuple[str, ...] = ("serial", "ref_pct", "reference_source", "n_reference", "group_label")
CHANNEL_NET_COLUMNS: tuple[str, ...] = ("model_family", "grade_at_sale", "sale_quarter", "channel", "median_resale_net", "n")
FAMILY_REALISATION_COLUMNS: tuple[str, ...] = ("catalogue_family", "age_bucket", "grade_at_sale", "median_ratio", "n")
TERM_MEDIAN_COLUMNS: tuple[str, ...] = (
    "model_family", "purchase_half_year", "term_months", "median_result_eur", "median_result_per_month_eur", "n",
)

#: Grade order of the forecast grid: a grid that values a worse grade above a better one at
#: the same (model, month) is not read for a grade comparison (lever L04).
GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D")

AGE_BUCKET_MONTHS = 6


# --------------------------------------------------------------------------------------
# scalar helpers
# --------------------------------------------------------------------------------------


def to_date(v: Any) -> date | None:
    """Coerce a frame value (Timestamp, datetime, date, ISO string, NaT) to a ``date`` or None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(str(v)).date()


def to_float(v: Any) -> float | None:
    """Coerce a frame value (Decimal, numpy scalar, None, NaN) to ``float`` or None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_str(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v)
    return s if s else None


def half_year(d: date) -> str:
    """``"2024-H1"`` for January to June, ``"2024-H2"`` for July to December."""
    d = to_date(d)  # type: ignore[assignment]
    if d is None:
        raise ValueError("half_year needs a date")
    return f"{d.year}-H{1 if d.month <= 6 else 2}"


def age_bucket(months_since_launch: float) -> str:
    """6-month bucket label of an age in months: 27.3 -> ``"24-30"``."""
    lo = int(np.floor(max(float(months_since_launch), 0.0) / AGE_BUCKET_MONTHS)) * AGE_BUCKET_MONTHS
    return f"{lo}-{lo + AGE_BUCKET_MONTHS}"


def months_at(launch: Any, when: Any) -> float | None:
    """Fractional months since launch at ``when`` (None when a date is missing)."""
    ld, wd = to_date(launch), to_date(when)
    if ld is None or wd is None:
        return None
    return months_between_float(ld, wd)


def grade_at_sale_of(row: Any) -> str | None:
    """Grade the device was sold at: grade_at_sale, else grade_out, else grade_inspected, else grade_used."""
    for col in ("grade_at_sale", "grade_out", "grade_inspected", "grade_used"):
        v = _get(row, col)
        s = to_str(v)
        if s is not None:
            return s
    return None


def _get(row: Any, col: str) -> Any:
    """Read a column from a Series, a dict or a namedtuple row; missing -> None."""
    if isinstance(row, dict):
        return row.get(col)
    if isinstance(row, pd.Series):
        return row[col] if col in row.index else None
    return getattr(row, col, None)


# --------------------------------------------------------------------------------------
# frame helpers
# --------------------------------------------------------------------------------------


def _date_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype(float)


def _with_grade_at_sale(dl: pd.DataFrame) -> pd.Series:
    """Vectorised :func:`grade_at_sale_of` over a device ledger frame."""
    out = pd.Series([None] * len(dl), index=dl.index, dtype=object)
    for col in ("grade_at_sale", "grade_out", "grade_inspected", "grade_used"):
        if col in dl.columns:
            vals = dl[col].where(dl[col].notna(), None)
            out = out.where(out.notna(), vals)
    return out


# --------------------------------------------------------------------------------------
# fleet references
# --------------------------------------------------------------------------------------


def reference_discount(dl: pd.DataFrame, min_n: int) -> pd.DataFrame:
    """Per serial: the p75 of ``discount_vs_rrp_pct`` over its fleet group.

    Group 1 is (oem, supplier_role, purchase half-year) when it holds at least ``min_n``
    received serials with a discount; else group 2 is the oem over all time when that holds
    at least ``min_n``; else the serial is not attributed (``ref_pct`` NaN, source ``"none"``).
    Columns: serial, ref_pct, reference_source ("oem+role+half_year" | "oem" | "none"),
    n_reference, group_label.
    """
    empty = pd.DataFrame(columns=list(DISCOUNT_REF_COLUMNS))
    if dl is None or len(dl) == 0:
        return empty
    need = {"serial", "oem", "supplier_role", "purchase_date", "discount_vs_rrp_pct"}
    if not need.issubset(dl.columns):
        return empty
    f = pd.DataFrame(
        {
            "serial": dl["serial"].astype(str),
            "oem": dl["oem"].astype(object),
            "supplier_role": dl["supplier_role"].astype(object),
            "purchase_date": _date_series(dl["purchase_date"]),
            "pct": _num(dl["discount_vs_rrp_pct"]),
        }
    )
    f["hy"] = f["purchase_date"].map(lambda d: half_year(d) if pd.notna(d) else None)
    valid = f[f["pct"].notna() & f["oem"].notna()]

    g1 = (
        valid.dropna(subset=["supplier_role", "hy"])
        .groupby(["oem", "supplier_role", "hy"])["pct"]
        .agg(["count", lambda s: float(s.quantile(0.75))])
    )
    g1.columns = ["n1", "p75_1"]
    g2 = valid.groupby(["oem"])["pct"].agg(["count", lambda s: float(s.quantile(0.75))])
    g2.columns = ["n2", "p75_2"]

    f = f.merge(g1, left_on=["oem", "supplier_role", "hy"], right_index=True, how="left")
    f = f.merge(g2, left_on=["oem"], right_index=True, how="left")
    f["n1"] = f["n1"].fillna(0).astype(int)
    f["n2"] = f["n2"].fillna(0).astype(int)

    use1 = f["n1"] >= int(min_n)
    use2 = (~use1) & (f["n2"] >= int(min_n))
    f["ref_pct"] = np.where(use1, f["p75_1"], np.where(use2, f["p75_2"], np.nan))
    f["reference_source"] = np.where(use1, "oem+role+half_year", np.where(use2, "oem", "none"))
    f["n_reference"] = np.where(use1, f["n1"], np.where(use2, f["n2"], 0)).astype(int)
    f["group_label"] = np.where(
        use1,
        f["oem"].astype(str) + " / " + f["supplier_role"].astype(str) + " / " + f["hy"].astype(str),
        np.where(use2, f["oem"].astype(str) + " / all time", ""),
    )
    return f[list(DISCOUNT_REF_COLUMNS)].reset_index(drop=True)


def realised_channel_net(dl: pd.DataFrame, min_n: int) -> pd.DataFrame:
    """(model_family, grade_at_sale, sale_quarter, channel) -> median ``resale_net`` and n.

    Groups below ``min_n`` are dropped. Informational reference for the resale pages; the
    channel lever itself uses the forecast of record (section 7.2).
    """
    empty = pd.DataFrame(columns=list(CHANNEL_NET_COLUMNS))
    if dl is None or len(dl) == 0 or not {"model_family", "sale_date", "resale_channel", "resale_net"}.issubset(dl.columns):
        return empty
    f = pd.DataFrame(
        {
            "model_family": dl["model_family"].astype(object),
            "grade_at_sale": _with_grade_at_sale(dl),
            "sale_date": _date_series(dl["sale_date"]),
            "channel": dl["resale_channel"].astype(object),
            "net": _num(dl["resale_net"]),
        }
    )
    f = f[f["sale_date"].notna() & f["net"].notna() & f["channel"].notna() & f["grade_at_sale"].notna()]
    if len(f) == 0:
        return empty
    f["sale_quarter"] = f["sale_date"].map(lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}")
    g = f.groupby(["model_family", "grade_at_sale", "sale_quarter", "channel"])["net"].agg(["median", "count"]).reset_index()
    g.columns = ["model_family", "grade_at_sale", "sale_quarter", "channel", "median_resale_net", "n"]
    g = g[g["n"] >= int(min_n)]
    g["median_resale_net"] = g["median_resale_net"].round(2)
    return g[list(CHANNEL_NET_COLUMNS)].reset_index(drop=True)


def family_realisation_median(dl: pd.DataFrame, min_n: int) -> pd.DataFrame:
    """(catalogue_family, 6-month age bucket at sale, grade_at_sale) -> median resale_gross / rrp_net, n.

    Over the fleet's own sold devices. Groups below ``min_n`` are dropped; the manufacturer
    lever passes ``2 x lever_reference_min_n`` (section 7.2, L06).
    """
    empty = pd.DataFrame(columns=list(FAMILY_REALISATION_COLUMNS))
    need = {"catalogue_family", "launch_date", "sale_date", "resale_gross", "rrp_net_eur"}
    if dl is None or len(dl) == 0 or not need.issubset(dl.columns):
        return empty
    f = pd.DataFrame(
        {
            "catalogue_family": dl["catalogue_family"].astype(object),
            "grade_at_sale": _with_grade_at_sale(dl),
            "launch": _date_series(dl["launch_date"]),
            "sale": _date_series(dl["sale_date"]),
            "gross": _num(dl["resale_gross"]),
            "rrp": _num(dl["rrp_net_eur"]),
        }
    )
    f = f[f["sale"].notna() & f["launch"].notna() & f["gross"].notna() & (f["rrp"] > 0) & f["grade_at_sale"].notna()]
    f = f[f["catalogue_family"].notna()]
    if len(f) == 0:
        return empty
    months = (f["sale"] - f["launch"]).dt.days / 30.4375
    f["age_bucket"] = months.map(age_bucket)
    f["ratio"] = f["gross"] / f["rrp"]
    g = f.groupby(["catalogue_family", "age_bucket", "grade_at_sale"])["ratio"].agg(["median", "count"]).reset_index()
    g.columns = ["catalogue_family", "age_bucket", "grade_at_sale", "median_ratio", "n"]
    g = g[g["n"] >= int(min_n)]
    return g[list(FAMILY_REALISATION_COLUMNS)].reset_index(drop=True)


def term_result_medians(dl: pd.DataFrame, min_n: int) -> pd.DataFrame:
    """(model_family, purchase half-year, term_months) -> median ``lifecycle_result_eur`` over closed serials, n.

    ``median_result_per_month_eur`` is the median of ``lifecycle_result_eur / term_months``
    over the same serials: the per-month normalisation L07 compares, so that a 36-month term
    is not credited with its twelve extra months of rent against a 24-month term. Groups
    below ``min_n`` are dropped.
    """
    empty = pd.DataFrame(columns=list(TERM_MEDIAN_COLUMNS))
    need = {"model_family", "purchase_date", "term_months", "lifecycle_result_eur", "is_closed"}
    if dl is None or len(dl) == 0 or not need.issubset(dl.columns):
        return empty
    f = pd.DataFrame(
        {
            "model_family": dl["model_family"].astype(object),
            "purchase_date": _date_series(dl["purchase_date"]),
            "term_months": pd.to_numeric(dl["term_months"], errors="coerce"),
            "result": _num(dl["lifecycle_result_eur"]),
            "is_closed": dl["is_closed"].fillna(False).astype(bool),
        }
    )
    f = f[f["is_closed"] & f["result"].notna() & f["purchase_date"].notna() & f["term_months"].notna() & f["model_family"].notna()]
    if len(f) == 0:
        return empty
    f = f[f["term_months"] > 0]
    if len(f) == 0:
        return empty
    f["purchase_half_year"] = f["purchase_date"].map(half_year)
    f["term_months"] = f["term_months"].astype(int)
    f["per_month"] = f["result"] / f["term_months"]
    g = (
        f.groupby(["model_family", "purchase_half_year", "term_months"])
        .agg(median_result_eur=("result", "median"), median_result_per_month_eur=("per_month", "median"), n=("result", "count"))
        .reset_index()
    )
    g = g[g["n"] >= int(min_n)]
    g["median_result_eur"] = g["median_result_eur"].round(2)
    g["median_result_per_month_eur"] = g["median_result_per_month_eur"].round(4)
    return g[list(TERM_MEDIAN_COLUMNS)].reset_index(drop=True)


# --------------------------------------------------------------------------------------
# grid
# --------------------------------------------------------------------------------------


class GridIndex:
    """Dictionary index over ``rv_forecast_grid`` with the exact semantics of ``grid_lookup``.

    ``grid_lookup`` filters the whole grid frame on every call; the levers read the grid
    tens of thousands of times per run, so ``attribute_all`` builds this index once. The
    lookup clips ``months`` to the grid range of the (model, grade) pair, as ``grid_lookup``.
    """

    def __init__(self, rv_grid: pd.DataFrame | None) -> None:
        self._ratios: dict[tuple[str, str], dict[int, float]] = {}
        self._bounds: dict[tuple[str, str], tuple[int, int]] = {}
        self._fit_quality: dict[tuple[str, str], str] = {}
        if rv_grid is None or len(rv_grid) == 0:
            return
        need = {"model", "grade", "months_since_launch", "forecast_rv_ratio"}
        if not need.issubset(rv_grid.columns):
            return
        models = rv_grid["model"].astype(str).to_numpy()
        grades = rv_grid["grade"].astype(str).to_numpy()
        months = pd.to_numeric(rv_grid["months_since_launch"], errors="coerce").to_numpy()
        ratios = pd.to_numeric(rv_grid["forecast_rv_ratio"], errors="coerce").to_numpy()
        fits = rv_grid["fit_quality"].astype(object).to_numpy() if "fit_quality" in rv_grid.columns else [None] * len(models)
        for mo, gr, m, r, fq in zip(models, grades, months, ratios, fits):
            if np.isnan(m) or np.isnan(r):
                continue
            key = (mo, gr)
            d = self._ratios.setdefault(key, {})
            d[int(m)] = float(r)
            if fq is not None and key not in self._fit_quality and fq == fq:
                self._fit_quality[key] = str(fq)
        for key, d in self._ratios.items():
            self._bounds[key] = (min(d), max(d))

    def __len__(self) -> int:
        return len(self._ratios)

    def fit_quality(self, model: str, grade: str) -> str | None:
        """``fit_quality`` stored on the grid for (model, grade); None when absent."""
        return self._fit_quality.get((str(model), str(grade)))

    def grades_of(self, model: str) -> list[str]:
        """The grades the grid carries for ``model``, in ``GRADE_ORDER``."""
        present = {g for (m, g) in self._ratios if m == str(model)}
        return [g for g in GRADE_ORDER if g in present] + sorted(g for g in present if g not in GRADE_ORDER)

    def lookup(self, model: str, grade: str, months: int) -> tuple[float, int] | None:
        """``(ratio, month used)`` or None when the (model, grade) pair is not on the grid."""
        key = (str(model), str(grade))
        d = self._ratios.get(key)
        if not d:
            return None
        lo, hi = self._bounds[key]
        m = int(min(max(int(months), lo), hi))
        if m in d:
            return d[m], m
        # the grid is dense in v0.1 (every month); nearest month as a guard
        nearest = min(d, key=lambda k: abs(k - m))
        return d[nearest], nearest


def grid_fit_quality(rv_grid: pd.DataFrame | GridIndex | None, model: str, grade: str) -> str | None:
    """``fit_quality`` of the grid rows of (model, grade): from the index, or the first row of the frame."""
    if rv_grid is None:
        return None
    if isinstance(rv_grid, GridIndex):
        return rv_grid.fit_quality(model, grade)
    if len(rv_grid) == 0 or "fit_quality" not in rv_grid.columns:
        return None
    sub = rv_grid[(rv_grid["model"].astype(str) == str(model)) & (rv_grid["grade"].astype(str) == str(grade))]
    if len(sub) == 0:
        return None
    v = sub["fit_quality"].iloc[0]
    return None if v is None or v != v else str(v)


def grid_grades_monotone(rv_grid: pd.DataFrame | GridIndex | None, model: str, months: int) -> tuple[bool, dict[str, float]]:
    """Whether the grid values a better grade at least as high as a worse one at (model, months).

    Returns ``(monotone, ratios by grade)`` over the grades the grid carries for the model in
    ``GRADE_ORDER``; ``monotone`` is true when the ratios are non-increasing from A to D. A
    grid that breaks this (a grade B worth more than grade A) cannot price a grade
    difference and lever L04 refuses the read.
    """
    grades = rv_grid.grades_of(model) if isinstance(rv_grid, GridIndex) else (
        [g for g in GRADE_ORDER if rv_grid is not None and len(rv_grid) and ((rv_grid["model"].astype(str) == str(model)) & (rv_grid["grade"].astype(str) == g)).any()]
    )
    ratios: dict[str, float] = {}
    for g in grades:
        r = grid_ratio(rv_grid, model, g, months)
        if r is not None:
            ratios[g] = float(r)
    ordered = [ratios[g] for g in GRADE_ORDER if g in ratios]
    monotone = all(a >= b - EPS for a, b in zip(ordered, ordered[1:]))
    return monotone, ratios


def grid_ratio(rv_grid: pd.DataFrame | GridIndex | None, model: str, grade: str, months: int) -> float | None:
    """``forecast_rv_ratio`` of the grid at (model, grade, months clipped): ``forecast.registry.grid_lookup``."""
    if rv_grid is None:
        return None
    if isinstance(rv_grid, GridIndex):
        hit = rv_grid.lookup(model, grade, months)
        return hit[0] if hit is not None else None
    if len(rv_grid) == 0:
        return None
    return grid_lookup(rv_grid, str(model), str(grade), int(months))


# --------------------------------------------------------------------------------------
# channels (the R02 filter)
# --------------------------------------------------------------------------------------


def admissible_channels(
    grade: str,
    buyout_eligible: bool,
    days_to_cash: dict[str, float],
    thr: Thresholds,
    as_of: date,
) -> list[str]:
    """The channels R02 (``decide_channel``) would consider, in the same order and with the same rules.

    Grade equal to ``as_is_only_grade`` -> As Is only; every other grade -> every channel
    except As Is; ``employee_buyout`` dropped unless ``buyout_eligible``; channels slower
    than ``channel_max_days_to_cash`` dropped, slowest first, never the last remaining one.
    The channel universe is the key set of ``days_to_cash`` (ordered as ``SALE_CHANNELS``).
    """
    as_is_grade = str(thr.get("as_is_only_grade", as_of=as_of).value)
    max_days = float(thr.get("channel_max_days_to_cash", as_of=as_of).value)
    thr.get("employee_buyout_window_days", as_of=as_of)  # read so a missing threshold is an error here too

    universe = [c for c in SALE_CHANNELS if c in days_to_cash] + sorted(c for c in days_to_cash if c not in SALE_CHANNELS)
    if str(grade) == as_is_grade:
        return [c for c in universe if c == "as_is"]
    allowed = [c for c in universe if c != "as_is"]
    if not buyout_eligible and "employee_buyout" in allowed:
        allowed.remove("employee_buyout")

    def _days(c: str) -> float:
        return float(days_to_cash.get(c, 0.0))

    for c in sorted(allowed, key=lambda c: (-_days(c), c)):
        if len(allowed) <= 1:
            break
        if _days(c) > max_days + EPS:
            allowed.remove(c)
    return allowed


__all__ = [
    "DISCOUNT_REF_COLUMNS",
    "CHANNEL_NET_COLUMNS",
    "FAMILY_REALISATION_COLUMNS",
    "TERM_MEDIAN_COLUMNS",
    "AGE_BUCKET_MONTHS",
    "GRADE_ORDER",
    "GridIndex",
    "grid_fit_quality",
    "grid_grades_monotone",
    "to_date",
    "to_float",
    "to_str",
    "half_year",
    "age_bucket",
    "months_at",
    "grade_at_sale_of",
    "reference_discount",
    "realised_channel_net",
    "family_realisation_median",
    "term_result_medians",
    "grid_ratio",
    "admissible_channels",
]
