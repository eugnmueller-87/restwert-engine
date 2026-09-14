"""Tests for module 4 (decisions): spec section 6.5.

Every rule at threshold, threshold - epsilon and threshold + epsilon; record fields; advisory
invariance; log dedupe; ledger idempotence; queue priorities; generated rules doc; the shipped
thresholds file; and a runner end-to-end pass on a five-device fixture database.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_fixture_module():
    """Load tests/fixtures/decision_inputs.py by path (no dependency on a tests package)."""
    path = ROOT / "tests" / "fixtures" / "decision_inputs.py"
    spec = importlib.util.spec_from_file_location("decision_inputs_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fx = _load_fixture_module()

from restwert import db  # noqa: E402
from restwert.config import load_thresholds  # noqa: E402
from restwert.decisions import registry, rules, runner  # noqa: E402
from restwert.decisions.rules import (  # noqa: E402
    NO_ACTION_OUTCOMES,
    PRIORITY_1_OUTCOMES,
    decide_aging_write_down,
    decide_channel,
    decide_grade_a_replacement,
    decide_price_protection_reminder,
    decide_renewal_alert,
    decide_repair,
)

AS_OF = fx.AS_OF
RUN_ID = fx.RUN_ID
EM_DASH = chr(0x2014)   # must not appear in anything this module writes


@pytest.fixture(scope="module")
def thr(tmp_path_factory):
    return fx.thresholds_from_tmp(tmp_path_factory.mktemp("thr"))


@pytest.fixture()
def assumptions():
    return fx.make_assumptions()


def _repair(thr, **over):
    base = dict(
        serial="D-000001",
        family="android_like",
        repair_quote_eur=70.0,
        forecast_rv_after_repair_eur=200.0,
        forecast_rv_as_is_eur=50.0,
        thr=thr,
        as_of=AS_OF,
        run_id=RUN_ID,
    )
    base.update(over)
    return decide_repair(**base)


def _aging(thr, days, book=1000.0, already=0.0, serial="D-000009"):
    return decide_aging_write_down(
        serial=serial,
        days_in_stock=days,
        book_value_before=book,
        already_written_down=already,
        thr=thr,
        as_of=AS_OF,
        run_id=RUN_ID,
    )


def _replacement(thr, **over):
    base = dict(
        serial="D-000002",
        family="iphone_like",
        grade_inspected="A",
        months_since_launch=24.0,
        storage_gb=256,
        wipe_certificate=True,
        thr=thr,
        as_of=AS_OF,
        run_id=RUN_ID,
    )
    base.update(over)
    return decide_grade_a_replacement(**base)


def _price_protection(thr, days_left, **over):
    drop = AS_OF - timedelta(days=45) + timedelta(days=days_left)   # deadline = drop + 45 = as_of + days_left
    base = dict(
        po_number="PO-000001",
        supplier="Supplier-B",
        has_price_protection=True,
        price_protection_days=45,
        price_drop_date=drop,
        price_drop_amount=20.0,
        qty_delivered=50,
        thr=thr,
        as_of=AS_OF,
        run_id=RUN_ID,
    )
    base.update(over)
    return decide_price_protection_reminder(**base)


def _renewal(thr, d, notice_days=30, auto_renewal=False, annual=100000.0, contract_type="supplier_contract",
             contract_id="SC-01"):
    end = AS_OF + timedelta(days=d + notice_days)   # notice_deadline = end - notice = as_of + d
    return decide_renewal_alert(
        contract_type=contract_type,
        contract_id=contract_id,
        counterparty="Supplier-B",
        end_date=end,
        notice_days=notice_days,
        auto_renewal=auto_renewal,
        annual_value_eur=annual,
        thr=thr,
        as_of=AS_OF,
        run_id=RUN_ID,
    )


# --------------------------------------------------------------------------------------
# thresholds file
# --------------------------------------------------------------------------------------


def test_shipped_thresholds_load_with_exact_keys():
    thr = fx.shipped_thresholds()
    assert set(thr.thresholds) == set(fx.EXPECTED_THRESHOLD_KEYS) | set(fx.V02_THRESHOLD_KEYS)
    assert len(thr.thresholds) == 24  # 21 v0.1 keys plus the three v0.2 keys of R07, ADV03, ADV04
    for key, spec in thr.thresholds.items():
        assert spec.unit and spec.owner.strip() and spec.rationale, key
        assert isinstance(spec.valid_from, date), key
        assert isinstance(spec.placeholder_default, bool), key
        assert spec.rule_ids, key
        assert (spec.value is None) != (spec.values is None), key
    for rule in registry.RULES.values():
        for key in rule.threshold_keys:
            assert key in thr.thresholds, (rule.rule_id, key)
    # every rule id on every threshold resolves to a rule or a registered advisory, never to a typo
    for key, spec in thr.thresholds.items():
        for rid in spec.rule_ids:
            assert rid in registry.RULES or rid in registry.ADVISORY_SPECS, (key, rid)
    for adv in registry.ADVISORY_SPECS.values():
        for key in adv["threshold_keys"]:
            assert key in thr.thresholds, key


def test_thresholds_yaml_is_clean_utf8():
    raw = (ROOT / "config" / "thresholds.yaml").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert EM_DASH not in text
    assert "placeholder_default" in text


def test_inline_yaml_matches_shipped_values(tmp_path):
    inline = fx.thresholds_from_tmp(tmp_path)
    shipped = fx.shipped_thresholds()
    for key in fx.EXPECTED_THRESHOLD_KEYS:
        a, b = inline.thresholds[key], shipped.thresholds[key]
        assert a.value == b.value and a.values == b.values and a.unit == b.unit, key


# --------------------------------------------------------------------------------------
# R01 repair or not
# --------------------------------------------------------------------------------------


def test_r01_quote_equal_share_times_rv_is_repair(thr):
    rec = _repair(thr, repair_quote_eur=0.35 * 200.0)
    assert rec.outcome == "repair"
    assert rec.threshold_key.startswith("repair_max_share_of_rv")
    assert rec.threshold_value == 0.35
    assert rec.value_at_stake_eur == pytest.approx(200.0 - 70.0 - 50.0)


def test_r01_quote_just_above_share_is_no_repair(thr):
    rec = _repair(thr, repair_quote_eur=70.0 + 0.01)
    assert rec.outcome == "no_repair_quote_above_share"
    assert "above 35 %" in rec.outcome_detail
    assert _repair(thr, repair_quote_eur=69.99).outcome == "repair"


def test_r01_rv_below_min_is_no_repair_below_min_rv(thr):
    rec = _repair(thr, forecast_rv_after_repair_eur=39.99, repair_quote_eur=1.0)
    assert rec.outcome == "no_repair_below_min_rv"
    assert rec.threshold_key.startswith("repair_min_rv_eur")
    assert rec.threshold_value == 40
    # exactly the minimum is not below it
    assert _repair(thr, forecast_rv_after_repair_eur=40.0, repair_quote_eur=1.0).outcome == "repair"


def test_r01_family_specific_threshold(thr):
    # iphone_like share 0.40: a 39 % quote repairs, for android_like (0.35) it does not
    assert _repair(thr, family="iphone_like", repair_quote_eur=78.0).outcome == "repair"
    assert _repair(thr, family="android_like", repair_quote_eur=78.0).outcome == "no_repair_quote_above_share"


# --------------------------------------------------------------------------------------
# R02 channel choice
# --------------------------------------------------------------------------------------


def test_r02_grade_d_goes_as_is(thr):
    rec = decide_channel(thr=thr, **fx.channel_kwargs(grade="D"))
    assert rec.outcome == "as_is"
    assert rec.inputs["allowed_channels"] == ["as_is"]
    assert rec.inputs["channels"]["marketplace"]["allowed"] is False


def test_r02_other_grades_never_as_is(thr):
    rec = decide_channel(thr=thr, **fx.channel_kwargs(grade="C", forecast_rv_by_channel={
        "marketplace": 10.0, "employee_buyout": 10.0, "b2b_wholesale": 10.0, "as_is": 900.0}))
    assert rec.outcome != "as_is"
    assert rec.inputs["channels"]["as_is"]["allowed"] is False


def test_r02_ineligible_buyout_removes_employee_buyout(thr):
    rec = decide_channel(thr=thr, **fx.channel_kwargs(buyout_eligible=False))
    assert "employee_buyout" not in rec.inputs["allowed_channels"]
    assert rec.outcome != "employee_buyout"
    eligible = decide_channel(thr=thr, **fx.channel_kwargs(buyout_eligible=True))
    assert eligible.outcome == "employee_buyout"


def test_r02_days_to_cash_filter_never_removes_last_channel(thr):
    slow = dict(fx.DAYS_TO_CASH)
    slow["b2b_wholesale"] = 61.0
    rec = decide_channel(thr=thr, **fx.channel_kwargs(days_to_cash=slow))
    assert "b2b_wholesale" not in rec.inputs["allowed_channels"]
    # only channel left, and it is too slow: it stays
    only = decide_channel(thr=thr, **fx.channel_kwargs(
        forecast_rv_by_channel={"b2b_wholesale": 250.0, "as_is": 100.0}, days_to_cash=slow))
    assert only.outcome == "b2b_wholesale"
    assert only.inputs["allowed_channels"] == ["b2b_wholesale"]
    # every channel too slow: the fastest one survives
    all_slow = {c: 100.0 + i for i, c in enumerate(("employee_buyout", "marketplace", "b2b_wholesale", "as_is"))}
    fastest = decide_channel(thr=thr, **fx.channel_kwargs(days_to_cash=all_slow))
    assert fastest.inputs["allowed_channels"] == ["employee_buyout"]


def test_r02_uplift_equality_picks_slower_channel(thr):
    # fastest = employee_buyout (14 d). Make marketplace beat it by exactly the 15 EUR uplift.
    emp_rv = 200.0
    emp_net = fx.net("employee_buyout", emp_rv)
    target_net = emp_net + 15.0
    mkt_rv = (target_net + fx.FEE_FIXED["marketplace"] + fx.HOLDING * fx.DAYS_TO_CASH["marketplace"]) / (1 - fx.FEE_PCT["marketplace"])
    rv = {"marketplace": mkt_rv, "employee_buyout": emp_rv, "b2b_wholesale": 1.0, "as_is": 1.0}
    rec = decide_channel(thr=thr, **fx.channel_kwargs(forecast_rv_by_channel=rv))
    assert rec.outcome == "marketplace"
    assert rec.inputs["net_gain_best_over_fastest"] == pytest.approx(15.0, abs=1e-6)
    assert rec.threshold_key == "channel_min_net_uplift_eur"
    # one cent less uplift: the fastest channel wins
    rv["marketplace"] = mkt_rv - 0.02
    assert decide_channel(thr=thr, **fx.channel_kwargs(forecast_rv_by_channel=rv)).outcome == "employee_buyout"


def test_r02_net_formula_and_value_at_stake(thr):
    rec = decide_channel(thr=thr, **fx.channel_kwargs())
    ch = rec.inputs["channels"]
    assert ch["marketplace"]["net"] == pytest.approx(300 * 0.88 - 2.5 - 0.3 * 28, abs=0.01)
    assert ch["employee_buyout"]["net"] == pytest.approx(285 - 0.3 * 14, abs=0.01)
    nets = sorted((v["net"] for c, v in ch.items() if v["allowed"]), reverse=True)
    assert rec.value_at_stake_eur == pytest.approx(nets[0] - nets[1], abs=0.01)


# --------------------------------------------------------------------------------------
# R03 aging write-down
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "days, outcome",
    [(89, "none"), (90, "none"), (91, "write_down_90"), (180, "write_down_90"), (181, "write_down_180")],
)
def test_r03_boundaries(thr, days, outcome):
    rec = _aging(thr, days)
    assert rec.outcome == outcome
    expected_key = {"none": "aging_days_90", "write_down_90": "aging_days_90", "write_down_180": "aging_days_180"}[outcome]
    assert rec.threshold_key == expected_key


def test_r03_amount_is_target_minus_already_written_down(thr):
    rec = _aging(thr, 181, book=900.0, already=100.0)   # gross 1000, target 250
    assert rec.inputs["amount"] == pytest.approx(150.0)
    assert rec.value_at_stake_eur == pytest.approx(150.0)
    assert rec.inputs["book_value_after"] == pytest.approx(750.0)
    rec90 = _aging(thr, 100, book=900.0, already=100.0)  # target 100, already 100 -> 0
    assert rec90.outcome == "write_down_90"
    assert rec90.inputs["amount"] == 0.0
    over = _aging(thr, 100, book=700.0, already=300.0)   # target 100 < already -> floored at 0
    assert over.inputs["amount"] == 0.0
    assert _aging(thr, 10).inputs["amount"] == 0.0


# --------------------------------------------------------------------------------------
# R04 grade A as replacement
# --------------------------------------------------------------------------------------


def test_r04_months_equal_max_is_eligible(thr):
    rec = _replacement(thr, months_since_launch=24.0)
    assert rec.outcome == "eligible_as_replacement"
    assert rec.threshold_key.startswith("replacement_max_months_since_launch")
    just_over = _replacement(thr, months_since_launch=24.01)
    assert just_over.outcome == "not_eligible"
    assert "months since launch" in just_over.outcome_detail
    assert just_over.threshold_key.startswith("replacement_max_months_since_launch")


def test_r04_first_failing_condition_is_named(thr):
    grade = _replacement(thr, grade_inspected="B", months_since_launch=30.0, storage_gb=64, wipe_certificate=False)
    assert grade.outcome == "not_eligible"
    assert "grade B" in grade.outcome_detail
    assert grade.inputs["first_failing_condition"].startswith("grade")
    storage = _replacement(thr, storage_gb=64, wipe_certificate=False)
    assert "storage" in storage.outcome_detail
    assert storage.threshold_key.startswith("replacement_min_storage_gb")
    wipe = _replacement(thr, wipe_certificate=None)
    assert "wipe" in wipe.outcome_detail
    assert wipe.threshold_key == "replacement_requires_wipe"
    assert wipe.threshold_owner.startswith("Data Protection Officer")
    assert _replacement(thr, family="laptop_like", months_since_launch=36.0, storage_gb=256).outcome == "eligible_as_replacement"


# --------------------------------------------------------------------------------------
# R05 price protection
# --------------------------------------------------------------------------------------


def test_r05_days_left_equal_reminder_is_reminder(thr):
    rec = _price_protection(thr, days_left=14)
    assert rec.outcome == "claim_reminder"
    assert rec.threshold_key == "price_protection_reminder_days"
    assert rec.value_at_stake_eur == pytest.approx(1000.0)
    assert rec.due_date == AS_OF + timedelta(days=14)
    assert _price_protection(thr, days_left=15).outcome == "claim_open"
    assert _price_protection(thr, days_left=0).outcome == "claim_reminder"


def test_r05_minus_one_day_is_expired(thr):
    rec = _price_protection(thr, days_left=-1)
    assert rec.outcome == "claim_window_expired"
    assert rec.inputs["days_left"] == -1


def test_r05_below_min_claim_and_no_action(thr):
    below = _price_protection(thr, days_left=5, price_drop_amount=9.99)   # 499.50
    assert below.outcome == "below_min_claim"
    assert below.threshold_key == "price_protection_min_claim_eur"
    exact = _price_protection(thr, days_left=5, price_drop_amount=10.0)   # 500.00 is not below
    assert exact.outcome == "claim_reminder"
    assert _price_protection(thr, days_left=5, has_price_protection=False).outcome == "no_action"
    assert _price_protection(thr, days_left=5, price_drop_date=None).outcome == "no_action"
    assert _price_protection(thr, days_left=5, price_protection_days=None).outcome == "no_action"


# --------------------------------------------------------------------------------------
# R06 renewal alert
# --------------------------------------------------------------------------------------


def test_r06_d_equal_lead_is_alert(thr):
    rec = _renewal(thr, d=60)
    assert rec.outcome == "renewal_alert"
    assert rec.threshold_key == "renewal_alert_lead_days"
    assert rec.due_date == AS_OF + timedelta(days=60)
    assert _renewal(thr, d=61).outcome == "no_action"
    assert _renewal(thr, d=0).outcome == "renewal_alert"


def test_r06_high_value_escalates(thr):
    rec = _renewal(thr, d=10, annual=250000.0)
    assert rec.outcome == "renewal_alert_high_value"
    assert rec.threshold_key == "renewal_high_value_eur"
    assert _renewal(thr, d=10, annual=249999.99).outcome == "renewal_alert"


def test_r06_missed_notice(thr):
    assert _renewal(thr, d=-1, auto_renewal=True).outcome == "notice_missed_auto_renews"
    assert _renewal(thr, d=-1, auto_renewal=False).outcome == "expiring_no_notice_possible"
    assert _renewal(thr, d=-40, notice_days=30, auto_renewal=True).outcome == "expired"
    rental = _renewal(thr, d=5, contract_type="rental_contract")
    assert rental.subject_type == "rental_contract"
    with pytest.raises(ValueError):
        _renewal(thr, d=5, contract_type="supplier")


# --------------------------------------------------------------------------------------
# record shape and advisory invariance
# --------------------------------------------------------------------------------------


def _sample_records(thr):
    return [
        _repair(thr),
        decide_channel(thr=thr, **fx.channel_kwargs()),
        _aging(thr, 100),
        _replacement(thr),
        _price_protection(thr, days_left=3),
        _renewal(thr, d=3),
    ]


def test_every_record_carries_governance_fields(thr):
    for rec in _sample_records(thr):
        assert rec.rule_id in registry.RULES
        assert rec.rule_version == rules.RULE_VERSION
        assert rec.threshold_key
        assert rec.threshold_owner.strip()
        assert rec.threshold_value is not None
        assert rec.threshold_unit
        assert re.fullmatch(r"[0-9a-f]{64}", rec.input_hash)
        assert rec.outcome_detail
        assert rec.inputs["as_of"] == AS_OF.isoformat()
        assert rec.outcome in registry.RULES[rec.rule_id].outcomes


def test_advisory_never_changes_outcome(thr):
    adv = fx.sample_advisory()
    plain = _repair(thr)
    with_adv = _repair(thr, advisory=adv)
    assert with_adv.outcome == plain.outcome
    assert with_adv.input_hash == plain.input_hash
    assert with_adv.advisory is not None and plain.advisory is None

    plain = decide_channel(thr=thr, **fx.channel_kwargs())
    with_adv = decide_channel(thr=thr, advisory=adv, **fx.channel_kwargs())
    assert with_adv.outcome == plain.outcome
    assert with_adv.input_hash == plain.input_hash
    assert with_adv.advisory.kind == "sell_before_launch"


def test_same_inputs_same_hash_different_inputs_different_hash(thr):
    a, b = _aging(thr, 100), _aging(thr, 100)
    assert a.input_hash == b.input_hash and a.decision_id != b.decision_id
    assert _aging(thr, 101).input_hash != a.input_hash


# --------------------------------------------------------------------------------------
# frames, log, ledger, queue
# --------------------------------------------------------------------------------------


def test_records_to_frame_sorted_json_and_columns(thr):
    recs = [_repair(thr), decide_channel(thr=thr, advisory=fx.sample_advisory(), **fx.channel_kwargs())]
    frame = runner.records_to_frame(recs, is_synthetic_input=True)
    assert list(frame.columns) == list(runner.LOG_COLUMNS)
    assert len(frame) == 2
    for js in frame["inputs_json"]:
        keys = list(json.loads(js).keys())
        assert keys == sorted(keys)
    assert frame.loc[0, "advisory_json"] is None or pd.isna(frame.loc[0, "advisory_json"])
    adv = json.loads(frame.loc[1, "advisory_json"])
    assert adv["kind"] == "sell_before_launch"
    assert frame["is_synthetic_input"].all()
    assert frame.loc[0, "threshold_value_num"] == pytest.approx(0.35)
    empty = runner.records_to_frame([], is_synthetic_input=False)
    assert list(empty.columns) == list(runner.LOG_COLUMNS) and empty.empty


def test_write_log_appends_then_dedupes(thr):
    con = db.connect(":memory:")
    db.create_schema(con)
    recs = _sample_records(thr)
    frame = runner.records_to_frame(recs, is_synthetic_input=True)
    assert runner.write_log(con, frame) == len(recs)
    again = runner.records_to_frame(_sample_records(thr), is_synthetic_input=True)
    assert runner.write_log(con, again) == 0
    stored = db.read_df(con, "SELECT count(*) AS n FROM decision_log")
    assert int(stored["n"][0]) == len(recs)
    # a changed input is a new row
    changed = runner.records_to_frame([_aging(thr, 101)], is_synthetic_input=True)
    assert runner.write_log(con, changed) == 1


def test_book_write_downs_idempotent(thr):
    con = db.connect(":memory:")
    db.create_schema(con)
    recs = [_aging(thr, 181, book=900.0, already=100.0), _aging(thr, 10), _aging(thr, 100, book=700.0, already=300.0)]
    runner.write_log(con, runner.records_to_frame(recs, True))
    assert runner.book_write_downs(con, recs, RUN_ID) == 1
    ledger = db.read_df(con, "SELECT * FROM write_down_ledger")
    assert len(ledger) == 1
    assert float(ledger["book_value_before"][0]) == pytest.approx(900.0)
    assert float(ledger["amount"][0]) == pytest.approx(150.0)
    assert float(ledger["book_value_after"][0]) == pytest.approx(750.0)
    assert ledger["decision_id"][0] == recs[0].decision_id
    assert ledger["threshold_owner"][0] == recs[0].threshold_owner
    # rerun with fresh record objects (new decision ids, same inputs): nothing booked twice
    rerun = [_aging(thr, 181, book=900.0, already=100.0)]
    assert runner.book_write_downs(con, rerun, "decide-test-0002") == 0
    assert len(db.read_df(con, "SELECT * FROM write_down_ledger")) == 1


def test_build_queue_applies_no_action_and_priority(thr):
    con = db.connect(":memory:")
    db.create_schema(con)
    recs = [
        _aging(thr, 181, serial="D-000101"),                        # write_down_180 -> P1
        _aging(thr, 100, serial="D-000102"),                        # write_down_90  -> P2
        _aging(thr, 10, serial="D-000103"),                         # none           -> excluded
        _replacement(thr, serial="D-000104"),                       # eligible       -> P2
        _replacement(thr, serial="D-000105", grade_inspected="B"),  # not_eligible   -> excluded
        _price_protection(thr, days_left=3, po_number="PO-000101"),   # claim_reminder -> P1
        _price_protection(thr, days_left=30, po_number="PO-000102"),  # claim_open     -> excluded
        _renewal(thr, d=-1, auto_renewal=True, contract_id="SC-101"), # notice_missed  -> P1
        _renewal(thr, d=100, contract_id="SC-102"),                   # no_action      -> excluded
        _repair(thr),                                               # R01 every outcome queued -> P2
        decide_channel(thr=thr, advisory=fx.sample_advisory(), **fx.channel_kwargs()),  # P2 with advisory
    ]
    runner.write_log(con, runner.records_to_frame(recs, True))
    queue = runner.build_queue(con, RUN_ID)
    assert list(queue.columns) == list(runner.QUEUE_COLUMNS)
    outcomes = set(queue["outcome"])
    for rule_id, no_action in NO_ACTION_OUTCOMES.items():
        assert not (outcomes & no_action), rule_id
    assert "none" not in outcomes and "not_eligible" not in outcomes and "claim_open" not in outcomes and "no_action" not in outcomes
    assert len(queue) == 7
    p1 = set(queue.loc[queue["priority"] == 1, "outcome"])
    assert p1 == {"write_down_180", "claim_reminder", "notice_missed_auto_renews"}
    assert p1 <= PRIORITY_1_OUTCOMES
    assert set(queue.loc[queue["priority"] == 2, "outcome"]) == {"write_down_90", "eligible_as_replacement", "repair", "employee_buyout"}
    assert list(queue["priority"]) == sorted(queue["priority"])
    adv_row = queue[queue["advisory_kind"].notna()]
    assert len(adv_row) == 1 and adv_row.iloc[0]["advisory_confidence"] == "medium"
    assert queue["threshold_owner"].str.strip().astype(bool).all()
    assert (queue["explanation"] == queue["explanation"]).all() and queue["explanation"].str.len().gt(0).all()
    assert set(queue["rule_name"]) <= {r.name for r in registry.RULES.values()}


def test_build_queue_advisory_only_row_is_priority_3(thr):
    # R02 has no no-action outcomes, so construct a no-action record carrying an advisory via R01's
    # sibling: a record whose outcome is in the no-action set but which carries an advisory.
    con = db.connect(":memory:")
    db.create_schema(con)
    rec = _aging(thr, 10)   # outcome none
    frame = runner.records_to_frame([rec], True)
    frame.loc[0, "advisory_json"] = json.dumps(fx.sample_advisory().model_dump(), sort_keys=True)
    runner.write_log(con, frame)
    queue = runner.build_queue(con, RUN_ID)
    assert len(queue) == 1 and int(queue.loc[0, "priority"]) == 3


# --------------------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------------------


def test_render_rules_doc_contains_every_threshold_and_owner(thr):
    doc = registry.render_rules_doc(thr)
    for key, spec in thr.thresholds.items():
        assert f"`{key}" in doc, key
        assert spec.owner in doc, key
    for rule in registry.RULES.values():
        assert f"## {rule.rule_id} {rule.name}" in doc
        for outcome in rule.outcomes:
            assert f"`{outcome}`" in doc
    assert "ADV01" in doc and "ADV02" in doc
    assert "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD." in doc
    assert "GENERATED" in doc
    assert EM_DASH not in doc


def test_write_rules_doc_writes_utf8_without_bom(thr, tmp_path):
    path = registry.write_rules_doc(thr, tmp_path / "DECISION_RULES.md")
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8").startswith("# Decision rules")


def test_shipped_rules_doc_is_generated_from_current_thresholds():
    path = ROOT / "docs" / "DECISION_RULES.md"
    assert path.exists(), "run python -m restwert decide (or registry.write_rules_doc) first"
    assert path.read_text(encoding="utf-8") == registry.render_rules_doc(fx.shipped_thresholds())


def test_rule_spec_inputs_come_from_signatures():
    assert registry.RULES["R03"].input_names == ("serial", "days_in_stock", "book_value_before", "already_written_down")
    assert registry.RULES["R02"].accepts_advisory and not registry.RULES["R03"].accepts_advisory


# --------------------------------------------------------------------------------------
# no side effects
# --------------------------------------------------------------------------------------


def test_decisions_package_has_no_io_beyond_duckdb():
    forbidden = ("smtplib", "requests", "httpx", "urllib.request", "boto3", "paramiko", "subprocess", "socket")
    for py in (ROOT / "restwert" / "decisions").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for name in forbidden:
            assert not re.search(rf"^\s*(import|from)\s+{re.escape(name)}\b", text, re.M), (py.name, name)
        assert EM_DASH not in text, py.name


# --------------------------------------------------------------------------------------
# runner end to end on the fixture database
# --------------------------------------------------------------------------------------


def test_runner_end_to_end(thr, assumptions, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    con = fx.build_fixture_db()
    queue, summary = runner.run_all_decisions(con, AS_OF, thr, assumptions)

    assert summary.command == "decide" and summary.run_id
    c = summary.counts
    assert (c["records_R01"], c["records_R02"], c["records_R03"], c["records_R04"], c["records_R05"], c["records_R06"]) == (1, 3, 3, 4, 3, 5)
    assert c["records_total"] == 19
    assert c["log_appended"] == 19
    assert c["write_downs_booked"] == 2
    assert "skipped_R02" not in c

    log = db.read_df(con, "SELECT * FROM decision_log")
    by = {(r.rule_id, r.subject_id): r.outcome for r in log.itertuples(index=False)}
    assert by[("R01", "D-000004")] == "no_repair_quote_above_share"
    assert by[("R02", "D-000001")] == "marketplace"
    assert by[("R02", "D-000002")] == "employee_buyout"
    assert by[("R02", "D-000005")] == "as_is"
    assert by[("R03", "D-000001")] == "write_down_180"
    assert by[("R03", "D-000002")] == "write_down_90"
    assert by[("R03", "D-000005")] == "none"
    assert by[("R04", "D-000002")] == "eligible_as_replacement"
    assert by[("R04", "D-000003")] == "not_eligible"
    assert by[("R05", "PO-000001")] == "claim_open"
    assert by[("R05", "PO-000002")] == "claim_reminder"
    assert by[("R05", "PO-000003")] == "no_action"
    assert by[("R06", "SC-01")] == "renewal_alert"
    assert by[("R06", "SC-02")] == "no_action"
    assert by[("R06", "SC-03")] == "notice_missed_auto_renews"
    assert by[("R06", "RC-000004")] == "no_action"
    assert by[("R06", "RC-000006")] == "expiring_no_notice_possible"
    assert log["is_synthetic_input"].all()
    assert log["threshold_owner"].str.strip().astype(bool).all()

    adv = log[(log["rule_id"] == "R02") & (log["subject_id"] == "D-000002")]["advisory_json"].iloc[0]
    assert json.loads(adv)["kind"] == "sell_before_launch"

    ledger = db.read_df(con, "SELECT serial, amount FROM write_down_ledger ORDER BY serial")
    assert dict(zip(ledger["serial"], ledger["amount"].astype(float))) == {"D-000001": 75.0, "D-000002": 60.0}

    stored_queue = db.read_df(con, "SELECT * FROM decision_queue")
    # 3 priority-1 rows plus 8 priority-2 rows (3 x R02, R03 D-000002, R04 D-000002, R06 SC-01 and
    # RC-000006, R01 D-000004); the 8 no-action outcomes are logged but not queued
    assert len(stored_queue) == len(queue) == 11
    assert set(queue.loc[queue["priority"] == 1, "subject_id"]) == {"D-000001", "PO-000002", "SC-03"}
    assert set(queue.loc[queue["priority"] == 2, "subject_id"]) == {
        "D-000001", "D-000002", "D-000005", "SC-01", "RC-000006", "D-000004"
    }
    assert c["queue_priority_1"] == 3

    # rerun: 0 appended, ledger unchanged, identical queue
    queue2, summary2 = runner.run_all_decisions(con, AS_OF, thr, assumptions)
    assert summary2.counts["log_appended"] == 0
    assert summary2.counts["write_downs_booked"] == 0
    assert len(db.read_df(con, "SELECT * FROM decision_log")) == 19
    assert len(db.read_df(con, "SELECT * FROM write_down_ledger")) == 2
    pd.testing.assert_frame_equal(
        queue.sort_values("decision_id").reset_index(drop=True),
        queue2.sort_values("decision_id").reset_index(drop=True),
    )
    runs = db.read_df(con, "SELECT run_id, command, finished_at FROM runs WHERE command = 'decide'")
    assert len(runs) == 2 and runs["finished_at"].notna().all()


def test_runner_on_empty_schema_writes_nothing(thr, assumptions, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    con = db.connect(":memory:")
    db.create_schema(con)
    queue, summary = runner.run_all_decisions(con, AS_OF, thr, assumptions, run_id="decide-manual")
    assert queue.empty and summary.counts["records_total"] == 0 and summary.counts["log_appended"] == 0
    assert len(db.read_df(con, "SELECT * FROM decision_queue")) == 0


# --------------------------------------------------------------------------------------
# review fixes: threshold attribution, rental notice owner, valid_from, ADV02 in the queue
# --------------------------------------------------------------------------------------


def test_r02_names_the_threshold_that_decided_the_channel(thr):
    grade_d = decide_channel(thr=thr, **fx.channel_kwargs(grade="D"))
    assert grade_d.threshold_key == "as_is_only_grade"
    assert grade_d.threshold_owner.startswith("Head of Recommerce")
    assert grade_d.inputs["decided_by_threshold"] == "as_is_only_grade"

    # buyout would have won on net but the window is closed: the window threshold decided
    closed = decide_channel(thr=thr, **fx.channel_kwargs(buyout_eligible=False))
    assert closed.outcome != "employee_buyout"
    assert closed.threshold_key == "employee_buyout_window_days"
    assert closed.threshold_owner.startswith("Head of Customer Success")
    assert closed.inputs["employee_buyout_window_days"] == 60
    assert closed.inputs["employee_buyout_window_owner"].startswith("Head of Customer Success")

    # nothing removed a better channel: the uplift threshold decided
    plain = decide_channel(thr=thr, **fx.channel_kwargs())
    assert plain.threshold_key == "channel_min_net_uplift_eur"

    # the days-to-cash filter removed the best net: that threshold decided
    slow = dict(fx.DAYS_TO_CASH)
    slow["b2b_wholesale"] = 61.0
    rv = {"marketplace": 300.0, "employee_buyout": 285.0, "b2b_wholesale": 900.0, "as_is": 100.0}
    by_days = decide_channel(thr=thr, **fx.channel_kwargs(days_to_cash=slow, forecast_rv_by_channel=rv))
    assert "b2b_wholesale" not in by_days.inputs["allowed_channels"]
    assert by_days.threshold_key == "channel_max_days_to_cash"

    consulted = {t["key"]: t for t in plain.inputs["thresholds_consulted"]}
    assert set(consulted) == {"channel_min_net_uplift_eur", "channel_max_days_to_cash", "as_is_only_grade", "employee_buyout_window_days"}
    assert all(t["owner"].strip() and t["valid_from"] for t in consulted.values())


def test_r06_rental_contract_records_rental_notice_threshold_and_owner(thr):
    rental = decide_renewal_alert(
        contract_type="rental_contract", contract_id="RC-1", counterparty="CUST-0001",
        end_date=AS_OF + timedelta(days=100), notice_days=None, auto_renewal=False,
        annual_value_eur=360.0, thr=thr, as_of=AS_OF, run_id=RUN_ID,
    )
    assert rental.inputs["notice_days"] == 90
    assert rental.inputs["notice_days_source"] == "threshold rental_notice_days"
    assert rental.inputs["rental_notice_days"] == 90
    assert rental.inputs["rental_notice_days_owner"].startswith("Head of Customer Success")
    keys = {t["key"] for t in rental.inputs["thresholds_consulted"]}
    assert "rental_notice_days" in keys
    # notice deadline = end - 90 = as_of + 10 -> within the 60 day lead
    assert rental.outcome == "renewal_alert"
    supplier = _renewal(thr, d=5)
    assert supplier.inputs["notice_days_source"] == "contract_field"
    assert "rental_notice_days" not in supplier.inputs
    with pytest.raises(ValueError, match="needs notice_days"):
        decide_renewal_alert(
            contract_type="supplier_contract", contract_id="SC-9", counterparty="Supplier-B",
            end_date=AS_OF + timedelta(days=100), notice_days=None, auto_renewal=False,
            annual_value_eur=1.0, thr=thr, as_of=AS_OF, run_id=RUN_ID,
        )


def test_threshold_not_yet_valid_refuses_to_fire(tmp_path):
    thr_ok = fx.thresholds_from_tmp(tmp_path)
    future = thr_ok.model_copy(deep=True)
    future.thresholds["aging_days_90"].valid_from = AS_OF + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot fire before it is in force"):
        _aging(future, 100)
    rec = _aging(thr_ok, 100)
    assert rec.threshold_valid_from == thr_ok.thresholds["aging_days_90"].valid_from
    frame = runner.records_to_frame([rec], is_synthetic_input=True)
    assert "threshold_valid_from" in runner.LOG_COLUMNS
    assert pd.Timestamp(frame.loc[0, "threshold_valid_from"]).date() == rec.threshold_valid_from


def test_shipped_thresholds_are_in_force_on_the_generator_as_of():
    from restwert.config import load_generator_config

    shipped = fx.shipped_thresholds()
    as_of = load_generator_config().as_of
    for key, spec in shipped.thresholds.items():
        assert spec.valid_from <= as_of, key


def test_r01_skips_subject_without_as_is_forecast(thr, assumptions, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    con = fx.build_fixture_db()
    con.execute("UPDATE rv_forecast_current SET forecast_rv_as_is = NULL WHERE serial = 'D-000004'")
    _, summary = runner.run_all_decisions(con, AS_OF, thr, assumptions, write_docs=False)
    assert summary.counts["records_R01"] == 0
    assert summary.counts.get("skipped_R01") == 1
    assert any("write_docs=False" in n for n in summary.notes)


def test_calibration_advisory_reaches_the_queue(thr, assumptions, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_rules_doc", lambda t, path=None: tmp_path / "DECISION_RULES.md")
    con = fx.build_fixture_db()
    adv = pd.DataFrame([dict(
        advisory_id="ADV02-20260630-rv-2026-06-30", as_of=AS_OF, run_id="rv-2026-06-30", kind="forecast_calibration",
        subject_type="forecast", subject_id="rv-2026-06-30", confidence="medium",
        payload_json=json.dumps({"mean_bias_3m": 0.11, "months": "2026-03,2026-04,2026-05", "n_months": 3, "n_sales": 250, "threshold_bias_pct": 0.08}),
        note="advisory only: mean forecast bias +11.0 %; a human reviews the model. Owner: Head of Recommerce (test)",
        threshold_key="forecast_recalibration_bias_pct", threshold_owner="Head of Recommerce (test)",
    )])
    db.write_df(con, "advisories", adv, mode="append")
    queue, summary = runner.run_all_decisions(con, AS_OF, thr, assumptions)
    row = queue[queue["rule_id"] == "ADV02"]
    assert len(row) == 1
    row = row.iloc[0]
    assert row["priority"] == 3 and row["subject_type"] == "forecast" and row["outcome"] == "review_model"
    assert row["threshold_owner"] == "Head of Recommerce (test)" and row["threshold_key"] == "forecast_recalibration_bias_pct"
    assert row["advisory_kind"] == "forecast_calibration" and "reviews the model" in row["explanation"]
    assert row["threshold_value"] == "0.08"
    stored = db.read_df(con, "SELECT * FROM decision_queue WHERE rule_id = 'ADV02'")
    assert len(stored) == 1
    assert summary.counts["queue"] == 12  # the 11 decisions of the fixture plus the advisory
