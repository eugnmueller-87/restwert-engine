"""Konnektor ServiceNow-Tickets -> ``POST /v1/feeds/sd_tickets``: das Muster für jeden weiteren Konnektor.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Drei Schritte, jeder für sich testbar:

1. **Holen** (``fetch_servicenow``): ``GET <SERVICENOW_URL>/api/now/table/<table>`` mit
   ``sysparm_query`` aus der Mapping-Datei plus dem Wasserzeichen
   (``sys_updated_on > <letzter Stand>``), seitenweise. Token aus der
   Umgebungsvariable ``SERVICENOW_TOKEN``, nie aus einer Datei im Repo.
2. **Abbilden** (``map_record``): ``connectors/mappings/servicenow_tickets.yaml``
   sagt, welches Feld des Hauses welche Spalte des Feeds ``servicedesk/tickets``
   füllt (``columns``), wie Wertelisten übersetzt werden (``values``), welche
   Spalten einen festen Wert bekommen (``defaults``) und welche Felder ein
   ServiceNow-Datum (``YYYY-MM-DD HH:MM:SS``) in ISO (``T``) umschreiben
   (``transforms``). Der Konnektor kennt die Spaltenliste nicht selbst: er liest
   sie aus ``restwert.lake.feeds.FEEDS`` und weist eine Mapping-Datei ab, die
   eine Spalte nennt, die der Vertrag nicht hat.
3. **Schieben** (``push_rows``): ``POST <RESTWERT_API_URL>/v1/feeds/sd_tickets`` mit
   ``X-API-Key`` aus ``RESTWERT_API_KEY``. Die Schnittstelle landet die Datei,
   importiert sie und antwortet mit ``delivery_id``, Zählern und der Vorschau der
   ungeklärten Zeilen. Der Konnektor prüft nichts doppelt. Mehr als
   ``api.batch_size`` Zeilen (Standard 5000, unter der Grenze der Schnittstelle von
   50000 je Lieferung) gehen in Teilen, eine Lieferung je Teil; das Wasserzeichen
   rückt erst vor, wenn jeder Teil angenommen wurde.

Wasserzeichen: ``connectors/state/servicenow_tickets.json`` hält den größten
``sys_updated_on`` der zuletzt erfolgreich geschobenen Lieferung. Ein ``--dry-run``
schiebt mit ``dry_run=true`` (die Schnittstelle schreibt nichts) und rückt das
Wasserzeichen nicht vor. ``--fixture <datei.json>`` liest die Datensätze aus
einer Datei statt aus ServiceNow (Test ohne Netz; ``tests/fixtures/servicenow_records.py``).

Aufruf::

    python -m connectors.servicenow_tickets --dry-run
    python -m connectors.servicenow_tickets --fixture tests/fixtures/servicenow_sample.json --dry-run
    python -m connectors.servicenow_tickets
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - plain checkout without pip install -e
    sys.path.insert(0, str(ROOT))

from restwert.lake.feeds import FEEDS, FeedSpec  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_MAPPING = HERE / "mappings" / "servicenow_tickets.yaml"
DEFAULT_STATE = HERE / "state" / "servicenow_tickets.json"

FetchFn = Callable[[str | None], list[dict[str, Any]]]
PushFn = Callable[[list[dict[str, Any]], bool], dict[str, Any]]

DEFAULT_BATCH_SIZE = 5000  # Zeilen je Lieferung; die Schnittstelle nimmt höchstens Settings.max_rows (Standard 50000)


# ------------------------------------------------------------------------------------ Mapping


@dataclass(frozen=True)
class Mapping:
    feed: str
    columns: dict[str, str]
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    transforms: dict[str, str] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> FeedSpec:
        return FEEDS[self.feed]

    @property
    def batch_size(self) -> int:
        raw = self.api.get("batch_size")
        n = DEFAULT_BATCH_SIZE if raw is None or raw == "" else int(raw)
        if n < 1:
            raise ValueError(f"api.batch_size muss mindestens 1 sein, ist {n}")
        return n

    @property
    def watermark_field(self) -> str:
        return str(self.source.get("watermark_field") or "sys_updated_on")


def load_mapping(path: Path = DEFAULT_MAPPING) -> Mapping:
    """Mapping-Datei lesen und gegen den Feed-Vertrag prüfen; unbekannte Spalten sind ein Fehler."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "feed" not in raw or "columns" not in raw:
        raise ValueError(f"{path}: braucht 'feed' und 'columns'")
    feed = str(raw["feed"])
    if feed not in FEEDS:
        raise ValueError(f"{path}: unbekannter Feed {feed!r}; bekannt: {', '.join(FEEDS)}")
    m = Mapping(
        feed=feed, columns={str(k): str(v) for k, v in (raw.get("columns") or {}).items()},
        values={str(k): dict(v) for k, v in (raw.get("values") or {}).items()},
        defaults=dict(raw.get("defaults") or {}), transforms={str(k): str(v) for k, v in (raw.get("transforms") or {}).items()},
        source=dict(raw.get("source") or {}), api=dict(raw.get("api") or {}),
    )
    known = set(m.spec.column_names)
    for block_name, block in (("columns", m.columns), ("values", m.values), ("defaults", m.defaults), ("transforms", m.transforms)):
        unknown = sorted(c for c in block if c not in known)
        if unknown:
            raise ValueError(f"{path}: {block_name} nennt Spalten, die {feed} nicht hat: {unknown}")
    missing = [c.name for c in m.spec.columns if c.required and c.name not in m.columns and c.name not in m.defaults]
    if missing:
        raise ValueError(f"{path}: Pflichtspalten von {feed} ohne Quelle oder Standard: {missing}")
    bad_transform = sorted(v for v in m.transforms.values() if v not in _TRANSFORMS)
    if bad_transform:
        raise ValueError(f"{path}: unbekannte transforms {bad_transform}; bekannt: {sorted(_TRANSFORMS)}")
    return m


def _get_path(record: dict[str, Any], dotted: str) -> Any:
    """``a.b.c`` im Datensatz; ServiceNow liefert Referenzfelder auch als ``{"value": ..., "display_value": ...}``."""
    cur: Any = record
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    if isinstance(cur, dict) and "value" in cur:
        cur = cur["value"]
    return cur


def _t_datetime(v: Any) -> Any:
    if v is None:
        return None
    text = str(v).strip()
    if not text:
        return None
    if len(text) >= 19 and text[10] == " ":
        text = text[:10] + "T" + text[11:19]
    return text


def _t_date(v: Any) -> Any:
    if v is None:
        return None
    text = str(v).strip()
    return text[:10] if text else None


def _t_number(v: Any) -> Any:
    if v is None:
        return None
    text = str(v).strip().replace(",", ".")
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return text  # der Import meldet bad_type; der Konnektor rät nicht
    return int(num) if num.is_integer() else num


def _t_text(v: Any) -> Any:
    if v is None:
        return None
    text = str(v).strip()
    return text or None


_TRANSFORMS: dict[str, Callable[[Any], Any]] = {"datetime": _t_datetime, "date": _t_date, "number": _t_number, "text": _t_text}


def map_record(mapping: Mapping, record: dict[str, Any]) -> dict[str, Any]:
    """Ein Datensatz des Hauses -> eine Zeile des Feeds (Spalten in Vertragsreihenfolge, fehlend = None)."""
    row: dict[str, Any] = {}
    for col in mapping.spec.column_names:
        if col in mapping.columns:
            value = _get_path(record, mapping.columns[col])
        else:
            value = None
        if col in mapping.transforms:
            value = _TRANSFORMS[mapping.transforms[col]](value)
        elif isinstance(value, str):
            value = value.strip() or None
        if col in mapping.values:
            table = mapping.values[col]
            key = "" if value is None else str(value)
            if key in table:
                value = table[key]
            elif value is None and "" in table:
                value = table[""]
        if value is None and col in mapping.defaults:
            value = mapping.defaults[col]
        row[col] = value
    return row


# ------------------------------------------------------------------------------------ Wasserzeichen


def load_state(path: Path = DEFAULT_STATE) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"watermark": None, "last_run": None, "last_delivery_id": None, "runs": []}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path: Path, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        fh.write("\n")


# ------------------------------------------------------------------------------------ Holen und Schieben


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Umgebungsvariable {name} fehlt; sie steht nie im Repo")
    return value


def fetch_servicenow(mapping: Mapping, since: str | None, *, client: Any = None) -> list[dict[str, Any]]:
    """Alle Datensätze seit ``since`` (Wasserzeichen), seitenweise; ``client`` ist ein ``httpx.Client`` oder gleichwertig."""
    import httpx

    src = mapping.source
    base = _env(str(src.get("url_env") or "SERVICENOW_URL")).rstrip("/")
    token = _env(str(src.get("token_env") or "SERVICENOW_TOKEN"))
    table = str(src.get("table") or "incident")
    page = int(src.get("page_size") or 500)
    wm = mapping.watermark_field
    query = str(src.get("query") or "").strip()
    parts = [query] if query else []
    if since:
        parts.append(f"{wm}>{since}")
    parts.append(f"ORDERBY{wm}")
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    out: list[dict[str, Any]] = []
    offset = 0
    try:
        while True:
            resp = client.get(
                f"{base}/api/now/table/{table}",
                params={"sysparm_query": "^".join(parts), "sysparm_limit": page, "sysparm_offset": offset,
                        "sysparm_display_value": "false", "sysparm_exclude_reference_link": "true"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            batch = list(resp.json().get("result") or [])
            out.extend(batch)
            if len(batch) < page:
                return out
            offset += page
    finally:
        if own:
            client.close()


def push_rows(
    client: Any, base_url: str, api_key: str, feed_key: str, rows: list[dict[str, Any]], *,
    dry_run: bool, delivered_on: date | None = None,
) -> dict[str, Any]:
    """``POST <base>/v1/feeds/<feed>``; ``client`` ist ein ``httpx.Client`` oder der FastAPI-``TestClient``."""
    body: dict[str, Any] = {"rows": rows}
    if delivered_on:
        body["delivered_on"] = delivered_on.isoformat()
    resp = client.post(
        f"{base_url.rstrip('/')}/v1/feeds/{feed_key}", params={"dry_run": "true" if dry_run else "false"},
        json=body, headers={"X-API-Key": api_key},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Schnittstelle antwortet {resp.status_code}: {resp.text[:500]}")
    return resp.json()


# ------------------------------------------------------------------------------------ Lauf


def run(
    mapping: Mapping, state_path: Path, *, fetch: FetchFn, push: PushFn, dry_run: bool,
) -> dict[str, Any]:
    """Holen, abbilden, in Teilen schieben, Wasserzeichen vorrücken (nur ohne dry_run und nur, wenn jeder Teil angenommen wurde)."""
    state = load_state(state_path)
    since = state.get("watermark")
    records = fetch(since)
    wm = mapping.watermark_field
    summary: dict[str, Any] = {
        "feed": mapping.feed, "since": since, "records": len(records), "dry_run": dry_run,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not records:
        summary["result"] = None
        summary["note"] = "nichts Neues seit dem Wasserzeichen; nichts geschoben"
        return summary
    rows = [map_record(mapping, r) for r in records]
    size = mapping.batch_size
    results = [push(rows[start:start + size], dry_run) for start in range(0, len(rows), size)]
    result = results[-1]
    summary["result"] = result  # der letzte Teil; bei einem Teil die ganze Lieferung
    summary["batches"] = len(results)
    summary["delivery_ids"] = [r.get("delivery_id") for r in results]
    summary["rows_new"] = sum(int(r.get("rows_new") or 0) for r in results)
    summary["n_unresolved"] = sum(int(r.get("n_unresolved") or 0) for r in results)
    newest = max((str(_get_path(r, wm) or "") for r in records), default="")
    if not dry_run and newest:
        state["watermark"] = newest
        state["last_run"] = summary["started_at"]
        state["last_delivery_id"] = result.get("delivery_id")
        state.setdefault("runs", []).append({
            "at": summary["started_at"], "records": len(records), "batches": len(results),
            "delivery_id": result.get("delivery_id"), "delivery_ids": summary["delivery_ids"],
            "rows_new": summary["rows_new"], "n_unresolved": summary["n_unresolved"],
        })
        state["runs"] = state["runs"][-50:]
        save_state(state_path, state)
        summary["watermark_after"] = newest
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m connectors.servicenow_tickets", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true", help="an die Schnittstelle mit dry_run=true; Wasserzeichen bleibt")
    parser.add_argument("--fixture", type=Path, default=None, help="JSON-Datei mit Datensätzen statt ServiceNow (kein Netz)")
    parser.add_argument("--api-url", default=None, help="Standard: Umgebungsvariable aus der Mapping-Datei (RESTWERT_API_URL)")
    args = parser.parse_args(argv)

    import httpx

    mapping = load_mapping(args.mapping)
    api_url = args.api_url or _env(str(mapping.api.get("url_env") or "RESTWERT_API_URL"))
    api_key = _env(str(mapping.api.get("key_env") or "RESTWERT_API_KEY"))
    feed_key = mapping.spec.bronze_table.split(".", 1)[1]

    if args.fixture:
        with open(args.fixture, "r", encoding="utf-8") as fh:
            fixture_records = json.load(fh)
        if isinstance(fixture_records, dict):
            fixture_records = fixture_records.get("result") or []

        def fetch(since: str | None) -> list[dict[str, Any]]:
            wm = mapping.watermark_field
            return [r for r in fixture_records if not since or str(_get_path(r, wm) or "") > since]
    else:
        def fetch(since: str | None) -> list[dict[str, Any]]:
            return fetch_servicenow(mapping, since)

    with httpx.Client(timeout=120.0) as client:
        def push(rows: list[dict[str, Any]], dry: bool) -> dict[str, Any]:
            return push_rows(client, api_url, api_key, feed_key, rows, dry_run=dry)

        summary = run(mapping, args.state, fetch=fetch, push=push, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
