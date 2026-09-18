"""Vom Request zur Landing-Datei und durch den Import: der Kern von ``POST /v1/feeds/<system>/<feed>``.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Regeln, die hier durchgesetzt werden (alle aus ``restwert/lake``):

* Der Feed-Vertrag ist ``restwert.lake.feeds.FEEDS``; ein unbekannter Feed ist
  404, eine unbekannte Spalte 422. Es gibt keine zweite Spaltenliste.
* Die Landing-Datei heißt ``<YYYY-MM-DD>_<feed>_<seq:03d>.csv`` und liegt unter
  ``<raw>/<system>/<feed>/``; Name und Pfad kommen aus ``landing_file_name`` und
  ``landing_path``. Die Sequenznummer wird atomar vergeben (``open(..., "x")``
  unter einer Sperre), damit zwei parallele Lieferungen nie dieselbe Datei
  treffen. Eine gelandete Datei wird nie geändert.
* Keine ``#``-Zeile: die kennzeichnet generierte oder öffentliche Daten und macht
  eine Datei für ``all --wipe-raw`` löschbar. Eine echte Lieferung hat sie nicht.
* ``is_synthetic`` steht auf jeder Zeile und ist ``false``. Schickt der Aufrufer
  die Spalte mit einem anderen Wert, ist das 422.
* Die Identität einer Lieferung ist der SHA-256 der Datei. Er wird VOR dem
  Schreiben berechnet und gegen ``bronze.deliveries`` geprüft: dieselbe Lieferung
  ein zweites Mal legt keine zweite Datei an und meldet ``already_ingested``.
* Eine Lieferung hat höchstens ``max_rows`` Zeilen (``Settings.max_rows``, JSON wie
  CSV). Darüber ist ``TooManyRows`` (413), geprüft VOR jeder weiteren Arbeit an den
  Zeilen; beim CSV bricht der Leser bei der ersten Zeile über der Grenze ab. Die
  Größe des Körpers in Bytes prüft die Route (``routes/feeds.py``), bevor sie ihn
  vollständig liest.
* ``dry_run=true`` schreibt keine Datei und nichts in die Datenbank. Die Vorschau
  läuft über dieselben Funktionen, die der Import benutzt (``type_rows``,
  ``resolve_keys``, ``dedupe``), und ihre Grundcodes sind die geschlossene Liste
  ``UNRESOLVED_REASONS``. Das ist eine Erweiterung der CLI-Bedeutung von
  ``--dry-run`` (dort wird nur die Transaktion zurückgerollt; die Datei liegt
  schon): über die API gibt es die Datei ohne Import nicht, weil eine Datei in
  der Landung eine Lieferung ist.
"""

from __future__ import annotations

import csv
import hashlib
import io
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from restwert import db
from restwert.lake import ingest as lake_ingest
from restwert.lake.feeds import FEEDS, UNRESOLVED_REASONS, FeedSpec, landing_path, parse_landing_name
from restwert.lake.schema_lake import create_lake_schema

PREVIEW_LIMIT = 20
_TRUE = {"true", "t", "1", "yes", "y"}
_FALSE = {"false", "f", "0", "no", "n"}

# kurze Feed-Namen, wie die Bronze-Tabelle ohne Schema: "sd_tickets" -> "servicedesk/tickets"
SHORT_KEYS: dict[str, str] = {spec.bronze_table.split(".", 1)[1]: key for key, spec in FEEDS.items()}

# ------------------------------------------------------------------------------------ Feed-Auflösung


class UnknownFeed(KeyError):
    pass


class BadDelivery(ValueError):
    """Ein Request, der nicht zum Vertrag passt (422); der Text sagt, was."""


class TooManyRows(BadDelivery):
    """Mehr Zeilen als ``max_rows`` erlaubt (413); in Teilen liefern."""


def check_row_count(n: int, max_rows: int | None) -> None:
    """``TooManyRows``, wenn ``n`` über ``max_rows`` liegt; ``None`` heißt keine Grenze (nur in Tests des Kerns)."""
    if max_rows is not None and n > max_rows:
        raise TooManyRows(
            f"{n} Zeilen, erlaubt sind höchstens {max_rows} je Lieferung; in Teilen liefern "
            "(die Deduplizierung per SHA-256 und row_hash macht Teile gefahrlos)"
        )


def resolve_feed(text: str) -> FeedSpec:
    """``"servicedesk/tickets"`` oder ``"sd_tickets"`` -> FeedSpec; sonst ``UnknownFeed``."""
    key = text.strip().strip("/")
    if key in FEEDS:
        return FEEDS[key]
    if key in SHORT_KEYS:
        return FEEDS[SHORT_KEYS[key]]
    raise UnknownFeed(
        f"unbekannter Feed {text!r}; bekannt: " + ", ".join(f"{k} ({s.bronze_table.split('.', 1)[1]})" for k, s in FEEDS.items())
    )


def short_key(spec: FeedSpec) -> str:
    return spec.bronze_table.split(".", 1)[1]


# ------------------------------------------------------------------------------------ Zeilen aus dem Request


def _check_columns(spec: FeedSpec, columns: list[str]) -> None:
    allowed = set(spec.column_names) | {"is_synthetic"}
    unknown = sorted(c for c in columns if c not in allowed)
    if unknown:
        raise BadDelivery(
            f"Feed {spec.key}: unbekannte Spalten {unknown}; der Vertrag kennt {list(spec.column_names)} und is_synthetic"
        )
    missing = [c for c in spec.columns if c.required and c.name not in columns]
    if missing and columns:
        # eine fehlende Pflichtspalte ist kein 422: der Import meldet sie je Zeile als missing_required.
        # Fehlt aber JEDE Pflichtspalte, ist der Request offensichtlich der falsche Feed.
        if len(missing) == sum(1 for c in spec.columns if c.required):
            raise BadDelivery(
                f"Feed {spec.key}: keine einzige Pflichtspalte vorhanden ({[c.name for c in missing]}); falscher Feed?"
            )


def _synthetic_value_ok(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value is False
    text = str(value).strip().lower()
    return text == "" or text in _FALSE


def rows_from_json(spec: FeedSpec, rows: list[dict[str, Any]], *, max_rows: int | None = None) -> list[dict[str, Any]]:
    """JSON-Zeilen prüfen: Anzahl, Objekte, bekannte Spalten, ``is_synthetic`` nur ``false``. Werte bleiben, wie sie kamen."""
    if not isinstance(rows, list):
        raise BadDelivery("rows muss eine Liste von Objekten sein")
    if not rows:
        raise BadDelivery("rows ist leer; eine Lieferung ohne Zeilen wird nicht gelandet")
    check_row_count(len(rows), max_rows)
    columns: set[str] = set()
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise BadDelivery(f"rows[{i}] ist kein Objekt")
        columns.update(row.keys())
        if not _synthetic_value_ok(row.get("is_synthetic")):
            raise BadDelivery(f"rows[{i}]: is_synthetic muss false sein; eine Lieferung über die API ist echt")
    _check_columns(spec, sorted(columns))
    return [{k: v for k, v in row.items() if k != "is_synthetic"} for row in rows]


def rows_from_csv(spec: FeedSpec, text: str, *, max_rows: int | None = None) -> list[dict[str, Any]]:
    """CSV-Text prüfen (Kopfzeile, keine ``#``-Zeile, bekannte Spalten, höchstens ``max_rows``) und als Zeilen zurückgeben."""
    if text.startswith("﻿"):
        text = text[1:]
    stripped = text.lstrip()
    if stripped.startswith("#"):
        raise BadDelivery("die erste Zeile beginnt mit '#'; eine Lieferung über die API trägt keine Kennzeichnungszeile")
    if not stripped:
        raise BadDelivery("leerer CSV-Körper")
    reader = csv.DictReader(io.StringIO(text))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    if not columns or columns == [""]:
        raise BadDelivery("CSV ohne Kopfzeile")
    _check_columns(spec, columns)
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(reader, start=1):
        check_row_count(i, max_rows)
        row = {k.strip(): (v if v is not None else "") for k, v in raw.items() if k is not None}
        if None in raw:
            raise BadDelivery(f"Zeile {i}: mehr Werte als Spalten")
        if not _synthetic_value_ok(row.get("is_synthetic")):
            raise BadDelivery(f"Zeile {i}: is_synthetic muss false sein; eine Lieferung über die API ist echt")
        row.pop("is_synthetic", None)
        rows.append(row)
    if not rows:
        raise BadDelivery("CSV ohne Datenzeilen; eine Lieferung ohne Zeilen wird nicht gelandet")
    return rows


# ------------------------------------------------------------------------------------ Landing-Text


def _cell(value: Any) -> str:
    """Wert als Landing-Text: ISO-Datum, ``true``/``false``, Dezimalpunkt, leer für fehlend."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return repr(value) if value % 1 else str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def render_landing_csv(spec: FeedSpec, rows: list[dict[str, Any]]) -> str:
    """Kopf in Vertragsreihenfolge plus ``is_synthetic=false``; LF; UTF-8 ohne BOM; keine ``#``-Zeile."""
    columns = list(spec.column_names) + ["is_synthetic"]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in spec.column_names] + ["false"])
    return buf.getvalue()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------ Sequenz und Datei

_ALLOC_LOCK = threading.Lock()


def next_seq(raw_dir: Path, spec: FeedSpec, delivered_on: date) -> int:
    folder = Path(raw_dir) / spec.source_system / spec.feed
    seq = 0
    if folder.is_dir():
        for p in folder.glob("*.csv"):
            try:
                d, feed, s = parse_landing_name(p.name)
            except ValueError:
                continue
            if d == delivered_on and feed == spec.feed:
                seq = max(seq, s)
    return seq + 1


def write_landing_file(raw_dir: Path, spec: FeedSpec, delivered_on: date, text: str) -> Path:
    """Nächste freie Sequenznummer atomar belegen und die Datei schreiben; nie eine bestehende anfassen."""
    with _ALLOC_LOCK:
        seq = next_seq(raw_dir, spec, delivered_on)
        while True:
            if seq > 999:
                raise BadDelivery(f"mehr als 999 Lieferungen für {spec.key} am {delivered_on}; die Sequenz ist dreistellig")
            path = landing_path(Path(raw_dir), spec, delivered_on, seq)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                return path
            except FileExistsError:
                seq += 1


# ------------------------------------------------------------------------------------ Ergebnis


@dataclass
class DeliveryResult:
    feed: str
    short_key: str
    source_system: str
    dry_run: bool
    already_ingested: bool
    sha256: str
    delivered_on: date
    delivery_id: str | None = None
    landing_file: str | None = None
    run_id: str | None = None
    rows_read: int = 0
    rows_typed: int = 0
    rows_new: int = 0
    duplicates_identical: int = 0
    duplicates_conflict: int = 0
    unresolved_by_reason: dict[str, int] = field(default_factory=dict)
    unresolved_preview: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def n_unresolved(self) -> int:
        return int(sum(self.unresolved_by_reason.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed": self.feed, "short_key": self.short_key, "source_system": self.source_system,
            "dry_run": self.dry_run, "already_ingested": self.already_ingested, "sha256": self.sha256,
            "delivered_on": self.delivered_on.isoformat(), "delivery_id": self.delivery_id,
            "landing_file": self.landing_file, "run_id": self.run_id,
            "rows_read": self.rows_read, "rows_typed": self.rows_typed, "rows_new": self.rows_new,
            "duplicates_identical": self.duplicates_identical, "duplicates_conflict": self.duplicates_conflict,
            "n_unresolved": self.n_unresolved, "unresolved_by_reason": dict(sorted(self.unresolved_by_reason.items())),
            "unresolved_preview": self.unresolved_preview, "notes": self.notes, "seconds": round(self.seconds, 3),
        }


def _stored_delivery(con: duckdb.DuckDBPyConnection, sha: str) -> tuple | None:
    if not db.table_exists(con, "bronze.deliveries"):
        return None
    return con.execute(
        "SELECT delivery_id, source_system, feed, source_file, sha256, delivered_on, rows_read, rows_typed, rows_new, "
        "duplicates_identical, duplicates_conflict, n_unresolved, reasons_json FROM bronze.deliveries WHERE sha256 = ?",
        [sha],
    ).fetchone()


def _preview_from_db(con: duckdb.DuckDBPyConnection, delivery_id: str) -> list[dict[str, Any]]:
    if not db.table_exists(con, "bronze.unresolved"):
        return []
    rows = con.execute(
        "SELECT row_number, reason_code, reason_text FROM bronze.unresolved WHERE delivery_id = ? "
        "ORDER BY row_number LIMIT ?",
        [delivery_id, PREVIEW_LIMIT],
    ).fetchall()
    return [{"row_number": int(r), "reason_code": c, "reason_text": t} for r, c, t in rows]


def _preview_from_frames(frames: list[pd.DataFrame]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    present = [f for f in frames if len(f)]
    if not present:
        return {}, []
    problems = pd.concat(present, ignore_index=True).sort_values("row_number", kind="stable")
    codes = set(problems["reason_code"].unique().tolist())
    foreign = codes - set(UNRESOLVED_REASONS)
    assert not foreign, f"Grundcode außerhalb der geschlossenen Liste: {foreign}"
    counts = {str(k): int(v) for k, v in problems["reason_code"].value_counts().items()}
    head = problems.head(PREVIEW_LIMIT)
    preview = [
        {"row_number": int(r), "reason_code": str(c), "reason_text": str(t)}
        for r, c, t in zip(head["row_number"], head["reason_code"], head["reason_text"])
    ]
    return counts, preview


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    db.create_schema(con)
    create_lake_schema(con, drop_layers=())


def deliver(
    con: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    spec: FeedSpec,
    rows: list[dict[str, Any]],
    *,
    delivered_on: date,
    dry_run: bool,
    as_of: date | None = None,
) -> DeliveryResult:
    """Eine Lieferung landen und importieren (oder nur vorschauen); siehe Moduldokumentation."""
    t0 = time.perf_counter()
    text = render_landing_csv(spec, rows)
    sha = sha256_text(text)
    result = DeliveryResult(
        feed=spec.key, short_key=short_key(spec), source_system=spec.source_system, dry_run=dry_run,
        already_ingested=False, sha256=sha, delivered_on=delivered_on,
    )
    _ensure_schema(con)

    stored = _stored_delivery(con, sha)
    if stored is not None:
        report = lake_ingest._stored_report(stored, spec.key, dry_run, 0.0)
        result.already_ingested = True
        result.delivery_id = report.delivery_id
        result.landing_file = report.source_file
        result.delivered_on = report.delivered_on
        result.rows_read, result.rows_typed, result.rows_new = report.rows_read, report.rows_typed, report.rows_new
        result.duplicates_identical, result.duplicates_conflict = report.duplicates_identical, report.duplicates_conflict
        result.unresolved_by_reason = dict(report.unresolved)
        result.unresolved_preview = _preview_from_db(con, report.delivery_id)
        result.notes = list(report.notes) + ["keine neue Datei angelegt: derselbe Inhalt (SHA-256) liegt schon in bronze.deliveries"]
        result.seconds = time.perf_counter() - t0
        return result

    if dry_run:
        df = pd.read_csv(io.StringIO(text), dtype=object, keep_default_na=True)
        typed, rejected = lake_ingest.type_rows(spec, df)
        resolved, unresolved, soft_notes = lake_ingest.resolve_keys(con, spec, typed)
        new, n_identical, conflicts = lake_ingest.dedupe(con, spec, resolved)
        counts, preview = _preview_from_frames([rejected, unresolved, conflicts])
        result.rows_read, result.rows_typed, result.rows_new = int(len(df)), int(len(typed)), int(len(new))
        result.duplicates_identical, result.duplicates_conflict = int(n_identical), int(len(conflicts))
        result.unresolved_by_reason = {k: v for k, v in counts.items() if k != "duplicate_conflict"}
        result.unresolved_preview = preview
        result.notes = list(soft_notes) + ["dry run: keine Landing-Datei geschrieben, nichts importiert"]
        result.seconds = time.perf_counter() - t0
        return result

    path = write_landing_file(raw_dir, spec, delivered_on, text)
    stamp = as_of or date.today()
    run_id = db.new_run(con, "ingest", None, stamp, None)
    report = lake_ingest.ingest_file(con, spec.key, path)
    db.write_df(con, "gold.ingest_summary", lake_ingest.ingest_summary_frame(con, stamp), mode="replace")
    counts = {
        "files": 1, "rows_read": report.rows_read, "rows_new": report.rows_new,
        "duplicates_identical": report.duplicates_identical, "duplicates_conflict": report.duplicates_conflict,
        "unresolved": report.n_unresolved, **{f"unresolved_{k}": v for k, v in sorted(report.unresolved.items())},
    }
    db.finish_run(con, run_id, counts)
    result.delivery_id = report.delivery_id
    result.landing_file = str(path.relative_to(Path(raw_dir))).replace("\\", "/")
    result.run_id = run_id
    result.rows_read, result.rows_typed, result.rows_new = report.rows_read, report.rows_typed, report.rows_new
    result.duplicates_identical, result.duplicates_conflict = report.duplicates_identical, report.duplicates_conflict
    result.unresolved_by_reason = dict(report.unresolved)
    result.unresolved_preview = _preview_from_db(con, report.delivery_id)
    result.notes = list(report.notes)
    result.seconds = time.perf_counter() - t0
    return result


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "SHORT_KEYS", "PREVIEW_LIMIT", "UnknownFeed", "BadDelivery", "TooManyRows", "DeliveryResult",
    "check_row_count", "resolve_feed", "short_key", "rows_from_json", "rows_from_csv", "render_landing_csv", "sha256_text",
    "next_seq", "write_landing_file", "deliver",
]
