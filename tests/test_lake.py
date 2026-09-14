"""Tests of module 1 (the data lake): schema, feeds, ingest, conform, timeline, docs (SPEC_v0.2 section 4.6).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every ingest test works on hand-written landing files in ``tmp_path`` so that the
expected counts are absolute numbers, not something derived from the code under
test. The pipeline test runs only when the session lake fixture of module 6 exists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from restwert import db, schema
from restwert.lake import common, schema_lake
from restwert.lake.conform import allocate_over_serials, conform_frames, read_bronze, run_conform, write_compat_csvs
from restwert.lake.feeds import FEEDS, INGEST_ORDER, UNRESOLVED_REASONS, FeedSpec, landing_path, parse_landing_name, render_data_lake_md
from restwert.lake.ingest import ingest_all, ingest_file, read_landing_file, run_ingest
from restwert.lake.timeline import EXPECTED_STEPS, STEPS, build_timeline, chain_quality

AS_OF = date(2024, 12, 31)

# ---------------------------------------------------------------------------------------
# hand-written landing files
# ---------------------------------------------------------------------------------------


def _write(raw_dir: Path, key: str, rows: list[dict], delivered_on: date = date(2024, 12, 31), seq: int = 1,
           synthetic: bool = True, public: bool = False) -> Path:
    spec = FEEDS[key]
    path = landing_path(raw_dir, spec, delivered_on, seq)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(spec.column_names) + ["is_synthetic"]
    flag = "false" if (public or not synthetic) else "true"
    header = "# PUBLIC DATA - copy of test, every row carries its source URL" if public else \
        f"# SYNTHETIC DATA - restwert generate-lake seed=1 feed={key} delivery={delivered_on.isoformat()}"
    frame = pd.DataFrame([{**{c: r.get(c) for c in cols}, "is_synthetic": flag} for r in rows], columns=cols)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(header + "\n")
        frame.to_csv(fh, index=False, lineterminator="\n")
    return path


def _world(raw_dir: Path) -> dict[str, Path]:
    """A two-serial world through the whole cycle plus reference feeds; returns the file per feed key."""
    files: dict[str, Path] = {}
    files["catalogue/models"] = _write(raw_dir, "catalogue/models", [
        {"slug": "phone-a", "model_name": "Phone A", "oem": "Apple", "family": "Smartphone", "series": "Phone",
         "launch_date_de": "2023-09-22", "launch_date_kind": "verfuegbarkeit", "launch_source_url": "https://example.org/a"},
        {"slug": "laptop-b", "model_name": "Laptop B", "oem": "Lenovo", "family": "Laptop", "series": "Book",
         "launch_date_de": "2023-05", "launch_date_kind": "ankuendigung", "launch_source_url": "https://example.org/b"},
        {"slug": "tablet-c", "model_name": "Tablet C", "oem": "Samsung", "family": "Tablet", "series": "Tab",
         "launch_date_de": "", "launch_source_url": "https://example.org/c"},
    ], public=True)
    files["catalogue/variants"] = _write(raw_dir, "catalogue/variants", [
        {"slug": "phone-a", "spec": "128 GB", "storage_gb": 128, "rrp_eur_launch_de": 949, "rrp_source_url": "https://example.org/a", "rrp_source_date": "2023-09-22"},
        {"slug": "phone-a", "spec": "256 GB", "storage_gb": 256, "rrp_eur_launch_de": 1069, "rrp_source_url": "https://example.org/a", "rrp_source_date": "2023-09"},
        {"slug": "laptop-b", "spec": "16 GB / 512 GB", "storage_gb": 512, "rrp_eur_launch_de": 1499, "rrp_source_url": "https://example.org/b"},
        {"slug": "tablet-c", "spec": "128 GB", "storage_gb": 128, "rrp_eur_launch_de": 699, "rrp_source_url": "https://example.org/c"},
    ], public=True)
    files["market/curves"] = _write(raw_dir, "market/curves", [
        {"group_kind": "family_oem", "group": "Smartphone / Apple", "population": "marketplace", "n": 50, "age_min": 12, "age_max": 48,
         "intercept": -0.3, "slope_per_month": -0.012, "monthly_depreciation_pct": 0.012, "grade_A_offset": 0.04, "grade_C_offset": -0.12,
         "mape_in_sample": 0.2, "q_12": 0.64, "q_24": 0.55, "q_36": 0.48, "fit_quality": "ok"},
    ], public=True)
    files["contracts/register"] = _write(raw_dir, "contracts/register", [
        {"contract_id": "CTR-APL-01", "counterparty_name": "Apple", "counterparty_role": "manufacturer", "counterparty_is_public": "true",
         "category": "hardware", "start_date": "2022-01-01", "end_date": "2027-12-31", "notice_days": 90, "auto_renewal": "true",
         "price_protection": "true", "price_protection_days": 30, "claim_window_days": 14, "warranty_months": 12,
         "payment_terms_days": 30, "spend_under_contract_eur": 500000, "terms_note": "synthetic placeholder terms"},
        {"contract_id": "CTR-MKT-01", "counterparty_name": "Marketplace channel A (role-only)", "counterparty_role": "marketplace",
         "counterparty_is_public": "false", "category": "resale_channel", "start_date": "2022-01-01", "end_date": "2026-12-31",
         "notice_days": 60, "auto_renewal": "true", "price_protection": "false", "payment_terms_days": 28,
         "spend_under_contract_eur": 100000, "terms_note": "synthetic placeholder terms"},
    ])
    files["erp/purchase_orders"] = _write(raw_dir, "erp/purchase_orders", [
        {"po_number": "PO-2024-000001", "supplier_id": "SUP-APL", "supplier_name": "Apple", "supplier_role": "manufacturer",
         "contract_ref": "CTR-APL-01", "order_date": "2024-01-10", "promised_date": "2024-01-31", "currency": "EUR",
         "incoterm": "DAP", "payment_terms_days": 30},
    ])
    files["erp/po_lines"] = _write(raw_dir, "erp/po_lines", [
        {"po_number": "PO-2024-000001", "po_line": 1, "slug": "phone-a", "storage_gb": 128, "colour": "black", "qty_ordered": 2,
         "unit_price_eur": 700.00, "price_protection_days": 30, "order_date": "2024-01-10"},
    ])
    files["erp/goods_receipts"] = _write(raw_dir, "erp/goods_receipts", [
        {"gr_number": "GR-1", "po_number": "PO-2024-000001", "po_line": 1, "serial": "SN-APL-00000001", "received_at": "2024-01-25T10:00:00", "warehouse": "W1"},
        {"gr_number": "GR-1", "po_number": "PO-2024-000001", "po_line": 1, "serial": "SN-APL-00000002", "received_at": "2024-01-25T10:00:00", "warehouse": "W1"},
    ])
    files["erp/supplier_invoices"] = _write(raw_dir, "erp/supplier_invoices", [
        {"invoice_number": "INV-1", "invoice_line": 1, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "SN-APL-00000001", "invoice_date": "2024-01-30", "line_kind": "unit", "qty": 1, "amount_eur": 700.00, "currency": "EUR"},
        {"invoice_number": "INV-1", "invoice_line": 2, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "SN-APL-00000002", "invoice_date": "2024-01-30", "line_kind": "unit", "qty": 1, "amount_eur": 700.00, "currency": "EUR"},
        {"invoice_number": "INV-1", "invoice_line": 3, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "", "invoice_date": "2024-01-30", "line_kind": "freight", "qty": 2, "amount_eur": 10.01, "currency": "EUR"},
    ])
    files["erp/price_changes"] = _write(raw_dir, "erp/price_changes", [
        {"change_id": "PC-1", "supplier_id": "SUP-APL", "slug": "phone-a", "storage_gb": 128, "valid_from": "2024-02-10",
         "old_unit_price_eur": 700.00, "new_unit_price_eur": 650.00},
    ])
    files["wms/staging_log"] = _write(raw_dir, "wms/staging_log", [
        {"staging_id": "ST-1", "serial": "SN-APL-00000001", "staged_at": "2024-02-01T09:00:00", "mdm_enrolled": "true", "staging_cost_eur": 8.0},
        {"staging_id": "ST-2", "serial": "SN-APL-00000002", "staged_at": "2024-02-01T09:00:00", "mdm_enrolled": "true", "staging_cost_eur": 8.0},
    ])
    files["wms/shipments"] = _write(raw_dir, "wms/shipments", [
        {"shipment_id": "SH-1", "serial": "SN-APL-00000001", "direction": "outbound", "shipped_at": "2024-02-02T08:00:00",
         "delivered_at": "2024-02-03T08:00:00", "rental_contract_ref": "RC-1", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 6.5},
        {"shipment_id": "SH-2", "serial": "SN-APL-00000002", "direction": "outbound", "shipped_at": "2024-02-02T08:00:00",
         "delivered_at": "2024-02-03T08:00:00", "rental_contract_ref": "RC-2", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 6.5},
        {"shipment_id": "SH-3", "serial": "SN-APL-00000001", "direction": "return", "shipped_at": "2024-08-01T08:00:00",
         "delivered_at": "2024-08-03T08:00:00", "rental_contract_ref": "RC-1", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 7.0},
    ])
    files["portal/rental_contracts"] = _write(raw_dir, "portal/rental_contracts", [
        {"contract_id": "RC-1", "customer_id": "CUST-1", "serial": "SN-APL-00000001", "start_date": "2024-02-03", "term_months": 24,
         "monthly_rate_eur": 30.0, "end_date": "2026-02-03", "actual_end_date": "2024-08-01", "status": "terminated_early"},
        {"contract_id": "RC-2", "customer_id": "CUST-1", "serial": "SN-APL-00000002", "start_date": "2024-02-03", "term_months": 24,
         "monthly_rate_eur": 30.0, "end_date": "2026-02-03", "status": "active"},
    ])
    files["portal/rental_invoices"] = _write(raw_dir, "portal/rental_invoices", [
        {"invoice_id": f"RI-RC-1-{k:03d}", "contract_id": "RC-1", "serial": "SN-APL-00000001", "period_no": k,
         "period_month": f"2024-{2 + k:02d}-01", "invoice_date": common.billing_date(date(2024, 2, 3), k).isoformat(), "amount_eur": 30.0}
        for k in range(1, 6)
    ])
    files["servicedesk/tickets"] = _write(raw_dir, "servicedesk/tickets", [
        {"ticket_id": "TK-1", "serial": "SN-APL-00000001", "contract_id": "RC-1", "opened_at": "2024-05-01T10:00:00",
         "closed_at": "2024-05-08T10:00:00", "damage_type": "screen", "resolution": "repair", "quote_eur": 120.0, "repair_cost_eur": 110.0,
         "repair_partner_ref": "Refurbishment and repair partner (role-only)"},
    ])
    files["returns/receipts"] = _write(raw_dir, "returns/receipts", [
        {"receipt_id": "RR-1", "serial": "SN-APL-00000001", "contract_id": "RC-1", "returned_at": "2024-08-03T12:00:00",
         "grade_declared": "B", "grade_inspected": "C", "inspected_at": "2024-08-05T12:00:00", "wipe_certificate_id": "WIPE-1",
         "wiped_at": "2024-08-04T12:00:00", "wipe_grading_cost_eur": 5.0},
    ])
    files["refurb/work_orders"] = _write(raw_dir, "refurb/work_orders", [
        {"work_order_id": "WO-1", "serial": "SN-APL-00000001", "started_at": "2024-08-06T09:00:00", "finished_at": "2024-08-12T09:00:00",
         "cost_eur": 40.0, "grade_out": "B", "outcome": "sellable", "partner_ref": "Refurbishment and repair partner (role-only)"},
    ])
    files["recommerce/orders"] = _write(raw_dir, "recommerce/orders", [
        {"order_id": "RO-1", "serial": "SN-APL-00000001", "channel": "marketplace", "listed_at": "2024-08-13T09:00:00",
         "sold_at": "2024-09-01T09:00:00", "gross_price_eur": 420.0, "buyer_type": "consumer", "grade_at_sale": "B"},
    ])
    files["recommerce/credit_notes"] = _write(raw_dir, "recommerce/credit_notes", [
        {"credit_note_id": "CN-1", "order_id": "RO-1", "serial": "SN-APL-00000001", "channel": "marketplace", "credited_at": "2024-09-29T09:00:00",
         "gross_eur": 420.0, "fee_pct_eur": 50.4, "fee_fixed_eur": 2.5, "net_eur": 367.1},
    ])
    files["finance/indirect_spend"] = _write(raw_dir, "finance/indirect_spend", [
        {"spend_id": "IS-1", "invoice_date": "2024-03-01", "category": "logistics", "supplier_name": "Logistics partner (role-only)",
         "amount_eur": 1200.0, "has_po": "true", "has_contract": "true", "saving_eur": 0.0, "saving_confirmed_by_controlling": "false"},
    ])
    return files


@pytest.fixture
def lake_con():
    con = db.connect(":memory:")
    db.create_schema(con)
    schema_lake.create_lake_schema(con)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture
def world(tmp_path: Path):
    raw = tmp_path / "lake" / "raw"
    return raw, _world(raw)


def _count(con, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {db._q(table)}").fetchone()[0]


# ---------------------------------------------------------------------------------------
# schema and db
# ---------------------------------------------------------------------------------------


def test_db_accepts_schema_qualified_names():
    con = db.connect(":memory:")
    db.create_schema(con)
    schema_lake.create_lake_schema(con)
    assert db._q("bronze.x") == '"bronze"."x"' and db._q("x") == '"x"'
    frame = pd.DataFrame([{
        "po_number": "PO-1", "po_line": 1, "slug": "s", "storage_gb": 128, "colour": None, "qty_ordered": 1,
        "unit_price_eur": 1.0, "price_protection_days": None, "delivery_id": "d", "source_file": "f", "row_number": 1,
        "ingested_at": pd.Timestamp("2024-01-01"), "row_hash": "h", "is_synthetic": True,
    }])
    assert db.write_df(con, "bronze.erp_po_lines", frame, mode="append") == 1
    assert db.table_exists(con, "bronze.erp_po_lines")
    assert not db.table_exists(con, "erp_po_lines")
    assert db.table_exists(con, "devices") and not db.table_exists(con, "bronze.devices")
    assert _count(con, "bronze.erp_po_lines") == 1
    con.close()


def test_every_lake_table_created_in_memory():
    con = db.connect(":memory:")
    schema_lake.create_lake_schema(con)
    for name in schema_lake.LAKE_DDL:
        assert db.table_exists(con, name), name
    assert set(schema_lake.LAKE_TABLE_ORDER) == set(schema_lake.LAKE_DDL)
    for name, ddl in schema_lake.BRONZE_DDL.items():
        if name not in ("bronze.deliveries", "bronze.unresolved"):
            assert "row_hash VARCHAR NOT NULL" in ddl and "is_synthetic BOOLEAN NOT NULL" in ddl, name
    # rerun with the default drop layers is idempotent
    schema_lake.create_lake_schema(con)
    assert db.table_exists(con, "silver.ledger_lines")
    con.close()


def test_feeds_cover_every_bronze_table_and_order_respects_depends_on():
    tables = {f.bronze_table for f in FEEDS.values()}
    expected = set(schema_lake.BRONZE_DDL) - {"bronze.deliveries", "bronze.unresolved"}
    assert tables == expected
    assert len(FEEDS) == 19 and INGEST_ORDER == tuple(FEEDS)
    seen: set[str] = set()
    for key, spec in FEEDS.items():
        assert isinstance(spec, FeedSpec) and spec.key == key == f"{spec.source_system}/{spec.feed}"
        for dep in spec.depends_on:
            assert dep in seen, f"{key} depends on {dep} which comes later"
        for child, parent, pcol, reason in spec.resolves:
            assert reason in UNRESOLVED_REASONS
            assert parent in schema_lake.BRONZE_DDL
        assert spec.business_key and all(c in spec.column_names for c in spec.business_key)
        if spec.order_column is not None:
            assert spec.order_column in spec.column_names
        seen.add(key)
    assert FEEDS["erp/goods_receipts"].business_key == ("serial",)
    assert parse_landing_name("2024-03-31_goods_receipts_002.csv") == (date(2024, 3, 31), "goods_receipts", 2)
    with pytest.raises(ValueError):
        parse_landing_name("goods_receipts.csv")


# ---------------------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------------------


def test_ingest_types_dedupes_and_unresolves(lake_con, tmp_path: Path):
    raw = tmp_path / "raw"
    files = _world(raw)
    for key in ("catalogue/models", "catalogue/variants", "erp/purchase_orders", "erp/po_lines"):
        rep = ingest_file(lake_con, key, files[key])
        assert rep.rows_read == rep.rows_new and rep.n_unresolved == 0, rep.line()
    # goods receipts: one bad date, one identical duplicate inside the file
    good = {"gr_number": "GR-1", "po_number": "PO-2024-000001", "po_line": 1, "serial": "SN-APL-00000001", "received_at": "2024-01-25T10:00:00", "warehouse": "W1"}
    rows = [
        good,
        {**good, "serial": "SN-APL-00000002"},
        {**good, "serial": "SN-APL-00000003", "received_at": "2024-13-45T10:00:00"},   # bad_type
        {**good},                                                                       # identical duplicate
        {**good, "serial": "SN-APL-00000004", "po_line": 9},                            # unknown_po_line
    ]
    path = _write(raw, "erp/goods_receipts", rows, seq=7)
    rep = ingest_file(lake_con, "erp/goods_receipts", path)
    assert (rep.rows_read, rep.rows_typed, rep.rows_new, rep.duplicates_identical, rep.duplicates_conflict) == (5, 4, 2, 1, 0)
    assert rep.unresolved == {"bad_type": 1, "unknown_po_line": 1}
    assert "read=5 new=2 dup=1 conflict=0 unresolved=2 (bad_type=1, unknown_po_line=1)" in rep.line()
    assert _count(lake_con, "bronze.erp_goods_receipts") == 2
    unres = db.read_df(lake_con, "SELECT * FROM bronze.unresolved ORDER BY row_number")
    assert unres["reason_code"].tolist() == ["bad_type", "unknown_po_line"]
    assert unres["row_number"].tolist() == [3, 5]
    assert "2024-13-45" in unres.iloc[0]["row_json"] and '"serial": "SN-APL-00000003"' in unres.iloc[0]["key_json"]
    deliveries = db.read_df(lake_con, "SELECT * FROM bronze.deliveries WHERE feed = 'goods_receipts'")
    assert len(deliveries) == 1 and deliveries.iloc[0]["n_unresolved"] == 2 and deliveries.iloc[0]["delivered_on"] == pd.Timestamp("2024-12-31")

    # supplier invoices: a negative amount and an unknown serial
    inv_rows = [
        {"invoice_number": "INV-1", "invoice_line": 1, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "SN-APL-00000001", "invoice_date": "2024-01-30", "line_kind": "unit", "qty": 1, "amount_eur": 700.00, "currency": "EUR"},
        {"invoice_number": "INV-1", "invoice_line": 2, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "SN-APL-00000002", "invoice_date": "2024-01-30", "line_kind": "unit", "qty": 1, "amount_eur": -700.00, "currency": "EUR"},
        {"invoice_number": "INV-1", "invoice_line": 3, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "SN-APL-99999999", "invoice_date": "2024-01-30", "line_kind": "unit", "qty": 1, "amount_eur": 700.00, "currency": "EUR"},
        {"invoice_number": "INV-1", "invoice_line": 4, "supplier_id": "SUP-APL", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "", "invoice_date": "2024-01-30", "line_kind": "gift", "qty": 2, "amount_eur": 10.01, "currency": "EUR"},
    ]
    rep = ingest_file(lake_con, "erp/supplier_invoices", _write(raw, "erp/supplier_invoices", inv_rows, seq=7))
    assert rep.rows_new == 1 and rep.rows_typed == 2
    assert rep.unresolved == {"negative_amount": 1, "unknown_serial": 1, "bad_enum": 1}
    assert _count(lake_con, "bronze.erp_supplier_invoices") == 1

    # a conflicting duplicate in a later delivery never replaces the existing row
    conflict = [{**good, "warehouse": "W2"}]
    rep = ingest_file(lake_con, "erp/goods_receipts", _write(raw, "erp/goods_receipts", conflict, delivered_on=date(2025, 1, 31), seq=1))
    assert (rep.rows_read, rep.rows_new, rep.duplicates_identical, rep.duplicates_conflict, rep.n_unresolved) == (1, 0, 0, 1, 0)
    stored = db.read_df(lake_con, "SELECT warehouse, source_file FROM bronze.erp_goods_receipts WHERE serial = 'SN-APL-00000001'")
    assert stored.iloc[0]["warehouse"] == "W1" and stored.iloc[0]["source_file"].startswith("2024-12-31")
    conf = db.read_df(lake_con, "SELECT * FROM bronze.unresolved WHERE reason_code = 'duplicate_conflict'")
    assert len(conf) == 1 and conf.iloc[0]["feed"] == "goods_receipts"
    # an identical row in a later delivery is counted, not stored twice
    rep = ingest_file(lake_con, "erp/goods_receipts", _write(raw, "erp/goods_receipts", [good], delivered_on=date(2025, 2, 28), seq=1))
    assert rep.rows_new == 0 and rep.duplicates_identical == 1 and rep.duplicates_conflict == 0
    assert _count(lake_con, "bronze.erp_goods_receipts") == 2
    all_reasons = set(db.read_df(lake_con, "SELECT DISTINCT reason_code FROM bronze.unresolved")["reason_code"])
    assert all_reasons <= set(UNRESOLVED_REASONS)


def test_ingest_dry_run_writes_nothing(lake_con, world):
    raw, files = world
    rep = ingest_file(lake_con, "catalogue/models", files["catalogue/models"], dry_run=True)
    assert rep.dry_run and rep.rows_new == 3 and not rep.already_ingested
    assert _count(lake_con, "bronze.cat_models") == 0
    assert _count(lake_con, "bronze.deliveries") == 0
    assert _count(lake_con, "runs") == 0
    summary = run_ingest(lake_con, raw, None, None, dry_run=True)
    assert summary.counts["files"] == 19 and summary.run_id == "ingest-dry-run"
    assert _count(lake_con, "bronze.deliveries") == 0 and _count(lake_con, "runs") == 0
    for spec in FEEDS.values():
        assert _count(lake_con, spec.bronze_table) == 0, spec.key
    assert _count(lake_con, "gold.ingest_summary") == 0


def test_ingest_same_file_twice_is_noop(lake_con, world):
    raw, files = world
    first = ingest_file(lake_con, "catalogue/models", files["catalogue/models"])
    second = ingest_file(lake_con, "catalogue/models", files["catalogue/models"])
    assert not first.already_ingested and second.already_ingested
    assert (second.rows_read, second.rows_new, second.duplicates_identical) == (first.rows_read, first.rows_new, first.duplicates_identical)
    assert second.delivery_id == first.delivery_id == common.delivery_id(first.sha256)
    assert _count(lake_con, "bronze.cat_models") == 3 and _count(lake_con, "bronze.deliveries") == 1
    reports = ingest_all(lake_con, raw)
    assert len(reports) == 19 and sum(r.already_ingested for r in reports) == 1
    again = ingest_all(lake_con, raw)
    assert all(r.already_ingested for r in again)
    assert _count(lake_con, "bronze.deliveries") == 19
    summary = db.read_df(lake_con, "SELECT * FROM gold.ingest_summary")
    assert len(summary) == 19 and int(summary["n_files"].sum()) == 19
    assert summary.set_index("feed").loc["erp/goods_receipts", "bronze_rows"] == 2


def test_read_landing_file_refuses_missing_flag_and_reference_synthetic(tmp_path: Path, lake_con):
    bad = tmp_path / "2024-12-31_models_001.csv"
    bad.write_text("# SYNTHETIC DATA - test\nslug,model_name\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="is_synthetic"):
        read_landing_file(bad)
    raw = tmp_path / "raw"
    path = _write(raw, "catalogue/models", [{"slug": "x", "model_name": "X", "oem": "Apple", "family": "Smartphone"}], synthetic=True)
    with pytest.raises(ValueError, match="reference"):
        ingest_file(lake_con, "catalogue/models", path)
    with pytest.raises(ValueError, match="unknown feed key"):
        ingest_file(lake_con, "erp/nothing", path)


# ---------------------------------------------------------------------------------------
# conform
# ---------------------------------------------------------------------------------------


def test_conform_frames_validate_and_allocate_cents(lake_con, world, assumptions, tmp_path: Path):
    raw, _ = world
    ingest_all(lake_con, raw)
    bronze = read_bronze(lake_con)
    fees = assumptions.get("channel_fees")
    frames, excluded = conform_frames(bronze, 0.19, 0.12, fees, AS_OF)
    assert set(frames) == set(schema.SOURCE_TABLES)
    for table, df in frames.items():
        schema.validate_frame(table, df)  # raises on a bad row
        assert list(df.columns) == list(schema.ROW_MODELS[table].model_fields), table
        assert df["is_synthetic"].eq(True).all() if len(df) else True, table
    db.referential_checks(frames)
    assert excluded == ["tablet-c"]  # no launch date

    dev = frames["devices"].set_index("serial")
    assert dev.loc["SN-APL-00000001", "landed_cost"] == pytest.approx(705.00)
    assert dev.loc["SN-APL-00000002", "landed_cost"] == pytest.approx(705.01)
    assert float(dev["landed_cost"].sum() - dev["purchase_price"].sum()) == pytest.approx(10.01)
    assert dev.loc["SN-APL-00000001", "model_family"] == "iphone_like"
    assert dev.loc["SN-APL-00000001", "channel_in"] == "oem_direct"
    assert dev.loc["SN-APL-00000001", "po_number"] == "PO-2024-000001-1"
    assert dev.loc["SN-APL-00000001", "contract_id"] == "RC-1"
    assert dev.loc["SN-APL-00000001", "purchase_date"] == date(2024, 1, 25)
    assert common.allocate_cents(10.01, 2) == [5.00, 5.01]
    assert common.allocate_cents(10.01, 3) == [3.33, 3.33, 3.35]
    shares = allocate_over_serials(
        pd.DataFrame({"po_number": ["P"], "po_line": [1], "amount_eur": [10.01]}),
        pd.DataFrame({"po_number": ["P", "P"], "po_line": [1, 1], "serial": ["b", "a"]}), "amount_eur",
    )
    assert shares.to_dict() == {"a": 5.00, "b": 5.01}

    cat = frames["model_catalogue"].set_index("model")
    assert cat.loc["phone-a", "list_price"] == common.rrp_net(949, 0.19) == 797.48
    assert cat.loc["phone-a", "base_storage_gb"] == 128 and cat.loc["phone-a", "model_family"] == "iphone_like"
    assert cat.loc["laptop-b", "launch_date"] == date(2023, 5, 15)  # YYYY-MM takes the 15th
    assert cat.loc["laptop-b", "model_family"] == "laptop_like"

    po = frames["purchase_orders"].set_index("po_number")
    assert po.loc["PO-2024-000001-1", "supplier_contract_id"] == "CTR-APL-01"
    assert po.loc["PO-2024-000001-1", "qty_delivered"] == 2 and po.loc["PO-2024-000001-1", "delivered_date"] == date(2024, 1, 25)
    assert po.loc["PO-2024-000001-1", "benchmark_price"] == pytest.approx(round(797.48 * 0.88, 2))
    assert po.loc["PO-2024-000001-1", "price_drop_date"] == date(2024, 2, 10)
    assert po.loc["PO-2024-000001-1", "price_drop_amount"] == pytest.approx(50.0)

    ev = frames["events"].set_index("event_id")
    assert set(ev.index) == {"EV-T-TK-1-D", "EV-T-TK-1-R", "EV-R-RR-1"}
    assert ev.loc["EV-T-TK-1-D", "cost"] == 0.0 and ev.loc["EV-T-TK-1-D", "resolved"] is True
    assert ev.loc["EV-T-TK-1-R", "cost"] == 110.0 and ev.loc["EV-T-TK-1-R", "event_date"] == date(2024, 5, 8)
    assert ev.loc["EV-R-RR-1", "cost"] == 7.0 and ev.loc["EV-R-RR-1", "grade_inspected"] == "C" and ev.loc["EV-R-RR-1", "wipe_certificate"] is True
    rs = frames["resale"].set_index("sale_id")
    assert rs.loc["RO-1", "fees"] == pytest.approx(52.9) and rs.loc["RO-1", "price"] == 420.0
    sc = frames["supplier_contracts"].set_index("supplier_contract_id")
    assert list(sc.index) == ["CTR-APL-01"] and sc.loc["CTR-APL-01", "price_protection_days"] == 14
    assert frames["indirect_spend"].iloc[0]["supplier"] == "Logistics partner (role-only)"

    # run_conform writes main and the compat copies the v0.1 loader accepts
    csv_dir = tmp_path / "raw_csv"
    summary = run_conform(lake_con, AS_OF, assumptions, csv_dir=csv_dir, seed=1)
    assert summary.counts["devices"] == 2 and summary.counts["excluded_slugs"] == 1
    assert _count(lake_con, "devices") == 2 and _count(lake_con, "model_catalogue") == 2
    first = (csv_dir / "devices.csv").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# SYNTHETIC DATA - conformed from data/lake by restwert conform seed=1")
    scratch = db.connect(":memory:")
    db.create_schema(scratch)
    counts = db.load_csv_dir(scratch, csv_dir)
    assert counts["devices"] == 2 and counts["events"] == 3
    scratch.close()


def test_write_compat_csvs_marks_real_data(tmp_path: Path):
    frames = {t: pd.DataFrame(columns=list(schema.ROW_MODELS[t].model_fields)) for t in schema.SOURCE_TABLES}
    frames["devices"] = pd.DataFrame([{"serial": "S", "is_synthetic": False}])
    paths = write_compat_csvs(frames, tmp_path, None)
    assert len(paths) == 10
    assert (tmp_path / "devices.csv").read_text(encoding="utf-8").startswith("# REAL DATA")
    assert (tmp_path / "events.csv").read_text(encoding="utf-8").startswith("# SYNTHETIC DATA")


# ---------------------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------------------


def _bronze_for_timeline() -> dict[str, pd.DataFrame]:
    """One serial per status, each with exactly its expected steps present."""
    serials = {
        "S-nd": "not_deployed", "S-re": "rented", "S-aw": "awaiting_return", "S-wip": "wip",
        "S-is": "in_stock", "S-so": "sold", "S-sc": "scrapped", "S-bad": "sold",
    }
    gr = pd.DataFrame({"serial": list(serials), "po_number": "PO-1", "po_line": 1, "gr_number": "GR-1",
                       "received_at": "2024-01-20T10:00:00", "is_synthetic": True})
    po = pd.DataFrame({"po_number": ["PO-1"], "order_date": ["2024-01-05"]})
    shipped = [s for s in serials if s != "S-nd"]
    staging = pd.DataFrame({"staging_id": [f"ST-{s}" for s in shipped], "serial": shipped, "staged_at": "2024-02-01T09:00:00"})
    ship = pd.DataFrame({"shipment_id": [f"SH-{s}" for s in shipped], "serial": shipped, "direction": "outbound", "shipped_at": "2024-02-02T09:00:00"})
    returned = ["S-wip", "S-is", "S-so", "S-sc", "S-bad"]
    receipts = pd.DataFrame({
        "receipt_id": [f"RR-{s}" for s in returned], "serial": returned, "returned_at": "2024-08-01T09:00:00",
        "wiped_at": [None, "2024-08-02T09:00:00", "2024-08-02T09:00:00", "2024-08-02T09:00:00", "2024-08-02T09:00:00"],
        "inspected_at": [None, "2024-08-03T09:00:00", "2024-08-03T09:00:00", "2024-08-03T09:00:00", "2024-08-03T09:00:00"],
    })
    wo = pd.DataFrame({
        "work_order_id": ["WO-is", "WO-so", "WO-sc", "WO-bad"], "serial": ["S-is", "S-so", "S-sc", "S-bad"],
        "finished_at": ["2024-08-10T09:00:00", "2024-08-10T09:00:00", "2024-08-10T09:00:00", "2024-07-20T09:00:00"],
        "outcome": ["sellable", "sellable", "scrap", "sellable"],
    })
    orders = pd.DataFrame({"order_id": ["RO-so", "RO-bad"], "serial": ["S-so", "S-bad"], "sold_at": ["2024-09-01T09:00:00", "2024-09-01T09:00:00"]})
    cn = pd.DataFrame({"credit_note_id": ["CN-so", "CN-bad"], "serial": ["S-so", "S-bad"], "credited_at": ["2024-09-20T09:00:00", "2025-03-01T09:00:00"]})
    status = pd.DataFrame({"serial": list(serials), "lifecycle_status": list(serials.values())})
    bronze = {"erp_goods_receipts": gr, "erp_purchase_orders": po, "wms_staging_log": staging, "wms_shipments": ship,
              "ret_receipts": receipts, "rf_work_orders": wo, "rc_orders": orders, "rc_credit_notes": cn}
    return bronze, status


def test_timeline_expected_steps_per_status():
    bronze, status = _bronze_for_timeline()
    tl = build_timeline(bronze, status, AS_OF).set_index("serial")
    for serial, st in status.set_index("serial")["lifecycle_status"].items():
        if serial == "S-bad":
            continue
        row = tl.loc[serial]
        assert row["lifecycle_status"] == st
        assert row["steps_expected"] == len(EXPECTED_STEPS[st]), serial
        assert row["steps_present"] == len(EXPECTED_STEPS[st]), (serial, row["missing_steps"])
        assert row["chain_complete"] and row["is_monotonic"] and pd.isna(row["missing_steps"]), serial
        for step in STEPS:
            assert (row[step] is not None and not pd.isna(row[step])) == (step in EXPECTED_STEPS[st]), (serial, step)
    assert tl.loc["S-so", "days_return_to_cash"] == 50 and tl.loc["S-so", "days_sold_to_credited"] == 19
    assert tl.loc["S-nd", "days_order_to_receipt"] == 15 and pd.isna(tl.loc["S-nd", "days_receipt_to_ship"])
    assert '"ordered_at": "erp:PO-1"' in tl.loc["S-so", "source_refs_json"] and '"sold_at": "recommerce:RO-so"' in tl.loc["S-so", "source_refs_json"]
    # a credit note after as_of is not a step yet: the sold chain is incomplete with credited_at as the first missing step
    assert tl.loc["S-bad", "steps_present"] == 9 and tl.loc["S-bad", "first_missing_step"] == "credited_at"
    quality = chain_quality(tl.reset_index(), AS_OF)
    q = quality.set_index(["lifecycle_status", "step"])
    assert q.loc[("sold", "credited_at"), "n_present"] == 1 and q.loc[("sold", "credited_at"), "n_serials"] == 2
    assert q.loc[("rented", "returned_at"), "is_expected"] is False or not q.loc[("rented", "returned_at"), "is_expected"]
    assert set(quality["step"]) == set(STEPS)


def test_timeline_non_monotonic_flagged():
    bronze, status = _bronze_for_timeline()
    tl = build_timeline(bronze, status, AS_OF).set_index("serial")
    bad = tl.loc["S-bad"]
    # sellable (work order finished 2024-07-20) lies before the return (2024-08-01)
    assert not bad["is_monotonic"] and bad["non_monotonic_pair"] == "graded_at>sellable_at"
    assert not bad["chain_complete"]
    assert tl["is_monotonic"].drop("S-bad").all()
    # without a status table every serial is not_deployed and complete on its two steps,
    # except the one whose present steps are out of order (monotonicity is checked on every present step)
    tl2 = build_timeline(bronze, pd.DataFrame(columns=["serial", "lifecycle_status"]), AS_OF).set_index("serial")
    assert (tl2["lifecycle_status"] == "not_deployed").all() and (tl2["steps_expected"] == 2).all()
    assert tl2["chain_complete"].drop("S-bad").all() and not tl2.loc["S-bad", "chain_complete"]


# ---------------------------------------------------------------------------------------
# docs
# ---------------------------------------------------------------------------------------


def test_data_lake_md_renders_every_feed_and_has_no_em_dash():
    md = render_data_lake_md()
    for key in FEEDS:
        assert f"### `{key}`" in md, key
    for name in schema_lake.LAKE_DDL:
        assert f"#### `{name}`" in md, name
    for reason in UNRESOLVED_REASONS:
        assert f"`{reason}`" in md
    assert "vat_rate" in md and "allocate_cents" in md and "chain_complete" in md
    assert chr(0x2014) not in md
    assert "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD." in md


# ---------------------------------------------------------------------------------------
# pipeline (session lake DB of module 6; skipped until that fixture exists)
# ---------------------------------------------------------------------------------------


def test_lake_pipeline_bronze_rows_and_unresolved(request):
    try:
        con = request.getfixturevalue("lake_pipeline_db")
    except pytest.FixtureLookupError:
        pytest.skip("session fixture lake_pipeline_db (module 6) not available")
    for spec in FEEDS.values():
        assert _count(con, spec.bronze_table) > 0, spec.key
    unres = db.read_df(con, "SELECT reason_code, count(*) AS n FROM bronze.unresolved GROUP BY reason_code")
    assert len(unres) > 0, "bronze.unresolved must be non-empty (injected defects)"
    assert set(unres["reason_code"]) <= set(UNRESOLVED_REASONS)
    minted = set(db.read_df(con, "SELECT serial FROM bronze.erp_goods_receipts")["serial"])
    for spec in FEEDS.values():
        if spec.serial_column is None or spec.key == "erp/goods_receipts":
            continue
        serials = db.read_df(con, f'SELECT DISTINCT "{spec.serial_column}" AS s FROM {db._q(spec.bronze_table)} WHERE "{spec.serial_column}" IS NOT NULL')["s"]
        outside = set(serials) - minted
        assert not outside, f"{spec.key}: {sorted(outside)[:5]} not minted by goods_receipts"
    deliveries = db.read_df(con, "SELECT * FROM bronze.deliveries")
    assert len(deliveries) > 0 and deliveries["sha256"].is_unique
    tl = db.read_df(con, "SELECT count(*) AS n, sum(CASE WHEN chain_complete THEN 1 ELSE 0 END) AS ok FROM silver.serial_timeline")
    assert int(tl.iloc[0]["n"]) == len(minted) and int(tl.iloc[0]["ok"]) > 0
