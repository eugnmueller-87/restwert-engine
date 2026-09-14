"""Shared pytest fixtures (SPEC section 8.6).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

* ``tmp_db``: in-memory DuckDB with the full schema.
* ``small_cfg``: GeneratorConfig from the shipped yaml with ``n_devices = 400``.
* ``thresholds`` / ``assumptions`` / ``targets``: the shipped config files.
* ``as_of``: ``small_cfg.as_of``.
* ``full_pipeline_db``: session-scoped, runs ``all --small`` once into a
  temporary directory and yields a connection to the resulting database.
  Also exposes the paths used via ``full_pipeline_paths``.

The repository root is put on ``sys.path`` so ``pytest`` works from a plain
checkout without ``pip install -e .``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass(frozen=True)
class PipelinePaths:
    """Where the session-scoped ``all --small`` run put its artefacts."""

    db: Path
    out: Path
    csv_dir: Path
    seconds: float
    return_code: int


@pytest.fixture
def tmp_db():
    """In-memory DuckDB with every table of ``schema.TABLE_ORDER`` created."""
    from restwert import db

    con = db.connect(":memory:")
    db.create_schema(con)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture(scope="session")
def small_cfg():
    """The shipped generator config with ``n_devices`` set to 400."""
    from restwert.config import load_generator_config

    cfg = load_generator_config()
    return cfg.model_copy(update={"n_devices": 400})


@pytest.fixture(scope="session")
def thresholds():
    from restwert.config import load_thresholds

    return load_thresholds()


@pytest.fixture(scope="session")
def assumptions():
    from restwert.config import load_assumptions

    return load_assumptions()


@pytest.fixture(scope="session")
def targets():
    from restwert.config import load_kpi_targets

    return load_kpi_targets()


@pytest.fixture(scope="session")
def as_of(small_cfg) -> date:
    return small_cfg.as_of


@pytest.fixture(scope="session")
def full_pipeline_paths(tmp_path_factory) -> PipelinePaths:
    """Run ``python -m restwert all --small`` once per session into a temp dir."""
    import time

    from restwert.cli import main

    base = tmp_path_factory.mktemp("pipeline")
    db_path = base / "restwert.duckdb"
    out_dir = base / "outputs"
    csv_dir = base / "raw_csv"
    t0 = time.perf_counter()
    rc = main(
        [
            "all",
            "--small",
            "--db",
            str(db_path),
            "--out",
            str(out_dir),
            "--csv-dir",
            str(csv_dir),
        ]
    )
    seconds = time.perf_counter() - t0
    return PipelinePaths(db=db_path, out=out_dir, csv_dir=csv_dir, seconds=seconds, return_code=rc)


@pytest.fixture(scope="session")
def full_pipeline_db(full_pipeline_paths: PipelinePaths):
    """Connection to the database produced by the session-scoped ``all --small`` run."""
    from restwert import db

    assert full_pipeline_paths.return_code == 0, "all --small failed; see captured output"
    con = db.connect(full_pipeline_paths.db)
    try:
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------- v0.2 (SPEC_v0.2 9.5), additive


def lake_packages_available() -> bool:
    """True when the v0.2 core packages (generate-lake, ingest, conform) are importable.

    ``all`` falls back to the v0.1 chain without them, so the lake fixtures skip
    instead of failing a checkout where modules 1 and 2 have not landed yet.
    """
    import importlib

    for name in ("restwert.lakegen", "restwert.lake.ingest", "restwert.lake.conform"):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            return False
    return True


@dataclass(frozen=True)
class LakePipelinePaths:
    """Where the session-scoped v0.2 ``all --small`` run put its artefacts."""

    db: Path
    out: Path
    csv_dir: Path
    lake_dir: Path
    seconds: float
    return_code: int


@pytest.fixture(scope="session")
def lake_pipeline_paths(tmp_path_factory) -> LakePipelinePaths:
    """Run ``python -m restwert all --small`` (v0.2 chain) once per session with an explicit ``--lake-dir``."""
    import time

    if not lake_packages_available():
        pytest.skip("v0.2 lake packages (restwert.lakegen, restwert.lake) not present in this checkout")
    from restwert.cli import main

    base = tmp_path_factory.mktemp("lake_pipeline")
    db_path = base / "restwert.duckdb"
    out_dir = base / "outputs"
    csv_dir = base / "raw_csv"
    lake_dir = base / "lake"
    t0 = time.perf_counter()
    rc = main(
        [
            "all",
            "--small",
            "--db",
            str(db_path),
            "--out",
            str(out_dir),
            "--csv-dir",
            str(csv_dir),
            "--lake-dir",
            str(lake_dir),
        ]
    )
    seconds = time.perf_counter() - t0
    return LakePipelinePaths(
        db=db_path, out=out_dir, csv_dir=csv_dir, lake_dir=lake_dir, seconds=seconds, return_code=rc
    )


@pytest.fixture(scope="session")
def lake_pipeline_db(lake_pipeline_paths: LakePipelinePaths):
    """Connection to the database produced by the session-scoped v0.2 ``all --small`` run."""
    from restwert import db

    assert lake_pipeline_paths.return_code == 0, "all --small (v0.2) failed; see captured output"
    con = db.connect(lake_pipeline_paths.db)
    try:
        yield con
    finally:
        con.close()
