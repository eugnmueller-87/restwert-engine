"""The interface (``restwert_api``): deliveries land as files and go through the same ingest, keys gate, nothing leaks.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every test builds its own app with paths under ``tmp_path`` and its own keys;
the repository's ``data/`` and ``config/`` are never touched. The rows come from
``tests/fixtures/api_rows.py`` and are synthetic.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from restwert import db
from restwert.lake.feeds import FEEDS, UNRESOLVED_REASONS, landing_path, parse_landing_name
from restwert_api import landing
from restwert_api.app import create_app
from restwert_api.auth import ApiKey, KeyRing, parse_keys_env
from restwert_api.schemas import DeliveryBody
from restwert_api.settings import MAX_ROWS_CEILING, Settings
from tests.fixtures.allowlist import is_allowed
from tests.fixtures.api_rows import CAT_MODELS, CAT_VARIANTS, ERP_PO_LINES, ERP_PURCHASE_ORDERS, SD_TICKETS
from tests.fixtures.denylist import find_denylisted

SD_SECRET = "test-servicedesk-secret-0001"
ERP_SECRET = "test-erp-secret-0002"
RUN_SECRET = "test-scheduler-secret-0003"
SD = {"X-API-Key": SD_SECRET}
ERP = {"X-API-Key": ERP_SECRET}
RUN = {"X-API-Key": RUN_SECRET}
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z_]+_\d{3}\.csv$")


@dataclass
class Api:
    client: TestClient
    settings: Settings


def _ring() -> KeyRing:
    return KeyRing([
        ApiKey("sd-test", SD_SECRET, frozenset({"servicedesk"})),
        ApiKey("erp-test", ERP_SECRET, frozenset({"erp"})),
        ApiKey("scheduler-test", RUN_SECRET, frozenset({"*"}), may_run=True),
    ])


@pytest.fixture
def api(tmp_path) -> Api:
    settings = Settings().with_paths(tmp_path)
    with TestClient(create_app(settings, _ring())) as client:
        yield Api(client=client, settings=settings)


def _landing_files(settings: Settings) -> list[Path]:
    return sorted(p for p in settings.raw_dir.rglob("*.csv"))


# --------------------------------------------------------------------------- contract and keys


def test_feeds_list_is_the_contract(api: Api):
    r = api.client.get("/v1/feeds")
    assert r.status_code == 200
    body = r.json()
    assert [f["key"] for f in body] == list(FEEDS)
    assert len(body) == 19
    sd = next(f for f in body if f["key"] == "servicedesk/tickets")
    assert sd["short_key"] == "sd_tickets"
    assert [c["name"] for c in sd["columns"]] == list(FEEDS["servicedesk/tickets"].column_names)
    assert api.client.get("/v1/feeds/sd_tickets").json() == api.client.get("/v1/feeds/servicedesk/tickets").json()
    assert api.client.get("/v1/feeds/nope").status_code == 404


def test_health_needs_no_key_and_has_no_side_effect(api: Api):
    r = api.client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["db_exists"] is False and r.json()["feeds"] == 19 and r.json()["keys_configured"] == 3
    assert not api.settings.db_path.exists()


def test_missing_or_wrong_key_is_401_and_wrong_system_is_403(api: Api):
    assert api.client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS}).status_code == 401
    assert api.client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS}, headers={"X-API-Key": "falsch-falsch"}).status_code == 401
    assert api.client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS}, headers=ERP).status_code == 403
    assert api.client.post("/v1/runs", json={"steps": ["ingest"]}, headers=SD).status_code == 403
    assert api.client.get("/v1/deliveries").status_code == 401
    assert not _landing_files(api.settings), "a refused request must not land a file"


def test_no_keys_configured_is_503_never_open(tmp_path):
    with TestClient(create_app(Settings().with_paths(tmp_path), KeyRing())) as client:
        assert client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS}, headers=SD).status_code == 503
        assert client.get("/v1/health").status_code == 200


def test_keys_from_env_text_and_bad_keys_refused():
    keys = parse_keys_env("erp-x:secret-erp-01:erp+wms;sched:secret-run-01:*:run")
    assert [k.key_id for k in keys] == ["erp-x", "sched"]
    assert keys[0].source_systems == {"erp", "wms"} and keys[0].may_run is False
    assert keys[1].may_run is True and keys[1].allows_system("servicedesk")
    with pytest.raises(ValueError, match="kürzer als 8"):
        KeyRing([ApiKey("k", "kurz", frozenset({"erp"}))])
    with pytest.raises(ValueError, match="unbekannte Quellsysteme"):
        KeyRing([ApiKey("k", "lang-genug-01", frozenset({"crm"}))])
    with pytest.raises(ValueError, match="kein Quellsystem"):
        KeyRing([ApiKey("k", "lang-genug-01", frozenset())])


# --------------------------------------------------------------------------- dry run


def test_dry_run_previews_with_closed_reason_codes_and_writes_nothing(api: Api):
    r = api.client.post("/v1/feeds/sd_tickets?dry_run=true", json={"rows": SD_TICKETS}, headers=SD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["already_ingested"] is False
    assert body["delivery_id"] is None and body["landing_file"] is None
    assert body["rows_read"] == 2 and body["rows_typed"] == 1 and body["rows_new"] == 0
    assert body["unresolved_by_reason"] == {"bad_enum": 1, "unknown_serial": 1}
    assert {p["reason_code"] for p in body["unresolved_preview"]} <= set(UNRESOLVED_REASONS)
    assert body["unresolved_preview"][1]["reason_text"] == "bad_enum(damage_type), negative_amount(quote_eur)"
    assert not _landing_files(api.settings)
    assert not api.settings.db_path.exists(), "a dry run on a missing database creates no file"


# --------------------------------------------------------------------------- real deliveries


def test_sd_tickets_delivery_lands_a_real_file_and_reports(api: Api):
    r = api.client.post("/v1/feeds/servicedesk/tickets", json={"rows": SD_TICKETS, "delivered_on": "2026-09-18"}, headers=SD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["landing_file"] == "servicedesk/tickets/2026-09-18_tickets_001.csv"
    assert body["delivery_id"] == body["sha256"][:16]
    assert body["run_id"].startswith("ingest-")
    assert body["n_unresolved"] == 2 and body["unresolved_by_reason"] == {"bad_enum": 1, "unknown_serial": 1}

    files = _landing_files(api.settings)
    assert len(files) == 1 and NAME_RE.match(files[0].name)
    assert parse_landing_name(files[0].name)[1] == "tickets"
    raw = files[0].read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    lines = raw.decode("utf-8").splitlines()
    assert not lines[0].startswith("#"), "a real delivery carries no marker line"
    assert lines[0] == ",".join(FEEDS["servicedesk/tickets"].column_names) + ",is_synthetic"
    assert all(line.endswith(",false") for line in lines[1:]) and len(lines) == 3

    con = db.connect(api.settings.db_path)
    try:
        n_del, synth = con.execute("SELECT count(*), bool_or(is_synthetic) FROM bronze.deliveries").fetchone()
        assert n_del == 1 and synth is False
        codes = [c for (c,) in con.execute("SELECT reason_code FROM bronze.unresolved ORDER BY row_number").fetchall()]
        assert codes == ["unknown_serial", "bad_enum"]
        assert con.execute("SELECT count(*) FROM runs WHERE command = 'ingest'").fetchone()[0] == 1
        assert con.execute("SELECT n_files FROM gold.ingest_summary WHERE feed = 'servicedesk/tickets'").fetchone()[0] == 1
    finally:
        con.close()

    d = api.client.get("/v1/deliveries?feed=sd_tickets", headers=SD).json()
    assert len(d) == 1 and d[0]["delivery_id"] == body["delivery_id"] and d[0]["is_synthetic"] is False
    assert d[0]["reasons"] == {"bad_enum": 1, "unknown_serial": 1}


def test_erp_po_lines_resolve_against_earlier_deliveries(api: Api):
    assert api.client.post("/v1/feeds/catalogue/models", json=CAT_MODELS, headers=RUN).status_code == 201
    assert api.client.post("/v1/feeds/catalogue/variants", json=CAT_VARIANTS, headers=RUN).status_code == 201
    po = api.client.post("/v1/feeds/erp_purchase_orders", json=ERP_PURCHASE_ORDERS, headers=ERP)
    assert po.status_code == 201 and po.json()["rows_new"] == 2 and po.json()["n_unresolved"] == 0
    lines = api.client.post("/v1/feeds/erp/po_lines", json=ERP_PO_LINES, headers=ERP)
    assert lines.status_code == 201, lines.text
    body = lines.json()
    assert body["rows_read"] == 2 and body["rows_new"] == 1
    assert body["unresolved_by_reason"] == {"unknown_po": 1}
    assert body["unresolved_preview"] == [
        {"row_number": 2, "reason_code": "unknown_po", "reason_text": "unknown_po(po_number -> bronze.erp_purchase_orders.po_number)"}
    ]
    assert {f["feed"] for f in api.client.get("/v1/deliveries", headers=ERP).json()} == {"models", "variants", "purchase_orders", "po_lines"}


def test_same_content_twice_is_already_ingested_and_lands_no_second_file(api: Api):
    first = api.client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS, "delivered_on": "2026-09-18"}, headers=SD)
    second = api.client.post("/v1/feeds/sd_tickets", json={"rows": SD_TICKETS, "delivered_on": "2026-09-19"}, headers=SD)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["already_ingested"] is True
    assert second.json()["delivery_id"] == first.json()["delivery_id"]
    assert second.json()["landing_file"] == "2026-09-18_tickets_001.csv"
    assert second.json()["unresolved_by_reason"] == first.json()["unresolved_by_reason"]
    assert len(_landing_files(api.settings)) == 1
    assert len(api.client.get("/v1/deliveries", headers=SD).json()) == 1
    # a changed content on the same day is a second delivery with the next sequence number
    changed = [dict(SD_TICKETS[0], quote_eur=130.0)]
    third = api.client.post("/v1/feeds/sd_tickets", json={"rows": changed, "delivered_on": "2026-09-18"}, headers=SD)
    assert third.status_code == 201 and third.json()["landing_file"].endswith("_002.csv")
    assert third.json()["unresolved_by_reason"] == {"unknown_serial": 1}


def test_csv_body_is_accepted_and_marker_lines_or_synthetic_rows_refused(api: Api):
    header = ",".join(FEEDS["servicedesk/tickets"].column_names)
    csv_text = header + "\nT-9,SN-T-9,,2026-09-03T00:00:00,,screen,repair,10,,,Refurbishment and repair partner (role-only)\n"
    r = api.client.post("/v1/feeds/sd_tickets?dry_run=true", content=csv_text, headers={**SD, "Content-Type": "text/csv"})
    assert r.status_code == 200 and r.json()["rows_read"] == 1 and r.json()["unresolved_by_reason"] == {"unknown_serial": 1}
    marked = "# SYNTHETIC DATA - test\n" + csv_text
    r = api.client.post("/v1/feeds/sd_tickets", content=marked, headers={**SD, "Content-Type": "text/csv"})
    assert r.status_code == 422 and "Kennzeichnungszeile" in r.json()["detail"]
    synthetic = header + ",is_synthetic\nT-9,SN-T-9,,2026-09-03T00:00:00,,screen,repair,10,,,X (role-only),true\n"
    r = api.client.post("/v1/feeds/sd_tickets", content=synthetic, headers={**SD, "Content-Type": "text/csv"})
    assert r.status_code == 422 and "is_synthetic" in r.json()["detail"]
    assert not _landing_files(api.settings)


def test_unknown_column_and_wrong_feed_are_refused_before_landing(api: Api):
    r = api.client.post("/v1/feeds/sd_tickets", json=[{"ticket_id": "T-1", "foo": 1}], headers=SD)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["loc"] == ["foo"]
    r = api.client.post("/v1/feeds/sd_tickets", json=[{"po_number": "PO-1"}], headers=SD)
    assert r.status_code == 422
    r = api.client.post("/v1/feeds/sd_tickets", json={"rows": []}, headers=SD)
    assert r.status_code == 422
    assert not _landing_files(api.settings)


# --------------------------------------------------------------------------- limits: body size and row count


SD_HEADER = ",".join(FEEDS["servicedesk/tickets"].column_names)
SD_LINE = "T-{i},SN-T-{i},,2026-09-03T00:00:00,,screen,repair,10,,,Refurbishment and repair partner (role-only)"


def _sd_rows(n: int) -> list[dict]:
    return [dict(SD_TICKETS[0], ticket_id=f"T-{i:04d}", serial=f"SN-T-{i:04d}") for i in range(n)]


def _sd_csv(n: int) -> str:
    return SD_HEADER + "\n" + "\n".join(SD_LINE.format(i=i) for i in range(n)) + "\n"


def test_limits_come_from_the_environment_and_never_exceed_the_ceiling():
    s = Settings.from_env({"RESTWERT_API_MAX_BODY_MB": "2", "RESTWERT_API_MAX_ROWS": "1234"})
    assert s.max_body_bytes == 2 * 1024 * 1024 and s.max_rows == 1234
    assert Settings.from_env({}).max_body_bytes == 25 * 1024 * 1024 and Settings.from_env({}).max_rows == 50_000
    with pytest.raises(ValueError, match="RESTWERT_API_MAX_ROWS"):
        Settings.from_env({"RESTWERT_API_MAX_ROWS": str(MAX_ROWS_CEILING + 1)})
    with pytest.raises(ValueError, match="RESTWERT_API_MAX_BODY_MB"):
        Settings.from_env({"RESTWERT_API_MAX_BODY_MB": "0"})
    with pytest.raises(ValueError, match="keine ganze Zahl"):
        Settings.from_env({"RESTWERT_API_MAX_ROWS": "viele"})
    with pytest.raises(ValueError, match="max_rows"):
        Settings(max_rows=0)
    # the schema carries the ceiling, so /docs and the OpenAPI file say it too
    assert DeliveryBody.model_json_schema()["properties"]["rows"]["maxItems"] == MAX_ROWS_CEILING
    assert DeliveryBody.model_json_schema()["properties"]["rows"]["minItems"] == 1


def test_oversized_body_is_413_on_every_route_before_anything_is_read_or_landed(tmp_path):
    settings = replace(Settings().with_paths(tmp_path), max_body_bytes=2048, max_rows=3)
    with TestClient(create_app(settings, _ring())) as client:
        big = {"rows": [dict(SD_TICKETS[0], repair_partner_ref="x" * 3000)]}
        r = client.post("/v1/feeds/sd_tickets", json=big, headers=SD)
        assert r.status_code == 413 and "Körper zu groß" in r.json()["detail"]
        # no Content-Length (chunked): the route reads in pieces and cuts at the limit
        raw = json.dumps(big).encode("utf-8")
        r = client.post(
            "/v1/feeds/sd_tickets", content=(raw[i:i + 500] for i in range(0, len(raw), 500)),
            headers={**SD, "Content-Type": "application/json"},
        )
        assert r.status_code == 413 and "Körper zu groß" in r.json()["detail"]
        # CSV, same limit
        r = client.post("/v1/feeds/sd_tickets", content=_sd_csv(40), headers={**SD, "Content-Type": "text/csv"})
        assert r.status_code == 413
        # every route, even without a key: nobody reads a body over the limit
        r = client.post("/v1/runs", content=b"{" + b" " * 3000 + b"}", headers={**RUN, "Content-Type": "application/json"})
        assert r.status_code == 413
        r = client.post("/v1/feeds/sd_tickets", json=big)
        assert r.status_code == 413
        # under the limit the contract check runs as before
        r = client.post("/v1/feeds/sd_tickets?dry_run=true", json={"rows": SD_TICKETS[:1]}, headers=SD)
        assert r.status_code == 200, r.text
        assert not list(tmp_path.rglob("*.csv")) and not settings.db_path.exists()
        audit = settings.audit_path.read_text(encoding="utf-8")
        assert '"status": 413' in audit and SD_SECRET not in audit


def test_too_many_rows_is_413_for_json_and_csv_and_nothing_lands(tmp_path):
    settings = replace(Settings().with_paths(tmp_path), max_rows=3)
    with TestClient(create_app(settings, _ring())) as client:
        four = _sd_rows(4)
        r = client.post("/v1/feeds/sd_tickets", json={"rows": four}, headers=SD)
        assert r.status_code == 413 and "höchstens 3" in r.json()["detail"], r.text
        r = client.post("/v1/feeds/sd_tickets", json=four, headers=SD)  # bare list, same gate
        assert r.status_code == 413
        r = client.post("/v1/feeds/sd_tickets?dry_run=true", json=four, headers=SD)  # a dry run counts too
        assert r.status_code == 413
        r = client.post("/v1/feeds/sd_tickets", content=_sd_csv(4), headers={**SD, "Content-Type": "text/csv"})
        assert r.status_code == 413 and "höchstens 3" in r.json()["detail"]
        assert not list(tmp_path.rglob("*.csv")) and not settings.db_path.exists()
        # exactly at the limit is a delivery
        r = client.post("/v1/feeds/sd_tickets", json={"rows": four[:3]}, headers=SD)
        assert r.status_code == 201, r.text
        assert r.json()["rows_read"] == 3 and len(_landing_files(settings)) == 1
    # the core functions carry the same gate, so nothing depends on the route alone
    spec = FEEDS["servicedesk/tickets"]
    with pytest.raises(landing.TooManyRows):
        landing.rows_from_json(spec, four, max_rows=3)
    with pytest.raises(landing.TooManyRows):
        landing.rows_from_csv(spec, _sd_csv(4), max_rows=3)
    assert len(landing.rows_from_csv(spec, _sd_csv(3), max_rows=3)) == 3


# --------------------------------------------------------------------------- the feed key never becomes a path


def test_feed_key_with_path_segments_is_404_and_the_landing_path_comes_from_the_contract(api: Api, tmp_path):
    for text in ("../../etc/passwd", "servicedesk/../erp/purchase_orders", "sd_tickets/..", "..\\..\\sd_tickets",
                 "/sd_tickets/../../x", "servicedesk/tickets/../tickets", "sd_tickets\x00"):
        with pytest.raises(landing.UnknownFeed):
            landing.resolve_feed(text)
    # percent-encoded so the client does not normalise the dots away before they reach the server
    for key in ("%2e%2e%2f%2e%2e%2fsd_tickets", "servicedesk%2f%2e%2e%2ferp%2fpurchase_orders", "sd_tickets%2f%2e%2e",
                "%2e%2e%5c%2e%2e%5csd_tickets", "sd_tickets%00"):
        r = api.client.post(f"/v1/feeds/{key}", json={"rows": SD_TICKETS}, headers=SD)
        assert r.status_code == 404, (key, r.status_code, r.text)
        r = api.client.get(f"/v1/feeds/{key}")
        assert r.status_code == 404, (key, r.status_code)
    assert not list(tmp_path.rglob("*.csv")) and not api.settings.db_path.exists()
    # the file name and folder come from the FeedSpec, never from the request text
    raw = api.settings.raw_dir.resolve()
    for spec in FEEDS.values():
        p = landing_path(api.settings.raw_dir, spec, date(2026, 9, 18), 1).resolve()
        assert raw in p.parents and NAME_RE.match(p.name), p
        assert p.parent == raw / spec.source_system / spec.feed


# --------------------------------------------------------------------------- runs and kpis


def _wait(api: Api, run_id: str, headers: dict, timeout: float = 60.0) -> dict:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        s = api.client.get(f"/v1/runs/{run_id}", headers=headers).json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def test_run_ingest_step_is_queued_executed_and_persisted(api: Api):
    api.client.post("/v1/feeds/sd_tickets", json=SD_TICKETS, headers=SD)
    r = api.client.post("/v1/runs", json={"as_of": "2026-09-18", "steps": ["ingest"]}, headers=RUN)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    assert run_id.startswith("api-") and r.json()["status"] in ("queued", "running")
    status = _wait(api, run_id, SD)
    assert status["status"] == "done", status
    assert [s["step"] for s in status["results"]] == ["ingest"]
    assert status["results"][0]["counts"]["files_already_ingested"] == 1
    assert status["results"][0]["run_id"].startswith("ingest-")
    assert (api.settings.runs_dir / f"{run_id}.json").exists()
    assert api.client.get("/v1/runs", headers=SD).json()[0]["run_id"] == run_id
    assert api.client.get("/v1/runs/steps").json()["steps"][0] == "ingest"
    assert api.client.get("/v1/runs/api-unknown", headers=SD).status_code == 404


def test_run_never_accepts_generate_lake_or_unknown_steps(api: Api):
    assert api.client.post("/v1/runs", json={"steps": ["generate-lake", "ingest"]}, headers=RUN).status_code == 422
    assert api.client.post("/v1/runs", json={"steps": ["all"]}, headers=RUN).status_code == 422
    assert api.client.post("/v1/runs", json={"steps": ["ingest", "ingest"]}, headers=RUN).status_code == 422
    r = api.client.post("/v1/runs", json={"steps": ["kpis", "ingest"]}, headers=RUN)
    assert r.status_code == 202 and r.json()["steps"] == ["ingest", "kpis"], "chain order wins over request order"
    status = _wait(api, r.json()["run_id"], SD)
    assert status["status"] in ("done", "failed")


def test_kpis_latest_is_404_without_db_and_empty_after_first_delivery(api: Api):
    assert api.client.get("/v1/kpis/latest", headers=SD).status_code == 404
    api.client.post("/v1/feeds/sd_tickets", json=SD_TICKETS, headers=SD)
    r = api.client.get("/v1/kpis/latest", headers=SD)
    assert r.status_code == 200 and r.json() == {"scope": "gold", "table": "gold.kpi_values", "as_of": None, "n": 0, "rows": []}
    assert api.client.get("/v1/kpis/latest?scope=v01", headers=SD).json()["n"] == 0


# --------------------------------------------------------------------------- audit and honesty


def test_audit_log_names_the_key_never_the_secret(api: Api):
    api.client.post("/v1/feeds/sd_tickets?dry_run=true", json=SD_TICKETS, headers=SD)
    api.client.post("/v1/feeds/sd_tickets", json=SD_TICKETS, headers={"X-API-Key": "falsch-falsch"})
    text = api.settings.audit_path.read_text(encoding="utf-8")
    assert SD_SECRET not in text and "falsch-falsch" not in text
    lines = [line for line in text.splitlines() if line.strip()]
    assert any('"key_id": "sd-test"' in line and '"dry_run": true' in line and '"status": 200' in line for line in lines)
    assert any('"status": 401' in line for line in lines)
    assert not any("/v1/health" in line for line in lines)


def test_engine_package_never_imports_the_interface_or_connectors():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "restwert").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(from|import)\s+(restwert_api|connectors)\b", text, re.MULTILINE):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, f"the engine imports the interface: {offenders}"
    for folder in ("restwert_api", "connectors"):
        for path in sorted((root / folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not re.search(r"^\s*(from|import)\s+subprocess\b", text, re.MULTILINE), path
            assert chr(0x2014) not in text, f"em dash in {path}"


def test_fixture_rows_respect_denylist_and_allowlist():
    text = (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "api_rows.py").read_text(encoding="utf-8")
    assert find_denylisted(text) == []
    names = [r["supplier_name"] for r in ERP_PURCHASE_ORDERS] + [r["repair_partner_ref"] for r in SD_TICKETS]
    assert all(is_allowed(n) for n in names), names
