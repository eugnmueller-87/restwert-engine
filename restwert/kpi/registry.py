"""KPI registry and shared helpers (SPEC section 7.1).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``KpiSpec`` describes one KPI: id, exact name, area, definition, formula text,
source tables, the "measurable from" sentence, unit, direction and ``min_n``.
The ``register`` decorator adds a KPI function to ``KPI_REGISTRY`` and to the
area's id list in ``KPI_TREE``. The tree has exactly five areas in this order:
Top, Procurement, Inventory, Recommerce, Indirect.

Every KPI function has the signature ``fn(con, as_of, targets) -> KpiValue``.
It reads DuckDB tables only, never another module's Python.

The helpers at the bottom (``load_table``, ``to_datetime``, ``to_numeric``,
``window_mask``, ``ratio``, ``make_breakdown``) are the shared defensive
plumbing: a missing table, a missing column, an all-NULL column or an empty
denominator all end in ``KpiValue.not_measurable(note)`` with ``value=None``.
Choice where the spec is silent: whole tables are read into pandas and
filtered there. Tables in v0.1 are a few thousand rows, and reading the whole
table gives one uniform place to check that the required columns exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable, Literal

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.config import KpiTargets
from restwert.dates import add_months
from restwert.records import KpiValue

KpiFn = Callable[[duckdb.DuckDBPyConnection, date, KpiTargets], KpiValue]

AREAS: tuple[str, ...] = ("Top", "Procurement", "Inventory", "Recommerce", "Indirect")
DIRECTIONS: tuple[str, ...] = ("up", "down", "zero", "one")
BREAKDOWN_COLUMNS: tuple[str, ...] = ("dimension", "dimension_value", "value", "numerator", "denominator", "n")


@dataclass(frozen=True)
class KpiSpec:
    """Static description of one KPI; the function itself is ``fn``."""

    kpi_id: str
    name: str
    area: str
    definition: str
    formula_text: str
    source_tables: tuple[str, ...]
    measurable_from: str
    unit: str
    direction: Literal["up", "down", "zero", "one"]
    min_n: int
    fn: KpiFn


KPI_REGISTRY: dict[str, KpiSpec] = {}
KPI_TREE: list[tuple[str, list[str]]] = [(area, []) for area in AREAS]


def register(
    *,
    kpi_id: str,
    name: str,
    area: str,
    definition: str,
    formula_text: str,
    source_tables: tuple[str, ...] | list[str],
    measurable_from: str,
    unit: str,
    direction: Literal["up", "down", "zero", "one"],
    min_n: int = 1,
) -> Callable[[KpiFn], KpiFn]:
    """Decorator that registers a KPI function under ``kpi_id``.

    Raises ``ValueError`` on a duplicate id, an unknown area or direction, or
    an empty descriptive field, so a half-described KPI never reaches the
    catalogue.
    """

    if kpi_id in KPI_REGISTRY:
        raise ValueError(f"KPI id {kpi_id} registered twice")
    if area not in AREAS:
        raise ValueError(f"KPI {kpi_id}: area {area!r} is not one of {AREAS}")
    if direction not in DIRECTIONS:
        raise ValueError(f"KPI {kpi_id}: direction {direction!r} is not one of {DIRECTIONS}")
    for field_name, text in (
        ("name", name),
        ("definition", definition),
        ("formula_text", formula_text),
        ("measurable_from", measurable_from),
        ("unit", unit),
    ):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"KPI {kpi_id}: {field_name} must be a non-empty string")
    if not source_tables:
        raise ValueError(f"KPI {kpi_id}: source_tables must not be empty")
    if min_n < 1:
        raise ValueError(f"KPI {kpi_id}: min_n must be >= 1")

    def decorator(fn: KpiFn) -> KpiFn:
        spec = KpiSpec(
            kpi_id=kpi_id,
            name=name,
            area=area,
            definition=definition.strip(),
            formula_text=formula_text.strip(),
            source_tables=tuple(source_tables),
            measurable_from=measurable_from.strip(),
            unit=unit,
            direction=direction,
            min_n=min_n,
            fn=fn,
        )
        KPI_REGISTRY[kpi_id] = spec
        for tree_area, ids in KPI_TREE:
            if tree_area == area:
                ids.append(kpi_id)
        return fn

    return decorator


def trailing_window(as_of: date, months: int) -> tuple[date, date]:
    """Inclusive window ``(add_months(as_of, -months) + 1 day, as_of)``."""

    return add_months(as_of, -months) + timedelta(days=1), as_of


# ---------------------------------------------------------------------------
# Shared defensive helpers
# ---------------------------------------------------------------------------


def load_table(
    con: duckdb.DuckDBPyConnection,
    table: str,
    required: Iterable[str] = (),
    *,
    allow_empty: bool = False,
) -> tuple[pd.DataFrame | None, str]:
    """Read a whole table; return ``(frame, "")`` or ``(None, reason)``.

    ``None`` is returned when the table does not exist, a required column is
    missing, the table is empty (unless ``allow_empty``) or a required column
    is entirely NULL. The reason string is meant to become the KpiValue note.
    """

    required = tuple(required)
    if not db.table_exists(con, table):
        return None, f"table {table} does not exist"
    frame = db.read_df(con, f"SELECT * FROM {table}")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        return None, f"table {table} lacks column(s) {', '.join(missing)}"
    if frame.empty:
        if allow_empty:
            return frame, ""
        return None, f"table {table} is empty"
    all_null = [c for c in required if frame[c].isna().all()]
    if all_null:
        return None, f"table {table}: column(s) {', '.join(all_null)} entirely NULL"
    return frame, ""


def _to_float(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def to_numeric(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Coerce columns (DECIMAL objects included) to float64 in place."""

    for col in columns:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col].map(_to_float), errors="coerce").astype("float64")
    return frame


def to_datetime(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Coerce DATE / TIMESTAMP / date-object columns to datetime64 in place."""

    for col in columns:
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce")
    return frame


def as_bool(series: pd.Series) -> pd.Series:
    """Nullable boolean-ish column to plain bool (NULL counts as False)."""

    return series.map(lambda v: bool(v) if v is not None and v == v else False).astype(bool)


def window_mask(series: pd.Series, start: date, end: date) -> pd.Series:
    """Boolean mask ``start <= series <= end`` on a datetime64 column."""

    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end)
    return series.notna() & (series >= lo) & (series <= hi)


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator`` or ``None`` when the denominator is unusable."""

    if numerator is None or denominator is None:
        return None
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(den) or den == 0 or not np.isfinite(num):
        return None
    return num / den


def make_breakdown(dimension: str, rows: Iterable[dict[str, Any]]) -> pd.DataFrame | None:
    """Build a breakdown frame with the fixed columns; ``None`` if no rows.

    Each row dict carries ``dimension_value`` and optionally ``value``,
    ``numerator``, ``denominator`` and ``n``. A missing ``value`` is derived
    as ``ratio(numerator, denominator)``.
    """

    records: list[dict[str, Any]] = []
    for row in rows:
        numerator = row.get("numerator")
        denominator = row.get("denominator")
        value = row["value"] if "value" in row else ratio(numerator, denominator)
        records.append(
            {
                "dimension": dimension,
                "dimension_value": str(row["dimension_value"]),
                "value": None if value is None else float(value),
                "numerator": None if numerator is None else float(numerator),
                "denominator": None if denominator is None else float(denominator),
                "n": int(row.get("n", 0)),
            }
        )
    if not records:
        return None
    return pd.DataFrame.from_records(records, columns=list(BREAKDOWN_COLUMNS))


def ratio_breakdown(frame: pd.DataFrame, dimension: str, numerator_col: str, denominator_col: str | None) -> pd.DataFrame | None:
    """Group ``frame`` by ``dimension``; value = sum(num) / sum(den) (count when den is None)."""

    if dimension not in frame.columns or frame.empty:
        return None
    rows: list[dict[str, Any]] = []
    for key, grp in frame.groupby(frame[dimension].fillna("(none)"), sort=True):
        num = float(grp[numerator_col].sum())
        den = float(grp[denominator_col].sum()) if denominator_col else float(len(grp))
        rows.append({"dimension_value": key, "numerator": num, "denominator": den, "n": int(len(grp))})
    return make_breakdown(dimension, rows)


def ok(
    value: float | None,
    numerator: float | None,
    denominator: float | None,
    n: int,
    note: str = "",
    breakdown: pd.DataFrame | None = None,
) -> KpiValue:
    """Build an ``ok`` KpiValue; falls back to not_measurable when value is None."""

    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return KpiValue.not_measurable(note or "value not finite")
    return KpiValue(
        value=float(value),
        numerator=None if numerator is None else float(numerator),
        denominator=None if denominator is None else float(denominator),
        n=int(n),
        status="ok",
        note=note,
        breakdown=breakdown,
    )


def fmt_window(start: date, end: date) -> str:
    return f"{start.isoformat()} to {end.isoformat()}"
