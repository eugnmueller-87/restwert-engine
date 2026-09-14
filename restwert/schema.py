"""DuckDB schema, row models and the DATA_MODEL.md renderer (SPEC.md sections 2.7 and 2.8).

The DDL strings are the single source of truth for table shapes. ``validate_frame``
checks a pandas frame row by row against a pydantic model whose field names are
the column names; enum columns are validated against ``restwert.enums``.

Run ``python -m restwert.schema`` to regenerate ``docs/DATA_MODEL.md``.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from restwert import GOVERNANCE_PRINCIPLE, __version__
from restwert.enums import (
    BuyerType,
    Channel,
    ChannelIn,
    ContractStatus,
    DamageType,
    EventType,
    Family,
    Grade,
    RefurbOutcome,
    SavingType,
    SpendCategory,
)
from restwert.paths import DOCS_DIR

# --------------------------------------------------------------------------- table lists

SOURCE_TABLES: tuple[str, ...] = (
    "model_catalogue",
    "devices",
    "rental_contracts",
    "events",
    "refurbishment",
    "resale",
    "supplier_contracts",
    "purchase_orders",
    "indirect_spend",
    "benchmarks",
)

DERIVED_TABLES: tuple[str, ...] = (
    "runs",
    "device_pnl",
    "tco_per_model",
    "pnl_aggregate",
    "forecast_runs",
    "rv_forecast_grid",
    "rv_forecast_current",
    "rv_forecast_of_record",
    "rv_forecast_error_monthly",
    "backtest_result",
    "advisories",
    "decision_log",
    "decision_queue",
    "write_down_ledger",
    "kpi_values",
    "kpi_breakdown",
    "contracts_register",
    "renewal_calendar",
)

IMMUTABLE_TABLES: tuple[str, ...] = ("forecast_runs", "decision_log", "write_down_ledger", "runs")

TABLE_ORDER: list[str] = list(SOURCE_TABLES) + list(DERIVED_TABLES)

# v0.2: the ten source tables are conformed from the bronze layer of the data lake (SPEC_v0.2.md section 3.7).
SCHEMA_LAYER_NOTE = (
    "v0.2: the ten source tables are conformed from the bronze layer of the data lake; "
    "see docs/DATA_LAKE.md for feeds, keys and the silver ledger."
)

# --------------------------------------------------------------------------- DDL (section 2.8 verbatim)

DDL: dict[str, str] = {
    "model_catalogue": """CREATE TABLE IF NOT EXISTS model_catalogue (
  model VARCHAR PRIMARY KEY, model_family VARCHAR NOT NULL, generation INTEGER NOT NULL,
  launch_date DATE NOT NULL, list_price DECIMAL(12,2) NOT NULL, base_storage_gb INTEGER NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "devices": """CREATE TABLE IF NOT EXISTS devices (
  serial VARCHAR PRIMARY KEY, model_family VARCHAR NOT NULL, model VARCHAR NOT NULL,
  storage_gb INTEGER NOT NULL, colour VARCHAR, launch_date DATE NOT NULL, purchase_date DATE NOT NULL,
  purchase_price DECIMAL(12,2) NOT NULL, landed_cost DECIMAL(12,2) NOT NULL,
  supplier VARCHAR NOT NULL, channel_in VARCHAR NOT NULL, po_number VARCHAR NOT NULL, contract_id VARCHAR,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "rental_contracts": """CREATE TABLE IF NOT EXISTS rental_contracts (
  contract_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, customer_id VARCHAR NOT NULL,
  start_date DATE NOT NULL, term_months INTEGER NOT NULL, monthly_rate DECIMAL(12,2) NOT NULL,
  end_date DATE NOT NULL, actual_end_date DATE, status VARCHAR NOT NULL, replaces_contract_id VARCHAR,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "events": """CREATE TABLE IF NOT EXISTS events (
  event_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, event_type VARCHAR NOT NULL,
  event_date DATE NOT NULL, cost DECIMAL(12,2) NOT NULL DEFAULT 0, damage_type VARCHAR,
  resolved BOOLEAN,
  replacement_serial VARCHAR,
  return_date DATE, grade_pre_return VARCHAR, grade_inspected VARCHAR, wipe_certificate BOOLEAN,
  note VARCHAR, is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "refurbishment": """CREATE TABLE IF NOT EXISTS refurbishment (
  refurb_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  days INTEGER NOT NULL, cost DECIMAL(12,2) NOT NULL, grade_out VARCHAR NOT NULL, outcome VARCHAR NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "resale": """CREATE TABLE IF NOT EXISTS resale (
  sale_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL, sale_date DATE NOT NULL,
  price DECIMAL(12,2) NOT NULL, fees DECIMAL(12,2) NOT NULL DEFAULT 0, buyer_type VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "supplier_contracts": """CREATE TABLE IF NOT EXISTS supplier_contracts (
  supplier_contract_id VARCHAR PRIMARY KEY, supplier VARCHAR NOT NULL, category VARCHAR NOT NULL,
  start_date DATE NOT NULL, end_date DATE NOT NULL, auto_renewal BOOLEAN NOT NULL, notice_days INTEGER NOT NULL,
  price_protection BOOLEAN NOT NULL, price_protection_days INTEGER, payment_terms_days INTEGER NOT NULL,
  spend_under_contract DECIMAL(12,2) NOT NULL, is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "purchase_orders": """CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number VARCHAR PRIMARY KEY, supplier VARCHAR NOT NULL, supplier_contract_id VARCHAR, model VARCHAR,
  order_date DATE NOT NULL, promised_date DATE NOT NULL, delivered_date DATE,
  qty_ordered INTEGER NOT NULL, qty_delivered INTEGER NOT NULL, unit_price DECIMAL(12,2) NOT NULL,
  benchmark_price DECIMAL(12,2), price_drop_date DATE, price_drop_amount DECIMAL(12,2),
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "indirect_spend": """CREATE TABLE IF NOT EXISTS indirect_spend (
  spend_id VARCHAR PRIMARY KEY, invoice_date DATE NOT NULL, category VARCHAR NOT NULL, supplier VARCHAR NOT NULL,
  amount DECIMAL(12,2) NOT NULL, has_po BOOLEAN NOT NULL, has_contract BOOLEAN NOT NULL,
  saving DECIMAL(12,2) NOT NULL DEFAULT 0, saving_confirmed_by_controlling BOOLEAN NOT NULL DEFAULT false,
  saving_type VARCHAR, baseline_amount DECIMAL(12,2),
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR);""",
    "benchmarks": """CREATE TABLE IF NOT EXISTS benchmarks (
  model VARCHAR NOT NULL, valid_from DATE NOT NULL, landed_cost_benchmark DECIMAL(12,2) NOT NULL,
  source_note VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL, source_file VARCHAR,
  PRIMARY KEY (model, valid_from));""",
    "runs": """CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, command VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP,
  seed INTEGER, as_of DATE NOT NULL, thresholds_sha256 VARCHAR, counts_json VARCHAR);""",
    "device_pnl": """CREATE TABLE IF NOT EXISTS device_pnl (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, model_family VARCHAR, model VARCHAR, storage_gb INTEGER,
  purchase_date DATE, cohort VARCHAR, launch_date DATE, months_since_launch DOUBLE,
  landed_cost DECIMAL(12,2), purchase_price DECIMAL(12,2),
  months_billed INTEGER, rental_revenue DECIMAL(12,2),
  return_date DATE, grade_inspected VARCHAR, sellable_date DATE, refurb_outcome VARCHAR, grade_current VARCHAR,
  resale_channel VARCHAR, sale_date DATE, resale_price_gross DECIMAL(12,2), resale_fees DECIMAL(12,2),
  realised_rv DECIMAL(12,2),
  forecast_rv DECIMAL(12,2),
  repair_cost DECIMAL(12,2), replacement_logistics_cost DECIMAL(12,2), return_logistics_cost DECIMAL(12,2),
  refurb_cost DECIMAL(12,2), channel_fees DECIMAL(12,2), service_and_logistics_cost DECIMAL(12,2),
  lifecycle_margin DECIMAL(12,2),
  lifecycle_margin_pct_of_landed DOUBLE,
  margin_if_liquidated_today DECIMAL(12,2),
  lifecycle_status VARCHAR NOT NULL, is_closed BOOLEAN NOT NULL, closed_date DATE,
  days_in_stock INTEGER, days_in_wip INTEGER,
  book_value_sl DECIMAL(12,2), write_down_cum DECIMAL(12,2), book_value DECIMAL(12,2));""",
    "tco_per_model": """CREATE TABLE IF NOT EXISTS tco_per_model (
  model VARCHAR NOT NULL, term_months INTEGER NOT NULL, as_of DATE NOT NULL, model_family VARCHAR,
  n_devices INTEGER, landed_cost_avg DECIMAL(12,2), purchase_price_avg DECIMAL(12,2),
  months_since_launch_at_purchase_avg DOUBLE, grade_assumed VARCHAR, forecast_rv_ratio_at_end DOUBLE, forecast_rv_at_end DECIMAL(12,2),
  expected_repair_cost DECIMAL(12,2), expected_refurb_cost DECIMAL(12,2), expected_logistics_cost DECIMAL(12,2), expected_channel_fees DECIMAL(12,2),
  tco DECIMAL(12,2), tco_per_month DECIMAL(12,2), monthly_rate_avg DECIMAL(12,2), gap_rate_minus_tco_per_month DECIMAL(12,2),
  inputs_source VARCHAR, forecast_rv_source VARCHAR, rv_months_at_end INTEGER, rv_months_used INTEGER,
  rv_clipped_to_grid BOOLEAN,
  PRIMARY KEY (model, term_months));""",
    "pnl_aggregate": """CREATE TABLE IF NOT EXISTS pnl_aggregate (
  group_by VARCHAR NOT NULL, group_value VARCHAR NOT NULL, as_of DATE NOT NULL, n INTEGER, n_closed INTEGER,
  sum_rental_revenue DECIMAL(14,2), sum_landed_cost DECIMAL(14,2), sum_realised_rv DECIMAL(14,2),
  sum_service_and_logistics_cost DECIMAL(14,2),
  sum_rental_revenue_closed DECIMAL(14,2), sum_landed_cost_closed DECIMAL(14,2), sum_realised_rv_closed DECIMAL(14,2),
  sum_service_and_logistics_cost_closed DECIMAL(14,2),
  sum_lifecycle_margin DECIMAL(14,2), mean_lifecycle_margin DECIMAL(12,2),
  margin_pct DOUBLE, PRIMARY KEY (group_by, group_value));""",
    "forecast_runs": """CREATE TABLE IF NOT EXISTS forecast_runs (
  run_id VARCHAR PRIMARY KEY, as_of DATE NOT NULL, method VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
  n_train_total INTEGER NOT NULL, n_train_json VARCHAR NOT NULL, fit_quality_json VARCHAR NOT NULL,
  coefficients_json VARCHAR NOT NULL, sigma_log_json VARCHAR NOT NULL, as_is_ratio_json VARCHAR NOT NULL,
  feature_names_json VARCHAR NOT NULL, unsupported_json VARCHAR);""",
    "rv_forecast_grid": """CREATE TABLE IF NOT EXISTS rv_forecast_grid (
  run_id VARCHAR NOT NULL, model VARCHAR NOT NULL, model_family VARCHAR NOT NULL, grade VARCHAR NOT NULL,
  months_since_launch INTEGER NOT NULL, n_launches_since INTEGER NOT NULL, storage_gb INTEGER NOT NULL,
  forecast_rv_ratio DOUBLE NOT NULL, ratio_low DOUBLE, ratio_high DOUBLE,
  list_price DECIMAL(12,2), forecast_rv_on_list DECIMAL(12,2),
  n_train INTEGER, fit_quality VARCHAR,
  PRIMARY KEY (model, grade, months_since_launch));""",
    "rv_forecast_current": """CREATE TABLE IF NOT EXISTS rv_forecast_current (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, run_id VARCHAR NOT NULL, model VARCHAR, model_family VARCHAR,
  grade_used VARCHAR NOT NULL, grade_source VARCHAR NOT NULL,
  months_since_launch DOUBLE, n_launches_since INTEGER,
  forecast_rv_ratio DOUBLE, forecast_rv DECIMAL(12,2),
  forecast_rv_employee_buyout DECIMAL(12,2), forecast_rv_b2b_wholesale DECIMAL(12,2), forecast_rv_as_is DECIMAL(12,2),
  forecast_rv_grade_b DECIMAL(12,2),
  fit_quality VARCHAR, n_train INTEGER);""",
    "rv_forecast_of_record": """CREATE TABLE IF NOT EXISTS rv_forecast_of_record (
  serial VARCHAR PRIMARY KEY, return_date DATE NOT NULL, run_id VARCHAR, run_as_of DATE,
  grade_used VARCHAR, target_date DATE, months_since_launch DOUBLE, n_launches_since INTEGER,
  forecast_rv_ratio DOUBLE, forecast_rv DECIMAL(12,2),
  channel_factor_employee_buyout DOUBLE, channel_factor_b2b_wholesale DOUBLE,
  is_missing BOOLEAN NOT NULL, missing_reason VARCHAR);""",
    "rv_forecast_error_monthly": """CREATE TABLE IF NOT EXISTS rv_forecast_error_monthly (
  month DATE NOT NULL, model_family VARCHAR NOT NULL,
  n_sales INTEGER NOT NULL, n_with_forecast INTEGER NOT NULL, n_excluded_as_is INTEGER NOT NULL,
  mape DOUBLE, bias DOUBLE, wape DOUBLE, mae_eur DOUBLE, realisation_ratio DOUBLE,
  sum_realised DECIMAL(14,2), sum_forecast DECIMAL(14,2),
  mape_channel_adjusted DOUBLE, bias_channel_adjusted DOUBLE, sum_forecast_channel_adjusted DECIMAL(14,2),
  n_marketplace INTEGER, mape_marketplace DOUBLE, bias_marketplace DOUBLE,
  run_ids_used VARCHAR,
  PRIMARY KEY (month, model_family));""",
    "backtest_result": """CREATE TABLE IF NOT EXISTS backtest_result (
  backtest_id VARCHAR NOT NULL, run_at TIMESTAMP NOT NULL, cutoff DATE NOT NULL, model_family VARCHAR NOT NULL,
  n_train INTEGER, n_test INTEGER, mape DOUBLE, bias DOUBLE, wape DOUBLE, mae_eur DOUBLE, rmse_log DOUBLE,
  train_max_sale_date DATE, test_min_sale_date DATE, PRIMARY KEY (backtest_id, model_family));""",
    "advisories": """CREATE TABLE IF NOT EXISTS advisories (
  advisory_id VARCHAR PRIMARY KEY, as_of DATE NOT NULL, run_id VARCHAR, kind VARCHAR NOT NULL,
  subject_type VARCHAR NOT NULL, subject_id VARCHAR NOT NULL, confidence VARCHAR NOT NULL,
  payload_json VARCHAR NOT NULL, note VARCHAR, threshold_key VARCHAR, threshold_owner VARCHAR);""",
    "decision_log": """CREATE TABLE IF NOT EXISTS decision_log (
  decision_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, decided_at TIMESTAMP NOT NULL, as_of DATE NOT NULL,
  rule_id VARCHAR NOT NULL, rule_version VARCHAR NOT NULL, subject_type VARCHAR NOT NULL, subject_id VARCHAR NOT NULL,
  outcome VARCHAR NOT NULL, outcome_detail VARCHAR, threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL,
  threshold_value_num DOUBLE, threshold_unit VARCHAR, threshold_owner VARCHAR NOT NULL, threshold_valid_from DATE,
  inputs_json VARCHAR NOT NULL, advisory_json VARCHAR, value_at_stake_eur DECIMAL(12,2), due_date DATE,
  input_hash VARCHAR NOT NULL UNIQUE, is_synthetic_input BOOLEAN NOT NULL);""",
    "decision_queue": """CREATE TABLE IF NOT EXISTS decision_queue (
  decision_id VARCHAR PRIMARY KEY, run_id VARCHAR, as_of DATE, priority INTEGER NOT NULL, rule_id VARCHAR, rule_name VARCHAR,
  outcome VARCHAR, subject_type VARCHAR, subject_id VARCHAR, explanation VARCHAR, threshold_key VARCHAR,
  threshold_value VARCHAR, threshold_owner VARCHAR, due_date DATE, value_at_stake_eur DECIMAL(12,2),
  advisory_kind VARCHAR, advisory_confidence VARCHAR, advisory_json VARCHAR);""",
    "write_down_ledger": """CREATE TABLE IF NOT EXISTS write_down_ledger (
  write_down_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, as_of DATE NOT NULL, rule_id VARCHAR NOT NULL,
  decision_id VARCHAR NOT NULL, book_value_before DECIMAL(12,2) NOT NULL, amount DECIMAL(12,2) NOT NULL,
  book_value_after DECIMAL(12,2) NOT NULL, threshold_owner VARCHAR NOT NULL, run_id VARCHAR NOT NULL,
  UNIQUE (serial, rule_id, as_of));""",
    "kpi_values": """CREATE TABLE IF NOT EXISTS kpi_values (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, name VARCHAR NOT NULL, area VARCHAR NOT NULL,
  value DOUBLE, numerator DOUBLE, denominator DOUBLE, n INTEGER NOT NULL, unit VARCHAR NOT NULL,
  status VARCHAR NOT NULL, note VARCHAR, target DOUBLE, direction VARCHAR, formula_text VARCHAR, source_tables VARCHAR,
  run_id VARCHAR, PRIMARY KEY (kpi_id, as_of));""",
    "kpi_breakdown": """CREATE TABLE IF NOT EXISTS kpi_breakdown (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, dimension VARCHAR NOT NULL, dimension_value VARCHAR NOT NULL,
  value DOUBLE, numerator DOUBLE, denominator DOUBLE, n INTEGER, PRIMARY KEY (kpi_id, as_of, dimension, dimension_value));""",
    "contracts_register": """CREATE TABLE IF NOT EXISTS contracts_register (
  contract_type VARCHAR NOT NULL, contract_id VARCHAR NOT NULL, counterparty VARCHAR NOT NULL, category VARCHAR,
  start_date DATE, end_date DATE, notice_days INTEGER, notice_deadline DATE, auto_renewal BOOLEAN,
  price_protection BOOLEAN, price_protection_days INTEGER, payment_terms_days INTEGER,
  annual_value DECIMAL(12,2), status VARCHAR, PRIMARY KEY (contract_type, contract_id));""",
    "renewal_calendar": """CREATE TABLE IF NOT EXISTS renewal_calendar (
  contract_type VARCHAR NOT NULL, contract_id VARCHAR NOT NULL, counterparty VARCHAR, as_of DATE NOT NULL,
  end_date DATE, notice_days INTEGER, notice_deadline DATE, days_to_notice_deadline INTEGER, days_to_end INTEGER,
  auto_renewal BOOLEAN, annual_value DECIMAL(12,2), action_required BOOLEAN NOT NULL, month_bucket VARCHAR,
  PRIMARY KEY (contract_type, contract_id));""",
}

assert set(DDL) == set(TABLE_ORDER), "DDL and TABLE_ORDER out of sync"

# --------------------------------------------------------------------------- column docs

_COMMON_SOURCE_DOCS = {
    "is_synthetic": "True on every synthetic row; a real CSV must carry the column with false (never defaulted).",
    "source_file": "File the row was loaded from (<table>.csv).",
}

COLUMN_DOCS: dict[str, dict[str, str]] = {
    "model_catalogue": {
        "model": "Model id, e.g. P-Gen05 (prefix P iphone_like, A android_like, L laptop_like).",
        "model_family": "One of iphone_like, android_like, laptop_like.",
        "generation": "Generation counter within the family, 1 = first_launch.",
        "launch_date": "Launch date of the generation; devices.launch_date is a copy.",
        "list_price": "List price in EUR (synthetic band, rounded to end in 9).",
        "base_storage_gb": "Storage of the base variant; storage uplift is relative to it.",
        **_COMMON_SOURCE_DOCS,
    },
    "devices": {
        "serial": "Device serial, primary key (D-000001 style).",
        "model_family": "Family of the model (copy of model_catalogue.model_family).",
        "model": "Model id, must exist in model_catalogue.",
        "storage_gb": "Storage variant in GB.",
        "colour": "Cosmetic colour, informational.",
        "launch_date": "Launch date of the model (copy of model_catalogue.launch_date).",
        "purchase_date": "Date the device was bought; defines the cohort (quarter).",
        "purchase_price": "Net purchase price in EUR (list price minus discount).",
        "landed_cost": "purchase_price plus freight and duty; the cost basis of the P&L.",
        "supplier": "Hardware supplier (Supplier-A .. Supplier-F).",
        "channel_in": "Inbound channel: distributor, oem_direct, refurb_buyback.",
        "po_number": "Purchase order the device was delivered on; must exist in purchase_orders.",
        "contract_id": "First rental contract of the device; later contracts via rental_contracts.serial. NULL for a spare never deployed.",
        **_COMMON_SOURCE_DOCS,
    },
    "rental_contracts": {
        "contract_id": "Contract id, primary key (RC-000001 style).",
        "serial": "Rented device; must exist in devices.",
        "customer_id": "Business customer (CUST-0001 style).",
        "start_date": "Rental start.",
        "term_months": "Contract term in months: 12, 24, 36 or 48 (config.TERM_MONTHS).",
        "monthly_rate": "Monthly rental rate in EUR.",
        "end_date": "Planned end = add_months(start_date, term_months).",
        "actual_end_date": "Set on early termination or replacement; NULL otherwise.",
        "status": "active, ended, terminated_early, replaced.",
        "replaces_contract_id": "For a replacement contract: the contract of the damaged device it replaces.",
        **_COMMON_SOURCE_DOCS,
    },
    "events": {
        "event_id": "Event id, primary key (EV-000001 style).",
        "serial": "Device the event concerns.",
        "contract_id": "Rental contract active at the event, if any.",
        "event_type": "damage, repair, replacement, return.",
        "event_date": "Date of the event.",
        "cost": "repair = repair cost; replacement = outbound+inbound shipping; return = reverse logistics; damage = quote if resolved=false else 0.",
        "damage_type": "damage only: screen, battery, housing, water, other.",
        "resolved": "damage only: false = open quote (cost = quote), true = repaired or replaced.",
        "replacement_serial": "replacement only: the serial shipped to the customer.",
        "return_date": "return only: date the device arrived back.",
        "grade_pre_return": "return only: grade declared before return (A..D).",
        "grade_inspected": "return only: grade after inspection (A..D).",
        "wipe_certificate": "return only: data wipe certified.",
        "note": "Free text.",
        **_COMMON_SOURCE_DOCS,
    },
    "refurbishment": {
        "refurb_id": "Refurbishment id, primary key (RF-000001 style).",
        "serial": "Device refurbished; at most one row per serial in v0.1.",
        "start_date": "Refurbishment start.",
        "end_date": "Refurbishment end = sellable_date.",
        "days": "end_date - start_date in days.",
        "cost": "Refurbishment cost in EUR.",
        "grade_out": "Grade after refurbishment (A..D).",
        "outcome": "sellable, as_is, scrap.",
        **_COMMON_SOURCE_DOCS,
    },
    "resale": {
        "sale_id": "Sale id, primary key (S-000001 style).",
        "serial": "Device sold; at most one row per serial.",
        "channel": "employee_buyout, marketplace, b2b_wholesale, as_is.",
        "sale_date": "Date of sale.",
        "price": "GROSS sale price in EUR before fees; the realised residual value.",
        "fees": "Channel fees in EUR (percentage plus fixed).",
        "buyer_type": "employee, consumer, trader, recycler.",
        "grade_at_sale": "Grade at sale (A..D).",
        **_COMMON_SOURCE_DOCS,
    },
    "supplier_contracts": {
        "supplier_contract_id": "Contract id, primary key (SC-01 style).",
        "supplier": "Counterparty (Supplier-A .. Supplier-L).",
        "category": "Spend category (hardware or an indirect category).",
        "start_date": "Contract start.",
        "end_date": "Contract end.",
        "auto_renewal": "Renews automatically unless notice is given.",
        "notice_days": "Notice period in days before end_date.",
        "price_protection": "Supplier grants price protection on price drops.",
        "price_protection_days": "Claim window in days after a price drop; NULL without price protection.",
        "payment_terms_days": "Payment terms in days.",
        "spend_under_contract": "Annual spend covered by the contract in EUR.",
        **_COMMON_SOURCE_DOCS,
    },
    "purchase_orders": {
        "po_number": "PO number, primary key (PO-000001 style).",
        "supplier": "Hardware supplier.",
        "supplier_contract_id": "Hardware contract in force at order_date, else NULL (spend not under contract).",
        "model": "Model ordered.",
        "order_date": "Order date.",
        "promised_date": "Promised delivery date.",
        "delivered_date": "Actual delivery date; NULL if not delivered.",
        "qty_ordered": "Units ordered.",
        "qty_delivered": "Units delivered (short delivery when below qty_ordered).",
        "unit_price": "Unit price paid in EUR.",
        "benchmark_price": "Reference unit price in EUR (synthetic band midpoint, not a market figure).",
        "price_drop_date": "Date the supplier's price dropped after delivery; NULL if none.",
        "price_drop_amount": "Price drop per unit in EUR; the claim value is amount x qty_delivered.",
        **_COMMON_SOURCE_DOCS,
    },
    "indirect_spend": {
        "spend_id": "Invoice line id, primary key (IS-000001 style).",
        "invoice_date": "Invoice date.",
        "category": "Indirect spend category.",
        "supplier": "Indirect supplier (Supplier-G .. Supplier-L).",
        "amount": "Invoice amount in EUR.",
        "has_po": "A purchase order existed for the invoice.",
        "has_contract": "The supplier had a contract for the category.",
        "saving": "Claimed saving in EUR (0 if none): baseline_amount - amount.",
        "saving_confirmed_by_controlling": "Controlling confirmed the saving; only confirmed savings count against the plan.",
        "saving_type": "hard_price_reduction | cost_avoidance | rebate; NULL when no saving is claimed. Only hard_price_reduction counts in the savings KPI numerator; avoidance and rebates are reported separately.",
        "baseline_amount": "Price before the saving in EUR (what would have been paid); NULL when no saving is claimed.",
        **_COMMON_SOURCE_DOCS,
    },
    "benchmarks": {
        "model": "Model the benchmark applies to.",
        "valid_from": "Benchmark valid from this date; latest row with valid_from <= purchase_date applies.",
        "landed_cost_benchmark": "Reference landed cost in EUR.",
        "source_note": "Where the number comes from; synthetic rows say so explicitly.",
        **_COMMON_SOURCE_DOCS,
    },
    "runs": {
        "run_id": "<command>-<YYYYmmddHHMMSS>-<4hex>.",
        "command": "CLI command that produced the run.",
        "started_at": "Run start (UTC).",
        "finished_at": "Run end (UTC); NULL while running or if aborted.",
        "seed": "Generator seed if the run generated data.",
        "as_of": "Reporting date of the run.",
        "thresholds_sha256": "sha256 of config/thresholds.yaml at run time.",
        "counts_json": "Row counts written per table.",
    },
    "device_pnl": {
        "serial": "Device.",
        "lifecycle_status": "Derived status: not_deployed, rented, awaiting_return, wip, in_stock, sold, scrapped.",
        "realised_rv": "resale.price (gross); 0 if scrapped; NULL if unsold.",
        "forecast_rv": "Marketplace-baseline forecast from rv_forecast_current for unsold devices, valued at as_of (a grade with no training support carries the family As-Is value).",
        "lifecycle_margin": "rental_revenue - (landed_cost - realised_rv) - service_and_logistics_cost; closed lifecycles only.",
        "margin_if_liquidated_today": "Open devices only: rental revenue billed so far - (landed_cost - forecast_rv at as_of) - service cost to date - marketplace fee on forecast_rv. A liquidation-today view, NOT a lifecycle forecast: remaining contracted rent and the residual value at contract end are not included.",
        "book_value_sl": "Straight-line book value at as_of for open devices; 0 once the lifecycle is closed (sold or scrapped), the asset is derecognised.",
        "write_down_cum": "Cumulative R03 write-downs booked before as_of; 0 for closed devices.",
        "book_value": "max(book_value_sl - write_down_cum, 0) for open devices, 0 for closed devices; management view, not IFRS 16 / HGB.",
    },
    "tco_per_model": {
        "tco": "landed_cost_avg - forecast_rv_at_end + expected repair + refurb + logistics + channel fees.",
        "forecast_rv_at_end": "Marketplace-baseline grade forecast at the end of term plus return-to-sale days: grid ratio x purchase_price_avg, or planned_rv_ratio x landed_cost_avg when the grid has no row for the model.",
        "expected_channel_fees": "Marketplace fee share (realised marketplace fees / marketplace price when enough marketplace sales exist, else the assumption) applied to forecast_rv_at_end: same valuation basis as the residual value.",
        "inputs_source": "realised | assumptions | mixed: where the expected-cost inputs came from; a clipped residual value or a planned-ratio fallback makes the row 'mixed' / 'assumptions'.",
        "forecast_rv_source": "grid | grid_clipped | planned_ratio_on_landed_cost: where forecast_rv_ratio_at_end came from.",
        "rv_months_at_end": "Months since launch at which the residual value should be read (months at purchase + term + return-to-sale days).",
        "rv_months_used": "Months since launch the grid was actually read at (equals rv_months_at_end unless clipped).",
        "rv_clipped_to_grid": "True when rv_months_at_end lies outside the grid horizon and the nearest grid month was used; the residual value is then overstated and the row must not be read as realised-input truth.",
    },
    "pnl_aggregate": {
        "sum_rental_revenue": "Sum over ALL devices of the group (open included).",
        "sum_landed_cost": "Sum over ALL devices of the group (open included).",
        "sum_realised_rv": "Sum over ALL devices of the group (NULL for unsold devices counts as 0).",
        "sum_service_and_logistics_cost": "Sum over ALL devices of the group (open included).",
        "sum_rental_revenue_closed": "Closed lifecycles only; the P&L identity holds on the _closed columns: sum_lifecycle_margin = sum_rental_revenue_closed - (sum_landed_cost_closed - sum_realised_rv_closed) - sum_service_and_logistics_cost_closed.",
        "sum_landed_cost_closed": "Closed lifecycles only; denominator of margin_pct.",
        "sum_realised_rv_closed": "Closed lifecycles only.",
        "sum_service_and_logistics_cost_closed": "Closed lifecycles only.",
        "sum_lifecycle_margin": "Closed lifecycles only.",
        "margin_pct": "sum_lifecycle_margin / sum_landed_cost_closed.",
    },
    "forecast_runs": {
        "run_id": "rv-YYYY-MM-DD; one immutable run per month end.",
        "coefficients_json": "Per-family coefficients of the log-linear fit.",
        "unsupported_json": "Per family: design columns with zero variance in the training set (e.g. grade_D when every grade-D sale went As Is). Their coefficient is not identified; a forecast for such a grade returns the family As-Is ratio and is labelled fit_quality 'unsupported_grade'.",
    },
    "rv_forecast_of_record": {
        "forecast_rv": "Marketplace-baseline forecast in force before the return (business view).",
        "channel_factor_employee_buyout": "exp(coef ch_employee_buyout) of the run in force; multiplies forecast_rv into the channel-specific forecast.",
        "channel_factor_b2b_wholesale": "exp(coef ch_b2b_wholesale) of the run in force.",
    },
    "rv_forecast_error_monthly": {
        "model_family": "Family or '*' for all families.",
        "mape": "Business view: mean absolute percentage error of the marketplace-baseline forecast of record vs realised gross price over every non-As-Is channel. Contains channel mix, not only model error.",
        "bias": "Business view: mean (forecast - realised) / realised; positive = forecast too high (collateral overstated). Roughly two thirds of the bias on synthetic data is channel mix.",
        "mape_channel_adjusted": "Model view: MAPE of forecast_rv x channel factor of the channel actually used vs realised price.",
        "bias_channel_adjusted": "Model view: bias of the channel-adjusted forecast; this is what ADV02 (forecast_calibration) tests against its threshold.",
        "sum_forecast_channel_adjusted": "Sum of the channel-adjusted forecasts of the month.",
        "n_marketplace": "Sales through the marketplace channel with a forecast (the baseline's own channel).",
        "mape_marketplace": "MAPE on marketplace sales only (no channel effect at all).",
        "bias_marketplace": "Bias on marketplace sales only.",
    },
    "decision_log": {
        "rule_id": "R01..R06.",
        "threshold_owner": "Named human who owns the threshold that fired.",
        "threshold_valid_from": "valid_from of the threshold version that fired; never after as_of (Thresholds.get refuses it).",
        "inputs_json": "Every input the rule read, including thresholds_consulted (key, value, unit, owner, valid_from of every threshold the rule looked at).",
        "input_hash": "sha256 of rule, subject, as_of and inputs; a rerun with unchanged inputs appends nothing.",
        "is_synthetic_input": "True when the decision was computed on synthetic data.",
    },
    "write_down_ledger": {
        "amount": "Write-down booked by rule R03; one row per (serial, rule_id, as_of).",
    },
    "kpi_values": {
        "status": "ok | not_measurable (denominator 0, column all NULL, or n below min_n).",
    },
}

# --------------------------------------------------------------------------- row models


class _Row(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="ignore", str_strip_whitespace=True)


class ModelCatalogueRow(_Row):
    model: str
    model_family: Family
    generation: int
    launch_date: date
    list_price: float
    base_storage_gb: int
    is_synthetic: bool
    source_file: str | None = None


class DeviceRow(_Row):
    serial: str
    model_family: Family
    model: str
    storage_gb: int
    colour: str | None = None
    launch_date: date
    purchase_date: date
    purchase_price: float
    landed_cost: float
    supplier: str
    channel_in: ChannelIn
    po_number: str
    contract_id: str | None = None
    is_synthetic: bool
    source_file: str | None = None


class RentalContractRow(_Row):
    contract_id: str
    serial: str
    customer_id: str
    start_date: date
    term_months: int
    monthly_rate: float
    end_date: date
    actual_end_date: date | None = None
    status: ContractStatus
    replaces_contract_id: str | None = None
    is_synthetic: bool
    source_file: str | None = None


class EventRow(_Row):
    event_id: str
    serial: str
    contract_id: str | None = None
    event_type: EventType
    event_date: date
    cost: float = 0.0
    damage_type: DamageType | None = None
    resolved: bool | None = None
    replacement_serial: str | None = None
    return_date: date | None = None
    grade_pre_return: Grade | None = None
    grade_inspected: Grade | None = None
    wipe_certificate: bool | None = None
    note: str | None = None
    is_synthetic: bool
    source_file: str | None = None


class RefurbishmentRow(_Row):
    refurb_id: str
    serial: str
    start_date: date
    end_date: date
    days: int
    cost: float
    grade_out: Grade
    outcome: RefurbOutcome
    is_synthetic: bool
    source_file: str | None = None


class ResaleRow(_Row):
    sale_id: str
    serial: str
    channel: Channel
    sale_date: date
    price: float
    fees: float = 0.0
    buyer_type: BuyerType
    grade_at_sale: Grade
    is_synthetic: bool
    source_file: str | None = None


class SupplierContractRow(_Row):
    supplier_contract_id: str
    supplier: str
    category: SpendCategory
    start_date: date
    end_date: date
    auto_renewal: bool
    notice_days: int
    price_protection: bool
    price_protection_days: int | None = None
    payment_terms_days: int
    spend_under_contract: float
    is_synthetic: bool
    source_file: str | None = None


class PurchaseOrderRow(_Row):
    po_number: str
    supplier: str
    supplier_contract_id: str | None = None
    model: str | None = None
    order_date: date
    promised_date: date
    delivered_date: date | None = None
    qty_ordered: int
    qty_delivered: int
    unit_price: float
    benchmark_price: float | None = None
    price_drop_date: date | None = None
    price_drop_amount: float | None = None
    is_synthetic: bool
    source_file: str | None = None


class IndirectSpendRow(_Row):
    spend_id: str
    invoice_date: date
    category: SpendCategory
    supplier: str
    amount: float
    has_po: bool
    has_contract: bool
    saving: float = 0.0
    saving_confirmed_by_controlling: bool = False
    saving_type: SavingType | None = None
    baseline_amount: float | None = None
    is_synthetic: bool
    source_file: str | None = None


class BenchmarkRow(_Row):
    model: str
    valid_from: date
    landed_cost_benchmark: float
    source_note: str
    is_synthetic: bool
    source_file: str | None = None


ROW_MODELS: dict[str, type[BaseModel]] = {
    "model_catalogue": ModelCatalogueRow,
    "devices": DeviceRow,
    "rental_contracts": RentalContractRow,
    "events": EventRow,
    "refurbishment": RefurbishmentRow,
    "resale": ResaleRow,
    "supplier_contracts": SupplierContractRow,
    "purchase_orders": PurchaseOrderRow,
    "indirect_spend": IndirectSpendRow,
    "benchmarks": BenchmarkRow,
}

assert set(ROW_MODELS) == set(SOURCE_TABLES)

# --------------------------------------------------------------------------- validation


def _clean_value(v):
    """Turn pandas missing markers into None and Timestamps into dates."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, datetime):  # pd.Timestamp is a datetime subclass
        if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
            return v.date()
        return v
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _to_records(df: pd.DataFrame) -> list[dict]:
    work = df.astype(object)
    records = work.to_dict("records")
    for rec in records:
        for k, v in rec.items():
            rec[k] = _clean_value(v)
    return records


def validate_frame(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """Validate and coerce a source-table frame; raises ``ValueError`` on the first bad row.

    The message has the form ``"<table> row <i> field <f>: <msg>"`` where ``i`` is
    the positional row index in ``df``. Returns a new frame with exactly the model's
    columns in DDL order and python-native values (dates as ``datetime.date``,
    enums as plain strings, missing values as ``None``).
    """
    if table not in ROW_MODELS:
        raise KeyError(f"{table!r} is not a source table")
    model = ROW_MODELS[table]
    fields = list(model.model_fields)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=fields)
    missing_required = [
        f for f in fields if f not in df.columns and model.model_fields[f].is_required()
    ]
    if missing_required:
        raise ValueError(f"{table}: missing required columns {missing_required}")
    present = [c for c in fields if c in df.columns]
    records = _to_records(df[present].reset_index(drop=True))
    adapter = TypeAdapter(list[model])  # type: ignore[valid-type]
    try:
        rows = adapter.validate_python(records)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = err.get("loc", ())
        row_i = loc[0] if len(loc) > 0 else "?"
        field_name = loc[1] if len(loc) > 1 else "?"
        raise ValueError(f"{table} row {row_i} field {field_name}: {err['msg']}") from None
    out = pd.DataFrame([r.model_dump() for r in rows], columns=fields)
    return out


# --------------------------------------------------------------------------- DATA_MODEL.md

_CONSTRAINT_START = ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")


def _ddl_columns(ddl: str) -> list[tuple[str, str]]:
    """Parse ``(name, type_and_constraints)`` pairs out of a CREATE TABLE statement."""
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]
    body = re.sub(r"--[^\n]*", "", body)
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    cols: list[tuple[str, str]] = []
    for p in parts:
        p = " ".join(p.split())
        if not p or p.upper().startswith(_CONSTRAINT_START):
            continue
        name, _, rest = p.partition(" ")
        cols.append((name, rest))
    return cols


def render_data_model_md() -> str:
    """Render the data model as markdown (generated file, never hand-edited)."""
    lines: list[str] = []
    lines.append("# Restwert Engine: data model")
    lines.append("")
    lines.append(f"GENERATED by `python -m restwert.schema` from `restwert/schema.py` (package version {__version__}). Do not edit by hand.")
    lines.append("")
    lines.append(f"> {GOVERNANCE_PRINCIPLE}")
    lines.append("")
    lines.append("Conventions: money `DECIMAL(12,2)` EUR; ratios `DOUBLE`; dates `DATE`; ids `VARCHAR`. ")
    lines.append("Every source table ends with `is_synthetic BOOLEAN NOT NULL, source_file VARCHAR`. ")
    lines.append("Foreign keys are validated by `schema.validate_frame` and `db.load_csv_dir` (referential check), not by DuckDB constraints.")
    lines.append("")
    lines.append(SCHEMA_LAYER_NOTE)
    lines.append("")
    lines.append("## Table groups")
    lines.append("")
    lines.append("| Group | Tables |")
    lines.append("|---|---|")
    lines.append("| Source (loaded from CSV, `is_synthetic` on every row) | " + ", ".join(f"`{t}`" for t in SOURCE_TABLES) + " |")
    derived_rebuilt = [t for t in DERIVED_TABLES if t not in IMMUTABLE_TABLES]
    lines.append("| Derived, rebuilt on every run | " + ", ".join(f"`{t}`" for t in derived_rebuilt) + " |")
    lines.append("| Immutable, append only | " + ", ".join(f"`{t}`" for t in IMMUTABLE_TABLES) + " |")
    lines.append("")
    lines.append("Referential checks at load: `devices.model` in `model_catalogue`; `rental_contracts.serial`, `events.serial`, `refurbishment.serial`, `resale.serial` in `devices`; `devices.po_number` in `purchase_orders`.")
    lines.append("")

    def section(title: str, tables: tuple[str, ...] | list[str]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        for t in tables:
            lines.append(f"### `{t}`")
            lines.append("")
            if t in IMMUTABLE_TABLES:
                lines.append("Immutable: rows are only ever appended.")
                lines.append("")
            docs = COLUMN_DOCS.get(t, {})
            lines.append("| Column | Type and constraints | Meaning |")
            lines.append("|---|---|---|")
            for name, rest in _ddl_columns(DDL[t]):
                lines.append(f"| `{name}` | `{rest}` | {docs.get(name, '')} |")
            lines.append("")
            lines.append("```sql")
            lines.append(DDL[t].strip())
            lines.append("```")
            lines.append("")

    section("Source tables", SOURCE_TABLES)
    section("Derived tables", DERIVED_TABLES)
    return "\n".join(lines) + "\n"


def write_data_model_md(path: Path = DOCS_DIR / "DATA_MODEL.md") -> Path:
    """Write ``docs/DATA_MODEL.md`` as UTF-8 without BOM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_data_model_md())
    return path


__all__ = [
    "TABLE_ORDER",
    "SOURCE_TABLES",
    "DERIVED_TABLES",
    "IMMUTABLE_TABLES",
    "SCHEMA_LAYER_NOTE",
    "DDL",
    "COLUMN_DOCS",
    "ROW_MODELS",
    "ModelCatalogueRow",
    "DeviceRow",
    "RentalContractRow",
    "EventRow",
    "RefurbishmentRow",
    "ResaleRow",
    "SupplierContractRow",
    "PurchaseOrderRow",
    "IndirectSpendRow",
    "BenchmarkRow",
    "validate_frame",
    "render_data_model_md",
    "write_data_model_md",
]


if __name__ == "__main__":
    out = write_data_model_md()
    print(f"wrote {out}")
