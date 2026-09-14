"""Contracts register v2 tests (SPEC_v0.2 section 8.5).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Hand rows with absolute expectations for the pure functions; the session lake
database (module 6 fixture ``lake_pipeline_db``) for R05 and R06. When that
fixture is not available yet the pipeline tests skip instead of failing, so
this module stays green while the other modules land.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from restwert import db
from restwert.contracts import counterparties as cp
from restwert.contracts.register import contracts_register, renewal_calendar
from restwert.contracts.register_v2 import (
    CALENDAR_V2_COLUMNS,
    COVERAGE_COLUMNS,
    REBATE_COLUMNS,
    SILVER_CONTRACT_COLUMNS,
    contracts_v2,
    coverage_by_oem,
    price_protection_windows_open,
    rebate_progress,
    renewal_calendar_v2,
    run_contracts_v2,
)
from restwert.lake.common import MANUFACTURERS, ROLE_ONLY_SUFFIX
from restwert.lake.schema_lake import LAKE_DDL, create_lake_schema

AS_OF = date(2026, 6, 30)


def _lake_db(request):
    """The session lake DB from module 6's conftest, or skip while it is not there."""

    try:
        return request.getfixturevalue("lake_pipeline_db")
    except pytest.FixtureLookupError:
        pytest.skip("lake_pipeline_db fixture (module 6 conftest) not available")


def _ctr_row(contract_id: str, name: str, role: str, category: str, start: str, end: str, **over) -> dict:
    row = dict(
        contract_id=contract_id,
        counterparty_name=name,
        counterparty_role=role,
        counterparty_is_public=name in MANUFACTURERS,
        category=category,
        start_date=start,
        end_date=end,
        notice_days=90,
        auto_renewal=False,
        price_protection=False,
        price_protection_days=None,
        claim_window_days=None,
        warranty_months=None,
        rebate_tiers_json=None,
        volume_commitment_units=None,
        payment_terms_days=30,
        sla_json="{}",
        spend_under_contract_eur=100000.0,
        terms_note=cp.TERMS_NOTE,
        is_synthetic=True,
    )
    row.update(over)
    return row


@pytest.fixture
def hand_register() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # active, notice deadline 2026-07-02 (2 days ahead): action required
            _ctr_row("CTR-APL", "Apple", "manufacturer", "hardware", "2024-01-01", "2026-09-30",
                     price_protection=True, price_protection_days=30, claim_window_days=14, warranty_months=12),
            # active reseller with rebate tiers, auto renewal, far end: no action (outside the horizon)
            _ctr_row("CTR-RSA", "IT reseller A" + ROLE_ONLY_SUFFIX, "reseller", "hardware", "2024-01-01", "2027-12-31",
                     notice_days=60, auto_renewal=True, price_protection=True, price_protection_days=30, claim_window_days=14,
                     rebate_tiers_json=json.dumps([{"from_eur": 0, "pct": 0.0}, {"from_eur": 1000, "pct": 0.01}, {"from_eur": 5000, "pct": 0.02}]),
                     spend_under_contract_eur=5000.0),
            # expired
            _ctr_row("CTR-DEL", "Dell", "manufacturer", "hardware", "2022-01-01", "2026-01-31", warranty_months=36, payment_terms_days=45),
            # future marketplace
            _ctr_row("CTR-MKA", "Marketplace channel A" + ROLE_ONLY_SUFFIX, "marketplace", "resale_channel", "2026-08-01", "2028-07-31",
                     notice_days=30, payment_terms_days=28, spend_under_contract_eur=1000.0),
            # refurbishment and logistics rows fed by ledger lines
            _ctr_row("CTR-RFB", "Refurbishment and repair partner" + ROLE_ONLY_SUFFIX, "refurb_repair", "refurbishment", "2024-01-01", "2027-06-30"),
            _ctr_row("CTR-LOG", "Logistics partner" + ROLE_ONLY_SUFFIX, "logistics", "logistics", "2024-01-01", "2027-06-30"),
            # carrier fed by indirect spend of category connectivity
            _ctr_row("CTR-CAR", "Carrier partner" + ROLE_ONLY_SUFFIX, "carrier", "connectivity", "2024-01-01", "2027-06-30"),
        ]
    )


@pytest.fixture
def hand_purchases() -> dict[str, pd.DataFrame]:
    po_headers = pd.DataFrame(
        [
            dict(po_number="PO-1", supplier_id="S-RSA", supplier_name="IT reseller A" + ROLE_ONLY_SUFFIX, supplier_role="reseller", contract_ref="CTR-RSA", order_date="2026-03-01"),
            dict(po_number="PO-2", supplier_id="S-APL", supplier_name="Apple", supplier_role="manufacturer", contract_ref="CTR-APL", order_date="2026-02-01"),
            dict(po_number="PO-3", supplier_id="S-APL", supplier_name="Apple", supplier_role="manufacturer", contract_ref=None, order_date="2026-02-01"),
            # expired Dell contract referenced after its end: not covered
            dict(po_number="PO-4", supplier_id="S-DEL", supplier_name="Dell", supplier_role="manufacturer", contract_ref="CTR-DEL", order_date="2026-03-01"),
        ]
    )
    po_lines = pd.DataFrame(
        [
            dict(po_number="PO-1", po_line=1, slug="iphone-15", storage_gb=128, unit_price_eur=700.0, price_protection_days=30),
            dict(po_number="PO-2", po_line=1, slug="iphone-15", storage_gb=128, unit_price_eur=720.0, price_protection_days=30),
            dict(po_number="PO-3", po_line=1, slug="iphone-15", storage_gb=128, unit_price_eur=720.0, price_protection_days=30),
            dict(po_number="PO-4", po_line=1, slug="latitude-5540", storage_gb=512, unit_price_eur=900.0, price_protection_days=None),
        ]
    )
    goods_receipts = pd.DataFrame(
        [
            dict(serial="S1", po_number="PO-1", po_line=1, received_at="2026-03-20 10:00:00"),
            dict(serial="S2", po_number="PO-1", po_line=1, received_at="2026-03-20 10:00:00"),
            dict(serial="S3", po_number="PO-2", po_line=1, received_at="2026-02-20 10:00:00"),
            dict(serial="S4", po_number="PO-3", po_line=1, received_at="2026-02-20 10:00:00"),
            dict(serial="S5", po_number="PO-4", po_line=1, received_at="2026-03-25 10:00:00"),
            # outside the trailing 12 months: counted as a serial under contract, not as spend
            dict(serial="S6", po_number="PO-2", po_line=1, received_at="2024-02-20 10:00:00"),
        ]
    )
    cat_models = pd.DataFrame([dict(slug="iphone-15", oem="Apple"), dict(slug="latitude-5540", oem="Dell")])
    credit_notes = pd.DataFrame([dict(channel="marketplace", credited_at="2026-05-01", gross_eur=300.0), dict(channel="b2b_wholesale", credited_at="2026-05-01", gross_eur=999.0)])
    lines = pd.DataFrame(
        [
            dict(line_type="refurbishment", event_date="2026-04-01", amount_eur=-40.0),
            dict(line_type="repair", event_date="2026-04-01", amount_eur=-120.0),
            dict(line_type="outbound_shipping", event_date="2026-04-02", amount_eur=-6.0),
            dict(line_type="return_logistics", event_date="2026-04-03", amount_eur=-7.0),
            dict(line_type="replacement_logistics", event_date="2026-04-04", amount_eur=-8.0),
            dict(line_type="outbound_shipping", event_date="2024-04-02", amount_eur=-99.0),  # outside window
        ]
    )
    indirect = pd.DataFrame([dict(invoice_date="2026-05-10", category="connectivity", supplier_name="Carrier partner" + ROLE_ONLY_SUFFIX, amount_eur=250.0)])
    price_changes = pd.DataFrame([dict(change_id="PC-1", supplier_id="S-RSA", slug="iphone-15", storage_gb=128, valid_from="2026-04-05", old_unit_price_eur=700.0, new_unit_price_eur=650.0)])
    return dict(
        po_headers=po_headers, po_lines=po_lines, goods_receipts=goods_receipts, cat_models=cat_models,
        credit_notes=credit_notes, lines=lines, indirect=indirect, price_changes=price_changes,
    )


# ---------------------------------------------------------------------------
# counterparties
# ---------------------------------------------------------------------------


def test_every_counterparty_name_is_allowed():
    assert len(cp.COUNTERPARTIES) >= 20
    for party in cp.COUNTERPARTIES:
        assert cp.is_allowed_name(party.name), party.name
        assert party.is_public == (party.name in MANUFACTURERS)
    names = {p.name for p in cp.COUNTERPARTIES}
    assert set(MANUFACTURERS) <= names, "every catalogue manufacturer is a counterparty"
    assert set(cp.MANUFACTURER_NAMES) == set(MANUFACTURERS)
    assert all(n.endswith(ROLE_ONLY_SUFFIX) for n in cp.ROLE_ONLY_NAMES)
    assert {"rugged_oem", "reseller", "carrier", "refurb_repair", "logistics", "marketplace", "financing", "mtd"} <= set(cp.ROLES)
    assert not cp.is_allowed_name("Some Company GmbH")
    assert not cp.is_allowed_name("apple")  # exact catalogue spelling only
    assert cp.is_allowed_name("Anything" + ROLE_ONLY_SUFFIX)
    assert "no term is from any provider" in cp.TERMS_NOTE
    assert chr(0x2014) not in cp.TERMS_NOTE


def test_counterparty_terms_as_specified():
    apple = cp.counterparty_by_name("Apple")
    assert apple.price_protection_days == 30 and apple.claim_window_days == 14 and apple.rebate_tiers is None
    samsung = cp.counterparty_by_name("Samsung")
    assert samsung.price_protection_days == 60 and samsung.claim_window_days == 30
    assert [t["from_eur"] for t in samsung.rebate_tiers] == [0, 250000, 750000]
    for name in ("Motorola", "Fairphone", "HMD Global (Nokia)", "Microsoft"):
        assert cp.counterparty_by_name(name).price_protection_days is None
    assert {p.name for p in cp.COUNTERPARTIES if p.rebate_tiers} == {"Samsung", "Lenovo", "Dell", "HP"}
    refurb = [p for p in cp.COUNTERPARTIES if p.role == "refurb_repair"]
    assert sorted(p.category for p in refurb) == ["refurbishment", "repair"]
    rugged = cp.counterparty_by_name("Rugged-device OEM" + ROLE_ONLY_SUFFIX)
    assert rugged.role == "rugged_oem" and rugged.warranty_months == 36
    mtd = cp.counterparty_by_name("Mobile threat defense partner" + ROLE_ONLY_SUFFIX)
    assert mtd.category == "security_software" and mtd.sla["per_device_month_eur"] == 1.2


# ---------------------------------------------------------------------------
# silver.contracts
# ---------------------------------------------------------------------------


def test_contracts_v2_status_and_deadlines(hand_register, hand_purchases):
    p = hand_purchases
    out = contracts_v2(hand_register, p["po_headers"], p["po_lines"], p["goods_receipts"], p["credit_notes"], p["lines"], p["indirect"], AS_OF, cat_models=p["cat_models"])
    assert list(out.columns) == list(SILVER_CONTRACT_COLUMNS)
    assert len(out) == len(hand_register)
    by_id = out.set_index("contract_id")
    assert by_id.loc["CTR-APL", "status"] == "active"
    assert by_id.loc["CTR-DEL", "status"] == "expired"
    assert by_id.loc["CTR-MKA", "status"] == "future"
    # notice deadline = end - notice days, exact
    assert pd.Timestamp(by_id.loc["CTR-APL", "notice_deadline"]) == pd.Timestamp("2026-07-02")
    assert int(by_id.loc["CTR-APL", "days_to_notice_deadline"]) == 2
    assert int(by_id.loc["CTR-APL", "days_to_end"]) == 92
    assert int(by_id.loc["CTR-DEL", "days_to_end"]) == -150
    assert pd.Timestamp(by_id.loc["CTR-RSA", "notice_deadline"]) == pd.Timestamp("2027-11-01")
    # action_required is the v0.1 verdict: inside the horizon with a deadline within two months
    assert bool(by_id.loc["CTR-APL", "action_required"]) is True
    assert bool(by_id.loc["CTR-RSA", "action_required"]) is False  # auto renewal but outside the horizon
    assert bool(by_id.loc["CTR-DEL", "action_required"]) is False
    # spend per role, trailing 12 months
    assert by_id.loc["CTR-APL", "spend_actual_12m_eur"] == pytest.approx(720.0)  # S3 only, S6 outside the window
    assert int(by_id.loc["CTR-APL", "n_serials_under_contract"]) == 2
    assert by_id.loc["CTR-RSA", "spend_actual_12m_eur"] == pytest.approx(1400.0)
    assert by_id.loc["CTR-RSA", "covers_oems"] == "Apple"
    assert by_id.loc["CTR-DEL", "spend_actual_12m_eur"] == pytest.approx(900.0)  # spend is spend, coverage is judged elsewhere
    assert by_id.loc["CTR-MKA", "spend_actual_12m_eur"] == pytest.approx(300.0)  # marketplace channel only
    assert by_id.loc["CTR-RFB", "spend_actual_12m_eur"] == pytest.approx(40.0)  # refurbishment category, not repair
    assert by_id.loc["CTR-LOG", "spend_actual_12m_eur"] == pytest.approx(21.0)  # 6 + 7 + 8, the 99 is outside the window
    assert by_id.loc["CTR-CAR", "spend_actual_12m_eur"] == pytest.approx(250.0)
    assert by_id.loc["CTR-RSA", "spend_actual_vs_planned_pct"] == pytest.approx(0.28)
    assert (out["terms_note"] == cp.TERMS_NOTE).all()
    assert out["is_synthetic"].all()


def test_contracts_v2_without_transactions_keeps_rows_and_nulls_spend(hand_register):
    out = contracts_v2(hand_register, None, None, None, None, None, None, AS_OF)
    assert len(out) == len(hand_register)
    assert out["spend_actual_12m_eur"].isna().all()
    assert out["covers_oems"].isna().all()
    assert set(out["status"]) == {"active", "expired", "future"}


def test_contracts_v2_empty_register():
    out = contracts_v2(pd.DataFrame(), None, None, None, None, None, None, AS_OF)
    assert out.empty and list(out.columns) == list(SILVER_CONTRACT_COLUMNS)


# ---------------------------------------------------------------------------
# gold.contract_coverage_by_oem
# ---------------------------------------------------------------------------


def test_coverage_by_oem_counts_reseller_spend_for_the_oem(hand_register, hand_purchases):
    p = hand_purchases
    cov = coverage_by_oem(p["po_headers"], p["po_lines"], p["goods_receipts"], p["cat_models"], hand_register, AS_OF)
    assert list(cov.columns) == list(COVERAGE_COLUMNS)
    by = cov.set_index("oem")
    # Apple: S1, S2 via reseller (1400, covered by CTR-RSA), S3 direct under CTR-APL (720), S4 direct without contract (720)
    assert int(by.loc["Apple", "n_units"]) == 4
    assert by.loc["Apple", "spend_total"] == pytest.approx(2840.0)
    assert by.loc["Apple", "spend_under_contract"] == pytest.approx(2120.0)
    assert by.loc["Apple", "coverage_pct"] == pytest.approx(2120.0 / 2840.0)
    assert by.loc["Apple", "spend_direct"] == pytest.approx(1440.0)
    assert by.loc["Apple", "spend_via_reseller"] == pytest.approx(1400.0)
    assert int(by.loc["Apple", "n_contracts_in_force"]) == 2  # CTR-APL and CTR-RSA
    assert pd.Timestamp(by.loc["Apple", "next_notice_deadline"]) == pd.Timestamp("2026-07-02")
    # Dell: the referenced contract had expired at the order date, so nothing is covered
    assert by.loc["Dell", "spend_total"] == pytest.approx(900.0)
    assert by.loc["Dell", "spend_under_contract"] == pytest.approx(0.0)
    assert by.loc["Dell", "coverage_pct"] == pytest.approx(0.0)
    assert int(by.loc["Dell", "n_contracts_in_force"]) == 0


# ---------------------------------------------------------------------------
# gold.renewal_calendar_v2
# ---------------------------------------------------------------------------


def test_renewal_calendar_v2_matches_v01_action_required(hand_register, hand_purchases):
    p = hand_purchases
    contracts = contracts_v2(hand_register, p["po_headers"], p["po_lines"], p["goods_receipts"], p["credit_notes"], p["lines"], p["indirect"], AS_OF, cat_models=p["cat_models"])
    cal2 = renewal_calendar_v2(contracts, p["po_lines"], p["price_changes"], p["goods_receipts"], AS_OF, po_headers=p["po_headers"])
    assert list(cal2.columns) == list(CALENDAR_V2_COLUMNS)
    # the same rows through the v0.1 path: supplier_contracts shape -> contracts_register -> renewal_calendar
    sup = pd.DataFrame(
        {
            "supplier_contract_id": hand_register["contract_id"],
            "supplier": hand_register["counterparty_name"],
            "category": hand_register["category"],
            "start_date": hand_register["start_date"],
            "end_date": hand_register["end_date"],
            "auto_renewal": hand_register["auto_renewal"],
            "notice_days": hand_register["notice_days"],
            "price_protection": hand_register["price_protection"],
            "price_protection_days": hand_register["claim_window_days"],
            "payment_terms_days": hand_register["payment_terms_days"],
            "spend_under_contract": hand_register["spend_under_contract_eur"],
        }
    )
    cal1 = renewal_calendar(contracts_register(sup, pd.DataFrame(), AS_OF, 90), AS_OF, horizon_months=6)
    assert sorted(cal1["contract_id"]) == sorted(cal2["contract_id"]) == ["CTR-APL"]
    v1 = cal1.set_index("contract_id")["action_required"].astype(bool).to_dict()
    v2 = cal2.set_index("contract_id")["action_required"].astype(bool).to_dict()
    assert v1 == v2
    # silver.contracts carries the same verdict
    silver = contracts.set_index("contract_id")["action_required"].astype(bool).to_dict()
    for cid, flag in v1.items():
        assert silver[cid] == flag
    row = cal2.set_index("contract_id").loc["CTR-APL"]
    assert row["counterparty_role"] == "manufacturer" and row["category"] == "hardware"
    assert int(row["price_protection_days"]) == 30 and int(row["claim_window_days"]) == 14
    assert row["spend_actual_12m_eur"] == pytest.approx(720.0)
    assert bool(row["price_protection_window_open"]) is False  # no price change on Apple's PO lines


def test_price_protection_window_open_only_while_claim_window_runs(hand_register, hand_purchases):
    p = hand_purchases
    contracts = contracts_v2(hand_register, p["po_headers"], p["po_lines"], p["goods_receipts"], None, None, None, AS_OF)
    # PO-1 received 2026-03-20, protection 30 days -> drop on 2026-04-05 is inside; claim window 14 days -> open until 2026-04-19
    for day, expected in ((date(2026, 4, 10), True), (date(2026, 4, 19), True), (date(2026, 4, 20), False)):
        flags = price_protection_windows_open(contracts, p["po_lines"], p["price_changes"], p["goods_receipts"], day, po_headers=p["po_headers"])
        assert bool(flags["CTR-RSA"]) is expected, day
        assert bool(flags["CTR-APL"]) is False


# ---------------------------------------------------------------------------
# gold.rebate_progress
# ---------------------------------------------------------------------------


def test_rebate_progress_next_tier(hand_register, hand_purchases):
    p = hand_purchases
    contracts = contracts_v2(hand_register, p["po_headers"], p["po_lines"], p["goods_receipts"], None, None, None, AS_OF)
    reb = rebate_progress(contracts, p["po_headers"], p["po_lines"], p["goods_receipts"], AS_OF)
    assert list(reb.columns) == list(REBATE_COLUMNS)
    assert list(reb["contract_id"]) == ["CTR-RSA"]  # the only row with tiers
    row = reb.iloc[0]
    assert row["spend_12m_eur"] == pytest.approx(1400.0)
    assert row["current_tier_pct"] == pytest.approx(0.01)
    assert row["next_tier_from_eur"] == pytest.approx(5000.0)
    assert row["next_tier_pct"] == pytest.approx(0.02)
    assert row["gap_to_next_tier_eur"] == pytest.approx(3600.0)


def test_rebate_progress_top_tier_has_no_next():
    contracts = pd.DataFrame(
        [dict(contract_id="C", counterparty_name="Samsung", rebate_tiers_json=json.dumps([{"from_eur": 0, "pct": 0.0}, {"from_eur": 100, "pct": 0.02}]), spend_actual_12m_eur=150.0)]
    )
    reb = rebate_progress(contracts, None, None, None, AS_OF)
    row = reb.iloc[0]
    assert row["spend_12m_eur"] == pytest.approx(150.0) and row["current_tier_pct"] == pytest.approx(0.02)
    assert pd.isna(row["next_tier_from_eur"]) and pd.isna(row["gap_to_next_tier_eur"])


# ---------------------------------------------------------------------------
# runner on a hand-built lake database
# ---------------------------------------------------------------------------


def _write_bronze(con, table: str, frame: pd.DataFrame) -> None:
    f = frame.copy()
    f["delivery_id"] = "d0"
    f["source_file"] = "hand"
    f["row_number"] = range(1, len(f) + 1)
    f["ingested_at"] = "2026-06-30 00:00:00"
    f["row_hash"] = [f"h{i}" for i in range(len(f))]
    if "is_synthetic" not in f.columns:
        f["is_synthetic"] = True
    db.write_df(con, table, f, mode="replace")


def test_run_contracts_v2_writes_silver_and_gold(thresholds, hand_register, hand_purchases):
    p = hand_purchases
    con = db.connect(":memory:")
    db.create_schema(con)
    create_lake_schema(con)
    _write_bronze(con, "bronze.ctr_register", hand_register)
    ph = p["po_headers"].assign(promised_date="2026-03-22", currency="EUR")
    _write_bronze(con, "bronze.erp_purchase_orders", ph)
    _write_bronze(con, "bronze.erp_po_lines", p["po_lines"].assign(qty_ordered=2))
    _write_bronze(con, "bronze.erp_goods_receipts", p["goods_receipts"].assign(gr_number="GR"))
    _write_bronze(con, "bronze.cat_models", p["cat_models"].assign(model_name="x", family="Smartphone"))
    _write_bronze(con, "bronze.erp_price_changes", p["price_changes"])
    summary = run_contracts_v2(con, AS_OF, thresholds)
    assert summary.counts["silver_contracts"] == len(hand_register)
    assert summary.counts["contract_coverage_by_oem"] >= 2
    assert summary.counts["renewal_calendar_v2"] == 1
    assert summary.counts["rebate_progress"] == 1
    assert "contracts_register" in summary.counts  # v0.1 ran first
    silver = db.read_df(con, "SELECT * FROM silver.contracts")
    assert set(SILVER_CONTRACT_COLUMNS) <= set(silver.columns)
    assert silver["as_of"].nunique() == 1
    cov = db.read_df(con, "SELECT * FROM gold.contract_coverage_by_oem WHERE oem = 'Apple'")
    assert float(cov.loc[0, "spend_via_reseller"]) == pytest.approx(1400.0)
    assert any(t in db.read_df(con, "SELECT command FROM runs")["command"].tolist() for t in ("contracts_v2",))
    con.close()


def test_run_contracts_v2_on_v01_database_runs_only_v01(thresholds):
    con = db.connect(":memory:")
    db.create_schema(con)
    summary = run_contracts_v2(con, AS_OF, thresholds)
    assert "contracts_register" in summary.counts
    assert "silver_contracts" not in summary.counts
    assert any("skipped" in n for n in summary.notes)
    con.close()


def test_lake_ddl_has_the_contract_tables():
    for name in ("silver.contracts", "gold.contract_coverage_by_oem", "gold.renewal_calendar_v2", "gold.rebate_progress"):
        assert name in LAKE_DDL


# ---------------------------------------------------------------------------
# session lake database (module 6 fixture)
# ---------------------------------------------------------------------------


def test_conformed_supplier_contracts_keep_r05_and_r06_running(request):
    con = _lake_db(request)
    assert db.table_exists(con, "decision_log")
    rules = set(db.read_df(con, "SELECT DISTINCT rule_id FROM decision_log")["rule_id"])
    assert "R05" in rules, "R05 (price protection) must keep firing on the conformed supplier_contracts"
    assert "R06" in rules, "R06 (renewal) must keep firing on the conformed supplier_contracts"
    assert db.table_exists(con, "silver.contracts")
    silver = db.read_df(con, "SELECT * FROM silver.contracts")
    assert not silver.empty
    assert set(SILVER_CONTRACT_COLUMNS) <= set(silver.columns)
    assert all(cp.is_allowed_name(n) for n in silver["counterparty_name"])
    assert (silver["terms_note"] == cp.TERMS_NOTE).all()
    cov = db.read_df(con, "SELECT * FROM gold.contract_coverage_by_oem")
    assert not cov.empty and cov["spend_via_reseller"].astype(float).fillna(0).sum() > 0
