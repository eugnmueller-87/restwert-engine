"""The ServiceNow connector: mapping against the contract, record to row, run with a fixture and the TestClient, no network.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from connectors.servicenow_tickets import DEFAULT_MAPPING, load_mapping, load_state, map_record, push_rows, run
from restwert.lake.feeds import FEEDS
from restwert_api.app import create_app
from restwert_api.auth import ApiKey, KeyRing
from restwert_api.settings import Settings
from tests.fixtures.servicenow_records import RECORDS

SECRET = "test-servicedesk-secret-0001"


@pytest.fixture
def client(tmp_path):
    settings = Settings().with_paths(tmp_path)
    with TestClient(create_app(settings, KeyRing([ApiKey("sd-test", SECRET, frozenset({"servicedesk"}))]))) as c:
        c.rw_settings = settings  # type: ignore[attr-defined]
        yield c


def test_mapping_file_matches_the_feed_contract():
    m = load_mapping(DEFAULT_MAPPING)
    spec = FEEDS["servicedesk/tickets"]
    assert m.feed == "servicedesk/tickets"
    assert set(m.columns) <= set(spec.column_names)
    required = {c.name for c in spec.columns if c.required}
    assert required <= set(m.columns) | set(m.defaults)
    assert set(m.values["damage_type"].values()) <= set(next(c.enum for c in spec.columns if c.name == "damage_type"))
    assert set(m.values["resolution"].values()) <= set(next(c.enum for c in spec.columns if c.name == "resolution"))
    assert m.watermark_field == "sys_updated_on"
    assert "secret" not in Path(DEFAULT_MAPPING).read_text(encoding="utf-8").lower().replace("kein geheimnis", "")


def test_mapping_rejects_unknown_columns_and_missing_required(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("feed: servicedesk/tickets\ncolumns:\n  ticket_id: number\n  foo: bar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="foo"):
        load_mapping(bad)
    bad.write_text("feed: servicedesk/tickets\ncolumns:\n  ticket_id: number\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Pflichtspalten"):
        load_mapping(bad)
    bad.write_text("feed: crm/leads\ncolumns:\n  a: b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unbekannter Feed"):
        load_mapping(bad)


def test_map_record_translates_fields_values_and_timestamps():
    m = load_mapping(DEFAULT_MAPPING)
    row = map_record(m, RECORDS[0])
    assert list(row) == list(FEEDS["servicedesk/tickets"].column_names)
    assert row["ticket_id"] == "INC0010001" and row["serial"] == "SN-T-0001"
    assert row["contract_id"] == "RC-T-01", "reference fields arrive as {value, link}"
    assert row["opened_at"] == "2026-09-01T09:00:00" and row["closed_at"] == "2026-09-03T15:30:00"
    assert row["damage_type"] == "screen" and row["resolution"] == "repair"
    assert row["quote_eur"] == 120 and row["repair_cost_eur"] == 95.5, "comma decimal is accepted"
    assert row["replacement_serial"] is None
    assert row["repair_partner_ref"] == "Refurbishment and repair partner (role-only)"
    second = map_record(m, RECORDS[1])
    assert second["closed_at"] is None and second["resolution"] == "open" and second["contract_id"] is None
    third = map_record(m, RECORDS[2])
    assert third["damage_type"] == "Cracked case", "an unmapped value passes through; the ingest says bad_enum, the connector never guesses"


def test_run_with_fixture_and_testclient_dry_run_leaves_no_trace(client, tmp_path):
    m = load_mapping(DEFAULT_MAPPING)
    state_path = tmp_path / "state" / "servicenow_tickets.json"

    def push(rows, dry_run):
        return push_rows(client, "", SECRET, "sd_tickets", rows, dry_run=dry_run)

    summary = run(m, state_path, fetch=lambda since: RECORDS, push=push, dry_run=True)
    assert summary["records"] == 3 and summary["since"] is None
    res = summary["result"]
    assert res["dry_run"] is True and res["rows_read"] == 3
    assert res["unresolved_by_reason"] == {"bad_enum": 1, "unknown_serial": 2}
    assert not state_path.exists(), "a dry run never advances the watermark"
    assert not list(client.rw_settings.raw_dir.rglob("*.csv"))


def test_run_for_real_lands_and_advances_the_watermark(client, tmp_path):
    m = load_mapping(DEFAULT_MAPPING)
    state_path = tmp_path / "state" / "servicenow_tickets.json"
    seen: list[str | None] = []

    def fetch(since):
        seen.append(since)
        return [r for r in RECORDS if not since or r["sys_updated_on"] > since]

    def push(rows, dry_run):
        return push_rows(client, "", SECRET, "sd_tickets", rows, dry_run=dry_run)

    first = run(m, state_path, fetch=fetch, push=push, dry_run=False)
    assert first["result"]["delivery_id"] and first["watermark_after"] == "2026-09-05 12:00:00"
    state = load_state(state_path)
    assert state["watermark"] == "2026-09-05 12:00:00" and state["last_delivery_id"] == first["result"]["delivery_id"]
    assert len(state["runs"]) == 1
    files = list(client.rw_settings.raw_dir.rglob("*.csv"))
    assert len(files) == 1 and files[0].parent.name == "tickets"
    second = run(m, state_path, fetch=fetch, push=push, dry_run=False)
    assert seen == [None, "2026-09-05 12:00:00"]
    assert second["records"] == 0 and second["result"] is None and "nichts Neues" in second["note"]
    assert len(list(client.rw_settings.raw_dir.rglob("*.csv"))) == 1


def test_run_delivers_in_batches_under_the_api_row_limit(client, tmp_path):
    m = replace(load_mapping(DEFAULT_MAPPING), api={"batch_size": 2})
    assert m.batch_size == 2 and load_mapping(DEFAULT_MAPPING).batch_size == 5000
    state_path = tmp_path / "state" / "servicenow_tickets.json"

    def push(rows, dry_run):
        assert len(rows) <= 2, "a batch never exceeds api.batch_size"
        return push_rows(client, "", SECRET, "sd_tickets", rows, dry_run=dry_run)

    summary = run(m, state_path, fetch=lambda since: RECORDS, push=push, dry_run=False)
    assert summary["records"] == 3 and summary["batches"] == 2
    assert len(summary["delivery_ids"]) == 2 and all(summary["delivery_ids"])
    assert summary["result"]["delivery_id"] == summary["delivery_ids"][-1]
    assert summary["rows_new"] == 0 and summary["n_unresolved"] == 3  # unknown_serial x2, bad_enum x1 across both parts
    files = sorted(client.rw_settings.raw_dir.rglob("*.csv"))
    assert [p.name[-7:] for p in files] == ["001.csv", "002.csv"]
    state = load_state(state_path)
    assert state["last_delivery_id"] == summary["delivery_ids"][-1] and state["runs"][0]["batches"] == 2
    with pytest.raises(ValueError, match="batch_size"):
        _ = replace(m, api={"batch_size": 0}).batch_size


def test_fixture_json_matches_the_python_fixture():
    sample = Path(__file__).resolve().parent / "fixtures" / "servicenow_sample.json"
    assert json.loads(sample.read_text(encoding="utf-8")) == {"result": RECORDS}


def test_push_reports_api_errors_instead_of_hiding_them(client):
    with pytest.raises(RuntimeError, match="401"):
        push_rows(client, "", "falsch-falsch", "sd_tickets", [{"ticket_id": "x"}], dry_run=True)
