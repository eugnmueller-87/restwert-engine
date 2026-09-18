"""Pydantic-Modelle der Schnittstelle; die Zeilenmodelle je Feed entstehen zur Laufzeit aus ``FEEDS``.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Es gibt keine zweite Spaltenliste: ``row_model(spec)`` baut das Modell aus
``ColumnSpec`` (Name, Typ, Pflicht, geschlossene Liste, Untergrenze). Das Modell
prüft die FORM (bekannte Spalten, Objekt je Zeile); Typ, Pflicht, Enum und
Untergrenze prüft der Import je Zeile und schreibt den Grund nach
``bronze.unresolved``. Eine Zeile mit falschem Datum ist deshalb kein 422,
sondern ``bad_type`` in der Vorschau, genau wie bei einer Handlieferung.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from restwert.lake.feeds import FEEDS, FeedSpec

_TYPE_HINT = {"str": "string", "int": "integer", "float": "number", "date": "string, ISO YYYY-MM-DD",
              "datetime": "string, ISO YYYY-MM-DDTHH:MM:SS", "bool": "boolean, true/false"}


def _camel(text: str) -> str:
    return "".join(p.capitalize() for p in text.replace("/", "_").split("_"))


@lru_cache(maxsize=None)
def row_model(feed_key: str) -> type[BaseModel]:
    """Ein Modell je Feed, ``extra='forbid'``: jede Spalte optional, weil der Import die Pflicht je Zeile meldet."""
    spec: FeedSpec = FEEDS[feed_key]
    fields: dict[str, Any] = {}
    for col in spec.columns:
        desc = f"{_TYPE_HINT[col.dtype]}; {'Pflicht' if col.required else 'optional'}"
        if col.enum:
            desc += f"; einer von {', '.join(col.enum)}"
        if col.min_value is not None:
            desc += f"; mindestens {col.min_value:g}"
        fields[col.name] = (str | int | float | bool | None, Field(default=None, description=desc))
    fields["is_synthetic"] = (Literal[False, "false"] | None, Field(default=None, description="darf nur false sein"))
    return create_model(f"{_camel(spec.key)}Row", __config__=ConfigDict(extra="forbid"), **fields)


class FeedColumn(BaseModel):
    name: str
    dtype: str
    required: bool
    enum: list[str] | None = None
    min_value: float | None = None


class FeedResolve(BaseModel):
    child: str
    parent_table: str
    parent_column: str
    reason_code: str


class FeedInfo(BaseModel):
    key: str
    short_key: str
    source_system: str
    feed: str
    delivering_system: str
    bronze_table: str
    business_key: list[str]
    order_column: str | None
    serial_column: str | None
    is_reference: bool
    depends_on: list[str]
    landing_pattern: str
    columns: list[FeedColumn]
    resolves: list[FeedResolve]
    description: str


def feed_info(spec: FeedSpec) -> FeedInfo:
    return FeedInfo(
        key=spec.key, short_key=spec.bronze_table.split(".", 1)[1], source_system=spec.source_system, feed=spec.feed,
        delivering_system=spec.delivering_system, bronze_table=spec.bronze_table, business_key=list(spec.business_key),
        order_column=spec.order_column, serial_column=spec.serial_column, is_reference=spec.is_reference,
        depends_on=list(spec.depends_on), landing_pattern=spec.landing_pattern,
        columns=[FeedColumn(name=c.name, dtype=c.dtype, required=c.required, enum=list(c.enum) if c.enum else None,
                            min_value=c.min_value) for c in spec.columns],
        resolves=[FeedResolve(child=ch, parent_table=pt, parent_column=pc, reason_code=rc) for ch, pt, pc, rc in spec.resolves],
        description=spec.description,
    )


class DeliveryBody(BaseModel):
    """JSON-Körper von ``POST /v1/feeds/<feed>``; alternativ ``text/csv`` mit Kopfzeile."""

    model_config = ConfigDict(extra="forbid")
    rows: list[dict[str, Any]] = Field(description="eine Zeile je Objekt, Spalten wie im Feed-Vertrag")
    delivered_on: date | None = Field(default=None, description="Lieferdatum im Dateinamen; Standard heute")


class UnresolvedPreviewRow(BaseModel):
    row_number: int
    reason_code: str
    reason_text: str


class DeliveryResponse(BaseModel):
    feed: str
    short_key: str
    source_system: str
    dry_run: bool
    already_ingested: bool
    sha256: str
    delivered_on: date
    delivery_id: str | None
    landing_file: str | None
    run_id: str | None
    rows_read: int
    rows_typed: int
    rows_new: int
    duplicates_identical: int
    duplicates_conflict: int
    n_unresolved: int
    unresolved_by_reason: dict[str, int]
    unresolved_preview: list[UnresolvedPreviewRow]
    notes: list[str]
    seconds: float


class DeliveryRow(BaseModel):
    delivery_id: str
    source_system: str
    feed: str
    source_file: str
    sha256: str
    delivered_on: date
    ingested_at: datetime
    rows_read: int
    rows_typed: int
    rows_new: int
    duplicates_identical: int
    duplicates_conflict: int
    n_unresolved: int
    reasons: dict[str, int]
    is_synthetic: bool


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: date | None = Field(default=None, description="Stichtag des Laufs; Standard heute")
    steps: list[str] | None = Field(default=None, description="Teilmenge in Kettenreihenfolge; Standard alle elf")


class StepReport(BaseModel):
    step: str
    seconds: float
    counts: dict[str, Any]
    run_id: str | None = None


class RunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "done", "failed"]
    as_of: date
    steps: list[str]
    key_id: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    seconds: float | None
    results: list[StepReport]
    error: str | None
    queue_position: int | None = None


class KpiRow(BaseModel):
    kpi_id: str
    as_of: date
    name: str
    page: str | None = None
    area: str | None = None
    value: float | None
    numerator: float | None
    denominator: float | None
    n: int | None
    unit: str
    status: str
    note: str | None
    target: float | None
    direction: str | None
    owner: str | None = None
    run_id: str | None


class KpisLatest(BaseModel):
    scope: Literal["gold", "v01"]
    table: str
    as_of: date | None
    n: int
    rows: list[KpiRow]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    engine_version: str
    api_version: str
    db_path: str
    db_exists: bool
    db_readable: bool
    raw_dir: str
    feeds: int
    keys_configured: int
    runs_queued: int
    run_active: str | None
    governance: str


__all__ = [
    "row_model", "FeedColumn", "FeedResolve", "FeedInfo", "feed_info", "DeliveryBody", "DeliveryResponse",
    "UnresolvedPreviewRow", "DeliveryRow", "RunRequest", "StepReport", "RunStatus", "KpiRow", "KpisLatest", "Health",
]
