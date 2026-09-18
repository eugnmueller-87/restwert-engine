"""Audit-Log: eine JSON-Zeile je Aufruf, angehängt, nie umgeschrieben.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Felder je Zeile: ``ts`` (UTC, ISO), ``method``, ``path``, ``status``, ``ms``,
``key_id`` (die Kennung, nie das Geheimnis), ``client``, und je nach Route
``source_system``, ``feed``, ``delivery_id``, ``run_id``, ``rows_read``,
``rows_new``, ``n_unresolved``, ``dry_run``, ``error``. Die Routen legen ihre
Felder in ``request.state.audit`` ab; die Middleware in ``app.py`` schreibt die
Zeile, wenn die Antwort steht. Ablage: ``Settings.audit_path`` (Standard
``data/api_audit.jsonl``, per ``.gitignore`` ausgeschlossen).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record},
                          sort_keys=True, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """Für Tests und die Nachtwache: jede Zeile als Dict."""
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


__all__ = ["AuditLog"]
