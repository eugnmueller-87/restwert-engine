"""Gold KPI tests (SPEC_v0.2 section 8.5).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- the registry is pinned at 14 KPIs on the eight cycle pages;
- on an empty database every KPI is ``not_measurable`` with ``value = None``,
  never 0;
- on a hand-built lake database every KPI is ``ok`` with absolute numbers;
- on the session lake database (module 6 fixture) every KPI is ``ok`` unless
  ``n < min_n``; the fixture missing skips that test instead of failing;
- the catalogue renders every id without an em dash.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from restwert import db
from restwert.config import KpiTargets
from restwert.gold import (
    GOLD_KPI_REGISTRY,
    GOLD_KPI_TREE,
    PAGE_OWNERS,
    PAGES,
    compute_all_gold,
    compute_one_gold,
    page_order,
    register_gold,
    render_gold_catalogue,
    run_gold_kpis,
)
from restwert.gold.run import GOLD_KPI_BREAKDOWN_COLUMNS, GOLD_KPI_VALUES_COLUMNS
from restwert.lake.schema_lake import create_lake_schema

AS_OF = date(2026, 6, 30)
EM_DASH = chr(0x2014)

EXPECTED_IDS: tuple[str, ...] = (
    "KPI_DATA_CHAIN_COMPLETE",
    "KPI_DATA_UNRESOLVED_SHARE",
    "KPI_DATA_RECONCILED",
    "KPI_PUR_DISCOUNT_VS_RRP",
    "KPI_PUR_LANDED_VS_RRP",
    "KPI_PUR_PRICE_PROTECTION_CAPTURE",
    "KPI_TCO_PER_CLOSED_DEVICE",
    "KPI_TCO_ESTIMATE_SHARE",
    "KPI_RES_ESTIMATE_VS_ANCHOR",
    "KPI_RSL_REALISED_VS_RECORD",
    "KPI_RSL_DAYS_RETURN_TO_CASH",
    "KPI_RSLT_CLOSED_PER_DEVICE",
    "KPI_LEV_ADDITIVE_EUR_PA",
    "KPI_CTR_COVERAGE_BY_OEM",
)


def _targets() -> KpiTargets:
    return KpiTargets(version=1, savings_plan_eur={2026: 1000.0}, savings_plan_owner="x", targets={"KPI_DATA_CHAIN_COMPLETE": 0.95}, targets_owner="CFO (name)")


def _lake_db(request):
    try:
        return request.getfixturevalue("lake_pipeline_db")
    except pytest.FixtureLookupError:
        pytest.skip("lake_pipeline_db fixture (module 6 conftest) not available")


@pytest.fixture
def empty_lake_db():
    con = db.connect(":memory:")
    db.create_schema(con)
    create_lake_schema(con)
    try:
        yield con
    finally:
        con.close()


def _ledger_row(serial: str, **over) -> dict:
    row = dict(
        serial=serial, as_of=AS_OF, oem="Apple", catalogue_family="Smartphone", model_family="iphone_like",
        rrp_net_eur=1000.0, received_at="2026-03-01", purchase_date="2026-03-01", supplier_role="manufacturer",
        price_protection_status="not_applicable", price_protection_credit_eur=None, price_protection_claimable_eur=None,
        purchase_price=900.0, landed_cost=910.0, tco_eur=None, holding_cost_eur=None, tco_transactional_eur=None,
        lifecycle_status="rented", is_closed=False, closed_date=None, term_months=24,
        estimate_rv_lease_end=None, anchor_rv_lease_end=None, resale_channel=None, sale_date=None, credited_at=None,
        resale_gross=None, estimate_rv_of_record=None, days_return_to_cash=None, lifecycle_result_eur=None,
        n_lines=1, n_estimate_lines=0, is_synthetic=True,
    )
    row.update(over)
    return row


@pytest.fixture
def hand_lake_db(empty_lake_db):
    """A small lake with exact numbers behind every KPI."""

    con = empty_lake_db
    ledger = pd.DataFrame(
        [
            # rented, with estimate and anchor
            _ledger_row("R1", estimate_rv_lease_end=400.0, anchor_rv_lease_end=500.0),
            _ledger_row("R2", oem="Samsung", supplier_role="reseller", purchase_price=800.0, landed_cost=820.0, estimate_rv_lease_end=300.0, anchor_rv_lease_end=400.0,
                        price_protection_status="claimed", price_protection_credit_eur=30.0),
            # closed sold in the window
            _ledger_row("C1", received_at="2024-03-01", purchase_date="2024-03-01", lifecycle_status="sold", is_closed=True, closed_date="2026-05-10",
                        tco_eur=1200.0, holding_cost_eur=60.0, tco_transactional_eur=1140.0, resale_channel="marketplace", sale_date="2026-05-01", credited_at="2026-05-10",
                        resale_gross=450.0, estimate_rv_of_record=500.0, days_return_to_cash=40, lifecycle_result_eur=120.0,
                        price_protection_status="missed", price_protection_claimable_eur=70.0),
            _ledger_row("C2", oem="Samsung", catalogue_family="Tablet", received_at="2024-04-01", purchase_date="2024-04-01", lifecycle_status="sold", is_closed=True,
                        closed_date="2026-04-20", tco_eur=800.0, holding_cost_eur=40.0, tco_transactional_eur=760.0, resale_channel="b2b_wholesale", sale_date="2026-04-10",
                        credited_at="2026-04-20", resale_gross=250.0, estimate_rv_of_record=250.0, days_return_to_cash=20, lifecycle_result_eur=-40.0, term_months=36),
            # as-is sale: excluded from realised vs record; closed outside the window: excluded from TCO and result
            _ledger_row("C3", received_at="2024-01-01", purchase_date="2024-01-01", lifecycle_status="sold", is_closed=True, closed_date="2025-01-15",
                        tco_eur=999.0, holding_cost_eur=1.0, tco_transactional_eur=998.0, resale_channel="as_is", sale_date="2025-01-10", credited_at="2025-01-15",
                        resale_gross=50.0, estimate_rv_of_record=100.0, days_return_to_cash=5, lifecycle_result_eur=-300.0),
        ]
    )
    db.write_df(con, "silver.device_ledger", ledger, mode="replace")
    timeline = pd.DataFrame(
        [
            dict(serial="R1", as_of=AS_OF, lifecycle_status="rented", steps_expected=4, steps_present=4, is_monotonic=True, chain_complete=True, source_refs_json="{}", is_synthetic=True),
            dict(serial="R2", as_of=AS_OF, lifecycle_status="rented", steps_expected=4, steps_present=3, first_missing_step="staged_at", is_monotonic=True, chain_complete=False, source_refs_json="{}", is_synthetic=True),
            dict(serial="C1", as_of=AS_OF, lifecycle_status="sold", steps_expected=10, steps_present=10, is_monotonic=True, chain_complete=True, source_refs_json="{}", is_synthetic=True),
            dict(serial="C2", as_of=AS_OF, lifecycle_status="sold", steps_expected=10, steps_present=10, is_monotonic=True, chain_complete=True, source_refs_json="{}", is_synthetic=True),
        ]
    )
    db.write_df(con, "silver.serial_timeline", timeline, mode="replace")
    recon = pd.DataFrame(
        [
            dict(serial="C1", as_of=AS_OF, field="landed_cost", ok=True),
            dict(serial="C1", as_of=AS_OF, field="rental_revenue", ok=True),
            dict(serial="C2", as_of=AS_OF, field="landed_cost", ok=True),
            dict(serial="C2", as_of=AS_OF, field="rental_revenue", ok=False),
        ]
    )
    db.write_df(con, "silver.reconciliation", recon, mode="replace")
    deliveries = pd.DataFrame(
        [
            dict(delivery_id="d1", source_system="erp", feed="erp/po_lines", source_file="a.csv", sha256="s1", delivered_on="2026-03-31", ingested_at="2026-06-30 00:00:00",
                 rows_read=90, rows_typed=90, rows_new=88, duplicates_identical=1, duplicates_conflict=0, n_unresolved=1, reasons_json="{}", is_synthetic=True),
            dict(delivery_id="d2", source_system="wms", feed="wms/shipments", source_file="b.csv", sha256="s2", delivered_on="2026-03-31", ingested_at="2026-06-30 00:00:00",
                 rows_read=10, rows_typed=10, rows_new=8, duplicates_identical=0, duplicates_conflict=0, n_unresolved=2, reasons_json="{}", is_synthetic=True),
        ]
    )
    db.write_df(con, "bronze.deliveries", deliveries, mode="replace")
    unresolved = pd.DataFrame(
        [
            dict(unresolved_id="u1", delivery_id="d1", source_system="erp", feed="erp/po_lines", source_file="a.csv", row_number=3, key_json="{}", reason_code="unknown_po", reason_text="x", row_json="{}", ingested_at="2026-06-30 00:00:00", is_synthetic=True),
            dict(unresolved_id="u2", delivery_id="d2", source_system="wms", feed="wms/shipments", source_file="b.csv", row_number=4, key_json="{}", reason_code="unknown_serial", reason_text="x", row_json="{}", ingested_at="2026-06-30 00:00:00", is_synthetic=True),
            dict(unresolved_id="u3", delivery_id="d2", source_system="wms", feed="wms/shipments", source_file="b.csv", row_number=5, key_json="{}", reason_code="unknown_serial", reason_text="x", row_json="{}", ingested_at="2026-06-30 00:00:00", is_synthetic=True),
        ]
    )
    db.write_df(con, "bronze.unresolved", unresolved, mode="replace")
    levers = pd.DataFrame(
        [
            dict(lever_id="L01", lever_name="purchase_discount", component="purchase_price", basis="fleet", additive=True, n_eligible=10, n_attributed=8, eur_per_device=12.0, eur_per_device_p90=30.0,
                 eur_fleet_per_year=1200.0, share_of_lever_basis=0.1, lever_basis_eur=12000.0, lever_basis="landed cost of the same purchases in the window", threshold_key="purchase_discount_floor_pct", threshold_value="Apple: 0.06", threshold_unit="ratio", threshold_owner="Head of Procurement (name)", rule_id="R07", reference_key="lever_reference_min_n", reference_owner="CFO (name)", reference_sentence="p75", rank=1, as_of=AS_OF),
            dict(lever_id="L02", lever_name="price_protection", component="price_protection_credit", basis="fleet", additive=True, n_eligible=10, n_attributed=8, eur_per_device=3.0, eur_per_device_p90=9.0,
                 eur_fleet_per_year=300.0, share_of_lever_basis=0.02, lever_basis_eur=15000.0, lever_basis="landed cost of the same purchases in the window", threshold_key="price_protection_min_claim_eur", threshold_value="500", threshold_unit="EUR", threshold_owner="Category Manager Hardware (name)", rule_id="R05", reference_key="n/a", reference_owner="the serial's own data", reference_sentence="missed", rank=2, as_of=AS_OF),
            dict(lever_id="L03", lever_name="channel_choice", component="resale_gross + channel_fee", basis="forecast_of_record", additive=False, n_eligible=10, n_attributed=8, eur_per_device=20.0, eur_per_device_p90=50.0,
                 eur_fleet_per_year=5000.0, share_of_lever_basis=0.3, lever_basis_eur=16666.67, lever_basis="absolute closed result of the same serials in the window", threshold_key="channel_min_net_uplift_eur", threshold_value="15", threshold_unit="EUR", threshold_owner="Head of Recommerce (name)", rule_id="R02", reference_key="channel_fees", reference_owner="Head of Recommerce (name)", reference_sentence="best channel", rank=3, as_of=AS_OF),
        ]
    )
    db.write_df(con, "gold.levers_summary", levers, mode="replace")
    coverage = pd.DataFrame(
        [
            dict(oem="Apple", n_units=4, spend_total=2840.0, spend_under_contract=2120.0, coverage_pct=2120 / 2840, spend_direct=1440.0, spend_via_reseller=1400.0, n_contracts_in_force=2, as_of=AS_OF),
            dict(oem="Dell", n_units=1, spend_total=900.0, spend_under_contract=0.0, coverage_pct=0.0, spend_direct=900.0, spend_via_reseller=0.0, n_contracts_in_force=0, as_of=AS_OF),
            dict(oem="HP", n_units=0, spend_total=None, spend_under_contract=None, coverage_pct=None, spend_direct=None, spend_via_reseller=None, n_contracts_in_force=1, as_of=AS_OF),
        ]
    )
    db.write_df(con, "gold.contract_coverage_by_oem", coverage, mode="replace")
    return con


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_gold_registry_has_14_kpis_and_pages():
    assert len(GOLD_KPI_REGISTRY) == 14
    assert set(GOLD_KPI_REGISTRY) == set(EXPECTED_IDS)
    assert PAGES == ("0 Data", "1 Purchase", "2 TCO", "3 Residual estimate", "4 Resale", "5 Result", "6 Levers", "7 Contracts")
    assert [page for page, _ in GOLD_KPI_TREE] == list(PAGES)
    for page, ids in GOLD_KPI_TREE:
        assert ids, f"page {page} has no KPI"
        for kpi_id in ids:
            assert GOLD_KPI_REGISTRY[kpi_id].page == page
    assert page_order() == list(EXPECTED_IDS)
    for spec in GOLD_KPI_REGISTRY.values():
        assert spec.owner and spec.owner == PAGE_OWNERS[spec.page]
        assert spec.min_n >= 1 and spec.source_tables
        assert spec.direction in ("up", "down", "zero", "one")
        assert EM_DASH not in spec.definition and EM_DASH not in spec.formula_text and EM_DASH not in spec.name
    # the v0.1 registry is untouched by the gold registrations
    from restwert.kpi import KPI_REGISTRY

    assert len(KPI_REGISTRY) == 20
    assert not set(KPI_REGISTRY) & set(GOLD_KPI_REGISTRY)


def test_register_gold_rejects_duplicates_and_bad_page():
    with pytest.raises(ValueError):
        register_gold(kpi_id="KPI_DATA_CHAIN_COMPLETE", name="dup", page="0 Data", definition="d", formula_text="f", source_tables=("x",), unit="ratio", direction="up")
    with pytest.raises(ValueError):
        register_gold(kpi_id="KPI_X_NEW", name="n", page="9 Nowhere", definition="d", formula_text="f", source_tables=("x",), unit="ratio", direction="up")
    with pytest.raises(ValueError):
        register_gold(kpi_id="KPI_X_NEW", name="n", page="0 Data", definition="d", formula_text="f", source_tables=(), unit="ratio", direction="up")
    with pytest.raises(ValueError):
        register_gold(kpi_id="KPI_X_NEW", name="n", page="0 Data", definition="d", formula_text="f", source_tables=("x",), unit="ratio", direction="sideways")
    assert "KPI_X_NEW" not in GOLD_KPI_REGISTRY
    assert len(GOLD_KPI_REGISTRY) == 14


# ---------------------------------------------------------------------------
# empty database: not_measurable, never zero
# ---------------------------------------------------------------------------


def test_gold_kpis_not_measurable_never_zero(empty_lake_db):
    con = empty_lake_db
    for kpi_id in EXPECTED_IDS:
        kv = compute_one_gold(kpi_id, con, AS_OF, _targets())
        assert kv.status == "not_measurable", kpi_id
        assert kv.value is None, kpi_id
        assert kv.note, kpi_id
    summary = run_gold_kpis(con, AS_OF, _targets(), write_catalogue_md=False)
    assert summary.counts == {"gold_kpi_values": 14, "gold_kpi_breakdown": 0, "gold_kpis_ok": 0, "gold_kpis_not_measurable": 14}
    values = db.read_df(con, "SELECT * FROM gold.kpi_values")
    assert len(values) == 14
    assert values["value"].isna().all()
    assert (values["status"] == "not_measurable").all()
    assert values["owner"].notna().all() and (values["owner"] != "").all()
    assert values["note"].notna().all()


# ---------------------------------------------------------------------------
# hand database: every KPI ok with absolute numbers
# ---------------------------------------------------------------------------


def test_gold_kpis_on_hand_db_all_ok(hand_lake_db):
    con = hand_lake_db
    values, breakdown = compute_all_gold(con, AS_OF, _targets(), run_id="t")
    assert list(values.columns) == list(GOLD_KPI_VALUES_COLUMNS)
    assert list(breakdown.columns) == list(GOLD_KPI_BREAKDOWN_COLUMNS)
    assert list(values["kpi_id"]) == list(EXPECTED_IDS)
    bad = values[values["status"] != "ok"]
    assert bad.empty, bad[["kpi_id", "note"]].to_string()
    v = values.set_index("kpi_id")["value"]
    assert v["KPI_DATA_CHAIN_COMPLETE"] == pytest.approx(3 / 4)
    assert v["KPI_DATA_UNRESOLVED_SHARE"] == pytest.approx(3 / 100)
    assert v["KPI_DATA_RECONCILED"] == pytest.approx(1 / 2)
    # received in window: R1 (900/1000), R2 (800/1000 with a 30.00 price protection credit received, a price reduction)
    # -> 1 - (900 + 770)/2000; landed (910 + 820 - 30)/2000
    assert v["KPI_PUR_DISCOUNT_VS_RRP"] == pytest.approx(1 - 1670 / 2000)
    assert v["KPI_PUR_LANDED_VS_RRP"] == pytest.approx(1700 / 2000)
    # price protection: window by received_at holds R1, R2 (claimed 30) only; C1 (missed 70) was received 2024
    assert v["KPI_PUR_PRICE_PROTECTION_CAPTURE"] == pytest.approx(1.0)
    assert v["KPI_TCO_PER_CLOSED_DEVICE"] == pytest.approx(1000.0)  # (1200 + 800) / 2, C3 outside the window
    assert v["KPI_TCO_ESTIMATE_SHARE"] == pytest.approx(100 / 2000)
    assert v["KPI_RES_ESTIMATE_VS_ANCHOR"] == pytest.approx(700 / 900)
    assert v["KPI_RSL_REALISED_VS_RECORD"] == pytest.approx(700 / 750)  # as-is C3 excluded
    assert v["KPI_RSL_DAYS_RETURN_TO_CASH"] == pytest.approx(30.0)  # median of 40 and 20
    assert v["KPI_RSLT_CLOSED_PER_DEVICE"] == pytest.approx(40.0)  # (120 - 40) / 2
    assert v["KPI_LEV_ADDITIVE_EUR_PA"] == pytest.approx(1500.0)  # L01 + L02, L03 is not additive
    assert v["KPI_CTR_COVERAGE_BY_OEM"] == pytest.approx(2120 / 3740)
    # targets and owners travel with the values
    t = values.set_index("kpi_id")["target"]
    assert t["KPI_DATA_CHAIN_COMPLETE"] == pytest.approx(0.95)
    assert values["owner"].notna().all()
    # breakdowns: dimensions as specified
    dims = breakdown.groupby("kpi_id")["dimension"].agg(lambda s: set(s)).to_dict()
    assert dims["KPI_DATA_CHAIN_COMPLETE"] == {"lifecycle_status", "first_missing_step"}
    assert dims["KPI_DATA_UNRESOLVED_SHARE"] == {"feed", "reason_code"}
    assert dims["KPI_DATA_RECONCILED"] == {"field"}
    assert dims["KPI_PUR_DISCOUNT_VS_RRP"] == {"oem", "supplier_role"}
    assert dims["KPI_PUR_LANDED_VS_RRP"] == {"oem"}
    assert dims["KPI_PUR_PRICE_PROTECTION_CAPTURE"] == {"oem"}
    assert dims["KPI_TCO_PER_CLOSED_DEVICE"] == {"catalogue_family", "oem"}
    assert dims["KPI_TCO_ESTIMATE_SHARE"] == {"catalogue_family"}
    assert dims["KPI_RES_ESTIMATE_VS_ANCHOR"] == {"catalogue_family", "oem"}
    assert dims["KPI_RSL_REALISED_VS_RECORD"] == {"resale_channel", "oem"}
    assert dims["KPI_RSL_DAYS_RETURN_TO_CASH"] == {"resale_channel"}
    assert dims["KPI_RSLT_CLOSED_PER_DEVICE"] == {"oem", "catalogue_family", "term_months"}
    assert dims["KPI_LEV_ADDITIVE_EUR_PA"] == {"lever_id"}
    assert dims["KPI_CTR_COVERAGE_BY_OEM"] == {"oem"}
    oem_disc = breakdown[(breakdown["kpi_id"] == "KPI_PUR_DISCOUNT_VS_RRP") & (breakdown["dimension"] == "oem")].set_index("dimension_value")["value"]
    assert oem_disc["Apple"] == pytest.approx(0.10) and oem_disc["Samsung"] == pytest.approx(0.23)  # 1 - 770 / 1000, credit netted
    lever_bd = breakdown[breakdown["kpi_id"] == "KPI_LEV_ADDITIVE_EUR_PA"]
    assert any("not additive" in x for x in lever_bd["dimension_value"])


def test_run_gold_kpis_replaces_rows_of_the_as_of(hand_lake_db):
    con = hand_lake_db
    first = run_gold_kpis(con, AS_OF, _targets(), write_catalogue_md=False)
    assert first.counts["gold_kpis_ok"] == 14
    second = run_gold_kpis(con, AS_OF, _targets(), write_catalogue_md=False)
    assert second.counts["gold_kpi_values"] == 14
    assert db.read_df(con, "SELECT count(*) AS n FROM gold.kpi_values")["n"].iloc[0] == 14
    other = run_gold_kpis(con, date(2026, 7, 31), _targets(), write_catalogue_md=False)
    assert other.counts["gold_kpi_values"] == 14
    assert db.read_df(con, "SELECT count(*) AS n FROM gold.kpi_values")["n"].iloc[0] == 28
    pages = db.read_df(con, "SELECT DISTINCT page FROM gold.kpi_values WHERE as_of = ?", [AS_OF])["page"]
    assert set(pages) == set(PAGES)
    runs = db.read_df(con, "SELECT command FROM runs")["command"].tolist()
    assert runs.count("gold-kpis") == 3


def test_min_n_rule_flags_but_keeps_value(hand_lake_db):
    con = hand_lake_db
    spec = GOLD_KPI_REGISTRY["KPI_RSLT_CLOSED_PER_DEVICE"]
    kv = spec.fn(con, AS_OF, _targets())
    assert kv.status == "ok" and kv.n == 2
    strict = spec.__class__(**{**spec.__dict__, "min_n": 5})
    GOLD_KPI_REGISTRY["KPI_RSLT_CLOSED_PER_DEVICE"] = strict
    try:
        kv2 = compute_one_gold("KPI_RSLT_CLOSED_PER_DEVICE", con, AS_OF, _targets())
        assert kv2.status == "not_measurable" and kv2.value == pytest.approx(40.0)
        assert "below min_n" in kv2.note
    finally:
        GOLD_KPI_REGISTRY["KPI_RSLT_CLOSED_PER_DEVICE"] = spec


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------


def test_gold_catalogue_renders_every_id_no_em_dash():
    md = render_gold_catalogue(targets=_targets())
    for kpi_id in EXPECTED_IDS:
        assert f"`{kpi_id}`" in md
    for page in PAGES:
        assert f"## {page}" in md
    for owner in set(PAGE_OWNERS.values()):
        assert owner in md
    assert "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD." in md
    assert "target:** 0.95" in md
    assert EM_DASH not in md
    assert not md.startswith("﻿")


# ---------------------------------------------------------------------------
# session lake database (module 6 fixture)
# ---------------------------------------------------------------------------


def test_gold_kpis_on_pipeline_all_ok(request, targets):
    con = _lake_db(request)
    as_of_row = db.read_df(con, "SELECT max(as_of) AS as_of FROM silver.device_ledger")
    as_of = pd.Timestamp(as_of_row["as_of"].iloc[0]).date()
    values, breakdown = compute_all_gold(con, as_of, targets)
    broken = ("error while computing", "does not exist", "lacks column", "entirely NULL", "not KpiValue")
    for _, row in values.iterrows():
        if row["status"] == "ok":
            assert row["value"] == row["value"], row["kpi_id"]
            continue
        note = str(row["note"])
        # a KPI may be not measurable on the small fleet only for a stated data reason: too few
        # observations, or an empty population in the window (the note says which); never for a
        # missing table or column and never for an exception inside the KPI
        assert not any(b in note for b in broken), f"{row['kpi_id']} broken on the pipeline: {note}"
        assert "below min_n" in note or note.startswith("no "), f"{row['kpi_id']} not measurable on the pipeline: {note}"
    assert int((values["status"] == "ok").sum()) >= 12
    stored = db.read_df(con, "SELECT * FROM gold.kpi_values")
    assert len(stored) >= 14
    assert set(stored["kpi_id"]) >= set(EXPECTED_IDS)
    assert stored["owner"].notna().all()
