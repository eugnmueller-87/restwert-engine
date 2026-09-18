"""Läufe über die Schnittstelle: die Kette ``ingest`` bis ``export`` zum Stichtag, seriell, mit Zustand je Lauf.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Was ein Lauf ist: ``ALL_ORDER_V2`` aus ``restwert/cli.py`` ohne den ersten Schritt
``generate-lake``. Die Schnittstelle ruft dieselben Entry-Points wie ``cmd_all``
(``run_ingest``, ``run_conform``, ``run_forecast``, ``run_pnl``, ``run_timeline``,
``run_ledger``, ``run_levers``, ``run_all_decisions``, ``run_contracts`` und v2,
``run_kpis`` und Gold, ``export_all`` plus ``export_lake`` plus ``write_manifest``),
und zwar direkt im Prozess, nie ``python -m restwert all`` per ``subprocess``.

Was ein Lauf nie tut: die Datenbankdatei löschen, die Landung leeren, die
synthetische Flotte erzeugen, die generierten Dokumente unter ``docs/`` neu
schreiben. Das ist die ``--keep-db``-Bedeutung von ``all``, fest verdrahtet, weil
eine echte Lieferung über die API sonst beim nächsten Lauf verschwände.

Warum seriell: ``data/restwert.duckdb`` ist EINE Datei, und weder ``db.connect``
noch ``ingest_file`` kennen einen zweiten Schreiber. ``DB_LOCK`` hält alle
Schreiber der Schnittstelle in einer Reihe: jede Lieferung (kurz) und jeder Lauf
(die ganze Kette). Läufe warten in einer Warteschlange und laufen in einem
Arbeitsfaden nacheinander; ``GET /v1/runs/<id>`` zeigt Position, Schritte und
Fehler. Der Zustand liegt als JSON-Datei je Lauf unter ``Settings.runs_dir``
(Standard ``data/api_runs/``, per ``.gitignore`` ausgeschlossen), damit er einen
Neustart des Servers überlebt; ein Lauf, der beim Neustart ``running`` war, wird
beim Start als ``failed`` mit Grund markiert, nie still wiederholt.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from restwert import cli as engine_cli
from restwert import db
from restwert.cli import ALL_ORDER_V2
from restwert.paths import LAKE_CONFIG

from restwert_api.settings import Settings

STEPS: tuple[str, ...] = tuple(s for s in ALL_ORDER_V2 if s != "generate-lake")
assert STEPS == ("ingest", "conform", "forecast", "pnl", "timeline", "ledger", "levers", "decide", "contracts", "kpis", "export")

DB_LOCK = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunRecord:
    run_id: str
    as_of: date
    steps: list[str]
    key_id: str
    status: str = "queued"
    requested_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    traceback: str | None = None

    @property
    def seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["seconds"] = self.seconds
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunRecord":
        def _dt(v: Any) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            run_id=d["run_id"], as_of=date.fromisoformat(d["as_of"]), steps=list(d["steps"]), key_id=d.get("key_id", ""),
            status=d.get("status", "queued"), requested_at=_dt(d.get("requested_at")) or _now(),
            started_at=_dt(d.get("started_at")), finished_at=_dt(d.get("finished_at")),
            results=list(d.get("results") or []), error=d.get("error"), traceback=d.get("traceback"),
        )


def new_run_id() -> str:
    return f"api-{_now():%Y%m%d%H%M%S}-{uuid4().hex[:4]}"


def validate_steps(steps: list[str] | None) -> list[str]:
    """Teilmenge von ``STEPS`` in Kettenreihenfolge; ``generate-lake`` und Unbekanntes sind ein Fehler."""
    if not steps:
        return list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise ValueError(f"unbekannte oder nicht erlaubte Schritte {unknown}; erlaubt in dieser Reihenfolge: {', '.join(STEPS)}")
    if len(set(steps)) != len(steps):
        raise ValueError("ein Schritt ist doppelt genannt")
    return [s for s in STEPS if s in steps]


class RunStore:
    """Eine JSON-Datei je Lauf; ``list`` sortiert nach Anforderungszeit, jüngster zuerst."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> Path:
        return self.folder / f"{run_id}.json"

    def save(self, record: RunRecord) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self._path(record.run_id).with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(record.to_dict(), fh, indent=2, sort_keys=True, default=str, ensure_ascii=False)
            tmp.replace(self._path(record.run_id))

    def load(self, run_id: str) -> RunRecord | None:
        p = self._path(run_id)
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as fh:
            return RunRecord.from_dict(json.load(fh))

    def list(self, limit: int = 50) -> list[RunRecord]:
        if not self.folder.is_dir():
            return []
        records = []
        for p in self.folder.glob("api-*.json"):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    records.append(RunRecord.from_dict(json.load(fh)))
            except (OSError, ValueError, KeyError):
                continue
        records.sort(key=lambda r: r.requested_at, reverse=True)
        return records[:limit]

    def mark_interrupted(self) -> int:
        """Beim Start: was ``running`` oder ``queued`` war, ist es nicht mehr; als ``failed`` markieren, nie wiederholen."""
        n = 0
        for rec in self.list(limit=10_000):
            if rec.status in ("running", "queued"):
                rec.status = "failed"
                rec.finished_at = _now()
                rec.error = "Server neu gestartet, bevor der Lauf beendet war; nicht wiederholt"
                self.save(rec)
                n += 1
        return n


# ---------------------------------------------------------------------------------------- die Kette


def _build_steps(con, settings: Settings, as_of: date) -> dict[str, Callable[[], Any]]:
    """Ein Aufruf je Schritt, wie in ``cmd_all`` (``restwert/cli.py``), mit ``docs=False`` und ohne Löschen."""
    from restwert.config import load_assumptions, load_kpi_targets, load_thresholds
    from restwert.decisions.runner import run_all_decisions
    from restwert.export import export_all, export_lake, write_manifest
    from restwert.forecast.run import run_forecast
    from restwert.lake.ingest import run_ingest
    from restwert.lakegen.config import load_lake_config
    from restwert.pnl.lifecycle import run_pnl

    a = load_assumptions()
    thr = load_thresholds()
    targets = load_kpi_targets()
    lake_cfg = load_lake_config(LAKE_CONFIG)
    raw_dir = settings.raw_dir
    out_dir = Path(settings.out_dir)
    lake_dir = Path(settings.lake_dir)

    def _timeline() -> Any:
        from restwert.lake.timeline import run_timeline

        return run_timeline(con, as_of)

    def _ledger() -> Any:
        from restwert.ledger.run import run_ledger

        return run_ledger(con, as_of, a)

    def _export() -> dict[str, Any]:
        paths = export_all(con, out_dir, "both")
        lake_paths = export_lake(con, out_dir, lake_dir, "both")
        write_manifest(out_dir, con, engine_cli._latest_run_id(con))
        return {"files": len(paths) + 1, "lake_files": len(lake_paths)}

    return {
        "ingest": lambda: run_ingest(con, raw_dir, None, None, False, as_of=as_of),
        "conform": lambda: engine_cli._run_conform(con, as_of, a, None, None, False),
        "forecast": lambda: run_forecast(con, as_of, a, thr, lake_cfg, replay=True, backtest=True),
        "pnl": lambda: run_pnl(con, as_of, a),
        "timeline": _timeline,
        "ledger": _ledger,
        "levers": lambda: engine_cli._run_levers(con, as_of, thr, a, False),
        "decide": lambda: run_all_decisions(con, as_of, thr, a, write_docs=False),
        "contracts": lambda: engine_cli._run_contracts_v1_and_v2(con, as_of, thr),
        "kpis": lambda: engine_cli._run_kpis_v1_and_gold(con, as_of, targets, False),
        "export": _export,
    }


def execute_run(record: RunRecord, settings: Settings, store: RunStore) -> RunRecord:
    """Die Schritte des Laufs nacheinander unter ``DB_LOCK``; Zustand nach jedem Schritt sichern."""
    record.status = "running"
    record.started_at = _now()
    store.save(record)
    with DB_LOCK:
        con = db.connect(Path(settings.db_path))
        try:
            db.create_schema(con)
            steps = _build_steps(con, settings, record.as_of)
            for name in record.steps:
                t0 = time.perf_counter()
                try:
                    result = steps[name]()
                except Exception as exc:  # noqa: BLE001 - der Fehler wird gespeichert und gemeldet, nicht verschluckt
                    record.status = "failed"
                    record.error = f"{name}: {type(exc).__name__}: {exc}"
                    record.traceback = traceback.format_exc()
                    record.finished_at = _now()
                    store.save(record)
                    return record
                seconds = time.perf_counter() - t0
                counts, run_id = engine_cli._summary_counts(result)
                record.results.append({"step": name, "seconds": round(seconds, 3), "counts": counts, "run_id": run_id})
                store.save(record)
        finally:
            con.close()
    record.status = "done"
    record.finished_at = _now()
    store.save(record)
    return record


class RunWorker:
    """Ein Arbeitsfaden, eine Warteschlange; ``submit`` ist sofort zurück, ``GET /v1/runs/<id>`` zeigt den Stand."""

    def __init__(self, settings: Settings, store: RunStore) -> None:
        self.settings = settings
        self.store = store
        self._queue: "queue.Queue[RunRecord | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active: RunRecord | None = None
        self._queued: list[str] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self.store.mark_interrupted()
        self._thread = threading.Thread(target=self._loop, name="restwert-api-runs", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=timeout)
        self._thread = None

    def submit(self, record: RunRecord) -> RunRecord:
        self.store.save(record)
        with self._lock:
            self._queued.append(record.run_id)
        self._queue.put(record)
        return record

    @property
    def active_run_id(self) -> str | None:
        return self._active.run_id if self._active is not None else None

    @property
    def queued(self) -> list[str]:
        with self._lock:
            return list(self._queued)

    def queue_position(self, run_id: str) -> int | None:
        with self._lock:
            return self._queued.index(run_id) + 1 if run_id in self._queued else None

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            with self._lock:
                if item.run_id in self._queued:
                    self._queued.remove(item.run_id)
                self._active = item
            try:
                execute_run(item, self.settings, self.store)
            except Exception as exc:  # noqa: BLE001 - der Faden darf nicht sterben
                item.status = "failed"
                item.error = f"{type(exc).__name__}: {exc}"
                item.traceback = traceback.format_exc()
                item.finished_at = _now()
                self.store.save(item)
            finally:
                self._active = None


__all__ = ["STEPS", "DB_LOCK", "RunRecord", "RunStore", "RunWorker", "new_run_id", "validate_steps", "execute_run"]
