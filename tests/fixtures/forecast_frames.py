"""Hand-built frames and config objects for the forecast module tests (SPEC 5.8, 10).

Everything here is synthetic-of-synthetic: a known log-linear curve with known
coefficients, so the tests can check what the fit recovers. No generator module is
imported; the frames are built in this file.

If the foundation's ``restwert.db`` is not present yet (parallel build), a minimal stub
with the same signatures is installed into ``sys.modules`` by :func:`ensure_foundation`
BEFORE the forecast package is imported. The stub lives in this test fixture only and
never in package code (SPEC section 10).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# foundation stub (only when restwert.db is missing)
# --------------------------------------------------------------------------------------
_STUB_DDL: dict[str, str] = {
    "model_catalogue": """CREATE TABLE IF NOT EXISTS model_catalogue (
  model VARCHAR PRIMARY KEY, model_family VARCHAR NOT NULL, generation INTEGER NOT NULL,
  launch_date DATE NOT NULL, list_price DECIMAL(12,2) NOT NULL, base_storage_gb INTEGER NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR)""",
    "devices": """CREATE TABLE IF NOT EXISTS devices (
  serial VARCHAR PRIMARY KEY, model_family VARCHAR NOT NULL, model VARCHAR NOT NULL,
  storage_gb INTEGER NOT NULL, colour VARCHAR, launch_date DATE NOT NULL, purchase_date DATE NOT NULL,
  purchase_price DECIMAL(12,2) NOT NULL, landed_cost DECIMAL(12,2) NOT NULL,
  supplier VARCHAR NOT NULL, channel_in VARCHAR NOT NULL, po_number VARCHAR NOT NULL, contract_id VARCHAR,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR)""",
    "events": """CREATE TABLE IF NOT EXISTS events (
  event_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, event_type VARCHAR NOT NULL,
  event_date DATE NOT NULL, cost DECIMAL(12,2) NOT NULL DEFAULT 0, damage_type VARCHAR,
  resolved BOOLEAN, replacement_serial VARCHAR,
  return_date DATE, grade_pre_return VARCHAR, grade_inspected VARCHAR, wipe_certificate BOOLEAN,
  note VARCHAR, is_synthetic BOOLEAN NOT NULL, source_file VARCHAR)""",
    "refurbishment": """CREATE TABLE IF NOT EXISTS refurbishment (
  refurb_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  days INTEGER NOT NULL, cost DECIMAL(12,2) NOT NULL, grade_out VARCHAR NOT NULL, outcome VARCHAR NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR)""",
    "resale": """CREATE TABLE IF NOT EXISTS resale (
  sale_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL, sale_date DATE NOT NULL,
  price DECIMAL(12,2) NOT NULL, fees DECIMAL(12,2) NOT NULL DEFAULT 0, buyer_type VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL,
  is_synthetic BOOLEAN NOT NULL, source_file VARCHAR)""",
    "runs": """CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, command VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP,
  seed INTEGER, as_of DATE NOT NULL, thresholds_sha256 VARCHAR, counts_json VARCHAR)""",
    "forecast_runs": """CREATE TABLE IF NOT EXISTS forecast_runs (
  run_id VARCHAR PRIMARY KEY, as_of DATE NOT NULL, method VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
  n_train_total INTEGER NOT NULL, n_train_json VARCHAR NOT NULL, fit_quality_json VARCHAR NOT NULL,
  coefficients_json VARCHAR NOT NULL, sigma_log_json VARCHAR NOT NULL, as_is_ratio_json VARCHAR NOT NULL,
  feature_names_json VARCHAR NOT NULL)""",
    "rv_forecast_grid": """CREATE TABLE IF NOT EXISTS rv_forecast_grid (
  run_id VARCHAR NOT NULL, model VARCHAR NOT NULL, model_family VARCHAR NOT NULL, grade VARCHAR NOT NULL,
  months_since_launch INTEGER NOT NULL, n_launches_since INTEGER NOT NULL, storage_gb INTEGER NOT NULL,
  forecast_rv_ratio DOUBLE NOT NULL, ratio_low DOUBLE, ratio_high DOUBLE,
  list_price DECIMAL(12,2), forecast_rv_on_list DECIMAL(12,2),
  n_train INTEGER, fit_quality VARCHAR,
  PRIMARY KEY (model, grade, months_since_launch))""",
    "rv_forecast_current": """CREATE TABLE IF NOT EXISTS rv_forecast_current (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, run_id VARCHAR NOT NULL, model VARCHAR, model_family VARCHAR,
  grade_used VARCHAR NOT NULL, grade_source VARCHAR NOT NULL,
  months_since_launch DOUBLE, n_launches_since INTEGER,
  forecast_rv_ratio DOUBLE, forecast_rv DECIMAL(12,2),
  forecast_rv_employee_buyout DECIMAL(12,2), forecast_rv_b2b_wholesale DECIMAL(12,2), forecast_rv_as_is DECIMAL(12,2),
  forecast_rv_grade_b DECIMAL(12,2),
  fit_quality VARCHAR, n_train INTEGER)""",
    "rv_forecast_of_record": """CREATE TABLE IF NOT EXISTS rv_forecast_of_record (
  serial VARCHAR PRIMARY KEY, return_date DATE NOT NULL, run_id VARCHAR, run_as_of DATE,
  grade_used VARCHAR, target_date DATE, months_since_launch DOUBLE, n_launches_since INTEGER,
  forecast_rv_ratio DOUBLE, forecast_rv DECIMAL(12,2), is_missing BOOLEAN NOT NULL, missing_reason VARCHAR)""",
    "rv_forecast_error_monthly": """CREATE TABLE IF NOT EXISTS rv_forecast_error_monthly (
  month DATE NOT NULL, model_family VARCHAR NOT NULL,
  n_sales INTEGER NOT NULL, n_with_forecast INTEGER NOT NULL, n_excluded_as_is INTEGER NOT NULL,
  mape DOUBLE, bias DOUBLE, wape DOUBLE, mae_eur DOUBLE, realisation_ratio DOUBLE,
  sum_realised DECIMAL(14,2), sum_forecast DECIMAL(14,2), run_ids_used VARCHAR,
  PRIMARY KEY (month, model_family))""",
    "backtest_result": """CREATE TABLE IF NOT EXISTS backtest_result (
  backtest_id VARCHAR NOT NULL, run_at TIMESTAMP NOT NULL, cutoff DATE NOT NULL, model_family VARCHAR NOT NULL,
  n_train INTEGER, n_test INTEGER, mape DOUBLE, bias DOUBLE, wape DOUBLE, mae_eur DOUBLE, rmse_log DOUBLE,
  train_max_sale_date DATE, test_min_sale_date DATE, PRIMARY KEY (backtest_id, model_family))""",
    "advisories": """CREATE TABLE IF NOT EXISTS advisories (
  advisory_id VARCHAR PRIMARY KEY, as_of DATE NOT NULL, run_id VARCHAR, kind VARCHAR NOT NULL,
  subject_type VARCHAR NOT NULL, subject_id VARCHAR NOT NULL, confidence VARCHAR NOT NULL,
  payload_json VARCHAR NOT NULL, note VARCHAR, threshold_key VARCHAR, threshold_owner VARCHAR)""",
}


def _make_db_stub() -> types.ModuleType:
    import duckdb

    mod = types.ModuleType("restwert.db")
    mod.__doc__ = "TEST STUB of restwert.db (foundation not present yet). Same signatures as SPEC 2.6."

    def connect(path=":memory:", read_only=False):
        return duckdb.connect(str(path), read_only=read_only)

    def create_schema(con, drop_derived: bool = False) -> None:
        for ddl in _STUB_DDL.values():
            con.execute(ddl)

    def table_exists(con, table: str) -> bool:
        n = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        return n > 0

    def read_df(con, sql: str, params=None) -> pd.DataFrame:
        return con.execute(sql, params or []).df()

    def _columns(con, table: str) -> list[str]:
        return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]

    def write_df(con, table: str, df: pd.DataFrame, mode: str = "replace") -> int:
        if mode == "replace":
            con.execute(f"DELETE FROM {table}")
        if df is None or len(df) == 0:
            return 0
        cols = _columns(con, table)
        frame = df.copy()
        for c in cols:
            if c not in frame.columns:
                frame[c] = None
        frame = frame[cols]
        con.register("_stub_df", frame)
        con.execute(f"INSERT INTO {table} SELECT * FROM _stub_df")
        con.unregister("_stub_df")
        return int(len(frame))

    def append_rows(con, table: str, df: pd.DataFrame) -> int:
        return write_df(con, table, df, mode="append")

    def is_synthetic(con) -> bool:
        if not table_exists(con, "devices"):
            return False
        return bool(con.execute("SELECT coalesce(bool_or(is_synthetic), false) FROM devices").fetchone()[0])

    def new_run(con, command: str, seed, as_of: date, thresholds_sha256) -> str:
        run_id = f"{command}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:4]}"
        con.execute(
            "INSERT INTO runs (run_id, command, started_at, seed, as_of, thresholds_sha256) VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, command, datetime.now(timezone.utc).replace(tzinfo=None), seed, as_of, thresholds_sha256],
        )
        return run_id

    def finish_run(con, run_id: str, counts: dict) -> None:
        con.execute(
            "UPDATE runs SET finished_at = ?, counts_json = ? WHERE run_id = ?",
            [datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(counts, sort_keys=True), run_id],
        )

    for fn in (connect, create_schema, table_exists, read_df, write_df, append_rows, is_synthetic, new_run, finish_run):
        setattr(mod, fn.__name__, fn)
    mod.IS_STUB = True
    return mod


def ensure_foundation() -> bool:
    """Install the ``restwert.db`` stub when the real module is absent. Returns True if stubbed."""
    if "restwert.db" in sys.modules:
        return bool(getattr(sys.modules["restwert.db"], "IS_STUB", False))
    if importlib.util.find_spec("restwert.db") is not None:
        return False
    import restwert  # noqa: F401  (namespace or real package)

    stub = _make_db_stub()
    sys.modules["restwert.db"] = stub
    setattr(sys.modules["restwert"], "db", stub)
    return True


STUBBED = ensure_foundation()


def fresh_db():
    """In-memory DuckDB with every table the forecast module reads or writes."""
    from restwert import db

    con = db.connect(":memory:")
    db.create_schema(con)
    for ddl in _STUB_DDL.values():  # idempotent: CREATE TABLE IF NOT EXISTS
        con.execute(ddl)
    return con


# --------------------------------------------------------------------------------------
# config objects (built in code, no yaml)
# --------------------------------------------------------------------------------------
_TRUTH_DEFAULT = {
    "base": 0.92, "lambda": 0.028, "lambda_after_24": 0.017, "step": 0.88, "noise": 0.10,
    "storage_exp": 0.08, "grade_A": 1.0, "grade_B": 0.88, "grade_C": 0.72, "grade_D": 0.45,
}
FAMILY_PARAMS = {
    "iphone_like": dict(first_launch=date(2019, 9, 15), launch_cadence_months=12, launch_month=9,
                        list_price_min=799, list_price_max=1299, storage_options=[128, 256, 512], base_storage_gb=128,
                        truth={**_TRUTH_DEFAULT}),
    "android_like": dict(first_launch=date(2020, 2, 15), launch_cadence_months=12, launch_month=2,
                         list_price_min=449, list_price_max=899, storage_options=[128, 256], base_storage_gb=128,
                         truth={**_TRUTH_DEFAULT, "base": 0.88, "lambda": 0.036, "lambda_after_24": 0.022, "step": 0.85}),
    "laptop_like": dict(first_launch=date(2020, 3, 15), launch_cadence_months=18, launch_month=None,
                        list_price_min=1199, list_price_max=1899, storage_options=[256, 512, 1024], base_storage_gb=256,
                        truth={**_TRUTH_DEFAULT, "base": 0.90, "lambda": 0.022, "lambda_after_24": 0.015, "step": 0.93}),
}
MODEL_PREFIX = {"iphone_like": "P", "android_like": "A", "laptop_like": "L"}


def families_cfg():
    from restwert.config import FamilyConfig

    out = {}
    for name, p in FAMILY_PARAMS.items():
        out[name] = FamilyConfig(
            name=name, discount_min=0.08, discount_max=0.18, freight_duty_pct=0.035,
            damage_rate_pa=0.10, repair_share=0.70, repair_cost_min=60, repair_cost_max=320,
            share_of_fleet=1 / 3, term_mix={12: 0.10, 24: 0.50, 36: 0.35, 48: 0.05}, monthly_rate_pct_of_landed=0.042, **p,
        )
    return out


class _Cfg:
    """Minimal stand-in for GeneratorConfig: only ``families``, ``seed`` and ``as_of`` are read."""

    def __init__(self, families, seed: int = 7, as_of: date = date(2026, 6, 30)):
        self.families = families
        self.seed = seed
        self.as_of = as_of


def generator_cfg(as_of: date = date(2026, 6, 30)):
    return _Cfg(families_cfg(), seed=7, as_of=as_of)


def assumptions():
    from restwert.config import AssumptionBlock, Assumptions

    blocks = {
        "planned_rv_ratio": AssumptionBlock(owner="CFO (name)", values={"iphone_like": 0.20, "android_like": 0.10, "laptop_like": 0.15}),
        "holding_cost_per_day_eur": AssumptionBlock(owner="CFO (name)", value=0.30),
        "channel_fees": AssumptionBlock(owner="Head of Recommerce (name)", values={
            "employee_buyout": {"fee_pct": 0.0, "fee_fixed_eur": 0.0, "days_to_cash": 14},
            "marketplace": {"fee_pct": 0.12, "fee_fixed_eur": 2.5, "days_to_cash": 28},
            "b2b_wholesale": {"fee_pct": 0.03, "fee_fixed_eur": 0.0, "days_to_cash": 45},
            "as_is": {"fee_pct": 0.05, "fee_fixed_eur": 0.0, "days_to_cash": 30}}),
        "expected_grade_at_return": AssumptionBlock(owner="Head of Recommerce (name)", values={"iphone_like": "B", "android_like": "B", "laptop_like": "B"}),
        "expected_return_to_sale_days": AssumptionBlock(owner="Head of Recommerce (name)", values={"iphone_like": 30, "android_like": 35, "laptop_like": 40}),
        "as_is_ratio_fallback": AssumptionBlock(owner="Head of Recommerce (name)", values={"iphone_like": 0.10, "android_like": 0.08, "laptop_like": 0.10}),
    }
    return Assumptions(version=1, blocks=blocks)


def thresholds(lookahead_days: int = 90, min_drop_pct: float = 0.08, bias_pct: float = 0.08):
    from restwert.config import Thresholds, ThresholdSpec

    common = dict(rationale="test placeholder", valid_from=date(2026, 1, 1), placeholder_default=True)
    return Thresholds(version=1, thresholds={
        "sell_before_launch_lookahead_days": ThresholdSpec(value=lookahead_days, unit="days", owner="Head of Recommerce (name)", rule_ids=["ADV01"], **common),
        "sell_before_launch_min_drop_pct": ThresholdSpec(value=min_drop_pct, unit="ratio", owner="Head of Recommerce (name)", rule_ids=["ADV01"], **common),
        "forecast_recalibration_bias_pct": ThresholdSpec(value=bias_pct, unit="ratio", owner="Head of Recommerce (name)", rule_ids=["ADV02"], **common),
        "as_is_only_grade": ThresholdSpec(value="D", unit="grade", owner="Head of Recommerce (name)", rule_ids=["R02"], **common),
    })


# --------------------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------------------
def catalogue(until: date = date(2026, 6, 30)) -> pd.DataFrame:
    """Every calendar-rule generation per family launched at or before ``until``."""
    from restwert.dates import add_months

    rows = []
    for fam, p in FAMILY_PARAMS.items():
        d = p["first_launch"]
        gen = 1
        while d <= until:
            rows.append({
                "model": f"{MODEL_PREFIX[fam]}-Gen{gen:02d}", "model_family": fam, "generation": gen,
                "launch_date": d, "list_price": float((p["list_price_min"] + p["list_price_max"]) // 2 // 10 * 10 + 9),
                "base_storage_gb": p["base_storage_gb"], "is_synthetic": True, "source_file": "model_catalogue.csv",
            })
            d = add_months(p["first_launch"], gen * p["launch_cadence_months"])
            gen += 1
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------
# synthetic-of-synthetic training frame (known coefficients)
# --------------------------------------------------------------------------------------
KNOWN = {
    "intercept": math.log(0.92),
    "months_since_launch": -0.03,
    "n_launches_since": math.log(0.88),
    "grade_B": math.log(0.88),
    "grade_C": math.log(0.72),
    "grade_D": math.log(0.45),
    "log_storage": 0.08,
    "ch_employee_buyout": math.log(0.95),
    "ch_b2b_wholesale": math.log(0.85),
}


def known_curve_frame(n: int = 600, seed: int = 11, family: str = "iphone_like", noise: float = 0.05,
                      start_serial: int = 1) -> pd.DataFrame:
    """``n`` rows from the KNOWN log-linear curve with N(0, noise) on the log scale."""
    rng = np.random.default_rng(seed)
    m = rng.uniform(6, 42, n)
    steps = np.floor(m / 12).astype(int)
    grade = rng.choice(["A", "B", "C", "D"], n, p=[0.35, 0.40, 0.20, 0.05])
    storage = rng.choice([128, 256, 512], n)
    channel = rng.choice(["employee_buyout", "marketplace", "b2b_wholesale"], n, p=[0.25, 0.5, 0.25])
    log_storage = np.log(storage / 128)
    y = (
        KNOWN["intercept"] + KNOWN["months_since_launch"] * m + KNOWN["n_launches_since"] * steps
        + KNOWN["grade_B"] * (grade == "B") + KNOWN["grade_C"] * (grade == "C") + KNOWN["grade_D"] * (grade == "D")
        + KNOWN["log_storage"] * log_storage
        + KNOWN["ch_employee_buyout"] * (channel == "employee_buyout") + KNOWN["ch_b2b_wholesale"] * (channel == "b2b_wholesale")
        + rng.normal(0, noise, n)
    )
    purchase_price = rng.uniform(700, 1100, n).round(2)
    rv = np.exp(y)
    sale_date = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 700, n), unit="D")
    return pd.DataFrame({
        "serial": [f"D-{i:06d}" for i in range(start_serial, start_serial + n)],
        "model_family": family, "model": f"{MODEL_PREFIX[family]}-Gen05", "sale_date": sale_date, "channel": channel,
        "grade": grade, "storage_gb": storage.astype(float), "base_storage_gb": 128.0,
        "purchase_price": purchase_price, "price": (rv * purchase_price).round(2), "rv_ratio": rv, "y": y,
        "months_since_launch": m, "n_launches_since": steps, "log_storage": log_storage,
    })


def as_is_rows(family: str = "iphone_like", n: int = 12, ratio: float = 0.11) -> pd.DataFrame:
    return pd.DataFrame({
        "serial": [f"X-{i:04d}" for i in range(n)], "model_family": family,
        "sale_date": pd.Timestamp("2025-01-15"), "price": 100.0 * ratio, "purchase_price": 100.0, "rv_ratio": ratio,
    })


# --------------------------------------------------------------------------------------
# mini fleet (devices, events, refurbishment, resale) with a hidden truth curve
# --------------------------------------------------------------------------------------
def _true_ratio(fam: str, m: float, n_launch: int, grade: str, storage: int, channel: str, noise: float) -> float:
    p = FAMILY_PARAMS[fam]
    t = p["truth"]
    decay = math.exp(-t["lambda"] * min(m, 24) - t["lambda_after_24"] * max(m - 24, 0))
    ch_mult = {"employee_buyout": 0.95, "marketplace": 1.0, "b2b_wholesale": 0.85, "as_is": 0.55}[channel]
    r = t["base"] * decay * t["step"] ** n_launch * t[f"grade_{grade}"] * ch_mult
    r *= (storage / p["base_storage_gb"]) ** t["storage_exp"] * math.exp(noise)
    if grade == "D":
        r *= 1 - 0.004 * m
    return float(min(max(r, 0.02), 1.10))


def mini_fleet(n_devices: int = 400, seed: int = 7, as_of: date = date(2026, 6, 30), purchase_days: int = 1460) -> dict[str, pd.DataFrame]:
    """A small fleet: purchases from 2022-01-01 over ``purchase_days`` days, 24/36-month rentals,
    returns, refurbishment, resale.

    Returns dict with model_catalogue, devices, events, refurbishment, resale. All rows
    carry ``is_synthetic = True``.
    """
    from restwert.dates import add_months

    rng = np.random.default_rng(seed)
    cat = catalogue(as_of)
    fams = list(FAMILY_PARAMS)
    fam_of = rng.choice(fams, n_devices, p=[0.5, 0.3, 0.2])
    devices, events, refurb, resale = [], [], [], []
    ev = rf = sl = 0
    for i in range(n_devices):
        fam = fam_of[i]
        p = FAMILY_PARAMS[fam]
        purchase = date(2022, 1, 1) + timedelta(days=int(rng.integers(0, purchase_days)))
        gens = cat[(cat["model_family"] == fam) & (pd.to_datetime(cat["launch_date"]) <= pd.Timestamp(purchase))]
        if len(gens) == 0:
            gens = cat[cat["model_family"] == fam].head(1)
        row = gens.iloc[-1] if rng.random() < 0.8 or len(gens) == 1 else gens.iloc[-2]
        launch = pd.Timestamp(row["launch_date"]).date()
        list_price = float(row["list_price"])
        storage = int(rng.choice(p["storage_options"]))
        purchase_price = round(list_price * (1 - rng.uniform(0.08, 0.18)), 2)
        serial = f"D-{i + 1:06d}"
        devices.append({
            "serial": serial, "model_family": fam, "model": row["model"], "storage_gb": storage, "colour": "black",
            "launch_date": launch, "purchase_date": purchase, "purchase_price": purchase_price,
            "landed_cost": round(purchase_price * 1.035, 2), "supplier": "Supplier-A", "channel_in": "distributor",
            "po_number": f"PO-{i // 10 + 1:06d}", "contract_id": f"RC-{i + 1:06d}", "is_synthetic": True, "source_file": "devices.csv",
        })
        term = 36 if rng.random() < 0.35 else 24
        start = purchase + timedelta(days=int(rng.integers(3, 30)))
        end = add_months(start, term)
        if end > as_of:
            continue
        return_date = end + timedelta(days=int(rng.integers(2, 15)))
        if return_date > as_of:
            continue
        pre = rng.choice(["A", "B", "C", "D"], p=[0.35, 0.40, 0.20, 0.05])
        order = "ABCD"
        insp = pre
        u = rng.random()
        if u < 0.20:
            insp = order[min(order.index(pre) + 1, 3)]
        elif u < 0.25:
            insp = order[max(order.index(pre) - 1, 0)]
        ev += 1
        events.append({
            "event_id": f"EV-{ev:06d}", "serial": serial, "contract_id": f"RC-{i + 1:06d}", "event_type": "return",
            "event_date": end, "cost": 9.5, "damage_type": None, "resolved": None, "replacement_serial": None,
            "return_date": return_date, "grade_pre_return": pre, "grade_inspected": insp, "wipe_certificate": True,
            "note": None, "is_synthetic": True, "source_file": "events.csv",
        })
        rstart = return_date + timedelta(days=int(rng.integers(1, 5)))
        days = int(rng.integers(3, 15))
        rend = rstart + timedelta(days=days)
        outcome = "sellable" if insp != "D" else ("scrap" if rng.random() < 0.1 else "as_is")
        rf += 1
        refurb.append({
            "refurb_id": f"RF-{rf:06d}", "serial": serial, "start_date": rstart, "end_date": rend, "days": days,
            "cost": 30.0, "grade_out": insp, "outcome": outcome, "is_synthetic": True, "source_file": "refurbishment.csv",
        })
        if outcome == "scrap" or rend > as_of:
            continue
        channel = "as_is" if insp == "D" else rng.choice(["employee_buyout", "marketplace", "b2b_wholesale"], p=[0.25, 0.5, 0.25])
        sale = rend + timedelta(days=int(rng.integers(5, 45)))
        if sale > as_of:
            continue
        m = (sale - launch).days / 30.4375
        cat_fam = cat[cat["model_family"] == fam]
        n_launch = int(((pd.to_datetime(cat_fam["launch_date"]) > pd.Timestamp(launch)) & (pd.to_datetime(cat_fam["launch_date"]) <= pd.Timestamp(sale))).sum())
        ratio = _true_ratio(fam, m, n_launch, insp, storage, channel, rng.normal(0, p["truth"]["noise"]))
        price = round(purchase_price * ratio, 2)
        fee_pct = {"employee_buyout": 0.0, "marketplace": 0.12, "b2b_wholesale": 0.03, "as_is": 0.05}[channel]
        sl += 1
        resale.append({
            "sale_id": f"S-{sl:06d}", "serial": serial, "channel": channel, "sale_date": sale, "price": price,
            "fees": round(price * fee_pct + (2.5 if channel == "marketplace" else 0.0), 2),
            "buyer_type": {"employee_buyout": "employee", "marketplace": "consumer", "b2b_wholesale": "trader", "as_is": "recycler"}[channel],
            "grade_at_sale": insp, "is_synthetic": True, "source_file": "resale.csv",
        })
    return {
        "model_catalogue": cat,
        "devices": pd.DataFrame(devices),
        "events": pd.DataFrame(events),
        "refurbishment": pd.DataFrame(refurb),
        "resale": pd.DataFrame(resale),
    }


def load_fleet(con, frames: dict[str, pd.DataFrame]) -> None:
    from restwert import db

    for table in ("model_catalogue", "devices", "events", "refurbishment", "resale"):
        db.write_df(con, table, frames[table], mode="replace")


# --------------------------------------------------------------------------------------
# small hand frames for the error series
# --------------------------------------------------------------------------------------
def six_row_error_fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Six sales in 2026-03: four iphone_like with a forecast, one as_is, one without a forecast.

    Hand numbers for the four forecasted rows (price, forecast):
      (100, 110) (200, 180) (300, 330) (400, 400)
      ape  = 0.10, 0.10, 0.10, 0.00  -> MAPE 0.075
      bias = +0.10, -0.10, +0.10, 0  -> +0.025
      WAPE = (10 + 20 + 30 + 0) / 1000 = 0.06 ; MAE = 15 ; realisation = 1000/1020 - 1
    """
    devices = pd.DataFrame({
        "serial": [f"D-{i:06d}" for i in range(1, 7)],
        "model_family": ["iphone_like"] * 4 + ["android_like", "iphone_like"],
    })
    resale = pd.DataFrame({
        "sale_id": [f"S-{i:06d}" for i in range(1, 7)],
        "serial": devices["serial"],
        "channel": ["marketplace", "employee_buyout", "b2b_wholesale", "marketplace", "as_is", "marketplace"],
        "sale_date": [date(2026, 3, 3), date(2026, 3, 10), date(2026, 3, 15), date(2026, 3, 28), date(2026, 3, 20), date(2026, 3, 31)],
        "price": [100.0, 200.0, 300.0, 400.0, 50.0, 250.0],
        "fees": 0.0, "buyer_type": "consumer", "grade_at_sale": "B", "is_synthetic": True, "source_file": "resale.csv",
    })
    record = pd.DataFrame({
        "serial": devices["serial"],
        "return_date": pd.Timestamp("2026-02-10"),
        "run_id": ["rv-2026-01-31", "rv-2026-01-31", "rv-2026-01-31", "rv-2026-01-31", "rv-2026-01-31", None],
        "run_as_of": pd.Timestamp("2026-01-31"),
        "grade_used": "B", "target_date": pd.Timestamp("2026-03-12"), "months_since_launch": 20.0, "n_launches_since": 1,
        "forecast_rv_ratio": 0.3,
        "forecast_rv": [110.0, 180.0, 330.0, 400.0, 60.0, np.nan],
        "is_missing": [False, False, False, False, False, True],
        "missing_reason": [None, None, None, None, None, "no run before return_date"],
    })
    return resale, devices, record
