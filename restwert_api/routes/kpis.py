"""``GET /v1/kpis/latest``: ``gold.kpi_values`` (oder ``kpi_values``, v0.1) zum jüngsten Stichtag. Reines Lesen.

Die Spalten sind die der Tabelle (``restwert/lake/schema_lake.py`` für Gold,
``restwert/kpi/compute.py`` ``KPI_VALUES_COLUMNS`` für v0.1); ``value`` ist
``None`` und ``status`` ``not_measurable``, wenn die Engine es so gespeichert hat.
Die Schnittstelle rechnet nichts nach und rundet nichts.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from restwert import db

from restwert_api.auth import ApiKey
from restwert_api.deps import get_settings, open_read, require_key
from restwert_api.schemas import KpiRow, KpisLatest
from restwert_api.settings import Settings

router = APIRouter(tags=["kpis"])

_TABLE = {"gold": "gold.kpi_values", "v01": "kpi_values"}


@router.get("/kpis/latest", response_model=KpisLatest, summary="Kennzahlen zum jüngsten Stichtag")
def kpis_latest(
    key: Annotated[ApiKey, Depends(require_key)],
    settings: Annotated[Settings, Depends(get_settings)],
    scope: Annotated[Literal["gold", "v01"], Query(description="gold: die 14 Gold-Kennzahlen; v01: die 20 der v0.1-Registry")] = "gold",
) -> KpisLatest:
    table = _TABLE[scope]
    con = open_read(settings)
    try:
        if not db.table_exists(con, table):
            return KpisLatest(scope=scope, table=table, as_of=None, n=0, rows=[])
        q = db._q(table)
        frame = db.read_df(con, f"SELECT * FROM {q} WHERE as_of = (SELECT max(as_of) FROM {q}) ORDER BY kpi_id")
    finally:
        con.close()
    if len(frame) == 0:
        return KpisLatest(scope=scope, table=table, as_of=None, n=0, rows=[])
    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    rows = [KpiRow.model_validate({k: (v if k not in ("as_of",) else _to_date(v)) for k, v in r.items()}) for r in records]
    return KpisLatest(scope=scope, table=table, as_of=rows[0].as_of, n=len(rows), rows=rows)


def _to_date(v):
    from restwert.lake.common import to_date

    return to_date(v)
