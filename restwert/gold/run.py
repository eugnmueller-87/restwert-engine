"""Compute the gold KPIs and write ``gold.kpi_values`` / ``gold.kpi_breakdown`` (SPEC_v0.2 section 8.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``compute_one_gold`` runs one registered gold KPI and applies the ``min_n``
  rule exactly like v0.1 ``kpi.compute.compute_one``: below ``min_n`` the
  value is kept for reference but the status is ``not_measurable``. ``min_n``
  comes from ``config/kpi_targets.yaml`` (``min_n[kpi_id]``, owner
  ``min_n_owner``) and falls back to the registry default of 1 only when the
  file names no value for the KPI; an
  exception inside a KPI function becomes ``not_measurable`` with the
  exception text, never a silent 0.
- ``compute_all_gold`` runs every gold KPI in page order into the two frames.
- ``run_gold_kpis`` replaces the rows of this ``as_of`` in both gold tables
  (other as_of rows are kept), stamps the page owner and the target from
  ``kpi_targets.yaml`` when a key matches, and regenerates ``docs/GOLD_KPIS.md``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from restwert import db
from restwert.config import KpiTargets
from restwert.gold.registry import GOLD_KPI_REGISTRY, GoldKpiSpec, page_order
from restwert.records import KpiValue, RunSummary

GOLD_KPI_VALUES_COLUMNS: tuple[str, ...] = (
    "kpi_id",
    "as_of",
    "name",
    "page",
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
    "owner",
    "run_id",
)

GOLD_KPI_BREAKDOWN_COLUMNS: tuple[str, ...] = ("kpi_id", "as_of", "dimension", "dimension_value", "value", "numerator", "denominator", "n")


def _ensure_registry_loaded() -> None:
    """Import the KPI module so the registry is complete even on a direct import of this module."""

    import restwert.gold.kpis  # noqa: F401


def compute_one_gold(kpi_id: str, con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Run one gold KPI; apply the ``min_n`` rule; never let an exception escape as a value."""

    _ensure_registry_loaded()
    spec = GOLD_KPI_REGISTRY.get(kpi_id)
    if spec is None:
        raise KeyError(f"unknown gold KPI id {kpi_id}")
    try:
        result = spec.fn(con, as_of, targets)
    except Exception as exc:  # noqa: BLE001  (documented choice: report, do not hide behind 0)
        return KpiValue.not_measurable(f"error while computing: {type(exc).__name__}: {exc}")
    if not isinstance(result, KpiValue):
        return KpiValue.not_measurable(f"KPI function returned {type(result).__name__}, not KpiValue")
    if result.status == "not_measurable":
        result.value = None
        return result
    min_n = effective_min_n(spec, targets)
    if result.n < min_n:
        result.status = "not_measurable"
        suffix = f"n={result.n} below min_n={min_n} (config/kpi_targets.yaml, owner {min_n_owner(targets)}); value kept for reference"
        result.note = f"{result.note}; {suffix}" if result.note else suffix
    return result


def effective_min_n(spec: GoldKpiSpec, targets: KpiTargets | None) -> int:
    """``min_n`` of one gold KPI: ``config/kpi_targets.yaml`` ``min_n[kpi_id]`` (owned), else the registry default."""

    if targets is not None and getattr(targets, "min_n", None):
        value = targets.min_n.get(spec.kpi_id)
        if value is not None:
            return max(int(value), 1)
    return int(spec.min_n)


def min_n_owner(targets: KpiTargets | None) -> str:
    owner = str(getattr(targets, "min_n_owner", "") or "").strip() if targets is not None else ""
    return owner or "registry default, no owner"


def _target_for(spec: GoldKpiSpec, targets: KpiTargets | None) -> float | None:
    if targets is None:
        return None
    value = targets.targets.get(spec.kpi_id)
    return None if value is None else float(value)


def compute_all_gold(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    targets: KpiTargets,
    run_id: str | None = None,
    kpi_ids: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute the requested gold KPIs (default: all, page order) into the two table-shaped frames."""

    _ensure_registry_loaded()
    ids = list(kpi_ids) if kpi_ids is not None else page_order()
    value_rows: list[dict[str, Any]] = []
    breakdown_rows: list[dict[str, Any]] = []
    for kpi_id in ids:
        spec = GOLD_KPI_REGISTRY[kpi_id]
        kv = compute_one_gold(kpi_id, con, as_of, targets)
        value_rows.append(
            {
                "kpi_id": kpi_id,
                "as_of": as_of,
                "name": spec.name,
                "page": spec.page,
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
                "owner": spec.owner,
                "run_id": run_id,
            }
        )
        if kv.breakdown is not None and not kv.breakdown.empty:
            bd = kv.breakdown.drop_duplicates(subset=["dimension", "dimension_value"], keep="first")
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
    values = pd.DataFrame.from_records(value_rows, columns=list(GOLD_KPI_VALUES_COLUMNS))
    breakdown = pd.DataFrame.from_records(breakdown_rows, columns=list(GOLD_KPI_BREAKDOWN_COLUMNS))
    for frame, cols in ((values, ("value", "numerator", "denominator", "target")), (breakdown, ("value", "numerator", "denominator"))):
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    values["n"] = values["n"].astype("int64")
    breakdown["n"] = pd.to_numeric(breakdown["n"], errors="coerce").astype("Int64")
    return values, breakdown


def _replace_for_as_of(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame, as_of: date) -> int:
    con.execute(f"DELETE FROM {table} WHERE as_of = ?", [as_of])
    if frame.empty:
        return 0
    return int(db.write_df(con, table, frame, mode="append"))


def run_gold_kpis(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    targets: KpiTargets,
    write_catalogue_md: bool = True,
) -> RunSummary:
    """Compute every gold KPI, write both gold KPI tables for this as_of, render ``docs/GOLD_KPIS.md``."""

    from restwert.gold.catalogue import write_gold_catalogue
    from restwert.lake.schema_lake import create_lake_schema

    started = datetime.now(timezone.utc)
    create_lake_schema(con, drop_layers=())
    run_id = db.new_run(con, "gold-kpis", None, as_of, None)
    values, breakdown = compute_all_gold(con, as_of, targets, run_id=run_id)
    n_values = _replace_for_as_of(con, "gold.kpi_values", values, as_of)
    n_breakdown = _replace_for_as_of(con, "gold.kpi_breakdown", breakdown, as_of)
    notes: list[str] = []
    if write_catalogue_md:
        path = write_gold_catalogue(targets=targets)
        notes.append(f"gold catalogue written to {path}")
    n_ok = int((values["status"] == "ok").sum())
    n_nm = int((values["status"] == "not_measurable").sum())
    counts = {
        "gold_kpi_values": int(n_values),
        "gold_kpi_breakdown": int(n_breakdown),
        "gold_kpis_ok": n_ok,
        "gold_kpis_not_measurable": n_nm,
    }
    for _, row in values[values["status"] != "ok"].iterrows():
        notes.append(f"{row['kpi_id']} not measurable: {row['note']}")
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="gold-kpis",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=(finished - started).total_seconds(),
        counts=counts,
        notes=notes,
    )


__all__ = [
    "GOLD_KPI_VALUES_COLUMNS",
    "GOLD_KPI_BREAKDOWN_COLUMNS",
    "compute_one_gold",
    "effective_min_n",
    "min_n_owner",
    "compute_all_gold",
    "run_gold_kpis",
]
