"""Ingest landing files into the bronze layer (SPEC_v0.2 section 4.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

One landing file, one DuckDB transaction: type every column (vectorised),
resolve every hard key against its parent bronze table, deduplicate by row
hash, append the new rows with the tail columns, register the delivery.
Rows that fail land in ``bronze.unresolved`` with a closed reason code; a
file is never refused because of a row, and a row is never guessed.

Idempotence: the sha256 of the file is the identity of a delivery. The same
file twice is a no-op; the same business key with different content is a
``duplicate_conflict`` and the existing row stays (first delivery wins).
A dry run rolls the transaction back and leaves nothing behind (on a database
that already exists; ``ingest --dry-run`` on a missing database file runs
against an in-memory copy of the schema so no file is created).

Stated, not hidden: ``bronze.unresolved`` is keyed by (delivery, row number,
reason). A landing row that a source system re-delivers in a NEW file (a
different sha256) and that still cannot be resolved is recorded again under the
new delivery, so the table and the Data page list such a row once per delivery
that carried it; identical re-deliveries of the same file are no-ops and never
add rows. Counting distinct source rows would need a source-side key the feeds
do not all carry, so the count stays per delivery and says so.

``gold.ingest_summary`` carries the run's ``as_of`` (the valuation date every
other gold table carries), never the wall-clock date.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.lake import common
from restwert.lake.feeds import FEEDS, INGEST_ORDER, RESOLVE_FILTERS, FeedSpec, parse_landing_name
from restwert.lake.schema_lake import create_lake_schema
from restwert.paths import LAKE_RAW_DIR
from restwert.records import RunSummary

_TRUE = {"true", "t", "1", "yes", "y"}
_FALSE = {"false", "f", "0", "no", "n"}
_SEP = "\x1f"


# --------------------------------------------------------------------------- report


@dataclass
class IngestReport:
    """What happened to one landing file."""

    delivery_id: str
    feed: str
    source_system: str
    source_file: str
    sha256: str
    delivered_on: date
    rows_read: int
    rows_typed: int
    rows_new: int
    duplicates_identical: int
    duplicates_conflict: int
    unresolved: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    already_ingested: bool = False
    dry_run: bool = False
    seconds: float = 0.0

    @property
    def n_unresolved(self) -> int:
        return int(sum(self.unresolved.values()))

    def line(self) -> str:
        """One report line, e.g. ``erp/goods_receipts 2023-12-31_goods_receipts_002.csv read=1210 new=1198 dup=8 conflict=1 unresolved=3 (unknown_po_line=3)``."""
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(self.unresolved.items()) if v)
        text = (
            f"{self.feed} {self.source_file} read={self.rows_read} new={self.rows_new} "
            f"dup={self.duplicates_identical} conflict={self.duplicates_conflict} unresolved={self.n_unresolved}"
        )
        if reasons:
            text += f" ({reasons})"
        if self.already_ingested:
            text += " [already ingested]"
        if self.dry_run:
            text += " [dry-run]"
        return text


# --------------------------------------------------------------------------- reading


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _header_lines(path: Path) -> list[str]:
    out: list[str] = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            out.append(line.rstrip("\r\n"))
    return out


def _read_csv_text(path: Path) -> pd.DataFrame:
    """Like ``db._read_csv_skip_comments`` but with plain object columns (no arrow strings), for speed."""
    import io

    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    body = "".join(lines[i:])
    if not body.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(body), dtype=object, keep_default_na=True)


def read_landing_file(path: Path) -> tuple[pd.DataFrame, bool]:
    """Read a landing file as strings; returns ``(frame, is_synthetic)``.

    Leading ``#`` lines are skipped. Raises ``ValueError`` when the ``is_synthetic``
    column is missing or not one uniform boolean per file (the v0.1 rule: the
    loader never defaults it). The returned frame keeps every landing column
    (``is_synthetic`` included) as text; empty cells are NaN.
    """
    path = Path(path)
    df = _read_csv_text(path)
    if len(df.columns) == 0:
        # an empty file: the flag is read from the header line
        header = _header_lines(path)
        flag = any(h.upper().startswith("# SYNTHETIC") for h in header)
        return pd.DataFrame(), flag
    if "is_synthetic" not in df.columns:
        raise ValueError(
            f"{path.name} has no 'is_synthetic' column. Real data must carry the column with the value false on "
            f"every row (synthetic data carries true); the reader never defaults it."
        )
    if len(df) == 0:
        header = _header_lines(path)
        return df, any(h.upper().startswith("# SYNTHETIC") for h in header)
    distinct = pd.Series(df["is_synthetic"].unique(), dtype=object)
    flags = db._parse_bool_column(path.stem, distinct)  # the v0.1 rule, applied to the distinct values
    uniq = set(flags.tolist())
    if len(uniq) != 1:
        raise ValueError(f"{path.name}: is_synthetic mixes true and false rows; one file carries one value")
    return df, bool(flags.iloc[0])


# --------------------------------------------------------------------------- typing


def _parse_dates(text: pd.Series) -> pd.Series:
    """ISO dates and timestamps; a 7-character ``YYYY-MM`` value takes the 15th (the catalogue rule)."""
    s = pd.Series(text.to_numpy(dtype=object), index=text.index, dtype=object)
    lengths = s.str.len()
    seven = lengths.notna() & (lengths == 7)
    if seven.to_numpy().any():
        s = s.where(~seven, s + "-15")
    return pd.to_datetime(s, errors="coerce", format="ISO8601")


def type_rows(spec: FeedSpec, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Type every landing column of ``df`` (strings) per ``spec``; returns ``(typed, rejected)``.

    ``typed`` carries ``row_number`` (1-based data row index) plus every spec
    column in its type: int64 (float64 with NaN when a value is missing),
    float64, datetime64, bool (object with None when missing), object strings.
    ``rejected`` has ``row_number, reason_code, reason_text`` with the first reason
    as the code and every reason the row hit in the text. A rejected row never
    aborts the file. Vectorised per column; no per-row Python.
    """
    n = len(df)
    row_number = np.arange(1, n + 1, dtype="int64")
    cols: dict[str, object] = {"row_number": row_number}
    hits: list[pd.DataFrame] = []

    def _hit(mask: np.ndarray, code: str, col: str) -> None:
        if mask.any():
            hits.append(pd.DataFrame({"row_number": row_number[mask], "reason_code": code, "reason_text": f"{code}({col})"}))

    for col in spec.columns:
        if col.name in df.columns:
            text = df[col.name].astype(object).str.strip()
        else:
            text = pd.Series([None] * n, index=df.index, dtype=object)
        obj = text.to_numpy(dtype=object)
        missing = pd.isna(obj) | (obj == "")
        present = ~missing
        obj = np.where(present, obj, None)
        text = pd.Series(obj, index=df.index, dtype=object)
        if col.required:
            _hit(missing, "missing_required", col.name)
        if col.dtype == "str":
            values: object = obj
            if col.enum is not None:
                _hit(present & ~text.isin(col.enum).to_numpy(), "bad_enum", col.name)
        elif col.dtype in ("int", "float"):
            num = pd.to_numeric(text, errors="coerce").astype("float64").to_numpy()
            bad = present & np.isnan(num)
            if col.dtype == "int":
                bad = bad | (present & ~np.isnan(num) & (np.mod(num, 1.0) != 0))
            _hit(bad, "bad_type", col.name)
            if col.min_value is not None:
                _hit(present & ~np.isnan(num) & (num < col.min_value), "negative_amount", col.name)
            values = np.where(bad, np.nan, num)
        elif col.dtype in ("date", "datetime"):
            dt = _parse_dates(text)
            _hit(present & dt.isna().to_numpy(), "bad_type", col.name)
            values = (dt.dt.normalize() if col.dtype == "date" else dt).to_numpy()
        elif col.dtype == "bool":
            low = text.str.lower()
            tru = low.isin(_TRUE).to_numpy()
            fal = low.isin(_FALSE).to_numpy()
            _hit(present & ~tru & ~fal, "bad_type", col.name)
            values = np.where(tru, True, np.where(fal, False, None))
        else:  # pragma: no cover
            raise ValueError(f"unknown dtype {col.dtype!r} for column {col.name!r}")
        if col.dtype == "int" and not np.isnan(values).any():
            values = values.astype("int64")
        elif col.dtype == "bool" and not pd.isna(values).any():
            values = values.astype(bool)
        cols[col.name] = values

    if spec.key == "catalogue/models":
        cols["launch_date"] = _parse_dates(pd.Series(cols["launch_date_de"], dtype=object)).dt.normalize().to_numpy()
    typed = pd.DataFrame({
        name: (pd.Series(v, dtype=object) if getattr(v, "dtype", None) == object else v) for name, v in cols.items()
    })

    if hits:
        all_hits = pd.concat(hits, ignore_index=True).sort_values(["row_number"], kind="stable")
        rejected = (
            all_hits.groupby("row_number", sort=True)
            .agg(reason_code=("reason_code", "first"), reason_text=("reason_text", lambda s: ", ".join(s)))
            .reset_index()
        )
        bad_rows = typed["row_number"].isin(rejected["row_number"]).to_numpy()
        typed = typed.loc[~bad_rows]
    else:
        rejected = pd.DataFrame(columns=["row_number", "reason_code", "reason_text"])
    return typed.reset_index(drop=True), rejected


# --------------------------------------------------------------------------- key strings


def _num_strings(arr: np.ndarray, idx) -> pd.Series:
    """Floats as text: whole numbers without a decimal part, others as repr, '' for NaN."""
    miss = np.isnan(arr)
    safe = np.where(miss, 0.0, arr)
    whole = np.mod(safe, 1.0) == 0
    as_int = safe.astype("int64").astype(str).astype(object)
    as_float = pd.Series(safe).map(repr).to_numpy(dtype=object)
    out = np.where(miss, "", np.where(whole, as_int, as_float))
    return pd.Series(out, index=idx, dtype=object)


def _col_strings(frame: pd.DataFrame, col: str, spec: FeedSpec | None = None) -> pd.Series:
    """Canonical text of one typed column ('' for missing) for hashing and key comparison, vectorised."""
    s = frame[col]
    idx = frame.index
    if pd.api.types.is_datetime64_any_dtype(s):
        dtype = None
        if spec is not None:
            dtype = next((c.dtype for c in spec.columns if c.name == col), None)
        fmt = "%Y-%m-%d" if dtype == "date" else "%Y-%m-%dT%H:%M:%S"
        out = s.dt.strftime(fmt).to_numpy(dtype=object)
        return pd.Series(np.where(s.notna().to_numpy(), out, ""), index=idx, dtype=object)
    if pd.api.types.is_bool_dtype(s) and not isinstance(s.dtype, pd.api.extensions.ExtensionDtype):
        return pd.Series(np.where(s.to_numpy(), "true", "false"), index=idx, dtype=object)
    if isinstance(s.dtype, pd.Int64Dtype) or pd.api.types.is_integer_dtype(s):
        return _num_strings(s.astype("float64").to_numpy(), idx)
    if pd.api.types.is_float_dtype(s):
        return _num_strings(s.to_numpy(dtype="float64"), idx)
    obj = s.to_numpy(dtype=object)
    spec_dtype = next((c.dtype for c in spec.columns if c.name == col), None) if spec is not None else None
    if spec_dtype == "str":
        return pd.Series(np.where(pd.isna(obj), "", obj), index=idx, dtype=object)
    is_true = np.fromiter((v is True for v in obj), dtype=bool, count=len(obj))
    is_false = np.fromiter((v is False for v in obj), dtype=bool, count=len(obj))
    out = np.where(pd.isna(obj), "", obj)
    out = np.where(is_true, "true", np.where(is_false, "false", out)).astype(object)
    if any(not isinstance(v, str) for v in out):
        out = np.array([v if isinstance(v, str) else str(v) for v in out], dtype=object)
    return pd.Series(out, index=idx, dtype=object)


def _key_series(frame: pd.DataFrame, cols: list[str], spec: FeedSpec | None = None) -> pd.Series:
    parts = [_col_strings(frame, c, spec).to_numpy(dtype=object) for c in cols]
    out = parts[0]
    for p in parts[1:]:
        out = out + _SEP + p
    return pd.Series(out, index=frame.index, dtype=object)


_KEY_CACHE: dict[tuple[int, str, tuple[str, ...], str | None], tuple[object, int, set[str]]] = {}
_KEY_CACHE_MAX = 256


def _parent_keys(con: duckdb.DuckDBPyConnection, table: str, cols: list[str], where: str | None) -> set[str]:
    """Key strings of a parent bronze table (columns joined by the separator); cached while the table's row count is unchanged."""
    try:
        n_rows = con.execute(f"SELECT count(*) FROM {db._q(table)}").fetchone()[0]
    except duckdb.CatalogException:
        return set()
    cache_key = (id(con), table, tuple(cols), where)
    hit = _KEY_CACHE.get(cache_key)
    if hit is not None and hit[0] is con and hit[1] == n_rows:
        return hit[2]
    if len(_KEY_CACHE) >= _KEY_CACHE_MAX:
        _KEY_CACHE.clear()
    expr = f" || '{_SEP}' || ".join(f'COALESCE(CAST("{c}" AS VARCHAR), \'\')' for c in cols)
    sql = f"SELECT DISTINCT {expr} AS k FROM {db._q(table)}"
    if where:
        sql += f" WHERE {where}"
    keys = set(con.execute(sql).df()["k"].tolist())
    _KEY_CACHE[cache_key] = (con, n_rows, keys)  # the connection itself keeps its id from being reused
    return keys


# --------------------------------------------------------------------------- resolve


def resolve_keys(
    con: duckdb.DuckDBPyConnection, spec: FeedSpec, typed: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Check every hard reference of ``spec``; returns ``(resolved, unresolved, soft notes)``.

    ``unresolved`` has ``row_number, reason_code, reason_text`` (the first miss as
    the code, every miss in the text). Keys inside the same frame count as parents
    when the feed references itself. Soft references only produce a note.
    """
    if len(typed) == 0:
        return typed, pd.DataFrame(columns=["row_number", "reason_code", "reason_text"]), []
    hits: list[pd.DataFrame] = []
    for child, parent, pcol, reason in spec.resolves:
        child_cols = child.split("+")
        parent_cols = pcol.split("+")
        keys = _parent_keys(con, parent, parent_cols, RESOLVE_FILTERS.get((spec.key, parent)))
        if parent == spec.bronze_table:
            keys |= set(_key_series(typed, parent_cols, spec).tolist())
        present = pd.Series(True, index=typed.index)
        for c in child_cols:
            present &= typed[c].notna()
        child_key = _key_series(typed, child_cols, spec)
        miss = present & ~child_key.isin(keys)
        if miss.any():
            hits.append(pd.DataFrame({
                "row_number": typed.loc[miss, "row_number"].to_numpy(),
                "reason_code": reason,
                "reason_text": f"{reason}({child} -> {parent}.{pcol})",
            }))
    notes: list[str] = []
    for child, parent, pcol in spec.soft_refs:
        keys = _parent_keys(con, parent, [pcol], None)
        if parent == spec.bronze_table:
            keys |= set(_key_series(typed, [pcol], spec).tolist())
        present = typed[child].notna()
        miss = present & ~_key_series(typed, [child], spec).isin(keys)
        if miss.any():
            notes.append(f"soft reference {child} -> {parent}.{pcol}: {int(miss.sum())} row(s) not found (kept)")
    if hits:
        all_hits = pd.concat(hits, ignore_index=True)
        unresolved = (
            all_hits.groupby("row_number", sort=True)
            .agg(reason_code=("reason_code", "first"), reason_text=("reason_text", lambda s: ", ".join(s)))
            .reset_index()
        )
        resolved = typed.loc[~typed["row_number"].isin(unresolved["row_number"])].reset_index(drop=True)
    else:
        unresolved = pd.DataFrame(columns=["row_number", "reason_code", "reason_text"])
        resolved = typed
    return resolved, unresolved, notes


# --------------------------------------------------------------------------- dedupe


def row_hashes(spec: FeedSpec, frame: pd.DataFrame) -> pd.Series:
    """``common.row_hash`` over the content columns in ``FeedSpec`` column order.

    The column texts are joined vectorised with the separator ``common.row_hash``
    uses, so ``row_hash([joined])`` equals ``row_hash([v1, v2, ...])`` value for value.
    """
    if len(frame) == 0:
        return pd.Series([], dtype=object)
    joined = _key_series(frame, [c.name for c in spec.columns], spec)
    return pd.Series([common.row_hash([v]) for v in joined.to_numpy(dtype=object)], index=frame.index, dtype=object)


def dedupe(
    con: duckdb.DuckDBPyConnection, spec: FeedSpec, resolved: pd.DataFrame
) -> tuple[pd.DataFrame, int, pd.DataFrame]:
    """Deduplicate ``resolved`` against bronze and inside the file; returns ``(new, n_identical, conflicts)``.

    Same business key, same hash: identical duplicate (skipped, counted). Same key,
    different hash: ``duplicate_conflict`` (in ``conflicts`` with ``row_number``), the
    existing row stays. Inside one file the first occurrence wins.
    """
    empty_conf = pd.DataFrame(columns=["row_number", "reason_code", "reason_text"])
    if len(resolved) == 0:
        return resolved, 0, empty_conf
    work = resolved.copy()
    work["row_hash"] = row_hashes(spec, work)
    key_cols = list(spec.business_key)
    work["_key"] = _key_series(work, key_cols, spec)

    # inside the file: first occurrence wins
    dup_in_file = work.duplicated(subset="_key", keep="first")
    first_hash = work.loc[~dup_in_file].set_index("_key")["row_hash"]
    in_file_hash = work["_key"].map(first_hash)
    ident_mask = dup_in_file & (work["row_hash"] == in_file_hash)
    conf_mask = dup_in_file & (work["row_hash"] != in_file_hash)

    # against bronze
    try:
        n_existing = con.execute(f"SELECT count(*) FROM {db._q(spec.bronze_table)}").fetchone()[0]
    except duckdb.CatalogException:
        n_existing = 0
    if n_existing:
        keys = pd.DataFrame({"_key": work.loc[~dup_in_file, "_key"].unique()})
        expr = " || '\x1f' || ".join(f'COALESCE(CAST(t."{c}" AS VARCHAR), \'\')' for c in key_cols)
        con.register("_restwert_dedupe_keys", keys)
        try:
            existing = con.execute(
                f'SELECT k."_key" AS "_key", t."row_hash" AS "existing_hash" FROM {db._q(spec.bronze_table)} t '
                f'JOIN _restwert_dedupe_keys k ON ({expr}) = k."_key"'
            ).df()
        finally:
            con.unregister("_restwert_dedupe_keys")
        if len(existing):
            existing_hash = work["_key"].map(existing.drop_duplicates("_key").set_index("_key")["existing_hash"])
            has_existing = existing_hash.notna() & ~dup_in_file
            ident_mask |= has_existing & (work["row_hash"] == existing_hash)
            conf_mask |= has_existing & (work["row_hash"] != existing_hash)

    conflicts = pd.DataFrame({
        "row_number": work.loc[conf_mask, "row_number"].to_numpy(),
        "reason_code": "duplicate_conflict",
        "reason_text": "duplicate_conflict(" + ", ".join(key_cols) + "): same key, different content; existing row kept",
    }) if conf_mask.any() else empty_conf
    new = work.loc[~ident_mask & ~conf_mask].drop(columns=["_key"]).reset_index(drop=True)
    return new, int(ident_mask.sum()), conflicts


# --------------------------------------------------------------------------- one file


def _native(frame: pd.DataFrame, extra: dict | None = None) -> pd.DataFrame:
    """Plain numpy dtypes where possible (so ``db.write_df`` takes its vectorised paths), built in one pass."""
    cols: dict[str, object] = {}
    n = len(frame)
    for c in frame.columns:
        s = frame[c]
        if isinstance(s.dtype, pd.Int64Dtype):
            cols[c] = s.astype("float64")
        elif s.dtype == object:
            arr = s.to_numpy(dtype=object)
            if n and all(isinstance(v, (bool, np.bool_)) for v in arr):
                cols[c] = arr.astype(bool)
            else:
                cols[c] = s
        else:
            cols[c] = s
    for k, v in (extra or {}).items():
        cols[k] = pd.Series([v] * n, index=frame.index)
    return pd.DataFrame(cols, index=frame.index)



def _raw_row_json(df: pd.DataFrame, row_numbers: np.ndarray) -> list[str]:
    rows = df.iloc[row_numbers - 1].astype(object).where(df.iloc[row_numbers - 1].notna(), None)
    return [json.dumps(r, sort_keys=True, default=str) for r in rows.to_dict("records")]


def _key_json(df: pd.DataFrame, spec: FeedSpec, row_numbers: np.ndarray) -> list[str]:
    cols = [c for c in spec.business_key if c in df.columns]
    if not cols:
        return ["{}"] * len(row_numbers)
    rows = df.iloc[row_numbers - 1][cols].astype(object)
    rows = rows.where(rows.notna(), None)
    return [json.dumps(r, sort_keys=True, default=str) for r in rows.to_dict("records")]


def _stored_report(row: tuple, feed_key: str, dry_run: bool, seconds: float) -> IngestReport:
    (delivery_id, source_system, feed, source_file, sha256, delivered_on, rows_read, rows_typed, rows_new,
     dup_ident, dup_conf, n_unres, reasons_json) = row
    reasons = {k: int(v) for k, v in json.loads(reasons_json).items() if k != "duplicate_conflict"}
    return IngestReport(
        delivery_id=delivery_id, feed=feed_key, source_system=source_system, source_file=source_file, sha256=sha256,
        delivered_on=common.to_date(delivered_on), rows_read=int(rows_read), rows_typed=int(rows_typed),
        rows_new=int(rows_new), duplicates_identical=int(dup_ident), duplicates_conflict=int(dup_conf),
        unresolved=reasons, notes=["already ingested (same sha256); nothing written"], already_ingested=True,
        dry_run=dry_run, seconds=seconds,
    )


def ingest_file(
    con: duckdb.DuckDBPyConnection,
    feed_key: str,
    path: Path,
    *,
    dry_run: bool = False,
    ingested_at: datetime | None = None,
) -> IngestReport:
    """Ingest one landing file of ``feed_key`` into bronze inside one transaction (see the module docstring)."""
    t0 = time.perf_counter()
    if feed_key not in FEEDS:
        raise ValueError(f"unknown feed key {feed_key!r}; known: {', '.join(INGEST_ORDER)}")
    spec = FEEDS[feed_key]
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"landing file not found: {path}")
    stamp = ingested_at or datetime.now(timezone.utc).replace(tzinfo=None)

    if not db.table_exists(con, "bronze.deliveries") or not db.table_exists(con, spec.bronze_table):
        create_lake_schema(con, drop_layers=())
    sha = _file_sha256(path)
    row = con.execute(
        "SELECT delivery_id, source_system, feed, source_file, sha256, delivered_on, rows_read, rows_typed, rows_new, "
        "duplicates_identical, duplicates_conflict, n_unresolved, reasons_json FROM bronze.deliveries WHERE sha256 = ?",
        [sha],
    ).fetchone()
    if row is not None:
        return _stored_report(row, feed_key, dry_run, time.perf_counter() - t0)

    df, is_synthetic = read_landing_file(path)
    if spec.is_reference and is_synthetic:
        raise ValueError(f"{path.name}: feed {feed_key} is a public reference copy and must carry is_synthetic=false")
    notes: list[str] = []
    try:
        delivered_on, _, _ = parse_landing_name(path.name)
    except ValueError:
        delivered_on = date.today()
        notes.append("file name not in <YYYY-MM-DD>_<feed>_<seq>.csv form; delivered_on = today")
    did = common.delivery_id(sha)

    con.begin()
    try:
        typed, rejected = type_rows(spec, df)
        resolved, unresolved, soft_notes = resolve_keys(con, spec, typed)
        notes.extend(soft_notes)
        new, n_identical, conflicts = dedupe(con, spec, resolved)

        if len(new):
            out = _native(new, {"delivery_id": did, "source_file": path.name, "ingested_at": stamp, "is_synthetic": bool(is_synthetic)})
            db.write_df(con, spec.bronze_table, out, mode="append")

        problem_frames = [f for f in (rejected, unresolved, conflicts) if len(f)]
        reason_counts: dict[str, int] = {}
        if problem_frames:
            problems = pd.concat(problem_frames, ignore_index=True)
            problems["row_number"] = problems["row_number"].astype("int64")
            rn = problems["row_number"].to_numpy()
            problems["unresolved_id"] = [
                hashlib.sha1(f"{did}|{r}|{c}".encode("utf-8")).hexdigest()[:24]
                for r, c in zip(rn, problems["reason_code"])
            ]
            problems["delivery_id"] = did
            problems["source_system"] = spec.source_system
            problems["feed"] = spec.feed
            problems["source_file"] = path.name
            problems["key_json"] = _key_json(df, spec, rn)
            problems["row_json"] = _raw_row_json(df, rn)
            problems["ingested_at"] = stamp
            problems["is_synthetic"] = bool(is_synthetic)
            db.write_df(con, "bronze.unresolved", problems, mode="append")
            reason_counts = {k: int(v) for k, v in problems["reason_code"].value_counts().items()}

        unresolved_counts = {k: v for k, v in reason_counts.items() if k != "duplicate_conflict"}
        n_conf = int(len(conflicts))
        con.execute(
            "INSERT INTO bronze.deliveries (delivery_id, source_system, feed, source_file, sha256, delivered_on, ingested_at, "
            "rows_read, rows_typed, rows_new, duplicates_identical, duplicates_conflict, n_unresolved, reasons_json, is_synthetic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [did, spec.source_system, spec.feed, path.name, sha, delivered_on, stamp, int(len(df)), int(len(typed)),
             int(len(new)), int(n_identical), n_conf, int(sum(unresolved_counts.values())),
             json.dumps(reason_counts, sort_keys=True), bool(is_synthetic)],
        )
        if dry_run:
            con.rollback()
        else:
            con.commit()
    except Exception:
        con.rollback()
        raise

    return IngestReport(
        delivery_id=did, feed=feed_key, source_system=spec.source_system, source_file=path.name, sha256=sha,
        delivered_on=delivered_on, rows_read=int(len(df)), rows_typed=int(len(typed)), rows_new=int(len(new)),
        duplicates_identical=int(n_identical), duplicates_conflict=n_conf, unresolved=unresolved_counts,
        notes=notes, already_ingested=False, dry_run=dry_run, seconds=time.perf_counter() - t0,
    )


# --------------------------------------------------------------------------- many files


def landing_files(raw_dir: Path) -> list[tuple[str, Path]]:
    """Every landing file under ``raw_dir`` as ``(feed key, path)`` in ingest order, then by file name."""
    raw_dir = Path(raw_dir)
    out: list[tuple[str, Path]] = []
    if not raw_dir.is_dir():
        return out
    for key in INGEST_ORDER:
        spec = FEEDS[key]
        folder = raw_dir / spec.source_system / spec.feed
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.csv"), key=lambda x: x.name):
            if p.name.startswith("_"):
                continue
            out.append((key, p))
    return out


def ingest_summary_frame(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """``gold.ingest_summary`` rows: one per feed (every feed, zeros when nothing landed yet)."""
    rows = []
    deliveries = (
        db.read_df(con, "SELECT source_system, feed, delivered_on, rows_read, rows_new, duplicates_identical, "
                        "duplicates_conflict, n_unresolved FROM bronze.deliveries")
        if db.table_exists(con, "bronze.deliveries") else pd.DataFrame()
    )
    for key, spec in FEEDS.items():
        d = deliveries[(deliveries["source_system"] == spec.source_system) & (deliveries["feed"] == spec.feed)] if len(deliveries) else pd.DataFrame()
        bronze_rows = con.execute(f"SELECT count(*) FROM {db._q(spec.bronze_table)}").fetchone()[0] if db.table_exists(con, spec.bronze_table) else 0
        rows.append({
            "feed": key, "source_system": spec.source_system, "delivering_system": spec.delivering_system,
            "n_files": int(len(d)),
            "last_delivered_on": (common.to_date(d["delivered_on"].max()) if len(d) else None),
            "rows_read": int(d["rows_read"].sum()) if len(d) else 0,
            "rows_new": int(d["rows_new"].sum()) if len(d) else 0,
            "duplicates_identical": int(d["duplicates_identical"].sum()) if len(d) else 0,
            "duplicates_conflict": int(d["duplicates_conflict"].sum()) if len(d) else 0,
            "n_unresolved": int(d["n_unresolved"].sum()) if len(d) else 0,
            "bronze_rows": int(bronze_rows), "as_of": as_of,
        })
    return pd.DataFrame(rows)


def ingest_all(
    con: duckdb.DuckDBPyConnection, raw_dir: Path = LAKE_RAW_DIR, *, dry_run: bool = False, as_of: date | None = None
) -> list[IngestReport]:
    """Ingest every landing file under ``raw_dir`` in feed order, print one line per file, rebuild ``gold.ingest_summary``.

    ``as_of`` is stamped on ``gold.ingest_summary`` (the run's valuation date); ``None`` means today.
    """
    reports: list[IngestReport] = []
    for key, path in landing_files(raw_dir):
        report = ingest_file(con, key, path, dry_run=dry_run)
        print(report.line())
        reports.append(report)
    if not dry_run:
        create_lake_schema(con, drop_layers=())
        db.write_df(con, "gold.ingest_summary", ingest_summary_frame(con, as_of or date.today()), mode="replace")
    return reports


def run_ingest(
    con: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    feed_key: str | None,
    path: Path | None,
    dry_run: bool,
    as_of: date | None = None,
) -> RunSummary:
    """CLI entry point: one file (``feed_key`` and ``path``) or every file under ``raw_dir``.

    Writes a ``runs`` row unless ``dry_run``; the per-file report lines are printed
    and repeated in ``notes``. ``as_of`` (the configured valuation date) is stamped on
    the ``runs`` row and on ``gold.ingest_summary``; ``None`` means today.
    """
    started = datetime.now(timezone.utc)
    stamp = as_of or date.today()
    db.create_schema(con)
    create_lake_schema(con, drop_layers=())
    run_id = "ingest-dry-run" if dry_run else db.new_run(con, "ingest", None, stamp, None)
    if feed_key is not None and path is not None:
        report = ingest_file(con, feed_key, Path(path), dry_run=dry_run)
        print(report.line())
        reports = [report]
        if not dry_run:
            db.write_df(con, "gold.ingest_summary", ingest_summary_frame(con, stamp), mode="replace")
    elif feed_key is None and path is None:
        reports = ingest_all(con, Path(raw_dir), dry_run=dry_run, as_of=stamp)
    else:
        raise ValueError("run_ingest needs both feed_key and path, or neither (ingest everything under raw_dir)")
    reasons: dict[str, int] = {}
    for r in reports:
        for k, v in r.unresolved.items():
            reasons[k] = reasons.get(k, 0) + int(v)
    counts = {
        "files": len(reports),
        "files_already_ingested": sum(1 for r in reports if r.already_ingested),
        "rows_read": sum(r.rows_read for r in reports),
        "rows_new": sum(r.rows_new for r in reports),
        "duplicates_identical": sum(r.duplicates_identical for r in reports),
        "duplicates_conflict": sum(r.duplicates_conflict for r in reports),
        "unresolved": sum(r.n_unresolved for r in reports),
        **{f"unresolved_{k}": v for k, v in sorted(reasons.items())},
    }
    notes = [r.line() for r in reports]
    if dry_run:
        notes.append("dry run: nothing written, no runs row")
    else:
        db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="ingest", run_id=run_id, started_at=started, finished_at=finished,
        seconds=(finished - started).total_seconds(), counts=counts, notes=notes,
    )


__all__ = [
    "IngestReport",
    "read_landing_file",
    "type_rows",
    "resolve_keys",
    "dedupe",
    "row_hashes",
    "ingest_file",
    "landing_files",
    "ingest_all",
    "ingest_summary_frame",
    "run_ingest",
]
