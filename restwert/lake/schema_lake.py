"""Frozen DDL of the v0.2 data lake: schemas ``bronze``, ``silver``, ``gold`` (SPEC_v0.2 section 3.8).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Decision D1: three DuckDB schemas inside the one file ``data/restwert.duckdb``;
the ten v0.1 source tables stay unqualified in ``main``. Every bronze table
ends with ``BRONZE_TAIL`` (delivery id, source file, row number, ingestion
timestamp, row hash, synthetic flag). Money is ``DECIMAL(12,2)`` (spend
aggregates ``DECIMAL(14,2)``), ratios ``DOUBLE``, timestamps ``TIMESTAMP``,
dates ``DATE``. Primary keys are the business keys.

``LAKE_DDL`` is keyed by the qualified table name (``bronze.deliveries``).
"""

from __future__ import annotations

import duckdb

LAKE_SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold")

BRONZE_TAIL = (
    "delivery_id VARCHAR NOT NULL, source_file VARCHAR NOT NULL, row_number INTEGER NOT NULL, "
    "ingested_at TIMESTAMP NOT NULL, row_hash VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL"
)

_T = BRONZE_TAIL

BRONZE_DDL: dict[str, str] = {
    # registry and rejects
    "bronze.deliveries": """CREATE TABLE IF NOT EXISTS bronze.deliveries (
  delivery_id VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL, source_file VARCHAR NOT NULL,
  sha256 VARCHAR NOT NULL UNIQUE, delivered_on DATE NOT NULL, ingested_at TIMESTAMP NOT NULL,
  rows_read INTEGER NOT NULL, rows_typed INTEGER NOT NULL, rows_new INTEGER NOT NULL,
  duplicates_identical INTEGER NOT NULL, duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL,
  reasons_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL);""",
    "bronze.unresolved": """CREATE TABLE IF NOT EXISTS bronze.unresolved (
  unresolved_id VARCHAR PRIMARY KEY, delivery_id VARCHAR NOT NULL, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL,
  source_file VARCHAR NOT NULL, row_number INTEGER NOT NULL, key_json VARCHAR NOT NULL, reason_code VARCHAR NOT NULL,
  reason_text VARCHAR NOT NULL, row_json VARCHAR NOT NULL, ingested_at TIMESTAMP NOT NULL, is_synthetic BOOLEAN NOT NULL);""",
    # reference feeds (public)
    "bronze.cat_models": f"""CREATE TABLE IF NOT EXISTS bronze.cat_models (
  slug VARCHAR PRIMARY KEY, model_name VARCHAR NOT NULL, oem VARCHAR NOT NULL, family VARCHAR NOT NULL, series VARCHAR,
  launch_date_de VARCHAR, launch_date DATE, launch_date_kind VARCHAR, launch_source_url VARCHAR, successor VARCHAR,
  successor_launch_date DATE, notes VARCHAR, {_T});""",
    "bronze.cat_variants": f"""CREATE TABLE IF NOT EXISTS bronze.cat_variants (
  slug VARCHAR NOT NULL, spec VARCHAR NOT NULL, storage_gb INTEGER, ram_gb INTEGER, rrp_eur_launch_de DECIMAL(12,2),
  rrp_source_url VARCHAR, rrp_source_date DATE, {_T}, PRIMARY KEY (slug, spec));""",
    "bronze.mkt_curves": f"""CREATE TABLE IF NOT EXISTS bronze.mkt_curves (
  group_kind VARCHAR NOT NULL, "group" VARCHAR NOT NULL, population VARCHAR NOT NULL, n INTEGER, age_min DOUBLE, age_max DOUBLE,
  intercept DOUBLE, slope_per_month DOUBLE, monthly_depreciation_pct DOUBLE, grade_A_offset DOUBLE, grade_C_offset DOUBLE,
  grade_D_offset DOUBLE, mape_in_sample DOUBLE, q_12 DOUBLE, q_24 DOUBLE, q_36 DOUBLE, fit_quality VARCHAR NOT NULL,
  {_T}, PRIMARY KEY (group_kind, "group", population));""",
    # ERP
    "bronze.erp_purchase_orders": f"""CREATE TABLE IF NOT EXISTS bronze.erp_purchase_orders (
  po_number VARCHAR PRIMARY KEY, supplier_id VARCHAR NOT NULL, supplier_name VARCHAR NOT NULL, supplier_role VARCHAR NOT NULL,
  contract_ref VARCHAR, order_date DATE NOT NULL, promised_date DATE NOT NULL, currency VARCHAR NOT NULL, incoterm VARCHAR,
  payment_terms_days INTEGER, {_T});""",
    "bronze.erp_po_lines": f"""CREATE TABLE IF NOT EXISTS bronze.erp_po_lines (
  po_number VARCHAR NOT NULL, po_line INTEGER NOT NULL, slug VARCHAR NOT NULL, storage_gb INTEGER NOT NULL, colour VARCHAR,
  qty_ordered INTEGER NOT NULL, unit_price_eur DECIMAL(12,2) NOT NULL, price_protection_days INTEGER,
  {_T}, PRIMARY KEY (po_number, po_line));""",
    "bronze.erp_goods_receipts": f"""CREATE TABLE IF NOT EXISTS bronze.erp_goods_receipts (
  gr_number VARCHAR NOT NULL, po_number VARCHAR NOT NULL, po_line INTEGER NOT NULL, serial VARCHAR PRIMARY KEY,
  received_at TIMESTAMP NOT NULL, warehouse VARCHAR, {_T});""",
    "bronze.erp_supplier_invoices": f"""CREATE TABLE IF NOT EXISTS bronze.erp_supplier_invoices (
  invoice_number VARCHAR NOT NULL, invoice_line INTEGER NOT NULL, supplier_id VARCHAR NOT NULL, po_number VARCHAR NOT NULL,
  po_line INTEGER NOT NULL, serial VARCHAR, invoice_date DATE NOT NULL, line_kind VARCHAR NOT NULL, qty INTEGER NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, currency VARCHAR NOT NULL, {_T}, PRIMARY KEY (invoice_number, invoice_line));""",
    "bronze.erp_price_changes": f"""CREATE TABLE IF NOT EXISTS bronze.erp_price_changes (
  change_id VARCHAR PRIMARY KEY, supplier_id VARCHAR NOT NULL, slug VARCHAR NOT NULL, storage_gb INTEGER NOT NULL,
  valid_from DATE NOT NULL, old_unit_price_eur DECIMAL(12,2) NOT NULL, new_unit_price_eur DECIMAL(12,2) NOT NULL, {_T});""",
    # WMS
    "bronze.wms_staging_log": f"""CREATE TABLE IF NOT EXISTS bronze.wms_staging_log (
  staging_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, staged_at TIMESTAMP NOT NULL, mdm_enrolled BOOLEAN NOT NULL,
  staging_cost_eur DECIMAL(12,2) NOT NULL, {_T});""",
    "bronze.wms_shipments": f"""CREATE TABLE IF NOT EXISTS bronze.wms_shipments (
  shipment_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, related_serial VARCHAR, direction VARCHAR NOT NULL,
  shipped_at TIMESTAMP NOT NULL, delivered_at TIMESTAMP, rental_contract_ref VARCHAR, carrier_ref VARCHAR NOT NULL,
  cost_eur DECIMAL(12,2) NOT NULL, {_T});""",
    # customer portal
    "bronze.portal_rental_contracts": f"""CREATE TABLE IF NOT EXISTS bronze.portal_rental_contracts (
  contract_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, start_date DATE NOT NULL,
  term_months INTEGER NOT NULL, monthly_rate_eur DECIMAL(12,2) NOT NULL, end_date DATE NOT NULL, actual_end_date DATE,
  status VARCHAR NOT NULL, replaces_contract_id VARCHAR, {_T});""",
    "bronze.portal_rental_invoices": f"""CREATE TABLE IF NOT EXISTS bronze.portal_rental_invoices (
  invoice_id VARCHAR PRIMARY KEY, contract_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, period_no INTEGER NOT NULL,
  period_month DATE NOT NULL, invoice_date DATE NOT NULL, amount_eur DECIMAL(12,2) NOT NULL, {_T});""",
    # service desk
    "bronze.sd_tickets": f"""CREATE TABLE IF NOT EXISTS bronze.sd_tickets (
  ticket_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, opened_at TIMESTAMP NOT NULL, closed_at TIMESTAMP,
  damage_type VARCHAR NOT NULL, resolution VARCHAR NOT NULL, quote_eur DECIMAL(12,2) NOT NULL, repair_cost_eur DECIMAL(12,2),
  replacement_serial VARCHAR, repair_partner_ref VARCHAR NOT NULL, {_T});""",
    # returns desk
    "bronze.ret_receipts": f"""CREATE TABLE IF NOT EXISTS bronze.ret_receipts (
  receipt_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, returned_at TIMESTAMP NOT NULL,
  grade_declared VARCHAR NOT NULL, grade_inspected VARCHAR NOT NULL, inspected_at TIMESTAMP NOT NULL,
  wipe_certificate_id VARCHAR, wiped_at TIMESTAMP, wipe_grading_cost_eur DECIMAL(12,2) NOT NULL, {_T});""",
    # refurbishment partner
    "bronze.rf_work_orders": f"""CREATE TABLE IF NOT EXISTS bronze.rf_work_orders (
  work_order_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP NOT NULL,
  cost_eur DECIMAL(12,2) NOT NULL, grade_out VARCHAR NOT NULL, outcome VARCHAR NOT NULL, partner_ref VARCHAR NOT NULL, {_T});""",
    # recommerce channels
    "bronze.rc_orders": f"""CREATE TABLE IF NOT EXISTS bronze.rc_orders (
  order_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL, listed_at TIMESTAMP NOT NULL,
  sold_at TIMESTAMP NOT NULL, gross_price_eur DECIMAL(12,2) NOT NULL, buyer_type VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL, {_T});""",
    "bronze.rc_credit_notes": f"""CREATE TABLE IF NOT EXISTS bronze.rc_credit_notes (
  credit_note_id VARCHAR PRIMARY KEY, order_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL,
  credited_at TIMESTAMP NOT NULL, gross_eur DECIMAL(12,2) NOT NULL, fee_pct_eur DECIMAL(12,2) NOT NULL,
  fee_fixed_eur DECIMAL(12,2) NOT NULL, net_eur DECIMAL(12,2) NOT NULL, {_T});""",
    # contracts register (CLM)
    "bronze.ctr_register": f"""CREATE TABLE IF NOT EXISTS bronze.ctr_register (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL,
  counterparty_is_public BOOLEAN NOT NULL, category VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  notice_days INTEGER NOT NULL, auto_renewal BOOLEAN NOT NULL, price_protection BOOLEAN NOT NULL, price_protection_days INTEGER,
  claim_window_days INTEGER, warranty_months INTEGER, rebate_tiers_json VARCHAR, volume_commitment_units INTEGER,
  payment_terms_days INTEGER NOT NULL, sla_json VARCHAR, spend_under_contract_eur DECIMAL(14,2) NOT NULL,
  terms_note VARCHAR NOT NULL, {_T});""",
    # finance (indirect)
    "bronze.fin_indirect_spend": f"""CREATE TABLE IF NOT EXISTS bronze.fin_indirect_spend (
  spend_id VARCHAR PRIMARY KEY, invoice_date DATE NOT NULL, category VARCHAR NOT NULL, supplier_name VARCHAR NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, has_po BOOLEAN NOT NULL, has_contract BOOLEAN NOT NULL, saving_eur DECIMAL(12,2) NOT NULL,
  saving_confirmed_by_controlling BOOLEAN NOT NULL, saving_type VARCHAR, baseline_amount_eur DECIMAL(12,2), {_T});""",
}

SILVER_DDL: dict[str, str] = {
    "silver.ledger_lines": """CREATE TABLE IF NOT EXISTS silver.ledger_lines (
  line_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, line_type VARCHAR NOT NULL, line_class VARCHAR NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, event_date DATE NOT NULL, period_month DATE NOT NULL,
  source_system VARCHAR NOT NULL, source_table VARCHAR NOT NULL, source_ref VARCHAR NOT NULL, delivery_id VARCHAR,
  allocation_basis VARCHAR NOT NULL, is_estimate BOOLEAN NOT NULL, assumption_key VARCHAR, assumption_owner VARCHAR,
  counterparty VARCHAR, counterparty_role VARCHAR, contract_ref VARCHAR, as_of DATE NOT NULL, is_synthetic BOOLEAN NOT NULL,
  UNIQUE (serial, line_type, source_system, source_ref));""",
    "silver.serial_timeline": """CREATE TABLE IF NOT EXISTS silver.serial_timeline (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, ordered_at DATE, received_at DATE, staged_at DATE, shipped_at DATE,
  returned_at DATE, wiped_at DATE, graded_at DATE, sellable_at DATE, sold_at DATE, credited_at DATE,
  lifecycle_status VARCHAR NOT NULL, steps_expected INTEGER NOT NULL, steps_present INTEGER NOT NULL,
  missing_steps VARCHAR, first_missing_step VARCHAR, is_monotonic BOOLEAN NOT NULL, non_monotonic_pair VARCHAR,
  chain_complete BOOLEAN NOT NULL, days_order_to_receipt INTEGER, days_receipt_to_ship INTEGER,
  days_return_to_sellable INTEGER, days_sellable_to_sold INTEGER, days_sold_to_credited INTEGER, days_return_to_cash INTEGER,
  source_refs_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL);""",
    "silver.device_ledger": """CREATE TABLE IF NOT EXISTS silver.device_ledger (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL,
  slug VARCHAR, model_name VARCHAR, oem VARCHAR, catalogue_family VARCHAR, model_family VARCHAR, series VARCHAR,
  variant_spec VARCHAR, storage_gb INTEGER, rrp_gross_eur DECIMAL(12,2), rrp_net_eur DECIMAL(12,2), launch_date DATE,
  months_since_launch_at_as_of DOUBLE,
  po_number VARCHAR, po_line INTEGER, supplier_id VARCHAR, supplier_name VARCHAR, supplier_role VARCHAR, contract_ref VARCHAR,
  order_date DATE, received_at DATE, purchase_date DATE, cohort_month DATE, cohort_quarter VARCHAR,
  price_protection_days INTEGER, price_protection_status VARCHAR, price_protection_claimable_eur DECIMAL(12,2),
  purchase_price DECIMAL(12,2), discount_vs_rrp_eur DECIMAL(12,2), discount_vs_rrp_pct DOUBLE, freight_eur DECIMAL(12,2),
  duty_eur DECIMAL(12,2), landed_cost DECIMAL(12,2), landed_vs_rrp_pct DOUBLE, price_protection_credit_eur DECIMAL(12,2),
  staging_eur DECIMAL(12,2), outbound_shipping_eur DECIMAL(12,2), repair_eur DECIMAL(12,2), replacement_logistics_eur DECIMAL(12,2),
  return_logistics_eur DECIMAL(12,2), wipe_grading_eur DECIMAL(12,2), refurb_eur DECIMAL(12,2), holding_cost_eur DECIMAL(12,2),
  support_eur DECIMAL(12,2), mdm_eur DECIMAL(12,2),
  channel_fee_eur DECIMAL(12,2), days_in_stock_to_date INTEGER, tco_excl_landed_eur DECIMAL(12,2), tco_transactional_eur DECIMAL(12,2),
  tco_eur DECIMAL(12,2), n_lines INTEGER NOT NULL, n_estimate_lines INTEGER NOT NULL,
  first_contract_id VARCHAR, customer_id VARCHAR, term_months INTEGER, monthly_rate DECIMAL(12,2), contract_start DATE,
  contract_end_planned DATE, contract_end_effective DATE, months_billed INTEGER, months_remaining INTEGER,
  rental_revenue DECIMAL(12,2), remaining_contracted_rent DECIMAL(12,2),
  return_date DATE, grade_declared VARCHAR, grade_inspected VARCHAR, wipe_certificate_id VARCHAR, grade_out VARCHAR,
  refurb_outcome VARCHAR, sellable_date DATE, resale_channel VARCHAR, listed_at DATE, sale_date DATE, credited_at DATE,
  resale_gross DECIMAL(12,2), resale_net DECIMAL(12,2), days_return_to_sale INTEGER, days_return_to_cash INTEGER,
  estimate_run_id VARCHAR, grade_used VARCHAR, grade_source VARCHAR, estimate_rv_today DECIMAL(12,2),
  estimate_rv_lease_end DECIMAL(12,2), estimate_months_at_lease_end INTEGER, estimate_rv_source VARCHAR, estimate_fit_quality VARCHAR,
  estimate_rv_of_record DECIMAL(12,2), anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, anchor_rv_lease_end DECIMAL(12,2),
  estimate_vs_anchor_ratio DOUBLE, realised_vs_record_ratio DOUBLE,
  realised_rv DECIMAL(12,2), lifecycle_result_eur DECIMAL(12,2), result_v01_basis_eur DECIMAL(12,2), result_pct_of_landed DOUBLE,
  result_if_liquidated_today DECIMAL(12,2), result_projected_at_lease_end DECIMAL(12,2), projected_label VARCHAR,
  expected_remaining_cost DECIMAL(12,2), expected_cost_inputs_source VARCHAR,
  lifecycle_status VARCHAR NOT NULL, is_closed BOOLEAN NOT NULL, closed_date DATE, chain_complete BOOLEAN, is_synthetic BOOLEAN NOT NULL);""",
    "silver.reconciliation": """CREATE TABLE IF NOT EXISTS silver.reconciliation (
  serial VARCHAR NOT NULL, as_of DATE NOT NULL, field VARCHAR NOT NULL, device_pnl_value DECIMAL(12,2), ledger_value DECIMAL(12,2),
  diff DECIMAL(12,2), ok BOOLEAN NOT NULL, PRIMARY KEY (serial, field));""",
    "silver.contracts": """CREATE TABLE IF NOT EXISTS silver.contracts (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, counterparty_is_public BOOLEAN NOT NULL,
  category VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, notice_days INTEGER NOT NULL, notice_deadline DATE NOT NULL,
  auto_renewal BOOLEAN NOT NULL, price_protection BOOLEAN NOT NULL, price_protection_days INTEGER, claim_window_days INTEGER,
  warranty_months INTEGER, rebate_tiers_json VARCHAR, volume_commitment_units INTEGER, payment_terms_days INTEGER NOT NULL, sla_json VARCHAR,
  spend_under_contract_eur DECIMAL(14,2) NOT NULL, spend_actual_12m_eur DECIMAL(14,2), spend_actual_vs_planned_pct DOUBLE,
  covers_oems VARCHAR, n_serials_under_contract INTEGER, status VARCHAR NOT NULL, days_to_notice_deadline INTEGER, days_to_end INTEGER,
  action_required BOOLEAN NOT NULL, terms_note VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL, as_of DATE NOT NULL);""",
}

GOLD_DDL: dict[str, str] = {
    "gold.ingest_summary": """CREATE TABLE IF NOT EXISTS gold.ingest_summary (
  feed VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, delivering_system VARCHAR NOT NULL, n_files INTEGER NOT NULL,
  last_delivered_on DATE, rows_read INTEGER NOT NULL, rows_new INTEGER NOT NULL, duplicates_identical INTEGER NOT NULL,
  duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL, bronze_rows INTEGER NOT NULL, as_of DATE NOT NULL);""",
    "gold.chain_quality": """CREATE TABLE IF NOT EXISTS gold.chain_quality (
  lifecycle_status VARCHAR NOT NULL, step VARCHAR NOT NULL, n_serials INTEGER NOT NULL, n_present INTEGER NOT NULL,
  share_present DOUBLE, is_expected BOOLEAN NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (lifecycle_status, step));""",
    "gold.purchase_by_oem_month": """CREATE TABLE IF NOT EXISTS gold.purchase_by_oem_month (
  oem VARCHAR NOT NULL, purchase_month DATE NOT NULL, supplier_role VARCHAR NOT NULL, n_units INTEGER NOT NULL,
  sum_rrp_net DECIMAL(14,2), sum_unit_price DECIMAL(14,2), sum_freight_duty DECIMAL(14,2), sum_landed DECIMAL(14,2),
  discount_vs_rrp_pct DOUBLE, landed_vs_rrp_pct DOUBLE, ppv_vs_po_eur DECIMAL(14,2), share_under_contract DOUBLE,
  pp_claimable_eur DECIMAL(14,2), pp_credited_eur DECIMAL(14,2), as_of DATE NOT NULL, PRIMARY KEY (oem, purchase_month, supplier_role));""",
    "gold.tco_by_cohort": """CREATE TABLE IF NOT EXISTS gold.tco_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, line_type VARCHAR NOT NULL, n_devices INTEGER NOT NULL,
  mean_eur DECIMAL(12,2), sum_eur DECIMAL(14,2), estimate_eur DECIMAL(14,2), is_estimate BOOLEAN NOT NULL, as_of DATE NOT NULL,
  PRIMARY KEY (cohort_kind, cohort_value, line_type));""",
    "gold.estimate_vs_anchor": """CREATE TABLE IF NOT EXISTS gold.estimate_vs_anchor (
  catalogue_family VARCHAR NOT NULL, oem VARCHAR NOT NULL, n_rented INTEGER NOT NULL, n_with_anchor INTEGER NOT NULL,
  sum_estimate_lease_end DECIMAL(14,2), sum_anchor_lease_end DECIMAL(14,2), mean_estimate_ratio DOUBLE, mean_anchor_ratio DOUBLE,
  estimate_vs_anchor_ratio DOUBLE, anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, as_of DATE NOT NULL,
  PRIMARY KEY (catalogue_family, oem));""",
    "gold.resale_by_channel_grade": """CREATE TABLE IF NOT EXISTS gold.resale_by_channel_grade (
  channel VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL, n INTEGER NOT NULL, sum_gross DECIMAL(14,2), sum_fees DECIMAL(14,2),
  sum_net DECIMAL(14,2), sum_refurb DECIMAL(14,2), sum_estimate_of_record DECIMAL(14,2), realised_vs_record_ratio DOUBLE,
  median_days_return_to_cash DOUBLE, n_credit_note_missing INTEGER NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (channel, grade_at_sale));""",
    "gold.result_by_cohort": """CREATE TABLE IF NOT EXISTS gold.result_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, n INTEGER NOT NULL, n_closed INTEGER NOT NULL, n_open INTEGER NOT NULL,
  sum_result_closed DECIMAL(14,2), mean_result_closed DECIMAL(12,2), result_pct_of_landed_closed DOUBLE,
  sum_rental_revenue_closed DECIMAL(14,2), sum_realised_rv_closed DECIMAL(14,2), sum_pp_credit_closed DECIMAL(14,2),
  sum_tco_closed DECIMAL(14,2), sum_landed_closed DECIMAL(14,2), tco_per_closed_device DECIMAL(12,2), rv_per_closed_device DECIMAL(12,2),
  rent_per_closed_device DECIMAL(12,2), sum_liquidation_today_open DECIMAL(14,2), mean_liquidation_today_open DECIMAL(12,2),
  sum_projected_lease_end_open DECIMAL(14,2), mean_projected_lease_end_open DECIMAL(12,2), as_of DATE NOT NULL,
  PRIMARY KEY (cohort_kind, cohort_value));""",
    "gold.levers_per_device": """CREATE TABLE IF NOT EXISTS gold.levers_per_device (
  serial VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, delta_eur DECIMAL(12,2), actual_value DOUBLE, reference_value DOUBLE,
  reference_source VARCHAR NOT NULL, n_reference INTEGER NOT NULL, is_attributed BOOLEAN NOT NULL, additive BOOLEAN NOT NULL,
  basis VARCHAR NOT NULL, event_date DATE, counterfactual_json VARCHAR NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (serial, lever_id));""",
    "gold.levers_by_cohort": """CREATE TABLE IF NOT EXISTS gold.levers_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, n_attributed INTEGER NOT NULL,
  sum_delta_eur DECIMAL(14,2), mean_delta_eur DECIMAL(12,2), as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value, lever_id));""",
    "gold.levers_summary": """CREATE TABLE IF NOT EXISTS gold.levers_summary (
  lever_id VARCHAR PRIMARY KEY, lever_name VARCHAR NOT NULL, component VARCHAR NOT NULL, basis VARCHAR NOT NULL, additive BOOLEAN NOT NULL,
  n_eligible INTEGER NOT NULL, n_attributed INTEGER NOT NULL, eur_per_device DECIMAL(12,2), eur_per_device_p90 DECIMAL(12,2),
  eur_fleet_per_year DECIMAL(14,2), share_of_lever_basis DOUBLE, lever_basis_eur DECIMAL(14,2), lever_basis VARCHAR NOT NULL,
  threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL,
  threshold_unit VARCHAR NOT NULL, threshold_owner VARCHAR NOT NULL, rule_id VARCHAR NOT NULL, reference_key VARCHAR NOT NULL,
  reference_owner VARCHAR NOT NULL, reference_sentence VARCHAR NOT NULL,
  rank INTEGER NOT NULL, as_of DATE NOT NULL);""",
    "gold.contract_coverage_by_oem": """CREATE TABLE IF NOT EXISTS gold.contract_coverage_by_oem (
  oem VARCHAR PRIMARY KEY, n_units INTEGER NOT NULL, spend_total DECIMAL(14,2), spend_under_contract DECIMAL(14,2), coverage_pct DOUBLE,
  spend_direct DECIMAL(14,2), spend_via_reseller DECIMAL(14,2), n_contracts_in_force INTEGER NOT NULL, next_notice_deadline DATE, as_of DATE NOT NULL);""",
    "gold.renewal_calendar_v2": """CREATE TABLE IF NOT EXISTS gold.renewal_calendar_v2 (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, category VARCHAR NOT NULL,
  end_date DATE, notice_days INTEGER, notice_deadline DATE, days_to_notice_deadline INTEGER, days_to_end INTEGER, auto_renewal BOOLEAN,
  spend_under_contract_eur DECIMAL(14,2), spend_actual_12m_eur DECIMAL(14,2), price_protection_days INTEGER, claim_window_days INTEGER,
  price_protection_window_open BOOLEAN NOT NULL, action_required BOOLEAN NOT NULL, month_bucket VARCHAR, as_of DATE NOT NULL);""",
    "gold.rebate_progress": """CREATE TABLE IF NOT EXISTS gold.rebate_progress (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, spend_12m_eur DECIMAL(14,2), current_tier_pct DOUBLE,
  next_tier_from_eur DECIMAL(14,2), next_tier_pct DOUBLE, gap_to_next_tier_eur DECIMAL(14,2), as_of DATE NOT NULL);""",
    "gold.kpi_values": """CREATE TABLE IF NOT EXISTS gold.kpi_values (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, name VARCHAR NOT NULL, page VARCHAR NOT NULL, value DOUBLE, numerator DOUBLE,
  denominator DOUBLE, n INTEGER NOT NULL, unit VARCHAR NOT NULL, status VARCHAR NOT NULL, note VARCHAR, target DOUBLE, direction VARCHAR,
  formula_text VARCHAR, source_tables VARCHAR, owner VARCHAR, run_id VARCHAR, PRIMARY KEY (kpi_id, as_of));""",
    "gold.kpi_breakdown": """CREATE TABLE IF NOT EXISTS gold.kpi_breakdown (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, dimension VARCHAR NOT NULL, dimension_value VARCHAR NOT NULL, value DOUBLE,
  numerator DOUBLE, denominator DOUBLE, n INTEGER, PRIMARY KEY (kpi_id, as_of, dimension, dimension_value));""",
}

LAKE_DDL: dict[str, str] = {**BRONZE_DDL, **SILVER_DDL, **GOLD_DDL}  # keyed by qualified name
LAKE_TABLE_ORDER: list[str] = list(LAKE_DDL)


def qualified(name: str) -> str:
    """``'bronze.x'`` -> ``'"bronze"."x"'``, ``'x'`` -> ``'"x"'`` (same as ``db._q``)."""
    if "." in name:
        schema_name, table = name.split(".", 1)
        return f'"{schema_name}"."{table}"'
    return f'"{name}"'


def create_lake_schema(con: duckdb.DuckDBPyConnection, drop_layers: tuple[str, ...] = ("silver", "gold")) -> None:
    """``CREATE SCHEMA IF NOT EXISTS`` for the three layers, drop the tables of ``drop_layers``, create every table.

    Bronze is never in ``drop_layers`` by default: it is the append-only landing
    layer, silver and gold are rebuilt on every run.
    """

    for schema_name in LAKE_SCHEMAS:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
    for name in LAKE_TABLE_ORDER:
        layer = name.split(".", 1)[0]
        if layer in drop_layers:
            con.execute(f"DROP TABLE IF EXISTS {qualified(name)}")
    for name in LAKE_TABLE_ORDER:
        con.execute(LAKE_DDL[name])


def render_lake_ddl_md() -> str:
    """Markdown rendering of every lake table with its DDL (used by ``feeds.write_data_lake_md``)."""

    out: list[str] = []
    for layer, ddl in (("bronze", BRONZE_DDL), ("silver", SILVER_DDL), ("gold", GOLD_DDL)):
        out.append(f"### Layer `{layer}`")
        out.append("")
        for name, statement in ddl.items():
            out.append(f"#### `{name}`")
            out.append("")
            out.append("```sql")
            out.append(statement.strip())
            out.append("```")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


__all__ = [
    "LAKE_SCHEMAS",
    "BRONZE_TAIL",
    "BRONZE_DDL",
    "SILVER_DDL",
    "GOLD_DDL",
    "LAKE_DDL",
    "LAKE_TABLE_ORDER",
    "qualified",
    "create_lake_schema",
    "render_lake_ddl_md",
]
