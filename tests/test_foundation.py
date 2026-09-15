"""Tests for the foundation module (SPEC.md section 3.5, tests/test_foundation.py)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from restwert import GOVERNANCE_PRINCIPLE, __version__, config, db, dates, records, schema  # noqa: E402
from restwert.config import FamilyConfig  # noqa: E402

# --------------------------------------------------------------------------- dates


def test_months_between_boundaries_and_leap_years():
    assert dates.months_between(date(2024, 1, 31), date(2024, 2, 29)) == 0
    assert dates.months_between(date(2024, 1, 15), date(2024, 3, 15)) == 2
    assert dates.months_between(date(2024, 1, 31), date(2024, 3, 31)) == 2
    assert dates.months_between(date(2024, 1, 31), date(2024, 3, 30)) == 1
    assert dates.months_between(date(2024, 2, 29), date(2025, 2, 28)) == 11
    assert dates.months_between(date(2024, 2, 29), date(2025, 3, 1)) == 12
    assert dates.months_between(date(2024, 5, 1), date(2024, 5, 1)) == 0
    assert dates.months_between(date(2024, 5, 1), date(2024, 4, 1)) == 0


def test_add_months_clamps_and_month_ends():
    assert dates.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert dates.add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert dates.add_months(date(2024, 11, 30), 3) == date(2025, 2, 28)
    assert dates.add_months(date(2024, 3, 15), -3) == date(2023, 12, 15)
    assert dates.month_end(date(2024, 2, 10)) == date(2024, 2, 29)
    assert dates.month_floor(date(2024, 2, 10)) == date(2024, 2, 1)
    assert dates.month_ends(date(2024, 1, 15), date(2024, 4, 30)) == [
        date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30),
    ]
    assert dates.month_ends(date(2024, 1, 31), date(2024, 1, 30)) == []
    assert dates.quarter_label(date(2024, 5, 3)) == "2024-Q2"
    assert dates.days_between(date(2024, 1, 1), date(2024, 1, 11)) == 10
    assert dates.months_between_float(date(2024, 1, 1), date(2024, 1, 1) ) == 0.0


def _iphone_cfg() -> dict[str, FamilyConfig]:
    return {
        "iphone_like": FamilyConfig(
            name="iphone_like", first_launch=date(2019, 9, 15), launch_cadence_months=12, launch_month=9,
            list_price_min=799, list_price_max=1299, storage_options=[128, 256], base_storage_gb=128,
            discount_min=0.1, discount_max=0.2, freight_duty_pct=0.03,
            truth={"base": 0.9, "lambda": 0.03, "lambda_after_24": 0.02, "step": 0.9, "noise": 0.1, "storage_exp": 0.1,
                   "grade_A": 1, "grade_B": 0.9, "grade_C": 0.7, "grade_D": 0.5},
            damage_rate_pa=0.1, repair_share=0.7, repair_cost_min=50, repair_cost_max=300,
            share_of_fleet=1.0, term_mix={12: 0.1, 24: 0.5, 36: 0.3, 48: 0.1}, monthly_rate_pct_of_landed=0.04,
        ),
        "no_cadence": FamilyConfig(
            name="no_cadence", first_launch=date(2020, 3, 15), launch_cadence_months=None, launch_month=None,
            list_price_min=1, list_price_max=2, storage_options=[1], base_storage_gb=1,
            discount_min=0, discount_max=0, freight_duty_pct=0,
            truth={"base": 0.9, "lambda": 0.03, "lambda_after_24": 0.02, "step": 0.9, "noise": 0.1, "storage_exp": 0.1,
                   "grade_A": 1, "grade_B": 0.9, "grade_C": 0.7, "grade_D": 0.5},
            damage_rate_pa=0.1, repair_share=0.7, repair_cost_min=50, repair_cost_max=300,
            share_of_fleet=1.0, term_mix_36=0.3, monthly_rate_pct_of_landed=0.04,
        ),
    }


def _family_kwargs(**over) -> dict:
    """Keyword arguments of a valid FamilyConfig; ``over`` replaces or removes (None) entries."""
    base = dict(
        name="fam", first_launch=date(2019, 9, 15), launch_cadence_months=12, launch_month=9,
        list_price_min=799, list_price_max=1299, storage_options=[128, 256], base_storage_gb=128,
        discount_min=0.1, discount_max=0.2, freight_duty_pct=0.03,
        truth={"base": 0.9, "lambda": 0.03, "lambda_after_24": 0.02, "step": 0.9, "noise": 0.1, "storage_exp": 0.1,
               "grade_A": 1, "grade_B": 0.9, "grade_C": 0.7, "grade_D": 0.5},
        damage_rate_pa=0.1, repair_share=0.7, repair_cost_min=50, repair_cost_max=300,
        share_of_fleet=1.0, monthly_rate_pct_of_landed=0.04,
    )
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def test_term_mix_is_validated_and_derived_from_term_mix_36():
    from restwert.config import TERM_MONTHS

    assert TERM_MONTHS == (12, 24, 36, 48)
    # four terms, shares sum to 1: accepted, keys ascending, term_draw renormalises exactly
    fc = FamilyConfig(**_family_kwargs(term_mix={48: 0.05, 12: 0.10, 36: 0.35, 24: 0.50}))
    assert fc.term_mix == {12: 0.10, 24: 0.50, 36: 0.35, 48: 0.05}
    terms, probs = fc.term_draw()
    assert terms == [12, 24, 36, 48] and sum(probs) == pytest.approx(1.0, abs=1e-12)
    # legacy scalar only: derived as {24: 1 - p, 36: p}
    legacy = FamilyConfig(**_family_kwargs(term_mix_36=0.3))
    assert legacy.term_mix == {24: pytest.approx(0.7), 36: pytest.approx(0.3)}
    assert legacy.term_draw()[0] == [24, 36]
    # both given: term_mix wins, term_mix_36 is ignored
    both = FamilyConfig(**_family_kwargs(term_mix={12: 0.5, 48: 0.5}, term_mix_36=0.99))
    assert both.term_mix == {12: 0.5, 48: 0.5}
    # a subset of the closed list is fine (one term only is a valid policy)
    assert FamilyConfig(**_family_kwargs(term_mix={36: 1.0})).term_mix == {36: 1.0}
    # the tolerance on the sum is 1e-6
    assert FamilyConfig(**_family_kwargs(term_mix={24: 0.5, 36: 0.5 + 5e-7})).term_mix[36] == pytest.approx(0.5, abs=1e-6)
    # refused: neither field, a key outside {12, 24, 36, 48}, a sum away from 1, a negative share, a term_mix_36 outside [0, 1]
    with pytest.raises(ValueError, match="term_mix"):
        FamilyConfig(**_family_kwargs())
    with pytest.raises(ValueError, match="keys must be from"):
        FamilyConfig(**_family_kwargs(term_mix={18: 0.5, 24: 0.5}))
    with pytest.raises(ValueError, match="sum to 1"):
        FamilyConfig(**_family_kwargs(term_mix={12: 0.5, 24: 0.5, 36: 0.1}))
    with pytest.raises(ValueError, match="sum to 1"):
        FamilyConfig(**_family_kwargs(term_mix={24: 0.5, 36: 0.5 + 1e-5}))
    with pytest.raises(ValueError, match="negative"):
        FamilyConfig(**_family_kwargs(term_mix={12: -0.2, 24: 0.7, 36: 0.5}))
    with pytest.raises(ValueError, match="term_mix_36"):
        FamilyConfig(**_family_kwargs(term_mix_36=1.2))
    with pytest.raises(ValueError, match="term_mix"):
        FamilyConfig(**_family_kwargs(term_mix={}))
    # the shipped configs carry a four-term mix per family (the legacy scalar is gone from the YAML)
    for fam, fc in config.load_generator_config().families.items():
        assert set(fc.term_mix) == set(TERM_MONTHS), fam
        assert sum(fc.term_mix.values()) == pytest.approx(1.0, abs=1e-6), fam


def test_next_launch_date_september_rule():
    fams = _iphone_cfg()
    assert dates.next_launch_date("iphone_like", date(2024, 6, 30), fams) == date(2024, 9, 15)
    assert dates.next_launch_date("iphone_like", date(2024, 9, 15), fams) == date(2025, 9, 15)
    assert dates.next_launch_date("iphone_like", date(2024, 9, 14), fams) == date(2024, 9, 15)
    assert dates.next_launch_date("iphone_like", date(2010, 1, 1), fams) == date(2019, 9, 15)
    assert dates.next_launch_date("no_cadence", date(2024, 1, 1), fams) is None


def test_expected_launch_steps_september_rule():
    fams = _iphone_cfg()
    assert dates.expected_launch_steps("iphone_like", date(2024, 6, 30), date(2024, 12, 31), fams) == 1
    assert dates.expected_launch_steps("iphone_like", date(2024, 6, 30), date(2026, 9, 15), fams) == 3
    assert dates.expected_launch_steps("iphone_like", date(2024, 9, 15), date(2025, 9, 14), fams) == 0
    assert dates.expected_launch_steps("iphone_like", date(2024, 9, 15), date(2024, 9, 15), fams) == 0
    assert dates.expected_launch_steps("iphone_like", date(2025, 1, 1), date(2024, 1, 1), fams) == 0
    assert dates.expected_launch_steps("no_cadence", date(2020, 1, 1), date(2030, 1, 1), fams) == 0


# --------------------------------------------------------------------------- config

_THRESHOLDS_OK = """
version: 1
thresholds:
  repair_max_share_of_rv:
    values: {iphone_like: 0.40, android_like: 0.35, laptop_like: 0.45}
    unit: ratio
    owner: "Head of Service Operations (name)"
    rationale: "placeholder"
    valid_from: 2026-09-01
    placeholder_default: true
    rule_ids: [R01]
  aging_days_90:
    value: 90
    unit: days
    owner: "CFO (name)"
    rationale: "first aging bucket"
    valid_from: 2026-09-01
    rule_ids: [R03]
  as_is_only_grade:
    value: "D"
    unit: grade
    owner: "Head of Recommerce (name)"
    rationale: "grade D only"
    valid_from: 2026-09-01
    rule_ids: [R02]
  replacement_requires_wipe:
    value: true
    unit: bool
    owner: "Data Protection Officer (name)"
    rationale: "never without wipe"
    valid_from: 2026-09-01
    placeholder_default: false
    rule_ids: [R04]
"""


def test_load_thresholds_and_get(tmp_path: Path):
    p = tmp_path / "thresholds.yaml"
    p.write_text(_THRESHOLDS_OK, encoding="utf-8")
    thr = config.load_thresholds(p)
    r = thr.get("repair_max_share_of_rv", "android_like")
    assert isinstance(r, records.ResolvedThreshold)
    assert r.value == 0.35
    assert r.resolved_key == "repair_max_share_of_rv[android_like]"
    assert r.key == "repair_max_share_of_rv"
    assert r.owner == "Head of Service Operations (name)"
    assert r.unit == "ratio"
    assert r.rule_ids == ["R01"]
    simple = thr.get("aging_days_90")
    assert simple.value == 90 and simple.resolved_key == "aging_days_90" and simple.owner == "CFO (name)"
    assert thr.get("as_is_only_grade").value == "D"
    assert thr.get("replacement_requires_wipe").value is True
    assert thr.thresholds["replacement_requires_wipe"].placeholder_default is False
    assert thr.thresholds["aging_days_90"].placeholder_default is True


def test_thresholds_get_requires_sub_for_values_and_raises_on_missing(tmp_path: Path):
    p = tmp_path / "thresholds.yaml"
    p.write_text(_THRESHOLDS_OK, encoding="utf-8")
    thr = config.load_thresholds(p)
    with pytest.raises(ValueError):
        thr.get("repair_max_share_of_rv")
    with pytest.raises(KeyError, match="repair_max_share_of_rv"):
        thr.get("repair_max_share_of_rv", "tablet_like")
    with pytest.raises(KeyError, match="does_not_exist"):
        thr.get("does_not_exist")


def test_load_thresholds_raises_on_missing_owner(tmp_path: Path):
    bad = _THRESHOLDS_OK.replace('    owner: "CFO (name)"\n', "")
    p = tmp_path / "thresholds.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="threshold aging_days_90 has no owner"):
        config.load_thresholds(p)


def test_load_thresholds_raises_on_empty_owner(tmp_path: Path):
    bad = _THRESHOLDS_OK.replace('owner: "CFO (name)"', 'owner: "  "')
    p = tmp_path / "thresholds.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="has no owner"):
        config.load_thresholds(p)


def test_load_thresholds_raises_on_missing_valid_from(tmp_path: Path):
    bad = _THRESHOLDS_OK.replace(
        '    owner: "CFO (name)"\n    rationale: "first aging bucket"\n    valid_from: 2026-09-01\n',
        '    owner: "CFO (name)"\n    rationale: "first aging bucket"\n',
    )
    assert "valid_from" not in bad.split("aging_days_90")[1].split("as_is_only_grade")[0]
    p = tmp_path / "thresholds.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="threshold aging_days_90 has no valid_from"):
        config.load_thresholds(p)


def test_load_thresholds_raises_on_value_and_values(tmp_path: Path):
    bad = _THRESHOLDS_OK.replace("    value: 90\n", "    value: 90\n    values: {a: 1}\n")
    p = tmp_path / "thresholds.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        config.load_thresholds(p)


def test_load_generator_config_shipped():
    cfg = config.load_generator_config()
    assert cfg.seed == 42
    assert set(cfg.families) == {"iphone_like", "android_like", "laptop_like"}
    assert cfg.families["iphone_like"].name == "iphone_like"
    assert cfg.families["laptop_like"].launch_cadence_months == 18
    assert abs(sum(f.share_of_fleet for f in cfg.families.values()) - 1.0) < 1e-9
    assert cfg.history_start <= cfg.purchase_end <= cfg.as_of


def test_assumptions_and_kpi_targets_loaders(tmp_path: Path):
    a_path = tmp_path / "assumptions.yaml"
    a_path.write_text(
        "version: 1\nblocks:\n"
        "  planned_rv_ratio: {owner: 'CFO (name)', values: {iphone_like: 0.20}}\n"
        "  holding_cost_per_day_eur: {owner: 'CFO (name)', value: 0.30, note: placeholder}\n"
        "  channel_fees: {owner: 'Head of Recommerce (name)', values: {marketplace: {fee_pct: 0.12, days_to_cash: 28}}}\n",
        encoding="utf-8",
    )
    a = config.load_assumptions(a_path)
    assert a.get("planned_rv_ratio", "iphone_like") == 0.20
    assert a.get("holding_cost_per_day_eur") == 0.30
    assert a.get("channel_fees", "marketplace")["fee_pct"] == 0.12
    assert a.get("channel_fees")["marketplace"]["days_to_cash"] == 28
    assert a.owner("holding_cost_per_day_eur") == "CFO (name)"
    with pytest.raises(KeyError):
        a.get("nope")
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nblocks:\n  x: {value: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="has no owner"):
        config.load_assumptions(bad)

    k_path = tmp_path / "kpi_targets.yaml"
    k_path.write_text(
        "version: 1\nsavings_plan_owner: 'Head of Indirect Procurement (name)'\n"
        "savings_plan_eur: {2024: 200000, 2025: 250000}\ntargets_owner: 'CFO (name)'\n"
        "targets: {KPI_TOP_LIFECYCLE_MARGIN: 40}\n",
        encoding="utf-8",
    )
    t = config.load_kpi_targets(k_path)
    assert t.savings_plan_eur[2025] == 250000
    assert t.targets["KPI_TOP_LIFECYCLE_MARGIN"] == 40


def test_file_sha256(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"abc")
    assert config.file_sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --------------------------------------------------------------------------- schema + db


def test_create_schema_in_memory_creates_every_table():
    con = db.connect(":memory:")
    db.create_schema(con)
    existing = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    for t in schema.TABLE_ORDER:
        assert t in existing, t
    assert set(schema.SOURCE_TABLES) | set(schema.DERIVED_TABLES) == set(schema.TABLE_ORDER)
    assert set(schema.IMMUTABLE_TABLES) <= set(schema.TABLE_ORDER)
    assert len(schema.SOURCE_TABLES) == len(schema.ROW_MODELS)
    # idempotent, and drop_derived keeps source and immutable tables
    db.create_schema(con, drop_derived=True)
    existing2 = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert existing2 == existing


def test_source_table_ddl_ends_with_is_synthetic_and_source_file():
    for t in schema.SOURCE_TABLES:
        assert "is_synthetic BOOLEAN NOT NULL" in schema.DDL[t]
        assert "source_file VARCHAR" in schema.DDL[t]
        fields = list(schema.ROW_MODELS[t].model_fields)
        assert fields[-2:] == ["is_synthetic", "source_file"]


def test_write_df_replace_then_append_counts():
    con = db.connect(":memory:")
    db.create_schema(con)
    df = pd.DataFrame(
        {
            "model": ["P-Gen01", "P-Gen02"],
            "model_family": ["iphone_like", "iphone_like"],
            "generation": [1, 2],
            "launch_date": [date(2019, 9, 15), "2020-09-15"],
            "list_price": [999.0, 1099.0],
            "base_storage_gb": [128, 128],
            "is_synthetic": [True, True],
            "source_file": ["model_catalogue.csv", None],
        }
    )
    assert db.write_df(con, "model_catalogue", df, mode="replace") == 2
    assert con.execute("SELECT count(*) FROM model_catalogue").fetchone()[0] == 2
    df2 = df.copy()
    df2["model"] = ["P-Gen03", "P-Gen04"]
    assert db.append_rows(con, "model_catalogue", df2) == 2
    assert con.execute("SELECT count(*) FROM model_catalogue").fetchone()[0] == 4
    assert db.write_df(con, "model_catalogue", df, mode="replace") == 2
    assert con.execute("SELECT count(*) FROM model_catalogue").fetchone()[0] == 2
    out = db.read_df(con, "SELECT model, launch_date, list_price FROM model_catalogue ORDER BY model")
    assert list(out["model"]) == ["P-Gen01", "P-Gen02"]
    assert pd.Timestamp(out["launch_date"].iloc[1]).date() == date(2020, 9, 15)
    assert float(out["list_price"].iloc[0]) == 999.0
    assert db.write_df(con, "model_catalogue", df.iloc[0:0], mode="replace") == 0
    assert con.execute("SELECT count(*) FROM model_catalogue").fetchone()[0] == 0


def test_write_df_handles_nan_money_and_missing_columns():
    con = db.connect(":memory:")
    db.create_schema(con)
    df = pd.DataFrame(
        {
            "po_number": ["PO-1", "PO-2"],
            "supplier": ["Supplier-A", "Supplier-B"],
            "order_date": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "promised_date": [date(2024, 1, 20), date(2024, 2, 20)],
            "qty_ordered": [10, 5],
            "qty_delivered": [10, 4],
            "unit_price": [100.0, 200.0],
            "benchmark_price": [float("nan"), 190.0],
            "is_synthetic": [True, True],
        }
    )
    assert db.write_df(con, "purchase_orders", df) == 2
    out = db.read_df(con, "SELECT benchmark_price, delivered_date, supplier_contract_id FROM purchase_orders ORDER BY po_number")
    assert pd.isna(out["benchmark_price"].iloc[0])
    assert float(out["benchmark_price"].iloc[1]) == 190.0
    assert out["delivered_date"].isna().all()
    assert out["supplier_contract_id"].isna().all()


def test_immutable_tables_refuse_replace():
    con = db.connect(":memory:")
    db.create_schema(con)
    with pytest.raises(ValueError, match="immutable"):
        db.write_df(con, "decision_log", pd.DataFrame(), mode="replace")


def test_validate_frame_rejects_bad_grade_with_row_index():
    df = pd.DataFrame(
        {
            "refurb_id": ["RF-1", "RF-2", "RF-3"],
            "serial": ["D-1", "D-2", "D-3"],
            "start_date": [date(2024, 1, 1)] * 3,
            "end_date": [date(2024, 1, 5)] * 3,
            "days": [4, 4, 4],
            "cost": [10.0, 12.0, 14.0],
            "grade_out": ["A", "B", "E"],
            "outcome": ["sellable", "sellable", "sellable"],
            "is_synthetic": [True, True, True],
        }
    )
    with pytest.raises(ValueError) as exc:
        schema.validate_frame("refurbishment", df)
    msg = str(exc.value)
    assert msg.startswith("refurbishment row 2 field grade_out:")


def test_validate_frame_coerces_strings_and_missing():
    df = pd.DataFrame(
        {
            "event_id": ["EV-1"],
            "serial": ["D-1"],
            "contract_id": [None],
            "event_type": ["return"],
            "event_date": ["2024-03-01"],
            "cost": ["9.5"],
            "damage_type": [float("nan")],
            "resolved": [None],
            "replacement_serial": [None],
            "return_date": ["2024-03-01"],
            "grade_pre_return": ["A"],
            "grade_inspected": ["B"],
            "wipe_certificate": ["true"],
            "note": [""],
            "is_synthetic": ["false"],
            "source_file": ["events.csv"],
        }
    )
    out = schema.validate_frame("events", df)
    assert list(out.columns) == list(schema.ROW_MODELS["events"].model_fields)
    row = out.iloc[0]
    assert row["event_date"] == date(2024, 3, 1)
    assert row["cost"] == 9.5
    assert row["damage_type"] is None
    assert bool(row["wipe_certificate"]) is True
    assert bool(row["is_synthetic"]) is False
    assert row["note"] is None
    with pytest.raises(ValueError, match="missing required columns"):
        schema.validate_frame("events", df.drop(columns=["event_type"]))
    with pytest.raises(KeyError):
        schema.validate_frame("device_pnl", df)


def test_is_synthetic_and_runs():
    con = db.connect(":memory:")
    db.create_schema(con)
    assert db.is_synthetic(con) is False
    df = pd.DataFrame(
        {
            "serial": ["D-1"], "model_family": ["iphone_like"], "model": ["P-Gen01"], "storage_gb": [128],
            "colour": ["black"], "launch_date": [date(2019, 9, 15)], "purchase_date": [date(2022, 1, 5)],
            "purchase_price": [900.0], "landed_cost": [930.0], "supplier": ["Supplier-A"],
            "channel_in": ["distributor"], "po_number": ["PO-1"], "contract_id": [None], "is_synthetic": [False],
        }
    )
    db.write_df(con, "devices", df)
    assert db.is_synthetic(con) is False
    df["is_synthetic"] = [True]
    db.write_df(con, "devices", df)
    assert db.is_synthetic(con) is True
    run_id = db.new_run(con, "load", 42, date(2026, 6, 30), "abc")
    assert run_id.startswith("load-")
    db.finish_run(con, run_id, {"devices": 1})
    row = db.read_df(con, "SELECT * FROM runs WHERE run_id = ?", [run_id]).iloc[0]
    assert row["command"] == "load"
    assert '"devices": 1' in row["counts_json"]
    assert pd.notna(row["finished_at"])


def test_records_hash_and_build_record():
    thr = records.ResolvedThreshold(
        key="aging_days_90", resolved_key="aging_days_90", value=90, unit="days", owner="CFO (name)", rule_ids=["R03"]
    )
    inputs = {"days_in_stock": 95, "book_value_before": 100.0, "as_of": date(2026, 6, 30)}
    rec = records.build_record(
        run_id="decide-1", as_of=date(2026, 6, 30), rule_id="R03", rule_version="1.0", subject_type="device",
        subject_id="D-1", outcome="write_down_90", outcome_detail="95 days in stock", thr=thr, inputs=inputs,
        value_at_stake_eur=10.0,
    )
    assert rec.threshold_owner == "CFO (name)"
    assert rec.threshold_value == 90
    assert rec.input_hash == records.make_input_hash("R03", "D-1", date(2026, 6, 30), inputs)
    assert rec.input_hash != records.make_input_hash("R03", "D-2", date(2026, 6, 30), inputs)
    assert records.inputs_to_json({"b": 1, "a": date(2026, 1, 1)}) == '{"a": "2026-01-01", "b": 1}'
    kv = records.KpiValue.not_measurable("denominator is 0")
    assert kv.status == "not_measurable" and kv.value is None and kv.n == 0
    adv = records.Advisory(kind="sell_before_launch", run_id="rv-2026-06-30", payload={"drop_pct": 0.1}, confidence="low")
    rec2 = records.build_record(
        run_id="decide-1", as_of=date(2026, 6, 30), rule_id="R03", rule_version="1.0", subject_type="device",
        subject_id="D-1", outcome="write_down_90", outcome_detail="x", thr=thr, inputs=inputs, advisory=adv,
    )
    assert rec2.outcome == rec.outcome and rec2.input_hash == rec.input_hash


def test_package_metadata_and_data_model_render():
    assert __version__ == "0.3.0"  # bumped by v0.3 (support and MDM allocations, the page)
    assert GOVERNANCE_PRINCIPLE == "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD."
    md = schema.render_data_model_md()
    assert GOVERNANCE_PRINCIPLE in md
    for t in schema.TABLE_ORDER:
        assert f"### `{t}`" in md
    assert chr(0x2014) not in md  # no em dash in generated prose


def test_thresholds_get_refuses_a_threshold_not_yet_in_force(tmp_path: Path):
    p = tmp_path / "thresholds.yaml"
    p.write_text(_THRESHOLDS_OK, encoding="utf-8")
    thr = config.load_thresholds(p)
    r = thr.get("aging_days_90", as_of=date(2026, 9, 1))     # valid_from 2026-09-01: in force on the day
    assert r.valid_from == date(2026, 9, 1)
    with pytest.raises(ValueError, match="cannot fire before it is in force"):
        thr.get("aging_days_90", as_of=date(2026, 8, 31))
    with pytest.raises(ValueError, match="repair_max_share_of_rv"):
        thr.get("repair_max_share_of_rv", "android_like", as_of=date(2026, 1, 1))
    assert thr.get("aging_days_90").valid_from == date(2026, 9, 1)   # no as_of: no check, but the date travels
