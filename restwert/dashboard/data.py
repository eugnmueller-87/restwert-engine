"""Cached DuckDB readers for the dashboard (SPEC 8.3, SPEC_v0.2 9.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Design choice: every reader takes ``db_path`` as its first argument and opens
its own short-lived connection. ``st.cache_data`` keys on the arguments, so a
DuckDB connection object (unhashable, recreated on every rerun) cannot be the
cache key; the path can. DuckDB shares one database instance per path inside a
process, so the readers and the app's own connection do not conflict.

Every reader returns an empty frame (never raises) when the table is missing,
so a half-run pipeline still renders with "no data yet" placeholders.

v0.2 adds readers for the lake tables (``bronze.*``, ``silver.*``, ``gold.*``).
They pass schema-qualified names through ``_read``; the existence check there
understands both bare and qualified names whatever version of
``db.table_exists`` is installed.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd
import streamlit as st

from restwert import db

_CACHE_TTL = 300


def current_db_path() -> str:
    """The DB path chosen in the sidebar (stored by ``app.py`` in session state)."""
    from restwert.paths import DEFAULT_DB

    return str(st.session_state.get("db_path", str(DEFAULT_DB)))


def _q(table: str) -> str:
    """``'bronze.x' -> '"bronze"."x"'``, ``'x' -> '"x"'``."""
    if "." in table:
        schema_name, name = table.split(".", 1)
        return f'"{schema_name}"."{name}"'
    return f'"{table}"'


def _exists(con, table: str) -> bool:
    """Existence check for a bare (``main``) or schema-qualified (``silver.x``) table name."""
    if "." in table:
        schema_name, name = table.split(".", 1)
    else:
        schema_name, name = "main", table
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema_name, name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _read(db_path: str, sql: str, params: list | None = None, needs: Iterable[str] = ()) -> pd.DataFrame:
    """Run ``sql`` read-only; empty frame if a required table is missing."""
    con = db.connect(db_path)
    try:
        for table in needs:
            if not _exists(con, table):
                return pd.DataFrame()
        return db.read_df(con, sql, params)
    finally:
        con.close()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def is_synthetic(db_path: str) -> bool:
    """True if any source table carries ``is_synthetic = true``."""
    con = db.connect(db_path)
    try:
        return bool(db.is_synthetic(con))
    except Exception:
        return False
    finally:
        con.close()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def available_as_of(db_path: str) -> list[date]:
    """Distinct ``kpi_values.as_of`` values, newest first."""
    df = _read(db_path, "SELECT DISTINCT as_of FROM kpi_values ORDER BY as_of DESC", needs=["kpi_values"])
    if df.empty:
        return []
    return [pd.Timestamp(x).date() for x in df["as_of"]]


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def kpi_values(db_path: str, as_of: date) -> pd.DataFrame:
    """All KPI rows for one ``as_of``."""
    return _read(
        db_path,
        "SELECT * FROM kpi_values WHERE as_of = ? ORDER BY area, kpi_id",
        [as_of],
        needs=["kpi_values"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def kpi_breakdown(db_path: str, kpi_id: str, as_of: date) -> pd.DataFrame:
    """Breakdown rows of one KPI for one ``as_of``."""
    return _read(
        db_path,
        "SELECT * FROM kpi_breakdown WHERE kpi_id = ? AND as_of = ? ORDER BY dimension, dimension_value",
        [kpi_id, as_of],
        needs=["kpi_breakdown"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def error_series(db_path: str) -> pd.DataFrame:
    """``rv_forecast_error_monthly`` for all families, ordered by month."""
    return _read(
        db_path,
        "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family",
        needs=["rv_forecast_error_monthly"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def grid(db_path: str) -> pd.DataFrame:
    """``rv_forecast_grid`` of the latest run."""
    return _read(
        db_path,
        "SELECT * FROM rv_forecast_grid ORDER BY model_family, model, grade, months_since_launch",
        needs=["rv_forecast_grid"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def realised_curve_points(db_path: str) -> pd.DataFrame:
    """Resale joined to devices: months since launch at sale, rv_ratio, family, grade, channel."""
    sql = """
        SELECT r.serial, d.model_family, d.model, r.channel, r.grade_at_sale AS grade,
               d.storage_gb, r.sale_date,
               date_diff('day', d.launch_date, r.sale_date) / 30.4375 AS months_since_launch,
               CAST(r.price AS DOUBLE) / NULLIF(CAST(d.purchase_price AS DOUBLE), 0) AS rv_ratio,
               CAST(r.price AS DOUBLE) AS price, CAST(d.purchase_price AS DOUBLE) AS purchase_price
        FROM resale r JOIN devices d USING (serial)
        WHERE d.purchase_price > 0
        ORDER BY r.sale_date
    """
    return _read(db_path, sql, needs=["resale", "devices"])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def device_pnl(db_path: str) -> pd.DataFrame:
    """The whole ``device_pnl`` table."""
    return _read(db_path, "SELECT * FROM device_pnl", needs=["device_pnl"])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def tco(db_path: str) -> pd.DataFrame:
    """``tco_per_model`` ordered by family, model, term."""
    return _read(
        db_path,
        "SELECT * FROM tco_per_model ORDER BY model_family, model, term_months",
        needs=["tco_per_model"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def backtest(db_path: str) -> pd.DataFrame:
    """Rows of the latest backtest_id."""
    sql = """
        SELECT * FROM backtest_result
        WHERE backtest_id = (SELECT backtest_id FROM backtest_result ORDER BY run_at DESC LIMIT 1)
        ORDER BY model_family
    """
    return _read(db_path, sql, needs=["backtest_result"])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def queue(db_path: str) -> pd.DataFrame:
    """``decision_queue`` joined to ``decision_log`` for ``inputs_json`` and ``rule_version``."""
    sql = """
        SELECT q.*, l.inputs_json, l.rule_version, l.outcome_detail, l.threshold_unit
        FROM decision_queue q LEFT JOIN decision_log l USING (decision_id)
        ORDER BY q.priority, q.due_date NULLS LAST, q.value_at_stake_eur DESC NULLS LAST
    """
    return _read(db_path, sql, needs=["decision_queue", "decision_log"])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def renewal_calendar(db_path: str) -> pd.DataFrame:
    """``renewal_calendar`` ordered by end date."""
    return _read(
        db_path,
        "SELECT * FROM renewal_calendar ORDER BY end_date, contract_type, contract_id",
        needs=["renewal_calendar"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def contracts_register(db_path: str) -> pd.DataFrame:
    """``contracts_register`` ordered by type and end date."""
    return _read(
        db_path,
        "SELECT * FROM contracts_register ORDER BY contract_type, end_date, contract_id",
        needs=["contracts_register"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def advisories(db_path: str) -> pd.DataFrame:
    """The ``advisories`` table of the latest forecast run."""
    return _read(db_path, "SELECT * FROM advisories ORDER BY kind, subject_id", needs=["advisories"])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def table_counts(db_path: str, tables: tuple[str, ...]) -> dict[str, int | None]:
    """Row counts per table (None when the table is missing); bare or qualified names."""
    con = db.connect(db_path)
    try:
        out: dict[str, int | None] = {}
        for t in tables:
            if _exists(con, t):
                out[t] = int(con.execute(f"SELECT count(*) FROM {_q(t)}").fetchone()[0])
            else:
                out[t] = None
        return out
    finally:
        con.close()


# --------------------------------------------------------------------------- v0.2 lake readers


def _lake_frame(db_path: str, table: str, order_by: str = "") -> pd.DataFrame:
    """``SELECT * FROM <schema.table>`` (empty frame when the table is missing)."""
    sql = f"SELECT * FROM {_q(table)}" + (f" ORDER BY {order_by}" if order_by else "")
    return _read(db_path, sql, needs=[table])


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def deliveries(db_path: str) -> pd.DataFrame:
    """``bronze.deliveries``: one row per ingested landing file."""
    return _lake_frame(db_path, "bronze.deliveries", "source_system, feed, delivered_on, source_file")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def unresolved(db_path: str) -> pd.DataFrame:
    """``bronze.unresolved``: every rejected or unmatched landing row with its reason."""
    return _lake_frame(db_path, "bronze.unresolved", "feed, reason_code, source_file, row_number")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def ingest_summary(db_path: str) -> pd.DataFrame:
    """``gold.ingest_summary``: one row per feed."""
    return _lake_frame(db_path, "gold.ingest_summary", "source_system, feed")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def chain_quality(db_path: str) -> pd.DataFrame:
    """``gold.chain_quality``: share of serials with each timeline step, per lifecycle status."""
    return _lake_frame(db_path, "gold.chain_quality", "lifecycle_status, step")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def timeline(db_path: str) -> pd.DataFrame:
    """``silver.serial_timeline``: the ten timestamps per serial."""
    return _lake_frame(db_path, "silver.serial_timeline", "serial")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def reconciliation_failures(db_path: str) -> pd.DataFrame:
    """Rows of ``silver.reconciliation`` where the ledger and device_pnl disagree."""
    return _read(
        db_path,
        'SELECT * FROM "silver"."reconciliation" WHERE NOT ok ORDER BY serial, field',
        needs=["silver.reconciliation"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def device_ledger(db_path: str) -> pd.DataFrame:
    """The whole ``silver.device_ledger`` (one row per serial)."""
    return _lake_frame(db_path, "silver.device_ledger", "serial")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def ledger_lines(db_path: str, serial: str) -> pd.DataFrame:
    """``silver.ledger_lines`` of one serial in event order."""
    return _read(
        db_path,
        'SELECT * FROM "silver"."ledger_lines" WHERE serial = ? ORDER BY event_date, line_type',
        [serial],
        needs=["silver.ledger_lines"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def purchase_by_oem_month(db_path: str) -> pd.DataFrame:
    """``gold.purchase_by_oem_month``."""
    return _lake_frame(db_path, "gold.purchase_by_oem_month", "oem, purchase_month, supplier_role")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def tco_by_cohort(db_path: str) -> pd.DataFrame:
    """``gold.tco_by_cohort`` (closed devices only)."""
    return _lake_frame(db_path, "gold.tco_by_cohort", "cohort_kind, cohort_value, line_type")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def estimate_vs_anchor(db_path: str) -> pd.DataFrame:
    """``gold.estimate_vs_anchor`` per (catalogue family, oem)."""
    return _lake_frame(db_path, "gold.estimate_vs_anchor", "catalogue_family, oem")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def resale_by_channel_grade(db_path: str) -> pd.DataFrame:
    """``gold.resale_by_channel_grade`` (trailing 12 months)."""
    return _lake_frame(db_path, "gold.resale_by_channel_grade", "channel, grade_at_sale")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def result_by_cohort(db_path: str, kind: str) -> pd.DataFrame:
    """``gold.result_by_cohort`` rows of one cohort kind."""
    return _read(
        db_path,
        'SELECT * FROM "gold"."result_by_cohort" WHERE cohort_kind = ? ORDER BY cohort_value',
        [kind],
        needs=["gold.result_by_cohort"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def result_cohort_kinds(db_path: str) -> list[str]:
    """Distinct cohort kinds present in ``gold.result_by_cohort``."""
    df = _read(
        db_path,
        'SELECT DISTINCT cohort_kind FROM "gold"."result_by_cohort" ORDER BY 1',
        needs=["gold.result_by_cohort"],
    )
    return [] if df.empty else [str(x) for x in df["cohort_kind"]]


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def levers_summary(db_path: str) -> pd.DataFrame:
    """``gold.levers_summary``: the where-to-tighten table, ranked."""
    return _lake_frame(db_path, "gold.levers_summary", "rank, lever_id")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def levers_by_cohort(db_path: str) -> pd.DataFrame:
    """``gold.levers_by_cohort``."""
    return _lake_frame(db_path, "gold.levers_by_cohort", "cohort_kind, cohort_value, lever_id")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def levers_per_device(db_path: str, lever_id: str) -> pd.DataFrame:
    """``gold.levers_per_device`` rows of one lever, largest delta first."""
    return _read(
        db_path,
        'SELECT * FROM "gold"."levers_per_device" WHERE lever_id = ? ORDER BY delta_eur DESC NULLS LAST, serial',
        [lever_id],
        needs=["gold.levers_per_device"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def contracts_v2(db_path: str) -> pd.DataFrame:
    """``silver.contracts`` (register v2)."""
    return _lake_frame(db_path, "silver.contracts", "counterparty_role, counterparty_name, end_date, contract_id")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def coverage_by_oem(db_path: str) -> pd.DataFrame:
    """``gold.contract_coverage_by_oem``."""
    return _lake_frame(db_path, "gold.contract_coverage_by_oem", "oem")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def renewal_calendar_v2(db_path: str) -> pd.DataFrame:
    """``gold.renewal_calendar_v2`` ordered by end date."""
    return _lake_frame(db_path, "gold.renewal_calendar_v2", "end_date, counterparty_role, contract_id")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def rebate_progress(db_path: str) -> pd.DataFrame:
    """``gold.rebate_progress``."""
    return _lake_frame(db_path, "gold.rebate_progress", "counterparty_name, contract_id")


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def gold_kpi_values(db_path: str, as_of: date) -> pd.DataFrame:
    """``gold.kpi_values`` rows for one ``as_of``."""
    return _read(
        db_path,
        'SELECT * FROM "gold"."kpi_values" WHERE as_of = ? ORDER BY page, kpi_id',
        [as_of],
        needs=["gold.kpi_values"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def gold_kpi_breakdown(db_path: str, kpi_id: str, as_of: date) -> pd.DataFrame:
    """``gold.kpi_breakdown`` rows of one gold KPI for one ``as_of``."""
    return _read(
        db_path,
        'SELECT * FROM "gold"."kpi_breakdown" WHERE kpi_id = ? AND as_of = ? ORDER BY dimension, dimension_value',
        [kpi_id, as_of],
        needs=["gold.kpi_breakdown"],
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def curves_rows(db_path: str) -> pd.DataFrame:
    """``bronze.mkt_curves``: the public anchor curves copied into the lake."""
    return _lake_frame(db_path, "bronze.mkt_curves", 'group_kind, "group", population')


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def queue_for_rules(db_path: str, rule_ids: tuple[str, ...]) -> pd.DataFrame:
    """``decision_queue`` rows of the given rule ids (v0.1 table; empty when missing)."""
    if not rule_ids:
        return pd.DataFrame()
    marks = ", ".join("?" for _ in rule_ids)
    return _read(
        db_path,
        f"SELECT * FROM decision_queue WHERE rule_id IN ({marks}) ORDER BY priority, value_at_stake_eur DESC NULLS LAST",
        list(rule_ids),
        needs=["decision_queue"],
    )


def clear_cache() -> None:
    """Drop every cached frame (called after an action that rewrote tables)."""
    st.cache_data.clear()
