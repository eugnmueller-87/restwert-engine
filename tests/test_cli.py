"""End-to-end tests of the CLI (SPEC section 8.6, module 6).

These run the whole pipeline through ``restwert.cli.main`` on 400 synthetic
devices (session fixture ``full_pipeline_db``) and check the acceptance
criteria of SPEC section 8: return code, runtime, every table present, every
derived table populated (except ``write_down_ledger``, which is only written
when an aging rule fires), exports and manifest, idempotent ``decide``, the
catalogue flag, the governance banner and the per-step timing lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from restwert import GOVERNANCE_PRINCIPLE, db, schema
from restwert.cli import ALL_ORDER, build_parser, main
from restwert.export import EXPORT_TABLES

# advisories fire only when a threshold is reached on a cohort of at least lever_reference_min_n serials
# (ADV02 calibration, ADV03 manufacturer mix, ADV04 term gap). With four contract terms (12/24/36/48)
# the 500-serial small fleet forms no (family, half-year) cohort with two terms at min_n, so the table
# is legitimately empty there; the hand-built fleets in test_levers.py assert the advisories themselves.
MAY_BE_EMPTY = {"write_down_ledger", "advisories"}


def _count(con, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


# --------------------------------------------------------------------------- all --small


def test_all_small_returns_zero_fast(full_pipeline_paths):
    assert full_pipeline_paths.return_code == 0
    assert full_pipeline_paths.seconds < 20, f"all --small took {full_pipeline_paths.seconds:.1f}s"


def test_every_table_exists(full_pipeline_db):
    missing = [t for t in schema.TABLE_ORDER if not db.table_exists(full_pipeline_db, t)]
    assert not missing, f"tables missing after all --small: {missing}"


def test_every_derived_table_has_rows(full_pipeline_db):
    empty = [
        t
        for t in schema.DERIVED_TABLES
        if t not in MAY_BE_EMPTY and _count(full_pipeline_db, t) == 0
    ]
    assert not empty, f"derived tables without rows: {empty}"


def test_source_tables_are_synthetic(full_pipeline_db):
    assert db.is_synthetic(full_pipeline_db) is True
    for t in schema.SOURCE_TABLES:
        n_false = int(
            full_pipeline_db.execute(f"SELECT count(*) FROM {t} WHERE NOT is_synthetic").fetchone()[0]
        )
        assert n_false == 0, f"{t} carries rows not marked synthetic"


def test_synthetic_md_written(full_pipeline_paths):
    candidates = [
        full_pipeline_paths.csv_dir / "SYNTHETIC.md",
        full_pipeline_paths.csv_dir.parent / "SYNTHETIC.md",
    ]
    assert any(p.exists() for p in candidates), "data/SYNTHETIC.md was not written next to the CSVs"


def test_export_files_and_manifest(full_pipeline_paths, full_pipeline_db):
    out = full_pipeline_paths.out
    for table in EXPORT_TABLES:
        assert (out / f"{table}.csv").exists(), f"missing {table}.csv"
        assert (out / f"{table}.parquet").exists(), f"missing {table}.parquet"
    manifest_path = out / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["is_synthetic"] is True
    assert manifest["governance"] == GOVERNANCE_PRINCIPLE
    assert manifest["missing_tables"] == []
    for table in EXPORT_TABLES:
        assert manifest["tables"][table] == _count(full_pipeline_db, table)


def test_decision_queue_has_owners(full_pipeline_db):
    q = db.read_df(full_pipeline_db, "SELECT * FROM decision_queue")
    assert len(q) > 0
    assert q["threshold_owner"].notna().all()
    assert (q["threshold_owner"].str.len() > 0).all()
    assert q["rule_id"].notna().all()


def test_decision_log_owner_and_hash(full_pipeline_db):
    log = db.read_df(
        full_pipeline_db,
        "SELECT rule_id, threshold_key, threshold_owner, input_hash, inputs_json FROM decision_log",
    )
    assert len(log) > 0
    assert log["threshold_owner"].notna().all()
    assert log["threshold_key"].notna().all()
    assert log["input_hash"].is_unique
    for raw in log["inputs_json"].head(20):
        json.loads(raw)


def test_rerun_decide_appends_zero_rows(full_pipeline_paths, full_pipeline_db):
    before = _count(full_pipeline_db, "decision_log")
    queue_before = _count(full_pipeline_db, "decision_queue")
    rc = main(["decide", "--db", str(full_pipeline_paths.db)])
    assert rc == 0
    after = _count(full_pipeline_db, "decision_log")
    assert after == before, f"decide appended {after - before} rows on rerun"
    assert _count(full_pipeline_db, "decision_queue") == queue_before


def test_forecast_runs_immutable_on_rerun(full_pipeline_paths, full_pipeline_db):
    runs_before = db.read_df(full_pipeline_db, "SELECT run_id, created_at FROM forecast_runs ORDER BY run_id")
    err_before = db.read_df(
        full_pipeline_db, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family"
    )
    rc = main(["forecast", "--db", str(full_pipeline_paths.db), "--no-backtest"])
    assert rc == 0
    runs_after = db.read_df(full_pipeline_db, "SELECT run_id, created_at FROM forecast_runs ORDER BY run_id")
    assert runs_after["run_id"].tolist() == runs_before["run_id"].tolist()
    err_after = db.read_df(
        full_pipeline_db, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family"
    )
    assert err_after.shape == err_before.shape
    assert err_after["mape"].fillna(-1).tolist() == err_before["mape"].fillna(-1).tolist()


def test_kpis_catalogue_flag_writes_file(capsys):
    from restwert.paths import DOCS_DIR

    rc = main(["kpis", "--catalogue"])
    assert rc == 0
    path = DOCS_DIR / "KPI_CATALOGUE.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "KPI_TOP_LIFECYCLE_MARGIN" in text
    assert "Lifecycle margin per device" in text
    out = capsys.readouterr().out
    assert GOVERNANCE_PRINCIPLE in out


def test_kpi_values_have_status_and_no_zero_for_not_measurable(full_pipeline_db):
    kv = db.read_df(full_pipeline_db, "SELECT kpi_id, value, status FROM kpi_values")
    assert len(kv) >= 18
    assert set(kv["status"]).issubset({"ok", "not_measurable"})
    top = kv[kv["kpi_id"] == "KPI_TOP_LIFECYCLE_MARGIN"]
    assert len(top) == 1


def test_export_command_alone(full_pipeline_paths, tmp_path):
    out = tmp_path / "exp"
    rc = main(["export", "--db", str(full_pipeline_paths.db), "--out", str(out), "--fmt", "csv"])
    assert rc == 0
    assert (out / "device_pnl.csv").exists()
    assert not (out / "device_pnl.parquet").exists()
    assert (out / "manifest.json").exists()


# --------------------------------------------------------------------------- CLI surface


def test_every_subcommand_exists_with_flags():
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
    commands = set(sub.choices)
    expected = {"generate", "load", "forecast", "pnl", "decide", "kpis", "contracts", "export", "all", "dashboard"}
    assert expected <= commands
    assert set(ALL_ORDER) == expected - {"dashboard", "all"}

    def flags(name: str) -> set[str]:
        return {o for a in sub.choices[name]._actions for o in a.option_strings}

    assert {"--seed", "--devices", "--out", "--config"} <= flags("generate")
    assert {"--csv-dir", "--db", "--allow-mixed", "--no-validate"} <= flags("load")
    assert {"--as-of", "--no-replay", "--no-backtest", "--db"} <= flags("forecast")
    assert {"--as-of", "--db"} <= flags("pnl")
    assert {"--as-of", "--db"} <= flags("decide")
    assert {"--as-of", "--db", "--catalogue"} <= flags("kpis")
    assert {"--as-of", "--db"} <= flags("contracts")
    assert {"--db", "--out", "--fmt"} <= flags("export")
    assert {"--seed", "--devices", "--as-of", "--db", "--out", "--small"} <= flags("all")
    assert {"--db"} <= flags("dashboard")


def test_banner_and_step_timing_printed(full_pipeline_paths, capsys):
    rc = main(["contracts", "--db", str(full_pipeline_paths.db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count(GOVERNANCE_PRINCIPLE) == 1
    assert "contracts ..." in out and "s " in out


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code != 0


def test_failure_returns_one(tmp_path, capsys):
    """A CSV without the mandatory is_synthetic column must fail the load with return code 1."""
    csv_dir = tmp_path / "bad_csv"
    csv_dir.mkdir()
    (csv_dir / "devices.csv").write_text(
        "serial,model_family,model,storage_gb,colour,launch_date,purchase_date,purchase_price,"
        "landed_cost,supplier,channel_in,po_number,contract_id\n"
        "D-000001,iphone_like,P-Gen01,128,black,2019-09-15,2022-01-10,800,828,Supplier-A,distributor,PO-000001,\n",
        encoding="utf-8",
    )
    rc = main(["load", "--csv-dir", str(csv_dir), "--db", str(tmp_path / "x.duckdb")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err


def test_as_of_default_is_generator_as_of_on_synthetic(full_pipeline_db, small_cfg):
    import pandas as pd

    kv = db.read_df(full_pipeline_db, "SELECT DISTINCT as_of FROM kpi_values")
    assert len(kv) == 1
    assert pd.Timestamp(kv["as_of"].iloc[0]).date() == small_cfg.as_of


def test_dashboard_renders_headless(full_pipeline_paths):
    """Run the Streamlit app headless against the pipeline DB: no exception, banner, tiles, tabs."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = Path(__file__).resolve().parents[1] / "restwert" / "dashboard" / "app.py"
    at = AppTest.from_file(str(app), default_timeout=120)
    at.session_state["db_path"] = str(full_pipeline_paths.db)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert any("SYNTHETIC" in str(w.value) for w in at.warning), "synthetic banner missing"
    labels = [m.label for m in at.metric]
    assert "Lifecycle margin per device" in labels
    assert "Residual value forecast error" in labels
    page_text = " ".join(str(getattr(el, "value", "")) for el in at.main)
    assert GOVERNANCE_PRINCIPLE in page_text or any(GOVERNANCE_PRINCIPLE in str(c.value) for c in at.caption)
    assert not at.error, [str(e.value) for e in at.error]


def test_pyproject_exposes_script():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'restwert = "restwert.cli:main"' in text
    assert 'name = "restwert"' in text
    req = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    pinned = [ln for ln in req.splitlines() if ln and not ln.startswith("#")]
    assert pinned and all("==" in ln for ln in pinned)


def test_docs_untouched_when_running_off_the_default_paths(full_pipeline_paths, tmp_path):
    """A pipeline run into another directory must not rewrite the tracked docs/*.md files."""
    from restwert.paths import DOCS_DIR

    targets = [DOCS_DIR / n for n in ("DATA_MODEL.md", "DECISION_RULES.md", "KPI_CATALOGUE.md")]
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in targets if p.exists()}
    assert main(["kpis", "--db", str(full_pipeline_paths.db)]) == 0
    assert main(["decide", "--db", str(full_pipeline_paths.db)]) == 0
    assert main(["load", "--csv-dir", str(full_pipeline_paths.csv_dir), "--db", str(tmp_path / "scratch.duckdb")]) == 0
    for p, (mtime, content) in before.items():
        assert p.stat().st_mtime_ns == mtime and p.read_bytes() == content, p.name
    # --docs forces the regeneration into a chosen path only for the catalogue-style writers;
    # the parser exposes both flags on the commands that write docs
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
    for name in ("load", "decide", "kpis", "all"):
        opts = {o for a in sub.choices[name]._actions for o in a.option_strings}
        assert {"--docs", "--no-docs"} <= opts, name


def test_docs_wanted_logic():
    import argparse

    from restwert.cli import _docs_wanted
    from restwert.paths import DEFAULT_DB, OUTPUTS_DIR

    assert _docs_wanted(argparse.Namespace(command="kpis", db=str(DEFAULT_DB), docs=None)) is True
    assert _docs_wanted(argparse.Namespace(command="kpis", db="C:/tmp/x.duckdb", docs=None)) is False
    assert _docs_wanted(argparse.Namespace(command="kpis", db="C:/tmp/x.duckdb", docs=True)) is True
    assert _docs_wanted(argparse.Namespace(command="all", db=str(DEFAULT_DB), out="C:/tmp/out", docs=None)) is False
    assert _docs_wanted(argparse.Namespace(command="all", db=str(DEFAULT_DB), out=str(OUTPUTS_DIR), docs=None)) is True
    assert _docs_wanted(argparse.Namespace(command="all", db=str(DEFAULT_DB), out=str(OUTPUTS_DIR), docs=False)) is False


def test_tco_rows_are_not_clipped_on_the_shipped_grid(full_pipeline_db):
    tco = db.read_df(full_pipeline_db, "SELECT rv_clipped_to_grid, forecast_rv_source, inputs_source FROM tco_per_model")
    assert len(tco) > 0
    assert not tco["rv_clipped_to_grid"].astype(bool).any(), "the shipped grid (registry.GRID_MONTHS) must cover every term of 12 to 48 months"
    assert set(tco["forecast_rv_source"]) <= {"grid", "grid_clipped", "planned_ratio_on_landed_cost"}


def test_grade_d_forecasts_use_the_as_is_value(full_pipeline_db):
    cur = db.read_df(
        full_pipeline_db,
        "SELECT forecast_rv, forecast_rv_as_is, fit_quality FROM rv_forecast_current WHERE grade_used = 'D'",
    )
    if cur.empty:
        pytest.skip("no grade-D device in the small fleet")
    assert (cur["forecast_rv"].astype(float) == cur["forecast_rv_as_is"].astype(float)).all()
    assert set(cur["fit_quality"]) == {"unsupported_grade"}
    log = db.read_df(full_pipeline_db, "SELECT DISTINCT threshold_owner FROM decision_log")
    assert any(o.startswith("Head of Customer Success") for o in log["threshold_owner"])
