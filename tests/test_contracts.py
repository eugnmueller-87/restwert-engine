"""Tests for the contracts register (SPEC 7.6): union, notice arithmetic, horizon boundary, coverage."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from restwert import db
from restwert.config import Thresholds
from restwert.contracts.register import contract_coverage, contracts_register, renewal_calendar, run_contracts
from restwert.dates import add_months
from restwert.kpi import KPI_REGISTRY

try:
    from tests.fixtures import kpi_frames as kf
except ImportError:  # repo root not on sys.path (plain `pytest` invocation): tests/ is
    from fixtures import kpi_frames as kf

AS_OF = kf.AS_OF
RENTAL_NOTICE = 90


@pytest.fixture()
def thresholds() -> Thresholds:
    return Thresholds.model_validate(
        {
            "version": 1,
            "thresholds": {
                "rental_notice_days": {
                    "value": RENTAL_NOTICE,
                    "unit": "days",
                    "owner": "Head of Customer Success (fixture)",
                    "rationale": "fixture",
                    "valid_from": "2026-01-01",
                    "placeholder_default": True,
                    "rule_ids": ["R06"],
                }
            },
        }
    )


def test_register_unions_both_types_with_notice_deadline():
    reg = contracts_register(kf.supplier_contracts_frame(), kf.rental_contracts_frame(), AS_OF, RENTAL_NOTICE)
    assert sorted(reg["contract_type"].unique()) == ["rental", "supplier"]
    assert len(reg) == 4 + 3
    sup = reg[reg["contract_type"] == "supplier"].set_index("contract_id")
    assert sup.loc["SC-01", "notice_deadline"] == pd.Timestamp(date(2026, 9, 30) - timedelta(days=90))
    assert sup.loc["SC-01", "counterparty"] == "Supplier-A"
    assert sup.loc["SC-01", "annual_value"] == pytest.approx(300000.0)
    assert sup.loc["SC-01", "status"] == "active"
    assert sup.loc["SC-04", "status"] == "expired"  # ended 2026-03-31
    assert bool(sup.loc["SC-01", "price_protection"]) is True
    assert sup.loc["SC-01", "payment_terms_days"] == 45
    ren = reg[reg["contract_type"] == "rental"].set_index("contract_id")
    assert ren.loc["RC-8", "counterparty"] == "CUST-0002"
    assert ren.loc["RC-8", "category"] == "rental"
    assert ren.loc["RC-8", "notice_days"] == RENTAL_NOTICE
    assert ren.loc["RC-8", "notice_deadline"] == pd.Timestamp(date(2026, 8, 1) - timedelta(days=RENTAL_NOTICE))
    assert ren.loc["RC-8", "annual_value"] == pytest.approx(50.0 * 12)
    # effective end = actual_end_date when set
    assert ren.loc["RC-9", "end_date"] == pd.Timestamp(date(2026, 7, 15))
    assert ren.loc["RC-1", "status"] == "expired"
    assert pd.isna(ren.loc["RC-8", "price_protection"])
    assert pd.isna(ren.loc["RC-8", "payment_terms_days"])


def test_register_empty_inputs():
    reg = contracts_register(pd.DataFrame(), pd.DataFrame(), AS_OF, RENTAL_NOTICE)
    assert reg.empty
    assert "notice_deadline" in reg.columns


def _register_with_ends(ends: dict[str, tuple[date, int, bool]]) -> pd.DataFrame:
    rows = []
    for cid, (end, notice, auto) in ends.items():
        rows.append(
            dict(
                supplier_contract_id=cid, supplier="Supplier-Z", category="hardware", start_date=date(2024, 1, 1), end_date=end,
                auto_renewal=auto, notice_days=notice, price_protection=False, price_protection_days=None,
                payment_terms_days=30, spend_under_contract=1000.0,
            )
        )
    sup = kf.frame("supplier_contracts", [dict(r, is_synthetic=True, source_file="fixture") for r in rows])
    return contracts_register(sup, pd.DataFrame(), AS_OF, RENTAL_NOTICE)


def test_renewal_calendar_horizon_boundary():
    horizon_end = add_months(AS_OF, 6)  # 2026-12-30
    assert horizon_end == date(2026, 12, 30)
    reg = _register_with_ends(
        {
            "ON-EDGE": (horizon_end, 0, False),
            "ONE-DAY-LATER": (horizon_end + timedelta(days=1), 0, False),
            "TODAY": (AS_OF, 0, False),
            "YESTERDAY": (AS_OF - timedelta(days=1), 0, False),
            "VIA-NOTICE": (horizon_end + timedelta(days=60), 60, False),  # end beyond, notice deadline on the edge
        }
    )
    cal = renewal_calendar(reg, AS_OF, horizon_months=6)
    ids = set(cal["contract_id"])
    assert "ON-EDGE" in ids
    assert "ONE-DAY-LATER" not in ids
    assert "TODAY" in ids
    assert "YESTERDAY" not in ids
    assert "VIA-NOTICE" in ids
    row = cal.set_index("contract_id").loc["ON-EDGE"]
    assert row["days_to_end"] == (horizon_end - AS_OF).days
    assert row["days_to_notice_deadline"] == (horizon_end - AS_OF).days
    assert row["month_bucket"] == "2026-12"
    assert list(cal.columns) == [
        "contract_type", "contract_id", "counterparty", "as_of", "end_date", "notice_days", "notice_deadline",
        "days_to_notice_deadline", "days_to_end", "auto_renewal", "annual_value", "action_required", "month_bucket",
    ]


def test_renewal_calendar_action_required():
    two_months = add_months(AS_OF, 2)  # 2026-08-30
    reg = _register_with_ends(
        {
            "SOON": (two_months, 0, False),                        # notice deadline == as_of + 2 months -> action
            "SOON-PLUS-1": (two_months + timedelta(days=1), 0, False),  # one day later -> no action
            "AUTO": (add_months(AS_OF, 5), 0, True),              # auto-renewal always requires action
            "NOTICE-INSIDE": (add_months(AS_OF, 5), 100, False),   # deadline 100 days before a Nov 30 end -> inside 2 months
        }
    )
    cal = renewal_calendar(reg, AS_OF).set_index("contract_id")
    assert bool(cal.loc["SOON", "action_required"]) is True
    assert bool(cal.loc["SOON-PLUS-1", "action_required"]) is False
    assert bool(cal.loc["AUTO", "action_required"]) is True
    assert bool(cal.loc["NOTICE-INSIDE", "action_required"]) is True


def test_renewal_calendar_on_fixture_includes_rentals():
    reg = contracts_register(kf.supplier_contracts_frame(), kf.rental_contracts_frame(), AS_OF, RENTAL_NOTICE)
    cal = renewal_calendar(reg, AS_OF)
    ids = set(zip(cal["contract_type"], cal["contract_id"]))
    assert ("supplier", "SC-01") in ids  # ends 2026-09-30
    assert ("supplier", "SC-02") in ids  # ends 2026-12-30 exactly on the horizon
    assert ("supplier", "SC-03") not in ids  # ends 2026-12-31, one day past
    assert ("supplier", "SC-04") not in ids  # already expired
    assert ("rental", "RC-8") in ids
    assert ("rental", "RC-9") in ids
    assert ("rental", "RC-1") not in ids


def test_contract_coverage_equals_kpi_on_same_fixture():
    con = kf.fixture_con()
    direct = contract_coverage(kf.purchase_orders_frame(), kf.indirect_spend_frame(), AS_OF)
    via_kpi = KPI_REGISTRY["KPI_PROC_SPEND_UNDER_CONTRACT"].fn(con, AS_OF, kf.TARGETS)
    assert direct.status == via_kpi.status == "ok"
    assert direct.value == pytest.approx(2650 / 4450)
    assert via_kpi.value == pytest.approx(direct.value)
    assert via_kpi.numerator == pytest.approx(direct.numerator)
    assert via_kpi.denominator == pytest.approx(direct.denominator)
    pd.testing.assert_frame_equal(direct.breakdown, via_kpi.breakdown)


def test_contract_coverage_not_measurable_when_empty():
    kv = contract_coverage(pd.DataFrame(), pd.DataFrame(), AS_OF)
    assert kv.status == "not_measurable" and kv.value is None
    kv = contract_coverage(None, None, AS_OF)
    assert kv.status == "not_measurable" and kv.value is None


def test_contract_coverage_one_side_only():
    kv = contract_coverage(None, kf.indirect_spend_frame(), AS_OF)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(1200 / 2000)


def test_run_contracts_writes_tables(thresholds):
    con = kf.fixture_con({"supplier_contracts": kf.supplier_contracts_frame(), "rental_contracts": kf.rental_contracts_frame()})
    summary = run_contracts(con, AS_OF, thresholds)
    assert summary.command == "contracts"
    assert summary.counts["contracts_register"] == 7
    reg = db.read_df(con, "SELECT * FROM contracts_register ORDER BY contract_type, contract_id")
    assert len(reg) == 7
    cal = db.read_df(con, "SELECT * FROM renewal_calendar ORDER BY end_date")
    assert len(cal) == summary.counts["renewal_calendar"] > 0
    assert set(cal["contract_id"]) >= {"SC-01", "SC-02", "RC-8", "RC-9"}
    assert cal["action_required"].notna().all()
    # rerun replaces, never duplicates
    run_contracts(con, AS_OF, thresholds)
    assert db.read_df(con, "SELECT count(*) AS n FROM contracts_register")["n"].iloc[0] == 7
