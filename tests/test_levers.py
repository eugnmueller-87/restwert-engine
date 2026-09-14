"""Tests for restwert.levers (spec v0.2 section 7.8), rule R07 and the advisories ADV03 / ADV04.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The hand-built device ledger has 12 serials and every EUR asserted here was computed by hand
from the numbers in ``_hand_ledger`` (see the comments on each serial). ``lever_reference_min_n``
is set to 3 on the test assumptions so a 12-serial fleet can form references; the serials of
the Lenovo laptop (one of a kind) and of the Apple reseller half-year (two of a kind) show
what "not attributed below min_n" means.

Layout of the fleet (as_of 2026-06-30; all Smartphone slugs launched 2024-03-01):

* S01..S05 Samsung, manufacturer, bought 2025-09-15, net RRP 700, discounts 10/12/15/18/20 %
  (S02 also received a 30.00 price protection credit, so its effective discount is 16.3 %),
  all sold 2026-04-15 (S02 on 2026-06-01) at 210/215/220/225/230 gross; S01..S03 term 24,
  S04..S05 term 36 (closed results 10/20/30 and 120/150: the 36-month term closes better per
  month of term, the L07 reference).
* A01, A02 Apple, reseller, bought 2025-08-01, net RRP 1000, discounts 5/8 %, sold at 350/360;
  A03 Apple, manufacturer, bought 2024-02-01, discount 10 %, in stock since 2026-01-01.
* G01..G03 Google, manufacturer, bought 2025-10-01, net RRP 500, discounts 10/12/14 %, sold
  at 100 each (poor realisation: the manufacturer-mix advisory fires for Google).
* L01 Lenovo laptop, one of a kind, sold: below min_n on every fleet reference and not on
  the grid.

Tests that need the session lake pipeline (``lake_pipeline_db`` from module 6) skip cleanly
when that fixture is not registered yet.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from restwert import db  # noqa: E402
from restwert.config import AssumptionBlock, Assumptions, load_assumptions, load_thresholds  # noqa: E402
from restwert.decisions import registry, runner  # noqa: E402
from restwert.decisions.rules import NO_ACTION_OUTCOMES, decide_channel, decide_purchase_floor  # noqa: E402
from restwert.levers import attribution as att  # noqa: E402
from restwert.levers import references as ref  # noqa: E402
from restwert.levers import summary as summ  # noqa: E402
from restwert.levers.run import run_levers  # noqa: E402
from tests.fixtures import decision_inputs as dfx  # noqa: E402

AS_OF = date(2026, 6, 30)
EM_DASH = "\u2014"  # written as an escape so this file does not trip the repository scan
LAUNCH = date(2024, 3, 1)
APPLE_LAUNCH = date(2024, 9, 1)


# --------------------------------------------------------------------------------------
# hand fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def thr():
    return load_thresholds()


@pytest.fixture(scope="module")
def a():
    """Shipped assumptions with ``lever_reference_min_n = 3`` so a 12-serial fleet forms references."""
    base = load_assumptions()
    blocks = dict(base.blocks)
    blocks["lever_reference_min_n"] = AssumptionBlock(owner="CFO (test)", value=3, note="test")
    return Assumptions(version=base.version, blocks=blocks)


def _serial(
    serial: str,
    *,
    oem: str,
    slug: str,
    catalogue_family: str,
    model_family: str,
    rrp: float,
    launch: date,
    supplier_role: str,
    purchase_date: date,
    discount: float,
    term: int,
    status: str,
    po: str,
    po_line: int,
    gross: float | None = None,
    fees: float | None = None,
    sale_date: date | None = None,
    sellable_date: date | None = None,
    return_date: date | None = None,
    grade_declared: str | None = None,
    grade_inspected: str | None = None,
    grade_out: str | None = None,
    result: float | None = None,
    pp_status: str = "not_applicable",
    pp_claimable: float = 0.0,
    pp_credit: float = 0.0,
    days_return_to_cash: int | None = None,
    contract_end_effective: date | None = None,
) -> dict:
    price = round(rrp * (1 - discount), 2)
    effective = round(price - pp_credit, 2)  # a price protection credit received is a purchase price reduction
    closed = status in ("sold", "scrapped")
    net = None if gross is None else round(gross - (fees or 0.0), 2)
    return {
        "serial": serial,
        "as_of": AS_OF,
        "slug": slug,
        "model_name": slug,
        "oem": oem,
        "catalogue_family": catalogue_family,
        "model_family": model_family,
        "rrp_net_eur": rrp,
        "launch_date": launch,
        "po_number": po,
        "po_line": po_line,
        "supplier_name": oem if supplier_role == "manufacturer" else "IT reseller A (role-only)",
        "supplier_role": supplier_role,
        "order_date": purchase_date - timedelta(days=20),
        "received_at": purchase_date,
        "purchase_date": purchase_date,
        "cohort_month": date(purchase_date.year, purchase_date.month, 1),
        "cohort_quarter": f"{purchase_date.year}-Q{(purchase_date.month - 1) // 3 + 1}",
        "price_protection_days": 30,
        "price_protection_status": pp_status,
        "price_protection_claimable_eur": pp_claimable,
        "price_protection_credit_eur": pp_credit,
        "purchase_price": price,
        "discount_vs_rrp_eur": round(rrp - effective, 2),
        "discount_vs_rrp_pct": (rrp - effective) / rrp,
        "landed_cost": price + 10.0,
        "term_months": term,
        "contract_end_effective": contract_end_effective,
        "return_date": return_date,
        "grade_declared": grade_declared,
        "grade_inspected": grade_inspected,
        "grade_out": grade_out,
        "grade_used": grade_out or grade_inspected or "B",
        "sellable_date": sellable_date,
        "resale_channel": "marketplace" if gross is not None else None,
        "sale_date": sale_date,
        "resale_gross": gross,
        "resale_net": net,
        "days_return_to_sale": (sale_date - return_date).days if (sale_date and return_date) else None,
        "days_return_to_cash": days_return_to_cash,
        "realised_rv": gross if closed else None,
        "lifecycle_result_eur": result if closed else None,
        "result_v01_basis_eur": (result + 5.0) if (closed and result is not None) else None,
        "lifecycle_status": status,
        "is_closed": closed,
        "closed_date": sale_date if closed else None,
        "chain_complete": True,
        "is_synthetic": True,
    }


def _hand_ledger() -> pd.DataFrame:
    sale = date(2026, 4, 15)
    ret = date(2026, 3, 1)
    sellable = date(2026, 3, 15)
    end = date(2026, 3, 1)
    rows = []
    sam = dict(oem="Samsung", slug="sam-p01", catalogue_family="Smartphone", model_family="android_like", rrp=700.0,
               launch=LAUNCH, supplier_role="manufacturer", purchase_date=date(2025, 9, 15), po="PO-2025-000001")
    # S01: discount 10 % (price 630), missed credit 25, declared B inspected C, refurb B, sold 210 gross / 27.70 fees
    rows.append(_serial("S01", **sam, discount=0.10, term=24, status="sold", po_line=1, gross=210.0, fees=27.70,
                        sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="C",
                        grade_out="B", result=10.0, pp_status="missed", pp_claimable=25.0, days_return_to_cash=78,
                        contract_end_effective=end))
    # S02: discount 12 % (price 616), claimed credit 30, sold late on 2026-06-01 (aging), 215 gross
    rows.append(_serial("S02", **sam, discount=0.12, term=24, status="sold", po_line=1, gross=215.0, fees=28.30,
                        sale_date=date(2026, 6, 1), sellable_date=sellable, return_date=ret, grade_declared="B",
                        grade_inspected="B", grade_out="B", result=20.0, pp_status="claimed", pp_credit=30.0,
                        days_return_to_cash=120, contract_end_effective=end))
    # S03: discount 15 % (price 595), 220 gross
    rows.append(_serial("S03", **sam, discount=0.15, term=24, status="sold", po_line=2, gross=220.0, fees=28.90,
                        sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B",
                        grade_out="B", result=30.0, days_return_to_cash=78, contract_end_effective=end))
    # S04: discount 18 % (price 574), price protection window open, term 36, 225 gross, result 120
    rows.append(_serial("S04", **sam, discount=0.18, term=36, status="sold", po_line=2, gross=225.0, fees=29.50,
                        sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B",
                        grade_out="B", result=120.0, pp_status="open", pp_claimable=15.0, days_return_to_cash=78,
                        contract_end_effective=end))
    # S05: discount 20 % (price 560), beats the p75 reference, term 36, 230 gross, result 150
    rows.append(_serial("S05", **sam, discount=0.20, term=36, status="sold", po_line=3, gross=230.0, fees=30.10,
                        sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B",
                        grade_out="B", result=150.0, days_return_to_cash=78, contract_end_effective=end))
    apl = dict(oem="Apple", slug="apl-p01", catalogue_family="Smartphone", model_family="iphone_like", rrp=1000.0,
               launch=LAUNCH)
    # A01, A02: reseller, 2025-H2 (only two: the half-year group is below min_n, the oem group of three is used)
    rows.append(_serial("A01", **apl, supplier_role="reseller", purchase_date=date(2025, 8, 1), discount=0.05, term=24,
                        status="sold", po="PO-2025-000002", po_line=1, gross=350.0, fees=44.50, sale_date=sale,
                        sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B", grade_out="B",
                        result=40.0, days_return_to_cash=78, contract_end_effective=end))
    rows.append(_serial("A02", **apl, supplier_role="reseller", purchase_date=date(2025, 8, 1), discount=0.08, term=24,
                        status="sold", po="PO-2025-000002", po_line=1, gross=360.0, fees=45.70, sale_date=sale,
                        sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B", grade_out="B",
                        result=50.0, days_return_to_cash=78, contract_end_effective=end))
    # A03: manufacturer, 2024-H1, in stock since 2026-01-01 (aging lever measured at as_of)
    rows.append(_serial("A03", oem="Apple", slug="apl-p02", catalogue_family="Smartphone", model_family="iphone_like",
                        rrp=1000.0, launch=APPLE_LAUNCH, supplier_role="manufacturer", purchase_date=date(2024, 2, 1),
                        discount=0.10, term=24, status="in_stock", po="PO-2024-000001", po_line=1,
                        sellable_date=date(2026, 1, 1), return_date=date(2025, 12, 20), grade_declared="B",
                        grade_inspected="B", grade_out="B", contract_end_effective=date(2025, 12, 15)))
    goo = dict(oem="Google", slug="goo-p01", catalogue_family="Smartphone", model_family="android_like", rrp=500.0,
               launch=LAUNCH, supplier_role="manufacturer", purchase_date=date(2025, 10, 1), po="PO-2025-000003")
    for i, (disc, term, result) in enumerate([(0.10, 36, 180.0), (0.12, 24, 40.0), (0.14, 24, 50.0)], start=1):
        rows.append(_serial(f"G0{i}", **goo, discount=disc, term=term, status="sold", po_line=1, gross=100.0, fees=14.50,
                            sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B",
                            grade_inspected="B", grade_out="B", result=result, days_return_to_cash=78,
                            contract_end_effective=end))
    # L01: the only laptop, the only Lenovo, not on the grid
    rows.append(_serial("L01", oem="Lenovo", slug="len-p01", catalogue_family="Laptop", model_family="laptop_like",
                        rrp=1200.0, launch=date(2024, 5, 1), supplier_role="manufacturer", purchase_date=date(2025, 3, 1),
                        discount=0.20, term=36, status="sold", po="PO-2025-000004", po_line=1, gross=500.0, fees=62.50,
                        sale_date=sale, sellable_date=sellable, return_date=ret, grade_declared="B", grade_inspected="B",
                        grade_out="B", result=-30.0, days_return_to_cash=78, contract_end_effective=end))
    return pd.DataFrame(rows)


def _hand_grid() -> pd.DataFrame:
    """Grid ratios: ``base[grade] - 0.005 x months`` rounded to 4 decimals, months 0..72, two models."""
    base = {"A": 0.55, "B": 0.50, "C": 0.42, "D": 0.20}
    rows = []
    for model, family in (("sam-p01", "android_like"), ("apl-p01", "iphone_like"), ("apl-p02", "iphone_like"), ("goo-p01", "android_like")):
        for grade, b in base.items():
            for m in range(0, 73):
                rows.append({"run_id": "rv-2026-06-30", "model": model, "model_family": family, "grade": grade,
                             "months_since_launch": m, "forecast_rv_ratio": round(b - 0.005 * m, 4)})
    return pd.DataFrame(rows)


def _hand_lines() -> pd.DataFrame:
    """Repair lines: S01 one repair of 120.00 at month 20 (2025-11-01); S03 one repair of 30.00."""
    return pd.DataFrame(
        [
            {"line_id": "l1", "serial": "S01", "line_type": "repair", "line_class": "cost", "amount_eur": -120.0,
             "event_date": date(2025, 11, 1), "source_ref": "servicedesk:TK-1"},
            {"line_id": "l2", "serial": "S03", "line_type": "repair", "line_class": "cost", "amount_eur": -30.0,
             "event_date": date(2025, 11, 1), "source_ref": "servicedesk:TK-2"},
            {"line_id": "l3", "serial": "S01", "line_type": "rental_revenue", "line_class": "revenue", "amount_eur": 40.0,
             "event_date": date(2025, 11, 1), "source_ref": "portal:RI-1"},
        ]
    )


def _hand_full_lines(dl: pd.DataFrame) -> pd.DataFrame:
    """The hand repair and rent lines plus, per closed serial, the purchase line, the credit line and one balancing
    rental line, so that the non-bridge lines sum to ``result_v01_basis_eur`` exactly as a real ledger would."""
    base = _hand_lines()
    rows = base.to_dict("records")
    for r in dl.to_dict("records"):
        if not r["is_closed"]:
            continue
        serial = r["serial"]
        rows.append({"line_id": f"pp-{serial}", "serial": serial, "line_type": "purchase_price", "line_class": "cost",
                     "amount_eur": -float(r["purchase_price"]), "event_date": r["purchase_date"], "source_ref": f"erp:INV-{serial}"})
        if float(r["price_protection_credit_eur"] or 0.0) > 0:
            rows.append({"line_id": f"cr-{serial}", "serial": serial, "line_type": "price_protection_credit", "line_class": "revenue",
                         "amount_eur": float(r["price_protection_credit_eur"]), "event_date": r["purchase_date"], "source_ref": f"erp:CR-{serial}"})
        existing = sum(float(x["amount_eur"]) for x in base.to_dict("records") if x["serial"] == serial)
        balance = float(r["result_v01_basis_eur"]) + float(r["purchase_price"]) - existing
        rows.append({"line_id": f"bal-{serial}", "serial": serial, "line_type": "rental_revenue", "line_class": "revenue",
                     "amount_eur": round(balance, 2), "event_date": r["sale_date"], "source_ref": f"portal:BAL-{serial}"})
    return pd.DataFrame(rows)


def _hand_record() -> pd.DataFrame:
    """Forecast of record: 260 on S01 with buyout factor 0.90 and b2b 0.97; A02 missing; the rest 250."""
    rows = []
    for s in ("S01", "S02", "S03", "S04", "S05", "A01", "A02", "G01", "G02", "G03", "L01"):
        rows.append({"serial": s, "return_date": date(2026, 3, 1), "run_id": "rv-2026-02-28", "run_as_of": date(2026, 2, 28),
                     "grade_used": "B", "forecast_rv": 260.0 if s == "S01" else 250.0,
                     "channel_factor_employee_buyout": 0.90, "channel_factor_b2b_wholesale": 0.97,
                     "is_missing": s == "A02", "missing_reason": "no run before return_date" if s == "A02" else None})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def dl() -> pd.DataFrame:
    return _hand_ledger()


@pytest.fixture(scope="module")
def grid() -> pd.DataFrame:
    return _hand_grid()


@pytest.fixture(scope="module")
def per_device(dl, grid, thr, a) -> pd.DataFrame:
    return att.attribute_all(dl, _hand_lines(), None, _hand_record(), grid, thr, a, AS_OF)


def _row(per_device: pd.DataFrame, serial: str, lever: str) -> pd.Series:
    sub = per_device[(per_device["serial"] == serial) & (per_device["lever_id"] == lever)]
    assert len(sub) == 1, (serial, lever)
    return sub.iloc[0]


def _cf(row: pd.Series) -> dict:
    return json.loads(row["counterfactual_json"])


def _levers_db(dl: pd.DataFrame, grid: pd.DataFrame):
    """In-memory DuckDB with the v0.1 schema plus the hand silver tables (typed from the frames)."""
    con = db.connect(":memory:")
    db.create_schema(con)
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    frame = dl.copy()
    for c in ("as_of", "launch_date", "order_date", "received_at", "purchase_date", "cohort_month", "contract_end_effective",
              "return_date", "sellable_date", "sale_date", "closed_date"):
        frame[c] = pd.to_datetime(frame[c])
    con.register("_dl", frame)
    con.execute('CREATE TABLE "silver"."device_ledger" AS SELECT * FROM _dl')
    con.unregister("_dl")
    lines = _hand_full_lines(dl)
    lines["event_date"] = pd.to_datetime(lines["event_date"])
    con.register("_ll", lines)
    con.execute('CREATE TABLE "silver"."ledger_lines" AS SELECT * FROM _ll')
    con.unregister("_ll")
    db.write_df(con, "rv_forecast_grid", grid.assign(n_launches_since=0, storage_gb=128, ratio_low=0.0, ratio_high=1.0,
                                                     list_price=0.0, forecast_rv_on_list=0.0, n_train=10, fit_quality="family"))
    db.write_df(con, "rv_forecast_of_record", _hand_record())
    return con


# --------------------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------------------


def test_half_year_and_age_bucket():
    assert ref.half_year(date(2024, 1, 1)) == "2024-H1"
    assert ref.half_year(date(2024, 6, 30)) == "2024-H1"
    assert ref.half_year(date(2024, 7, 1)) == "2024-H2"
    assert ref.age_bucket(27.3) == "24-30"
    assert ref.age_bucket(0.0) == "0-6"
    assert ref.age_bucket(30.0) == "30-36"


def test_reference_discount_p75_group_then_oem_then_none(dl):
    r = ref.reference_discount(dl, min_n=3).set_index("serial")
    # Samsung: five in (Samsung, manufacturer, 2025-H2): p75 of 10/12/15/18/20 % = 18 %
    assert r.loc["S01", "reference_source"] == "oem+role+half_year"
    assert r.loc["S01", "n_reference"] == 5
    assert r.loc["S01", "ref_pct"] == pytest.approx(0.18)
    # Apple: reseller half-year has two, the oem over all time has three -> p75 of 5/8/10 % = 9 %
    assert r.loc["A01", "reference_source"] == "oem"
    assert r.loc["A01", "n_reference"] == 3
    assert r.loc["A01", "ref_pct"] == pytest.approx(0.09)
    # Lenovo: one of a kind -> not attributed
    assert r.loc["L01", "reference_source"] == "none"
    assert pd.isna(r.loc["L01", "ref_pct"])


def test_family_and_term_medians(dl):
    fam = ref.family_realisation_median(dl, min_n=6)
    assert list(fam.columns) == list(ref.FAMILY_REALISATION_COLUMNS)
    sp = fam[fam["catalogue_family"] == "Smartphone"]
    assert len(sp) == 1 and int(sp.iloc[0]["n"]) == 10
    assert sp.iloc[0]["age_bucket"] == "24-30" and sp.iloc[0]["grade_at_sale"] == "B"
    assert float(sp.iloc[0]["median_ratio"]) == pytest.approx((215 + 220) / 1400)
    assert "Laptop" not in set(fam["catalogue_family"])
    term = ref.term_result_medians(dl, min_n=3)
    t = term.set_index(["model_family", "purchase_half_year", "term_months"])["median_result_eur"]
    assert t[("android_like", "2025-H2", 24)] == 30.0  # 10, 20, 30, 40, 50
    assert t[("android_like", "2025-H2", 36)] == 150.0  # 120, 150, 180
    per_month = term.set_index(["model_family", "purchase_half_year", "term_months"])["median_result_per_month_eur"]
    assert per_month[("android_like", "2025-H2", 24)] == pytest.approx(30.0 / 24, abs=1e-4)
    assert per_month[("android_like", "2025-H2", 36)] == pytest.approx(150.0 / 36, abs=1e-4)
    assert ("iphone_like", "2025-H2", 24) not in t.index  # only two


def test_grid_ratio_matches_grid_lookup_and_clips(grid):
    from restwert.forecast.registry import grid_lookup

    idx = ref.GridIndex(grid)
    assert ref.grid_ratio(idx, "sam-p01", "B", 24) == pytest.approx(0.38)
    monotone, ratios = ref.grid_grades_monotone(idx, "sam-p01", 24)
    assert monotone and ratios == {"A": pytest.approx(0.43), "B": pytest.approx(0.38), "C": pytest.approx(0.30), "D": pytest.approx(0.08)}
    assert ref.grid_fit_quality(idx, "sam-p01", "B") is None  # the hand grid carries no fit_quality
    bad = grid.copy()
    bad.loc[(bad["model"] == "sam-p01") & (bad["grade"] == "B"), "forecast_rv_ratio"] += 0.10  # B above A
    assert ref.grid_grades_monotone(ref.GridIndex(bad), "sam-p01", 24)[0] is False
    assert ref.grid_ratio(grid, "sam-p01", "B", 24) == grid_lookup(grid, "sam-p01", "B", 24)
    assert ref.grid_ratio(idx, "sam-p01", "C", 200) == grid_lookup(grid, "sam-p01", "C", 200)  # clipped to 72
    assert ref.grid_ratio(idx, "len-p01", "B", 24) is None


# --------------------------------------------------------------------------------------
# one test per lever
# --------------------------------------------------------------------------------------


def test_l01_purchase_discount_exact_and_not_attributed(per_device):
    s01 = _row(per_device, "S01", "L01")
    # reference price 700 x (1 - 0.18) = 574.00; paid 630.00 -> 56.00 left on the table
    assert bool(s01["is_attributed"]) and s01["basis"] == "fleet" and bool(s01["additive"])
    assert float(s01["delta_eur"]) == pytest.approx(56.00, abs=0.005)
    assert float(s01["reference_value"]) == pytest.approx(0.18)
    assert int(s01["n_reference"]) == 5 and s01["event_date"].date() == date(2025, 9, 15)
    cf = _cf(s01)
    assert cf["reference_purchase_price"] == 574.0 and cf["purchase_price"] == 630.0 and cf["n"] == 5
    # S02 paid 616 and received a 30.00 price protection credit: effective 586.00 against 574.00 -> 12.00, not 42.00
    s02 = _row(per_device, "S02", "L01")
    assert float(s02["delta_eur"]) == pytest.approx(12.00, abs=0.005)
    assert _cf(s02)["purchase_price"] == 586.0 and _cf(s02)["purchase_price_invoiced"] == 616.0 and _cf(s02)["price_protection_credit_eur"] == 30.0
    # S05 beat the reference: floored at 0, still attributed
    s05 = _row(per_device, "S05", "L01")
    assert bool(s05["is_attributed"]) and float(s05["delta_eur"]) == 0.0
    # Apple falls back to the oem group: 1000 x (1 - 0.09) = 910; A01 paid 950 -> 40.00
    a01 = _row(per_device, "A01", "L01")
    assert a01["reference_source"] == "fleet:p75:oem" and float(a01["delta_eur"]) == pytest.approx(40.0, abs=0.005)
    # Lenovo: below min_n on every group -> not attributed, delta NULL
    l01 = _row(per_device, "L01", "L01")
    assert not bool(l01["is_attributed"]) and pd.isna(l01["delta_eur"]) and int(l01["n_reference"]) == 0
    assert "min_n" in _cf(l01)["not_attributed_reason"]


def test_l02_price_protection_missed_claimed_na_open(per_device):
    assert float(_row(per_device, "S01", "L02")["delta_eur"]) == 25.0
    assert _row(per_device, "S01", "L02")["reference_source"] == "po_line:missed"
    s02 = _row(per_device, "S02", "L02")
    assert float(s02["delta_eur"]) == 0.0 and s02["reference_source"] == "claimed" and bool(s02["is_attributed"])
    s03 = _row(per_device, "S03", "L02")
    assert float(s03["delta_eur"]) == 0.0 and s03["reference_source"] == "n/a"
    s04 = _row(per_device, "S04", "L02")
    assert not bool(s04["is_attributed"]) and s04["reference_source"] == "open" and pd.isna(s04["delta_eur"])


def test_l03_channel_choice_exact_and_missing_record(per_device):
    s01 = _row(per_device, "S01", "L03")
    # nets at the record: buyout 260 x 0.90 x (1 - 0) - 0.30 x 14 = 229.80; marketplace 260 x (1 - 0.12) - 2.50 - 0.30 x 28 = 217.90;
    # b2b 260 x 0.97 x (1 - 0.03) - 0.30 x 45 = 231.13; sold on the marketplace -> 231.13 - 217.90 = 13.23.
    # The realised 210.00 against the record 260.00 is forecast accuracy and stays out of the lever.
    assert bool(s01["is_attributed"]) and s01["basis"] == "forecast_of_record" and not bool(s01["additive"])
    assert float(s01["delta_eur"]) == pytest.approx(13.23, abs=0.005)
    assert float(s01["reference_value"]) == pytest.approx(231.13, abs=0.005)
    assert float(s01["actual_value"]) == pytest.approx(217.90, abs=0.005)
    cf = _cf(s01)
    assert cf["best_channel"] == "b2b_wholesale" and cf["buyout_eligible"] is True
    assert cf["admissible_channels"] == ["employee_buyout", "marketplace", "b2b_wholesale"]
    assert cf["net_by_channel"]["employee_buyout"] == pytest.approx(229.80, abs=0.005)
    assert cf["realised_vs_record_gap_eur"] == pytest.approx(210.0 - 260.0, abs=0.005)
    assert int(s01["n_reference"]) == 3 and s01["event_date"].date() == date(2026, 4, 15)
    # a device sold through the best admissible channel carries exactly 0 (same basis on both sides)
    forced = pd.DataFrame([{**r, "resale_channel": "b2b_wholesale"} for r in _hand_ledger().to_dict("records") if r["serial"] == "S01"])
    zero = att.lever_channel(forced.iloc[0], _hand_record().set_index("serial").loc["S01"].to_dict() | {"serial": "S01"},
                             load_thresholds(), load_assumptions(), AS_OF)
    assert zero.is_attributed and zero.delta_eur == 0.0 and zero.counterfactual["actual_channel"] == "b2b_wholesale"
    a02 = _row(per_device, "A02", "L03")
    assert not bool(a02["is_attributed"]) and a02["reference_source"] == "forecast_of_record:missing"
    # an in-stock serial is not eligible for L03 at all
    assert len(per_device[(per_device["serial"] == "A03") & (per_device["lever_id"] == "L03")]) == 0


def test_l04_grade_and_repair_exact(per_device):
    s01 = _row(per_device, "S01", "L04")
    # month 24 at return: B 0.38, C 0.30 -> 630 x 0.08 = 50.40 (declared B, inspected C)
    # repair 120.00 at month 20, grade_used B: limit 0.35 x 630 x 0.40 = 88.20 -> excess 31.80
    assert bool(s01["is_attributed"]) and s01["basis"] == "grid"
    assert float(s01["delta_eur"]) == pytest.approx(50.40 + 31.80, abs=0.005)
    cf = _cf(s01)
    assert cf["months_since_launch_at_return"] == 24 and cf["grade_part_eur"] == pytest.approx(50.40, abs=0.005)
    assert cf["repairs"][0]["months_since_launch"] == 20 and cf["repairs"][0]["excess_eur"] == pytest.approx(31.80, abs=0.005)
    assert cf["repair_max_share_of_rv"] == 0.35 and "Service Operations" in cf["repair_max_share_owner"]
    assert s01["event_date"].date() == date(2026, 4, 15)
    # S03: same grades, repair 30.00 below the limit -> 0
    assert float(_row(per_device, "S03", "L04")["delta_eur"]) == 0.0
    # a grid that values grade C above grade B at the return month cannot price the grade difference: refused, not guessed
    bad = _hand_grid()
    bad.loc[(bad["model"] == "sam-p01") & (bad["grade"] == "C"), "forecast_rv_ratio"] += 0.20
    s01_row = _hand_ledger().set_index("serial").loc["S01"].to_dict() | {"serial": "S01"}
    refused = att.lever_grade_repair(s01_row, _hand_lines(), ref.GridIndex(bad), load_thresholds(), AS_OF)
    assert not refused.is_attributed and refused.delta_eur is None
    assert "not monotone" in refused.counterfactual["not_attributed_reason"]
    assert refused.counterfactual["repair_part_eur"] == pytest.approx(31.80, abs=0.005)  # kept for reading
    # an unsupported grade (the As-Is fallback) is refused too
    unsupported = _hand_grid().assign(fit_quality="family")
    unsupported.loc[unsupported["grade"] == "C", "fit_quality"] = "unsupported_grade"
    refused2 = att.lever_grade_repair(s01_row, _hand_lines(), ref.GridIndex(unsupported), load_thresholds(), AS_OF)
    assert not refused2.is_attributed and "unsupported grade" in refused2.counterfactual["not_attributed_reason"]
    # same declared and inspected grade: nothing to price, the grid order does not matter
    s03_row = _hand_ledger().set_index("serial").loc["S03"].to_dict() | {"serial": "S03"}
    assert att.lever_grade_repair(s03_row, _hand_lines(), ref.GridIndex(bad), load_thresholds(), AS_OF).is_attributed
    # Lenovo not on the grid -> not attributed; the in-stock A03 is not closed -> no row
    assert not bool(_row(per_device, "L01", "L04")["is_attributed"])
    assert len(per_device[(per_device["serial"] == "A03") & (per_device["lever_id"] == "L04")]) == 0


def test_l05_aging_exact_for_sold_and_in_stock(per_device):
    # S01: 31 days sellable to sold, expected 35 -> excess 0; months 26 vs 25 on grade B: 0.37 vs 0.375 -> 0
    assert float(_row(per_device, "S01", "L05")["delta_eur"]) == 0.0
    # S02: 78 days -> excess 43 x 0.30 = 12.90; months 26 (0.37) vs 27 (0.365): 616 x 0.005 = 3.08 -> 15.98
    s02 = _row(per_device, "S02", "L05")
    assert float(s02["delta_eur"]) == pytest.approx(15.98, abs=0.005)
    cf = _cf(s02)
    assert cf["excess_days"] == 43 and cf["months_expected"] == 26 and cf["months_actual"] == 27
    assert s02["event_date"].date() == date(2026, 6, 1)
    # A03 in stock since 2026-01-01: 180 days at as_of, expected 30 -> 150 x 0.30 = 45.00;
    # months 17 (0.415) vs 22 (0.39) on grade B: 900 x 0.025 = 22.50 -> 67.50, event date = as_of
    a03 = _row(per_device, "A03", "L05")
    assert float(a03["delta_eur"]) == pytest.approx(67.50, abs=0.005)
    assert a03["event_date"].date() == AS_OF and _cf(a03)["lifecycle_status"] == "in_stock"
    assert not bool(_row(per_device, "L01", "L05")["is_attributed"])


def test_l06_manufacturer_mix_exact_and_negative_allowed(per_device):
    # family median over 10 smartphone sales = (215 + 220) / 1400 = 0.310714
    s01 = _row(per_device, "S01", "L06")
    assert bool(s01["is_attributed"]) and int(s01["n_reference"]) == 10
    assert float(s01["delta_eur"]) == pytest.approx(217.5 - 210.0, abs=0.005)  # 7.50
    g01 = _row(per_device, "G01", "L06")
    assert float(g01["delta_eur"]) == pytest.approx((0.3107142857 - 0.2) * 500, abs=0.005)  # 55.36
    a01 = _row(per_device, "A01", "L06")
    assert float(a01["delta_eur"]) == pytest.approx((0.3107142857 - 0.35) * 1000, abs=0.005)  # -39.29, negative allowed
    assert float(a01["delta_eur"]) < 0
    l01 = _row(per_device, "L01", "L06")
    assert not bool(l01["is_attributed"]) and _cf(l01)["catalogue_family"] == "Laptop"


def test_l07_term_length_same_value_on_the_cohort(per_device):
    # android_like 2025-H2: term 24 median 30 / 24 = 1.25 per month (n 5), term 36 median 150 / 36 = 4.1667 per month (n 3)
    # -> (4.1667 - 1.25) x 24 = 70.00 on every 24-month serial, 0 on the 36-month ones ((1.25 - 4.1667) x 36 < 0)
    for s in ("S01", "S02", "S03", "G02", "G03"):
        r = _row(per_device, s, "L07")
        assert bool(r["is_attributed"]) and float(r["delta_eur"]) == pytest.approx(70.0, abs=0.005), s
        cf = _cf(r)
        assert cf["other_term"] == 36 and cf["gap_signed_eur"] == pytest.approx(70.0, abs=0.005)
        assert cf["median_result_per_month_other_term"] == pytest.approx(150.0 / 36, abs=1e-4) and cf["summary_basis"] == "per_cohort"
    for s in ("S04", "S05", "G01"):
        r = _row(per_device, s, "L07")
        assert float(r["delta_eur"]) == 0.0 and _cf(r)["gap_signed_eur"] == pytest.approx(-105.0, abs=0.005), s
    # iphone_like 2025-H2 has only one term -> not attributed
    a01 = _row(per_device, "A01", "L07")
    assert not bool(a01["is_attributed"]) and _cf(a01)["terms_with_reference"] == []
    assert _row(per_device, "S01", "L07")["event_date"].date() == date(2026, 4, 15)


def _term_ref(rows: dict[int, tuple[float, int]], family: str = "android_like", hy: str = "2025-H2") -> pd.DataFrame:
    """A ``term_result_medians`` frame from ``{term: (median_result_eur, n)}``; per month = median / term."""
    return pd.DataFrame(
        [
            {"model_family": family, "purchase_half_year": hy, "term_months": t, "median_result_eur": med,
             "median_result_per_month_eur": med / t, "n": n}
            for t, (med, n) in rows.items()
        ],
        columns=list(ref.TERM_MEDIAN_COLUMNS),
    )


def _closed_row(term: int, family: str = "android_like") -> dict:
    return {"serial": f"T{term}", "is_closed": True, "closed_date": date(2026, 4, 15), "purchase_date": date(2025, 9, 15),
            "term_months": term, "model_family": family}


def test_l07_picks_the_best_other_term_among_three_and_four_terms():
    """The reference is the best OTHER term by median result per month, whatever the number of terms in the cohort."""
    # four terms; per month: 12 -> 2.0, 24 -> 1.25, 36 -> 4.1667, 48 -> 3.0
    four = _term_ref({12: (24.0, 4), 24: (30.0, 5), 36: (150.0, 3), 48: (144.0, 6)})
    idx = att.term_index(four)
    assert set(idx[("android_like", "2025-H2")]) == {12, 24, 36, 48}
    r24 = att.lever_term(_closed_row(24), four)
    assert r24.is_attributed and r24.counterfactual["other_term"] == 36
    assert r24.delta_eur == pytest.approx((150.0 / 36 - 30.0 / 24) * 24, abs=0.005)  # 70.00
    r12 = att.lever_term(_closed_row(12), idx)
    assert r12.counterfactual["other_term"] == 36 and r12.delta_eur == pytest.approx((150.0 / 36 - 2.0) * 12, abs=0.005)
    r48 = att.lever_term(_closed_row(48), idx)
    assert r48.counterfactual["other_term"] == 36 and r48.delta_eur == pytest.approx((150.0 / 36 - 3.0) * 48, abs=0.005)
    r36 = att.lever_term(_closed_row(36), idx)  # the best term itself: the best other is 48, the gap is negative, floored at 0
    assert r36.is_attributed and r36.counterfactual["other_term"] == 48 and r36.delta_eur == 0.0
    assert r36.counterfactual["gap_signed_eur"] == pytest.approx((3.0 - 150.0 / 36) * 36, abs=0.005)
    assert r36.reference_source == "fleet:median_per_month:term_48"
    # three terms: 12 -> 2.0, 24 -> 1.25, 48 -> 3.0; the 36 rows are absent (below min_n)
    three = _term_ref({12: (24.0, 4), 24: (30.0, 5), 48: (144.0, 6)})
    r24 = att.lever_term(_closed_row(24), three)
    assert r24.counterfactual["other_term"] == 48 and r24.delta_eur == pytest.approx((3.0 - 1.25) * 24, abs=0.005)
    r36 = att.lever_term(_closed_row(36), three)  # this term has no reference -> not attributed, the other terms are listed
    assert not r36.is_attributed and r36.counterfactual["terms_with_reference"] == [12, 24, 48]
    # a tie on the per-month median goes to the lower term (deterministic order)
    tie = _term_ref({12: (24.0, 4), 24: (48.0, 5), 48: (96.0, 6)})
    assert att.lever_term(_closed_row(12), tie).counterfactual["other_term"] == 24


def test_adv04_payload_carries_every_term_of_the_cohort(thr):
    """ADV04 names the better term and the n of 12, 24, 36 and 48 (0 when absent) with four terms in a cohort."""
    from restwert.levers.run import term_gap_advisories

    four = _term_ref({12: (24.0, 4), 24: (30.0, 5), 36: (150.0, 3), 48: (144.0, 6)})
    rows = []
    for term in (12, 24, 36, 48):
        res = att.lever_term(_closed_row(term), four)
        rows.append({"serial": f"T{term}", "lever_id": "L07", "is_attributed": res.is_attributed,
                     "delta_eur": res.delta_eur, "counterfactual_json": json.dumps(res.counterfactual)})
    adv = term_gap_advisories(pd.DataFrame(rows), thr, AS_OF, "lv-test")
    assert len(adv) == 1
    payload = json.loads(adv.iloc[0]["payload_json"])
    assert payload["better_term"] == 36
    # the widest scaled gap in the cohort sits on the 24-month term here (per-month gap times 24); the note says so
    assert payload["gap_eur"] == pytest.approx((150.0 / 36 - 1.25) * 24, abs=0.005)
    assert payload["vs_term"] == 24 and payload["gap_horizon_months"] == 24
    assert (payload["n_12"], payload["n_24"], payload["n_36"], payload["n_48"]) == (4, 5, 3, 6)
    note = str(adv.iloc[0]["note"])
    assert "the 36-month term closed" in note
    assert "better than the 24-month term (the widest gap in the cohort, per 24 months of term" in note
    assert "best other term" not in note, "the gap is against the term with the widest scaled gap, not the best other one"
    # a cohort with two terms still reports all four n keys (absent terms as 0)
    two = _term_ref({24: (30.0, 5), 36: (150.0, 3)})
    rows = []
    for term in (24, 36):
        res = att.lever_term(_closed_row(term), two)
        rows.append({"serial": f"T{term}", "lever_id": "L07", "is_attributed": res.is_attributed,
                     "delta_eur": res.delta_eur, "counterfactual_json": json.dumps(res.counterfactual)})
    payload = json.loads(term_gap_advisories(pd.DataFrame(rows), thr, AS_OF, "lv-test").iloc[0]["payload_json"])
    assert (payload["n_12"], payload["n_24"], payload["n_36"], payload["n_48"]) == (0, 5, 3, 0)
    assert payload["better_term"] == 36 and payload["vs_term"] == 24 and payload["gap_horizon_months"] == 24


def test_per_device_frame_shape(per_device):
    assert list(per_device.columns) == list(att.PER_DEVICE_COLUMNS)
    assert set(per_device["lever_id"]) == set(att.LEVERS)
    assert not per_device.duplicated(["serial", "lever_id"]).any()
    assert per_device.loc[per_device["is_attributed"], "delta_eur"].notna().all()
    assert per_device.loc[~per_device["is_attributed"], "delta_eur"].isna().all()
    assert set(per_device["basis"]) <= {"fleet", "forecast_of_record", "grid"}
    for r in per_device.itertuples(index=False):
        json.loads(r.counterfactual_json)


# --------------------------------------------------------------------------------------
# admissible channels, additivity, summary
# --------------------------------------------------------------------------------------


def test_admissible_channels_matches_decide_channel_rules(thr):
    days = {"employee_buyout": 14.0, "marketplace": 28.0, "b2b_wholesale": 45.0, "as_is": 30.0}

    def _r02_allowed(grade: str, eligible: bool, d: dict) -> list[str]:
        rec = decide_channel(serial="X", family="android_like", grade=grade,
                             forecast_rv_by_channel={c: 100.0 for c in d}, fee_pct={}, fee_fixed_eur={},
                             days_to_cash=d, holding_cost_per_day=0.3, buyout_eligible=eligible, thr=thr, as_of=AS_OF,
                             run_id="t")
        return sorted(rec.inputs["allowed_channels"])

    for grade, eligible, d in (
        ("B", True, days),
        ("B", False, days),
        ("D", True, days),
        ("A", True, {**days, "b2b_wholesale": 70.0}),
        ("B", False, {"marketplace": 90.0, "b2b_wholesale": 100.0}),
    ):
        mine = sorted(ref.admissible_channels(grade, eligible, d, thr, AS_OF))
        assert mine == _r02_allowed(grade, eligible, d), (grade, eligible, d)
    assert ref.admissible_channels("D", True, days, thr, AS_OF) == ["as_is"]
    assert ref.admissible_channels("B", False, days, thr, AS_OF) == ["marketplace", "b2b_wholesale"]


def test_check_additivity_identity_holds_and_detects_a_break(dl, per_device):
    ok = att.check_additivity(dl, per_device)
    assert list(ok.columns) == list(att.ADDITIVITY_COLUMNS) and len(ok) == 0 and ok.attrs["lines_used"] is False
    broken = per_device.copy()
    idx = broken[(broken["serial"] == "S01") & (broken["lever_id"] == "L01")].index[0]
    broken.loc[idx, "delta_eur"] = float(broken.loc[idx, "delta_eur"]) + 1.0
    bad = att.check_additivity(dl, broken)
    assert len(bad) == 1 and bad.iloc[0]["serial"] == "S01" and bad.iloc[0]["diff"] == pytest.approx(1.0)
    # with the lines the right side is rebuilt from silver.ledger_lines: it holds on consistent lines and it catches
    # a device ledger whose basis or purchase price drifted from its lines (something the formula test cannot see)
    lines = _hand_full_lines(dl)
    with_lines = att.check_additivity(dl, per_device, lines)
    assert len(with_lines) == 0 and with_lines.attrs["lines_used"] is True
    drifted = dl.copy()
    drifted.loc[drifted["serial"] == "S03", "result_v01_basis_eur"] += 2.0
    assert len(att.check_additivity(drifted, per_device)) == 0  # the formula test is blind to it
    caught = att.check_additivity(drifted, per_device, lines)
    assert len(caught) == 1 and caught.iloc[0]["serial"] == "S03" and caught.iloc[0]["diff"] == pytest.approx(2.0)
    wrong_price = lines.copy()
    wrong_price.loc[(wrong_price["serial"] == "S02") & (wrong_price["line_type"] == "purchase_price"), "amount_eur"] -= 5.0
    mismatch = att.check_additivity(dl, per_device, wrong_price)
    assert len(mismatch) == 1 and mismatch.iloc[0]["serial"] == "S02" and mismatch.iloc[0]["diff"] == pytest.approx(-5.0)


def test_levers_summary_has_owner_rule_and_no_total_row(dl, per_device, thr):
    s = summ.levers_summary(per_device, dl, thr, AS_OF)
    assert list(s.columns) == list(summ.SUMMARY_COLUMNS)
    assert len(s) == 7 and set(s["lever_id"]) == set(att.LEVERS)
    assert "total" not in {str(x).lower() for x in s["lever_id"]} and "total" not in {str(x).lower() for x in s["lever_name"]}
    assert sorted(s["rank"].tolist()) == list(range(1, 8))
    assert list(s["rank"]) == sorted(s["rank"]) and s["eur_fleet_per_year"].fillna(-1).is_monotonic_decreasing
    by = s.set_index("lever_id")
    for lever_id, spec in att.LEVERS.items():
        row = by.loc[lever_id]
        assert row["rule_id"] == spec.rule_id and row["reference_sentence"] == spec.reference_sentence
        sub = "Samsung" if spec.threshold_sub == "oem" else "android_like" if spec.threshold_sub == "model_family" else None
        t = thr.get(spec.threshold_key, sub, as_of=AS_OF)
        assert row["threshold_owner"] == t.owner and row["threshold_unit"] == t.unit
    # L01's per-oem threshold resolves for the oem with the largest yearly EUR (Samsung: 56 + 12 + 21 = 89)
    assert by.loc["L01", "threshold_key"] == "purchase_discount_floor_pct[Samsung]"
    assert by.loc["L01", "threshold_value"] == "Samsung: 0.15"
    assert by.loc["L01", "eur_fleet_per_year"] == pytest.approx(89.0 + 40.0 + 10.0 + 15.0 + 5.0, abs=0.01)
    assert int(by.loc["L01", "n_eligible"]) == 12 and int(by.loc["L01", "n_attributed"]) == 11
    assert by.loc["L02", "eur_fleet_per_year"] == pytest.approx(25.0)
    assert int(by.loc["L02", "n_attributed"]) == 11  # S04 is open
    # the share is measured on the lever's own population: L02 over the landed cost of the purchases in the window
    in_window = dl[(pd.to_datetime(dl["purchase_date"]) > pd.Timestamp(AS_OF) - pd.Timedelta(days=365)) & dl["serial"].ne("S04")]
    assert by.loc["L02", "lever_basis_eur"] == pytest.approx(float(in_window["landed_cost"].sum()), abs=0.01)
    assert by.loc["L02", "share_of_lever_basis"] == pytest.approx(25.0 / float(in_window["landed_cost"].sum()))
    assert "landed cost" in by.loc["L02", "lever_basis"] and "closed result" in by.loc["L03", "lever_basis"]
    # L03..L07 over the absolute closed result of the same serials; L07 counted once per cohort (70.00, not 5 x 70)
    assert by.loc["L07", "eur_fleet_per_year"] == pytest.approx(70.0, abs=0.01)
    # the L07-attributed serials: the android_like cohort (Samsung and Google); Apple has one term, Lenovo is alone
    closed_basis = float(dl.loc[dl["serial"].str.match(r"^[SG]0"), "lifecycle_result_eur"].abs().sum())
    assert closed_basis == 600.0
    assert by.loc["L07", "lever_basis_eur"] == pytest.approx(closed_basis, abs=0.01)
    assert by.loc["L07", "share_of_lever_basis"] == pytest.approx(70.0 / closed_basis)
    assert "share_of_closed_loss" not in s.columns
    # the reference parameter and its owner sit next to the reacting rule
    assert by.loc["L05", "reference_key"] == "expected_return_to_sale_days" and "Recommerce" in by.loc["L05", "reference_owner"]
    assert by.loc["L05", "rule_id"] == "R03" and by.loc["L05", "threshold_key"] == "aging_days_90"
    assert by.loc["L02", "reference_key"] == "n/a"
    assert by.loc["L04", "threshold_key"] == "repair_max_share_of_rv[android_like]"
    assert by.loc["L04", "threshold_value"].startswith("android_like: ")


def test_lever_rule_ids_and_thresholds_bind_to_the_registry(thr, a):
    """Every lever names a rule or advisory that exists and a threshold that lists it; the reference key is owned."""
    for spec in att.LEVERS.values():
        assert spec.rule_id in registry.RULES or spec.rule_id in registry.ADVISORY_SPECS, (spec.lever_id, spec.rule_id)
        assert spec.threshold_key in thr.thresholds, (spec.lever_id, spec.threshold_key)
        assert spec.rule_id in thr.thresholds[spec.threshold_key].rule_ids, (spec.lever_id, spec.rule_id, spec.threshold_key)
        if spec.reference_key is not None:
            assert a.owner(spec.reference_key).strip(), (spec.lever_id, spec.reference_key)
    # L05: the parameter that moves it is the return-to-sale assumption, R03 / aging_days_90 only react
    l05 = att.LEVERS["L05"]
    assert l05.reference_key == "expected_return_to_sale_days" and l05.threshold_key == "aging_days_90" and l05.rule_id == "R03"
    assert att.LEVERS["L07"].summary_basis == "per_cohort"


def test_levers_by_cohort(dl, per_device):
    c = summ.levers_by_cohort(per_device, dl, as_of=AS_OF)
    assert list(c.columns) == list(summ.COHORT_COLUMNS)
    oem = c[(c["cohort_kind"] == "oem") & (c["lever_id"] == "L01")].set_index("cohort_value")
    assert float(oem.loc["Samsung", "sum_delta_eur"]) == pytest.approx(89.0)
    assert int(oem.loc["Samsung", "n_attributed"]) == 5
    assert "Lenovo" not in oem.index
    assert set(c["cohort_kind"]) == set(summ.DEFAULT_COHORT_KINDS)
    term = c[(c["cohort_kind"] == "term_months") & (c["lever_id"] == "L07")].set_index("cohort_value")
    assert float(term.loc["24", "sum_delta_eur"]) == pytest.approx(5 * 70.0, abs=0.05)  # per serial here, once per cohort in the summary


# --------------------------------------------------------------------------------------
# R07
# --------------------------------------------------------------------------------------


def test_r07_pure_function_outcomes(thr):
    common = dict(po_line_id="PO-2025-000001-1", oem="Samsung", supplier_name="Samsung", supplier_role="manufacturer",
                  rrp_net_eur=700.0, qty=3, thr=thr, as_of=AS_OF, run_id="t")
    # discount exactly 15 % = the Samsung floor: equality passes
    rec = decide_purchase_floor(unit_price_eur=595.0, **common)
    assert rec.rule_id == "R07" and rec.outcome == "at_or_above_floor" and rec.value_at_stake_eur is None
    assert rec.threshold_key == "purchase_discount_floor_pct[Samsung]" and rec.threshold_value == 0.15
    assert rec.threshold_owner == "Head of Procurement (name)" and rec.subject_type == "purchase_order"
    assert rec.subject_id == "PO-2025-000001-1" and rec.inputs["discount_pct"] == pytest.approx(0.15)
    # discount 10 %: below the floor, stake (630 - 595) x 3 = 105.00
    rec = decide_purchase_floor(unit_price_eur=630.0, **common)
    assert rec.outcome == "below_floor_discount" and rec.value_at_stake_eur == pytest.approx(105.0)
    assert "discount 10.0 % vs floor 15.0 % for Samsung" in rec.outcome_detail
    assert rec.inputs["thresholds_consulted"][0]["owner"] == "Head of Procurement (name)"
    assert "at_or_above_floor" in NO_ACTION_OUTCOMES["R07"] and "below_floor_discount" not in NO_ACTION_OUTCOMES["R07"]
    assert registry.RULES["R07"].threshold_keys == ("purchase_discount_floor_pct",)
    assert registry.RULES["R07"].input_names == (
        "po_line_id", "oem", "supplier_name", "supplier_role", "unit_price_eur", "rrp_net_eur", "qty")
    with pytest.raises(KeyError):
        decide_purchase_floor(**{**common, "oem": "Unknown OEM"}, unit_price_eur=600.0)


def test_r07_zero_records_without_ledger(assumptions, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    con = dfx.build_fixture_db()
    inline = dfx.thresholds_from_tmp(tmp_path)
    queue, summary = runner.run_all_decisions(con, dfx.AS_OF, inline, assumptions)
    assert summary.counts["records_R07"] == 0 and "skipped_R07" not in summary.counts
    assert summary.counts["records_total"] == 19
    assert not (queue["rule_id"] == "R07").any()
    assert runner._r07(con, inline, dfx.AS_OF, "t") == ([], 0)


def test_r07_records_on_the_hand_ledger(dl, grid, thr):
    con = _levers_db(dl, grid)
    records, skipped = runner._r07(con, thr, AS_OF, "t")
    assert skipped == 0 and len(records) == 7  # seven distinct (po_number, po_line) pairs
    by = {r.subject_id: r for r in records}
    # PO-2025-000001-1 carries S01 (10 %) and S02 (12 %): mean unit 623.00, floor price 595.00, qty 2 -> 56.00
    r = by["PO-2025-000001-1"]
    assert r.outcome == "below_floor_discount" and r.inputs["qty"] == 2
    assert r.value_at_stake_eur == pytest.approx((623.0 - 595.0) * 2)
    # Google line: 10/12/14 % vs floor 12 % -> mean discount 12 % -> at or above
    assert by["PO-2025-000003-1"].outcome == "at_or_above_floor"
    assert by["PO-2025-000004-1"].outcome == "at_or_above_floor"  # Lenovo 20 % vs 18 %


# --------------------------------------------------------------------------------------
# runner: gold tables, advisories, queue
# --------------------------------------------------------------------------------------


def test_run_levers_refuses_without_ledger(thr, a):
    con = db.connect(":memory:")
    db.create_schema(con)
    with pytest.raises(ValueError, match="run ledger first"):
        run_levers(con, AS_OF, thr, a)


def test_run_levers_writes_gold_tables_and_advisories(dl, grid, thr, a, tmp_path, monkeypatch):
    from restwert.levers import run as run_mod

    monkeypatch.setattr(run_mod, "LEVERS_MD_PATH", tmp_path / "LEVERS.md")
    con = _levers_db(dl, grid)
    summary = run_levers(con, AS_OF, thr, a, write_docs=True)
    c = summary.counts
    assert summary.command == "levers" and c["additivity_violations"] == 0
    assert c["levers_per_device"] == 12 * 2 + 11 + 11 + 12 + 11 + 11  # L01+L02 (12 each), L03 (11 sold), L04 (11 closed with a return), L05 (11 sold + 1 in stock), L06 (11), L07 (11 closed)
    assert c["levers_summary"] == 7 and c["levers_by_cohort"] > 0 and c["serials"] == 12
    assert c["adv03"] == 1 and c["adv04"] == 1
    assert (tmp_path / "LEVERS.md").exists()
    for t in ("gold.levers_per_device", "gold.levers_by_cohort", "gold.levers_summary"):
        assert db.table_exists(con, t)
    pdv = db.read_df(con, 'SELECT * FROM "gold"."levers_per_device" ORDER BY serial, lever_id')
    assert len(pdv) == c["levers_per_device"] and set(pdv["lever_id"]) == set(att.LEVERS)
    s = db.read_df(con, 'SELECT * FROM "gold"."levers_summary" ORDER BY rank')
    assert list(s["rank"]) == list(range(1, 8)) and s["threshold_owner"].str.len().gt(0).all()
    runs = db.read_df(con, "SELECT command, counts_json FROM runs")
    assert runs.iloc[0]["command"] == "levers" and json.loads(runs.iloc[0]["counts_json"])["additivity_violations"] == 0
    # a rerun replaces the gold tables and the ADV03/ADV04 rows instead of duplicating them
    run_levers(con, AS_OF, thr, a, write_docs=False)
    assert len(db.read_df(con, 'SELECT * FROM "gold"."levers_per_device"')) == c["levers_per_device"]
    assert len(db.read_df(con, "SELECT * FROM advisories WHERE kind IN ('manufacturer_mix', 'term_gap')")) == 2


def test_adv03_adv04_rows_and_queue_priority_3_on_hand_ledger(dl, grid, thr, a, tmp_path, monkeypatch):
    con = _levers_db(dl, grid)
    run_levers(con, AS_OF, thr, a, write_docs=False)
    adv = db.read_df(con, "SELECT * FROM advisories ORDER BY advisory_id")
    a3 = adv[adv["kind"] == "manufacturer_mix"].iloc[0]
    assert a3["subject_type"] == "oem" and a3["subject_id"] == "Google"
    assert a3["threshold_key"] == "oem_realisation_gap_pct" and a3["threshold_owner"] == "Category Manager Hardware (name)"
    p3 = json.loads(a3["payload_json"])
    assert p3["n"] == 3 and p3["threshold"] == 0.05 and p3["family"] == "Smartphone"
    assert p3["mean_gap_pct"] == pytest.approx(0.3107142857 - 0.20, abs=1e-4)
    assert a3["confidence"] == "low" and a3["advisory_id"] == f"ADV03-Google-{AS_OF.isoformat()}"
    a4 = adv[adv["kind"] == "term_gap"].iloc[0]
    assert a4["subject_type"] == "cohort" and a4["subject_id"] == "android_like:2025-H2"
    p4 = json.loads(a4["payload_json"])
    assert p4["better_term"] == 36 and p4["gap_eur"] == 70.0 and p4["n_24"] == 5 and p4["n_36"] == 3
    assert a4["threshold_owner"] == "CFO (name)"
    # the queue carries both at priority 3 with the threshold value from the payload
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    queue, summary = runner.run_all_decisions(con, AS_OF, thr, a)
    q = queue[queue["rule_id"].isin(["ADV03", "ADV04"])].set_index("rule_id")
    assert len(q) == 2 and (q["priority"] == 3).all()
    assert q.loc["ADV03", "outcome"] == "review_oem_allocation" and q.loc["ADV03", "subject_id"] == "Google"
    assert q.loc["ADV04", "outcome"] == "review_term_policy" and q.loc["ADV04", "threshold_value"] == "50"
    assert q.loc["ADV03", "threshold_value"] == "0.05" and q.loc["ADV03", "advisory_kind"] == "manufacturer_mix"
    assert q["threshold_owner"].str.len().gt(0).all()
    # R07 decided the seven PO lines of the ledger on the same run
    assert summary.counts["records_R07"] == 7
    assert (queue["rule_id"] == "R07").sum() == int((queue["outcome"] == "below_floor_discount").sum()) >= 1
    assert set(runner._ADVISORY_QUEUE) == {"forecast_calibration", "manufacturer_mix", "term_gap"}
    assert runner.calibration_advisory_rows is runner.advisory_queue_rows
    for adv_id in ("ADV03", "ADV04"):
        spec = registry.ADVISORY_SPECS[adv_id]
        for key in spec["threshold_keys"]:
            assert key in thr.thresholds


# --------------------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------------------


def test_levers_md_no_em_dash_and_names_every_lever(thr):
    text = summ.render_levers_md(thr)
    assert EM_DASH not in text
    assert "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD." in text
    assert "a lever is a reference, not a counterfactual fact" in text.lower()
    for lever_id, spec in att.LEVERS.items():
        assert f"| {lever_id} |" in text and spec.name in text and f"`{spec.threshold_key}`" in text
        assert thr.thresholds[spec.threshold_key].owner in text
    assert "Worked example" in text and "SN-EXAMPLE" in text
    shipped = ROOT / "docs" / "LEVERS.md"
    if shipped.exists():
        raw = shipped.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and EM_DASH not in raw.decode("utf-8")


def test_levers_package_has_no_side_effect_imports():
    import re

    forbidden = ("smtplib", "requests", "httpx", "urllib.request", "boto3", "paramiko", "subprocess", "socket")
    for py in (ROOT / "restwert" / "levers").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for name in forbidden:
            assert not re.search(rf"^\s*(import|from)\s+{re.escape(name)}\b", text, re.M), (py.name, name)
        assert EM_DASH not in text, py.name


# --------------------------------------------------------------------------------------
# session lake pipeline (module 6 fixture; skipped while it does not exist)
# --------------------------------------------------------------------------------------


def _lake_db(request):
    try:
        return request.getfixturevalue("lake_pipeline_db")
    except pytest.FixtureLookupError:
        pytest.skip("lake_pipeline_db fixture not registered yet (module 6)")


def test_levers_pipeline_tables_populated(request):
    con = _lake_db(request)
    s = db.read_df(con, 'SELECT * FROM "gold"."levers_summary" ORDER BY rank')
    assert set(s["lever_id"]) == set(att.LEVERS) and list(s["rank"]) == list(range(1, 8))
    assert s["threshold_owner"].str.len().gt(0).all() and "total" not in set(s["lever_id"].str.lower())
    pdv = db.read_df(con, 'SELECT lever_id, count(*) AS n, sum(CASE WHEN is_attributed THEN 1 ELSE 0 END) AS n_att FROM "gold"."levers_per_device" GROUP BY lever_id').set_index("lever_id")
    assert set(pdv.index) == set(att.LEVERS) and (pdv["n"] > 0).all()
    for lever_id in ("L01", "L02", "L03", "L04", "L05"):
        assert pdv.loc[lever_id, "n_att"] > 0, lever_id
    # L06 and L07 need fleet groups of 2 x min_n and min_n; on the 500-serial fleet a group may stay
    # below that, and then every row says so instead of guessing
    for lever_id in ("L06", "L07"):
        if pdv.loc[lever_id, "n_att"] == 0:
            reasons = db.read_df(con, f"SELECT counterfactual_json FROM \"gold\".\"levers_per_device\" WHERE lever_id = '{lever_id}'")
            assert all("min_n" in json.loads(r)["not_attributed_reason"] for r in reasons["counterfactual_json"])
    runs = db.read_df(con, "SELECT counts_json FROM runs WHERE command = 'levers' ORDER BY started_at DESC LIMIT 1")
    assert len(runs) == 1 and json.loads(runs.iloc[0]["counts_json"])["additivity_violations"] == 0
    dl_db = db.read_df(con, 'SELECT * FROM "silver"."device_ledger"')
    pd_db = db.read_df(con, 'SELECT * FROM "gold"."levers_per_device"')
    assert len(att.check_additivity(dl_db, pd_db)) == 0


def test_adv03_adv04_rows_and_queue_priority_3(request):
    con = _lake_db(request)
    adv = db.read_df(con, "SELECT kind, subject_type, threshold_owner FROM advisories WHERE kind IN ('manufacturer_mix', 'term_gap')")
    q = db.read_df(con, "SELECT rule_id, priority, outcome FROM decision_queue WHERE rule_id IN ('ADV03', 'ADV04')")
    assert len(q) == len(adv)
    if len(q):
        assert (q["priority"] == 3).all()
        assert set(q["outcome"]) <= {"review_oem_allocation", "review_term_policy"}
        assert adv["threshold_owner"].str.len().gt(0).all()
