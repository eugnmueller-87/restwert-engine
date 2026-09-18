"""Die FastAPI-App: Fabrik, Audit-Middleware, Lebenszyklus des Arbeitsfadens, Router.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``create_app(settings, keyring)`` baut eine App mit eigenen Pfaden und Schlüsseln;
Tests und Rauchtests bekommen so eine Kopie, die das Repo nie berührt. Ohne
Argumente kommen Einstellungen und Schlüssel aus der Umgebung
(``Settings.from_env``, ``KeyRing.load``).

Routen (Präfix ``/v1``):

| Route | Schlüssel | Seiteneffekt |
|---|---|---|
| ``GET  /health`` | keiner | keiner |
| ``GET  /feeds``, ``GET /feeds/<feed>`` | keiner | keiner (reine Liste aus ``FEEDS``) |
| ``POST /feeds/<feed>`` | Quellsystem des Feeds | Landing-Datei plus Import, außer ``dry_run=true`` |
| ``GET  /deliveries`` | beliebig | keiner |
| ``POST /runs`` | ``may_run`` | ein Lauf in der Warteschlange |
| ``GET  /runs``, ``GET /runs/<id>`` | beliebig | keiner |
| ``GET  /kpis/latest`` | beliebig | keiner |
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from restwert_api import API_VERSION, GOVERNANCE_PRINCIPLE
from restwert_api.audit import AuditLog
from restwert_api.auth import KeyRing
from restwert_api.routes import deliveries, feeds, health, kpis, runs
from restwert_api.runner import RunStore, RunWorker
from restwert_api.settings import Settings

_DESCRIPTION = """Schnittstelle der Restwert Engine (v0.4): Quellsysteme liefern ihre Feeds per HTTP,
jede Lieferung landet als Datei unter `data/lake/raw/<system>/<feed>/` mit `is_synthetic=false`
und geht durch denselben Import wie eine Handlieferung (SHA-256, Typisieren, Schlüssel auflösen,
Deduplizieren, `bronze.deliveries`, `bronze.unresolved`). `POST /v1/runs` stößt die Kette
`ingest` bis `export` zum Stichtag an. Die Schnittstelle ruft nichts nach außen.

""" + GOVERNANCE_PRINCIPLE


def create_app(settings: Settings | None = None, keyring: KeyRing | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if keyring is None:
        keyring = KeyRing.load(settings.keys_file, settings.keys_env)
    store = RunStore(settings.runs_dir)
    worker = RunWorker(settings, store)
    audit = AuditLog(settings.audit_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(
        title="Restwert Engine API", version=API_VERSION, description=_DESCRIPTION, lifespan=lifespan,
        docs_url="/docs", redoc_url=None,
    )
    app.state.settings = settings
    app.state.keyring = keyring
    app.state.store = store
    app.state.worker = worker
    app.state.audit = audit

    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.audit = {}
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - protokollieren, dann als 500 melden
            audit.write({
                "method": request.method, "path": request.url.path, "status": 500,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "client": request.client.host if request.client else None,
                "error": f"{type(exc).__name__}: {exc}", **request.state.audit,
            })
            return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
        if request.url.path != "/v1/health":
            audit.write({
                "method": request.method, "path": request.url.path, "status": response.status_code,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "client": request.client.host if request.client else None, **request.state.audit,
            })
        return response

    for router in (health.router, feeds.router, deliveries.router, runs.router, kpis.router):
        app.include_router(router, prefix="/v1")
    return app


__all__ = ["create_app"]
