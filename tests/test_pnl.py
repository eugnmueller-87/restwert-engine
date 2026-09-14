"""Tests for module pnl (spec section 4.6).

Hand cases from spec 4.1, ``derive_status`` per branch with boundary dates, ``months_billed``
edge cases, straight-line book value, TCO on a two-model fixture with a hand-built grid,
aggregation sums, ledger cut-off, and ``run_pnl`` end to end on an in-memory DuckDB.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures import pnl_frames as fx  # noqa: E402

fx.ensure_foundation()

from restwert.config import TERM_MONTHS  # noqa: E402
from restwert.pnl.aggregate import AGGREGATE_DIMENSIONS, PNL_AGGREGATE_COLUMNS, aggregate_pnl  # noqa: E402
from restwert.pnl.book_value import planned_rv, straight_line_book_value  # noqa: E402
from restwert.pnl.lifecycle import (  # noqa: E402
    DEVICE_PNL_COLUMNS,
    build_device_pnl,
    derive_status,
    lifecycle_margin,
    months_billed,
    run_pnl,
)
from restwert.pnl.tco import TCO_COLUMNS, tco_per_model  # noqa: E402

AS_OF = fx.AS_OF


@pytest.fixture(scope="module")
def a():
    return fx.load_test_assumptions()


def _pnl(frames: dict, a, rv_current=None, ledger=None, as_of: date = AS_OF) -> pd.DataFrame:
    return build_device_pnl(
        frames["devices"], frames["rental_contracts"], frames["events"], frames["refurbishment"],
        frames["resale"], rv_current, ledger, a, as_of,
    )


# ----------------------------------------------------------------------------------------
# lifecycle_margin hand cases
# ----------------------------------------------------------------------------------------

def test_lifecycle_margin_pure_hand_cases():
    assert lifecycle_margin(720, 800, 250, 60 + 40 + 15 + 25) == pytest.approx(30.0)
    assert lifecycle_margin(600, 820, 310, 45 + 30 + 12 + 31) == pytest.approx(-28.0)


def test_hand_case_1_margin_30(a):
    df = _pnl(fx.hand_case_1(), a)
    r = df.set_index("serial").loc["D-000001"]
    assert r["lifecycle_status"] == "sold"
    assert r["months_billed"] == 24
    assert r["rental_revenue"] == pytest.approx(720.0)
    assert r["realised_rv"] == pytest.approx(250.0)
    assert r["resale_price_gross"] == pytest.approx(250.0)
    assert r["channel_fees"] == pytest.approx(25.0)
    assert r["service_and_logistics_cost"] == pytest.approx(140.0)
    assert r["lifecycle_margin"] == pytest.approx(30.0)
    assert r["lifecycle_margin_pct_of_landed"] == pytest.approx(30.0 / 800.0)
    assert bool(r["is_closed"]) is True
    assert r["closed_date"] == date(2026, 3, 1)
    # fees are counted once: inside the service block, the residual value stays gross
    assert r["rental_revenue"] - (r["landed_cost"] - r["realised_rv"]) - r["service_and_logistics_cost"] == pytest.approx(30.0)


def test_hand_case_2_margin_minus_28(a):
    df = _pnl(fx.hand_case_2(), a)
    r = df.iloc[0]
    assert r["lifecycle_status"] == "sold"
    assert r["rental_revenue"] == pytest.approx(600.0)
    assert r["service_and_logistics_cost"] == pytest.approx(118.0)
    assert r["lifecycle_margin"] == pytest.approx(-28.0)
    # grade drift is visible: declared A, inspected B, refurbished out as B
    assert r["grade_inspected"] == "B"
    assert r["grade_current"] == "B"


def test_replacement_case_revenue_split_never_doubled(a):
    df = _pnl(fx.replacement_case(), a).set_index("serial")
    assert df.loc["D-000010", "months_billed"] == 10
    assert df.loc["D-000010", "rental_revenue"] == pytest.approx(200.0)
    assert df.loc["D-000011", "months_billed"] == 14
    assert df.loc["D-000011", "rental_revenue"] == pytest.approx(280.0)
    assert df["rental_revenue"].sum() == pytest.approx(480.0)
    assert df.loc["D-000010", "replacement_logistics_cost"] == pytest.approx(18.0)
    assert df.loc["D-000010", "return_logistics_cost"] == pytest.approx(9.5)
    assert df.loc["D-000010", "lifecycle_status"] == "wip"
    assert df.loc["D-000011", "lifecycle_status"] == "wip"
    assert df["lifecycle_margin"].isna().all()


# ----------------------------------------------------------------------------------------
# derive_status: every branch with boundary dates
# ----------------------------------------------------------------------------------------

def _status(**kw) -> str:
    base = dict(as_of=AS_OF, has_sale=False, sale_date=None, refurb_outcome=None, refurb_end=None,
                return_date=None, contract_start=None, contract_end_effective=None)
    base.update(kw)
    return derive_status(**base)


def test_derive_status_sold_boundary():
    assert _status(has_sale=True, sale_date=AS_OF) == "sold"
    assert _status(has_sale=True, sale_date=AS_OF + pd.Timedelta(days=1).to_pytimedelta(),
                   refurb_end=date(2026, 5, 1), refurb_outcome="sellable") == "in_stock"
    # a sale row without a date never counts as sold
    assert _status(has_sale=True, sale_date=None, return_date=date(2026, 6, 1)) == "wip"


def test_derive_status_scrapped_and_in_stock_boundary():
    assert _status(refurb_outcome="scrap", refurb_end=AS_OF) == "scrapped"
    assert _status(refurb_outcome="scrap", refurb_end=date(2026, 7, 1), return_date=date(2026, 6, 1)) == "wip"
    assert _status(refurb_outcome="sellable", refurb_end=AS_OF) == "in_stock"
    assert _status(refurb_outcome="as_is", refurb_end=date(2026, 6, 1)) == "in_stock"
    assert _status(refurb_outcome="sellable", refurb_end=date(2026, 7, 1), return_date=date(2026, 6, 1)) == "wip"


def test_derive_status_wip_rented_awaiting_not_deployed():
    assert _status(return_date=AS_OF) == "wip"
    assert _status(return_date=date(2026, 7, 2), contract_start=date(2024, 1, 1),
                   contract_end_effective=date(2026, 6, 1)) == "awaiting_return"
    assert _status(contract_start=date(2024, 1, 1), contract_end_effective=date(2027, 1, 1)) == "rented"
    assert _status(contract_start=AS_OF, contract_end_effective=date(2028, 6, 30)) == "rented"
    assert _status(contract_start=date(2024, 1, 1), contract_end_effective=AS_OF) == "awaiting_return"
    assert _status(contract_start=date(2026, 7, 15), contract_end_effective=date(2028, 7, 15)) == "not_deployed"
    assert _status() == "not_deployed"


def test_derive_status_first_hit_wins():
    # a sold device with every other signal present is still sold
    assert _status(has_sale=True, sale_date=date(2026, 5, 1), refurb_outcome="scrap", refurb_end=date(2026, 4, 1),
                   return_date=date(2026, 3, 1), contract_start=date(2024, 1, 1),
                   contract_end_effective=date(2026, 2, 1)) == "sold"


# ----------------------------------------------------------------------------------------
# months_billed
# ----------------------------------------------------------------------------------------

def test_months_billed_cases():
    assert months_billed(date(2024, 1, 15), date(2026, 1, 15), date(2024, 11, 15), AS_OF) == 10
    assert months_billed(date(2024, 1, 15), date(2027, 1, 15), date(2026, 12, 1), AS_OF) == 29
    assert months_billed(date(2026, 7, 15), date(2028, 7, 15), None, AS_OF) == 0
    assert months_billed(date(2024, 2, 1), date(2026, 2, 1), None, AS_OF) == 24
    # full months only: 2024-01-31 to 2024-02-29 is not a month
    assert months_billed(date(2024, 1, 31), date(2026, 1, 31), None, date(2024, 2, 29)) == 0
    assert months_billed(date(2024, 1, 31), date(2026, 1, 31), None, date(2024, 3, 1)) == 1


def test_months_billed_pro_rata_not_implemented():
    with pytest.raises(NotImplementedError):
        months_billed(date(2024, 1, 1), date(2026, 1, 1), None, AS_OF, pro_rata=True)


# ----------------------------------------------------------------------------------------
# book value
# ----------------------------------------------------------------------------------------

def test_planned_rv_uses_family_ratio(a):
    assert planned_rv(800.0, "iphone_like", a) == pytest.approx(160.0)
    assert planned_rv(500.0, "android_like", a) == pytest.approx(50.0)
    with pytest.raises(KeyError):
        planned_rv(500.0, "unknown_family", a)


def test_straight_line_book_value_boundaries():
    purchase = date(2024, 1, 10)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, purchase) == pytest.approx(800.0)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, date(2024, 1, 20)) == pytest.approx(800.0)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, date(2027, 1, 10)) == pytest.approx(160.0)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, date(2028, 1, 10)) == pytest.approx(160.0)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, date(2025, 7, 10)) == pytest.approx(480.0)
    assert straight_line_book_value(800.0, 160.0, purchase, 36, date(2025, 7, 9)) == pytest.approx(800 - 640 * 17 / 36, abs=0.01)


# ----------------------------------------------------------------------------------------
# build_device_pnl: columns, open devices, forecast, ledger
# ----------------------------------------------------------------------------------------

def test_build_device_pnl_has_every_ddl_column(a):
    df = _pnl(fx.hand_case_1(), a)
    assert list(df.columns) == DEVICE_PNL_COLUMNS
    assert len(DEVICE_PNL_COLUMNS) == 41
    empty = _pnl(fx.empty_frames(), a)
    assert list(empty.columns) == DEVICE_PNL_COLUMNS and len(empty) == 0


def test_open_devices_margin_null_forecast_and_open_margin(a):
    frames = fx.open_device_case()
    rv_current = pd.DataFrame([fx.rv_current_row("D-000020", 300.0)], columns=fx.RV_CURRENT_COLS)
    df = _pnl(frames, a, rv_current=rv_current).set_index("serial")

    stock = df.loc["D-000020"]
    assert stock["lifecycle_status"] == "in_stock"
    assert pd.isna(stock["lifecycle_margin"]) and pd.isna(stock["realised_rv"])
    assert stock["service_and_logistics_cost"] == pytest.approx(115.0)
    assert stock["channel_fees"] == pytest.approx(0.0)
    assert stock["forecast_rv"] == pytest.approx(300.0)
    assert stock["margin_if_liquidated_today"] == pytest.approx(720 - (800 - 300) - 115 - 300 * 0.12)
    assert stock["sellable_date"] == date(2026, 3, 1)
    assert stock["days_in_stock"] == 121
    assert pd.isna(stock["days_in_wip"])
    assert stock["grade_current"] == "B" and stock["grade_inspected"] == "B"
    assert stock["cohort"] == "2024-Q1"
    assert bool(stock["is_closed"]) is False and pd.isna(stock["closed_date"])

    rented = df.loc["D-000021"]
    assert rented["lifecycle_status"] == "rented"
    assert rented["months_billed"] == 17 and rented["rental_revenue"] == pytest.approx(510.0)
    assert rented["repair_cost"] == pytest.approx(0.0), "an open damage quote is not cost"
    assert pd.isna(rented["forecast_rv"]) and pd.isna(rented["margin_if_liquidated_today"])


def test_forecast_ignored_for_closed_devices(a):
    rv_current = pd.DataFrame([fx.rv_current_row("D-000001", 300.0)], columns=fx.RV_CURRENT_COLS)
    df = _pnl(fx.hand_case_1(), a, rv_current=rv_current)
    assert pd.isna(df.iloc[0]["forecast_rv"]) and pd.isna(df.iloc[0]["margin_if_liquidated_today"])
    assert df.iloc[0]["lifecycle_margin"] == pytest.approx(30.0)


def test_book_value_uses_only_ledger_rows_before_as_of(a):
    frames = fx.open_device_case()
    ledger = pd.DataFrame([
        fx.ledger_row("D-000020", date(2026, 5, 31), 28.44),
        fx.ledger_row("D-000020", AS_OF, 50.0),            # same as_of: not strictly earlier, excluded
        fx.ledger_row("D-000020", date(2026, 7, 31), 70.0),  # future run: excluded
        fx.ledger_row("D-000021", date(2026, 4, 30), 5.0),
    ], columns=fx.LEDGER_COLS)
    df = _pnl(frames, a, ledger=ledger).set_index("serial")
    stock = df.loc["D-000020"]
    # purchase 2024-01-10, as_of 2026-06-30 -> 29 full months of 36, planned 160
    assert stock["book_value_sl"] == pytest.approx(round(800 - 640 * 29 / 36, 2))
    assert stock["write_down_cum"] == pytest.approx(28.44)
    assert stock["book_value"] == pytest.approx(stock["book_value_sl"] - 28.44)
    assert df.loc["D-000021", "write_down_cum"] == pytest.approx(5.0)
    no_ledger = _pnl(frames, a).set_index("serial")
    assert no_ledger.loc["D-000020", "write_down_cum"] == 0.0
    assert no_ledger.loc["D-000020", "book_value"] == pytest.approx(no_ledger.loc["D-000020", "book_value_sl"])


def test_book_value_floors_at_zero(a):
    frames = fx.open_device_case()
    ledger = pd.DataFrame([fx.ledger_row("D-000020", date(2026, 5, 31), 5000.0)], columns=fx.LEDGER_COLS)
    df = _pnl(frames, a, ledger=ledger).set_index("serial")
    assert df.loc["D-000020", "book_value"] == 0.0


# ----------------------------------------------------------------------------------------
# TCO
# ----------------------------------------------------------------------------------------

def _tco(frames, a, term):
    return tco_per_model(
        frames["devices"], frames["events"], frames["refurbishment"], frames["resale"], frames["rental_contracts"],
        frames["rv_grid"], a, None, term, AS_OF,
    ).set_index("model")


def test_tco_hand_numbers_24_and_36(a):
    frames = fx.tco_fixture()
    t24 = _tco(frames, a, 24)
    t36 = _tco(frames, a, 36)
    assert list(t24.reset_index().columns) == TCO_COLUMNS
    assert set(t24.index) == {"P-Gen01", "A-Gen01"}

    p = t24.loc["P-Gen01"]
    assert p["n_devices"] == 2
    assert p["landed_cost_avg"] == pytest.approx(850.0)
    assert p["purchase_price_avg"] == pytest.approx(820.0)
    assert p["months_since_launch_at_purchase_avg"] == pytest.approx(0.0)
    assert p["grade_assumed"] == "B"
    assert p["forecast_rv_ratio_at_end"] == pytest.approx(0.65)      # month round(24 + 30/30.4375) = 25
    assert p["forecast_rv_at_end"] == pytest.approx(533.0)
    assert p["expected_repair_cost"] == pytest.approx(0.10 * 2 * 0.70 * 150)   # 21.00
    assert p["expected_refurb_cost"] == pytest.approx(40.0)
    assert p["expected_logistics_cost"] == pytest.approx(12.0)
    assert p["expected_channel_fees"] == pytest.approx(63.96)
    assert p["tco"] == pytest.approx(453.96)
    assert p["tco_per_month"] * 24 == pytest.approx(p["tco"])
    assert p["monthly_rate_avg"] == pytest.approx(34.0)              # the term-24 contract only
    assert p["gap_rate_minus_tco_per_month"] == pytest.approx(34.0 - 453.96 / 24)
    assert p["inputs_source"] == "assumptions"

    p36 = t36.loc["P-Gen01"]
    assert p36["forecast_rv_ratio_at_end"] == pytest.approx(0.53)    # month 37
    assert p36["forecast_rv_at_end"] == pytest.approx(434.6)
    assert p36["expected_repair_cost"] == pytest.approx(31.5)
    assert p36["expected_channel_fees"] == pytest.approx(52.15, abs=0.006)
    assert p36["tco"] == pytest.approx(551.05, abs=0.011)
    assert p36["tco_per_month"] * 36 == pytest.approx(p36["tco"])
    assert p36["monthly_rate_avg"] == pytest.approx(40.0)            # the term-36 contract only

    an = t24.loc["A-Gen01"]
    assert an["forecast_rv_ratio_at_end"] == pytest.approx(0.55)     # round(24 + 35/30.4375) = 25
    assert an["forecast_rv_at_end"] == pytest.approx(264.0)
    assert an["expected_repair_cost"] == pytest.approx(0.11 * 2 * 0.65 * 110, abs=0.006)
    assert an["expected_refurb_cost"] == pytest.approx(35.0)
    assert an["expected_channel_fees"] == pytest.approx(31.68)
    assert an["tco"] == pytest.approx(330.41)
    assert an["gap_rate_minus_tco_per_month"] == pytest.approx(22.0 - 330.41 / 24)

    an36 = t36.loc["A-Gen01"]
    assert an36["forecast_rv_ratio_at_end"] == pytest.approx(0.50)   # month 37 clipped to grid max 30
    assert an36["forecast_rv_at_end"] == pytest.approx(240.0)
    assert an36["tco"] == pytest.approx(500 - 240 + 23.595 + 35 + 12 + 28.8, abs=0.011)
    assert an36["tco_per_month"] * 36 == pytest.approx(an36["tco"])
    assert an36["monthly_rate_avg"] == pytest.approx(22.0)           # no term-36 contract: all terms


def test_tco_realised_inputs_switch_source_to_mixed(a):
    frames = fx.tco_fixture()
    frames["events"] = fx.tco_realised_events(30, 100.0)
    t24 = _tco(frames, a, 24)
    p = t24.loc["P-Gen01"]
    # two contracts billed 21 months each up to as_of -> 3.5 device-years; 30 damages, 30 repairs at 100
    device_years = 42 / 12
    assert p["expected_repair_cost"] == pytest.approx((30 / device_years) * 2 * 1.0 * 100, abs=0.01)
    assert p["inputs_source"] == "mixed"
    # the android family has no realised events and stays on assumptions
    assert t24.loc["A-Gen01", "inputs_source"] == "assumptions"
    # below min_n the realised path is not taken
    frames["events"] = fx.tco_realised_events(29, 100.0)
    assert _tco(frames, a, 24).loc["P-Gen01", "inputs_source"] == "assumptions"


def test_tco_without_grid_falls_back_to_planned_ratio(a):
    frames = fx.tco_fixture()
    frames["rv_grid"] = pd.DataFrame(columns=fx.GRID_COLS)
    t = _tco(frames, a, 24)
    assert t.loc["P-Gen01", "forecast_rv_ratio_at_end"] == pytest.approx(0.20)
    assert t.loc["P-Gen01", "inputs_source"] == "assumptions"


# ----------------------------------------------------------------------------------------
# aggregate
# ----------------------------------------------------------------------------------------

def _combined_pnl(a) -> pd.DataFrame:
    cases = [fx.hand_case_1(), fx.hand_case_2(), fx.open_device_case()]
    frames = {k: pd.concat([c[k] for c in cases], ignore_index=True) for k in cases[0]}
    return _pnl(frames, a)


def test_aggregate_sums_and_margin_pct_from_sums(a):
    dp = _combined_pnl(a)
    agg = aggregate_pnl(dp, "model_family")
    assert list(agg.columns) == PNL_AGGREGATE_COLUMNS
    r = agg.set_index("group_value").loc["iphone_like"]
    assert r["group_by"] == "model_family"
    assert r["n"] == 4 and r["n_closed"] == 2
    assert r["sum_rental_revenue"] == pytest.approx(dp["rental_revenue"].sum())
    assert r["sum_landed_cost"] == pytest.approx(dp["landed_cost"].sum())
    assert r["sum_realised_rv"] == pytest.approx(dp["realised_rv"].sum())
    assert r["sum_service_and_logistics_cost"] == pytest.approx(dp["service_and_logistics_cost"].sum())
    assert r["sum_lifecycle_margin"] == pytest.approx(2.0)
    assert r["mean_lifecycle_margin"] == pytest.approx(1.0)
    closed_landed = dp.loc[dp["is_closed"], "landed_cost"].sum()
    assert r["margin_pct"] == pytest.approx(2.0 / closed_landed)
    mean_of_ratios = dp.loc[dp["is_closed"], "lifecycle_margin_pct_of_landed"].mean()
    assert r["margin_pct"] != pytest.approx(mean_of_ratios)


def test_aggregate_by_channel_groups_unsold_under_none(a):
    dp = _combined_pnl(a)
    agg = aggregate_pnl(dp, "resale_channel").set_index("group_value")
    assert agg.loc["marketplace", "n"] == 2 and agg.loc["marketplace", "n_closed"] == 2
    assert agg.loc["(none)", "n"] == 2 and agg.loc["(none)", "n_closed"] == 0
    assert pd.isna(agg.loc["(none)", "sum_lifecycle_margin"]) and pd.isna(agg.loc["(none)", "margin_pct"])
    for by in AGGREGATE_DIMENSIONS:
        assert len(aggregate_pnl(dp, by)) >= 1
    with pytest.raises(ValueError):
        aggregate_pnl(dp, "supplier")


# ----------------------------------------------------------------------------------------
# assumptions.yaml governance
# ----------------------------------------------------------------------------------------

def test_assumptions_yaml_has_owner_on_every_block_and_no_external_source():
    raw = yaml.safe_load((fx.ROOT / "config" / "assumptions.yaml").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    blocks = raw["blocks"]
    required = {
        "planned_rv_ratio", "depreciation_months", "holding_cost_per_day_eur", "channel_fees",
        "expected_grade_at_return", "expected_return_to_sale_days", "as_is_ratio_fallback", "expected_grade_mix",
        "damage_rate_pa_fallback", "repair_share_fallback", "repair_cost_fallback_eur", "refurb_cost_fallback_eur",
        "logistics_cost_fallback_eur", "billing", "min_n_for_realised_inputs",
    }
    assert required <= set(blocks)
    for key, block in blocks.items():
        assert block.get("owner"), f"block {key} has no owner"
        assert ("value" in block) != ("values" in block), f"block {key} needs exactly one of value / values"
        note = str(block.get("note", "")).lower()
        for marker in ("http", "www.", "according to", "market report", "analyst"):
            assert marker not in note, f"block {key} quotes an external source"
    text = (fx.ROOT / "config" / "assumptions.yaml").read_text(encoding="utf-8")
    assert not text.startswith(chr(0xFEFF)), "UTF-8 BOM"
    assert chr(0x2014) not in text, "no em dashes in prose"


def test_assumptions_loader_reads_nested_channel_fees(a):
    assert a.get("channel_fees", "marketplace")["fee_pct"] == pytest.approx(0.12)
    assert a.get("billing", "pro_rata") is False
    assert a.get("min_n_for_realised_inputs") == 30
    assert a.owner("planned_rv_ratio")


# ----------------------------------------------------------------------------------------
# run_pnl end to end on an in-memory database
# ----------------------------------------------------------------------------------------

def test_run_pnl_writes_three_tables(a):
    from restwert import db

    con = db.connect(":memory:")
    db.create_schema(con)
    cases = [fx.hand_case_1(), fx.hand_case_2(), fx.open_device_case(), fx.replacement_case()]
    frames = {k: pd.concat([c[k] for c in cases], ignore_index=True) for k in cases[0]}
    for table, df in frames.items():
        db.write_df(con, table, df, mode="replace")

    summary = run_pnl(con, AS_OF, a)
    assert summary.command == "pnl"
    assert summary.counts["device_pnl"] == 6
    assert summary.counts["tco_per_model"] == len(TERM_MONTHS)   # one model x the four terms 12, 24, 36, 48
    assert summary.counts["pnl_aggregate"] >= 4
    assert summary.seconds >= 0

    dp = db.read_df(con, "SELECT * FROM device_pnl ORDER BY serial")
    assert len(dp) == 6
    assert set(dp.columns) == set(DEVICE_PNL_COLUMNS)
    by_serial = dp.set_index("serial")
    assert float(by_serial.loc["D-000001", "lifecycle_margin"]) == pytest.approx(30.0)
    assert float(by_serial.loc["D-000002", "lifecycle_margin"]) == pytest.approx(-28.0)
    assert pd.isna(by_serial.loc["D-000020", "lifecycle_margin"])
    assert by_serial.loc["D-000020", "lifecycle_status"] == "in_stock"

    tco = db.read_df(con, "SELECT * FROM tco_per_model ORDER BY term_months")
    assert list(tco["term_months"]) == list(TERM_MONTHS)   # 12, 24, 36, 48
    assert set(tco["inputs_source"]) <= {"realised", "assumptions", "mixed"}

    agg = db.read_df(con, "SELECT * FROM pnl_aggregate")
    assert set(agg["group_by"]) == set(AGGREGATE_DIMENSIONS)

    # a rerun replaces, never appends
    run_pnl(con, AS_OF, a)
    assert db.read_df(con, "SELECT count(*) AS n FROM device_pnl")["n"].iloc[0] == 6


# ----------------------------------------------------------------------------------------
# review fixes: TCO clipping flag and valuation basis, aggregate identity, derecognition
# ----------------------------------------------------------------------------------------

def test_tco_clipping_is_flagged_and_downgrades_inputs_source(a):
    frames = fx.tco_fixture()
    t36 = _tco(frames, a, 36)
    an36 = t36.loc["A-Gen01"]           # android grid stops at month 30, term end is month 37
    assert bool(an36["rv_clipped_to_grid"]) is True
    assert an36["rv_months_at_end"] == 37 and an36["rv_months_used"] == 30
    assert an36["forecast_rv_source"] == "grid_clipped"
    assert an36["inputs_source"] == "mixed", "a clipped residual value must never read as realised or pure assumption"
    p36 = t36.loc["P-Gen01"]            # iphone grid runs to 48: not clipped
    assert bool(p36["rv_clipped_to_grid"]) is False
    assert p36["rv_months_at_end"] == p36["rv_months_used"] == 37
    assert p36["forecast_rv_source"] == "grid" and p36["inputs_source"] == "assumptions"


def test_tco_docstring_names_the_real_grid_horizon():
    """The module docstring states the horizon it reads; it must be the registry's, not a remembered number.

    The 84-month claim ("on the shipped data nothing is clipped") was stale: the ledger carried
    24 open serials at 85 to 94 months; after catalogue round 4 the measured maximum is 110. The horizon is that maximum plus 6 months reserve, rounded
    up to a full year, and the docstring names that number.
    """
    import restwert.pnl.tco as tco_mod
    from restwert.forecast.registry import GRID_MONTHS

    horizon = GRID_MONTHS.stop - 1
    doc = tco_mod.__doc__ or ""
    assert f"The grid runs to {horizon} months" in doc
    assert "runs to 84 months" not in doc and "nothing is clipped" not in doc
    assert horizon >= 110 + 6 and horizon % 12 == 0


def test_tco_planned_ratio_fallback_uses_landed_cost(a):
    frames = fx.tco_fixture()
    frames["rv_grid"] = pd.DataFrame(columns=fx.GRID_COLS)
    t = _tco(frames, a, 24)
    p = t.loc["P-Gen01"]
    assert p["forecast_rv_source"] == "planned_ratio_on_landed_cost"
    assert p["forecast_rv_at_end"] == pytest.approx(0.20 * 850.0)   # landed avg, not purchase avg 820
    assert bool(p["rv_clipped_to_grid"]) is False


def test_tco_fees_use_marketplace_fee_ratio_only(a):
    """Realised fees from buyout (0 %) and As-Is sales must not dilute the fee applied to a marketplace price."""
    frames = fx.tco_fixture()
    rows = []
    for i in range(80):
        ch = "employee_buyout" if i % 2 else "marketplace"
        fee = 0.0 if ch == "employee_buyout" else 30.0
        rows.append(fx.resale("D-000101" if i % 4 else "D-000102", date(2025, 6, 1), 300.0, fee, channel=ch))
    frames["resale"] = fx._frame(fx.RESALE_COLS, rows)
    t = _tco(frames, a, 24).loc["P-Gen01"]
    # 40 marketplace sales at 10 % fee (30 / 300): fees = 10 % of the marketplace forecast, not the 5 % blend
    assert t["expected_channel_fees"] == pytest.approx(round(0.10 * t["forecast_rv_at_end"], 2), abs=0.011)
    assert t["inputs_source"] == "mixed"


def test_aggregate_identity_holds_on_closed_columns(a):
    dp = _combined_pnl(a)
    for by in AGGREGATE_DIMENSIONS:
        agg = aggregate_pnl(dp, by)
        for _, r in agg.iterrows():
            if pd.isna(r["sum_lifecycle_margin"]):
                continue
            ident = r["sum_rental_revenue_closed"] - (r["sum_landed_cost_closed"] - r["sum_realised_rv_closed"]) - r["sum_service_and_logistics_cost_closed"]
            assert r["sum_lifecycle_margin"] == pytest.approx(ident, abs=0.011), (by, r["group_value"])
            assert r["margin_pct"] == pytest.approx(r["sum_lifecycle_margin"] / r["sum_landed_cost_closed"])
    fam = aggregate_pnl(dp, "model_family").set_index("group_value").loc["iphone_like"]
    # all-device columns still include the open devices, closed columns do not
    assert fam["sum_landed_cost"] > fam["sum_landed_cost_closed"]
    assert fam["sum_landed_cost_closed"] == pytest.approx(dp.loc[dp["is_closed"], "landed_cost"].sum())


def test_closed_devices_carry_no_book_value(a):
    ledger = pd.DataFrame([fx.ledger_row("D-000001", date(2026, 5, 31), 28.44)], columns=fx.LEDGER_COLS)
    df = _pnl(fx.hand_case_1(), a, ledger=ledger).set_index("serial")
    sold = df.loc["D-000001"]
    assert bool(sold["is_closed"]) and sold["lifecycle_status"] == "sold"
    assert sold["book_value_sl"] == 0.0 and sold["write_down_cum"] == 0.0 and sold["book_value"] == 0.0
    # open devices keep the straight line
    open_df = _pnl(fx.open_device_case(), a).set_index("serial")
    assert open_df.loc["D-000020", "book_value"] > 0


def test_margin_if_liquidated_today_is_liquidation_not_lifecycle(a):
    frames = fx.open_device_case()
    rv_current = pd.DataFrame([fx.rv_current_row("D-000021", 500.0)], columns=fx.RV_CURRENT_COLS)
    df = _pnl(frames, a, rv_current=rv_current).set_index("serial")
    rented = df.loc["D-000021"]
    # 17 of 36 months billed at 30: 510 revenue; landed 800; RV today 500; no service; 12 % fee
    assert rented["margin_if_liquidated_today"] == pytest.approx(510 - (800 - 500) - 0 - 500 * 0.12)
    assert "margin_open_forecast" not in df.columns
