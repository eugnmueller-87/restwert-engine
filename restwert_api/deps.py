"""FastAPI-Abhängigkeiten: Einstellungen aus dem App-Zustand, Schlüsselprüfung, Datenbankzugriff.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Antwortcodes der Schlüsselprüfung, fest und getestet:

* 503: kein einziger Schlüssel konfiguriert (fail closed, nie ein stiller Durchlass)
* 401: Header ``X-API-Key`` fehlt oder passt zu keinem Schlüssel
* 403: Schlüssel gültig, aber nicht für dieses Quellsystem oder nicht für Läufe
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import Header, HTTPException, Request

from restwert import db
from restwert.lake.feeds import FeedSpec

from restwert_api.auth import HEADER_NAME, ApiKey, KeyRing
from restwert_api.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_keyring(request: Request) -> KeyRing:
    return request.app.state.keyring


def require_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=HEADER_NAME, description="das Geheimnis des Schlüssels")] = None,
) -> ApiKey:
    ring = get_keyring(request)
    if len(ring) == 0:
        raise HTTPException(
            status_code=503,
            detail="keine API-Schlüssel konfiguriert (config/api_keys.yaml oder RESTWERT_API_KEYS); die Schnittstelle nimmt nichts an",
        )
    if not x_api_key:
        raise HTTPException(status_code=401, detail=f"Header {HEADER_NAME} fehlt")
    key = ring.authenticate(x_api_key)
    if key is None:
        raise HTTPException(status_code=401, detail="API-Schlüssel unbekannt")
    request.state.audit["key_id"] = key.key_id
    return key


def require_system(key: ApiKey, spec: FeedSpec) -> None:
    if not key.allows_system(spec.source_system):
        raise HTTPException(
            status_code=403,
            detail=f"Schlüssel {key.key_id!r} darf nicht für das Quellsystem {spec.source_system!r} liefern",
        )


def require_run(key: ApiKey) -> None:
    if not key.may_run:
        raise HTTPException(status_code=403, detail=f"Schlüssel {key.key_id!r} darf keine Läufe auslösen (may_run fehlt)")


def db_missing(settings: Settings) -> bool:
    p = Path(settings.db_path)
    return str(p) != ":memory:" and not p.exists()


def open_read(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Verbindung für lesende Routen; ohne Datei ist es 404, nie eine leere Datei nebenbei."""
    if db_missing(settings):
        raise HTTPException(
            status_code=404,
            detail=f"Datenbank {settings.db_path} existiert noch nicht; erst eine Lieferung landen oder einen Lauf starten",
        )
    return db.connect(Path(settings.db_path))


__all__ = ["get_settings", "get_keyring", "require_key", "require_system", "require_run", "db_missing", "open_read"]
