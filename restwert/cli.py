"""Command line interface for the Restwert Engine (SPEC 8.1 and SPEC_v0.2 section 9.1).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every subcommand is a function ``cmd_<name>(args) -> int`` that calls the module
entry points listed here and nothing else:

============= ================================================================
command       entry point
============= ================================================================
generate      ``restwert.generate.run_generate`` (v0.1 synthetic CSVs)
load          ``restwert.db.create_schema`` + ``restwert.db.load_csv_dir``
generate-lake ``restwert.lakegen.run_generate_lake`` (v0.2 landing files)
ingest        ``restwert.lake.ingest.run_ingest`` (landing files -> bronze)
conform       ``restwert.lake.conform.run_conform`` (bronze -> the ten v0.1 tables)
forecast      ``restwert.forecast.run.run_forecast``
pnl           ``restwert.pnl.lifecycle.run_pnl``
timeline      ``restwert.lake.timeline.run_timeline``
ledger        ``restwert.ledger.run.run_ledger``
levers        ``restwert.levers.run.run_levers``
decide        ``restwert.decisions.runner.run_all_decisions``
kpis          ``restwert.kpi.compute.run_kpis`` then ``restwert.gold.run.run_gold_kpis``
contracts     ``restwert.contracts.register.run_contracts`` then ``register_v2.run_contracts_v2``
export        ``restwert.export.export_all`` (+ ``export_lake`` with ``--lake``) + ``write_manifest``
all           the v0.2 chain ``ALL_ORDER_V2`` (``--v01``: the v0.1 chain ``ALL_ORDER``)
market        ``restwert.market.run.run_market``
dashboard     starts Streamlit on ``restwert/dashboard/app.py``
============= ================================================================

Choices where the spec is silent (documented here on purpose):

* ``all`` deletes an existing DuckDB file before it starts unless ``--keep-db``
  is given. The immutable tables (``forecast_runs``, ``decision_log``,
  ``write_down_ledger``) would otherwise carry state from an older seed. The
  v0.2 chain also empties ``<lake-dir>/raw`` first for the same reason: a
  landing file is never modified after landing, but a from-scratch run must not
  ingest the landing files of an older seed or fleet size next to the new ones.
  Only GENERATED landing files are removed (first line ``# SYNTHETIC DATA`` or
  ``# PUBLIC DATA``); a real export in the landing layer blocks the wipe and the
  run says which file, unless ``--wipe-raw`` is given (``_wipe_raw_layer``).
* ``--csv-dir`` keeps its v0.1 meaning on the v0.2 chain: the conform step writes
  the ten conformed tables as ``<csv_dir>/<table>.csv`` so ``load --csv-dir``
  and the v0.1 path keep working (decision D18).
* ``dashboard`` uses ``subprocess.call`` with the current interpreter
  (``python -m streamlit run ...``) instead of ``os.execvp``: on Windows
  ``execvp`` detaches the console and the exit code is lost. This is the only
  process spawn in the package and it starts nothing but Streamlit.
* Generated docs (``docs/DATA_MODEL.md``, ``docs/DATA_LAKE.md``,
  ``docs/DECISION_RULES.md``, ``docs/KPI_CATALOGUE.md``, ``docs/GOLD_KPIS.md``,
  ``docs/LEVERS.md``) are rewritten ONLY when the command runs on the default
  database (and, for ``all``, the default output directory), or when ``--docs``
  is given; ``--no-docs`` always suppresses them. A pipeline run into a scratch
  directory (tests, experiments) therefore never touches the repo.
* The v0.2 packages are imported lazily inside each command. When the lake
  packages are not importable at all (a checkout with only the v0.1 engine),
  ``all`` says so on stdout and runs the v0.1 chain instead of failing; a step
  whose optional package is missing (timeline, ledger, levers, contracts v2,
  gold KPIs) is reported as skipped with the reason. Nothing is guessed and
  nothing is silent.

The module imports the other packages lazily inside each ``cmd_*`` function so
that ``python -m restwert --help`` and the dashboard work even when one
pipeline module is missing.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from restwert import GOVERNANCE_PRINCIPLE, __version__
from restwert.paths import CONFIG_DIR, DATA_DIR, DEFAULT_DB, DOCS_DIR, OUTPUTS_DIR, RAW_CSV_DIR, ROOT

BANNER = f"Restwert Engine v{__version__}  |  {GOVERNANCE_PRINCIPLE}"
SMALL_DEVICES = 400
SMALL_SERIALS = 500
ALL_ORDER: tuple[str, ...] = (
    "generate",
    "load",
    "forecast",
    "pnl",
    "decide",
    "kpis",
    "contracts",
    "export",
)
ALL_ORDER_V2: tuple[str, ...] = (
    "generate-lake",
    "ingest",
    "conform",
    "forecast",
    "pnl",
    "timeline",
    "ledger",
    "levers",
    "decide",
    "contracts",
    "kpis",
    "export",
)
CADENCES: tuple[str, ...] = ("yearly", "quarterly", "monthly")

# v0.2 lake paths: taken from restwert.paths when module 1 has landed, derived here otherwise
LAKE_DIR: Path = DATA_DIR / "lake"
CATALOGUE_DIR: Path = DATA_DIR / "catalogue"
MARKET_CURVES_CSV: Path = OUTPUTS_DIR / "market_curves.csv"
LAKE_CONFIG: Path = CONFIG_DIR / "lake.yaml"
try:  # pragma: no cover - the values are identical by spec 3.1
    from restwert.paths import LAKE_CONFIG as _LC, LAKE_DIR as _LD, CATALOGUE_DIR as _CD, MARKET_CURVES_CSV as _MC

    LAKE_DIR, CATALOGUE_DIR, MARKET_CURVES_CSV, LAKE_CONFIG = _LD, _CD, _MC, _LC
except ImportError:
    pass


# --------------------------------------------------------------------------- helpers


@dataclass
class StepResult:
    """What one pipeline step reports back to the caller."""

    step: str
    seconds: float
    counts: dict[str, int]
    run_id: str | None = None


def _fmt_counts(counts: dict[str, Any] | None) -> str:
    if not counts:
        return ""
    return " ".join(f"{k}={v}" for k, v in counts.items())


def _print_step(step: str, seconds: float, counts: dict[str, Any] | None) -> None:
    print(f"{step} ... {seconds:.2f}s  {_fmt_counts(counts)}".rstrip())


def _summary_counts(result: Any) -> tuple[dict[str, Any], str | None]:
    """Counts and run id of whatever an entry point returned (RunSummary, dict, tuple, other)."""
    counts: dict[str, Any] = {}
    run_id: str | None = None
    summary = result
    if isinstance(result, tuple) and result:
        summary = result[-1]
    if hasattr(summary, "counts"):
        counts = dict(getattr(summary, "counts") or {})
        run_id = getattr(summary, "run_id", None)
    elif isinstance(summary, dict):
        counts = {k: v for k, v in summary.items()}
    return counts, run_id


def _timed(step: str, fn: Callable[[], Any]) -> StepResult:
    """Run ``fn``, print ``<step> ... <seconds>s  <counts>`` and return a StepResult.

    ``fn`` may return a RunSummary, a dict of counts, a tuple whose last element
    is a RunSummary, or anything else (counts empty).
    """
    t0 = time.perf_counter()
    result = fn()
    seconds = time.perf_counter() - t0
    counts, run_id = _summary_counts(result)
    _print_step(step, seconds, counts)
    return StepResult(step=step, seconds=seconds, counts=counts, run_id=run_id)


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {text!r}") from exc


def _open_db(path: str | Path):
    """Connect to the DuckDB file, creating parent directories first."""
    from restwert import db

    p = Path(path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    return db.connect(p)


def _table_exists_q(con, table: str) -> bool:
    """``table_exists`` for a bare or schema-qualified name, independent of the db.py version."""
    if "." in table:
        schema_name, name = table.split(".", 1)
    else:
        schema_name, name = "main", table
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema_name, name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _is_lake_db(con) -> bool:
    """True when the database was built from the data lake (``bronze.cat_models`` exists)."""
    try:
        return _table_exists_q(con, "bronze.cat_models")
    except Exception:  # noqa: BLE001 - a closed or foreign connection is simply not a lake DB
        return False


def _lake_core_missing() -> str | None:
    """Name of the first v0.2 core package that cannot be imported, else None.

    The core of the v0.2 chain is generate-lake, ingest and conform; without them
    ``all`` runs the v0.1 chain and says so.
    """
    import importlib

    for name in ("restwert.lakegen", "restwert.lake.ingest", "restwert.lake.conform"):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            return f"{name} ({exc})"
    return None


def _lake_config_path(args: argparse.Namespace) -> Path:
    """``--config`` when it points at a lake.yaml, else the default ``config/lake.yaml``."""
    cfg = getattr(args, "config", None)
    if cfg and str(cfg).replace("\\", "/").endswith("lake.yaml"):
        return Path(cfg)
    return Path(LAKE_CONFIG)


def _load_lake_config(path: Path):
    from restwert.lakegen.config import load_lake_config

    return load_lake_config(Path(path))


def _default_as_of(con, config_path: Path) -> date:
    """``as_of`` of the generator config when the DB is synthetic, else today (SPEC 8.1).

    On a lake database (``bronze.cat_models`` exists) the ``as_of`` comes from
    ``config/lake.yaml`` (or the ``--config`` when that names a lake.yaml).
    """
    from restwert import db
    from restwert.config import load_generator_config

    try:
        synthetic = db.is_synthetic(con)
    except Exception:  # empty or unloaded DB: no source rows, so not synthetic
        synthetic = False
    if _is_lake_db(con):
        try:
            path = Path(config_path)
            if not str(path).replace("\\", "/").endswith("lake.yaml"):
                path = Path(LAKE_CONFIG)
            return _load_lake_config(path).as_of
        except (ImportError, FileNotFoundError, ValueError) as exc:
            print(f"  note: lake.yaml as_of not readable ({type(exc).__name__}: {exc}); using the v0.1 rule")
    if synthetic:
        return load_generator_config(Path(config_path)).as_of
    return date.today()


def _resolve_as_of(args: argparse.Namespace, con) -> date:
    if getattr(args, "as_of", None):
        return args.as_of
    return _default_as_of(con, Path(getattr(args, "config", CONFIG_DIR / "generator.yaml")))


def _pipeline_cfg(con, args: argparse.Namespace):
    """The ``cfg`` handed to ``run_forecast``: the lake config (four families) on a lake DB.

    On a v0.1 database the generator config from ``--config`` is returned unchanged.
    """
    from restwert.config import load_generator_config

    if _is_lake_db(con):
        try:
            return _load_lake_config(_lake_config_path(args))
        except (ImportError, FileNotFoundError, ValueError) as exc:
            print(f"  note: lake config not loaded ({type(exc).__name__}: {exc}); using generator.yaml")
    return load_generator_config(Path(getattr(args, "config", CONFIG_DIR / "generator.yaml")))


def _docs_wanted(args: argparse.Namespace) -> bool:
    """Whether this command may rewrite the generated docs in ``docs/``.

    Explicit ``--docs`` / ``--no-docs`` win; otherwise only a run on the default database
    (and the default output directory for ``all`` / ``export``) regenerates them.
    """
    explicit = getattr(args, "docs", None)
    if explicit is not None:
        return bool(explicit)
    try:
        db_default = Path(getattr(args, "db", DEFAULT_DB)).resolve() == Path(DEFAULT_DB).resolve()
    except OSError:
        db_default = False
    out = getattr(args, "out", None)
    out_default = True
    if out is not None and getattr(args, "command", "") == "all":
        try:
            out_default = Path(out).resolve() == Path(OUTPUTS_DIR).resolve()
        except OSError:
            out_default = False
    return db_default and out_default


def _add_docs_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--docs", dest="docs", action="store_true", default=None, help="regenerate docs/*.md even off the default paths")
    g.add_argument("--no-docs", dest="docs", action="store_false", help="never regenerate docs/*.md")


def _latest_run_id(con) -> str | None:
    from restwert import db

    if not db.table_exists(con, "runs"):
        return None
    row = con.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _print_summary_table(results: list[StepResult], total: float) -> None:
    print()
    print(f"{'step':<14} {'seconds':>8}  {'run_id':<28} counts")
    print("-" * 82)
    for r in results:
        print(f"{r.step:<14} {r.seconds:>8.2f}  {(r.run_id or '-'):<28} {_fmt_counts(r.counts)}")
    print("-" * 82)
    print(f"{'total':<14} {total:>8.2f}")


def _note(text: str) -> None:
    print(f"  note: {text}")


def _render_doc(label: str, fn: Callable[[], Any]) -> None:
    """Render one generated document; a doc render never fails the pipeline."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        _note(f"{label} not rendered ({type(exc).__name__}: {exc})")


def _write_levers_md(thr) -> Path:
    from restwert.levers.summary import render_levers_md

    path = DOCS_DIR / "LEVERS.md"
    path.write_text(render_levers_md(thr), encoding="utf-8", newline="\n")
    return path


def _resolve_lake_dir(args: argparse.Namespace) -> Path:
    """``--lake-dir`` when given; ``data/lake`` when ``--csv-dir`` is the default; else ``<csv_dir>.parent/lake``."""
    explicit = getattr(args, "lake_dir", None)
    if explicit:
        return Path(explicit)
    csv_dir = getattr(args, "csv_dir", None)
    if csv_dir is None:
        return Path(LAKE_DIR)
    try:
        if Path(csv_dir).resolve() == Path(RAW_CSV_DIR).resolve():
            return Path(LAKE_DIR)
    except OSError:
        pass
    return Path(csv_dir).parent / "lake"


def _copy_synthetic_md(lake_dir: Path, target: Path, seed: int, n: int) -> None:
    """Write ``<csv_dir>.parent/SYNTHETIC.md`` with the lake SYNTHETIC.md text (v0.1 location)."""
    source = Path(lake_dir) / "SYNTHETIC.md"
    if source.exists():
        text = source.read_text(encoding="utf-8")
    else:
        text = (
            "# SYNTHETIC DATA\n\n"
            f"Generated by `python -m restwert all` (seed {seed}, {n} serials) from the data lake under `{lake_dir}`. "
            "Every number is a synthetic design parameter from config/lake.yaml; the catalogue and the anchor curves "
            "are public; no market benchmark, no customer, no supplier beyond the public manufacturer names, no employer is real.\n"
        )
        _note(f"{source} not found; a short SYNTHETIC.md was written instead")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- v0.1 commands


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate the synthetic source CSVs (entry point ``generate.run_generate``)."""
    from restwert.config import load_generator_config
    from restwert.generate import run_generate

    cfg = load_generator_config(Path(args.config))
    seed = args.seed if args.seed is not None else cfg.seed
    n_devices = args.devices if args.devices is not None else cfg.n_devices
    out_dir = Path(args.out)
    _timed("generate", lambda: run_generate(cfg, seed, n_devices, out_dir))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Create the schema and load ``<csv_dir>/<table>.csv`` (entry point ``db.load_csv_dir``)."""
    from restwert import db

    con = _open_db(args.db)
    docs = _docs_wanted(args)
    try:

        def _load() -> dict[str, int]:
            db.create_schema(con)
            counts = db.load_csv_dir(
                con,
                Path(args.csv_dir),
                validate=not args.no_validate,
                allow_mixed=args.allow_mixed,
            )
            if docs:
                from restwert.schema import write_data_model_md

                write_data_model_md()
            return counts

        _timed("load", _load)
    finally:
        con.close()
    return 0


def cmd_forecast(args: argparse.Namespace) -> int:
    """Fit the residual value model, replay month ends, backtest (entry point ``forecast.run.run_forecast``)."""
    from restwert.config import load_assumptions, load_thresholds
    from restwert.forecast.run import run_forecast

    con = _open_db(args.db)
    try:
        as_of = _resolve_as_of(args, con)
        cfg = _pipeline_cfg(con, args)
        a = load_assumptions()
        thr = load_thresholds()
        _timed(
            "forecast",
            lambda: run_forecast(
                con, as_of, a, thr, cfg, replay=not args.no_replay, backtest=not args.no_backtest
            ),
        )
    finally:
        con.close()
    return 0


def cmd_pnl(args: argparse.Namespace) -> int:
    """Build device_pnl, tco_per_model and pnl_aggregate (entry point ``pnl.lifecycle.run_pnl``)."""
    from restwert.config import load_assumptions
    from restwert.pnl.lifecycle import run_pnl

    con = _open_db(args.db)
    try:
        as_of = _resolve_as_of(args, con)
        a = load_assumptions()
        _timed("pnl", lambda: run_pnl(con, as_of, a))
    finally:
        con.close()
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """Run rules R01..R07, append decision_log, rebuild the queue (entry point ``decisions.runner.run_all_decisions``)."""
    from restwert.config import load_assumptions, load_thresholds
    from restwert.decisions.runner import run_all_decisions

    con = _open_db(args.db)
    docs = _docs_wanted(args)
    try:
        as_of = _resolve_as_of(args, con)
        thr = load_thresholds()
        a = load_assumptions()
        _timed("decide", lambda: run_all_decisions(con, as_of, thr, a, write_docs=docs))
    finally:
        con.close()
    return 0


def _run_kpis_v1_and_gold(con, as_of: date, targets, docs: bool) -> dict[str, Any]:
    """v0.1 ``run_kpis`` then the gold registry when ``silver.device_ledger`` exists."""
    from restwert.kpi.compute import run_kpis

    counts, _ = _summary_counts(run_kpis(con, as_of, targets, write_catalogue_md=docs))
    if not _table_exists_q(con, "silver.device_ledger"):
        _note("gold KPIs skipped: silver.device_ledger not found (run ledger first)")
        return counts
    try:
        from restwert.gold.run import run_gold_kpis
    except ModuleNotFoundError as exc:
        _note(f"gold KPIs skipped: {exc}")
        return counts
    if _accepts(run_gold_kpis, "write_catalogue_md"):
        gold_counts, _ = _summary_counts(run_gold_kpis(con, as_of, targets, write_catalogue_md=docs))
    else:
        gold_counts, _ = _summary_counts(run_gold_kpis(con, as_of, targets))
        if docs:

            def _gold_doc() -> None:
                from restwert.gold.catalogue import write_gold_catalogue

                write_gold_catalogue()

            _render_doc("GOLD_KPIS.md", _gold_doc)
    counts.update({f"gold_{k}": v for k, v in gold_counts.items()})
    return counts


def cmd_kpis(args: argparse.Namespace) -> int:
    """Compute the KPI tree (entry point ``kpi.compute.run_kpis``), then the gold KPIs.

    With ``--catalogue`` only ``docs/KPI_CATALOGUE.md`` (and ``docs/GOLD_KPIS.md``
    when the gold package is present) is rendered from code and nothing is computed.
    """
    if args.catalogue:
        from restwert.kpi.catalogue import write_catalogue

        def _cat() -> dict[str, Any]:
            path = write_catalogue()
            out = {"catalogue": str(path)}
            try:
                from restwert.gold.catalogue import write_gold_catalogue

                out["gold_catalogue"] = str(write_gold_catalogue())
            except ModuleNotFoundError as exc:
                _note(f"GOLD_KPIS.md not rendered: {exc}")
            return out

        _timed("kpis --catalogue", _cat)
        return 0

    from restwert.config import load_kpi_targets

    con = _open_db(args.db)
    docs = _docs_wanted(args)
    try:
        as_of = _resolve_as_of(args, con)
        targets = load_kpi_targets()
        _timed("kpis", lambda: _run_kpis_v1_and_gold(con, as_of, targets, docs))
    finally:
        con.close()
    return 0


def _run_contracts_v1_and_v2(con, as_of: date, thr) -> dict[str, Any]:
    """v0.1 ``run_contracts`` then register v2 when ``bronze.ctr_register`` exists."""
    from restwert.contracts.register import run_contracts

    counts, _ = _summary_counts(run_contracts(con, as_of, thr))
    if not _table_exists_q(con, "bronze.ctr_register"):
        _note("contracts v2 skipped: bronze.ctr_register not found (v0.1 register only)")
        return counts
    try:
        from restwert.contracts.register_v2 import run_contracts_v2
    except ModuleNotFoundError as exc:
        _note(f"contracts v2 skipped: {exc}")
        return counts
    v2_counts, _ = _summary_counts(run_contracts_v2(con, as_of, thr))
    counts.update({f"v2_{k}": v for k, v in v2_counts.items()})
    return counts


def cmd_contracts(args: argparse.Namespace) -> int:
    """Build the contracts register and renewal calendar (v0.1), then register v2 on a lake DB."""
    from restwert.config import load_thresholds

    con = _open_db(args.db)
    try:
        as_of = _resolve_as_of(args, con)
        thr = load_thresholds()
        _timed("contracts", lambda: _run_contracts_v1_and_v2(con, as_of, thr))
    finally:
        con.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write CSV/parquet plus manifest.json to ``--out`` (entry points ``export.export_all`` and ``export_lake``)."""
    from restwert.export import export_all, export_lake, write_manifest

    con = _open_db(args.db)
    try:
        out_dir = Path(args.out)
        lake_dir = Path(args.lake_dir) if getattr(args, "lake_dir", None) else Path(LAKE_DIR)

        def _export() -> dict[str, Any]:
            paths = export_all(con, out_dir, args.fmt)
            n_lake = 0
            if getattr(args, "lake", False):
                n_lake = len(export_lake(con, out_dir, lake_dir, args.fmt))
            write_manifest(out_dir, con, _latest_run_id(con))
            out = {"files": len(paths) + 1}
            if getattr(args, "lake", False):
                out["lake_files"] = n_lake
            return out

        _timed("export", _export)
    finally:
        con.close()
    return 0


# --------------------------------------------------------------------------- v0.2 commands


def cmd_generate_lake(args: argparse.Namespace) -> int:
    """Generate the landing files of every feed (entry point ``lakegen.run_generate_lake``)."""
    from restwert.lakegen import run_generate_lake

    cfg = _load_lake_config(Path(args.config))
    if getattr(args, "cadence", None):
        cfg = cfg.model_copy(update={"delivery_cadence": args.cadence})
    seed = args.seed if args.seed is not None else cfg.seed
    n = args.serials if args.serials is not None else cfg.n_devices
    lake_dir = Path(args.lake_dir)
    raw_dir = lake_dir / "raw"
    catalogue_dir = Path(args.catalogue_dir)
    curves = Path(args.curves)
    _timed("generate-lake", lambda: run_generate_lake(cfg, seed, n, raw_dir, catalogue_dir, curves, wipe_raw=bool(getattr(args, "wipe_raw", False))))
    md = lake_dir / "SYNTHETIC.md"
    if not md.exists():
        _note(f"{md} was not written by the generator")
    print(f"seed={seed} serials={n} lake={lake_dir} cadence={cfg.delivery_cadence}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Load landing files into bronze (entry point ``lake.ingest.run_ingest``).

    ``--source <feed key> --file <path>`` ingests one file, ``--all`` walks
    ``<lake-dir>/raw`` in feed order. ``--dry-run`` reports counts and writes
    nothing: on an existing database the transaction is rolled back (the DDL of
    a missing lake table may still be created, empty); on a missing database
    file the dry run works on an in-memory schema so no file appears. A refused
    file (unknown feed key, missing ``is_synthetic``) raises, which ``main``
    turns into exit code 1. ``--as-of`` (default: lake.yaml on a lake DB) is
    stamped on ``gold.ingest_summary`` and the ``runs`` row.
    """
    from restwert import db
    from restwert.lake.ingest import run_ingest

    if args.all and (args.source or args.file):
        raise ValueError("ingest takes either --all or --source/--file, not both")
    if not args.all and not (args.source and args.file):
        raise ValueError("ingest needs either --all or both --source <feed key> and --file <path>")
    raw_dir = Path(args.lake_dir) / "raw"
    db_target = str(args.db)
    if args.dry_run and db_target != ":memory:" and not Path(db_target).exists():
        # a dry run writes nothing, so it must not leave an empty schema file behind either
        _note(f"database {db_target} does not exist; the dry run uses an in-memory schema and creates no file")
        db_target = ":memory:"
    con = _open_db(db_target)
    try:
        db.create_schema(con)
        feed_key = None if args.all else args.source
        path = None if args.all else Path(args.file)
        if not args.all and not path.exists():
            raise FileNotFoundError(f"landing file not found: {path}")
        step = "ingest --dry-run" if args.dry_run else "ingest"
        as_of = _resolve_as_of(args, con)
        _timed(step, lambda: run_ingest(con, raw_dir, feed_key, path, bool(args.dry_run), as_of=as_of))
    finally:
        con.close()
    return 0


def _run_conform(con, as_of: date, a, csv_dir: Path | None, seed: int | None, docs: bool) -> Any:
    from restwert.lake.conform import run_conform

    summary = run_conform(con, as_of, a, csv_dir=csv_dir, seed=seed)
    if docs:

        def _lake_doc() -> None:
            from restwert.lake.feeds import write_data_lake_md

            write_data_lake_md()

        def _model_doc() -> None:
            from restwert.schema import write_data_model_md

            write_data_model_md()

        _render_doc("DATA_LAKE.md", _lake_doc)
        _render_doc("DATA_MODEL.md", _model_doc)
    return summary


def cmd_conform(args: argparse.Namespace) -> int:
    """Materialise the ten v0.1 source tables from bronze (entry point ``lake.conform.run_conform``)."""
    from restwert.config import load_assumptions

    con = _open_db(args.db)
    docs = _docs_wanted(args)
    try:
        as_of = _resolve_as_of(args, con)
        a = load_assumptions()
        csv_dir = Path(args.csv_dir) if args.csv_dir else None
        seed = args.seed
        if seed is None:
            try:
                seed = _load_lake_config(_lake_config_path(args)).seed
            except Exception:  # noqa: BLE001 - the seed only labels the compat CSV header
                seed = None
        _timed("conform", lambda: _run_conform(con, as_of, a, csv_dir, seed, docs))
    finally:
        con.close()
    return 0


def cmd_timeline(args: argparse.Namespace) -> int:
    """Build ``silver.serial_timeline`` and ``gold.chain_quality`` (entry point ``lake.timeline.run_timeline``)."""
    from restwert.lake.timeline import run_timeline

    con = _open_db(args.db)
    try:
        as_of = _resolve_as_of(args, con)
        _timed("timeline", lambda: run_timeline(con, as_of))
    finally:
        con.close()
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    """Build the silver ledger, device ledger, reconciliation and cohorts (entry point ``ledger.run.run_ledger``)."""
    from restwert.config import load_assumptions
    from restwert.ledger.run import run_ledger

    con = _open_db(args.db)
    try:
        as_of = _resolve_as_of(args, con)
        a = load_assumptions()
        _timed("ledger", lambda: run_ledger(con, as_of, a))
    finally:
        con.close()
    return 0


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    """Whether ``fn`` takes a keyword argument ``name`` (entry points may carry a docs switch)."""
    import inspect

    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _run_levers(con, as_of: date, thr, a, docs: bool) -> Any:
    from restwert.levers.run import run_levers

    if _accepts(run_levers, "write_docs"):
        return run_levers(con, as_of, thr, a, write_docs=docs)
    summary = run_levers(con, as_of, thr, a)
    if docs:
        _render_doc("LEVERS.md", lambda: _write_levers_md(thr))
    return summary


def cmd_levers(args: argparse.Namespace) -> int:
    """Attribute the result gap to the seven levers (entry point ``levers.run.run_levers``)."""
    from restwert.config import load_assumptions, load_thresholds

    con = _open_db(args.db)
    docs = _docs_wanted(args)
    try:
        as_of = _resolve_as_of(args, con)
        thr = load_thresholds()
        a = load_assumptions()
        _timed("levers", lambda: _run_levers(con, as_of, thr, a, docs))
    finally:
        con.close()
    return 0


# --------------------------------------------------------------------------- all


def _cmd_all_v01(args: argparse.Namespace) -> int:
    """The v0.1 chain: generate -> load -> forecast -> pnl -> decide -> kpis -> contracts -> export."""
    from restwert import db
    from restwert.config import (
        load_assumptions,
        load_generator_config,
        load_kpi_targets,
        load_thresholds,
    )
    from restwert.contracts.register import run_contracts
    from restwert.decisions.runner import run_all_decisions
    from restwert.export import export_all, write_manifest
    from restwert.forecast.run import run_forecast
    from restwert.generate import run_generate
    from restwert.kpi.compute import run_kpis
    from restwert.pnl.lifecycle import run_pnl

    t_total = time.perf_counter()
    cfg = load_generator_config(Path(args.config))
    seed = args.seed if args.seed is not None else cfg.seed
    if args.small:
        n_devices = SMALL_DEVICES
    else:
        n_devices = args.devices if args.devices is not None else cfg.n_devices
    as_of: date = args.as_of if args.as_of else cfg.as_of
    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out)
    db_path = Path(args.db)
    docs = _docs_wanted(args)

    if str(db_path) != ":memory:" and db_path.exists() and not args.keep_db:
        db_path.unlink()
        wal = db_path.with_suffix(db_path.suffix + ".wal")
        if wal.exists():
            wal.unlink()

    a = load_assumptions()
    thr = load_thresholds()
    targets = load_kpi_targets()

    results: list[StepResult] = []
    results.append(_timed("generate", lambda: run_generate(cfg, seed, n_devices, csv_dir)))

    con = _open_db(db_path)
    try:

        def _load() -> dict[str, int]:
            db.create_schema(con)
            counts = db.load_csv_dir(con, csv_dir)
            # definition of done (SPEC 11): `all` on the default paths leaves docs/DATA_MODEL.md
            # rendered from the DDL; off the default paths the repo is left alone
            if docs:
                try:
                    from restwert.schema import write_data_model_md

                    write_data_model_md()
                except Exception as exc:  # noqa: BLE001 - a doc render never fails the pipeline
                    print(f"  note: DATA_MODEL.md not rendered ({type(exc).__name__}: {exc})")
            return counts

        results.append(_timed("load", _load))
        results.append(
            _timed("forecast", lambda: run_forecast(con, as_of, a, thr, cfg, replay=True, backtest=True))
        )
        results.append(_timed("pnl", lambda: run_pnl(con, as_of, a)))
        results.append(_timed("decide", lambda: run_all_decisions(con, as_of, thr, a, write_docs=docs)))
        results.append(_timed("kpis", lambda: run_kpis(con, as_of, targets, write_catalogue_md=docs)))
        results.append(_timed("contracts", lambda: run_contracts(con, as_of, thr)))

        def _export() -> dict[str, Any]:
            paths = export_all(con, out_dir, "both")
            write_manifest(out_dir, con, _latest_run_id(con))
            return {"files": len(paths) + 1}

        results.append(_timed("export", _export))
    finally:
        con.close()

    total = time.perf_counter() - t_total
    _print_summary_table(results, total)
    print(f"as_of={as_of} seed={seed} devices={n_devices} db={db_path} out={out_dir} docs={'regenerated' if docs else 'untouched'}")
    return 0


def _optional_step(step: str, module: str, fn: Callable[[], Any]) -> StepResult:
    """Run a v0.2 step whose package may not have landed yet; report a skip instead of failing."""
    import importlib

    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        _note(f"{step} skipped: {exc}")
        return StepResult(step=step, seconds=0.0, counts={"skipped": 1})
    return _timed(step, fn)


def cmd_all(args: argparse.Namespace) -> int:
    """Run the v0.2 chain ``ALL_ORDER_V2`` on synthetic data and print a summary table.

    ``--v01`` runs the legacy chain. When the v0.2 core packages are not importable
    the command says so and runs the legacy chain as well.
    """
    if getattr(args, "v01", False):
        return _cmd_all_v01(args)
    missing = _lake_core_missing()
    if missing:
        _note(f"v0.2 lake packages not importable: {missing}; running the v0.1 chain (all --v01)")
        return _cmd_all_v01(args)

    from restwert import db
    from restwert.config import load_assumptions, load_kpi_targets, load_thresholds
    from restwert.decisions.runner import run_all_decisions
    from restwert.export import export_all, export_lake, write_manifest
    from restwert.forecast.run import run_forecast
    from restwert.lake.ingest import run_ingest
    from restwert.lakegen import run_generate_lake
    from restwert.pnl.lifecycle import run_pnl

    t_total = time.perf_counter()
    lake_cfg = _load_lake_config(_lake_config_path(args))
    cadence = args.cadence or lake_cfg.delivery_cadence
    if cadence != lake_cfg.delivery_cadence:
        lake_cfg = lake_cfg.model_copy(update={"delivery_cadence": cadence})
    seed = args.seed if args.seed is not None else lake_cfg.seed
    if args.small:
        n = SMALL_SERIALS
    else:
        n = args.devices if args.devices is not None else lake_cfg.n_devices
    as_of: date = args.as_of if args.as_of else lake_cfg.as_of
    csv_dir = Path(args.csv_dir)
    out_dir = Path(args.out)
    db_path = Path(args.db)
    lake_dir = _resolve_lake_dir(args)
    raw_dir = lake_dir / "raw"
    docs = _docs_wanted(args)

    if not args.keep_db:
        if str(db_path) != ":memory:" and db_path.exists():
            db_path.unlink()
            wal = db_path.with_suffix(db_path.suffix + ".wal")
            if wal.exists():
                wal.unlink()
        if raw_dir.exists():
            _wipe_raw_layer(raw_dir, force=bool(getattr(args, "wipe_raw", False)))

    a = load_assumptions()
    thr = load_thresholds()
    targets = load_kpi_targets()

    results: list[StepResult] = []
    # The truth curve is calibrated to the public anchor curves. ``outputs/`` is not
    # tracked, so on a fresh clone the curves are fitted first from the tracked
    # anchors (``data/anchors``) into ``--out``; otherwise the generator would fall
    # back to the documented default curve without saying so on the console.
    curves_path = Path(MARKET_CURVES_CSV)
    if not curves_path.exists():
        from restwert.market.anchors import ANCHORS_DIR
        from restwert.market.run import run_market

        results.append(
            _timed("market", lambda: run_market(out_dir, Path(CATALOGUE_DIR), Path(ANCHORS_DIR), a, as_of))
        )
        curves_path = out_dir / "market_curves.csv"
        _note(f"{MARKET_CURVES_CSV} missing; curves fitted from the public anchors into {curves_path}")
    results.append(
        _timed("generate-lake", lambda: run_generate_lake(lake_cfg, seed, n, raw_dir, Path(CATALOGUE_DIR), curves_path))
    )

    con = _open_db(db_path)
    try:
        db.create_schema(con)
        results.append(_timed("ingest", lambda: run_ingest(con, raw_dir, None, None, False, as_of=as_of)))
        results.append(_timed("conform", lambda: _run_conform(con, as_of, a, csv_dir, seed, docs)))
        results.append(
            _timed("forecast", lambda: run_forecast(con, as_of, a, thr, lake_cfg, replay=True, backtest=True))
        )
        results.append(_timed("pnl", lambda: run_pnl(con, as_of, a)))

        def _timeline() -> Any:
            from restwert.lake.timeline import run_timeline

            return run_timeline(con, as_of)

        results.append(_optional_step("timeline", "restwert.lake.timeline", _timeline))

        def _ledger() -> Any:
            from restwert.ledger.run import run_ledger

            return run_ledger(con, as_of, a)

        results.append(_optional_step("ledger", "restwert.ledger.run", _ledger))
        results.append(_optional_step("levers", "restwert.levers.run", lambda: _run_levers(con, as_of, thr, a, docs)))
        results.append(_timed("decide", lambda: run_all_decisions(con, as_of, thr, a, write_docs=docs)))
        results.append(_timed("contracts", lambda: _run_contracts_v1_and_v2(con, as_of, thr)))
        results.append(_timed("kpis", lambda: _run_kpis_v1_and_gold(con, as_of, targets, docs)))

        def _export() -> dict[str, Any]:
            paths = export_all(con, out_dir, "both")
            lake_paths = export_lake(con, out_dir, lake_dir, "both")
            write_manifest(out_dir, con, _latest_run_id(con))
            return {"files": len(paths) + 1, "lake_files": len(lake_paths)}

        results.append(_timed("export", _export))
    finally:
        con.close()

    _copy_synthetic_md(lake_dir, csv_dir.parent / "SYNTHETIC.md", seed, n)

    total = time.perf_counter() - t_total
    _print_summary_table(results, total)
    print(f"as_of={as_of} seed={seed} serials={n} lake={lake_dir} cadence={cadence} db={db_path} out={out_dir} docs={'regenerated' if docs else 'untouched'}")
    return 0


def _wipe_raw_layer(raw_dir: Path, force: bool) -> None:
    """Empty ``<lake-dir>/raw`` before a from-scratch ``all`` run, without touching a real export.

    Every file under the landing layer must be a generated landing file (first line
    ``# SYNTHETIC DATA`` or ``# PUBLIC DATA``) or the generator's ``_manifest.json``; any other
    file names itself and blocks the wipe (exit code 1 through ``main``) unless ``--wipe-raw``
    is given. The count of removed files is printed. Real feeds are run through the individual
    steps (``ingest --all`` onwards), never through ``all``.
    """
    from restwert.lakegen import landing_file_is_generated
    from restwert.lakegen.writer import MANIFEST_NAME

    files = [p for p in sorted(raw_dir.rglob("*")) if p.is_file()]
    blocking = [p for p in files if not (p.name == MANIFEST_NAME or (p.suffix.lower() == ".csv" and landing_file_is_generated(p)))]
    if blocking and not force:
        shown = ", ".join(str(p.relative_to(raw_dir)) for p in blocking[:5])
        more = f" (+{len(blocking) - 5} more)" if len(blocking) > 5 else ""
        raise ValueError(
            f"all: refusing to empty {raw_dir}: {len(blocking)} file(s) are not generated landing files "
            f"(first line '# SYNTHETIC DATA' or '# PUBLIC DATA'): {shown}{more}. Real exports are never "
            f"modified after landing; run the individual steps on them, use --keep-db, another --lake-dir, "
            f"or --wipe-raw to remove them on purpose"
        )
    shutil.rmtree(raw_dir)
    print(f"  removed {len(files)} generated landing file(s) under {raw_dir}" + (" (--wipe-raw)" if blocking else ""))


def cmd_market(args: argparse.Namespace) -> int:
    """Public anchors: realisation per used price, curves per family, summary markdown."""
    from restwert.config import load_assumptions
    from restwert.market.run import run_market

    try:
        a = load_assumptions()
    except Exception as exc:  # noqa: BLE001 - the analysis runs with the placeholder default
        print(f"  note: assumptions not loaded ({type(exc).__name__}: {exc}); placeholder discount used")
        a = None
    as_of: date | None = args.as_of if getattr(args, "as_of", None) else None
    res = _timed(
        "market",
        lambda: run_market(Path(args.out), Path(args.catalogue_dir), Path(args.anchors_dir), a, as_of),
    )
    _print_summary_table([res], res.seconds)
    print(f"summary: {Path(args.out) / 'market_summary.md'}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start Streamlit on ``restwert/dashboard/app.py``; the only process spawn in the package."""
    import subprocess  # allowed here only (see tests/test_no_side_effects.py)

    app_path = ROOT / "restwert" / "dashboard" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--", "--db", str(args.db)]
    print("starting: " + " ".join(cmd))
    return int(subprocess.call(cmd))


# --------------------------------------------------------------------------- parser


def _add_db(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB file (default data/restwert.duckdb)")


def _add_as_of(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--as-of",
        dest="as_of",
        type=_parse_date,
        default=None,
        help="valuation date YYYY-MM-DD (default: generator as_of on synthetic data, lake.yaml as_of on a lake DB, else today)",
    )
    p.add_argument(
        "--config",
        default=str(CONFIG_DIR / "generator.yaml"),
        help="generator config (launch calendar, default as_of); a path ending in lake.yaml selects the lake config",
    )


def _add_lake_dir(p: argparse.ArgumentParser, default: str | None = str(LAKE_DIR)) -> None:
    p.add_argument("--lake-dir", dest="lake_dir", default=default, help="data lake root holding raw/ (default data/lake)")


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for all subcommands of SPEC 8.1 and SPEC_v0.2 9.1."""
    parser = argparse.ArgumentParser(
        prog="restwert",
        description="Restwert Engine: asset P&L, residual value forecast and deterministic decisions "
        "for a Device-as-a-Service fleet. " + GOVERNANCE_PRINCIPLE,
    )
    parser.add_argument("--version", action="version", version=f"restwert {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="write synthetic source CSVs (v0.1 generator)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--devices", type=int, default=None)
    p.add_argument("--out", default=str(RAW_CSV_DIR))
    p.add_argument("--config", default=str(CONFIG_DIR / "generator.yaml"))
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("load", help="create schema and load CSVs into DuckDB")
    p.add_argument("--csv-dir", dest="csv_dir", default=str(RAW_CSV_DIR))
    _add_db(p)
    p.add_argument("--allow-mixed", dest="allow_mixed", action="store_true")
    p.add_argument("--no-validate", dest="no_validate", action="store_true")
    _add_docs_flags(p)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("generate-lake", help="write the landing files of every feed into <lake-dir>/raw (v0.2 generator)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--serials", "--devices", dest="serials", type=int, default=None, help="fleet size in serials")
    _add_lake_dir(p)
    p.add_argument("--config", default=str(LAKE_CONFIG), help="lake config (default config/lake.yaml)")
    p.add_argument("--catalogue-dir", dest="catalogue_dir", default=str(CATALOGUE_DIR))
    p.add_argument("--curves", default=str(MARKET_CURVES_CSV), help="public anchor curves (outputs/market_curves.csv)")
    p.add_argument("--cadence", choices=CADENCES, default=None, help="delivery cadence of the landing files")
    p.add_argument("--wipe-raw", dest="wipe_raw", action="store_true", help="also remove landing files that are NOT generated; without it such a file blocks the run")
    p.set_defaults(func=cmd_generate_lake)

    p = sub.add_parser("ingest", help="load one landing file (or every file under <lake-dir>/raw) into bronze")
    p.add_argument("--source", default=None, help="feed key, e.g. erp/goods_receipts")
    p.add_argument("--file", default=None, help="landing file path")
    p.add_argument("--all", action="store_true", help="ingest every landing file under <lake-dir>/raw in feed order")
    _add_lake_dir(p)
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="report counts, duplicates and unresolved rows; write nothing")
    _add_as_of(p)
    _add_db(p)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("conform", help="materialise the ten v0.1 source tables from bronze (+ compat CSVs)")
    _add_as_of(p)
    _add_db(p)
    p.add_argument("--csv-dir", dest="csv_dir", default=None, help="also write <csv-dir>/<table>.csv compat copies")
    p.add_argument("--seed", type=int, default=None, help="seed named in the compat CSV header (default: lake.yaml seed)")
    _add_docs_flags(p)
    p.set_defaults(func=cmd_conform)

    p = sub.add_parser("forecast", help="fit residual value model, replay, backtest, error series")
    _add_as_of(p)
    p.add_argument("--no-replay", dest="no_replay", action="store_true")
    p.add_argument("--no-backtest", dest="no_backtest", action="store_true")
    _add_db(p)
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("pnl", help="lifecycle P&L, TCO, aggregates")
    _add_as_of(p)
    _add_db(p)
    p.set_defaults(func=cmd_pnl)

    p = sub.add_parser("timeline", help="timestamp chain per serial and chain quality")
    _add_as_of(p)
    _add_db(p)
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("ledger", help="silver ledger lines, device ledger, reconciliation, cohorts")
    _add_as_of(p)
    _add_db(p)
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("levers", help="attribute the result gap to the seven levers (where to tighten)")
    _add_as_of(p)
    _add_db(p)
    _add_docs_flags(p)
    p.set_defaults(func=cmd_levers)

    p = sub.add_parser("decide", help="run deterministic rules R01..R07")
    _add_as_of(p)
    _add_db(p)
    _add_docs_flags(p)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("kpis", help="compute the KPI tree (v0.1) and the gold KPIs (v0.2)")
    _add_as_of(p)
    _add_db(p)
    p.add_argument(
        "--catalogue",
        action="store_true",
        help="only render docs/KPI_CATALOGUE.md (and docs/GOLD_KPIS.md) from code and exit",
    )
    _add_docs_flags(p)
    p.set_defaults(func=cmd_kpis)

    p = sub.add_parser("contracts", help="contracts register and renewal calendar (v0.1), register v2 on a lake DB")
    _add_as_of(p)
    _add_db(p)
    p.set_defaults(func=cmd_contracts)

    p = sub.add_parser("export", help="write CSV/parquet + manifest for Power BI")
    _add_db(p)
    p.add_argument("--out", default=str(OUTPUTS_DIR))
    p.add_argument("--fmt", choices=("csv", "parquet", "both"), default="both")
    p.add_argument("--lake", action="store_true", help="also export the lake tables and write parquet mirrors under <lake-dir>/{bronze,silver,gold}/")
    _add_lake_dir(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "all",
        help="generate-lake -> ingest -> conform -> forecast -> pnl -> timeline -> ledger -> levers -> decide -> contracts -> kpis -> export (--v01: the v0.1 chain)",
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--devices", "--serials", dest="devices", type=int, default=None, help="fleet size (serials)")
    _add_as_of(p)
    _add_db(p)
    p.add_argument("--out", default=str(OUTPUTS_DIR))
    p.add_argument("--csv-dir", dest="csv_dir", default=str(RAW_CSV_DIR))
    _add_lake_dir(p, default=None)
    p.add_argument("--cadence", choices=CADENCES, default=None, help="delivery cadence of the landing files (default: lake.yaml)")
    p.add_argument("--small", action="store_true", help=f"{SMALL_SERIALS} serials (v0.2) or {SMALL_DEVICES} devices (--v01)")
    p.add_argument("--v01", action="store_true", help="run the legacy v0.1 chain (generate -> load -> ...)")
    p.add_argument(
        "--keep-db",
        dest="keep_db",
        action="store_true",
        help="do not delete an existing DuckDB file (nor the landing files) before the run",
    )
    p.add_argument(
        "--wipe-raw",
        dest="wipe_raw",
        action="store_true",
        help="also remove landing files that are NOT generated (no '# SYNTHETIC DATA' / '# PUBLIC DATA' first line); without it such a file blocks the run",
    )
    _add_docs_flags(p)
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("market", help="public anchors: realisation vs launch RRP, curves per family, summary")
    _add_as_of(p)
    p.add_argument("--out", default=str(OUTPUTS_DIR))
    p.add_argument("--catalogue-dir", dest="catalogue_dir", default=str(DATA_DIR / "catalogue"))
    p.add_argument("--anchors-dir", dest="anchors_dir", default=str(DATA_DIR / "anchors"))
    p.set_defaults(func=cmd_market)

    p = sub.add_parser("dashboard", help="start the Streamlit dashboard")
    _add_db(p)
    p.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv``, print the governance banner once, dispatch, return 0 or 1."""
    parser = build_parser()
    args = parser.parse_args(argv)
    print(BANNER)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI reports and returns 1, never raises
        print(f"ERROR in {args.command}: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
