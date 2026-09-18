"""``POST /v1/runs``, ``GET /v1/runs``, ``GET /v1/runs/<id>``: Läufe anstoßen und beobachten.

Ein Lauf ist asynchron: ``POST`` antwortet 202 mit der Kennung, der Arbeitsfaden
(``restwert_api.runner``) arbeitet die Warteschlange nacheinander ab.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from restwert_api.auth import ApiKey
from restwert_api.deps import require_key, require_run
from restwert_api.runner import STEPS, RunRecord, new_run_id, validate_steps
from restwert_api.schemas import RunRequest, RunStatus

router = APIRouter(tags=["runs"])


def _status(request: Request, record: RunRecord) -> RunStatus:
    worker = request.app.state.worker
    return RunStatus(
        run_id=record.run_id, status=record.status, as_of=record.as_of, steps=record.steps, key_id=record.key_id,  # type: ignore[arg-type]
        requested_at=record.requested_at, started_at=record.started_at, finished_at=record.finished_at,
        seconds=record.seconds, results=record.results, error=record.error,  # type: ignore[arg-type]
        queue_position=worker.queue_position(record.run_id),
    )


@router.post("/runs", response_model=RunStatus, status_code=202, summary="Die Kette ingest bis export zum Stichtag anstoßen")
def post_run(request: Request, body: RunRequest, key: Annotated[ApiKey, Depends(require_key)]) -> RunStatus:
    require_run(key)
    try:
        steps = validate_steps(body.steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record = RunRecord(run_id=new_run_id(), as_of=body.as_of or date.today(), steps=steps, key_id=key.key_id)
    request.app.state.worker.submit(record)
    request.state.audit.update({"run_id": record.run_id, "as_of": record.as_of.isoformat(), "steps": ",".join(steps)})
    return _status(request, record)


@router.get("/runs", response_model=list[RunStatus], summary="Die letzten Läufe, jüngster zuerst")
def list_runs(
    request: Request, key: Annotated[ApiKey, Depends(require_key)], limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[RunStatus]:
    return [_status(request, r) for r in request.app.state.store.list(limit=limit)]


@router.get("/runs/steps", summary="Die erlaubten Schritte in Kettenreihenfolge")
def list_steps() -> dict[str, list[str]]:
    return {"steps": list(STEPS)}


@router.get("/runs/{run_id}", response_model=RunStatus, summary="Stand eines Laufs")
def get_run(request: Request, run_id: str, key: Annotated[ApiKey, Depends(require_key)]) -> RunStatus:
    record = request.app.state.store.load(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Lauf {run_id!r} unbekannt")
    return _status(request, record)
