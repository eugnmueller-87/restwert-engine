"""``run_ledger``: the only DuckDB-touching function of the ledger package (SPEC_v0.2 6.6).

Reads bronze, ``silver.serial_timeline`` (module 1), v0.1 ``device_pnl`` and
``model_catalogue``, and the forecast tables ``rv_forecast_current``,
``rv_forecast_of_record``, ``rv_forecast_grid`` (missing forecast tables degrade to NULL
estimates with a note). Writes ``silver.ledger_lines``, ``silver.device_ledger``,
``silver.reconciliation`` and the five gold tables of the ledger, all in ``mode="replace"``.
Refuses to finish (raises ``ValueError``) when a single serial fails the reconciliation
to ``device_pnl``; the reconciliation table is written before the check so the failure
is visible on the Data page.

Runs after ``forecast``, ``pnl`` and ``timeline``. Nothing here has a side effect outside
the DuckDB file.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from restwert import db
from restwert.lake.schema_lake import LAKE_DDL, create_lake_schema
from restwert.ledger.cohorts import (
    COHORT_KINDS,
    TCO_COHORT_KINDS,
    estimate_vs_anchor,
    purchase_by_oem_month,
    resale_by_channel_grade,
    result_by_cohort,
    tco_by_cohort,
)
from restwert.ledger.device_ledger import build_device_ledger
from restwert.ledger.lines import build_ledger_lines, read_bronze_frames
from restwert.ledger.reconcile import assert_reconciled, reconcile_to_device_pnl
from restwert.records import RunSummary

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

    from restwert.config import Assumptions


LEDGER_SILVER_TABLES: tuple[str, ...] = ("silver.ledger_lines", "silver.device_ledger", "silver.reconciliation")
LEDGER_GOLD_TABLES: tuple[str, ...] = (
    "gold.result_by_cohort",
    "gold.tco_by_cohort",
    "gold.purchase_by_oem_month",
    "gold.estimate_vs_anchor",
    "gold.resale_by_channel_grade",
)


def _read_optional(con: "duckdb.DuckDBPyConnection", table: str) -> pd.DataFrame | None:
    if not db.table_exists(con, table):
        return None
    return db.read_df(con, f"SELECT * FROM {db._q(table)}")


_TMP_VIEW = "_restwert_ledger_write_tmp"
_NUMERIC_PREFIXES = ("DECIMAL", "DOUBLE", "INTEGER", "BIGINT", "FLOAT", "SMALLINT", "HUGEINT", "REAL")


def _write_replace(con: "duckdb.DuckDBPyConnection", table: str, df: pd.DataFrame | None) -> int:
    """Rebuild ``table`` from ``df`` (same contract as ``db.write_df(mode="replace")``, typed and fast).

    The ledger writes about 150k lines with 14 string columns per run; ``db.write_df``
    converts every value through Python, which costs more than building the ledger.
    This writer hands DuckDB typed pandas columns (datetime64 for DATE and TIMESTAMP,
    float with an ``isnan`` guard for money and ratios, bool, object strings with None)
    and casts to the DDL type in SQL. Columns absent from the frame are inserted as NULL,
    frame columns absent from the table are ignored.
    """
    if table in LAKE_DDL:
        # rebuilt on every run: DROP + CREATE resets the primary-key index, which makes the
        # insert of 150k lines about five times faster than DELETE + INSERT on the same table
        con.execute(f"DROP TABLE IF EXISTS {db._q(table)}")
        con.execute(LAKE_DDL[table])
    else:
        con.execute(f"DELETE FROM {db._q(table)}")
    cols = db._table_columns(con, table)
    if df is None or len(df) == 0:
        return 0
    typed: dict[str, pd.Series] = {}
    parts: list[str] = []
    for name, typ in cols:
        t = typ.upper()
        if name not in df.columns:
            parts.append(f'NULL AS "{name}"')
            continue
        s = df[name].reset_index(drop=True)
        cast = f'CAST("{name}" AS {typ}) AS "{name}"'
        guarded = f'CAST(CASE WHEN "{name}" IS NULL OR isnan("{name}") THEN NULL ELSE "{name}" END AS {typ}) AS "{name}"'
        if t in ("DATE", "TIMESTAMP"):
            typed[name] = pd.to_datetime(s, errors="coerce")
            parts.append(cast)
        elif t.startswith(_NUMERIC_PREFIXES):
            typed[name] = pd.to_numeric(s, errors="coerce").astype("float64")
            parts.append(guarded)
        elif t == "BOOLEAN":
            if pd.api.types.is_bool_dtype(s):
                typed[name] = s.astype(bool)
            else:
                typed[name] = pd.Series([None if v is None or (isinstance(v, float) and np.isnan(v)) else bool(v) for v in s], dtype=object)
            parts.append(cast)
        else:
            obj = s.astype(object)
            typed[name] = pd.Series([None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v) for v in obj], dtype=object)
            parts.append(cast)
    frame = pd.DataFrame(typed, index=pd.RangeIndex(len(df)))
    con.register(_TMP_VIEW, frame)
    try:
        con.execute(f'INSERT INTO {db._q(table)} SELECT {", ".join(parts)} FROM {_TMP_VIEW}')
    finally:
        con.unregister(_TMP_VIEW)
    return int(len(frame))


def load_lake_truth(notes: list[str]) -> Any | None:
    """``lakegen.config.load_lake_config().truth_v2`` when the package and ``config/lake.yaml`` exist, else ``None``."""
    try:
        from restwert.lakegen.config import load_lake_config

        return load_lake_config().truth_v2
    except Exception as exc:  # the lake config is optional for the ledger; say why it was skipped
        notes.append(f"lake.yaml truth_v2 not loaded ({type(exc).__name__}); anchor grade D offset defaults to -0.60")
        return None


def run_ledger(con: "duckdb.DuckDBPyConnection", as_of: date, a: "Assumptions") -> RunSummary:
    """Build and write the ledger tables for ``as_of``; raise when the ledger does not reconcile.

    Counts: ``ledger_lines``, ``device_ledger``, ``closed``, ``open``, ``reconciled_ok``,
    ``reconciled_fail``, ``estimate_lines``, ``result_by_cohort`` (plus the other gold tables).
    """
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc)
    notes: list[str] = []
    db.create_schema(con)
    create_lake_schema(con, drop_layers=())
    run_id = db.new_run(con, "ledger", None, as_of, None)

    if not db.table_exists(con, "device_pnl"):
        raise ValueError("device_pnl missing: run pnl before ledger")
    if not db.table_exists(con, "silver.serial_timeline"):
        raise ValueError("silver.serial_timeline missing: run timeline before ledger")
    device_pnl = db.read_df(con, "SELECT * FROM device_pnl")
    timeline = db.read_df(con, 'SELECT * FROM "silver"."serial_timeline"')
    if len(timeline) == 0:
        notes.append("silver.serial_timeline is empty: no holding_cost lines, chain_complete NULL")
    b = read_bronze_frames(con)
    if len(b.erp_goods_receipts) == 0:
        notes.append("bronze.erp_goods_receipts is empty: identity columns are NULL")
    catalogue = _read_optional(con, "model_catalogue")
    rv_current = _read_optional(con, "rv_forecast_current")
    if rv_current is None or len(rv_current) == 0:
        notes.append("rv_forecast_current missing or empty: estimate_rv_today and result_if_liquidated_today are NULL")
    rv_of_record = _read_optional(con, "rv_forecast_of_record")
    if rv_of_record is None or len(rv_of_record) == 0:
        notes.append("rv_forecast_of_record missing or empty: estimate_rv_of_record and realised_vs_record_ratio are NULL")
    rv_grid = _read_optional(con, "rv_forecast_grid")
    if rv_grid is None or len(rv_grid) == 0:
        notes.append("rv_forecast_grid missing or empty: estimate_rv_lease_end uses planned_rv_ratio on landed cost")
    lake_truth = load_lake_truth(notes)

    synthetic = bool(db.is_synthetic(con))
    lines = build_ledger_lines(b, timeline, a, as_of, synthetic)
    dl = build_device_ledger(lines, timeline, b, device_pnl, catalogue, rv_current, rv_of_record, rv_grid, a, lake_truth, as_of)
    recon = reconcile_to_device_pnl(dl, device_pnl, as_of)

    counts: dict[str, int] = {}
    counts["ledger_lines"] = _write_replace(con, "silver.ledger_lines", lines)
    counts["device_ledger"] = _write_replace(con, "silver.device_ledger", dl)
    counts["reconciliation"] = _write_replace(con, "silver.reconciliation", recon)
    counts["closed"] = int(dl["is_closed"].astype(bool).sum()) if len(dl) else 0
    counts["open"] = int(len(dl)) - counts["closed"]
    counts["reconciled_ok"] = int(recon["ok"].astype(bool).sum()) if len(recon) else 0
    counts["reconciled_fail"] = int((~recon["ok"].astype(bool)).sum()) if len(recon) else 0
    counts["estimate_lines"] = int(lines["is_estimate"].astype(bool).sum()) if len(lines) else 0
    assert_reconciled(recon)

    result_frames = [result_by_cohort(dl, kind, as_of) for kind in COHORT_KINDS]
    result_cohorts = pd.concat([f for f in result_frames if len(f)], ignore_index=True) if any(len(f) for f in result_frames) else result_frames[0]
    tco_frames = [tco_by_cohort(dl, lines, kind, as_of) for kind in TCO_COHORT_KINDS]
    tco_cohorts = pd.concat([f for f in tco_frames if len(f)], ignore_index=True) if any(len(f) for f in tco_frames) else tco_frames[0]
    counts["result_by_cohort"] = _write_replace(con, "gold.result_by_cohort", result_cohorts)
    counts["tco_by_cohort"] = _write_replace(con, "gold.tco_by_cohort", tco_cohorts)
    counts["purchase_by_oem_month"] = _write_replace(con, "gold.purchase_by_oem_month", purchase_by_oem_month(dl, b, as_of))
    counts["estimate_vs_anchor"] = _write_replace(con, "gold.estimate_vs_anchor", estimate_vs_anchor(dl, as_of))
    counts["resale_by_channel_grade"] = _write_replace(con, "gold.resale_by_channel_grade", resale_by_channel_grade(dl, as_of))

    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="ledger",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=time.perf_counter() - t0,
        counts=counts,
        notes=notes,
    )


__all__ = ["LEDGER_SILVER_TABLES", "LEDGER_GOLD_TABLES", "run_ledger", "load_lake_truth"]
