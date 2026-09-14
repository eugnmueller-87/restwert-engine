"""Tests for the KPI module (SPEC 7.6): registry completeness, hand numbers, honesty rules."""

from __future__ import annotations

import math
from datetime import date

import pytest

from restwert import db
from restwert.config import KpiTargets
from restwert.kpi import KPI_REGISTRY, KPI_TREE, catalogue, compute, registry
from restwert.kpi.compute import compute_all, compute_one, run_kpis
from restwert.records import KpiValue

try:
    from tests.fixtures import kpi_frames as kf
except ImportError:  # repo root not on sys.path (plain `pytest` invocation): tests/ is
    from fixtures import kpi_frames as kf

AS_OF = kf.AS_OF
TARGETS = kf.TARGETS

EXPECTED_IDS_AND_NAMES = {
    "KPI_TOP_LIFECYCLE_MARGIN": "Lifecycle margin per device",
    "KPI_TOP_RV_FORECAST_ERROR": "Residual value forecast error",
    "KPI_PROC_LANDED_VS_BENCHMARK": "Landed cost per device vs benchmark",
    "KPI_PROC_SPEND_UNDER_CONTRACT": "Share of spend under contract",
    "KPI_PROC_SUPPLIER_OTIF": "Supplier OTIF",
    "KPI_PROC_PPV": "Purchase price variance (PPV)",
    "KPI_INV_WEEKS_OF_COVER": "Weeks of cover",
    "KPI_INV_AGING_90": "Aging >90 days",
    "KPI_INV_AGING_180": "Aging >180 days",
    "KPI_INV_WIP_DAYS": "WIP days return-to-sellable",
    "KPI_INV_WRITE_DOWN_PCT": "Write-down in % of book value",
    "KPI_REC_RV_REALISATION": "Residual value realisation vs forecast",
    "KPI_REC_MARGIN_MIX_CHANNEL": "Margin mix by channel",
    "KPI_REC_DAYS_TO_SALE": "Days to sale",
    "KPI_REC_GRADING_ACCURACY": "Grading accuracy",
    "KPI_IND_SAVINGS_CONFIRMED": "Savings vs plan confirmed by controlling",
    "KPI_IND_SPEND_UNDER_MGMT": "Share of spend under management",
    "KPI_IND_PO_RATE": "PO rate",
    "KPI_IND_MAVERICK_SHARE": "Maverick share",
    "KPI_IND_ACTIVE_SUPPLIERS": "Active suppliers",
}


@pytest.fixture(scope="module")
def con():
    return kf.fixture_con()


@pytest.fixture()
def empty():
    return kf.empty_con()


def value_of(kpi_id: str, con) -> KpiValue:
    return KPI_REGISTRY[kpi_id].fn(con, AS_OF, TARGETS)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_has_every_id_with_exact_names():
    assert set(KPI_REGISTRY) == set(EXPECTED_IDS_AND_NAMES)
    for kpi_id, name in EXPECTED_IDS_AND_NAMES.items():
        assert KPI_REGISTRY[kpi_id].name == name


def test_registry_specs_are_complete():
    for spec in KPI_REGISTRY.values():
        assert spec.definition.strip()
        assert spec.formula_text.strip()
        assert spec.source_tables and all(t.strip() for t in spec.source_tables)
        assert spec.measurable_from.strip()
        assert spec.unit.strip()
        assert spec.direction in ("up", "down", "zero", "one")
        assert spec.min_n >= 1
        assert callable(spec.fn)


def test_tree_has_five_areas_top_first_and_covers_registry():
    areas = [area for area, _ in KPI_TREE]
    assert areas == ["Top", "Procurement", "Inventory", "Recommerce", "Indirect"]
    tree_ids = [k for _, ids in KPI_TREE for k in ids]
    assert sorted(tree_ids) == sorted(KPI_REGISTRY)
    assert len(tree_ids) == len(set(tree_ids))
    assert KPI_TREE[0][1] == ["KPI_TOP_LIFECYCLE_MARGIN", "KPI_TOP_RV_FORECAST_ERROR"]


def test_register_rejects_duplicates_and_bad_area():
    with pytest.raises(ValueError):
        registry.register(
            kpi_id="KPI_TOP_LIFECYCLE_MARGIN", name="x", area="Top", definition="d", formula_text="f",
            source_tables=("t",), measurable_from="m", unit="u", direction="up",
        )
    with pytest.raises(ValueError):
        registry.register(
            kpi_id="KPI_X_NEW", name="x", area="Elsewhere", definition="d", formula_text="f",
            source_tables=("t",), measurable_from="m", unit="u", direction="up",
        )
    assert "KPI_X_NEW" not in KPI_REGISTRY


def test_trailing_window():
    start, end = registry.trailing_window(date(2026, 6, 30), 12)
    assert (start, end) == (date(2025, 7, 1), date(2026, 6, 30))
    start, end = registry.trailing_window(date(2026, 3, 31), 1)
    assert (start, end) == (date(2026, 3, 1), date(2026, 3, 31))


# ---------------------------------------------------------------------------
# hand numbers on the fixture
# ---------------------------------------------------------------------------


def test_supplier_otif_two_of_three(con):
    kv = value_of("KPI_PROC_SUPPLIER_OTIF", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(2 / 3)
    assert (kv.numerator, kv.denominator, kv.n) == (2.0, 3.0, 3)
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["Supplier-A", "value"] == pytest.approx(1.0)
    assert bd.loc["Supplier-B", "value"] == pytest.approx(0.0)


def test_ppv_hand_number_excludes_lines_without_benchmark(con):
    kv = value_of("KPI_PROC_PPV", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(0.05)
    assert kv.numerator == pytest.approx(50.0)
    assert kv.denominator == pytest.approx(1000.0)
    assert kv.n == 1
    assert "2 line(s) without benchmark" in kv.note


def test_grading_accuracy_two_of_four(con):
    kv = value_of("KPI_REC_GRADING_ACCURACY", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(0.5)
    assert (kv.numerator, kv.denominator, kv.n) == (2.0, 4.0, 4)
    assert "0.2500" in kv.note  # optimism share: one of four declared better than inspected
    cells = dict(zip(kv.breakdown["dimension_value"], kv.breakdown["n"]))
    assert cells == {"A>A": 1, "B>B": 1, "B>C": 1, "C>B": 1}


def test_weeks_of_cover_26(con):
    kv = value_of("KPI_INV_WEEKS_OF_COVER", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(26.0)
    assert kv.numerator == 4.0
    assert kv.denominator == pytest.approx(2 / 13)


def test_savings_ignores_unconfirmed_rows_claimed_only_in_note(con):
    kv = value_of("KPI_IND_SAVINGS_CONFIRMED", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(120 / 1000)
    assert kv.numerator == pytest.approx(120.0)
    assert kv.denominator == pytest.approx(1000.0)
    assert "170.00" in kv.note  # claimed total including the unconfirmed 50
    assert kv.n == 2


def test_savings_not_measurable_without_plan(con):
    no_plan = KpiTargets(version=1, savings_plan_eur={2024: 1.0}, savings_plan_owner="x", targets={}, targets_owner="y")
    kv = KPI_REGISTRY["KPI_IND_SAVINGS_CONFIRMED"].fn(con, AS_OF, no_plan)
    assert kv.status == "not_measurable" and kv.value is None


def test_landed_vs_benchmark_excludes_and_counts_models_without_benchmark(con):
    kv = value_of("KPI_PROC_LANDED_VS_BENCHMARK", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(1400 / 1350 - 1)
    assert kv.numerator == pytest.approx(1400.0)
    assert kv.denominator == pytest.approx(1350.0)
    assert kv.n == 2
    assert "1 device(s) without benchmark" in kv.note
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["P-Gen14", "value"] == pytest.approx(900 / 850 - 1)
    assert bd.loc["A-Gen06", "value"] == pytest.approx(0.0)


def test_spend_under_contract_hand_number(con):
    kv = value_of("KPI_PROC_SPEND_UNDER_CONTRACT", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(2650 / 4450)
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["hardware", "value"] == pytest.approx(1450 / 2450)
    assert bd.loc["indirect", "value"] == pytest.approx(1200 / 2000)


def test_indirect_shares(con):
    assert value_of("KPI_IND_SPEND_UNDER_MGMT", con).value == pytest.approx(1700 / 2000)
    assert value_of("KPI_IND_PO_RATE", con).value == pytest.approx(1500 / 2000)
    assert value_of("KPI_IND_MAVERICK_SHARE", con).value == pytest.approx(300 / 2000)


def test_active_suppliers(con):
    kv = value_of("KPI_IND_ACTIVE_SUPPLIERS", con)
    assert kv.status == "ok"
    assert kv.value == 5.0  # G, H, I from indirect; A, B from delivered POs; J outside window; C not delivered
    assert kv.denominator is None
    assert kv.n == 5
    bd = dict(zip(kv.breakdown["dimension_value"], kv.breakdown["n"]))
    assert bd["hardware"] == 2 and bd["logistics"] == 1


def test_lifecycle_margin_per_device(con):
    kv = value_of("KPI_TOP_LIFECYCLE_MARGIN", con)
    # D-3 (+30), D-4 (-28), D-7 (-100) closed in the window; D-5 closed outside it
    assert kv.status == "ok"
    assert kv.value == pytest.approx((30 - 28 - 100) / 3)
    assert kv.n == 3
    flagged = compute_one("KPI_TOP_LIFECYCLE_MARGIN", con, AS_OF, TARGETS)
    assert flagged.status == "not_measurable"
    assert flagged.value == pytest.approx((30 - 28 - 100) / 3)  # value kept, greyed by min_n
    assert "min_n=30" in flagged.note


def test_rv_forecast_error_latest_complete_month(con):
    kv = value_of("KPI_TOP_RV_FORECAST_ERROR", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(0.12)  # May 2026, not the current June row
    assert kv.n == 40
    assert "2026-05" in kv.note and "bias" in kv.note
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["iphone_like", "value"] == pytest.approx(0.10)


def test_aging_buckets(con):
    a90 = value_of("KPI_INV_AGING_90", con)
    a180 = value_of("KPI_INV_AGING_180", con)
    assert a90.value == pytest.approx(2 / 4)
    assert a180.value == pytest.approx(1 / 4)
    assert "400.00 EUR" in a90.note  # book value of the two aged units 300 + 100
    assert "100.00 EUR" in a180.note


def test_wip_days(con):
    kv = value_of("KPI_INV_WIP_DAYS", con)
    # RF-1: 2026-04-30 - 2026-04-01 = 29; RF-2: 2026-03-10 - 2026-02-20 = 18; RF-3 outside window
    assert kv.status == "ok"
    assert kv.value == pytest.approx((29 + 18) / 2)
    assert kv.n == 2
    assert "median 23.5" in kv.note


def test_write_down_pct(con):
    kv = value_of("KPI_INV_WRITE_DOWN_PCT", con)
    assert kv.status == "ok"
    assert kv.value == pytest.approx(25 / 1400)  # latest ledger as_of only, not 10 + 25


def test_rv_realisation_excludes_as_is_and_missing(con):
    kv = value_of("KPI_REC_RV_REALISATION", con)
    # trailing 3 months: S-3 (250 vs 200) counts; S-7 as_is excluded; S-4 outside window
    assert kv.status == "ok"
    assert kv.value == pytest.approx(250 / 200)
    assert kv.n == 1
    assert "1 As-Is excluded" in kv.note


def test_margin_mix_by_channel(con):
    kv = value_of("KPI_REC_MARGIN_MIX_CHANNEL", con)
    # marketplace 250-25-40-15 = 170; b2b 310-31-30-12 = 237; as_is 50-2.5-5-9.5 = 33; total 440
    assert kv.status == "ok"
    assert kv.value == pytest.approx(237 / 440)
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["marketplace", "numerator"] == pytest.approx(170.0)
    assert bd.loc["marketplace", "value"] == pytest.approx(170 / 440)
    assert bd.loc["as_is", "n"] == 1


def test_days_to_sale(con):
    kv = value_of("KPI_REC_DAYS_TO_SALE", con)
    # trailing 3 months: S-3 10 days, S-7 12 days
    assert kv.status == "ok"
    assert kv.value == pytest.approx(11.0)
    assert kv.n == 2


# ---------------------------------------------------------------------------
# honesty rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kpi_id", sorted(EXPECTED_IDS_AND_NAMES))
def test_every_kpi_not_measurable_on_empty_tables(kpi_id, empty):
    kv = KPI_REGISTRY[kpi_id].fn(empty, AS_OF, TARGETS)
    assert kv.status == "not_measurable"
    assert kv.value is None
    assert kv.note


def test_missing_table_and_missing_column_are_not_measurable(con):
    partial = kf.fixture_con({"purchase_orders": kf.purchase_orders_frame()})
    partial.execute("DROP TABLE device_pnl")
    kv = KPI_REGISTRY["KPI_TOP_LIFECYCLE_MARGIN"].fn(partial, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None and "device_pnl" in kv.note
    partial.execute("ALTER TABLE purchase_orders DROP COLUMN benchmark_price")
    kv = KPI_REGISTRY["KPI_PROC_PPV"].fn(partial, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None and "benchmark_price" in kv.note


def test_all_null_required_column_is_not_measurable():
    po = kf.purchase_orders_frame()
    po["delivered_date"] = None  # nullable in the DDL; entirely NULL means OTIF has no evidence
    partial = kf.fixture_con({"purchase_orders": po})
    kv = KPI_REGISTRY["KPI_PROC_SUPPLIER_OTIF"].fn(partial, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None
    assert "delivered_date" in kv.note
    kv = KPI_REGISTRY["KPI_PROC_PPV"].fn(partial, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None


def test_compute_one_reports_exception_as_not_measurable(con, monkeypatch):
    def boom(con, as_of, targets):
        raise RuntimeError("fixture failure")

    spec = KPI_REGISTRY["KPI_PROC_PPV"]
    monkeypatch.setitem(KPI_REGISTRY, "KPI_PROC_PPV", registry.KpiSpec(**{**spec.__dict__, "fn": boom}))
    kv = compute_one("KPI_PROC_PPV", con, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None and "fixture failure" in kv.note


# ---------------------------------------------------------------------------
# compute_all / run_kpis / catalogue
# ---------------------------------------------------------------------------


def test_compute_all_frames_have_table_shape(con):
    values, breakdown = compute_all(con, AS_OF, TARGETS, run_id="test-run")
    assert list(values.columns) == list(compute.KPI_VALUES_COLUMNS)
    assert list(breakdown.columns) == list(compute.KPI_BREAKDOWN_COLUMNS)
    assert len(values) == 20 and values["kpi_id"].is_unique
    assert values["kpi_id"].tolist()[:2] == ["KPI_TOP_LIFECYCLE_MARGIN", "KPI_TOP_RV_FORECAST_ERROR"]
    assert set(values["status"]) <= {"ok", "not_measurable"}
    nm = values[values["status"] == "not_measurable"]
    # on the full fixture only the top margin is greyed (n=3 below min_n=30); its value is kept
    assert set(nm["kpi_id"]) == {"KPI_TOP_LIFECYCLE_MARGIN"}
    assert nm["value"].notna().all()
    assert values.set_index("kpi_id").loc["KPI_PROC_SUPPLIER_OTIF", "target"] == pytest.approx(0.9)
    assert not breakdown.duplicated(subset=["kpi_id", "dimension", "dimension_value"]).any()
    assert (values["run_id"] == "test-run").all()


def test_run_kpis_writes_tables_and_catalogue(tmp_path, monkeypatch):
    con = kf.fixture_con()
    target = tmp_path / "KPI_CATALOGUE.md"

    def fake_write(path=None, targets=None):
        # keep the test out of docs/: render to tmp instead of the repo
        target.write_text(catalogue.render_catalogue(targets=targets), encoding="utf-8")
        return target

    monkeypatch.setattr(catalogue, "write_catalogue", fake_write)
    summary = run_kpis(con, AS_OF, TARGETS, write_catalogue_md=True)
    assert summary.command == "kpis"
    assert summary.counts["kpi_values"] == 20
    assert summary.counts["kpi_breakdown"] > 0
    values = db.read_df(con, "SELECT kpi_id, status, value FROM kpi_values ORDER BY kpi_id")
    assert len(values) == 20
    assert db.read_df(con, "SELECT count(*) AS n FROM kpi_breakdown")["n"].iloc[0] == summary.counts["kpi_breakdown"]
    assert target.exists()
    # rerun for the same as_of replaces instead of duplicating
    run_kpis(con, AS_OF, TARGETS, write_catalogue_md=False)
    assert db.read_df(con, "SELECT count(*) AS n FROM kpi_values")["n"].iloc[0] == 20


def test_render_catalogue_contains_every_id_and_governance_sentence():
    text = catalogue.render_catalogue()
    assert catalogue.GOVERNANCE_SENTENCE in text
    assert "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD." in text
    for kpi_id, name in EXPECTED_IDS_AND_NAMES.items():
        assert kpi_id in text
        assert name in text
    for area in ("Top", "Procurement", "Inventory", "Recommerce", "Indirect"):
        assert f"## {area}" in text
    assert "\u2014" not in text  # no em dash in generated prose
    with_targets = catalogue.render_catalogue(targets=TARGETS)
    assert "Head of Indirect Procurement (fixture)" in with_targets


def test_write_catalogue_writes_file(tmp_path):
    path = catalogue.write_catalogue(tmp_path / "docs" / "KPI_CATALOGUE.md", targets=TARGETS)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"KPI_IND_ACTIVE_SUPPLIERS" in raw


def test_never_zero_when_denominator_is_zero():
    pnl = kf.device_pnl_frame()
    pnl["book_value"] = 0.0
    con = kf.fixture_con({"device_pnl": pnl, "write_down_ledger": kf.write_down_ledger_frame(), "decision_log": kf.decision_log_frame()})
    kv = KPI_REGISTRY["KPI_INV_WRITE_DOWN_PCT"].fn(con, AS_OF, TARGETS)
    assert kv.status == "not_measurable" and kv.value is None
    assert registry.ratio(5.0, 0.0) is None
    assert registry.ratio(5.0, math.nan) is None


def test_savings_only_hard_price_reductions_count(con):
    con.execute("UPDATE indirect_spend SET saving_type = 'cost_avoidance', baseline_amount = amount + saving WHERE spend_id = 'IS-4'")
    con.execute("UPDATE indirect_spend SET saving_type = 'hard_price_reduction', baseline_amount = amount + saving WHERE spend_id = 'IS-1'")
    kv = value_of("KPI_IND_SAVINGS_CONFIRMED", con)
    assert kv.status == "ok"
    assert kv.numerator == pytest.approx(100.0)          # IS-4 (20, confirmed) is avoidance: reported, not counted
    assert kv.value == pytest.approx(100 / 1000)
    assert "cost avoidance 20.00 EUR" in kv.note
    assert "untyped" not in kv.note
    bd = kv.breakdown.set_index("dimension_value")
    assert bd.loc["cost_avoidance", "numerator"] == pytest.approx(20.0)
    assert bd.loc["hard_price_reduction", "numerator"] == pytest.approx(100.0)


def test_savings_untyped_rows_count_as_hard_and_are_flagged():
    fresh = kf.fixture_con()                                  # untouched fixture: rows carry no saving_type
    kv = value_of("KPI_IND_SAVINGS_CONFIRMED", fresh)
    assert kv.numerator == pytest.approx(120.0)
    assert "2 confirmed row(s) without saving_type counted as hard (untyped)" in kv.note


def test_margin_mix_is_labelled_net_recovery_before_asset_cost(con):
    kv = value_of("KPI_REC_MARGIN_MIX_CHANNEL", con)
    assert "BEFORE asset cost" in kv.note and "not a P&L margin" in kv.note
    spec = KPI_REGISTRY["KPI_REC_MARGIN_MIX_CHANNEL"]
    assert "before asset cost" in spec.definition.lower()
    assert spec.name == "Margin mix by channel"   # the KPI tree name stays, the definition is honest


def test_top_forecast_error_definition_discloses_channel_mix(con):
    spec = KPI_REGISTRY["KPI_TOP_RV_FORECAST_ERROR"]
    assert "channel mix" in spec.definition
    assert "business view" in spec.definition and "model view" in spec.definition
