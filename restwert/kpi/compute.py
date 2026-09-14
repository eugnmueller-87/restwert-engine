"""Compute KPIs and write ``kpi_values`` / ``kpi_breakdown`` (SPEC 7.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``compute_one`` runs one registered KPI and applies the ``min_n`` rule: a
  KPI with fewer observations than its ``min_n`` keeps its computed value but
  is marked ``not_measurable`` with a note, so the dashboard greys it.
- ``compute_all`` runs every KPI in tree order and returns the two frames in
  the shape of the ``kpi_values`` and ``kpi_breakdown`` tables.
- ``run_kpis`` writes both tables for this ``as_of`` (rows of the same as_of
  are replaced, other as_of rows are kept) and regenerates
  ``docs/KPI_CATALOGUE.md``.

Choice where the spec is silent: an exception inside a KPI function is caught
and reported as ``not_measurable`` with the exception text in the note. One
broken source table must not take the other seventeen KPIs down with it, and
a visible note is more honest than a silent 0.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from restwert import db
from restwert.config import KpiTargets
from restwert.kpi.registry import KPI_REGISTRY, KPI_TREE, KpiSpec
from restwert.records import KpiValue, RunSummary

KPI_VALUES_COLUMNS: tuple[str, ...] = (
    "kpi_id",
    "as_of",
    "name",
    "area",
    "value",
    "numerator",
    "denominator",
    "n",
    "unit",
    "status",
    "note",
    "target",
    "direction",
    "formula_text",
    "source_tables",
    "run_id",
)

KPI_BREAKDOWN_COLUMNS: tuple[str, ...] = ("kpi_id", "as_of", "dimension", "dimension_value", "value", "numerator", "denominator", "n")


def _ensure_registry_loaded() -> None:
    """Import the KPI modules so the registry is complete even on a direct import of this module."""

    import restwert.kpi.indirect  # noqa: F401
    import restwert.kpi.inventory  # noqa: F401
    import restwert.kpi.procurement  # noqa: F401
    import restwert.kpi.recommerce  # noqa: F401
    import restwert.kpi.top  # noqa: F401


def tree_order() -> list[str]:
    """KPI ids in tree order (Top first), then any registered id not in the tree."""

    _ensure_registry_loaded()
    ordered = [kpi_id for _, ids in KPI_TREE for kpi_id in ids]
    ordered += [kpi_id for kpi_id in KPI_REGISTRY if kpi_id not in ordered]
    return ordered


def compute_one(kpi_id: str, con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Run one KPI; apply the ``min_n`` rule; never let an exception escape as a value."""

    _ensure_registry_loaded()
    spec = KPI_REGISTRY.get(kpi_id)
    if spec is None:
        raise KeyError(f"unknown KPI id {kpi_id}")
    try:
        result = spec.fn(con, as_of, targets)
    except Exception as exc:  # noqa: BLE001  (documented choice: report, do not hide behind 0)
        return KpiValue.not_measurable(f"error while computing: {type(exc).__name__}: {exc}")
    if not isinstance(result, KpiValue):
        return KpiValue.not_measurable(f"KPI function returned {type(result).__name__}, not KpiValue")
    if result.status == "not_measurable":
        if result.value is not None:
            result.value = None
        return result
    if result.n < spec.min_n:
        result.status = "not_measurable"
        suffix = f"n={result.n} below min_n={spec.min_n}; value kept for reference"
        result.note = f"{result.note}; {suffix}" if result.note else suffix
    return result


def _target_for(spec: KpiSpec, targets: KpiTargets | None) -> float | None:
    if targets is None:
        return None
    value = targets.targets.get(spec.kpi_id)
    return None if value is None else float(value)


def compute_all(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    targets: KpiTargets,
    run_id: str | None = None,
    kpi_ids: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute the requested KPIs (default: all, tree order) into two frames."""

    ids = list(kpi_ids) if kpi_ids is not None else tree_order()
    value_rows: list[dict[str, Any]] = []
    breakdown_rows: list[dict[str, Any]] = []
    for kpi_id in ids:
        spec = KPI_REGISTRY[kpi_id]
        kv = compute_one(kpi_id, con, as_of, targets)
        value_rows.append(
            {
                "kpi_id": kpi_id,
                "as_of": as_of,
                "name": spec.name,
                "area": spec.area,
                "value": kv.value,
                "numerator": kv.numerator,
                "denominator": kv.denominator,
                "n": int(kv.n),
                "unit": spec.unit,
                "status": kv.status,
                "note": kv.note or None,
                "target": _target_for(spec, targets),
                "direction": spec.direction,
                "formula_text": spec.formula_text,
                "source_tables": ", ".join(spec.source_tables),
                "run_id": run_id,
            }
        )
        if kv.breakdown is not None and not kv.breakdown.empty:
            bd = kv.breakdown.copy()
            bd = bd.drop_duplicates(subset=["dimension", "dimension_value"], keep="first")
            for _, row in bd.iterrows():
                breakdown_rows.append(
                    {
                        "kpi_id": kpi_id,
                        "as_of": as_of,
                        "dimension": str(row["dimension"]),
                        "dimension_value": str(row["dimension_value"]),
                        "value": None if pd.isna(row["value"]) else float(row["value"]),
                        "numerator": None if pd.isna(row["numerator"]) else float(row["numerator"]),
                        "denominator": None if pd.isna(row["denominator"]) else float(row["denominator"]),
                        "n": None if pd.isna(row["n"]) else int(row["n"]),
                    }
                )
    values = pd.DataFrame.from_records(value_rows, columns=list(KPI_VALUES_COLUMNS))
    breakdown = pd.DataFrame.from_records(breakdown_rows, columns=list(KPI_BREAKDOWN_COLUMNS))
    for frame, cols in ((values, ("value", "numerator", "denominator", "target")), (breakdown, ("value", "numerator", "denominator"))):
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    values["n"] = values["n"].astype("int64")
    breakdown["n"] = pd.to_numeric(breakdown["n"], errors="coerce").astype("Int64")
    return values, breakdown


def _replace_for_as_of(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame, as_of: date) -> int:
    """Delete rows of this as_of, then append the new rows (other as_of rows are kept)."""

    con.execute(f"DELETE FROM {table} WHERE as_of = ?", [as_of])
    if frame.empty:
        return 0
    return int(db.write_df(con, table, frame, mode="append"))


def run_kpis(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets, write_catalogue_md: bool = True) -> RunSummary:
    """Compute every KPI, write ``kpi_values`` and ``kpi_breakdown`` for this as_of, render the catalogue."""

    from restwert.kpi.catalogue import write_catalogue  # local import: catalogue imports registry only

    started = datetime.now(timezone.utc)
    run_id = db.new_run(con, "kpis", None, as_of, None)
    values, breakdown = compute_all(con, as_of, targets, run_id=run_id)
    n_values = _replace_for_as_of(con, "kpi_values", values, as_of)
    n_breakdown = _replace_for_as_of(con, "kpi_breakdown", breakdown, as_of)
    notes: list[str] = []
    if write_catalogue_md:
        path = write_catalogue(targets=targets)
        notes.append(f"catalogue written to {path}")
    n_ok = int((values["status"] == "ok").sum())
    n_nm = int((values["status"] == "not_measurable").sum())
    counts = {
        "kpi_values": int(n_values),
        "kpi_breakdown": int(n_breakdown),
        "kpis_ok": n_ok,
        "kpis_not_measurable": n_nm,
    }
    for _, row in values[values["status"] != "ok"].iterrows():
        notes.append(f"{row['kpi_id']} not measurable: {row['note']}")
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="kpis",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=(finished - started).total_seconds(),
        counts=counts,
        notes=notes,
    )


__all__ = ["compute_one", "compute_all", "run_kpis", "tree_order", "KPI_VALUES_COLUMNS", "KPI_BREAKDOWN_COLUMNS"]
