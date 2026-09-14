"""Hand-built fixture frames for the KPI and contracts tests (SPEC 7.6, module 5).

Every frame here is 3 to 9 rows, small enough to compute the KPI by hand. The
frames are loaded into an in-memory DuckDB through ``db.create_schema`` and
``db.write_df`` only; no generator, pnl, forecast or decisions code runs.

The hand numbers the tests check (AS_OF = 2026-06-30, trailing 12 months =
2025-07-01 .. 2026-06-30):

- Supplier OTIF: PO-1 and PO-3 on time in full, PO-2 late, PO-4 not delivered
  -> 2 of 3.
- PPV: only PO-1 carries a benchmark: (105 - 100) * 10 / (100 * 10) = 0.05;
  two delivered lines without benchmark excluded.
- Spend under contract: hardware 1450 of 2450 EUR (PO-1, PO-3 contracted;
  PO-2 not), indirect 1200 of 2000 EUR (IS-1, IS-4 have a contract)
  -> 2650 / 4450.
- Grading accuracy: EV-1 A=A, EV-3 B=B match; EV-2 B>C optimistic; EV-4 C>B
  pessimistic; EV-5 outside the 6-month window -> 2 of 4.
- Weeks of cover: 4 devices in stock, 2 sales in the trailing 13 weeks
  (S-3 on 2026-05-10, S-7 on 2026-06-01) -> 4 / (2 / 13) = 26.
- Landed vs benchmark: D-1 900 vs 850 (latest valid benchmark, not the older
  950), D-2 500 vs 500, D-3 has no benchmark (excluded, counted), D-4
  purchased outside the window -> 1400 / 1350 - 1.
- Savings 2026: confirmed 100 + 20 = 120, claimed 170 (IS-2 unconfirmed 50),
  plan 1000 -> 0.12.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from restwert import db
from restwert.config import KpiTargets
from restwert.schema import IMMUTABLE_TABLES

AS_OF = date(2026, 6, 30)

TARGETS = KpiTargets(
    version=1,
    savings_plan_eur={2025: 900.0, 2026: 1000.0},
    savings_plan_owner="Head of Indirect Procurement (fixture)",
    targets={"KPI_PROC_SUPPLIER_OTIF": 0.9, "KPI_TOP_LIFECYCLE_MARGIN": 40.0},
    targets_owner="CFO (fixture)",
)

# DDL column order per table (SPEC 2.8). Missing values in a row are filled with None.
COLUMNS: dict[str, tuple[str, ...]] = {
    "devices": (
        "serial", "model_family", "model", "storage_gb", "colour", "launch_date", "purchase_date", "purchase_price",
        "landed_cost", "supplier", "channel_in", "po_number", "contract_id", "is_synthetic", "source_file",
    ),
    "benchmarks": ("model", "valid_from", "landed_cost_benchmark", "source_note", "is_synthetic", "source_file"),
    "purchase_orders": (
        "po_number", "supplier", "supplier_contract_id", "model", "order_date", "promised_date", "delivered_date",
        "qty_ordered", "qty_delivered", "unit_price", "benchmark_price", "price_drop_date", "price_drop_amount",
        "is_synthetic", "source_file",
    ),
    "indirect_spend": (
        "spend_id", "invoice_date", "category", "supplier", "amount", "has_po", "has_contract", "saving",
        "saving_confirmed_by_controlling", "is_synthetic", "source_file",
    ),
    "events": (
        "event_id", "serial", "contract_id", "event_type", "event_date", "cost", "damage_type", "resolved",
        "replacement_serial", "return_date", "grade_pre_return", "grade_inspected", "wipe_certificate", "note",
        "is_synthetic", "source_file",
    ),
    "refurbishment": (
        "refurb_id", "serial", "start_date", "end_date", "days", "cost", "grade_out", "outcome", "is_synthetic", "source_file",
    ),
    "resale": (
        "sale_id", "serial", "channel", "sale_date", "price", "fees", "buyer_type", "grade_at_sale", "is_synthetic", "source_file",
    ),
    "supplier_contracts": (
        "supplier_contract_id", "supplier", "category", "start_date", "end_date", "auto_renewal", "notice_days",
        "price_protection", "price_protection_days", "payment_terms_days", "spend_under_contract", "is_synthetic", "source_file",
    ),
    "rental_contracts": (
        "contract_id", "serial", "customer_id", "start_date", "term_months", "monthly_rate", "end_date", "actual_end_date",
        "status", "replaces_contract_id", "is_synthetic", "source_file",
    ),
    "device_pnl": (
        "serial", "as_of", "model_family", "model", "storage_gb", "purchase_date", "cohort", "launch_date",
        "months_since_launch", "landed_cost", "purchase_price", "months_billed", "rental_revenue", "return_date",
        "grade_inspected", "sellable_date", "refurb_outcome", "grade_current", "resale_channel", "sale_date",
        "resale_price_gross", "resale_fees", "realised_rv", "forecast_rv", "repair_cost", "replacement_logistics_cost",
        "return_logistics_cost", "refurb_cost", "channel_fees", "service_and_logistics_cost", "lifecycle_margin",
        "lifecycle_margin_pct_of_landed", "margin_if_liquidated_today", "lifecycle_status", "is_closed", "closed_date",
        "days_in_stock", "days_in_wip", "book_value_sl", "write_down_cum", "book_value",
    ),
    "rv_forecast_error_monthly": (
        "month", "model_family", "n_sales", "n_with_forecast", "n_excluded_as_is", "mape", "bias", "wape", "mae_eur",
        "realisation_ratio", "sum_realised", "sum_forecast", "run_ids_used",
    ),
    "rv_forecast_of_record": (
        "serial", "return_date", "run_id", "run_as_of", "grade_used", "target_date", "months_since_launch",
        "n_launches_since", "forecast_rv_ratio", "forecast_rv", "is_missing", "missing_reason",
    ),
    "write_down_ledger": (
        "write_down_id", "serial", "as_of", "rule_id", "decision_id", "book_value_before", "amount", "book_value_after",
        "threshold_owner", "run_id",
    ),
    "decision_log": (
        "decision_id", "run_id", "decided_at", "as_of", "rule_id", "rule_version", "subject_type", "subject_id", "outcome",
        "outcome_detail", "threshold_key", "threshold_value", "threshold_value_num", "threshold_unit", "threshold_owner",
        "inputs_json", "advisory_json", "value_at_stake_eur", "due_date", "input_hash", "is_synthetic_input",
    ),
}


def frame(table: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a frame with every DDL column of ``table`` in DDL order (missing -> None)."""

    cols = COLUMNS[table]
    unknown = {k for r in rows for k in r} - set(cols)
    if unknown:
        raise KeyError(f"{table}: unknown fixture column(s) {sorted(unknown)}")
    records = [{c: r.get(c) for c in cols} for r in rows]
    df = pd.DataFrame.from_records(records, columns=list(cols)).astype(object)
    return df.where(df.notna(), None)


def _src(row: dict[str, Any]) -> dict[str, Any]:
    row.setdefault("is_synthetic", True)
    row.setdefault("source_file", "fixture")
    return row


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def devices_frame() -> pd.DataFrame:
    common = dict(storage_gb=128, colour="black", supplier="Supplier-A", channel_in="distributor", po_number="PO-1")
    rows = [
        dict(serial="D-1", model_family="iphone_like", model="P-Gen14", launch_date=date(2025, 9, 15), purchase_date=date(2026, 1, 15), purchase_price=870.0, landed_cost=900.0),
        dict(serial="D-2", model_family="android_like", model="A-Gen06", launch_date=date(2025, 2, 15), purchase_date=date(2026, 2, 1), purchase_price=480.0, landed_cost=500.0),
        dict(serial="D-3", model_family="laptop_like", model="L-Gen04", launch_date=date(2024, 9, 15), purchase_date=date(2026, 3, 1), purchase_price=1350.0, landed_cost=1400.0),
        dict(serial="D-4", model_family="iphone_like", model="P-Gen14", launch_date=date(2025, 9, 15), purchase_date=date(2025, 3, 1), purchase_price=970.0, landed_cost=1000.0),
        dict(serial="D-5", model_family="android_like", model="A-Gen05", launch_date=date(2024, 2, 15), purchase_date=date(2024, 3, 1), purchase_price=450.0, landed_cost=470.0),
        dict(serial="D-6", model_family="iphone_like", model="P-Gen13", launch_date=date(2024, 9, 15), purchase_date=date(2024, 10, 1), purchase_price=800.0, landed_cost=830.0),
        dict(serial="D-7", model_family="android_like", model="A-Gen05", launch_date=date(2024, 2, 15), purchase_date=date(2024, 4, 1), purchase_price=450.0, landed_cost=470.0),
        dict(serial="D-8", model_family="laptop_like", model="L-Gen03", launch_date=date(2023, 3, 15), purchase_date=date(2023, 6, 1), purchase_price=1300.0, landed_cost=1340.0),
        dict(serial="D-9", model_family="laptop_like", model="L-Gen03", launch_date=date(2023, 3, 15), purchase_date=date(2023, 7, 1), purchase_price=1300.0, landed_cost=1340.0),
    ]
    return frame("devices", [_src({**common, **r}) for r in rows])


def benchmarks_frame() -> pd.DataFrame:
    note = "synthetic: fixture band, not a market figure"
    rows = [
        dict(model="P-Gen14", valid_from=date(2024, 9, 15), landed_cost_benchmark=950.0, source_note=note),
        dict(model="P-Gen14", valid_from=date(2025, 9, 15), landed_cost_benchmark=850.0, source_note=note),
        dict(model="A-Gen06", valid_from=date(2025, 2, 15), landed_cost_benchmark=500.0, source_note=note),
    ]
    return frame("benchmarks", [_src(r) for r in rows])


def purchase_orders_frame() -> pd.DataFrame:
    rows = [
        dict(po_number="PO-1", supplier="Supplier-A", supplier_contract_id="SC-01", model="P-Gen14", order_date=date(2026, 1, 10),
             promised_date=date(2026, 2, 1), delivered_date=date(2026, 2, 1), qty_ordered=10, qty_delivered=10, unit_price=105.0, benchmark_price=100.0),
        dict(po_number="PO-2", supplier="Supplier-B", supplier_contract_id=None, model="A-Gen06", order_date=date(2026, 2, 10),
             promised_date=date(2026, 3, 1), delivered_date=date(2026, 3, 5), qty_ordered=5, qty_delivered=5, unit_price=200.0, benchmark_price=None),
        dict(po_number="PO-3", supplier="Supplier-A", supplier_contract_id="SC-01", model="L-Gen04", order_date=date(2026, 3, 10),
             promised_date=date(2026, 4, 1), delivered_date=date(2026, 4, 1), qty_ordered=8, qty_delivered=8, unit_price=50.0, benchmark_price=None),
        dict(po_number="PO-4", supplier="Supplier-C", supplier_contract_id="SC-02", model="P-Gen14", order_date=date(2026, 6, 20),
             promised_date=date(2026, 7, 15), delivered_date=None, qty_ordered=3, qty_delivered=0, unit_price=900.0, benchmark_price=850.0),
    ]
    return frame("purchase_orders", [_src(r) for r in rows])


def indirect_spend_frame() -> pd.DataFrame:
    rows = [
        dict(spend_id="IS-1", invoice_date=date(2026, 2, 1), category="logistics", supplier="Supplier-G", amount=1000.0, has_po=True, has_contract=True, saving=100.0, saving_confirmed_by_controlling=True),
        dict(spend_id="IS-2", invoice_date=date(2026, 3, 1), category="software", supplier="Supplier-H", amount=500.0, has_po=True, has_contract=False, saving=50.0, saving_confirmed_by_controlling=False),
        dict(spend_id="IS-3", invoice_date=date(2026, 4, 1), category="marketing", supplier="Supplier-I", amount=300.0, has_po=False, has_contract=False, saving=0.0, saving_confirmed_by_controlling=False),
        dict(spend_id="IS-4", invoice_date=date(2026, 5, 1), category="repair", supplier="Supplier-G", amount=200.0, has_po=False, has_contract=True, saving=20.0, saving_confirmed_by_controlling=True),
        dict(spend_id="IS-5", invoice_date=date(2025, 3, 1), category="consulting", supplier="Supplier-J", amount=900.0, has_po=True, has_contract=True, saving=90.0, saving_confirmed_by_controlling=True),
    ]
    return frame("indirect_spend", [_src(r) for r in rows])


def events_frame() -> pd.DataFrame:
    rows = [
        dict(event_id="EV-1", serial="D-3", contract_id="RC-3", event_type="return", event_date=date(2026, 4, 1), cost=9.5, return_date=date(2026, 4, 1), grade_pre_return="A", grade_inspected="A", wipe_certificate=True),
        dict(event_id="EV-2", serial="D-4", contract_id="RC-4", event_type="return", event_date=date(2026, 2, 20), cost=9.5, return_date=date(2026, 2, 20), grade_pre_return="B", grade_inspected="C", wipe_certificate=True),
        dict(event_id="EV-3", serial="D-1", contract_id="RC-1", event_type="return", event_date=date(2026, 3, 1), cost=9.5, return_date=date(2026, 3, 1), grade_pre_return="B", grade_inspected="B", wipe_certificate=True),
        dict(event_id="EV-4", serial="D-2", contract_id="RC-2", event_type="return", event_date=date(2026, 1, 15), cost=9.5, return_date=date(2026, 1, 15), grade_pre_return="C", grade_inspected="B", wipe_certificate=False),
        dict(event_id="EV-5", serial="D-6", contract_id="RC-6", event_type="return", event_date=date(2025, 1, 1), cost=9.5, return_date=date(2025, 1, 1), grade_pre_return="A", grade_inspected="A", wipe_certificate=True),
        dict(event_id="EV-6", serial="D-5", contract_id="RC-5", event_type="damage", event_date=date(2026, 6, 10), cost=80.0, damage_type="screen", resolved=False),
    ]
    return frame("events", [_src(r) for r in rows])


def refurbishment_frame() -> pd.DataFrame:
    rows = [
        dict(refurb_id="RF-1", serial="D-3", start_date=date(2026, 4, 5), end_date=date(2026, 4, 30), days=25, cost=40.0, grade_out="A", outcome="sellable"),
        dict(refurb_id="RF-2", serial="D-4", start_date=date(2026, 2, 25), end_date=date(2026, 3, 10), days=13, cost=30.0, grade_out="C", outcome="sellable"),
        dict(refurb_id="RF-3", serial="D-6", start_date=date(2025, 1, 5), end_date=date(2025, 1, 20), days=15, cost=20.0, grade_out="A", outcome="sellable"),
    ]
    return frame("refurbishment", [_src(r) for r in rows])


def resale_frame() -> pd.DataFrame:
    rows = [
        dict(sale_id="S-3", serial="D-3", channel="marketplace", sale_date=date(2026, 5, 10), price=250.0, fees=25.0, buyer_type="consumer", grade_at_sale="A"),
        dict(sale_id="S-4", serial="D-4", channel="b2b_wholesale", sale_date=date(2026, 3, 20), price=310.0, fees=31.0, buyer_type="trader", grade_at_sale="C"),
        dict(sale_id="S-7", serial="D-7", channel="as_is", sale_date=date(2026, 6, 1), price=50.0, fees=2.5, buyer_type="recycler", grade_at_sale="D"),
    ]
    return frame("resale", [_src(r) for r in rows])


def supplier_contracts_frame() -> pd.DataFrame:
    rows = [
        dict(supplier_contract_id="SC-01", supplier="Supplier-A", category="hardware", start_date=date(2024, 7, 1), end_date=date(2026, 9, 30),
             auto_renewal=False, notice_days=90, price_protection=True, price_protection_days=45, payment_terms_days=45, spend_under_contract=300000.0),
        dict(supplier_contract_id="SC-02", supplier="Supplier-C", category="hardware", start_date=date(2025, 1, 1), end_date=date(2026, 12, 30),
             auto_renewal=True, notice_days=0, price_protection=False, price_protection_days=None, payment_terms_days=30, spend_under_contract=120000.0),
        dict(supplier_contract_id="SC-03", supplier="Supplier-G", category="logistics", start_date=date(2025, 1, 1), end_date=date(2026, 12, 31),
             auto_renewal=False, notice_days=0, price_protection=False, price_protection_days=None, payment_terms_days=30, spend_under_contract=50000.0),
        dict(supplier_contract_id="SC-04", supplier="Supplier-H", category="software", start_date=date(2024, 1, 1), end_date=date(2026, 3, 31),
             auto_renewal=False, notice_days=30, price_protection=False, price_protection_days=None, payment_terms_days=60, spend_under_contract=20000.0),
    ]
    return frame("supplier_contracts", [_src(r) for r in rows])


def rental_contracts_frame() -> pd.DataFrame:
    rows = [
        dict(contract_id="RC-1", serial="D-1", customer_id="CUST-0001", start_date=date(2024, 2, 1), term_months=24, monthly_rate=30.0, end_date=date(2026, 2, 1), actual_end_date=None, status="ended"),
        dict(contract_id="RC-8", serial="D-8", customer_id="CUST-0002", start_date=date(2023, 8, 1), term_months=36, monthly_rate=50.0, end_date=date(2026, 8, 1), actual_end_date=None, status="active"),
        dict(contract_id="RC-9", serial="D-9", customer_id="CUST-0003", start_date=date(2024, 8, 1), term_months=36, monthly_rate=45.0, end_date=date(2027, 8, 1), actual_end_date=date(2026, 7, 15), status="terminated_early"),
    ]
    return frame("rental_contracts", [_src(r) for r in rows])


def device_pnl_frame() -> pd.DataFrame:
    base = dict(as_of=AS_OF, storage_gb=128, write_down_cum=0.0)
    rows = [
        dict(serial="D-1", model_family="iphone_like", model="P-Gen14", lifecycle_status="in_stock", is_closed=False, days_in_stock=100, book_value=300.0, book_value_sl=300.0, sellable_date=date(2026, 3, 22), return_date=date(2026, 3, 1), grade_current="B"),
        dict(serial="D-2", model_family="android_like", model="A-Gen06", lifecycle_status="in_stock", is_closed=False, days_in_stock=200, book_value=100.0, book_value_sl=125.0, write_down_cum=25.0, sellable_date=date(2025, 12, 12), return_date=date(2026, 1, 15), grade_current="B"),
        dict(serial="D-3", model_family="laptop_like", model="L-Gen04", lifecycle_status="sold", is_closed=True, closed_date=date(2026, 5, 10), sale_date=date(2026, 5, 10), sellable_date=date(2026, 4, 30), return_date=date(2026, 4, 1),
             resale_channel="marketplace", resale_price_gross=250.0, resale_fees=25.0, realised_rv=250.0, refurb_cost=40.0, return_logistics_cost=15.0, repair_cost=60.0, channel_fees=25.0, lifecycle_margin=30.0, rental_revenue=720.0, landed_cost=800.0),
        dict(serial="D-4", model_family="iphone_like", model="P-Gen14", lifecycle_status="sold", is_closed=True, closed_date=date(2026, 3, 20), sale_date=date(2026, 3, 20), sellable_date=date(2026, 3, 10), return_date=date(2026, 2, 20),
             resale_channel="b2b_wholesale", resale_price_gross=310.0, resale_fees=31.0, realised_rv=310.0, refurb_cost=30.0, return_logistics_cost=12.0, repair_cost=45.0, channel_fees=31.0, lifecycle_margin=-28.0, rental_revenue=600.0, landed_cost=820.0),
        dict(serial="D-5", model_family="android_like", model="A-Gen05", lifecycle_status="scrapped", is_closed=True, closed_date=date(2025, 1, 20), lifecycle_margin=-500.0, realised_rv=0.0),
        dict(serial="D-6", model_family="iphone_like", model="P-Gen13", lifecycle_status="wip", is_closed=False, days_in_wip=12, return_date=date(2026, 6, 18)),
        dict(serial="D-7", model_family="android_like", model="A-Gen05", lifecycle_status="sold", is_closed=True, closed_date=date(2026, 6, 1), sale_date=date(2026, 6, 1), sellable_date=date(2026, 5, 20),
             resale_channel="as_is", resale_price_gross=50.0, resale_fees=2.5, realised_rv=50.0, refurb_cost=5.0, return_logistics_cost=9.5, lifecycle_margin=-100.0),
        dict(serial="D-8", model_family="laptop_like", model="L-Gen03", lifecycle_status="in_stock", is_closed=False, days_in_stock=30, book_value=500.0, book_value_sl=500.0, sellable_date=date(2026, 5, 31)),
        dict(serial="D-9", model_family="laptop_like", model="L-Gen03", lifecycle_status="in_stock", is_closed=False, days_in_stock=10, book_value=500.0, book_value_sl=500.0, sellable_date=date(2026, 6, 20)),
    ]
    return frame("device_pnl", [{**base, **r} for r in rows])


def rv_error_frame() -> pd.DataFrame:
    rows = [
        dict(month=date(2026, 4, 1), model_family="*", n_sales=30, n_with_forecast=28, n_excluded_as_is=2, mape=0.15, bias=0.05, wape=0.14, mae_eur=30.0),
        dict(month=date(2026, 5, 1), model_family="*", n_sales=45, n_with_forecast=40, n_excluded_as_is=5, mape=0.12, bias=0.03, wape=0.11, mae_eur=25.0),
        dict(month=date(2026, 5, 1), model_family="iphone_like", n_sales=25, n_with_forecast=22, n_excluded_as_is=3, mape=0.10, bias=0.02, wape=0.09, mae_eur=20.0),
        dict(month=date(2026, 6, 1), model_family="*", n_sales=20, n_with_forecast=18, n_excluded_as_is=2, mape=0.20, bias=0.10, wape=0.19, mae_eur=40.0),
    ]
    return frame("rv_forecast_error_monthly", rows)


def rv_record_frame() -> pd.DataFrame:
    rows = [
        dict(serial="D-3", return_date=date(2026, 4, 1), run_id="rv-2026-03-31", run_as_of=date(2026, 3, 31), grade_used="A", forecast_rv_ratio=0.15, forecast_rv=200.0, is_missing=False),
        dict(serial="D-4", return_date=date(2026, 2, 20), run_id="rv-2026-01-31", run_as_of=date(2026, 1, 31), grade_used="C", forecast_rv_ratio=0.31, forecast_rv=300.0, is_missing=False),
        dict(serial="D-7", return_date=date(2026, 5, 1), run_id="rv-2026-04-30", run_as_of=date(2026, 4, 30), grade_used="D", forecast_rv_ratio=0.18, forecast_rv=80.0, is_missing=False),
        dict(serial="D-1", return_date=date(2026, 3, 1), run_id=None, run_as_of=None, grade_used="B", forecast_rv_ratio=None, forecast_rv=None, is_missing=True, missing_reason="no run before return_date"),
    ]
    return frame("rv_forecast_of_record", rows)


def write_down_ledger_frame() -> pd.DataFrame:
    rows = [
        dict(write_down_id="WD-1", serial="D-2", as_of=date(2026, 5, 31), rule_id="R03", decision_id="dec-1", book_value_before=125.0, amount=10.0, book_value_after=115.0, threshold_owner="CFO (fixture)", run_id="decide-1"),
        dict(write_down_id="WD-2", serial="D-2", as_of=date(2026, 6, 30), rule_id="R03", decision_id="dec-2", book_value_before=125.0, amount=25.0, book_value_after=100.0, threshold_owner="CFO (fixture)", run_id="decide-2"),
    ]
    return frame("write_down_ledger", rows)


def decision_log_frame() -> pd.DataFrame:
    rows = [
        dict(decision_id="dec-2", run_id="decide-2", decided_at=datetime(2026, 6, 30, 6, 0, tzinfo=timezone.utc).replace(tzinfo=None), as_of=date(2026, 6, 30), rule_id="R03", rule_version="1.0",
             subject_type="device", subject_id="D-2", outcome="write_down_180", outcome_detail="fixture", threshold_key="aging_days_180", threshold_value="180",
             threshold_value_num=180.0, threshold_unit="days", threshold_owner="CFO (fixture)", inputs_json="{}", advisory_json=None, value_at_stake_eur=25.0, due_date=None,
             input_hash="fixture-hash-1", is_synthetic_input=True),
    ]
    return frame("decision_log", rows)


def all_frames() -> dict[str, pd.DataFrame]:
    return {
        "devices": devices_frame(),
        "benchmarks": benchmarks_frame(),
        "purchase_orders": purchase_orders_frame(),
        "indirect_spend": indirect_spend_frame(),
        "events": events_frame(),
        "refurbishment": refurbishment_frame(),
        "resale": resale_frame(),
        "supplier_contracts": supplier_contracts_frame(),
        "rental_contracts": rental_contracts_frame(),
        "device_pnl": device_pnl_frame(),
        "rv_forecast_error_monthly": rv_error_frame(),
        "rv_forecast_of_record": rv_record_frame(),
        "write_down_ledger": write_down_ledger_frame(),
        "decision_log": decision_log_frame(),
    }


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


def empty_con() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with the full schema and no rows."""

    con = db.connect(":memory:")
    db.create_schema(con)
    return con


def fixture_con(tables: dict[str, pd.DataFrame] | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with the schema and the fixture frames loaded via ``db.write_df``."""

    con = empty_con()
    for table, df in (tables if tables is not None else all_frames()).items():
        if table in IMMUTABLE_TABLES:
            db.append_rows(con, table, df)  # the only call allowed on immutable tables
        else:
            db.write_df(con, table, df, mode="replace")
    return con
