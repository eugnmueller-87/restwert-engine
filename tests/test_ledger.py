"""Tests of the ledger module (SPEC_v0.2 section 6.8).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Hand-built bronze frames pin absolute numbers (a shared bug in two code paths cannot hide
behind a reconciliation that holds by construction). The pipeline tests run on the
session lake database of module 6 when its fixture is available and skip otherwise.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from restwert import db
from restwert.config import load_assumptions
from restwert.dates import months_between
from restwert.forecast.registry import grid_lookup
from restwert.lake.common import allocate_cents, billing_date
from restwert.lake.schema_lake import GOLD_DDL, SILVER_DDL, create_lake_schema
from restwert.ledger import cohorts as C
from restwert.ledger import result as R
from restwert.ledger.device_ledger import DEVICE_LEDGER_COLUMNS, _GridIndex, build_device_ledger, sums_by_line_type
from restwert.ledger.lines import (
    ESTIMATE_LINE_TYPES,
    LEDGER_LINE_COLUMNS,
    LEDGER_ORDER,
    LINE_TYPES,
    V01_BRIDGE_LINE_TYPES,
    BronzeFrames,
    build_ledger_lines,
    lines_of,
)
from restwert.ledger.reconcile import RECONCILED_FIELDS, assert_reconciled, reconcile_to_device_pnl
from restwert.ledger.run import run_ledger
from restwert.pnl.lifecycle import lifecycle_margin, months_billed

AS_OF = date(2026, 6, 30)
EM_DASH = chr(0x2014)


# --------------------------------------------------------------------------------------
# hand fixture: two serials on one PO line, S1 closed (sold), S2 rented
# --------------------------------------------------------------------------------------

def _ts(d: date, hour: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, 0)


def hand_bronze(as_of: date = AS_OF) -> BronzeFrames:
    """S1: the ten-line worked example of the spec (sold). S2: same PO line, rented, no invoice yet.

    S1 lines: unit 900.00, freight 10.01 over 2 serials (S1 gets 5.00), staging 8.50, outbound
    7.00, 12 rent invoices of 40.00, one repair 120.00, return 9.50, wipe 4.00, refurb 30.00,
    sale 400.00 with credit note fees 50.50, holding per stock phase: inbound 8 days (receipt
    2024-02-01 to shipment 2024-02-09) 2.40, return 6 days (2025-02-14 to sellable 2025-02-20)
    1.80, sale 10 days (to sold 2025-03-02) 3.00. S2 (rented) carries the inbound 8 days only.
    """
    tail = {"delivery_id": "d0", "source_file": "hand.csv", "row_number": 1, "ingested_at": _ts(as_of), "row_hash": "h", "is_synthetic": True}
    cat_models = pd.DataFrame([{
        "slug": "phone-x", "model_name": "Phone X", "oem": "Samsung", "family": "Smartphone", "series": "Galaxy S",
        "launch_date_de": "2023-02-01", "launch_date": date(2023, 2, 1), "launch_date_kind": "verfuegbarkeit",
        "launch_source_url": "https://example.invalid", "successor": None, "successor_launch_date": None, "notes": None, **tail,
    }])
    cat_variants = pd.DataFrame([
        {"slug": "phone-x", "spec": "8 GB / 256 GB", "storage_gb": 256, "ram_gb": 8, "rrp_eur_launch_de": 1190.0,
         "rrp_source_url": "https://example.invalid", "rrp_source_date": date(2023, 2, 1), **tail},
        {"slug": "phone-x", "spec": "8 GB / 256 GB (other colour)", "storage_gb": 256, "ram_gb": 8, "rrp_eur_launch_de": 1249.0,
         "rrp_source_url": "https://example.invalid", "rrp_source_date": date(2023, 2, 1), **tail},
    ])
    mkt_curves = pd.DataFrame([
        {"group_kind": "family_oem", "group": "Smartphone / Samsung", "population": "marketplace", "n": 61, "age_min": 17.5, "age_max": 54.6,
         "intercept": -0.40, "slope_per_month": -0.012, "monthly_depreciation_pct": 0.012, "grade_A_offset": 0.03, "grade_C_offset": -0.15,
         "grade_D_offset": None, "mape_in_sample": 0.2, "q_12": 0.58, "q_24": 0.50, "q_36": 0.44, "fit_quality": "ok", **tail},
        {"group_kind": "family", "group": "Smartphone", "population": "marketplace", "n": 138, "age_min": 17.5, "age_max": 60.9,
         "intercept": -0.45, "slope_per_month": -0.010, "monthly_depreciation_pct": 0.01, "grade_A_offset": 0.03, "grade_C_offset": -0.16,
         "grade_D_offset": None, "mape_in_sample": 0.2, "q_12": 0.57, "q_24": 0.50, "q_36": 0.45, "fit_quality": "ok", **tail},
    ])
    po = pd.DataFrame([{
        "po_number": "PO-2024-000001", "supplier_id": "SUP-SAM", "supplier_name": "Samsung", "supplier_role": "manufacturer",
        "contract_ref": "CTR-SAM", "order_date": date(2024, 1, 10), "promised_date": date(2024, 1, 31), "currency": "EUR",
        "incoterm": "DAP", "payment_terms_days": 30, **tail,
    }])
    po_lines = pd.DataFrame([{
        "po_number": "PO-2024-000001", "po_line": 1, "slug": "phone-x", "storage_gb": 256, "colour": "black", "qty_ordered": 2,
        "unit_price_eur": 905.0, "price_protection_days": 60, **tail,
    }])
    receipts = pd.DataFrame([
        {"gr_number": "GR-1", "po_number": "PO-2024-000001", "po_line": 1, "serial": "S1", "received_at": _ts(date(2024, 2, 1)), "warehouse": "W1", **tail},
        {"gr_number": "GR-1", "po_number": "PO-2024-000001", "po_line": 1, "serial": "S2", "received_at": _ts(date(2024, 2, 1)), "warehouse": "W1", **tail},
    ])
    invoices = pd.DataFrame([
        {"invoice_number": "INV-2024-000183", "invoice_line": 2, "supplier_id": "SUP-SAM", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": "S1", "invoice_date": date(2024, 2, 5), "line_kind": "unit", "qty": 1, "amount_eur": 900.0, "currency": "EUR", **tail},
        {"invoice_number": "INV-2024-000183", "invoice_line": 3, "supplier_id": "SUP-SAM", "po_number": "PO-2024-000001", "po_line": 1,
         "serial": None, "invoice_date": date(2024, 2, 5), "line_kind": "freight", "qty": 2, "amount_eur": 10.01, "currency": "EUR", **tail},
    ])
    price_changes = pd.DataFrame([{
        "change_id": "PC-1", "supplier_id": "SUP-SAM", "slug": "phone-x", "storage_gb": 256, "valid_from": date(2024, 3, 1),
        "old_unit_price_eur": 905.0, "new_unit_price_eur": 880.0, **tail,
    }])
    staging = pd.DataFrame([
        {"staging_id": "ST-1", "serial": "S1", "staged_at": _ts(date(2024, 2, 9)), "mdm_enrolled": True, "staging_cost_eur": 8.5, **tail},
        {"staging_id": "ST-2", "serial": "S2", "staged_at": _ts(date(2024, 2, 9)), "mdm_enrolled": True, "staging_cost_eur": 8.5, **tail},
    ])
    shipments = pd.DataFrame([
        {"shipment_id": "SH-1", "serial": "S1", "related_serial": None, "direction": "outbound", "shipped_at": _ts(date(2024, 2, 9)),
         "delivered_at": _ts(date(2024, 2, 10)), "rental_contract_ref": "RC-1", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 7.0, **tail},
        {"shipment_id": "SH-2", "serial": "S1", "related_serial": None, "direction": "return", "shipped_at": _ts(date(2025, 2, 12)),
         "delivered_at": _ts(date(2025, 2, 14)), "rental_contract_ref": "RC-1", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 9.5, **tail},
        {"shipment_id": "SH-3", "serial": "S2", "related_serial": None, "direction": "outbound", "shipped_at": _ts(date(2024, 2, 9)),
         "delivered_at": _ts(date(2024, 2, 10)), "rental_contract_ref": "RC-2", "carrier_ref": "Logistics partner (role-only)", "cost_eur": 7.0, **tail},
    ])
    contracts = pd.DataFrame([
        {"contract_id": "RC-1", "customer_id": "CUST-1", "serial": "S1", "start_date": date(2024, 2, 10), "term_months": 12, "monthly_rate_eur": 40.0,
         "end_date": date(2025, 2, 10), "actual_end_date": date(2025, 2, 14), "status": "ended", "replaces_contract_id": None, **tail},
        {"contract_id": "RC-2", "customer_id": "CUST-2", "serial": "S2", "start_date": date(2024, 2, 10), "term_months": 36, "monthly_rate_eur": 40.0,
         "end_date": date(2027, 2, 10), "actual_end_date": None, "status": "active", "replaces_contract_id": None, **tail},
    ])
    inv_rows = []
    for cid, serial, start, end in (("RC-1", "S1", date(2024, 2, 10), date(2025, 2, 14)), ("RC-2", "S2", date(2024, 2, 10), date(2027, 2, 10))):
        n = months_between(start, min(end, as_of))
        for k in range(1, n + 1):
            d = billing_date(start, k)
            inv_rows.append({"invoice_id": f"RI-{cid}-{k:03d}", "contract_id": cid, "serial": serial, "period_no": k,
                             "period_month": date(d.year, d.month, 1), "invoice_date": d, "amount_eur": 40.0, **tail})
    invoices_rent = pd.DataFrame(inv_rows)
    tickets = pd.DataFrame([{
        "ticket_id": "TK-1", "serial": "S1", "contract_id": "RC-1", "opened_at": _ts(date(2024, 8, 1)), "closed_at": _ts(date(2024, 8, 9)),
        "damage_type": "screen", "resolution": "repair", "quote_eur": 130.0, "repair_cost_eur": 120.0, "replacement_serial": None,
        "repair_partner_ref": "Refurbishment and repair partner (role-only)", **tail,
    }])
    receipts_ret = pd.DataFrame([{
        "receipt_id": "RR-1", "serial": "S1", "contract_id": "RC-1", "returned_at": _ts(date(2025, 2, 14)), "grade_declared": "B",
        "grade_inspected": "B", "inspected_at": _ts(date(2025, 2, 16)), "wipe_certificate_id": "WIPE-1", "wiped_at": _ts(date(2025, 2, 15)),
        "wipe_grading_cost_eur": 4.0, **tail,
    }])
    work_orders = pd.DataFrame([{
        "work_order_id": "WO-1", "serial": "S1", "started_at": _ts(date(2025, 2, 17)), "finished_at": _ts(date(2025, 2, 20)), "cost_eur": 30.0,
        "grade_out": "B", "outcome": "sellable", "partner_ref": "Refurbishment and repair partner (role-only)", **tail,
    }])
    orders = pd.DataFrame([{
        "order_id": "RO-1", "serial": "S1", "channel": "marketplace", "listed_at": _ts(date(2025, 2, 21)), "sold_at": _ts(date(2025, 3, 2)),
        "gross_price_eur": 400.0, "buyer_type": "consumer", "grade_at_sale": "B", **tail,
    }])
    credit_notes = pd.DataFrame([{
        "credit_note_id": "CN-1", "order_id": "RO-1", "serial": "S1", "channel": "marketplace", "credited_at": _ts(date(2025, 3, 30)),
        "gross_eur": 400.0, "fee_pct_eur": 48.0, "fee_fixed_eur": 2.5, "net_eur": 349.5, **tail,
    }])
    register = pd.DataFrame([{
        "contract_id": "CTR-SAM", "counterparty_name": "Samsung", "counterparty_role": "manufacturer", "counterparty_is_public": True,
        "category": "hardware", "start_date": date(2022, 1, 1), "end_date": date(2027, 12, 31), "notice_days": 90, "auto_renewal": True,
        "price_protection": True, "price_protection_days": 60, "claim_window_days": 30, "warranty_months": 24, "rebate_tiers_json": None,
        "volume_commitment_units": None, "payment_terms_days": 30, "sla_json": None, "spend_under_contract_eur": 100000.0,
        "terms_note": "synthetic placeholder terms", **tail,
    }])
    return BronzeFrames.empty(
        cat_models=cat_models, cat_variants=cat_variants, mkt_curves=mkt_curves, erp_purchase_orders=po, erp_po_lines=po_lines,
        erp_goods_receipts=receipts, erp_supplier_invoices=invoices, erp_price_changes=price_changes, wms_staging_log=staging,
        wms_shipments=shipments, portal_rental_contracts=contracts, portal_rental_invoices=invoices_rent, sd_tickets=tickets,
        ret_receipts=receipts_ret, rf_work_orders=work_orders, rc_orders=orders, rc_credit_notes=credit_notes, ctr_register=register,
    )


def hand_timeline(as_of: date = AS_OF) -> pd.DataFrame:
    """The timeline rows of S1 (sold: inbound 8, return 6 and sale 10 holding days) and S2 (rented: inbound 8 days)."""
    return pd.DataFrame([
        {"serial": "S1", "as_of": as_of, "ordered_at": date(2024, 1, 10), "received_at": date(2024, 2, 1), "staged_at": date(2024, 2, 9),
         "shipped_at": date(2024, 2, 9), "returned_at": date(2025, 2, 14), "wiped_at": date(2025, 2, 15), "graded_at": date(2025, 2, 16),
         "sellable_at": date(2025, 2, 20), "sold_at": date(2025, 3, 2), "credited_at": date(2025, 3, 30), "lifecycle_status": "sold",
         "steps_expected": 10, "steps_present": 10, "missing_steps": None, "first_missing_step": None, "is_monotonic": True,
         "non_monotonic_pair": None, "chain_complete": True, "days_order_to_receipt": 22, "days_receipt_to_ship": 8,
         "days_return_to_sellable": 6, "days_sellable_to_sold": 10, "days_sold_to_credited": 28, "days_return_to_cash": 44,
         "source_refs_json": "{}", "is_synthetic": True},
        {"serial": "S2", "as_of": as_of, "ordered_at": date(2024, 1, 10), "received_at": date(2024, 2, 1), "staged_at": date(2024, 2, 9),
         "shipped_at": date(2024, 2, 9), "returned_at": None, "wiped_at": None, "graded_at": None, "sellable_at": None, "sold_at": None,
         "credited_at": None, "lifecycle_status": "rented", "steps_expected": 4, "steps_present": 4, "missing_steps": None,
         "first_missing_step": None, "is_monotonic": True, "non_monotonic_pair": None, "chain_complete": True,
         "days_order_to_receipt": 22, "days_receipt_to_ship": 8, "days_return_to_sellable": None, "days_sellable_to_sold": None,
         "days_sold_to_credited": None, "days_return_to_cash": None, "source_refs_json": "{}", "is_synthetic": True},
    ])


def hand_device_pnl(as_of: date = AS_OF) -> pd.DataFrame:
    """v0.1 ``device_pnl`` rows for S1 and S2 with the reconciled fields filled by hand."""
    s1_rent = 40.0 * 12
    s1_service = 120.0 + 0.0 + 9.5 + 30.0 + 50.5
    s2_rent = 40.0 * months_between(date(2024, 2, 10), as_of)
    return pd.DataFrame([
        {"serial": "S1", "as_of": as_of, "model_family": "android_like", "model": "phone-x", "storage_gb": 256, "purchase_date": date(2024, 2, 1),
         "cohort": "2024-Q1", "launch_date": date(2023, 2, 1), "landed_cost": 905.0, "purchase_price": 900.0, "months_billed": 12,
         "rental_revenue": s1_rent, "repair_cost": 120.0, "replacement_logistics_cost": 0.0, "return_logistics_cost": 9.5, "refurb_cost": 30.0,
         "channel_fees": 50.5, "realised_rv": 400.0, "lifecycle_margin": lifecycle_margin(s1_rent, 905.0, 400.0, s1_service),
         "lifecycle_status": "sold", "is_closed": True, "closed_date": date(2025, 3, 2), "forecast_rv": None},
        {"serial": "S2", "as_of": as_of, "model_family": "android_like", "model": "phone-x", "storage_gb": 256, "purchase_date": date(2024, 2, 1),
         "cohort": "2024-Q1", "launch_date": date(2023, 2, 1), "landed_cost": 910.01, "purchase_price": 905.0,
         "months_billed": months_between(date(2024, 2, 10), as_of), "rental_revenue": s2_rent, "repair_cost": 0.0,
         "replacement_logistics_cost": 0.0, "return_logistics_cost": 0.0, "refurb_cost": 0.0, "channel_fees": 0.0, "realised_rv": None,
         "lifecycle_margin": None, "lifecycle_status": "rented", "is_closed": False, "closed_date": None, "forecast_rv": 300.0},
    ])


def hand_grid() -> pd.DataFrame:
    """A grid for phone-x: ratio falls 1 % per month from 0.90 at month 0; grade C 10 points lower."""
    rows = []
    for grade, base in (("A", 0.95), ("B", 0.90), ("C", 0.80), ("D", 0.50)):
        for m in range(0, 73):
            rows.append({"run_id": "rv-2026-06-30", "model": "phone-x", "model_family": "android_like", "grade": grade,
                         "months_since_launch": m, "n_launches_since": 0, "storage_gb": 256, "forecast_rv_ratio": max(base - 0.01 * m, 0.02),
                         "ratio_low": 0.0, "ratio_high": 1.0, "list_price": 1000.0, "forecast_rv_on_list": 0.0, "n_train": 100, "fit_quality": "ok"})
    return pd.DataFrame(rows)


def hand_rv_current() -> pd.DataFrame:
    return pd.DataFrame([{"serial": "S2", "as_of": AS_OF, "run_id": "rv-2026-06-30", "model": "phone-x", "model_family": "android_like",
                          "grade_used": "B", "grade_source": "expected", "months_since_launch": 40.9, "n_launches_since": 1,
                          "forecast_rv_ratio": 0.33, "forecast_rv": 300.0, "fit_quality": "ok", "n_train": 100}])


def hand_rv_of_record() -> pd.DataFrame:
    return pd.DataFrame([{"serial": "S1", "return_date": date(2025, 2, 14), "run_id": "rv-2025-01-31", "run_as_of": date(2025, 1, 31),
                          "grade_used": "B", "target_date": date(2025, 3, 16), "months_since_launch": 25.5, "n_launches_since": 1,
                          "forecast_rv_ratio": 0.5, "forecast_rv": 450.0, "channel_factor_employee_buyout": 1.0,
                          "channel_factor_b2b_wholesale": 0.9, "is_missing": False, "missing_reason": None}])


@pytest.fixture(scope="module")
def a():
    return load_assumptions()


@pytest.fixture(scope="module")
def hand(a):
    b = hand_bronze()
    tl = hand_timeline()
    lines = build_ledger_lines(b, tl, a, AS_OF, True)
    dl = build_device_ledger(lines, tl, b, hand_device_pnl(), None, hand_rv_current(), hand_rv_of_record(), hand_grid(), a, None, AS_OF)
    return b, tl, lines, dl


@pytest.fixture
def pipeline_db(request):
    """The session lake DB of module 6 when its fixture exists; skip otherwise."""
    try:
        return request.getfixturevalue("lake_pipeline_db")
    except pytest.FixtureLookupError:
        pytest.skip("lake_pipeline_db fixture (module 6) not available")


# --------------------------------------------------------------------------------------
# 6.8 tests
# --------------------------------------------------------------------------------------

def test_line_types_closed_and_signed(hand):
    _, _, lines, _ = hand
    assert set(LINE_TYPES) == set(LEDGER_ORDER) and len(LINE_TYPES) == 15
    assert list(lines.columns) == LEDGER_LINE_COLUMNS
    assert set(lines["line_type"]) <= set(LINE_TYPES)
    for t, cls in LINE_TYPES.items():
        sub = lines[lines["line_type"] == t]
        if cls == "revenue":
            assert (sub["amount_eur"] >= 0).all(), t
        else:
            assert (sub["amount_eur"] <= 0).all(), t
    assert (lines["line_class"] == lines["line_type"].map(LINE_TYPES)).all()
    est = lines[lines["is_estimate"]]
    assert set(est["line_type"]) <= set(ESTIMATE_LINE_TYPES) | {"purchase_price", "channel_fee"}
    assert set(est["assumption_key"]) <= {"holding_cost_per_day_eur", "po_line_price_pending_invoice", "channel_fees"}
    # every non-estimate line traces to a bronze row
    real = lines[~lines["is_estimate"]]
    assert real["source_ref"].str.contains(":").all() and real["delivery_id"].notna().all()
    assert lines["line_id"].is_unique
    assert not lines.duplicated(["serial", "line_type", "source_system", "source_ref"]).any()


def test_hand_serial_ten_lines(hand):
    _, _, lines, dl = hand
    s1 = lines_of(lines, "S1")
    assert len(s1) == 12 + 10 + 3  # 12 rent lines, the ten other line types plus the fee, three holding phases
    by = s1.groupby("line_type")["amount_eur"].sum().round(2).to_dict()
    assert by == pytest.approx({
        "purchase_price": -900.00, "freight": -5.00, "staging": -8.50, "outbound_shipping": -7.00, "rental_revenue": 480.00,
        "repair": -120.00, "return_logistics": -9.50, "wipe_grading": -4.00, "refurbishment": -30.00, "resale_gross": 400.00,
        "channel_fee": -50.50, "holding_cost": -7.20,
    })
    # holding per stock phase, one line each with its own source_ref: inbound 8 x 0.30, return 6 x 0.30, sale 10 x 0.30
    hold = s1[s1["line_type"] == "holding_cost"].set_index("source_ref")["amount_eur"].to_dict()
    assert hold == pytest.approx({
        "assumptions:holding_cost_per_day_eur:S1:inbound": -2.40,
        "assumptions:holding_cost_per_day_eur:S1:return": -1.80,
        "assumptions:holding_cost_per_day_eur:S1:sale": -3.00,
    })
    assert s1[s1["line_type"] == "holding_cost"]["is_estimate"].all()
    # the credit note's own date is the event date of the booked fee, not the sale date
    fee = s1[s1["line_type"] == "channel_fee"].iloc[0]
    assert pd.Timestamp(fee["event_date"]) == pd.Timestamp(date(2025, 3, 30)) and fee["source_ref"] == "recommerce:CN-1"
    hand_sum = -900 - 5.00 - 8.50 - 7.00 + 480 - 120 - 9.50 - 4.00 - 30 + 400 - 50.50 - 7.20
    assert R.result_closed(s1) == pytest.approx(round(hand_sum, 2)) == pytest.approx(-261.70)
    row = dl.set_index("serial").loc["S1"]
    assert row["lifecycle_result_eur"] == pytest.approx(-261.70)
    v01 = lifecycle_margin(480.0, 905.0, 400.0, 120.0 + 9.5 + 30.0 + 50.5)
    assert R.result_v01_basis(s1) == pytest.approx(v01) == pytest.approx(-235.00)
    assert row["result_v01_basis_eur"] == pytest.approx(v01)
    assert row["landed_cost"] == pytest.approx(905.00) and row["tco_eur"] == pytest.approx(905.0 + 8.5 + 7 + 120 + 9.5 + 4 + 30 + 7.2 + 50.5)
    assert row["tco_transactional_eur"] == pytest.approx(row["tco_eur"] - 7.2)
    assert row["realised_rv"] == pytest.approx(400.0) and row["resale_net"] == pytest.approx(349.5)
    assert row["months_billed"] == 12 and row["rental_revenue"] == pytest.approx(480.0)
    assert row["is_closed"] and row["lifecycle_status"] == "sold" and pd.Timestamp(row["closed_date"]) == pd.Timestamp(date(2025, 3, 2))
    assert row["days_in_stock_to_date"] == 10 and row["n_estimate_lines"] == 3
    # the second serial of the PO line carries the remainder cent and the pending-invoice price
    s2 = lines_of(lines, "S2")
    assert s2.loc[s2["line_type"] == "freight", "amount_eur"].iloc[0] == pytest.approx(-5.01)
    pp = s2[s2["line_type"] == "purchase_price"].iloc[0]
    assert pp["is_estimate"] and pp["assumption_key"] == "po_line_price_pending_invoice" and pp["amount_eur"] == pytest.approx(-905.0)
    assert dl.set_index("serial").loc["S2", "landed_cost"] == pytest.approx(910.01)


def test_allocate_cents_exact():
    assert allocate_cents(10.01, 3) == [3.33, 3.33, 3.35]
    assert allocate_cents(10.01, 2) == [5.00, 5.01]
    assert sum(allocate_cents(123.45, 7)) == pytest.approx(123.45)
    assert allocate_cents(5.0, 0) == []


@pytest.mark.parametrize("start", [date(2024, 1, 31), date(2024, 1, 30), date(2024, 2, 29)])
def test_rent_lines_equal_months_billed(a, start):
    as_of = date(2025, 6, 15)
    end = date(2027, 1, 31)
    n = months_billed(start, end, None, as_of)
    tail = {"delivery_id": "d", "source_file": "f", "row_number": 1, "ingested_at": _ts(as_of), "row_hash": "h", "is_synthetic": True}
    invoices = pd.DataFrame([
        {"invoice_id": f"RI-{k:03d}", "contract_id": "RC-X", "serial": "SX", "period_no": k, "period_month": billing_date(start, k).replace(day=1),
         "invoice_date": billing_date(start, k), "amount_eur": 50.0, **tail}
        for k in range(1, n + 3)  # two invoices dated after as_of must not be booked
    ])
    contracts = pd.DataFrame([{"contract_id": "RC-X", "customer_id": "C", "serial": "SX", "start_date": start, "term_months": 36,
                               "monthly_rate_eur": 50.0, "end_date": end, "actual_end_date": None, "status": "active", "replaces_contract_id": None, **tail}])
    b = BronzeFrames.empty(portal_rental_invoices=invoices, portal_rental_contracts=contracts)
    lines = build_ledger_lines(b, None, a, as_of, True)
    rent = lines[lines["line_type"] == "rental_revenue"]
    assert len(rent) == n
    assert rent["amount_eur"].sum() == pytest.approx(50.0 * n)
    assert all(months_between(start, billing_date(start, k)) == k for k in range(1, 40))


def test_two_open_numbers_differ_and_are_both_stored(hand):
    _, _, _, dl = hand
    row = dl.set_index("serial").loc["S2"]
    assert row["lifecycle_status"] == "rented" and not row["is_closed"]
    assert pd.isna(row["lifecycle_result_eur"])
    liq, proj = row["result_if_liquidated_today"], row["result_projected_at_lease_end"]
    assert liq is not None and proj is not None and not pd.isna(liq) and not pd.isna(proj)
    assert liq < proj  # remaining rent and the value at lease end are only in the projection
    assert row["projected_label"] == "projected at lease end"
    assert row["remaining_contracted_rent"] == pytest.approx(40.0 * row["months_remaining"])
    assert row["months_remaining"] == 36 - row["months_billed"]
    fee = load_assumptions().get("channel_fees", "marketplace")
    sum_lines = -905.0 - 5.01 - 8.5 - 7.0 - 2.40 + row["rental_revenue"]  # the inbound holding phase (8 days) is booked on the rented S2 too
    assert liq == pytest.approx(R.result_if_liquidated_today(sum_lines, 300.0, fee["fee_pct"], fee["fee_fixed_eur"]))
    assert proj == pytest.approx(R.result_projected_at_lease_end(
        sum_lines, row["remaining_contracted_rent"], row["estimate_rv_lease_end"], fee["fee_pct"], fee["fee_fixed_eur"], row["expected_remaining_cost"]))
    assert row["expected_cost_inputs_source"] in ("realised", "assumptions", "mixed")
    # S1 (closed) carries neither open number
    s1 = dl.set_index("serial").loc["S1"]
    assert pd.isna(s1["result_if_liquidated_today"]) and pd.isna(s1["result_projected_at_lease_end"]) and pd.isna(s1["projected_label"])


def test_projected_uses_expected_grade_and_grid(hand, a):
    _, _, _, dl = hand
    row = dl.set_index("serial").loc["S2"]
    assert row["grade_used"] == a.get("expected_grade_at_return", "android_like") and row["grade_source"] == "assumption"
    months = R.estimate_months_at_lease_end(date(2023, 2, 1), date(2027, 2, 10), float(a.get("expected_return_to_sale_days", "android_like")))
    assert row["estimate_months_at_lease_end"] == months
    ratio = grid_lookup(hand_grid(), "phone-x", row["grade_used"], months)
    assert row["estimate_rv_lease_end"] == pytest.approx(round(ratio * 905.0, 2))
    assert row["estimate_rv_source"] == "grid" and row["estimate_fit_quality"] == "ok"
    # a refurbished grade wins over the assumption on the sold serial
    s1 = dl.set_index("serial").loc["S1"]
    assert s1["grade_used"] == "B" and s1["grade_source"] == "refurbished"


def test_grid_index_matches_grid_lookup():
    grid = hand_grid()
    idx = _GridIndex(grid)
    for grade, months in (("A", 0), ("B", 40), ("C", 72), ("D", 99), ("B", -3)):
        hit = idx.lookup("phone-x", grade, months)
        assert hit is not None and hit[0] == pytest.approx(grid_lookup(grid, "phone-x", grade, months))
        assert hit[2] == (months < 0 or months > 72)
    assert idx.lookup("no-such-model", "B", 10) is None


def test_anchor_null_outside_age_range(hand):
    b, _, _, dl = hand
    row = b.mkt_curves.iloc[0]
    inside, group, fit = R.anchor_rv(1000.0, row, 30.0, "B", -0.60)
    assert inside == pytest.approx(round(1000.0 * np.exp(-0.40 - 0.012 * 30.0), 2)) and group == "Smartphone / Samsung" and fit == "ok"
    assert R.anchor_rv(1000.0, row, 17.4, "B", -0.60)[0] is None
    assert R.anchor_rv(1000.0, row, 54.7, "B", -0.60)[0] is None
    assert R.anchor_rv(1000.0, row, 54.6, "B", -0.60)[0] is not None
    a_val = R.anchor_rv(1000.0, row, 30.0, "A", -0.60)[0]
    d_val = R.anchor_rv(1000.0, row, 30.0, "D", -0.60)[0]
    assert a_val > inside > d_val and d_val == pytest.approx(round(1000.0 * np.exp(-0.40 - 0.012 * 30.0 - 0.60), 2))
    assert R.anchor_rv(1000.0, None, 30.0, "B", -0.60) == (None, None, None)
    # the family_oem row is chosen when ok, the family row otherwise
    assert R.select_anchor_curve(b.mkt_curves, "Smartphone", "Samsung")["group"] == "Smartphone / Samsung"
    assert R.select_anchor_curve(b.mkt_curves, "Smartphone", "Google")["group"] == "Smartphone"
    assert R.select_anchor_curve(b.mkt_curves, "Laptop", "Dell") is None
    # on the rented serial the anchor stands beside the estimate and never inside it
    s2 = dl.set_index("serial").loc["S2"]
    assert s2["anchor_curve_group"] == "Smartphone / Samsung"
    if s2["anchor_rv_lease_end"] is not None and not pd.isna(s2["anchor_rv_lease_end"]):
        assert s2["estimate_vs_anchor_ratio"] == pytest.approx(s2["estimate_rv_lease_end"] / s2["anchor_rv_lease_end"])


def test_reconciliation_on_hand_frames(hand):
    _, _, _, dl = hand
    recon = reconcile_to_device_pnl(dl, hand_device_pnl(), AS_OF)
    assert len(recon) == 2 * len(RECONCILED_FIELDS)
    assert recon["ok"].all(), recon[~recon["ok"]]
    assert_reconciled(recon)
    broken = hand_device_pnl()
    broken.loc[broken["serial"] == "S1", "repair_cost"] = 121.0
    bad = reconcile_to_device_pnl(dl, broken, AS_OF)
    assert not bad["ok"].all()
    with pytest.raises(ValueError, match="S1 field repair_cost"):
        assert_reconciled(bad)


def test_reconciliation_identity_on_pipeline(pipeline_db):
    con = pipeline_db
    recon = db.read_df(con, 'SELECT * FROM "silver"."reconciliation"')
    dl = db.read_df(con, 'SELECT * FROM "silver"."device_ledger"')
    n_pnl = con.execute("SELECT count(*) FROM device_pnl").fetchone()[0]
    assert len(recon) > 0 and recon["ok"].all(), recon[~recon["ok"]].head()
    assert recon["serial"].nunique() == n_pnl == len(dl)
    closed = dl[dl["is_closed"].astype(bool)]
    assert len(closed) > 0
    bridge = closed["staging_eur"].astype(float) + closed["outbound_shipping_eur"].astype(float) + closed["wipe_grading_eur"].astype(float) + closed["holding_cost_eur"].astype(float)
    expected = closed["result_v01_basis_eur"].astype(float) - bridge + closed["price_protection_credit_eur"].astype(float)
    assert np.allclose(closed["lifecycle_result_eur"].astype(float), expected, atol=0.011)
    lines = db.read_df(con, 'SELECT serial, sum(amount_eur) AS s FROM "silver"."ledger_lines" GROUP BY serial')
    m = closed.merge(lines, on="serial", how="left")
    assert np.allclose(m["lifecycle_result_eur"].astype(float), m["s"].astype(float), atol=0.011)
    est = db.read_df(con, 'SELECT DISTINCT line_type, assumption_key FROM "silver"."ledger_lines" WHERE is_estimate')
    assert set(est["line_type"]) <= {"holding_cost", "purchase_price", "channel_fee"}
    n_hold = con.execute('SELECT count(*) FROM "silver"."ledger_lines" WHERE line_type = \'holding_cost\'').fetchone()[0]
    assert n_hold > 0


def test_cohort_identity_per_row(hand):
    _, _, lines, dl = hand
    frames = [C.result_by_cohort(dl, kind, AS_OF) for kind in C.COHORT_KINDS]
    res = pd.concat(frames, ignore_index=True)
    assert len(res) > 0 and set(res["cohort_kind"]) <= set(C.COHORT_KINDS)
    closed = res[res["n_closed"] > 0]
    assert len(closed) > 0
    for _, r in closed.iterrows():
        rhs = float(r["sum_rental_revenue_closed"]) + float(r["sum_realised_rv_closed"]) + float(r["sum_pp_credit_closed"]) - float(r["sum_tco_closed"])
        assert float(r["sum_result_closed"]) == pytest.approx(rhs, abs=0.011)
    oem = res[(res["cohort_kind"] == "oem") & (res["cohort_value"] == "Samsung")].iloc[0]
    assert oem["n"] == 2 and oem["n_closed"] == 1 and oem["n_open"] == 1
    assert oem["sum_result_closed"] == pytest.approx(-261.70) and oem["sum_liquidation_today_open"] is not None
    tco = C.tco_by_cohort(dl, lines, "oem", AS_OF)
    assert set(tco["line_type"]) == {t for t, c in LINE_TYPES.items() if c == "cost"}
    assert tco.loc[tco["line_type"] == "holding_cost", "is_estimate"].all() and not tco.loc[tco["line_type"] != "holding_cost", "is_estimate"].any()
    # the estimate flag is read from the lines: S1's channel fee comes from a credit note, so it carries no estimated EUR
    est = tco.set_index("line_type")["estimate_eur"].astype(float)
    assert est["holding_cost"] == pytest.approx(7.20) and est["channel_fee"] == 0.0 and est["purchase_price"] == 0.0
    # a fee without a credit note is an estimate and the cohort table says so
    est_lines = lines.copy()
    est_lines.loc[est_lines["line_type"] == "channel_fee", "is_estimate"] = True
    tco2 = C.tco_by_cohort(dl, est_lines, "oem", AS_OF).set_index("line_type")
    assert bool(tco2.loc["channel_fee", "is_estimate"]) and float(tco2.loc["channel_fee", "estimate_eur"]) == pytest.approx(50.50)
    assert tco["mean_eur"].astype(float).sum() == pytest.approx(dl.set_index("serial").loc["S1", "tco_eur"], abs=0.011)
    pur = C.purchase_by_oem_month(dl, hand_bronze(), AS_OF)
    assert len(pur) == 1 and pur.iloc[0]["n_units"] == 2 and pur.iloc[0]["ppv_vs_po_eur"] == pytest.approx(-5.0)
    assert pur.iloc[0]["share_under_contract"] == pytest.approx(1.0)
    eva = C.estimate_vs_anchor(dl, AS_OF)
    assert len(eva) == 1 and eva.iloc[0]["n_rented"] == 1
    rs = C.resale_by_channel_grade(dl, date(2025, 6, 30))
    assert len(rs) == 1 and rs.iloc[0]["channel"] == "marketplace" and rs.iloc[0]["grade_at_sale"] == "B"
    assert rs.iloc[0]["realised_vs_record_ratio"] == pytest.approx(400.0 / 450.0) and rs.iloc[0]["n_credit_note_missing"] == 0


def test_cohort_identity_on_pipeline(pipeline_db):
    res = db.read_df(pipeline_db, 'SELECT * FROM "gold"."result_by_cohort" WHERE n_closed > 0')
    assert len(res) > 0
    lhs = res["sum_result_closed"].astype(float)
    rhs = res["sum_rental_revenue_closed"].astype(float) + res["sum_realised_rv_closed"].astype(float) + res["sum_pp_credit_closed"].astype(float) - res["sum_tco_closed"].astype(float)
    assert np.allclose(lhs, rhs, atol=0.011)
    for table in ("tco_by_cohort", "purchase_by_oem_month", "estimate_vs_anchor", "resale_by_channel_grade"):
        assert pipeline_db.execute(f'SELECT count(*) FROM "gold"."{table}"').fetchone()[0] > 0, table
    eva = db.read_df(pipeline_db, 'SELECT * FROM "gold"."estimate_vs_anchor"')
    assert (eva["n_with_anchor"] <= eva["n_rented"]).all()


def test_no_result_total_column_anywhere():
    for name, ddl in {**SILVER_DDL, **GOLD_DDL}.items():
        assert "result_total" not in ddl, name
    assert "result_total" not in DEVICE_LEDGER_COLUMNS
    for cols in (C.RESULT_BY_COHORT_COLUMNS, C.TCO_BY_COHORT_COLUMNS, C.PURCHASE_BY_OEM_MONTH_COLUMNS,
                 C.ESTIMATE_VS_ANCHOR_COLUMNS, C.RESALE_BY_CHANNEL_GRADE_COLUMNS):
        assert "result_total" not in cols
    assert {"result_if_liquidated_today", "result_projected_at_lease_end", "projected_label"} <= set(DEVICE_LEDGER_COLUMNS)


def test_device_ledger_columns_match_ddl(hand):
    _, _, _, dl = hand
    ddl = SILVER_DDL["silver.device_ledger"]
    import re

    ddl_cols = re.findall(r"(?:\(|,)\s*(\w+)\s+(?:VARCHAR|DATE|DECIMAL|DOUBLE|INTEGER|BOOLEAN|TIMESTAMP)", ddl)
    assert ddl_cols == DEVICE_LEDGER_COLUMNS
    assert list(dl.columns)[: len(DEVICE_LEDGER_COLUMNS)] == DEVICE_LEDGER_COLUMNS


def test_sums_by_line_type_magnitudes(hand):
    _, _, lines, _ = hand
    s = sums_by_line_type(lines)
    assert (s[list(LEDGER_ORDER)] >= 0).all().all()
    assert s.loc["S1", "purchase_price"] == pytest.approx(900.0) and s.loc["S1", "n_rent"] == 12
    assert s.loc["S1", "sum_amount"] == pytest.approx(-261.70)
    assert s.loc["S1", "estimate_cost"] == pytest.approx(7.20) and s.loc["S1", "n_estimate_lines"] == 3
    assert s.loc["S2", "estimate_cost"] == pytest.approx(905.0 + 2.40)  # pending PO price plus the inbound holding phase


def test_expected_remaining_cost_by_status(a):
    inputs = {"min_n": 30}
    rented, src = R.expected_remaining_cost("rented", "android_like", 12, False, inputs, a)
    awaiting, _ = R.expected_remaining_cost("awaiting_return", "android_like", 0, False, inputs, a)
    wip, _ = R.expected_remaining_cost("wip", "android_like", 0, False, inputs, a)
    stock, _ = R.expected_remaining_cost("in_stock", "android_like", 0, True, inputs, a)
    assert src == "assumptions" and rented > awaiting > wip > stock > 0
    assert R.expected_remaining_cost("sold", "android_like", 0, True, inputs, a) == (0.0, "none")
    holding = float(a.get("holding_cost_per_day_eur"))
    rts = float(a.get("expected_return_to_sale_days", "android_like"))
    # the same stock phases as the ledger: in stock with no day booked yet -> the whole return-to-sale span is ahead
    assert stock == pytest.approx(round(holding * rts, 2))
    # days since return already booked as holding lines are deducted, floored at 0 (never charged twice)
    assert R.expected_remaining_cost("in_stock", "android_like", 0, True, inputs, a, days_since_return=10)[0] == pytest.approx(round(holding * (rts - 10), 2))
    assert R.expected_remaining_cost("in_stock", "android_like", 0, True, inputs, a, days_since_return=400)[0] == 0.0
    wip_late, _ = R.expected_remaining_cost("wip", "android_like", 0, False, inputs, a, days_since_return=int(rts))
    assert wip_late == pytest.approx(float(a.get("refurb_cost_fallback_eur", "android_like")))
    realised = {"min_n": 2, "n_damage": 10, "n_repair": 6, "sum_repair_cost": 600.0, "device_years": 20.0, "n_refurb": 5,
                "sum_refurb_cost": 200.0, "logistics_by_device": {"a": 10.0, "b": 14.0}, "wipe_grading_mean": 5.0, "n_wipe": 4}
    val, src2 = R.expected_remaining_cost("rented", "android_like", 12, False, realised, a)
    assert src2 == "mixed"  # holding x days is always an assumption
    expected = (10 / 20.0) * 1.0 * 0.6 * 100.0 + 12.0 + 5.0 + 40.0 + holding * float(a.get("expected_return_to_sale_days", "android_like"))
    assert val == pytest.approx(round(expected, 2))


def test_run_ledger_in_memory(a):
    """End to end on the hand frames: bronze and device_pnl into DuckDB, run_ledger, tables populated."""
    from restwert.lake.schema_lake import BRONZE_DDL

    con = db.connect(":memory:")
    db.create_schema(con)
    create_lake_schema(con)
    b = hand_bronze()
    for short in b.__dataclass_fields__:
        frame = getattr(b, short)
        if len(frame):
            assert f"bronze.{short}" in BRONZE_DDL
            db.write_df(con, f"bronze.{short}", frame, mode="replace")
    db.write_df(con, "device_pnl", hand_device_pnl(), mode="replace")
    db.write_df(con, "silver.serial_timeline", hand_timeline(), mode="replace")
    db.write_df(con, "rv_forecast_grid", hand_grid(), mode="replace")
    db.write_df(con, "rv_forecast_current", hand_rv_current(), mode="replace")
    db.write_df(con, "rv_forecast_of_record", hand_rv_of_record(), mode="replace")
    summary = run_ledger(con, AS_OF, a)
    assert summary.command == "ledger" and summary.counts["device_ledger"] == 2
    assert summary.counts["closed"] == 1 and summary.counts["open"] == 1
    assert summary.counts["reconciled_fail"] == 0 and summary.counts["reconciled_ok"] == 2 * len(RECONCILED_FIELDS)
    assert summary.counts["estimate_lines"] == 5  # S1 three holding phases, S2 pending invoice and inbound holding
    assert summary.counts["ledger_lines"] == con.execute('SELECT count(*) FROM "silver"."ledger_lines"').fetchone()[0]
    dl = db.read_df(con, 'SELECT * FROM "silver"."device_ledger"')
    assert len(dl) == 2 and dl["lifecycle_result_eur"].dropna().iloc[0] == pytest.approx(-261.70)
    for table in ("result_by_cohort", "tco_by_cohort", "purchase_by_oem_month", "estimate_vs_anchor"):
        assert con.execute(f'SELECT count(*) FROM "gold"."{table}"').fetchone()[0] > 0, table
    assert con.execute("SELECT count(*) FROM runs WHERE command = 'ledger' AND finished_at IS NOT NULL").fetchone()[0] == 1
    # a broken device_pnl makes the run refuse to finish, with the reconciliation written
    broken = hand_device_pnl()
    broken.loc[broken["serial"] == "S1", "landed_cost"] = 999.0
    db.write_df(con, "device_pnl", broken, mode="replace")
    with pytest.raises(ValueError, match="does not reconcile"):
        run_ledger(con, AS_OF, a)
    assert con.execute('SELECT count(*) FROM "silver"."reconciliation" WHERE NOT ok').fetchone()[0] == 1
    con.close()


def test_ledger_md_exists_without_em_dash():
    from restwert.paths import DOCS_DIR

    text = (DOCS_DIR / "LEDGER.md").read_text(encoding="utf-8")
    assert "TCO is a sum of lines; the only rate is holding cost and every holding line says so" in text
    assert EM_DASH not in text
    for t in LINE_TYPES:
        assert t in text
    for t in V01_BRIDGE_LINE_TYPES:
        assert t in text
