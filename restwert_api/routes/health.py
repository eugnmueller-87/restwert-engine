"""``GET /v1/health``: lebt der Server, gibt es die Datenbankdatei, ist sie lesbar. Kein Schlüssel, kein Seiteneffekt."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from restwert import __version__, db
from restwert.lake.feeds import FEEDS

from restwert_api import API_VERSION, GOVERNANCE_PRINCIPLE
from restwert_api.schemas import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health(request: Request) -> Health:
    settings = request.app.state.settings
    worker = request.app.state.worker
    p = Path(settings.db_path)
    exists = p.exists()
    readable = False
    if exists:
        try:
            con = db.connect(p)
            try:
                con.execute("SELECT 1").fetchone()
                readable = True
            finally:
                con.close()
        except Exception:  # noqa: BLE001 - genau das ist der Befund
            readable = False
    return Health(
        status="ok" if (readable or not exists) else "degraded",
        engine_version=__version__, api_version=API_VERSION, db_path=str(p), db_exists=exists, db_readable=readable,
        raw_dir=str(settings.raw_dir), feeds=len(FEEDS), keys_configured=len(request.app.state.keyring),
        runs_queued=len(worker.queued), run_active=worker.active_run_id, governance=GOVERNANCE_PRINCIPLE,
    )
