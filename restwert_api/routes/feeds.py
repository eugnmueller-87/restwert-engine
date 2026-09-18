"""``GET /v1/feeds``, ``GET /v1/feeds/<feed>``, ``POST /v1/feeds/<feed>``: der Vertrag und die Lieferung.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``<feed>`` ist ``<system>/<feed>`` (``servicedesk/tickets``) oder der kurze Name der
Bronze-Tabelle (``sd_tickets``). Der Körper von ``POST`` ist JSON
(``{"rows": [...], "delivered_on": "YYYY-MM-DD"}`` oder eine nackte Liste) oder
``text/csv`` mit Kopfzeile. ``?dry_run=true`` schreibt nichts und zeigt die Vorschau.

Zwei Obergrenzen, beide aus ``Settings`` und beide 413: der Körper in Bytes
(``max_body_bytes``; erst ``Content-Length``, dann beim Lesen in Stücken, damit auch
ein Körper ohne Längenangabe die Grenze nicht umgeht) und die Zeilen je Lieferung
(``max_rows``, JSON wie CSV, geprüft vor der Zeilenprüfung). Beide greifen, bevor
irgendetwas gelandet wird.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from restwert import db
from restwert.lake.feeds import FEEDS

from restwert_api import landing
from restwert_api.auth import ApiKey
from restwert_api.deps import get_settings, require_key, require_system
from restwert_api.runner import DB_LOCK
from restwert_api.schemas import DeliveryBody, DeliveryResponse, FeedInfo, feed_info, row_model
from restwert_api.settings import Settings

router = APIRouter(tags=["feeds"])


@router.get("/feeds", response_model=list[FeedInfo], summary="Alle 19 Feed-Verträge, in Importreihenfolge")
def list_feeds() -> list[FeedInfo]:
    return [feed_info(spec) for spec in FEEDS.values()]


def _spec_or_404(feed_key: str):
    try:
        return landing.resolve_feed(feed_key)
    except landing.UnknownFeed as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/feeds/{feed_key:path}", response_model=FeedInfo, summary="Ein Feed-Vertrag")
def get_feed(feed_key: str) -> FeedInfo:
    return feed_info(_spec_or_404(feed_key))


def _too_large(limit: int, seen: int | None = None) -> HTTPException:
    got = f"{seen} Bytes" if seen is not None else "mehr"
    return HTTPException(
        status_code=413,
        detail=f"Körper zu groß: {got}, erlaubt sind höchstens {limit} Bytes je Lieferung; in Teilen liefern",
    )


async def read_body_limited(request: Request, limit: int) -> bytes:
    """Den Körper in Stücken lesen und bei ``limit`` abbrechen (413), nie erst alles in den Speicher holen."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            n = int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Content-Length ist keine Zahl: {declared!r}") from exc
        if n > limit:
            raise _too_large(limit, n)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def _bad(exc: landing.BadDelivery) -> HTTPException:
    return HTTPException(status_code=413 if isinstance(exc, landing.TooManyRows) else 422, detail=str(exc))


async def _parse_body(request: Request, spec, settings: Settings) -> tuple[list[dict[str, Any]], date | None]:
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    raw = await read_body_limited(request, settings.max_body_bytes)
    if content_type in ("text/csv", "application/csv", "text/plain"):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"CSV ist kein UTF-8: {exc}") from exc
        try:
            return landing.rows_from_csv(spec, text, max_rows=settings.max_rows), None
        except landing.BadDelivery as exc:
            raise _bad(exc) from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Körper ist weder JSON noch text/csv: {exc}") from exc
    if isinstance(payload, list):
        payload = {"rows": payload}
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        try:
            landing.check_row_count(len(payload["rows"]), settings.max_rows)  # vor der Zeilenprüfung, nie danach
        except landing.TooManyRows as exc:
            raise _bad(exc) from exc
    try:
        body = DeliveryBody.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    model = row_model(spec.key)
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(body.rows, start=1):
        try:
            model.model_validate(row)
        except ValidationError as exc:
            for e in exc.errors(include_url=False):
                errors.append({"row": i, "loc": list(e.get("loc", ())), "msg": e.get("msg")})
        if len(errors) >= 20:
            break
    if errors:
        raise HTTPException(status_code=422, detail={"message": f"Feed {spec.key}: Zeilen passen nicht zum Vertrag", "errors": errors})
    try:
        rows = landing.rows_from_json(spec, body.rows, max_rows=settings.max_rows)
    except landing.BadDelivery as exc:
        raise _bad(exc) from exc
    return rows, body.delivered_on


@router.post(
    "/feeds/{feed_key:path}", response_model=DeliveryResponse, status_code=201,
    summary="Eine Lieferung landen und importieren (dry_run: nur Vorschau)",
    openapi_extra={"requestBody": {"content": {
        "application/json": {"schema": DeliveryBody.model_json_schema()},
        "text/csv": {"schema": {"type": "string", "description": "Kopfzeile plus Zeilen, keine #-Zeile"}},
    }, "required": True}},
)
async def post_feed(
    request: Request,
    feed_key: str,
    key: Annotated[ApiKey, Depends(require_key)],
    settings: Annotated[Settings, Depends(get_settings)],
    dry_run: Annotated[bool, Query(description="true: keine Datei, kein Import, nur Vorschau")] = False,
    delivered_on: Annotated[date | None, Query(description="Lieferdatum im Dateinamen; Standard heute")] = None,
) -> Any:
    spec = _spec_or_404(feed_key)
    require_system(key, spec)
    request.state.audit.update({"source_system": spec.source_system, "feed": spec.feed, "dry_run": dry_run})
    rows, body_date = await _parse_body(request, spec, settings)
    when = delivered_on or body_date or date.today()

    with DB_LOCK:
        target = ":memory:" if (dry_run and str(settings.db_path) != ":memory:" and not Path(settings.db_path).exists()) else Path(settings.db_path)
        con = db.connect(target)
        try:
            try:
                result = landing.deliver(con, settings.raw_dir, spec, rows, delivered_on=when, dry_run=dry_run)
            except landing.BadDelivery as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Import verweigert: {exc}") from exc
        finally:
            con.close()

    request.state.audit.update({
        "delivery_id": result.delivery_id, "rows_read": result.rows_read, "rows_new": result.rows_new,
        "n_unresolved": result.n_unresolved, "already_ingested": result.already_ingested, "run_id": result.run_id,
    })
    payload = DeliveryResponse.model_validate(result.to_dict())
    status = 201 if (not dry_run and not result.already_ingested) else 200
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))
