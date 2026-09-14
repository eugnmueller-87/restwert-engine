"""CLI v0.2 tests (SPEC_v0.2 9.5): the chain, the flags, the lake tables, exports, timing.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The chain tests run on the session fixture ``lake_pipeline_paths`` (``all --small``
with an explicit ``--lake-dir``); they skip when the v0.2 core packages are not
present in this checkout. The surface tests (``ALL_ORDER_V2``, subcommands and
flags, ``export_lake`` on a bare database, ``all --v01``) run everywhere.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from restwert import db
from restwert.cli import ALL_ORDER, ALL_ORDER_V2, SMALL_SERIALS, build_parser, main
from restwert.export import EXPORT_TABLES, LAKE_EXPORT_TABLES, export_lake, write_manifest
from tests.conftest import lake_packages_available

needs_lake = pytest.mark.skipif(not lake_packages_available(), reason="v0.2 lake packages not present in this checkout")

BRONZE_TRANSACTIONAL = (
    "bronze.erp_purchase_orders",
    "bronze.erp_po_lines",
    "bronze.erp_goods_receipts",
    "bronze.erp_supplier_invoices",
    "bronze.wms_staging_log",
    "bronze.wms_shipments",
    "bronze.portal_rental_contracts",
    "bronze.portal_rental_invoices",
    "bronze.sd_tickets",
    "bronze.ret_receipts",
    "bronze.rf_work_orders",
    "bronze.rc_orders",
    "bronze.rc_credit_notes",
    "bronze.ctr_register",
    "bronze.fin_indirect_spend",
)


def _q(table: str) -> str:
    schema_name, name = table.split(".", 1) if "." in table else ("main", table)
    return f'"{schema_name}"."{name}"'


def _exists(con, table: str) -> bool:
    schema_name, name = table.split(".", 1) if "." in table else ("main", table)
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?", [schema_name, name]
    ).fetchone()
    return bool(row and row[0] > 0)


def _count(con, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0])


def _flags(name: str) -> set[str]:
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
    return {o for a in sub.choices[name]._actions for o in a.option_strings}


# --------------------------------------------------------------------------- surface (no lake needed)


def test_all_v2_default_chain_order():
    assert ALL_ORDER_V2 == (
        "generate-lake", "ingest", "conform", "forecast", "pnl", "timeline", "ledger", "levers", "decide", "contracts", "kpis", "export",
    )
    assert ALL_ORDER == ("generate", "load", "forecast", "pnl", "decide", "kpis", "contracts", "export")
    assert SMALL_SERIALS == 500


def test_new_subcommands_and_flags():
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
    commands = set(sub.choices)
    assert {"generate-lake", "ingest", "conform", "timeline", "ledger", "levers"} <= commands
    assert set(ALL_ORDER_V2) - {"generate-lake"} <= commands and "generate-lake" in commands
    assert {"--seed", "--serials", "--devices", "--lake-dir", "--config", "--catalogue-dir", "--curves"} <= _flags("generate-lake")
    assert {"--source", "--file", "--all", "--lake-dir", "--dry-run", "--db"} <= _flags("ingest")
    assert {"--as-of", "--db", "--csv-dir", "--docs", "--no-docs"} <= _flags("conform")
    assert {"--as-of", "--db"} <= _flags("timeline")
    assert {"--as-of", "--db"} <= _flags("ledger")
    assert {"--as-of", "--db", "--docs", "--no-docs"} <= _flags("levers")
    assert {"--db", "--out", "--fmt", "--lake", "--lake-dir"} <= _flags("export")
    assert {"--lake-dir", "--serials", "--devices", "--cadence", "--v01", "--small", "--keep-db"} <= _flags("all")
    args = parser.parse_args(["all", "--serials", "123", "--cadence", "monthly", "--v01"])
    assert args.devices == 123 and args.cadence == "monthly" and args.v01 is True
    with pytest.raises(SystemExit):
        parser.parse_args(["all", "--cadence", "weekly"])


def test_ingest_rejects_contradicting_flags(tmp_path, capsys):
    rc = main(["ingest", "--all", "--source", "erp/goods_receipts", "--file", "x.csv", "--db", str(tmp_path / "x.duckdb")])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err
    rc = main(["ingest", "--db", str(tmp_path / "y.duckdb")])
    assert rc == 1


def test_export_lake_on_bare_db_lists_missing_lake_tables(tmp_path):
    con = db.connect(":memory:")
    try:
        db.create_schema(con)
        out = tmp_path / "exp"
        written = export_lake(con, out, tmp_path / "lake", "both")
        assert written == []
        path = write_manifest(out, con, None)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["lake_tables"] == {}
        assert manifest["lake_files"] == []
        assert set(manifest["missing_lake_tables"]) == set(LAKE_EXPORT_TABLES)
        assert manifest["missing_tables"] == []
        for key in ("generated_at", "version", "run_id", "is_synthetic", "governance", "tables", "files"):
            assert key in manifest
    finally:
        con.close()


def test_all_v01_flag_runs_legacy_chain(tmp_path, capsys):
    base = tmp_path / "v01"
    rc = main(["all", "--v01", "--small", "--db", str(base / "v01.duckdb"), "--out", str(base / "out"), "--csv-dir", str(base / "raw_csv")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "generate ..." in out and "load ..." in out and "generate-lake" not in out
    con = db.connect(base / "v01.duckdb")
    try:
        assert _exists(con, "device_pnl")
        assert not _exists(con, "bronze.cat_models")
        assert not _exists(con, "silver.device_ledger")
    finally:
        con.close()
    assert (base / "raw_csv" / "devices.csv").exists()
    assert (base / "SYNTHETIC.md").exists()


def test_version_is_0_2_0():
    from restwert import __version__

    assert __version__ == "0.2.0"
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in text


# --------------------------------------------------------------------------- the v0.2 chain


@needs_lake
def test_all_small_v2_under_20s(lake_pipeline_paths):
    assert lake_pipeline_paths.return_code == 0
    assert lake_pipeline_paths.seconds < 20, f"all --small (v0.2) took {lake_pipeline_paths.seconds:.1f}s"


@needs_lake
@pytest.mark.slow
def test_all_default_fleet_under_60s(tmp_path):
    import time

    base = tmp_path / "default"
    t0 = time.perf_counter()
    rc = main(["all", "--serials", "5000", "--db", str(base / "r.duckdb"), "--out", str(base / "out"), "--csv-dir", str(base / "raw_csv"), "--lake-dir", str(base / "lake")])
    seconds = time.perf_counter() - t0
    assert rc == 0
    assert seconds < 60, f"all on 5000 serials took {seconds:.1f}s"
    con = db.connect(base / "r.duckdb")
    try:
        assert _count(con, "silver.device_ledger") >= 5000
        assert int(con.execute('SELECT count(*) FROM "silver"."reconciliation" WHERE NOT ok').fetchone()[0]) == 0
    finally:
        con.close()


@needs_lake
def test_every_lake_table_exists_and_populated(lake_pipeline_db):
    from restwert.lake.schema_lake import LAKE_DDL

    missing = [t for t in LAKE_DDL if not _exists(lake_pipeline_db, t)]
    assert not missing, f"lake tables missing after all --small: {missing}"
    assert _count(lake_pipeline_db, "bronze.deliveries") > 0
    empty = [t for t in BRONZE_TRANSACTIONAL if _count(lake_pipeline_db, t) == 0]
    assert not empty, f"bronze tables without rows: {empty}"
    empty = [t for t in LAKE_DDL if t.startswith(("silver.", "gold.")) and _count(lake_pipeline_db, t) == 0]
    assert not empty, f"silver/gold tables without rows: {empty}"
    assert _count(lake_pipeline_db, "bronze.unresolved") > 0, "the injected defects must leave unresolved rows"


@needs_lake
def test_v01_tables_still_present_and_synthetic(lake_pipeline_db):
    from restwert import schema

    missing = [t for t in schema.TABLE_ORDER if not db.table_exists(lake_pipeline_db, t)]
    assert not missing
    assert db.is_synthetic(lake_pipeline_db) is True
    assert _count(lake_pipeline_db, "device_pnl") == _count(lake_pipeline_db, "silver.device_ledger")


@needs_lake
def test_compat_csvs_loadable_by_v01_load(lake_pipeline_paths, tmp_path):
    from restwert import schema

    for table in schema.SOURCE_TABLES:
        path = lake_pipeline_paths.csv_dir / f"{table}.csv"
        assert path.exists(), f"compat csv missing: {path.name}"
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# SYNTHETIC DATA"), f"{path.name}: {first}"
    target = tmp_path / "compat.duckdb"
    rc = main(["load", "--csv-dir", str(lake_pipeline_paths.csv_dir), "--db", str(target)])
    assert rc == 0
    con = db.connect(target)
    try:
        assert _count(con, "devices") > 0
        assert db.is_synthetic(con) is True
    finally:
        con.close()


@needs_lake
def test_ingest_dry_run_cli_prints_counts_and_writes_nothing(lake_pipeline_paths, capsys):
    raw = lake_pipeline_paths.lake_dir / "raw"
    files = sorted((raw / "erp" / "goods_receipts").glob("*.csv"))
    assert files, "no goods_receipts landing file"
    con = db.connect(lake_pipeline_paths.db)
    try:
        before = (_count(con, "bronze.deliveries"), _count(con, "bronze.erp_goods_receipts"), _count(con, "runs"))
    finally:
        con.close()
    rc = main(["ingest", "--source", "erp/goods_receipts", "--file", str(files[0]), "--dry-run", "--db", str(lake_pipeline_paths.db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ingest" in out and "read=" in out
    con = db.connect(lake_pipeline_paths.db)
    try:
        after = (_count(con, "bronze.deliveries"), _count(con, "bronze.erp_goods_receipts"), _count(con, "runs"))
    finally:
        con.close()
    assert after == before, f"dry run changed the database: {before} -> {after}"


@needs_lake
def test_ingest_all_rerun_is_noop(lake_pipeline_paths, capsys):
    con = db.connect(lake_pipeline_paths.db)
    try:
        before = {t: _count(con, t) for t in BRONZE_TRANSACTIONAL + ("bronze.deliveries", "bronze.unresolved")}
    finally:
        con.close()
    rc = main(["ingest", "--all", "--lake-dir", str(lake_pipeline_paths.lake_dir), "--db", str(lake_pipeline_paths.db)])
    assert rc == 0
    capsys.readouterr()
    con = db.connect(lake_pipeline_paths.db)
    try:
        after = {t: _count(con, t) for t in before}
    finally:
        con.close()
    assert after == before


@needs_lake
def test_ingest_refused_file_returns_one(lake_pipeline_paths, tmp_path, capsys):
    bad = tmp_path / "2024-03-31_goods_receipts_009.csv"
    bad.write_text("# SYNTHETIC DATA - hand\ngr_number,po_number,po_line,serial,received_at,warehouse\nGR-1,PO-1,1,SN-X,2024-01-01T00:00:00,WH\n", encoding="utf-8")
    rc = main(["ingest", "--source", "erp/goods_receipts", "--file", str(bad), "--dry-run", "--db", str(lake_pipeline_paths.db)])
    assert rc == 1
    assert "ERROR" in capsys.readouterr().err
    rc = main(["ingest", "--source", "no/such_feed", "--file", str(bad), "--dry-run", "--db", str(lake_pipeline_paths.db)])
    assert rc == 1


@needs_lake
def test_export_lake_files_and_manifest(lake_pipeline_paths, lake_pipeline_db):
    out = lake_pipeline_paths.out
    for table in EXPORT_TABLES:
        assert (out / f"{table}.csv").exists()
    for table in LAKE_EXPORT_TABLES:
        stem = table.replace(".", "__")
        assert (out / f"{stem}.csv").exists(), f"missing {stem}.csv"
        assert (out / f"{stem}.parquet").exists(), f"missing {stem}.parquet"
        schema_name, name = table.split(".", 1)
        assert (lake_pipeline_paths.lake_dir / schema_name / f"{name}.parquet").exists(), f"missing mirror {schema_name}/{name}.parquet"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["missing_lake_tables"] == []
    assert manifest["missing_tables"] == []
    assert manifest["is_synthetic"] is True
    for table in LAKE_EXPORT_TABLES:
        assert manifest["lake_tables"][table] == _count(lake_pipeline_db, table)
    assert set(manifest["lake_files"]) == {f"{t.replace('.', '__')}.{ext}" for t in LAKE_EXPORT_TABLES for ext in ("csv", "parquet")}
    assert all("__" not in f for f in manifest["files"])


@needs_lake
def test_export_command_with_lake_flag(lake_pipeline_paths, tmp_path):
    out = tmp_path / "exp"
    lake = tmp_path / "lake"
    rc = main(["export", "--db", str(lake_pipeline_paths.db), "--out", str(out), "--fmt", "csv", "--lake", "--lake-dir", str(lake)])
    assert rc == 0
    assert (out / "silver__device_ledger.csv").exists()
    assert not (out / "silver__device_ledger.parquet").exists()
    assert (lake / "silver" / "device_ledger.parquet").exists()
    rc = main(["export", "--db", str(lake_pipeline_paths.db), "--out", str(tmp_path / "exp2"), "--fmt", "csv"])
    assert rc == 0
    assert not (tmp_path / "exp2" / "silver__device_ledger.csv").exists()


@needs_lake
def test_synthetic_md_both_locations(lake_pipeline_paths):
    lake_md = lake_pipeline_paths.lake_dir / "SYNTHETIC.md"
    data_md = lake_pipeline_paths.csv_dir.parent / "SYNTHETIC.md"
    assert lake_md.exists() and data_md.exists()
    assert lake_md.read_text(encoding="utf-8") == data_md.read_text(encoding="utf-8")
    assert "seed" in lake_md.read_text(encoding="utf-8").lower()


@needs_lake
def test_landing_files_land_under_lake_dir_and_raw_csv_is_conformed(lake_pipeline_paths):
    raw = lake_pipeline_paths.lake_dir / "raw"
    files = list(raw.rglob("*.csv"))
    assert len(files) > 19, "one landing file per feed at least"
    assert (lake_pipeline_paths.csv_dir / "devices.csv").exists()


@needs_lake
def test_forecast_rerun_on_lake_db_is_immutable(lake_pipeline_paths, lake_pipeline_db):
    families = {r[0] for r in lake_pipeline_db.execute("SELECT DISTINCT model_family FROM model_catalogue").fetchall()}
    assert families == {"iphone_like", "android_like", "tablet_like", "laptop_like"}
    runs_before = db.read_df(lake_pipeline_db, "SELECT run_id FROM forecast_runs ORDER BY run_id")
    err_before = db.read_df(lake_pipeline_db, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family")
    rc = main(["forecast", "--db", str(lake_pipeline_paths.db), "--no-backtest"])
    assert rc == 0
    runs_after = db.read_df(lake_pipeline_db, "SELECT run_id FROM forecast_runs ORDER BY run_id")
    assert runs_after["run_id"].tolist() == runs_before["run_id"].tolist()
    err_after = db.read_df(lake_pipeline_db, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family")
    assert err_after.shape == err_before.shape
    assert err_after["mape"].fillna(-1).tolist() == err_before["mape"].fillna(-1).tolist()


@needs_lake
def test_as_of_defaults_to_lake_yaml_on_lake_db(lake_pipeline_db):
    import pandas as pd

    from restwert.lakegen.config import load_lake_config

    kv = db.read_df(lake_pipeline_db, "SELECT DISTINCT as_of FROM kpi_values")
    assert len(kv) == 1
    assert pd.Timestamp(kv["as_of"].iloc[0]).date() == load_lake_config().as_of
    gv = db.read_df(lake_pipeline_db, 'SELECT DISTINCT as_of FROM "gold"."kpi_values"')
    assert len(gv) == 1 and pd.Timestamp(gv["as_of"].iloc[0]).date() == load_lake_config().as_of


@needs_lake
def test_pipeline_cfg_is_lake_config_on_lake_db(lake_pipeline_db):
    import argparse

    from restwert.cli import _default_as_of, _pipeline_cfg
    from restwert.lakegen.config import LakeConfig, load_lake_config
    from restwert.paths import CONFIG_DIR

    cfg = _pipeline_cfg(lake_pipeline_db, argparse.Namespace(config=str(CONFIG_DIR / "generator.yaml")))
    assert isinstance(cfg, LakeConfig)
    assert set(cfg.families) == {"iphone_like", "android_like", "tablet_like", "laptop_like"}
    assert _default_as_of(lake_pipeline_db, CONFIG_DIR / "generator.yaml") == load_lake_config().as_of


@needs_lake
def test_docs_untouched_by_v2_commands_off_default_paths(lake_pipeline_paths, tmp_path):
    from restwert.paths import DOCS_DIR

    names = ("DATA_MODEL.md", "DATA_LAKE.md", "DECISION_RULES.md", "KPI_CATALOGUE.md", "GOLD_KPIS.md", "LEVERS.md")
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (DOCS_DIR / n for n in names) if p.exists()}
    assert main(["conform", "--db", str(lake_pipeline_paths.db)]) == 0
    assert main(["levers", "--db", str(lake_pipeline_paths.db)]) == 0
    assert main(["kpis", "--db", str(lake_pipeline_paths.db)]) == 0
    for p, (mtime, content) in before.items():
        assert p.stat().st_mtime_ns == mtime and p.read_bytes() == content, p.name


def test_resolve_lake_dir_rules(tmp_path):
    """``--lake-dir`` wins; the default csv dir maps to data/lake; another csv dir maps to its parent's lake/."""
    import argparse

    from restwert.cli import _resolve_lake_dir
    from restwert.paths import LAKE_DIR, RAW_CSV_DIR

    assert _resolve_lake_dir(argparse.Namespace(lake_dir=None, csv_dir=str(tmp_path / "x" / "raw_csv"))) == tmp_path / "x" / "lake"
    assert _resolve_lake_dir(argparse.Namespace(lake_dir=None, csv_dir=str(RAW_CSV_DIR))) == Path(LAKE_DIR)
    assert _resolve_lake_dir(argparse.Namespace(lake_dir=str(tmp_path / "lake"), csv_dir=str(RAW_CSV_DIR))) == tmp_path / "lake"


@needs_lake
def test_all_refuses_to_wipe_a_real_export(lake_pipeline_paths, tmp_path, capsys):
    """``all`` empties raw/ only when every landing file is generated; a real export blocks the run by name."""
    lake_dir = tmp_path / "lake"
    shutil.copytree(lake_pipeline_paths.lake_dir / "raw", lake_dir / "raw")
    real = lake_dir / "raw" / "wms" / "shipments" / "2026-05-31_shipments_009.csv"
    real.write_text("shipment_id,serial,is_synthetic\nSH-REAL,SN-1,false\n", encoding="utf-8")
    rc = main(["all", "--small", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out"), "--csv-dir", str(tmp_path / "csv"),
               "--lake-dir", str(lake_dir), "--no-docs"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "2026-05-31_shipments_009.csv" in err and "--wipe-raw" in err
    assert real.exists()


@needs_lake
def test_ingest_dry_run_on_missing_db_creates_no_file(lake_pipeline_paths, tmp_path, capsys):
    """A dry run must leave nothing behind, not even an empty schema file."""
    folder = lake_pipeline_paths.lake_dir / "raw" / "catalogue" / "models"
    landing = sorted(folder.glob("*.csv"))[0]
    target = tmp_path / "dry.duckdb"
    rc = main(["ingest", "--db", str(target), "--source", "catalogue/models", "--file", str(landing), "--dry-run"])
    assert rc == 0 and not target.exists()
    assert "in-memory" in capsys.readouterr().out


@needs_lake
def test_ingest_summary_carries_the_run_as_of(lake_pipeline_db):
    """gold.ingest_summary is stamped with the run's as_of like every other gold table, never with today."""
    stamps = db.read_df(lake_pipeline_db, "SELECT DISTINCT as_of FROM gold.ingest_summary")["as_of"]
    chain = db.read_df(lake_pipeline_db, "SELECT DISTINCT as_of FROM gold.chain_quality")["as_of"]
    assert len(stamps) == 1 and len(chain) == 1
    assert str(stamps.iloc[0])[:10] == str(chain.iloc[0])[:10] == "2026-09-13"
