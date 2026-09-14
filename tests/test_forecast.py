"""Tests for restwert.forecast (SPEC 5.8): fit recovery, clipping, fallbacks, registry,
backtest leakage guard and invariance, launch-step guard, advisories, full run."""

from __future__ import annotations

import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures import forecast_frames as ff  # noqa: E402  (installs the db stub if needed)

from restwert.config import TERM_MONTHS  # noqa: E402
from restwert.forecast import advisory as adv  # noqa: E402
from restwert.forecast import backtest as bt  # noqa: E402
from restwert.forecast import features as fx  # noqa: E402
from restwert.forecast import model as mdl  # noqa: E402
from restwert.forecast import registry as reg  # noqa: E402
from restwert.forecast.run import run_forecast  # noqa: E402

A = ff.assumptions()
FAM = ff.families_cfg()
THR = ff.thresholds()


# --------------------------------------------------------------------------------------
# fit and predict
# --------------------------------------------------------------------------------------
def test_fit_recovers_known_slope_and_step():
    train = ff.known_curve_frame(600, seed=11)
    rvm = mdl.fit(train, ff.as_is_rows(), date(2026, 6, 30), "rv-2026-06-30", A)
    c = rvm.coefficients["iphone_like"]
    assert rvm.fit_quality["iphone_like"] == "family"
    assert rvm.n_train["iphone_like"] == 600
    assert abs(c["months_since_launch"] - ff.KNOWN["months_since_launch"]) < 0.005
    assert abs(c["n_launches_since"] - ff.KNOWN["n_launches_since"]) < 0.05
    assert abs(c["grade_B"] - ff.KNOWN["grade_B"]) < 0.05
    assert 0.0 < rvm.sigma_log["iphone_like"] < 0.10


def test_predict_ratio_is_clipped_and_uses_as_is_ratio():
    train = ff.known_curve_frame(600, seed=3)
    rvm = mdl.fit(train, ff.as_is_rows(n=12, ratio=0.11), date(2026, 6, 30), "rv-2026-06-30", A)
    kw = dict(family="iphone_like", grade="A", storage_gb=128, base_storage_gb=128)
    assert mdl.predict_ratio(rvm, months_since_launch=1000, n_launches_since=50, **kw) == pytest.approx(0.02)
    assert mdl.predict_ratio(rvm, months_since_launch=-200, n_launches_since=0, **kw) == pytest.approx(0.95)
    mid = mdl.predict_ratio(rvm, months_since_launch=12, n_launches_since=1, **kw)
    assert 0.02 < mid < 0.95
    # as_is never goes through the regression: median of the 12 as-is rows
    assert mdl.predict_ratio(rvm, months_since_launch=12, n_launches_since=1, channel="as_is", **kw) == pytest.approx(0.11)
    # below 10 as-is rows the assumptions fallback is used
    rvm2 = mdl.fit(train, ff.as_is_rows(n=5, ratio=0.30), date(2026, 6, 30), "rv-x", A)
    assert rvm2.as_is_ratio["iphone_like"] == pytest.approx(A.get("as_is_ratio_fallback", "iphone_like"))
    low, point, high = mdl.predict_band(rvm, months_since_launch=12, n_launches_since=1, **kw)
    assert low <= point <= high


def test_pooled_fallback_below_min_n():
    big = ff.known_curve_frame(200, seed=5, family="iphone_like")
    small = ff.known_curve_frame(20, seed=6, family="android_like", start_serial=1000)
    small["model"] = "A-Gen03"
    rvm = mdl.fit(pd.concat([big, small], ignore_index=True), None, date(2026, 6, 30), "rv-p", A, min_n_per_family=50)
    assert rvm.fit_quality["iphone_like"] == "family"
    assert rvm.fit_quality["android_like"] == "pooled"
    assert rvm.fit_quality["laptop_like"] == "pooled"
    assert "pooled" in rvm.coefficients
    assert "fam_android_like" in rvm.coefficients["pooled"]
    r = mdl.predict_ratio(rvm, family="android_like", months_since_launch=12, n_launches_since=1, grade="B", storage_gb=128, base_storage_gb=128)
    assert 0.02 <= r <= 0.95
    js = mdl.to_json(rvm)
    back = mdl.from_run_row(pd.Series({"run_id": "rv-p", "as_of": pd.Timestamp("2026-06-30"), "method": "loglinear_step_v1", **js}))
    assert back.coefficients == rvm.coefficients
    assert back.fit_quality == rvm.fit_quality


def test_none_path_returns_planned_ratio():
    train = ff.known_curve_frame(30, seed=8)
    rvm = mdl.fit(train, None, date(2026, 6, 30), "rv-none", A, min_n_per_family=50)
    assert set(rvm.fit_quality.values()) == {"none"}
    for fam in ("iphone_like", "android_like", "laptop_like"):
        r = mdl.predict_ratio(rvm, family=fam, months_since_launch=30, n_launches_since=2, grade="C", storage_gb=256, base_storage_gb=128)
        assert r == pytest.approx(A.get("planned_rv_ratio", fam))
    assert rvm.notes and "planned" in rvm.notes[0]


# --------------------------------------------------------------------------------------
# launch steps (leakage guard)
# --------------------------------------------------------------------------------------
def test_n_launches_since_never_counts_catalogue_rows_after_as_of():
    cat = ff.catalogue(date(2025, 12, 31))  # includes P-Gen07 launched 2025-09-15
    launch = date(2024, 9, 15)
    # as_of before the 2025 launch: it is counted only through the calendar rule
    assert fx.observed_launch_steps(cat, "iphone_like", launch, date(2025, 6, 30)) == 0
    assert fx.n_launches_since(cat, "iphone_like", launch, date(2025, 10, 31), date(2025, 6, 30), FAM) == 1
    assert fx.n_launches_since(cat, "iphone_like", launch, date(2025, 9, 14), date(2025, 6, 30), FAM) == 0
    # as_of after the launch: observed from the catalogue, same answer
    assert fx.n_launches_since(cat, "iphone_like", launch, date(2025, 10, 31), date(2025, 12, 31), FAM) == 1
    # an off-calendar catalogue row after as_of must never be counted
    extra = pd.concat([cat, pd.DataFrame([{**cat.iloc[-1].to_dict(), "model": "P-GenXX", "model_family": "iphone_like", "launch_date": date(2025, 11, 1)}])], ignore_index=True)
    assert fx.n_launches_since(extra, "iphone_like", launch, date(2025, 12, 15), date(2025, 6, 30), FAM) == 1
    assert fx.n_launches_since(extra, "iphone_like", launch, date(2025, 12, 15), date(2025, 12, 31), FAM) == 2  # knowable after the fact
    # vectorised version agrees
    vec = fx.n_launches_since_vec(cat, pd.Series(["iphone_like", "iphone_like"]), pd.Series([launch, launch]),
                                  pd.Series([date(2025, 10, 31), date(2025, 9, 14)]), date(2025, 6, 30), FAM)
    assert list(vec) == [1, 0]


# --------------------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------------------
def _fit_at(as_of: date, seed: int = 1):
    return mdl.fit(ff.known_curve_frame(120, seed=seed), None, as_of, reg.make_run_id(as_of), A)


def test_save_run_is_append_only_and_run_before_is_strict():
    con = ff.fresh_db()
    jan = _fit_at(date(2025, 1, 31), 1)
    feb = _fit_at(date(2025, 2, 28), 2)
    reg.save_run(con, jan)
    reg.save_run(con, feb)
    with pytest.raises(ValueError):
        reg.save_run(con, feb)
    assert reg.run_exists(con, "rv-2025-02-28")
    assert reg.load_run(con, "rv-2025-01-31").coefficients == jan.coefficients
    assert reg.run_before(con, date(2025, 2, 28)).run_id == "rv-2025-01-31"  # same day is NOT before
    assert reg.run_before(con, date(2025, 3, 1)).run_id == "rv-2025-02-28"
    assert reg.run_before(con, date(2025, 1, 31)) is None
    assert reg.latest_run(con, date(2025, 2, 28)).run_id == "rv-2025-02-28"
    assert reg.latest_run(con, date(2025, 2, 1)).run_id == "rv-2025-01-31"
    assert reg.latest_run(con).run_id == "rv-2025-02-28"
    runs = reg.load_all_runs(con)
    assert [r.run_id for r in runs] == ["rv-2025-01-31", "rv-2025-02-28"]
    assert reg.pick_run_before(runs, date(2025, 2, 28)).run_id == "rv-2025-01-31"


def test_grid_and_current_shapes():
    cat = ff.catalogue(date(2026, 6, 30))
    rvm = mdl.fit(ff.known_curve_frame(300, seed=4), ff.as_is_rows(), date(2026, 6, 30), "rv-2026-06-30", A)
    grid = reg.build_grid(rvm, cat, range(0, 49), families_cfg=FAM)
    assert len(grid) == len(cat) * 4 * 49
    assert grid["forecast_rv_ratio"].between(0.02, 0.95).all()
    assert (grid["ratio_low"] <= grid["forecast_rv_ratio"]).all() and (grid["forecast_rv_ratio"] <= grid["ratio_high"]).all()
    p07 = grid[(grid["model"] == "P-Gen07") & (grid["grade"] == "A")].set_index("months_since_launch")
    assert p07.loc[0, "n_launches_since"] == 0 and p07.loc[13, "n_launches_since"] == 1  # calendar rule beyond as_of
    fleet = ff.mini_fleet(60, seed=3)
    cur = reg.build_current(rvm, fleet["devices"], fleet["events"], fleet["refurbishment"], fleet["resale"], cat, A, FAM, date(2026, 6, 30))
    sold = set(fleet["resale"]["serial"])
    assert not set(cur["serial"]) & sold
    assert len(cur) == len(fleet["devices"]) - len(sold)
    assert set(cur["grade_source"]) <= {"inspected", "pre_return", "expected"}
    assert (cur["forecast_rv"] > 0).all()
    assert cur["forecast_rv_grade_b"].notna().all()


# --------------------------------------------------------------------------------------
# backtest
# --------------------------------------------------------------------------------------
def test_metric_signs_and_leakage_assertions():
    actual = np.array([100.0, 200.0, 300.0])
    assert bt.bias(actual, 1.1 * actual) == pytest.approx(0.10)
    assert bt.mape(actual, 1.1 * actual) == pytest.approx(0.10)
    assert bt.wape(actual, 0.9 * actual) == pytest.approx(0.10)
    assert bt.mae(actual, actual + 5) == pytest.approx(5.0)
    assert bt.rmse_log(actual, actual) == pytest.approx(0.0)
    cutoff = date(2025, 12, 31)
    train = pd.DataFrame({"serial": ["a", "b"], "sale_date": [date(2025, 11, 1), date(2026, 1, 2)]})
    test = pd.DataFrame({"serial": ["c"], "sale_date": [date(2026, 2, 1)]})
    with pytest.raises(AssertionError, match="train max sale_date"):
        bt.assert_no_leakage(train, test, cutoff, None)
    train_ok = pd.DataFrame({"serial": ["a", "c"], "sale_date": [date(2025, 11, 1), date(2025, 12, 31)]})
    with pytest.raises(AssertionError, match="serials in train and test"):
        bt.assert_no_leakage(train_ok, test, cutoff, None)
    bad_test = pd.DataFrame({"serial": ["d"], "sale_date": [date(2025, 12, 31)]})
    with pytest.raises(AssertionError, match="test min sale_date"):
        bt.assert_no_leakage(train_ok, bad_test, cutoff, None)


def test_time_split_backtest_finite_and_invariant_to_future_information():
    fleet = ff.mini_fleet(400, seed=7)
    cutoff = date(2025, 12, 31)
    results = bt.time_split_backtest(fleet["resale"], fleet["devices"], fleet["model_catalogue"], fleet["events"], A, FAM, cutoff)
    assert results and results[-1].model_family == "*"
    for r in results:
        for m in (r.mape, r.bias, r.wape, r.mae_eur, r.rmse_log):
            assert math.isfinite(m)
        assert r.train_max_sale_date <= cutoff < r.test_min_sale_date
        assert r.n_train > 0 and r.n_test > 0
    frame = bt.results_to_frame(results)
    assert set(frame["model_family"]) == {r.model_family for r in results}

    _, test_a, _ = bt.backtest_frames(fleet["resale"], fleet["devices"], fleet["model_catalogue"], fleet["events"], A, FAM, cutoff)
    # tamper with the future: prices after the cutoff x 1.5 and a catalogue launch after the cutoff
    resale2 = fleet["resale"].copy()
    after = pd.to_datetime(resale2["sale_date"]) > pd.Timestamp(cutoff)
    resale2.loc[after, "price"] = resale2.loc[after, "price"] * 1.5
    cat2 = pd.concat([fleet["model_catalogue"], pd.DataFrame([{
        "model": "P-Gen99", "model_family": "iphone_like", "generation": 99, "launch_date": date(2026, 3, 1),
        "list_price": 999.0, "base_storage_gb": 128, "is_synthetic": True, "source_file": "model_catalogue.csv"}])], ignore_index=True)
    _, test_b, _ = bt.backtest_frames(resale2, fleet["devices"], cat2, fleet["events"], A, FAM, cutoff)
    assert list(test_a["serial"]) == list(test_b["serial"])
    assert np.allclose(test_a["pred_price"].to_numpy(), test_b["pred_price"].to_numpy())
    assert np.allclose(test_a["n_launches_since"].to_numpy(), test_b["n_launches_since"].to_numpy())


# --------------------------------------------------------------------------------------
# advisories
# --------------------------------------------------------------------------------------
def _current_row(serial: str, fam: str, grade: str, n_launch: int, rv_now: float, grade_source: str = "inspected") -> dict:
    return {"serial": serial, "as_of": pd.Timestamp("2026-06-30"), "run_id": "rv-2026-06-30", "model": "P-Gen06",
            "model_family": fam, "grade_used": grade, "grade_source": grade_source, "months_since_launch": 21.5,
            "n_launches_since": n_launch, "forecast_rv_ratio": rv_now / 1000.0, "forecast_rv": rv_now,
            "forecast_rv_employee_buyout": rv_now * 0.95, "forecast_rv_b2b_wholesale": rv_now * 0.85,
            "forecast_rv_as_is": 100.0, "forecast_rv_grade_b": rv_now * 0.9, "fit_quality": "family", "n_train": 600}


def test_sell_before_launch_emits_only_above_threshold_with_owner():
    as_of = date(2026, 6, 30)  # iphone_like launch on 2026-09-15 is 77 days away
    cat = ff.catalogue(as_of)
    rvm = mdl.fit(ff.known_curve_frame(600, seed=11), ff.as_is_rows(), as_of, "rv-2026-06-30", A)
    devices = pd.DataFrame([
        {"serial": "D-1", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 900.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-2", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 900.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-3", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 900.0, "launch_date": date(2024, 9, 15)},
    ])
    rv_now = 900.0 * mdl.predict_ratio(rvm, family="iphone_like", months_since_launch=21.5, n_launches_since=1, grade="B", storage_gb=128, base_storage_gb=128)
    current = pd.DataFrame([
        _current_row("D-1", "iphone_like", "B", 1, rv_now),                       # returned, drop ~ 12 + 3 x 0.03 %: emits
        _current_row("D-2", "iphone_like", "B", 1, rv_now, grade_source="expected"),  # still rented: never
        _current_row("D-3", "iphone_like", "B", 1, 20.0),                         # drop in EUR below holding cost: delta <= 0
    ])
    out = adv.sell_before_launch(current, rvm, cat, devices, FAM, A, THR, as_of)
    assert list(out["subject_id"]) == ["D-1"]
    row = out.iloc[0]
    assert row["kind"] == "sell_before_launch" and row["threshold_key"] == "sell_before_launch_lookahead_days"
    assert row["threshold_owner"] == "Head of Recommerce (name)"
    assert row["confidence"] in ("low", "medium")
    import json
    payload = json.loads(row["payload_json"])
    assert payload["drop_pct"] >= 0.08 and payload["delta_eur"] > 0 and payload["days_to_launch"] == 77
    # raise the minimum drop above what the model forecasts: nothing emitted
    strict = ff.thresholds(min_drop_pct=0.60)
    assert len(adv.sell_before_launch(current, rvm, cat, devices, FAM, A, strict, as_of)) == 0
    # shrink the lookahead below 77 days: nothing emitted
    short = ff.thresholds(lookahead_days=30)
    assert len(adv.sell_before_launch(current, rvm, cat, devices, FAM, A, short, as_of)) == 0


def test_calibration_advisory_trailing_three_months():
    months = pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"])
    es = pd.DataFrame({"month": list(months) * 2, "model_family": ["*"] * 6 + ["iphone_like"] * 6,
                       "n_with_forecast": 20, "bias": [0.0, 0.0, 0.0, 0.12, 0.10, 0.50] + [0.9] * 6})
    out = adv.calibration_advisory(es, THR, date(2026, 6, 15), "rv-2026-06-15")  # complete months: Mar, Apr, May -> mean 0.0733
    assert len(out) == 0
    out = adv.calibration_advisory(es, THR, date(2026, 7, 15), "rv-2026-07-15")  # Apr, May, Jun -> 0.24
    assert len(out) == 1 and out.iloc[0]["kind"] == "forecast_calibration"
    assert out.iloc[0]["threshold_owner"] == "Head of Recommerce (name)" and out.iloc[0]["subject_id"] == "rv-2026-07-15"


# --------------------------------------------------------------------------------------
# full run on a 400-device synthetic DB
# --------------------------------------------------------------------------------------
def test_run_forecast_end_to_end_under_10_seconds():
    from restwert import db

    fleet = ff.mini_fleet(400, seed=7, purchase_days=730)  # denser lifecycle completion than the 4-year window
    con = ff.fresh_db()
    ff.load_fleet(con, fleet)
    cfg = ff.generator_cfg(date(2026, 6, 30))
    t0 = time.perf_counter()
    summary = run_forecast(con, date(2026, 6, 30), A, THR, cfg, replay=True, backtest=True, min_train=100)
    elapsed = time.perf_counter() - t0
    assert elapsed < 10, f"run_forecast took {elapsed:.1f}s"
    assert summary.command == "forecast" and summary.counts["forecast_runs_total"] >= 2
    n_runs = int(db.read_df(con, "SELECT count(*) AS n FROM forecast_runs")["n"].iloc[0])
    assert n_runs == summary.counts["forecast_runs_total"]
    assert reg.run_exists(con, "rv-2026-06-30")
    for t in ("rv_forecast_grid", "rv_forecast_current", "rv_forecast_of_record", "rv_forecast_error_monthly", "backtest_result"):
        assert int(db.read_df(con, f"SELECT count(*) AS n FROM {t}")["n"].iloc[0]) > 0, t
    assert db.table_exists(con, "advisories")
    # rerun: no new runs, identical error series, backtest appended under a new id
    es1 = db.read_df(con, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family")
    summary2 = run_forecast(con, date(2026, 6, 30), A, THR, cfg, replay=True, backtest=True, min_train=100)
    assert summary2.counts["forecast_runs_added"] == 0
    es2 = db.read_df(con, "SELECT * FROM rv_forecast_error_monthly ORDER BY month, model_family")
    pd.testing.assert_frame_equal(es1, es2)
    assert int(db.read_df(con, "SELECT count(DISTINCT backtest_id) AS n FROM backtest_result")["n"].iloc[0]) == 2
    err = db.read_df(con, "SELECT * FROM rv_forecast_error_monthly WHERE model_family = '*' AND mape IS NOT NULL")
    assert len(err) > 0 and np.isfinite(err["mape"]).all() and (err["n_with_forecast"] >= 10).all()
    thin = db.read_df(con, "SELECT * FROM rv_forecast_error_monthly WHERE n_with_forecast < 10")
    assert thin["mape"].isna().all()  # under 10 rows: counts kept, metrics NULL
    cur = db.read_df(con, "SELECT * FROM rv_forecast_current")
    assert cur["forecast_rv_ratio"].between(0.02, 0.95).all() and cur["run_id"].eq("rv-2026-06-30").all()
    advs = db.read_df(con, "SELECT * FROM advisories")
    assert advs["threshold_owner"].notna().all() if len(advs) else True


# --------------------------------------------------------------------------------------
# review fixes: unsupported grades, catalogue leakage check, advisory guards
# --------------------------------------------------------------------------------------
def test_training_without_grade_d_never_forecasts_grade_d_like_grade_a():
    """Every grade-D sale goes As Is and is excluded from training, so grade_D has zero
    variance; the fit must not silently hand the grade-A line to grade-D devices."""
    train = ff.known_curve_frame(600, seed=21)
    train = train[train["grade"] != "D"].reset_index(drop=True)
    rvm = mdl.fit(train, ff.as_is_rows(n=12, ratio=0.11), date(2026, 6, 30), "rv-no-d", A)
    assert rvm.unsupported.get("iphone_like") == ["grade_D"]
    assert any("grade_D" in n for n in rvm.notes)
    kw = dict(family="iphone_like", months_since_launch=18, n_launches_since=1, storage_gb=128, base_storage_gb=128)
    a_line = mdl.predict_ratio(rvm, grade="A", **kw)
    d_line = mdl.predict_ratio(rvm, grade="D", **kw)
    assert d_line != pytest.approx(a_line)
    assert d_line == pytest.approx(0.11)  # the family As-Is ratio, not the regression
    low, point, high = mdl.predict_band(rvm, grade="D", **kw)
    assert low == point == high == pytest.approx(0.11)
    assert mdl.fit_quality_for(rvm, "iphone_like", "D") == "unsupported_grade"
    assert mdl.fit_quality_for(rvm, "iphone_like", "B") == "family"
    frame = pd.DataFrame([
        {"model_family": "iphone_like", "months_since_launch": 18.0, "n_launches_since": 1, "grade": g, "storage_gb": 128.0, "base_storage_gb": 128.0}
        for g in ("A", "D")
    ])
    vec = mdl.predict_ratio_frame(rvm, frame)
    assert vec[0] == pytest.approx(a_line) and vec[1] == pytest.approx(0.11)
    # the run row round-trips the unsupported list and older rows without it still load
    js = mdl.to_json(rvm)
    back = mdl.from_run_row(pd.Series({"run_id": "rv-no-d", "as_of": pd.Timestamp("2026-06-30"), "method": mdl.METHOD, **js}))
    assert back.unsupported == rvm.unsupported
    old = {k: v for k, v in js.items() if k != "unsupported_json"}
    legacy = mdl.from_run_row(pd.Series({"run_id": "rv-old", "as_of": pd.Timestamp("2026-06-30"), "method": mdl.METHOD, **old}))
    assert legacy.unsupported == {}
    # grid and current carry the label
    cat = ff.catalogue(date(2026, 6, 30))
    grid = reg.build_grid(rvm, cat, range(0, 3), families_cfg=FAM)
    p = grid[(grid["model_family"] == "iphone_like")]
    assert set(p.loc[p["grade"] == "D", "fit_quality"]) == {"unsupported_grade"}
    assert set(p.loc[p["grade"] == "A", "fit_quality"]) == {"family"}
    assert np.allclose(p.loc[p["grade"] == "D", "forecast_rv_ratio"].to_numpy(dtype=float), 0.11)


def test_sell_before_launch_skips_the_as_is_only_grade():
    as_of = date(2026, 6, 30)
    cat = ff.catalogue(as_of)
    rvm = mdl.fit(ff.known_curve_frame(600, seed=11), ff.as_is_rows(), as_of, "rv-2026-06-30", A)
    devices = pd.DataFrame([
        {"serial": "D-1", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 900.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-9", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 900.0, "launch_date": date(2024, 9, 15)},
    ])
    rv_now = 900.0 * mdl.predict_ratio(rvm, family="iphone_like", months_since_launch=21.5, n_launches_since=1, grade="B", storage_gb=128, base_storage_gb=128)
    current = pd.DataFrame([
        _current_row("D-1", "iphone_like", "B", 1, rv_now),
        _current_row("D-9", "iphone_like", "D", 1, rv_now),   # grade D: R02 can only send it As Is
    ])
    out = adv.sell_before_launch(current, rvm, cat, devices, FAM, A, THR, as_of)
    assert list(out["subject_id"]) == ["D-1"]


def test_calibration_advisory_prefers_channel_adjusted_bias():
    months = pd.to_datetime(["2026-03-01", "2026-04-01", "2026-05-01"])
    # business-view bias far beyond the 8 % threshold, channel-adjusted bias inside it
    es = pd.DataFrame({"month": months, "model_family": "*", "n_with_forecast": 40,
                       "bias": [0.20, 0.22, 0.19], "bias_channel_adjusted": [0.01, 0.02, -0.01]})
    assert len(adv.calibration_advisory(es, THR, date(2026, 6, 15), "rv-x")) == 0
    es["bias_channel_adjusted"] = [0.15, 0.12, 0.10]
    out = adv.calibration_advisory(es, THR, date(2026, 6, 15), "rv-x")
    assert len(out) == 1
    import json
    payload = json.loads(out.iloc[0]["payload_json"])
    assert payload["mean_bias_3m"] == pytest.approx((0.15 + 0.12 + 0.10) / 3, abs=1e-4)
    assert payload["mean_bias_3m_marketplace_baseline"] == pytest.approx((0.20 + 0.22 + 0.19) / 3, abs=1e-4)
    assert "channel-adjusted" in payload["bias_basis"] and "channel-adjusted" in out.iloc[0]["note"]
    # without the column the baseline bias is used and the note says so
    legacy = es.drop(columns=["bias_channel_adjusted"])
    out2 = adv.calibration_advisory(legacy, THR, date(2026, 6, 15), "rv-y")
    assert len(out2) == 1 and "business view" in json.loads(out2.iloc[0]["payload_json"])["bias_basis"]


def test_assert_no_leakage_checks_catalogue_rows_after_cutoff():
    fleet = ff.mini_fleet(400, seed=7)
    cutoff = date(2025, 12, 31)
    train, test, _ = bt.backtest_frames(fleet["resale"], fleet["devices"], fleet["model_catalogue"], fleet["events"], A, FAM, cutoff)
    assert "launch_date" in test.columns
    bt.assert_no_leakage(train, test, cutoff, fleet["model_catalogue"], FAM)  # passes as built
    # a test frame whose launch steps were built from a leaked future launch must fail
    leaked = test.copy()
    leaked.loc[leaked.index[0], "n_launches_since"] = int(leaked.loc[leaked.index[0], "n_launches_since"]) + 1
    with pytest.raises(AssertionError, match="n_launches_since differs"):
        bt.assert_no_leakage(train, leaked, cutoff, fleet["model_catalogue"], FAM)


def test_grid_default_horizon_covers_the_ledger_maximum_with_reserve():
    """The horizon is derived from the shipped ledger, not from a purchase rule.

    Measured 2026-09-14 on silver.device_ledger (DuckDB read only, catalogue round 4):
    max(estimate_months_at_lease_end) = 110, max(months from launch to purchase) = 63, longest term 48. The 84-month grid that assumed
    "24 months since launch at purchase plus the longest term" clipped 24 open serials.
    """
    measured_max_months_at_lease_end = 110
    reserve_months = 6
    horizon = reg.GRID_MONTHS.stop - 1
    assert reg.GRID_MONTHS.start == 0
    assert horizon >= measured_max_months_at_lease_end + reserve_months
    assert horizon % 12 == 0, "rounded up to a full year"
    assert horizon == 120
    # the old rule still holds as a floor: bought 24 months after launch, rented for the longest term, sold 40 days later
    assert horizon >= 24 + max(TERM_MONTHS) + 2
    # a purchase 63 months after launch (the measured maximum) with the longest term and the return-to-sale days fits
    assert horizon >= 63 + max(TERM_MONTHS) + 2
