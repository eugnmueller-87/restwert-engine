"""Tests for restwert.forecast.error_series (SPEC 5.5, 5.8): forecast of record picks the
run strictly before the return date and the inspected grade; the error series excludes
and counts As-Is, gives NULL metrics under 10 rows, reproduces hand numbers and is
byte-identical on rerun."""

from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures import forecast_frames as ff  # noqa: E402

from restwert.forecast import error_series as es  # noqa: E402
from restwert.forecast import model as mdl  # noqa: E402
from restwert.forecast import registry as reg  # noqa: E402

A = ff.assumptions()
FAM = ff.families_cfg()


def _runs():
    jan = mdl.fit(ff.known_curve_frame(120, seed=1), None, date(2026, 1, 31), "rv-2026-01-31", A)
    feb = mdl.fit(ff.known_curve_frame(120, seed=2), None, date(2026, 2, 28), "rv-2026-02-28", A)
    return [jan, feb]


def _devices():
    return pd.DataFrame([
        {"serial": "D-1", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 256, "purchase_price": 1000.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-2", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 1000.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-3", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 1000.0, "launch_date": date(2024, 9, 15)},
        {"serial": "D-4", "model_family": "iphone_like", "model": "P-Gen06", "storage_gb": 128, "purchase_price": 1000.0, "launch_date": date(2024, 9, 15)},
    ])


def _events():
    return pd.DataFrame([
        {"event_id": "EV-1", "serial": "D-1", "event_type": "return", "event_date": date(2026, 2, 26), "return_date": date(2026, 3, 1), "grade_pre_return": "A", "grade_inspected": "B"},
        {"event_id": "EV-2", "serial": "D-2", "event_type": "return", "event_date": date(2026, 2, 20), "return_date": date(2026, 2, 28), "grade_pre_return": "A", "grade_inspected": None},
        {"event_id": "EV-3", "serial": "D-3", "event_type": "return", "event_date": date(2026, 1, 20), "return_date": date(2026, 1, 25), "grade_pre_return": None, "grade_inspected": None},
        {"event_id": "EV-4", "serial": "D-4", "event_type": "repair", "event_date": date(2026, 1, 20), "return_date": None, "grade_pre_return": None, "grade_inspected": None},
        {"event_id": "EV-5", "serial": "D-4", "event_type": "return", "event_date": date(2026, 3, 20), "return_date": date(2026, 4, 2), "grade_pre_return": "C", "grade_inspected": "C"},
    ])


def test_forecast_of_record_uses_run_strictly_before_return_and_inspected_grade():
    runs = _runs()
    cat = ff.catalogue(date(2026, 6, 30))
    rec = es.forecast_of_record_frame(_events(), _devices(), cat, runs, A, FAM).set_index("serial")
    # return on the 1st picks the previous month end, not a same-day run
    assert rec.loc["D-1", "run_id"] == "rv-2026-02-28"
    assert rec.loc["D-1", "grade_used"] == "B"  # inspected wins over pre-return
    assert rec.loc["D-1", "target_date"] == pd.Timestamp(date(2026, 3, 31))  # + 30 days iphone_like
    # return exactly on a run's as_of: that run is NOT before the return date
    assert rec.loc["D-2", "run_id"] == "rv-2026-01-31"
    assert rec.loc["D-2", "grade_used"] == "A"  # pre-return when no inspection
    # no run before: missing with reason
    assert bool(rec.loc["D-3", "is_missing"]) and rec.loc["D-3", "missing_reason"] == "no run before return_date"
    assert pd.isna(rec.loc["D-3", "forecast_rv"])
    # forecast equals a direct prediction with the same run and inputs
    feb = runs[1]
    m = (date(2026, 3, 31) - date(2024, 9, 15)).days / 30.4375
    expected = mdl.predict_ratio(feb, family="iphone_like", months_since_launch=m, n_launches_since=1, grade="B", storage_gb=256, base_storage_gb=128)
    assert rec.loc["D-1", "forecast_rv_ratio"] == expected
    assert rec.loc["D-1", "forecast_rv"] == round(expected * 1000.0, 2)
    assert rec.loc["D-1", "n_launches_since"] == 1  # P-Gen07 on 2025-09-15 observed before the run
    assert not bool(rec.loc["D-4", "is_missing"]) and rec.loc["D-4", "run_id"] == "rv-2026-02-28"


def test_build_forecast_of_record_from_db_matches_pure_frame():
    from restwert import db

    con = ff.fresh_db()
    cat = ff.catalogue(date(2026, 6, 30))
    dev = _devices().assign(colour=None, purchase_date=date(2025, 1, 10), landed_cost=1035.0, supplier="Supplier-A",
                            channel_in="distributor", po_number="PO-1", contract_id=None, is_synthetic=True, source_file="devices.csv")
    ev = _events().assign(contract_id=None, cost=0.0, damage_type=None, resolved=None, replacement_serial=None,
                          wipe_certificate=True, note=None, is_synthetic=True, source_file="events.csv")
    db.write_df(con, "model_catalogue", cat)
    db.write_df(con, "devices", dev)
    db.write_df(con, "events", ev)
    for r in _runs():
        reg.save_run(con, r)
    from_db = es.build_forecast_of_record(con, A, FAM).set_index("serial")
    pure = es.forecast_of_record_frame(_events(), _devices(), cat, _runs(), A, FAM).set_index("serial")
    assert list(from_db.index) == list(pure.index)
    assert list(from_db["run_id"].fillna("")) == list(pure["run_id"].fillna(""))
    np.testing.assert_allclose(from_db["forecast_rv"].astype(float), pure["forecast_rv"].astype(float))
    n = db.write_df(con, "rv_forecast_of_record", from_db.reset_index())
    assert n == 4


def test_error_series_hand_numbers_and_as_is_exclusion():
    resale, devices, record = ff.six_row_error_fixture()
    out = es.build_error_series(resale, devices, record, min_rows=1)
    star = out[(out["model_family"] == "*")].iloc[0]
    assert star["month"] == pd.Timestamp("2026-03-01")
    assert star["n_sales"] == 5 and star["n_with_forecast"] == 4 and star["n_excluded_as_is"] == 1
    assert star["mape"] == pytest_approx(0.075)
    assert star["bias"] == pytest_approx(0.025)
    assert star["wape"] == pytest_approx(0.06)
    assert star["mae_eur"] == pytest_approx(15.0)
    assert star["sum_realised"] == 1000.0 and star["sum_forecast"] == 1020.0
    assert star["realisation_ratio"] == pytest_approx(1000.0 / 1020.0 - 1.0)
    assert star["run_ids_used"] == "rv-2026-01-31..rv-2026-01-31"
    fam_rows = out[out["model_family"] == "iphone_like"].iloc[0]
    assert fam_rows["n_sales"] == 5 and fam_rows["n_with_forecast"] == 4 and fam_rows["n_excluded_as_is"] == 0
    android = out[out["model_family"] == "android_like"].iloc[0]
    assert android["n_sales"] == 0 and android["n_excluded_as_is"] == 1 and pd.isna(android["mape"])
    # production rule: under 10 forecasted rows the metrics are NULL, counts are kept
    prod = es.build_error_series(resale, devices, record)
    star_p = prod[prod["model_family"] == "*"].iloc[0]
    assert star_p["n_with_forecast"] == 4 and pd.isna(star_p["mape"]) and pd.isna(star_p["bias"])
    assert star_p["sum_realised"] == 1000.0


def test_error_series_is_byte_identical_on_rerun():
    resale, devices, record = ff.six_row_error_fixture()
    a = es.build_error_series(resale, devices, record)
    b = es.build_error_series(resale.sample(frac=1, random_state=3), devices, record.sample(frac=1, random_state=4))
    pd.testing.assert_frame_equal(a, b)
    assert a.to_csv(index=False).encode("utf-8") == b.to_csv(index=False).encode("utf-8")
    assert list(a.columns) == es.ERROR_COLUMNS


def test_latest_complete_month_helper():
    resale, devices, record = ff.six_row_error_fixture()
    out = es.build_error_series(resale, devices, record, min_rows=1)
    assert es.latest_complete_month_error(out, date(2026, 4, 15))["month"] == pd.Timestamp("2026-03-01")
    assert es.latest_complete_month_error(out, date(2026, 3, 15)) is None


def pytest_approx(x: float, rel: float = 1e-9):
    import pytest

    return pytest.approx(x, rel=rel, abs=1e-9)


def test_channel_adjusted_and_marketplace_views():
    """Business view uses the baseline; model view multiplies by the run's channel factor
    of the channel actually used; marketplace-only view coincides with both."""
    resale, devices, record = ff.six_row_error_fixture()
    record = record.assign(channel_factor_employee_buyout=0.9, channel_factor_b2b_wholesale=0.8)
    out = es.build_error_series(resale, devices, record, min_rows=1)
    star = out[out["model_family"] == "*"].iloc[0]
    # baseline unchanged
    assert star["mape"] == pytest_approx(0.075) and star["bias"] == pytest_approx(0.025)
    # channel-adjusted: (100,110) mkt, (200,180*0.9=162) buyout, (300,330*0.8=264) b2b, (400,400) mkt
    ape = [0.10, 0.19, 0.12, 0.0]
    err = [0.10, -0.19, -0.12, 0.0]
    assert star["mape_channel_adjusted"] == pytest_approx(sum(ape) / 4)
    assert star["bias_channel_adjusted"] == pytest_approx(sum(err) / 4)
    assert star["sum_forecast_channel_adjusted"] == pytest_approx(110 + 162 + 264 + 400)
    assert star["n_marketplace"] == 2
    assert star["mape_marketplace"] == pytest_approx(0.05) and star["bias_marketplace"] == pytest_approx(0.05)
    # a record without factor columns falls back to factor 1.0 (adjusted == baseline)
    plain = es.build_error_series(resale, devices, ff.six_row_error_fixture()[2], min_rows=1)
    star_p = plain[plain["model_family"] == "*"].iloc[0]
    assert star_p["mape_channel_adjusted"] == pytest_approx(star_p["mape"])
    assert star_p["bias_channel_adjusted"] == pytest_approx(star_p["bias"])


def test_forecast_of_record_carries_channel_factors_of_the_run_in_force():
    runs = _runs()
    cat = ff.catalogue(date(2026, 6, 30))
    rec = es.forecast_of_record_frame(_events(), _devices(), cat, runs, A, FAM).set_index("serial")
    feb = runs[1]
    assert rec.loc["D-1", "channel_factor_employee_buyout"] == pytest_approx(reg.channel_factors(feb, "iphone_like")["employee_buyout"])
    assert rec.loc["D-1", "channel_factor_b2b_wholesale"] == pytest_approx(reg.channel_factors(feb, "iphone_like")["b2b_wholesale"])
    assert pd.isna(rec.loc["D-3", "channel_factor_employee_buyout"])  # no run before the return
