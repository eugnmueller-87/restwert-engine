"""Aggregation of ``device_pnl`` into ``pnl_aggregate`` (spec section 4.4).

Line items (``rental_revenue``, ``landed_cost``, ``realised_rv``,
``service_and_logistics_cost``) are summed over ALL devices of the group AND, in the
``*_closed`` columns, over closed devices only. Margin sums and means are over closed
devices only, so the P&L identity holds on the ``_closed`` columns of every row::

    sum_lifecycle_margin = sum_rental_revenue_closed - (sum_landed_cost_closed - sum_realised_rv_closed)
                           - sum_service_and_logistics_cost_closed

``margin_pct = sum_lifecycle_margin / sum_landed_cost_closed``. Ratios are computed from
sums, never as a mean of per-device ratios. A Power BI measure built on the all-device
columns is a fleet view (open devices included); one built on the ``_closed`` columns
reconciles to the margin.

Devices without a value in the grouping column (an unsold device has no
``resale_channel``) are grouped under ``'(none)'`` because ``group_value`` is NOT NULL.
"""

from __future__ import annotations

import pandas as pd

AGGREGATE_DIMENSIONS: tuple[str, ...] = ("model_family", "model", "resale_channel", "cohort")

PNL_AGGREGATE_COLUMNS: list[str] = [
    "group_by", "group_value", "as_of", "n", "n_closed",
    "sum_rental_revenue", "sum_landed_cost", "sum_realised_rv",
    "sum_service_and_logistics_cost",
    "sum_rental_revenue_closed", "sum_landed_cost_closed", "sum_realised_rv_closed",
    "sum_service_and_logistics_cost_closed",
    "sum_lifecycle_margin", "mean_lifecycle_margin",
    "margin_pct",
]

NONE_GROUP = "(none)"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").astype("float64")


def aggregate_pnl(device_pnl: pd.DataFrame, by: str) -> pd.DataFrame:
    """Group ``device_pnl`` by one of ``AGGREGATE_DIMENSIONS`` and return ``pnl_aggregate`` rows."""
    if by not in AGGREGATE_DIMENSIONS:
        raise ValueError(f"aggregate_pnl: by must be one of {AGGREGATE_DIMENSIONS}, got {by!r}")
    if device_pnl is None or len(device_pnl) == 0:
        return pd.DataFrame(columns=PNL_AGGREGATE_COLUMNS)

    df = pd.DataFrame({
        "group_value": device_pnl[by].astype(object).where(device_pnl[by].notna(), NONE_GROUP).astype(str)
        if by in device_pnl.columns else NONE_GROUP,
        "rental_revenue": _num(device_pnl, "rental_revenue").fillna(0.0),
        "landed_cost": _num(device_pnl, "landed_cost").fillna(0.0),
        "realised_rv": _num(device_pnl, "realised_rv"),
        "service": _num(device_pnl, "service_and_logistics_cost").fillna(0.0),
        "margin": _num(device_pnl, "lifecycle_margin"),
        "is_closed": device_pnl["is_closed"].astype(bool) if "is_closed" in device_pnl.columns else False,
    })
    as_of = device_pnl["as_of"].iloc[0] if "as_of" in device_pnl.columns else None

    rows = []
    for value, g in df.groupby("group_value", sort=True):
        closed = g[g["is_closed"]]
        sum_margin = float(closed["margin"].sum(skipna=True)) if len(closed) else 0.0
        closed_landed = float(closed["landed_cost"].sum()) if len(closed) else 0.0
        n_margin = int(closed["margin"].notna().sum()) if len(closed) else 0
        rows.append({
            "group_by": by,
            "group_value": str(value),
            "as_of": as_of,
            "n": int(len(g)),
            "n_closed": int(len(closed)),
            "sum_rental_revenue": round(float(g["rental_revenue"].sum()), 2),
            "sum_landed_cost": round(float(g["landed_cost"].sum()), 2),
            "sum_realised_rv": round(float(g["realised_rv"].sum(skipna=True)), 2),
            "sum_service_and_logistics_cost": round(float(g["service"].sum()), 2),
            "sum_rental_revenue_closed": round(float(closed["rental_revenue"].sum()), 2) if len(closed) else 0.0,
            "sum_landed_cost_closed": round(closed_landed, 2),
            "sum_realised_rv_closed": round(float(closed["realised_rv"].sum(skipna=True)), 2) if len(closed) else 0.0,
            "sum_service_and_logistics_cost_closed": round(float(closed["service"].sum()), 2) if len(closed) else 0.0,
            "sum_lifecycle_margin": round(sum_margin, 2) if n_margin else None,
            "mean_lifecycle_margin": round(sum_margin / n_margin, 2) if n_margin else None,
            "margin_pct": (sum_margin / closed_landed) if (n_margin and closed_landed) else None,
        })
    return pd.DataFrame(rows, columns=PNL_AGGREGATE_COLUMNS)
