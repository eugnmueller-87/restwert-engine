"""Export derived tables to CSV and parquet for Power BI (SPEC 8.2, SPEC_v0.2 9.2).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Everything here is a plain file write into ``out_dir``. Nothing is sent
anywhere. A table that does not exist yet in the database is skipped and
listed under ``missing_tables`` (or ``missing_lake_tables``) in
``manifest.json`` instead of crashing the export, so a partial pipeline still
yields a usable manifest.

v0.2 adds the lake tables: ``export_lake`` writes ``<schema>__<table>.csv`` and
``.parquet`` into ``out_dir`` and, when a lake directory is given, parquet
mirrors under ``<lake_dir>/<schema>/<table>.parquet``. The mirrors are copies
for readers without DuckDB, never the source of truth (that stays the one
DuckDB file). ``EXPORT_TABLES`` and the v0.1 manifest keys are untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import duckdb

from restwert import GOVERNANCE_PRINCIPLE, __version__
from restwert import db
from restwert.paths import OUTPUTS_DIR

EXPORT_TABLES: tuple[str, ...] = (
    "device_pnl",
    "tco_per_model",
    "pnl_aggregate",
    "rv_forecast_grid",
    "rv_forecast_current",
    "rv_forecast_of_record",
    "rv_forecast_error_monthly",
    "backtest_result",
    "decision_log",
    "decision_queue",
    "write_down_ledger",
    "kpi_values",
    "kpi_breakdown",
    "contracts_register",
    "renewal_calendar",
    "advisories",
)

LAKE_EXPORT_TABLES: tuple[str, ...] = (
    "bronze.deliveries",
    "bronze.unresolved",
    "silver.ledger_lines",
    "silver.device_ledger",
    "silver.serial_timeline",
    "silver.reconciliation",
    "silver.contracts",
    "gold.result_by_cohort",
    "gold.tco_by_cohort",
    "gold.purchase_by_oem_month",
    "gold.estimate_vs_anchor",
    "gold.resale_by_channel_grade",
    "gold.chain_quality",
    "gold.ingest_summary",
    "gold.levers_per_device",
    "gold.levers_by_cohort",
    "gold.levers_summary",
    "gold.contract_coverage_by_oem",
    "gold.renewal_calendar_v2",
    "gold.rebate_progress",
    "gold.kpi_values",
    "gold.kpi_breakdown",
)

Format = Literal["csv", "parquet", "both"]


def _sql_path(path: Path) -> str:
    """Render a filesystem path for a DuckDB COPY statement (forward slashes, quotes escaped)."""
    return str(path).replace("\\", "/").replace("'", "''")


def _q(table: str) -> str:
    """``'bronze.x' -> '"bronze"."x"'``, ``'x' -> '"x"'`` (same rule as ``db._q`` in v0.2)."""
    if "." in table:
        schema_name, name = table.split(".", 1)
        return f'"{schema_name}"."{name}"'
    return f'"{table}"'


def _exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Existence check that understands schema-qualified names whatever ``db.table_exists`` does."""
    if "." in table:
        schema_name, name = table.split(".", 1)
    else:
        schema_name, name = "main", table
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema_name, name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0])


def _file_stem(table: str) -> str:
    """``bronze.deliveries`` -> ``bronze__deliveries``; a bare name stays as it is."""
    return table.replace(".", "__")


def export_table(
    con: duckdb.DuckDBPyConnection, table: str, out_dir: Path, fmt: Format = "both"
) -> list[Path]:
    """Write one table as ``<out_dir>/<table>.csv`` and/or ``.parquet`` via DuckDB COPY.

    A schema-qualified table lands as ``<schema>__<table>.<ext>``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    stem = _file_stem(table)
    if fmt in ("csv", "both"):
        target = out_dir / f"{stem}.csv"
        con.execute(
            f"COPY (SELECT * FROM {_q(table)}) TO '{_sql_path(target)}' (HEADER, DELIMITER ',')"
        )
        written.append(target)
    if fmt in ("parquet", "both"):
        target = out_dir / f"{stem}.parquet"
        con.execute(f"COPY (SELECT * FROM {_q(table)}) TO '{_sql_path(target)}' (FORMAT PARQUET)")
        written.append(target)
    return written


def export_all(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path = OUTPUTS_DIR,
    fmt: Format = "both",
    tables: tuple[str, ...] = EXPORT_TABLES,
) -> list[Path]:
    """Export every table in ``tables`` that exists; return the written paths.

    Missing tables are skipped silently here and reported by ``write_manifest``.
    """
    if fmt not in ("csv", "parquet", "both"):
        raise ValueError(f"fmt must be csv, parquet or both, got {fmt!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in tables:
        if not db.table_exists(con, table):
            continue
        written.extend(export_table(con, table, out_dir, fmt))
    return written


def export_lake(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    lake_dir: Path | None,
    fmt: Format = "both",
    tables: tuple[str, ...] = LAKE_EXPORT_TABLES,
) -> list[Path]:
    """Export the lake tables as ``<schema>__<table>.csv/.parquet`` into ``out_dir``.

    When ``lake_dir`` is given, a parquet mirror ``<lake_dir>/<schema>/<table>.parquet``
    is written as well (whatever ``fmt`` says: the mirror is always parquet). A table
    that does not exist is skipped; ``write_manifest`` lists it under
    ``missing_lake_tables``. Returns every path written.
    """
    if fmt not in ("csv", "parquet", "both"):
        raise ValueError(f"fmt must be csv, parquet or both, got {fmt!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in tables:
        if not _exists(con, table):
            continue
        written.extend(export_table(con, table, out_dir, fmt))
        if lake_dir is not None:
            schema_name, name = table.split(".", 1) if "." in table else ("main", table)
            mirror_dir = Path(lake_dir) / schema_name
            mirror_dir.mkdir(parents=True, exist_ok=True)
            target = mirror_dir / f"{name}.parquet"
            con.execute(f"COPY (SELECT * FROM {_q(table)}) TO '{_sql_path(target)}' (FORMAT PARQUET)")
            written.append(target)
    return written


def write_manifest(
    out_dir: Path,
    con: duckdb.DuckDBPyConnection,
    run_id: str | None,
    tables: tuple[str, ...] = EXPORT_TABLES,
    lake_tables: tuple[str, ...] = LAKE_EXPORT_TABLES,
) -> Path:
    """Write ``<out_dir>/manifest.json`` describing what was exported.

    Keys: ``generated_at``, ``version``, ``run_id``, ``is_synthetic``,
    ``governance`` (the governance sentence), ``tables`` (row counts),
    ``missing_tables``, ``files`` (v0.1, untouched), plus ``lake_tables`` (row
    counts of the lake tables present), ``missing_lake_tables`` and
    ``lake_files`` (the ``<schema>__<table>.*`` files found in ``out_dir``).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    present: dict[str, int] = {}
    missing: list[str] = []
    for table in tables:
        if db.table_exists(con, table):
            present[table] = _row_count(con, table)
        else:
            missing.append(table)
    files = sorted(
        p.name for p in out_dir.iterdir() if p.suffix in (".csv", ".parquet") and p.stem in present
    )
    lake_present: dict[str, int] = {}
    lake_missing: list[str] = []
    for table in lake_tables:
        if _exists(con, table):
            lake_present[table] = _row_count(con, table)
        else:
            lake_missing.append(table)
    lake_stems = {_file_stem(t) for t in lake_present}
    lake_files = sorted(
        p.name for p in out_dir.iterdir() if p.suffix in (".csv", ".parquet") and p.stem in lake_stems
    )
    synthetic = bool(db.is_synthetic(con))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": __version__,
        "run_id": run_id,
        "is_synthetic": synthetic,
        "governance": GOVERNANCE_PRINCIPLE,
        "note": (
            "Synthetic data: every number is a design parameter from config/lake.yaml (v0.2) or "
            "config/generator.yaml (v0.1), no market benchmark, no real customer, supplier or employer; "
            "the catalogue and the anchor curves are public."
            if synthetic
            else "Real data loaded via restwert ingest or restwert load."
        ),
        "tables": present,
        "missing_tables": missing,
        "files": files,
        "lake_tables": lake_present,
        "missing_lake_tables": lake_missing,
        "lake_files": lake_files,
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False), encoding="utf-8")
    return path
