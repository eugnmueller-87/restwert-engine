"""DuckDB access layer (SPEC.md section 2.6).

All cross-module data flow goes through DuckDB tables. ``write_df`` casts every
column explicitly to the DDL type, so frames with string dates, NaN money or
python ``date`` objects all land correctly. ``append_rows`` is the only call
allowed on the immutable tables (``forecast_runs``, ``decision_log``,
``write_down_ledger``, ``runs``).

``load_csv_dir`` refuses a CSV without an ``is_synthetic`` column: the loader
never defaults it, because that column is how the dashboard knows whether to
show the synthetic banner.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd

from restwert import schema
from restwert.paths import DEFAULT_DB

_TMP_VIEW = "_restwert_write_df_tmp"


def connect(path: Path | str = DEFAULT_DB, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection; ``":memory:"`` is allowed. Creates the parent folder."""
    if isinstance(path, str) and path == ":memory:":
        return duckdb.connect(":memory:")
    p = Path(path)
    if not read_only:
        p.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(p), read_only=read_only)


def _q(table: str) -> str:
    """'bronze.x' -> '"bronze"."x"', 'x' -> '"x"'."""
    if "." in table:
        schema_name, name = table.split(".", 1)
        return f'"{schema_name}"."{name}"'
    return f'"{table}"'


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    if "." in table:
        schema_name, name = table.split(".", 1)
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema_name, name],
        ).fetchone()
    else:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
            [table],
        ).fetchone()
    return bool(row and row[0] > 0)


def create_schema(con: duckdb.DuckDBPyConnection, drop_derived: bool = False) -> None:
    """``CREATE TABLE IF NOT EXISTS`` for every table in ``schema.TABLE_ORDER``.

    ``drop_derived=True`` drops the rebuilt derived tables first (never the
    immutable ones and never the source tables).
    """
    if drop_derived:
        for t in schema.DERIVED_TABLES:
            if t not in schema.IMMUTABLE_TABLES:
                con.execute(f'DROP TABLE IF EXISTS "{t}"')
    for t in schema.TABLE_ORDER:
        con.execute(schema.DDL[t])


# --------------------------------------------------------------------------- frames in / out


def _table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE {_q(table)}").fetchall()
    return [(r[0], r[1]) for r in rows]


def _to_sql_value(v, target: str):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return "true" if bool(v) else "false"
    if isinstance(v, datetime):
        if target == "DATE":
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, (np.integer,)):
        return str(int(v))
    if isinstance(v, (np.floating,)):
        return repr(float(v))
    return str(v)


def _prepare(df: pd.DataFrame, types: dict[str, str]) -> tuple[pd.DataFrame, set[str]]:
    """Return a frame whose columns are either numeric/bool numpy dtypes or strings.

    Everything that is not a plain numpy number or bool is converted to an ISO /
    text representation so DuckDB can ``CAST`` it to the DDL type. Returns the
    set of float columns (their NaN is mapped to NULL in SQL).
    """
    out = pd.DataFrame(index=df.index)
    float_cols: set[str] = set()
    for c in df.columns:
        s = df[c]
        target = types[c].upper()
        if pd.api.types.is_bool_dtype(s) and not isinstance(s.dtype, pd.api.extensions.ExtensionDtype):
            out[c] = s.astype(bool)
        elif pd.api.types.is_integer_dtype(s) and not isinstance(s.dtype, pd.api.extensions.ExtensionDtype):
            out[c] = s.astype("int64")
        elif pd.api.types.is_float_dtype(s) and not isinstance(s.dtype, pd.api.extensions.ExtensionDtype):
            out[c] = s.astype("float64")
            float_cols.add(c)
        elif pd.api.types.is_datetime64_any_dtype(s):
            fmt = "%Y-%m-%d" if target == "DATE" else "%Y-%m-%d %H:%M:%S.%f"
            text = s.dt.strftime(fmt)
            out[c] = pd.Series([t if ok else None for t, ok in zip(text, s.notna())], index=s.index, dtype=object)
        else:
            out[c] = pd.Series([_to_sql_value(v, target) for v in s], index=s.index, dtype=object)
    return out, float_cols


def write_df(
    con: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    mode: Literal["replace", "append"] = "replace",
) -> int:
    """Write a frame into ``table``; ``replace`` = DELETE + INSERT (keeps the DDL).

    Columns not in the table are ignored; table columns not in the frame are
    inserted as NULL. Returns the number of rows written.
    """
    if mode not in ("replace", "append"):
        raise ValueError(f"mode must be replace or append, got {mode!r}")
    if not table_exists(con, table):
        raise ValueError(f"table {table!r} does not exist; call create_schema first")
    if mode == "replace" and table in schema.IMMUTABLE_TABLES:
        raise ValueError(f"table {table!r} is immutable; use append_rows")
    cols = _table_columns(con, table)
    types = dict(cols)
    if mode == "replace":
        con.execute(f"DELETE FROM {_q(table)}")
    if df is None or len(df) == 0:
        return 0
    keep = [c for c in df.columns if c in types]
    prepared, float_cols = _prepare(df[keep].reset_index(drop=True), types)
    select_parts = []
    for name, typ in cols:
        if name not in prepared.columns:
            select_parts.append(f'NULL AS "{name}"')
        elif name in float_cols:
            select_parts.append(
                f'CAST(CASE WHEN "{name}" IS NULL OR isnan("{name}") THEN NULL ELSE "{name}" END AS {typ}) AS "{name}"'
            )
        else:
            select_parts.append(f'CAST("{name}" AS {typ}) AS "{name}"')
    con.register(_TMP_VIEW, prepared)
    try:
        con.execute(f"INSERT INTO {_q(table)} SELECT {', '.join(select_parts)} FROM {_TMP_VIEW}")
    finally:
        con.unregister(_TMP_VIEW)
    return int(len(prepared))


def append_rows(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Append only. The ONLY call allowed on the immutable tables."""
    return write_df(con, table, df, mode="append")


def read_df(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a query and return a pandas frame."""
    return con.execute(sql, params or []).df()


def is_synthetic(con: duckdb.DuckDBPyConnection) -> bool:
    """True if any source table has a row with ``is_synthetic = true``; False if ``devices`` is empty."""
    if not table_exists(con, "devices"):
        return False
    n_dev = con.execute("SELECT count(*) FROM devices").fetchone()[0]
    if n_dev == 0:
        return False
    for t in schema.SOURCE_TABLES:
        if not table_exists(con, t):
            continue
        n = con.execute(f'SELECT count(*) FROM "{t}" WHERE is_synthetic').fetchone()[0]
        if n > 0:
            return True
    return False


# --------------------------------------------------------------------------- runs


def new_run(
    con: duckdb.DuckDBPyConnection,
    command: str,
    seed: int | None,
    as_of: date,
    thresholds_sha256: str | None,
) -> str:
    """Insert a ``runs`` row and return its id ``<command>-<YYYYmmddHHMMSS>-<4hex>``."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"{command}-{now:%Y%m%d%H%M%S}-{uuid4().hex[:4]}"
    con.execute(
        "INSERT INTO runs (run_id, command, started_at, finished_at, seed, as_of, thresholds_sha256, counts_json) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, NULL)",
        [run_id, command, now, seed, as_of, thresholds_sha256],
    )
    return run_id


def finish_run(con: duckdb.DuckDBPyConnection, run_id: str, counts: dict[str, int]) -> None:
    """Stamp ``finished_at`` and the row counts onto a run."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    con.execute(
        "UPDATE runs SET finished_at = ?, counts_json = ? WHERE run_id = ?",
        [now, json.dumps(counts, sort_keys=True), run_id],
    )


# --------------------------------------------------------------------------- CSV load

_TRUE = {"true", "t", "1", "yes", "y"}
_FALSE = {"false", "f", "0", "no", "n"}


def _read_csv_skip_comments(path: Path) -> pd.DataFrame:
    """Read a CSV as strings, skipping leading ``#`` comment lines. Tolerates a BOM."""
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    body = "".join(lines[i:])
    if not body.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(body), dtype=str, keep_default_na=True)


def _parse_bool_column(table: str, s: pd.Series) -> pd.Series:
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            raise ValueError(f"{table}.csv: is_synthetic has an empty value; it must be true or false")
        t = str(v).strip().lower()
        if t in _TRUE:
            return True
        if t in _FALSE:
            return False
        raise ValueError(f"{table}.csv: is_synthetic value {v!r} is not a boolean")

    return s.map(one)


_REFERENTIAL_CHECKS: tuple[tuple[str, str, str, str], ...] = (
    ("devices", "model", "model_catalogue", "model"),
    ("rental_contracts", "serial", "devices", "serial"),
    ("events", "serial", "devices", "serial"),
    ("refurbishment", "serial", "devices", "serial"),
    ("resale", "serial", "devices", "serial"),
    ("devices", "po_number", "purchase_orders", "po_number"),
)


def referential_checks(frames: dict[str, pd.DataFrame]) -> None:
    """Raise ``ValueError`` if a child key is missing in its parent table (both present)."""
    for child, ckey, parent, pkey in _REFERENTIAL_CHECKS:
        if child not in frames or parent not in frames:
            continue
        cf, pf = frames[child], frames[parent]
        if len(cf) == 0:
            continue
        parent_keys = set(pf[pkey].dropna().astype(str))
        child_keys = cf[ckey].dropna().astype(str)
        missing = sorted(set(child_keys) - parent_keys)
        if missing:
            sample = ", ".join(missing[:5])
            raise ValueError(
                f"referential check failed: {child}.{ckey} has {len(missing)} value(s) not in "
                f"{parent}.{pkey} (e.g. {sample})"
            )


def load_csv_dir(
    con: duckdb.DuckDBPyConnection,
    csv_dir: Path,
    validate: bool = True,
    allow_mixed: bool = False,
) -> dict[str, int]:
    """Load ``<table>.csv`` for every source table present in ``csv_dir``.

    Skips leading ``#`` comment lines, validates via ``schema.validate_frame``,
    runs the referential checks, then ``write_df(mode="replace")`` per table.
    Raises ``ValueError`` if ``is_synthetic`` is missing in a file or differs
    across files (unless ``allow_mixed``). Returns rows written per table.
    """
    csv_dir = Path(csv_dir)
    if not csv_dir.is_dir():
        raise FileNotFoundError(f"csv directory not found: {csv_dir}")
    frames: dict[str, pd.DataFrame] = {}
    flags: dict[str, bool | None] = {}
    for table in schema.SOURCE_TABLES:
        path = csv_dir / f"{table}.csv"
        if not path.exists():
            continue
        df = _read_csv_skip_comments(path)
        if len(df.columns) and "is_synthetic" not in df.columns:
            raise ValueError(
                f"{path.name} has no 'is_synthetic' column. Real data must carry the column with the "
                f"value false on every row (synthetic data carries true); the loader never defaults it."
            )
        if len(df) == 0:
            frames[table] = pd.DataFrame(columns=list(schema.ROW_MODELS[table].model_fields))
            flags[table] = None
            continue
        df["is_synthetic"] = _parse_bool_column(table, df["is_synthetic"])
        if "source_file" not in df.columns:
            df["source_file"] = path.name
        uniq = set(df["is_synthetic"].unique().tolist())
        if len(uniq) > 1 and not allow_mixed:
            raise ValueError(f"{path.name}: is_synthetic mixes true and false rows (use allow_mixed to accept)")
        flags[table] = bool(df["is_synthetic"].iloc[0])
        frames[table] = schema.validate_frame(table, df) if validate else df
    if not frames:
        raise FileNotFoundError(f"no <table>.csv found in {csv_dir}")
    distinct = {v for v in flags.values() if v is not None}
    if len(distinct) > 1 and not allow_mixed:
        detail = ", ".join(f"{t}={v}" for t, v in flags.items() if v is not None)
        raise ValueError(f"is_synthetic differs across files ({detail}); use allow_mixed to accept")
    if validate:
        referential_checks(frames)
    counts: dict[str, int] = {}
    for table in schema.SOURCE_TABLES:
        if table in frames:
            counts[table] = write_df(con, table, frames[table], mode="replace")
    return counts


__all__ = [
    "connect",
    "create_schema",
    "load_csv_dir",
    "write_df",
    "append_rows",
    "read_df",
    "table_exists",
    "is_synthetic",
    "new_run",
    "finish_run",
    "referential_checks",
]
