"""Dashboard v0.2 tests (SPEC_v0.2 9.5): navigation, every Cycle page headless, no totals on Result.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The pages render against the session lake database when the v0.2 packages are
present. Before they land, a ``mini lake`` database is built here: the v0.1
``all --small`` database copied and extended with the silver and gold tables of
the frozen DDL (spec 3.8), filled with rows derived from ``device_pnl``. Either
way every Cycle page must render with no error, at most four metrics on the
page body and exactly one subheader question, and the Result page must never
label anything "total result".

``AppTest.switch_page`` only resolves file-based pages, so ``_switch`` selects a
callable page through the registered page hash (title -> hash).
"""

from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from restwert import GOVERNANCE_PRINCIPLE
from tests.conftest import lake_packages_available

pytest.importorskip("streamlit")

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "restwert" / "dashboard" / "app.py"

# frozen DDL subset (SPEC_v0.2 3.8) used only when the lake package has not landed
_MINI_DDL: dict[str, str] = {
    "bronze.deliveries": "delivery_id VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL, source_file VARCHAR NOT NULL, sha256 VARCHAR NOT NULL UNIQUE, delivered_on DATE NOT NULL, ingested_at TIMESTAMP NOT NULL, rows_read INTEGER NOT NULL, rows_typed INTEGER NOT NULL, rows_new INTEGER NOT NULL, duplicates_identical INTEGER NOT NULL, duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL, reasons_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL",
    "bronze.unresolved": "unresolved_id VARCHAR PRIMARY KEY, delivery_id VARCHAR NOT NULL, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL, source_file VARCHAR NOT NULL, row_number INTEGER NOT NULL, key_json VARCHAR NOT NULL, reason_code VARCHAR NOT NULL, reason_text VARCHAR NOT NULL, row_json VARCHAR NOT NULL, ingested_at TIMESTAMP NOT NULL, is_synthetic BOOLEAN NOT NULL",
    "silver.ledger_lines": "line_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, line_type VARCHAR NOT NULL, line_class VARCHAR NOT NULL, amount_eur DECIMAL(12,2) NOT NULL, event_date DATE NOT NULL, period_month DATE NOT NULL, source_system VARCHAR NOT NULL, source_table VARCHAR NOT NULL, source_ref VARCHAR NOT NULL, delivery_id VARCHAR, allocation_basis VARCHAR NOT NULL, is_estimate BOOLEAN NOT NULL, assumption_key VARCHAR, assumption_owner VARCHAR, counterparty VARCHAR, counterparty_role VARCHAR, contract_ref VARCHAR, as_of DATE NOT NULL, is_synthetic BOOLEAN NOT NULL",
    "silver.serial_timeline": "serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, ordered_at DATE, received_at DATE, staged_at DATE, shipped_at DATE, returned_at DATE, wiped_at DATE, graded_at DATE, sellable_at DATE, sold_at DATE, credited_at DATE, lifecycle_status VARCHAR NOT NULL, steps_expected INTEGER NOT NULL, steps_present INTEGER NOT NULL, missing_steps VARCHAR, first_missing_step VARCHAR, is_monotonic BOOLEAN NOT NULL, non_monotonic_pair VARCHAR, chain_complete BOOLEAN NOT NULL, days_order_to_receipt INTEGER, days_receipt_to_ship INTEGER, days_return_to_sellable INTEGER, days_sellable_to_sold INTEGER, days_sold_to_credited INTEGER, days_return_to_cash INTEGER, source_refs_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL",
    "silver.device_ledger": "serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, slug VARCHAR, model_name VARCHAR, oem VARCHAR, catalogue_family VARCHAR, model_family VARCHAR, series VARCHAR, variant_spec VARCHAR, storage_gb INTEGER, rrp_gross_eur DECIMAL(12,2), rrp_net_eur DECIMAL(12,2), launch_date DATE, months_since_launch_at_as_of DOUBLE, po_number VARCHAR, po_line INTEGER, supplier_id VARCHAR, supplier_name VARCHAR, supplier_role VARCHAR, contract_ref VARCHAR, order_date DATE, received_at DATE, purchase_date DATE, cohort_month DATE, cohort_quarter VARCHAR, price_protection_days INTEGER, price_protection_status VARCHAR, price_protection_claimable_eur DECIMAL(12,2), purchase_price DECIMAL(12,2), discount_vs_rrp_eur DECIMAL(12,2), discount_vs_rrp_pct DOUBLE, freight_eur DECIMAL(12,2), duty_eur DECIMAL(12,2), landed_cost DECIMAL(12,2), landed_vs_rrp_pct DOUBLE, price_protection_credit_eur DECIMAL(12,2), staging_eur DECIMAL(12,2), outbound_shipping_eur DECIMAL(12,2), repair_eur DECIMAL(12,2), replacement_logistics_eur DECIMAL(12,2), return_logistics_eur DECIMAL(12,2), wipe_grading_eur DECIMAL(12,2), refurb_eur DECIMAL(12,2), holding_cost_eur DECIMAL(12,2), channel_fee_eur DECIMAL(12,2), days_in_stock_to_date INTEGER, tco_excl_landed_eur DECIMAL(12,2), tco_transactional_eur DECIMAL(12,2), tco_eur DECIMAL(12,2), n_lines INTEGER NOT NULL, n_estimate_lines INTEGER NOT NULL, first_contract_id VARCHAR, customer_id VARCHAR, term_months INTEGER, monthly_rate DECIMAL(12,2), contract_start DATE, contract_end_planned DATE, contract_end_effective DATE, months_billed INTEGER, months_remaining INTEGER, rental_revenue DECIMAL(12,2), remaining_contracted_rent DECIMAL(12,2), return_date DATE, grade_declared VARCHAR, grade_inspected VARCHAR, wipe_certificate_id VARCHAR, grade_out VARCHAR, refurb_outcome VARCHAR, sellable_date DATE, resale_channel VARCHAR, listed_at DATE, sale_date DATE, credited_at DATE, resale_gross DECIMAL(12,2), resale_net DECIMAL(12,2), days_return_to_sale INTEGER, days_return_to_cash INTEGER, estimate_run_id VARCHAR, grade_used VARCHAR, grade_source VARCHAR, estimate_rv_today DECIMAL(12,2), estimate_rv_lease_end DECIMAL(12,2), estimate_months_at_lease_end INTEGER, estimate_rv_source VARCHAR, estimate_fit_quality VARCHAR, estimate_rv_of_record DECIMAL(12,2), anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, anchor_rv_lease_end DECIMAL(12,2), estimate_vs_anchor_ratio DOUBLE, realised_vs_record_ratio DOUBLE, realised_rv DECIMAL(12,2), lifecycle_result_eur DECIMAL(12,2), result_v01_basis_eur DECIMAL(12,2), result_pct_of_landed DOUBLE, result_if_liquidated_today DECIMAL(12,2), result_projected_at_lease_end DECIMAL(12,2), projected_label VARCHAR, expected_remaining_cost DECIMAL(12,2), expected_cost_inputs_source VARCHAR, lifecycle_status VARCHAR NOT NULL, is_closed BOOLEAN NOT NULL, closed_date DATE, chain_complete BOOLEAN, is_synthetic BOOLEAN NOT NULL",
    "silver.reconciliation": "serial VARCHAR NOT NULL, as_of DATE NOT NULL, field VARCHAR NOT NULL, device_pnl_value DECIMAL(12,2), ledger_value DECIMAL(12,2), diff DECIMAL(12,2), ok BOOLEAN NOT NULL, PRIMARY KEY (serial, field)",
    "silver.contracts": "contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, counterparty_is_public BOOLEAN NOT NULL, category VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, notice_days INTEGER NOT NULL, notice_deadline DATE NOT NULL, auto_renewal BOOLEAN NOT NULL, price_protection BOOLEAN NOT NULL, price_protection_days INTEGER, claim_window_days INTEGER, warranty_months INTEGER, rebate_tiers_json VARCHAR, volume_commitment_units INTEGER, payment_terms_days INTEGER NOT NULL, sla_json VARCHAR, spend_under_contract_eur DECIMAL(14,2) NOT NULL, spend_actual_12m_eur DECIMAL(14,2), spend_actual_vs_planned_pct DOUBLE, covers_oems VARCHAR, n_serials_under_contract INTEGER, status VARCHAR NOT NULL, days_to_notice_deadline INTEGER, days_to_end INTEGER, action_required BOOLEAN NOT NULL, terms_note VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL, as_of DATE NOT NULL",
    "gold.ingest_summary": "feed VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, delivering_system VARCHAR NOT NULL, n_files INTEGER NOT NULL, last_delivered_on DATE, rows_read INTEGER NOT NULL, rows_new INTEGER NOT NULL, duplicates_identical INTEGER NOT NULL, duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL, bronze_rows INTEGER NOT NULL, as_of DATE NOT NULL",
    "gold.chain_quality": "lifecycle_status VARCHAR NOT NULL, step VARCHAR NOT NULL, n_serials INTEGER NOT NULL, n_present INTEGER NOT NULL, share_present DOUBLE, is_expected BOOLEAN NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (lifecycle_status, step)",
    "gold.purchase_by_oem_month": "oem VARCHAR NOT NULL, purchase_month DATE NOT NULL, supplier_role VARCHAR NOT NULL, n_units INTEGER NOT NULL, sum_rrp_net DECIMAL(14,2), sum_unit_price DECIMAL(14,2), sum_freight_duty DECIMAL(14,2), sum_landed DECIMAL(14,2), discount_vs_rrp_pct DOUBLE, landed_vs_rrp_pct DOUBLE, ppv_vs_po_eur DECIMAL(14,2), share_under_contract DOUBLE, pp_claimable_eur DECIMAL(14,2), pp_credited_eur DECIMAL(14,2), as_of DATE NOT NULL, PRIMARY KEY (oem, purchase_month, supplier_role)",
    "gold.tco_by_cohort": "cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, line_type VARCHAR NOT NULL, n_devices INTEGER NOT NULL, mean_eur DECIMAL(12,2), sum_eur DECIMAL(14,2), estimate_eur DECIMAL(14,2), is_estimate BOOLEAN NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value, line_type)",
    "gold.estimate_vs_anchor": "catalogue_family VARCHAR NOT NULL, oem VARCHAR NOT NULL, n_rented INTEGER NOT NULL, n_with_anchor INTEGER NOT NULL, sum_estimate_lease_end DECIMAL(14,2), sum_anchor_lease_end DECIMAL(14,2), mean_estimate_ratio DOUBLE, mean_anchor_ratio DOUBLE, estimate_vs_anchor_ratio DOUBLE, anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, as_of DATE NOT NULL, PRIMARY KEY (catalogue_family, oem)",
    "gold.resale_by_channel_grade": "channel VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL, n INTEGER NOT NULL, sum_gross DECIMAL(14,2), sum_fees DECIMAL(14,2), sum_net DECIMAL(14,2), sum_refurb DECIMAL(14,2), sum_estimate_of_record DECIMAL(14,2), realised_vs_record_ratio DOUBLE, median_days_return_to_cash DOUBLE, n_credit_note_missing INTEGER NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (channel, grade_at_sale)",
    "gold.result_by_cohort": "cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, n INTEGER NOT NULL, n_closed INTEGER NOT NULL, n_open INTEGER NOT NULL, sum_result_closed DECIMAL(14,2), mean_result_closed DECIMAL(12,2), result_pct_of_landed_closed DOUBLE, sum_rental_revenue_closed DECIMAL(14,2), sum_realised_rv_closed DECIMAL(14,2), sum_pp_credit_closed DECIMAL(14,2), sum_tco_closed DECIMAL(14,2), sum_landed_closed DECIMAL(14,2), tco_per_closed_device DECIMAL(12,2), rv_per_closed_device DECIMAL(12,2), rent_per_closed_device DECIMAL(12,2), sum_liquidation_today_open DECIMAL(14,2), mean_liquidation_today_open DECIMAL(12,2), sum_projected_lease_end_open DECIMAL(14,2), mean_projected_lease_end_open DECIMAL(12,2), as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value)",
    "gold.levers_per_device": "serial VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, delta_eur DECIMAL(12,2), actual_value DOUBLE, reference_value DOUBLE, reference_source VARCHAR NOT NULL, n_reference INTEGER NOT NULL, is_attributed BOOLEAN NOT NULL, additive BOOLEAN NOT NULL, basis VARCHAR NOT NULL, event_date DATE, counterfactual_json VARCHAR NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (serial, lever_id)",
    "gold.levers_by_cohort": "cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, n_attributed INTEGER NOT NULL, sum_delta_eur DECIMAL(14,2), mean_delta_eur DECIMAL(12,2), as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value, lever_id)",
    "gold.levers_summary": "lever_id VARCHAR PRIMARY KEY, lever_name VARCHAR NOT NULL, component VARCHAR NOT NULL, basis VARCHAR NOT NULL, additive BOOLEAN NOT NULL, n_eligible INTEGER NOT NULL, n_attributed INTEGER NOT NULL, eur_per_device DECIMAL(12,2), eur_per_device_p90 DECIMAL(12,2), eur_fleet_per_year DECIMAL(14,2), share_of_lever_basis DOUBLE, lever_basis_eur DECIMAL(14,2), lever_basis VARCHAR NOT NULL, threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL, threshold_unit VARCHAR NOT NULL, threshold_owner VARCHAR NOT NULL, rule_id VARCHAR NOT NULL, reference_key VARCHAR NOT NULL, reference_owner VARCHAR NOT NULL, reference_sentence VARCHAR NOT NULL, rank INTEGER NOT NULL, as_of DATE NOT NULL",
    "gold.contract_coverage_by_oem": "oem VARCHAR PRIMARY KEY, n_units INTEGER NOT NULL, spend_total DECIMAL(14,2), spend_under_contract DECIMAL(14,2), coverage_pct DOUBLE, spend_direct DECIMAL(14,2), spend_via_reseller DECIMAL(14,2), n_contracts_in_force INTEGER NOT NULL, next_notice_deadline DATE, as_of DATE NOT NULL",
    "gold.renewal_calendar_v2": "contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, category VARCHAR NOT NULL, end_date DATE, notice_days INTEGER, notice_deadline DATE, days_to_notice_deadline INTEGER, days_to_end INTEGER, auto_renewal BOOLEAN, spend_under_contract_eur DECIMAL(14,2), spend_actual_12m_eur DECIMAL(14,2), price_protection_days INTEGER, claim_window_days INTEGER, price_protection_window_open BOOLEAN NOT NULL, action_required BOOLEAN NOT NULL, month_bucket VARCHAR, as_of DATE NOT NULL",
    "gold.rebate_progress": "contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, spend_12m_eur DECIMAL(14,2), current_tier_pct DOUBLE, next_tier_from_eur DECIMAL(14,2), next_tier_pct DOUBLE, gap_to_next_tier_eur DECIMAL(14,2), as_of DATE NOT NULL",
    "gold.kpi_values": "kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, name VARCHAR NOT NULL, page VARCHAR NOT NULL, value DOUBLE, numerator DOUBLE, denominator DOUBLE, n INTEGER NOT NULL, unit VARCHAR NOT NULL, status VARCHAR NOT NULL, note VARCHAR, target DOUBLE, direction VARCHAR, formula_text VARCHAR, source_tables VARCHAR, owner VARCHAR, run_id VARCHAR, PRIMARY KEY (kpi_id, as_of)",
    "gold.kpi_breakdown": "kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, dimension VARCHAR NOT NULL, dimension_value VARCHAR NOT NULL, value DOUBLE, numerator DOUBLE, denominator DOUBLE, n INTEGER, PRIMARY KEY (kpi_id, as_of, dimension, dimension_value)",
}
_GOLD_KPIS = ("KPI_DATA_CHAIN_COMPLETE", "KPI_DATA_UNRESOLVED_SHARE", "KPI_DATA_RECONCILED", "KPI_PUR_DISCOUNT_VS_RRP", "KPI_PUR_LANDED_VS_RRP",
              "KPI_PUR_PRICE_PROTECTION_CAPTURE", "KPI_TCO_PER_CLOSED_DEVICE", "KPI_TCO_ESTIMATE_SHARE", "KPI_RES_ESTIMATE_VS_ANCHOR",
              "KPI_RSL_REALISED_VS_RECORD", "KPI_RSL_DAYS_RETURN_TO_CASH", "KPI_RSLT_CLOSED_PER_DEVICE", "KPI_LEV_ADDITIVE_EUR_PA",
              "KPI_CTR_COVERAGE_BY_OEM")
_LEVERS = (("L01", "purchase_discount", "purchase_price", "fleet", True, "purchase_discount_floor_pct", "Head of Procurement (name)", "R07"),
           ("L02", "price_protection", "price_protection_credit", "fleet", True, "price_protection_min_claim_eur", "Category Manager Hardware (name)", "R05"),
           ("L03", "channel_choice", "resale_gross + channel_fee", "forecast_of_record", False, "channel_min_net_uplift_eur", "Head of Recommerce (name)", "R02"),
           ("L04", "grade_and_repair", "resale_gross, repair, refurbishment", "grid", False, "repair_max_share_of_rv", "Head of Service Operations (name)", "R01"),
           ("L05", "aging", "holding_cost, resale_gross", "grid", False, "aging_days_90", "CFO (name)", "R03"),
           ("L06", "manufacturer_mix", "resale_gross", "fleet", False, "oem_realisation_gap_pct", "Category Manager Hardware (name)", "ADV03"),
           ("L07", "term_length", "lifecycle_result", "fleet", False, "term_result_gap_alert_eur", "CFO (name)", "ADV04"))


def _insert(con, table: str, df: pd.DataFrame) -> None:
    """``INSERT ... BY NAME`` from a registered frame (DuckDB casts to the DDL types)."""
    if df is None or df.empty:
        return
    schema_name, name = table.split(".", 1)
    con.register("_mini_df", df.reset_index(drop=True))
    try:
        con.execute(f'INSERT INTO "{schema_name}"."{name}" BY NAME SELECT * FROM _mini_df')
    finally:
        con.unregister("_mini_df")


def _build_mini_lake(db_path: Path, as_of: date) -> None:
    """Create the lake tables (frozen DDL) and fill them from device_pnl with simple derived rows."""
    import duckdb

    con = duckdb.connect(str(db_path))
    try:
        for s in ("bronze", "silver", "gold"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        for table, cols in _MINI_DDL.items():
            schema_name, name = table.split(".", 1)
            con.execute(f'CREATE TABLE IF NOT EXISTS "{schema_name}"."{name}" ({cols})')
        pnl = con.execute("SELECT * FROM device_pnl").df().head(400)
        n = len(pnl)
        rng = np.random.default_rng(7)
        oems = np.where(pnl["model_family"] == "iphone_like", "Apple", np.where(pnl["model_family"] == "laptop_like", "Lenovo", "Samsung"))
        fam = np.where(pnl["model_family"] == "laptop_like", "Laptop", "Smartphone")
        purchase_price = pd.to_numeric(pnl["purchase_price"]).astype(float)
        rrp_net = (purchase_price / 0.85).round(2)
        landed = pd.to_numeric(pnl["landed_cost"]).astype(float)
        rent = pd.to_numeric(pnl["rental_revenue"]).astype(float).fillna(0.0)
        realised = pd.to_numeric(pnl["realised_rv"]).astype(float)
        fees = pd.to_numeric(pnl["channel_fees"]).astype(float).fillna(0.0)
        repair = pd.to_numeric(pnl["repair_cost"]).astype(float).fillna(0.0)
        refurb = pd.to_numeric(pnl["refurb_cost"]).astype(float).fillna(0.0)
        closed = pnl["is_closed"].astype(bool)
        status = pnl["lifecycle_status"].astype(str)
        holding = np.where(status.isin(["sold", "in_stock"]), 12.0, 0.0)
        tco_excl = 8.0 + 6.0 + repair + refurb + holding + fees + 5.0
        tco = landed + tco_excl
        result = np.where(closed, rent + realised.fillna(0.0) - tco, np.nan)
        sale_date = pd.to_datetime(pnl["sale_date"])
        return_date = pd.to_datetime(pnl["return_date"])
        pp_status = rng.choice(["not_applicable", "claimed", "open", "missed"], size=n, p=[0.6, 0.2, 0.1, 0.1])
        dl = pd.DataFrame(
            {
                "serial": pnl["serial"], "as_of": as_of, "slug": pnl["model"], "model_name": pnl["model"], "oem": oems, "catalogue_family": fam,
                "model_family": pnl["model_family"], "series": "S", "variant_spec": "base", "storage_gb": pnl["storage_gb"],
                "rrp_gross_eur": (rrp_net * 1.19).round(2), "rrp_net_eur": rrp_net, "launch_date": pnl["launch_date"],
                "months_since_launch_at_as_of": pnl["months_since_launch"], "po_number": "PO-2024-" + pd.Series(range(n)).mod(40).astype(str).str.zfill(6),
                "po_line": 1, "supplier_id": "S1", "supplier_name": np.where(rng.random(n) < 0.4, oems, "IT reseller A (role-only)"),
                "supplier_role": np.where(rng.random(n) < 0.4, "manufacturer", "reseller"), "contract_ref": "CTR-001",
                "order_date": pd.to_datetime(pnl["purchase_date"]) - timedelta(days=20), "received_at": pnl["purchase_date"],
                "purchase_date": pnl["purchase_date"], "cohort_month": pd.to_datetime(pnl["purchase_date"]).dt.to_period("M").dt.to_timestamp().dt.date,
                "cohort_quarter": pnl["cohort"], "price_protection_days": 30, "price_protection_status": pp_status,
                "price_protection_claimable_eur": np.where(pp_status == "not_applicable", 0.0, 25.0), "purchase_price": purchase_price,
                "discount_vs_rrp_eur": (rrp_net - purchase_price).round(2), "discount_vs_rrp_pct": 0.15, "freight_eur": 6.0, "duty_eur": 0.0,
                "landed_cost": landed, "landed_vs_rrp_pct": landed / rrp_net, "price_protection_credit_eur": np.where(pp_status == "claimed", 25.0, 0.0),
                "staging_eur": 8.0, "outbound_shipping_eur": 6.0, "repair_eur": repair, "replacement_logistics_eur": 0.0, "return_logistics_eur": 5.0,
                "wipe_grading_eur": 5.0, "refurb_eur": refurb, "holding_cost_eur": holding, "channel_fee_eur": fees,
                "days_in_stock_to_date": pnl["days_in_stock"], "tco_excl_landed_eur": tco_excl, "tco_transactional_eur": tco - holding, "tco_eur": tco,
                "n_lines": 10, "n_estimate_lines": np.where(holding > 0, 1, 0), "first_contract_id": "RC-" + pnl["serial"].astype(str),
                "customer_id": "CUST-0001", "term_months": np.where(rng.random(n) < 0.5, 24, 36), "monthly_rate": 30.0,
                "contract_start": pnl["purchase_date"], "contract_end_planned": pd.to_datetime(pnl["purchase_date"]) + timedelta(days=730),
                "contract_end_effective": return_date, "months_billed": pnl["months_billed"], "months_remaining": 6, "rental_revenue": rent,
                "remaining_contracted_rent": 180.0, "return_date": return_date, "grade_declared": pnl["grade_inspected"], "grade_inspected": pnl["grade_inspected"],
                "wipe_certificate_id": "WIPE-1", "grade_out": pnl["grade_current"], "refurb_outcome": pnl["refurb_outcome"], "sellable_date": pnl["sellable_date"],
                "resale_channel": pnl["resale_channel"], "listed_at": sale_date - timedelta(days=5), "sale_date": sale_date,
                "credited_at": sale_date + timedelta(days=20), "resale_gross": pd.to_numeric(pnl["resale_price_gross"]).astype(float),
                "resale_net": pd.to_numeric(pnl["resale_price_gross"]).astype(float) - fees, "days_return_to_sale": (sale_date - return_date).dt.days,
                "days_return_to_cash": (sale_date - return_date).dt.days + 20, "estimate_run_id": "forecast-1", "grade_used": "B", "grade_source": "assumption",
                "estimate_rv_today": pd.to_numeric(pnl["forecast_rv"]).astype(float), "estimate_rv_lease_end": (purchase_price * 0.30).round(2),
                "estimate_months_at_lease_end": 36, "estimate_rv_source": "grid", "estimate_fit_quality": "ok",
                "estimate_rv_of_record": pd.to_numeric(pnl["forecast_rv"]).astype(float), "anchor_curve_group": fam + " / " + oems, "anchor_fit_quality": "ok",
                "anchor_rv_lease_end": np.where(rng.random(n) < 0.8, (rrp_net * 0.40).round(2), np.nan), "estimate_vs_anchor_ratio": 0.75,
                "realised_vs_record_ratio": realised / pd.to_numeric(pnl["forecast_rv"]).astype(float), "realised_rv": realised,
                "lifecycle_result_eur": result, "result_v01_basis_eur": pd.to_numeric(pnl["lifecycle_margin"]).astype(float),
                "result_pct_of_landed": result / landed, "result_if_liquidated_today": np.where(closed, np.nan, pd.to_numeric(pnl["margin_if_liquidated_today"]).astype(float)),
                "result_projected_at_lease_end": np.where(closed, np.nan, pd.to_numeric(pnl["margin_if_liquidated_today"]).astype(float) + 150.0),
                "projected_label": np.where(closed, None, "projected at lease end"), "expected_remaining_cost": np.where(closed, np.nan, 40.0),
                "expected_cost_inputs_source": "assumption", "lifecycle_status": status, "is_closed": closed, "closed_date": pnl["closed_date"],
                "chain_complete": True, "is_synthetic": True,
            }
        )
        _insert(con, "silver.device_ledger", dl)
        lines = pd.DataFrame(
            {
                "line_id": [f"L{i}" for i in range(4)], "serial": dl["serial"].iloc[0], "line_type": ["purchase_price", "freight", "rental_revenue", "holding_cost"],
                "line_class": ["cost", "cost", "revenue", "cost"], "amount_eur": [-float(purchase_price.iloc[0]), -6.0, float(rent.iloc[0]), -12.0],
                "event_date": [as_of] * 4, "period_month": [as_of.replace(day=1)] * 4, "source_system": ["erp", "erp", "portal", "assumption"],
                "source_table": ["bronze.erp_supplier_invoices"] * 3 + ["silver.serial_timeline"], "source_ref": ["erp:INV-1/1", "erp:INV-1/2", "portal:RI-1", "assumption:holding"],
                "delivery_id": None, "allocation_basis": ["transaction", "per_unit_of_po_line", "transaction", "days_x_rate"], "is_estimate": [False, False, False, True],
                "assumption_key": [None, None, None, "holding_cost_per_day_eur"], "assumption_owner": [None, None, None, "CFO (name)"], "counterparty": "Apple",
                "counterparty_role": "manufacturer", "contract_ref": "CTR-001", "as_of": as_of, "is_synthetic": True,
            }
        )
        _insert(con, "silver.ledger_lines", lines)
        _insert(con, "silver.serial_timeline", pd.DataFrame({
            "serial": dl["serial"], "as_of": as_of, "ordered_at": dl["order_date"], "received_at": dl["received_at"], "lifecycle_status": status,
            "steps_expected": 4, "steps_present": 4, "is_monotonic": True, "chain_complete": True, "source_refs_json": "{}", "is_synthetic": True,
        }))
        _insert(con, "silver.reconciliation", pd.DataFrame({"serial": dl["serial"].head(3), "as_of": as_of, "field": "landed_cost",
                                                              "device_pnl_value": 1.0, "ledger_value": 1.0, "diff": 0.0, "ok": True}))
        _insert(con, "bronze.deliveries", pd.DataFrame({
            "delivery_id": ["d1", "d2"], "source_system": ["erp", "wms"], "feed": ["goods_receipts", "shipments"], "source_file": ["2024-03-31_goods_receipts_001.csv", "2024-03-31_shipments_001.csv"],
            "sha256": ["a" * 64, "b" * 64], "delivered_on": [date(2024, 3, 31)] * 2, "ingested_at": [pd.Timestamp("2026-01-01")] * 2, "rows_read": [100, 90], "rows_typed": [100, 89],
            "rows_new": [98, 88], "duplicates_identical": [2, 0], "duplicates_conflict": [0, 0], "n_unresolved": [0, 2], "reasons_json": ["{}", '{"unknown_serial": 2}'], "is_synthetic": True,
        }))
        _insert(con, "bronze.unresolved", pd.DataFrame({
            "unresolved_id": ["u1", "u2"], "delivery_id": "d2", "source_system": "wms", "feed": "shipments", "source_file": "2024-03-31_shipments_001.csv", "row_number": [5, 9],
            "key_json": '{"shipment_id": "SH-1"}', "reason_code": ["unknown_serial", "bad_type"], "reason_text": "serial not in goods receipts", "row_json": "{}",
            "ingested_at": pd.Timestamp("2026-01-01"), "is_synthetic": True,
        }))
        _insert(con, "gold.ingest_summary", pd.DataFrame({
            "feed": ["erp/goods_receipts", "wms/shipments"], "source_system": ["erp", "wms"], "delivering_system": ["ERP", "WMS"], "n_files": 1, "last_delivered_on": date(2024, 3, 31),
            "rows_read": [100, 90], "rows_new": [98, 88], "duplicates_identical": [2, 0], "duplicates_conflict": 0, "n_unresolved": [0, 2], "bronze_rows": [98, 88], "as_of": as_of,
        }))
        _insert(con, "gold.chain_quality", pd.DataFrame({
            "lifecycle_status": ["rented", "rented", "sold", "sold"], "step": ["ordered_at", "sold_at", "ordered_at", "sold_at"], "n_serials": 10, "n_present": [10, 0, 10, 9],
            "share_present": [1.0, 0.0, 1.0, 0.9], "is_expected": [True, False, True, True], "as_of": as_of,
        }))
        g = dl.assign(purchase_month=pd.to_datetime(dl["purchase_date"]).dt.to_period("M").dt.to_timestamp().dt.date)
        pbm = g.groupby(["oem", "purchase_month", "supplier_role"], as_index=False).agg(
            n_units=("serial", "size"), sum_rrp_net=("rrp_net_eur", "sum"), sum_unit_price=("purchase_price", "sum"), sum_landed=("landed_cost", "sum"),
            pp_claimable_eur=("price_protection_claimable_eur", "sum"), pp_credited_eur=("price_protection_credit_eur", "sum"))
        pbm["sum_freight_duty"] = pbm["sum_landed"] - pbm["sum_unit_price"]
        pbm["discount_vs_rrp_pct"] = 1 - pbm["sum_unit_price"] / pbm["sum_rrp_net"]
        pbm["landed_vs_rrp_pct"] = pbm["sum_landed"] / pbm["sum_rrp_net"]
        pbm["ppv_vs_po_eur"] = 0.0
        pbm["share_under_contract"] = 0.9
        pbm["as_of"] = as_of
        _insert(con, "gold.purchase_by_oem_month", pbm)
        cl = dl[dl["is_closed"]]
        rows = []
        for kind in ("catalogue_family", "oem"):
            for val, grp in cl.groupby(kind):
                for lt, col in (("purchase_price", "purchase_price"), ("freight", "freight_eur"), ("repair", "repair_eur"), ("holding_cost", "holding_cost_eur"), ("channel_fee", "channel_fee_eur")):
                    rows.append({"cohort_kind": kind, "cohort_value": str(val), "line_type": lt, "n_devices": len(grp), "mean_eur": float(grp[col].mean()),
                                 "sum_eur": float(grp[col].sum()), "estimate_eur": float(grp[col].sum()) if lt == "holding_cost" else 0.0,
                                 "is_estimate": lt == "holding_cost", "as_of": as_of})
        _insert(con, "gold.tco_by_cohort", pd.DataFrame(rows))
        rented = dl[dl["lifecycle_status"].isin(["rented", "awaiting_return"])]
        eva = rented.groupby(["catalogue_family", "oem"], as_index=False).agg(n_rented=("serial", "size"), n_with_anchor=("anchor_rv_lease_end", "count"),
                                                                                 sum_estimate_lease_end=("estimate_rv_lease_end", "sum"), sum_anchor_lease_end=("anchor_rv_lease_end", "sum"))
        eva["mean_estimate_ratio"] = 0.30 * 0.85
        eva["mean_anchor_ratio"] = 0.40
        eva["estimate_vs_anchor_ratio"] = eva["sum_estimate_lease_end"] / eva["sum_anchor_lease_end"]
        eva["anchor_curve_group"] = eva["catalogue_family"] + " / " + eva["oem"]
        eva["anchor_fit_quality"] = "ok"
        eva["as_of"] = as_of
        _insert(con, "gold.estimate_vs_anchor", eva)
        sold = dl[dl["sale_date"].notna()]
        rcg = sold.groupby(["resale_channel", "grade_out"], as_index=False).agg(n=("serial", "size"), sum_gross=("resale_gross", "sum"), sum_fees=("channel_fee_eur", "sum"),
                                                                                 sum_net=("resale_net", "sum"), sum_refurb=("refurb_eur", "sum"), sum_estimate_of_record=("estimate_rv_of_record", "sum"),
                                                                                 median_days_return_to_cash=("days_return_to_cash", "median"))
        rcg = rcg.rename(columns={"resale_channel": "channel", "grade_out": "grade_at_sale"})
        rcg["realised_vs_record_ratio"] = rcg["sum_gross"] / rcg["sum_estimate_of_record"]
        rcg["n_credit_note_missing"] = 0
        rcg["as_of"] = as_of
        _insert(con, "gold.resale_by_channel_grade", rcg)
        rows = []
        for kind in ("purchase_month", "oem", "catalogue_family", "term_months"):
            key = "cohort_month" if kind == "purchase_month" else kind
            for val, grp in dl.groupby(key):
                c_ = grp[grp["is_closed"]]
                o_ = grp[~grp["is_closed"]]
                rows.append({"cohort_kind": kind, "cohort_value": str(val), "n": len(grp), "n_closed": len(c_), "n_open": len(o_),
                             "sum_result_closed": float(c_["lifecycle_result_eur"].sum()) if len(c_) else None,
                             "mean_result_closed": float(c_["lifecycle_result_eur"].mean()) if len(c_) else None,
                             "result_pct_of_landed_closed": 0.1, "sum_rental_revenue_closed": float(c_["rental_revenue"].sum()), "sum_realised_rv_closed": float(c_["realised_rv"].sum()),
                             "sum_pp_credit_closed": float(c_["price_protection_credit_eur"].sum()), "sum_tco_closed": float(c_["tco_eur"].sum()), "sum_landed_closed": float(c_["landed_cost"].sum()),
                             "tco_per_closed_device": float(c_["tco_eur"].mean()) if len(c_) else None, "rv_per_closed_device": float(c_["realised_rv"].mean()) if len(c_) else None,
                             "rent_per_closed_device": float(c_["rental_revenue"].mean()) if len(c_) else None,
                             "sum_liquidation_today_open": float(o_["result_if_liquidated_today"].sum()) if len(o_) else None,
                             "mean_liquidation_today_open": float(o_["result_if_liquidated_today"].mean()) if len(o_) else None,
                             "sum_projected_lease_end_open": float(o_["result_projected_at_lease_end"].sum()) if len(o_) else None,
                             "mean_projected_lease_end_open": float(o_["result_projected_at_lease_end"].mean()) if len(o_) else None, "as_of": as_of})
        _insert(con, "gold.result_by_cohort", pd.DataFrame(rows))
        _insert(con, "gold.levers_summary", pd.DataFrame([{
            "lever_id": lid, "lever_name": name, "component": comp, "basis": basis, "additive": add, "n_eligible": 100, "n_attributed": 90 - i * 5,
            "eur_per_device": 20.0 + i, "eur_per_device_p90": 60.0, "eur_fleet_per_year": 5000.0 - 400 * i, "share_of_lever_basis": 0.1, "lever_basis_eur": 50000.0, "lever_basis": "absolute closed result of the same serials in the window", "reference_key": "n/a", "reference_owner": "the serial's own data",
            "threshold_key": key, "threshold_value": "0.1", "threshold_unit": "ratio", "threshold_owner": owner, "rule_id": rule,
            "reference_sentence": "actual minus a named reference", "rank": i + 1, "as_of": as_of,
        } for i, (lid, name, comp, basis, add, key, owner, rule) in enumerate(_LEVERS)]))
        _insert(con, "gold.levers_by_cohort", pd.DataFrame([{"cohort_kind": "oem", "cohort_value": o, "lever_id": lid, "n_attributed": 10, "sum_delta_eur": 300.0, "mean_delta_eur": 30.0, "as_of": as_of}
                                                             for o in ("Apple", "Samsung", "Lenovo") for lid, *_ in _LEVERS]))
        _insert(con, "gold.levers_per_device", pd.DataFrame([{"serial": s, "lever_id": "L01", "delta_eur": 12.5, "actual_value": 0.1, "reference_value": 0.15, "reference_source": "fleet p75",
                                                              "n_reference": 40, "is_attributed": True, "additive": True, "basis": "fleet", "event_date": as_of, "counterfactual_json": "{}", "as_of": as_of}
                                                             for s in dl["serial"].head(20)]))
        contracts = pd.DataFrame([
            {"contract_id": "CTR-001", "counterparty_name": "Apple", "counterparty_role": "manufacturer", "counterparty_is_public": True, "category": "hardware",
             "start_date": date(2023, 1, 1), "end_date": as_of + timedelta(days=90), "notice_days": 60, "notice_deadline": as_of + timedelta(days=30), "auto_renewal": True,
             "price_protection": True, "price_protection_days": 30, "claim_window_days": 14, "warranty_months": 12, "rebate_tiers_json": None, "volume_commitment_units": None,
             "payment_terms_days": 30, "sla_json": '{"delivery_lead_days": 14}', "spend_under_contract_eur": 500000.0, "spend_actual_12m_eur": 420000.0, "spend_actual_vs_planned_pct": 0.84,
             "covers_oems": None, "n_serials_under_contract": 300, "status": "active", "days_to_notice_deadline": 30, "days_to_end": 90, "action_required": True,
             "terms_note": "synthetic placeholder terms", "is_synthetic": True, "as_of": as_of},
            {"contract_id": "CTR-002", "counterparty_name": "IT reseller A (role-only)", "counterparty_role": "reseller", "counterparty_is_public": False, "category": "hardware",
             "start_date": date(2022, 1, 1), "end_date": as_of + timedelta(days=400), "notice_days": 90, "notice_deadline": as_of + timedelta(days=310), "auto_renewal": False,
             "price_protection": True, "price_protection_days": 30, "claim_window_days": 14, "warranty_months": None, "rebate_tiers_json": '[{"from_eur": 0, "pct": 0.0}, {"from_eur": 250000, "pct": 0.01}]',
             "volume_commitment_units": None, "payment_terms_days": 30, "sla_json": None, "spend_under_contract_eur": 300000.0, "spend_actual_12m_eur": 200000.0, "spend_actual_vs_planned_pct": 0.67,
             "covers_oems": "Samsung, Lenovo", "n_serials_under_contract": 200, "status": "active", "days_to_notice_deadline": 310, "days_to_end": 400, "action_required": False,
             "terms_note": "synthetic placeholder terms", "is_synthetic": True, "as_of": as_of},
        ])
        _insert(con, "silver.contracts", contracts)
        _insert(con, "gold.contract_coverage_by_oem", pd.DataFrame({"oem": ["Apple", "Samsung", "Lenovo"], "n_units": [100, 80, 60], "spend_total": [90000.0, 40000.0, 50000.0],
                                                                    "spend_under_contract": [85000.0, 20000.0, 45000.0], "coverage_pct": [0.94, 0.5, 0.9], "spend_direct": [50000.0, 0.0, 30000.0],
                                                                    "spend_via_reseller": [40000.0, 40000.0, 20000.0], "n_contracts_in_force": [1, 1, 1], "next_notice_deadline": as_of + timedelta(days=30), "as_of": as_of}))
        _insert(con, "gold.renewal_calendar_v2", pd.DataFrame({"contract_id": ["CTR-001"], "counterparty_name": ["Apple"], "counterparty_role": ["manufacturer"], "category": ["hardware"],
                                                               "end_date": [as_of + timedelta(days=90)], "notice_days": [60], "notice_deadline": [as_of + timedelta(days=30)], "days_to_notice_deadline": [30],
                                                               "days_to_end": [90], "auto_renewal": [True], "spend_under_contract_eur": [500000.0], "spend_actual_12m_eur": [420000.0], "price_protection_days": [30],
                                                               "claim_window_days": [14], "price_protection_window_open": [True], "action_required": [True], "month_bucket": ["2026-09"], "as_of": [as_of]}))
        _insert(con, "gold.rebate_progress", pd.DataFrame({"contract_id": ["CTR-002"], "counterparty_name": ["IT reseller A (role-only)"], "spend_12m_eur": [200000.0], "current_tier_pct": [0.0],
                                                           "next_tier_from_eur": [250000.0], "next_tier_pct": [0.01], "gap_to_next_tier_eur": [50000.0], "as_of": [as_of]}))
        _insert(con, "gold.kpi_values", pd.DataFrame([{"kpi_id": k, "as_of": as_of, "name": k.lower(), "page": "0 Data", "value": 0.5, "numerator": 1.0, "denominator": 2.0, "n": 10, "unit": "ratio",
                                                       "status": "ok", "note": "", "target": None, "direction": "up", "formula_text": "a / b", "source_tables": "silver.device_ledger", "owner": "CFO (name)", "run_id": "kpis-1"}
                                                      for k in _GOLD_KPIS]))
        _insert(con, "gold.kpi_breakdown", pd.DataFrame({"kpi_id": ["KPI_DATA_CHAIN_COMPLETE"], "as_of": [as_of], "dimension": ["lifecycle_status"], "dimension_value": ["sold"], "value": [0.9], "numerator": [9.0], "denominator": [10.0], "n": [10]}))
    finally:
        con.close()


@pytest.fixture(scope="session")
def mini_lake_db_path(full_pipeline_paths, tmp_path_factory, as_of) -> Path:
    """The v0.1 pipeline DB copied and extended with hand-filled lake tables (frozen DDL)."""
    assert full_pipeline_paths.return_code == 0
    target = tmp_path_factory.mktemp("mini_lake") / "mini.duckdb"
    shutil.copy(full_pipeline_paths.db, target)
    _build_mini_lake(target, as_of)
    return target


@pytest.fixture(scope="session")
def dashboard_db_path(request) -> Path:
    """The session lake DB when the v0.2 packages are present, else the mini lake."""
    if lake_packages_available():
        paths = request.getfixturevalue("lake_pipeline_paths")
        assert paths.return_code == 0
        return paths.db
    return request.getfixturevalue("mini_lake_db_path")


def _app(db_path: Path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.session_state["db_path"] = str(db_path)
    at.run()
    return at


def _switch(at, title: str) -> None:
    """Select a callable navigation page by its title (the default page registers an empty url path)."""
    for page_hash, info in at._registered_pages.items():
        if info.get("page_name") == title:
            at._page_hash = page_hash
            return
    raise KeyError(f"page {title!r} not registered; known: {[i.get('page_name') for i in at._registered_pages.values()]}")


def _cycle_titles() -> list[tuple[str, str]]:
    from restwert.dashboard.views import CYCLE_PAGES

    return [(title, url) for title, url, _ in CYCLE_PAGES]


# --------------------------------------------------------------------------- tests


def test_views_registry_shape():
    from restwert.dashboard.views import CYCLE_PAGES, ENGINE_PAGES, TABS

    assert [t for t, _ in TABS] == ["Realisation", "Overview", "Residual value curves", "Inventory", "Decision queue", "Contracts", "Export"]
    assert [t for t, _, _ in CYCLE_PAGES] == ["Realisation", "0 Data", "1 Purchase", "2 TCO", "3 Residual estimate", "4 Resale", "5 Result", "6 Levers", "7 Contracts"]
    assert [u for _, u, _ in CYCLE_PAGES] == ["realisation", "data", "purchase", "tco", "residual", "resale", "result", "levers", "contracts-v2"]
    assert [t for t, _, _ in ENGINE_PAGES] == ["Overview", "Residual value curves", "Inventory", "Decision queue", "Contracts (v0.1)", "Export"]
    urls = [u for _, u, _ in CYCLE_PAGES + ENGINE_PAGES]
    assert len(urls) == len(set(urls))
    for _, _, view in CYCLE_PAGES + ENGINE_PAGES:
        assert callable(getattr(view, "render", None))


def test_dashboard_navigation_has_cycle_and_engine_pages(dashboard_db_path):
    at = _app(dashboard_db_path)
    assert not at.exception, [str(e.value) for e in at.exception]
    assert not at.error, [str(e.value) for e in at.error]
    titles = [info.get("page_name") for info in at._registered_pages.values()]
    for title, _ in _cycle_titles():
        assert title in titles, title
    for title in ("Overview", "Residual value curves", "Inventory", "Decision queue", "Contracts (v0.1)", "Export"):
        assert title in titles, title
    sidebar_metrics = [m.label for m in at.sidebar.metric]
    assert "Lifecycle margin per device" in sidebar_metrics
    assert "Residual value forecast error" in sidebar_metrics
    assert any("SYNTHETIC" in str(w.value) for w in at.warning), "synthetic banner missing"
    assert any(GOVERNANCE_PRINCIPLE in str(c.value) for c in at.caption)


def test_each_cycle_page_renders_headless(dashboard_db_path):
    at = _app(dashboard_db_path)
    for title, _ in _cycle_titles():
        _switch(at, title)
        at.run()
        assert not at.exception, f"{title}: " + "; ".join(str(e.value) for e in at.exception)
        assert not at.error, f"{title}: " + "; ".join(str(e.value) for e in at.error)
        n_metrics = len(at.main.metric)
        assert n_metrics <= 4, f"{title}: {n_metrics} metrics on the page body"
        subheaders = [s.value for s in at.main.subheader]
        assert len(subheaders) == 1, f"{title}: subheaders {subheaders}"
        if title != "Realisation":
            assert subheaders[0].endswith("?"), f"{title}: the subheader is not a question: {subheaders[0]}"
            assert not at.main.info, f"{title}: shows the run hint although the lake tables are present"
            assert n_metrics == 4, f"{title}: expected four tiles, got {n_metrics}"


def test_result_page_never_totals_open_and_closed(dashboard_db_path):
    at = _app(dashboard_db_path)
    _switch(at, "5 Result")
    at.run()
    assert not at.exception and not at.error
    labels = [m.label for m in at.main.metric]
    assert any("if liquidated today" in lb for lb in labels), labels
    assert any("projected at lease end" in lb for lb in labels), labels
    for lb in labels:
        assert "total result" not in lb.lower(), lb
    columns: list[str] = []
    for df_el in at.main.dataframe:
        try:
            columns.extend(str(c) for c in df_el.value.columns)
        except AttributeError:
            continue
    assert columns, "no table on the Result page"
    for col in columns:
        assert "total result" not in col.lower() and col != "result_total", col
    assert any("never add them" in str(c.value) for c in at.main.caption)


@pytest.fixture(scope="module")
def v01_only_db_path(tmp_path_factory) -> Path:
    """A database built by the legacy chain (``all --v01 --small``): no bronze, silver or gold tables."""
    from restwert.cli import main

    base = tmp_path_factory.mktemp("v01_only")
    rc = main(["all", "--v01", "--small", "--db", str(base / "v01.duckdb"), "--out", str(base / "out"), "--csv-dir", str(base / "raw_csv")])
    assert rc == 0
    return base / "v01.duckdb"


def test_cycle_pages_show_run_hint_without_lake_tables(v01_only_db_path):
    """On a v0.1-only database every Cycle page after Realisation shows one info line and nothing else."""
    at = _app(v01_only_db_path)
    for title, _ in _cycle_titles():
        if title == "Realisation":
            continue
        _switch(at, title)
        at.run()
        assert not at.exception and not at.error, title
        infos = [i.value for i in at.main.info]
        assert len(infos) == 1 and "python -m restwert all" in infos[0], f"{title}: {infos}"
        assert len(at.main.metric) == 0, title
        assert len(at.main.subheader) == 1, title


def test_headless_app_default_page_keeps_v01_contract(dashboard_db_path):
    """The v0.1 headless test contract holds on the v0.2 app: tiles reachable from ``at.metric``."""
    at = _app(dashboard_db_path)
    labels = [m.label for m in at.metric]
    assert "Lifecycle margin per device" in labels
    assert "Residual value forecast error" in labels
