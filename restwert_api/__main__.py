"""``python -m restwert_api``: den Server starten (uvicorn), Port aus ``RESTWERT_API_PORT`` oder ``--port``, Standard 8420.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Beim Start stehen Pfade und Schlüsselkennungen auf der Konsole, nie ein Geheimnis.
Ohne Schlüssel startet der Server trotzdem und antwortet auf jede geschützte
Route mit 503, damit ein Konfigurationsfehler sichtbar ist und nicht still.
"""

from __future__ import annotations

import argparse
import sys

from restwert import GOVERNANCE_PRINCIPLE, __version__

from restwert_api import API_VERSION
from restwert_api.app import create_app
from restwert_api.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m restwert_api", description="Schnittstelle der Restwert Engine")
    parser.add_argument("--host", default=None, help="Adresse (Standard RESTWERT_API_HOST oder 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (Standard RESTWERT_API_PORT oder 8420)")
    parser.add_argument("--reload", action="store_true", help="Entwicklung: Server bei Codeänderung neu starten")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    host = args.host or settings.host
    port = args.port or settings.port
    app = create_app(settings)
    ring = app.state.keyring
    print(f"Restwert Engine API v{API_VERSION} (Motor v{__version__})  |  {GOVERNANCE_PRINCIPLE}")
    print(f"  db={settings.db_path}  raw={settings.raw_dir}  out={settings.out_dir}")
    print(f"  audit={settings.audit_path}  runs={settings.runs_dir}")
    print(f"  Schlüssel: {len(ring)} ({', '.join(ring.key_ids) or 'KEINE: jede geschützte Route antwortet 503'})")
    print(f"  http://{host}:{port}/docs")

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info", reload=bool(args.reload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
