"""``GET /v1/deliveries``: ``bronze.deliveries``, jüngste zuerst. Reines Lesen."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from restwert import db

from restwert_api import landing
from restwert_api.auth import ApiKey
from restwert_api.deps import get_settings, open_read, require_key
from restwert_api.schemas import DeliveryRow
from restwert_api.settings import Settings

router = APIRouter(tags=["deliveries"])

_COLUMNS = (
    "delivery_id, source_system, feed, source_file, sha256, delivered_on, ingested_at, rows_read, rows_typed, rows_new, "
    "duplicates_identical, duplicates_conflict, n_unresolved, reasons_json, is_synthetic"
)


@router.get("/deliveries", response_model=list[DeliveryRow], summary="Registrierte Lieferungen")
def list_deliveries(
    key: Annotated[ApiKey, Depends(require_key)],
    settings: Annotated[Settings, Depends(get_settings)],
    feed: Annotated[str | None, Query(description="<system>/<feed> oder kurzer Name, z. B. sd_tickets")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[DeliveryRow]:
    con = open_read(settings)
    try:
        if not db.table_exists(con, "bronze.deliveries"):
            return []
        sql = f"SELECT {_COLUMNS} FROM bronze.deliveries"
        params: list = []
        if feed:
            try:
                spec = landing.resolve_feed(feed)
            except landing.UnknownFeed as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            sql += " WHERE source_system = ? AND feed = ?"
            params += [spec.source_system, spec.feed]
        sql += " ORDER BY ingested_at DESC, source_file DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        (did, system, feed_name, source_file, sha, delivered_on, ingested_at, rr, rt, rn, di, dc, nu, reasons_json, synth) = r
        out.append(DeliveryRow(
            delivery_id=did, source_system=system, feed=feed_name, source_file=source_file, sha256=sha,
            delivered_on=delivered_on, ingested_at=ingested_at, rows_read=rr, rows_typed=rt, rows_new=rn,
            duplicates_identical=di, duplicates_conflict=dc, n_unresolved=nu,
            reasons={k: int(v) for k, v in json.loads(reasons_json or "{}").items()}, is_synthetic=bool(synth),
        ))
    return out
