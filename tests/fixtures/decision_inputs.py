"""Fixtures for tests/test_decisions.py (module 4).

Provides an inline thresholds YAML (same keys and values as config/thresholds.yaml), an
``Assumptions`` object for the channel fees, hand-built rule inputs and a tiny fixture database
so the runner can be exercised end to end on an in-memory DuckDB.

While ``restwert/db.py`` (module 1) is still being written, ``ensure_foundation()`` installs a
minimal stand-in for it into ``sys.modules`` so this module's tests can run. The stand-in is
never used once the real file exists, and it lives here in the tests, never in package code
(spec section 10).
"""

from __future__ import annotations

import importlib
import json
import sys
import types
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AS_OF = date(2026, 6, 30)
RUN_ID = "decide-test-0001"

# --------------------------------------------------------------------------------------
# foundation stand-in (db only; the other foundation files are required)
# --------------------------------------------------------------------------------------

FOUNDATION_STUBBED: list[str] = []

_MIN_DDL: dict[str, str] = {
    "runs": """CREATE TABLE IF NOT EXISTS runs (
      run_id VARCHAR PRIMARY KEY, command VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP,
      seed INTEGER, as_of DATE NOT NULL, thresholds_sha256 VARCHAR, counts_json VARCHAR);""",
    "decision_log": """CREATE TABLE IF NOT EXISTS decision_log (
      decision_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, decided_at TIMESTAMP NOT NULL, as_of DATE NOT NULL,
      rule_id VARCHAR NOT NULL, rule_version VARCHAR NOT NULL, subject_type VARCHAR NOT NULL, subject_id VARCHAR NOT NULL,
      outcome VARCHAR NOT NULL, outcome_detail VARCHAR, threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL,
      threshold_value_num DOUBLE, threshold_unit VARCHAR, threshold_owner VARCHAR NOT NULL,
      inputs_json VARCHAR NOT NULL, advisory_json VARCHAR, value_at_stake_eur DECIMAL(12,2), due_date DATE,
      input_hash VARCHAR NOT NULL UNIQUE, is_synthetic_input BOOLEAN NOT NULL);""",
    "decision_queue": """CREATE TABLE IF NOT EXISTS decision_queue (
      decision_id VARCHAR PRIMARY KEY, run_id VARCHAR, as_of DATE, priority INTEGER NOT NULL, rule_id VARCHAR, rule_name VARCHAR,
      outcome VARCHAR, subject_type VARCHAR, subject_id VARCHAR, explanation VARCHAR, threshold_key VARCHAR,
      threshold_value VARCHAR, threshold_owner VARCHAR, due_date DATE, value_at_stake_eur DECIMAL(12,2),
      advisory_kind VARCHAR, advisory_confidence VARCHAR, advisory_json VARCHAR);""",
    "write_down_ledger": """CREATE TABLE IF NOT EXISTS write_down_ledger (
      write_down_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, as_of DATE NOT NULL, rule_id VARCHAR NOT NULL,
      decision_id VARCHAR NOT NULL, book_value_before DECIMAL(12,2) NOT NULL, amount DECIMAL(12,2) NOT NULL,
      book_value_after DECIMAL(12,2) NOT NULL, threshold_owner VARCHAR NOT NULL, run_id VARCHAR NOT NULL,
      UNIQUE (serial, rule_id, as_of));""",
}


def _make_db_stub() -> types.ModuleType:
    """A minimal restwert.db with the spec 2.6 signatures the decisions module uses."""
    import duckdb

    mod = types.ModuleType("restwert.db")
    try:
        from restwert import schema as _schema

        ddl = dict(_schema.DDL)
        order = list(_schema.TABLE_ORDER)
    except Exception:  # pragma: no cover - schema.py absent as well
        ddl = dict(_MIN_DDL)
        order = list(_MIN_DDL)

    def connect(path=":memory:", read_only: bool = False):
        return duckdb.connect(str(path), read_only=read_only)

    def create_schema(con, drop_derived: bool = False) -> None:
        for t in order:
            con.execute(ddl[t])

    def table_exists(con, table: str) -> bool:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()
        return bool(row and row[0])

    def read_df(con, sql: str, params=None) -> pd.DataFrame:
        return con.execute(sql, params or []).df()

    def write_df(con, table: str, df: pd.DataFrame, mode: str = "replace") -> int:
        if mode == "replace":
            con.execute(f"DELETE FROM {table}")
        if df is None or len(df) == 0:
            return 0
        info = con.execute(f"DESCRIBE {table}").fetchall()
        types_by_col = {r[0]: r[1] for r in info}
        cols = [c for c in df.columns if c in types_by_col]
        frame = df[cols].reset_index(drop=True)
        # NaN / NaT -> None so that CAST into INTEGER / DATE columns never sees a NaN
        frame = frame.astype(object).where(pd.notna(frame), None)
        con.register("_restwert_tmp", frame)
        select = ", ".join(f'CAST("{c}" AS {types_by_col[c]}) AS "{c}"' for c in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        con.execute(f"INSERT INTO {table} ({collist}) SELECT {select} FROM _restwert_tmp")
        con.unregister("_restwert_tmp")
        return int(len(frame))

    def append_rows(con, table: str, df: pd.DataFrame) -> int:
        return write_df(con, table, df, mode="append")

    def is_synthetic(con) -> bool:
        if not table_exists(con, "devices"):
            return False
        row = con.execute("SELECT count(*) FROM devices WHERE is_synthetic").fetchone()
        return bool(row and row[0])

    def new_run(con, command: str, seed, as_of: date, thresholds_sha256) -> str:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        run_id = f"{command}-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:4]}"
        con.execute(
            "INSERT INTO runs (run_id, command, started_at, seed, as_of, thresholds_sha256) VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, command, now, seed, as_of, thresholds_sha256],
        )
        return run_id

    def finish_run(con, run_id: str, counts: dict) -> None:
        con.execute(
            "UPDATE runs SET finished_at = ?, counts_json = ? WHERE run_id = ?",
            [datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(counts, sort_keys=True), run_id],
        )

    for name, fn in {
        "connect": connect,
        "create_schema": create_schema,
        "table_exists": table_exists,
        "read_df": read_df,
        "write_df": write_df,
        "append_rows": append_rows,
        "is_synthetic": is_synthetic,
        "new_run": new_run,
        "finish_run": finish_run,
    }.items():
        setattr(mod, name, fn)
    return mod


def ensure_foundation() -> None:
    """Install the db stand-in when restwert/db.py does not exist yet (idempotent)."""
    if not (ROOT / "restwert" / "db.py").exists() and "restwert.db" not in sys.modules:
        pkg = importlib.import_module("restwert")
        stub = _make_db_stub()
        sys.modules["restwert.db"] = stub
        setattr(pkg, "db", stub)
        FOUNDATION_STUBBED.append("db")


ensure_foundation()

from restwert import db  # noqa: E402
from restwert.config import AssumptionBlock, Assumptions, Thresholds, load_thresholds  # noqa: E402
from restwert.records import Advisory  # noqa: E402

# --------------------------------------------------------------------------------------
# thresholds (inline; same keys and values as the shipped config/thresholds.yaml)
# --------------------------------------------------------------------------------------

EXPECTED_THRESHOLD_KEYS: tuple[str, ...] = (
    "repair_max_share_of_rv",
    "repair_min_rv_eur",
    "channel_min_net_uplift_eur",
    "channel_max_days_to_cash",
    "as_is_only_grade",
    "employee_buyout_window_days",
    "aging_days_90",
    "aging_days_180",
    "age_write_down_pct_90",
    "age_write_down_pct_180",
    "replacement_max_months_since_launch",
    "replacement_min_storage_gb",
    "replacement_requires_wipe",
    "price_protection_reminder_days",
    "price_protection_min_claim_eur",
    "renewal_alert_lead_days",
    "renewal_high_value_eur",
    "rental_notice_days",
    "sell_before_launch_lookahead_days",
    "sell_before_launch_min_drop_pct",
    "forecast_recalibration_bias_pct",
)

#: v0.2 keys added by the levers module (spec v0.2 section 3.6): R07, ADV03, ADV04.
V02_THRESHOLD_KEYS: tuple[str, ...] = (
    "purchase_discount_floor_pct",
    "oem_realisation_gap_pct",
    "term_result_gap_alert_eur",
)

TEST_THRESHOLDS_YAML = """
version: 1
thresholds:
  repair_max_share_of_rv:
    values: {iphone_like: 0.40, android_like: 0.35, laptop_like: 0.45, tablet_like: 0.38}
    unit: ratio
    owner: "Head of Service Operations (test)"
    rationale: "test placeholder"
    valid_from: 2026-01-01
    placeholder_default: true
    rule_ids: [R01]
  repair_min_rv_eur:
    values: {iphone_like: 60, android_like: 40, laptop_like: 120, tablet_like: 60}
    unit: EUR
    owner: "Head of Recommerce (test)"
    rationale: "test placeholder"
    valid_from: 2026-01-01
    placeholder_default: true
    rule_ids: [R01]
  channel_min_net_uplift_eur: {value: 15, unit: EUR, owner: "Head of Recommerce (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R02]}
  channel_max_days_to_cash: {value: 60, unit: days, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R02]}
  as_is_only_grade: {value: "D", unit: grade, owner: "Head of Recommerce (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R02]}
  employee_buyout_window_days: {value: 60, unit: days, owner: "Head of Customer Success (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R02]}
  aging_days_90: {value: 90, unit: days, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R03]}
  aging_days_180: {value: 180, unit: days, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R03]}
  age_write_down_pct_90: {value: 0.10, unit: ratio, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R03]}
  age_write_down_pct_180: {value: 0.25, unit: ratio, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R03]}
  replacement_max_months_since_launch:
    values: {iphone_like: 24, android_like: 24, laptop_like: 36, tablet_like: 30}
    unit: months
    owner: "Head of Service Operations (test)"
    rationale: "t"
    valid_from: 2026-01-01
    placeholder_default: true
    rule_ids: [R04]
  replacement_min_storage_gb:
    values: {iphone_like: 128, android_like: 128, laptop_like: 256, tablet_like: 128}
    unit: GB
    owner: "Head of Service Operations (test)"
    rationale: "t"
    valid_from: 2026-01-01
    placeholder_default: true
    rule_ids: [R04]
  replacement_requires_wipe: {value: true, unit: bool, owner: "Data Protection Officer (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: false, rule_ids: [R04]}
  price_protection_reminder_days: {value: 14, unit: days, owner: "Category Manager Hardware (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R05]}
  price_protection_min_claim_eur: {value: 500, unit: EUR, owner: "Category Manager Hardware (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R05]}
  renewal_alert_lead_days: {value: 60, unit: days, owner: "Head of Procurement (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R06]}
  renewal_high_value_eur: {value: 250000, unit: EUR, owner: "CFO (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R06]}
  rental_notice_days: {value: 90, unit: days, owner: "Head of Customer Success (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [R06]}
  sell_before_launch_lookahead_days: {value: 90, unit: days, owner: "Head of Recommerce (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [ADV01]}
  sell_before_launch_min_drop_pct: {value: 0.08, unit: ratio, owner: "Head of Recommerce (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [ADV01]}
  forecast_recalibration_bias_pct: {value: 0.08, unit: ratio, owner: "Head of Recommerce (test)", rationale: "t", valid_from: 2026-01-01, placeholder_default: true, rule_ids: [ADV02]}
"""


def thresholds_from_tmp(tmp_path: Path) -> Thresholds:
    """Write the inline YAML to a tmp file and load it through the real loader."""
    p = Path(tmp_path) / "thresholds.yaml"
    p.write_text(TEST_THRESHOLDS_YAML, encoding="utf-8")
    return load_thresholds(p)


def shipped_thresholds() -> Thresholds:
    return load_thresholds(ROOT / "config" / "thresholds.yaml")


# --------------------------------------------------------------------------------------
# assumptions and hand-built rule inputs
# --------------------------------------------------------------------------------------

CHANNEL_FEES = {
    "employee_buyout": {"fee_pct": 0.00, "fee_fixed_eur": 0.0, "days_to_cash": 14},
    "marketplace": {"fee_pct": 0.12, "fee_fixed_eur": 2.50, "days_to_cash": 28},
    "b2b_wholesale": {"fee_pct": 0.03, "fee_fixed_eur": 0.0, "days_to_cash": 45},
    "as_is": {"fee_pct": 0.05, "fee_fixed_eur": 0.0, "days_to_cash": 30},
}


def make_assumptions() -> Assumptions:
    return Assumptions(
        version=1,
        blocks={
            "channel_fees": AssumptionBlock(owner="Head of Recommerce (test)", values=CHANNEL_FEES),
            "holding_cost_per_day_eur": AssumptionBlock(owner="CFO (test)", value=0.30),
        },
    )


FEE_PCT = {c: v["fee_pct"] for c, v in CHANNEL_FEES.items()}
FEE_FIXED = {c: v["fee_fixed_eur"] for c, v in CHANNEL_FEES.items()}
DAYS_TO_CASH = {c: float(v["days_to_cash"]) for c, v in CHANNEL_FEES.items()}
HOLDING = 0.30


def channel_kwargs(**over) -> dict:
    """Default R02 inputs: grade B, all four channels forecast, buyout eligible."""
    base = dict(
        serial="D-000001",
        family="iphone_like",
        grade="B",
        forecast_rv_by_channel={
            "marketplace": 300.0,
            "employee_buyout": 285.0,
            "b2b_wholesale": 255.0,
            "as_is": 100.0,
        },
        fee_pct=dict(FEE_PCT),
        fee_fixed_eur=dict(FEE_FIXED),
        days_to_cash=dict(DAYS_TO_CASH),
        holding_cost_per_day=HOLDING,
        buyout_eligible=True,
        as_of=AS_OF,
        run_id=RUN_ID,
    )
    base.update(over)
    return base


def net(channel: str, rv: float) -> float:
    """Hand formula for R02 net (mirrors spec section 9)."""
    return rv * (1 - FEE_PCT[channel]) - FEE_FIXED[channel] - HOLDING * DAYS_TO_CASH[channel]


def sample_advisory() -> Advisory:
    return Advisory(
        kind="sell_before_launch",
        run_id="rv-2026-06-30",
        payload={"rv_now": 300.0, "rv_after": 260.0, "drop_pct": 0.133, "delta_eur": 31.0, "days_to_launch": 77},
        confidence="medium",
        note="test advisory",
    )


# --------------------------------------------------------------------------------------
# tiny fixture database for the runner
# --------------------------------------------------------------------------------------


def _d(s: str) -> date:
    return date.fromisoformat(s)


def fixture_frames(as_of: date = AS_OF) -> dict[str, pd.DataFrame]:
    """Five devices, three POs, three supplier contracts, one advisory.

    Expected decisions at as_of 2026-06-30 (see test_runner_end_to_end):
    D-000001 in stock 200 d, android B, buyout window closed  -> R02 not employee_buyout, R03 write_down_180
    D-000002 in stock 95 d, iphone A, wipe, buyout eligible    -> R03 write_down_90, R04 eligible, R02 carries advisory
    D-000003 wip, laptop A without wipe                        -> R04 not_eligible (wipe)
    D-000004 rented, open damage quote 89 vs grade B RV 217    -> R01 no_repair_quote_above_share
    D-000005 in stock 10 d, iphone D                           -> R02 as_is, R03 none
    PO-000001 claim 1000 EUR, deadline 2026-07-25             -> R05 claim_open
    PO-000002 claim 1000 EUR, deadline 2026-07-09             -> R05 claim_reminder
    PO-000003 no contract                                     -> R05 no_action
    SC-01 end 2026-08-15, notice 30                           -> R06 renewal_alert
    SC-02 end 2027-06-30                                      -> R06 no_action
    SC-03 end 2026-07-10, notice 30, auto renewal, 300k       -> R06 notice_missed_auto_renews
    RC-000004 active, end 2027-06-01                          -> R06 no_action
    RC-000006 active, end 2026-09-01 (notice 90 -> passed)    -> R06 expiring_no_notice_possible
    """
    devices = pd.DataFrame(
        [
            dict(serial="D-000001", model_family="android_like", model="A-Gen05", storage_gb=128, colour="black",
                 launch_date=_d("2024-02-15"), purchase_date=_d("2024-04-01"), purchase_price=600.0, landed_cost=621.0,
                 supplier="Supplier-B", channel_in="distributor", po_number="PO-000001", contract_id="RC-000001",
                 is_synthetic=True, source_file="devices.csv"),
            dict(serial="D-000002", model_family="iphone_like", model="P-Gen06", storage_gb=256, colour="blue",
                 launch_date=_d("2024-09-15"), purchase_date=_d("2024-10-01"), purchase_price=900.0, landed_cost=931.5,
                 supplier="Supplier-A", channel_in="oem_direct", po_number="PO-000002", contract_id="RC-000002",
                 is_synthetic=True, source_file="devices.csv"),
            dict(serial="D-000003", model_family="laptop_like", model="L-Gen04", storage_gb=512, colour="grey",
                 launch_date=_d("2024-09-15"), purchase_date=_d("2024-11-01"), purchase_price=1500.0, landed_cost=1545.0,
                 supplier="Supplier-C", channel_in="distributor", po_number="PO-000003", contract_id="RC-000003",
                 is_synthetic=True, source_file="devices.csv"),
            dict(serial="D-000004", model_family="android_like", model="A-Gen06", storage_gb=128, colour="black",
                 launch_date=_d("2025-02-15"), purchase_date=_d("2025-03-01"), purchase_price=650.0, landed_cost=672.75,
                 supplier="Supplier-B", channel_in="distributor", po_number="PO-000001", contract_id="RC-000004",
                 is_synthetic=True, source_file="devices.csv"),
            dict(serial="D-000005", model_family="iphone_like", model="P-Gen05", storage_gb=128, colour="black",
                 launch_date=_d("2023-09-15"), purchase_date=_d("2023-10-01"), purchase_price=850.0, landed_cost=879.75,
                 supplier="Supplier-A", channel_in="distributor", po_number="PO-000002", contract_id="RC-000005",
                 is_synthetic=True, source_file="devices.csv"),
        ]
    )

    rental_contracts = pd.DataFrame(
        [
            dict(contract_id="RC-000001", serial="D-000001", customer_id="CUST-0001", start_date=_d("2024-04-10"),
                 term_months=24, monthly_rate=26.0, end_date=_d("2026-04-10"), actual_end_date=_d("2025-12-01"),
                 status="terminated_early", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
            dict(contract_id="RC-000002", serial="D-000002", customer_id="CUST-0002", start_date=_d("2024-10-10"),
                 term_months=24, monthly_rate=39.0, end_date=_d("2026-10-10"), actual_end_date=_d("2026-06-01"),
                 status="terminated_early", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
            dict(contract_id="RC-000003", serial="D-000003", customer_id="CUST-0003", start_date=_d("2024-11-10"),
                 term_months=24, monthly_rate=58.0, end_date=_d("2026-11-10"), actual_end_date=_d("2026-06-20"),
                 status="terminated_early", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
            dict(contract_id="RC-000004", serial="D-000004", customer_id="CUST-0004", start_date=_d("2025-03-10"),
                 term_months=27, monthly_rate=30.0, end_date=_d("2027-06-01"), actual_end_date=None,
                 status="active", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
            dict(contract_id="RC-000005", serial="D-000005", customer_id="CUST-0005", start_date=_d("2023-10-10"),
                 term_months=24, monthly_rate=37.0, end_date=_d("2025-10-10"), actual_end_date=None,
                 status="ended", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
            dict(contract_id="RC-000006", serial="D-000004", customer_id="CUST-0004", start_date=_d("2025-09-01"),
                 term_months=12, monthly_rate=30.0, end_date=_d("2026-09-01"), actual_end_date=None,
                 status="active", replaces_contract_id=None, is_synthetic=True, source_file="rental_contracts.csv"),
        ]
    )

    events = pd.DataFrame(
        [
            dict(event_id="EV-000001", serial="D-000001", contract_id="RC-000001", event_type="return",
                 event_date=_d("2025-12-05"), cost=9.5, damage_type=None, resolved=None, replacement_serial=None,
                 return_date=_d("2025-12-05"), grade_pre_return="B", grade_inspected="B", wipe_certificate=True,
                 note=None, is_synthetic=True, source_file="events.csv"),
            dict(event_id="EV-000002", serial="D-000002", contract_id="RC-000002", event_type="return",
                 event_date=_d("2026-06-03"), cost=9.5, damage_type=None, resolved=None, replacement_serial=None,
                 return_date=_d("2026-06-03"), grade_pre_return="A", grade_inspected="A", wipe_certificate=True,
                 note=None, is_synthetic=True, source_file="events.csv"),
            dict(event_id="EV-000003", serial="D-000003", contract_id="RC-000003", event_type="return",
                 event_date=_d("2026-06-25"), cost=9.5, damage_type=None, resolved=None, replacement_serial=None,
                 return_date=_d("2026-06-25"), grade_pre_return="A", grade_inspected="A", wipe_certificate=False,
                 note=None, is_synthetic=True, source_file="events.csv"),
            dict(event_id="EV-000004", serial="D-000004", contract_id="RC-000004", event_type="damage",
                 event_date=_d("2026-06-20"), cost=89.0, damage_type="screen", resolved=False, replacement_serial=None,
                 return_date=None, grade_pre_return=None, grade_inspected=None, wipe_certificate=None,
                 note="open quote", is_synthetic=True, source_file="events.csv"),
            dict(event_id="EV-000005", serial="D-000005", contract_id="RC-000005", event_type="return",
                 event_date=_d("2025-10-15"), cost=9.5, damage_type=None, resolved=None, replacement_serial=None,
                 return_date=_d("2025-10-15"), grade_pre_return="C", grade_inspected="D", wipe_certificate=True,
                 note=None, is_synthetic=True, source_file="events.csv"),
        ]
    )

    def pnl(serial, fam, status, grade, days_in_stock, book, wdc, months, storage):
        return dict(
            serial=serial, as_of=as_of, model_family=fam, model=None, storage_gb=storage, purchase_date=None,
            cohort=None, launch_date=None, months_since_launch=months, landed_cost=None, purchase_price=None,
            months_billed=None, rental_revenue=None, return_date=None, grade_inspected=grade, sellable_date=None,
            refurb_outcome=None, grade_current=grade, resale_channel=None, sale_date=None, resale_price_gross=None,
            resale_fees=None, realised_rv=None, forecast_rv=None, repair_cost=None, replacement_logistics_cost=None,
            return_logistics_cost=None, refurb_cost=None, channel_fees=None, service_and_logistics_cost=None,
            lifecycle_margin=None, lifecycle_margin_pct_of_landed=None, margin_if_liquidated_today=None,
            lifecycle_status=status, is_closed=False, closed_date=None, days_in_stock=days_in_stock,
            days_in_wip=None, book_value_sl=None, write_down_cum=wdc, book_value=book,
        )

    device_pnl = pd.DataFrame(
        [
            pnl("D-000001", "android_like", "in_stock", "B", 200, 300.0, 0.0, 28.5, 128),
            pnl("D-000002", "iphone_like", "in_stock", "A", 95, 600.0, 0.0, 21.5, 256),
            pnl("D-000003", "laptop_like", "wip", "A", None, 1000.0, 0.0, 21.5, 512),
            pnl("D-000004", "android_like", "rented", None, None, 500.0, 0.0, 16.5, 128),
            pnl("D-000005", "iphone_like", "in_stock", "D", 10, 250.0, 0.0, 33.5, 128),
        ]
    )

    def fc(serial, fam, grade, mkt, emp, b2b, as_is, grade_b):
        return dict(
            serial=serial, as_of=as_of, run_id="rv-2026-06-30", model=None, model_family=fam, grade_used=grade,
            grade_source="inspected", months_since_launch=None, n_launches_since=None, forecast_rv_ratio=None,
            forecast_rv=mkt, forecast_rv_employee_buyout=emp, forecast_rv_b2b_wholesale=b2b, forecast_rv_as_is=as_is,
            forecast_rv_grade_b=grade_b, fit_quality="family", n_train=500,
        )

    rv_forecast_current = pd.DataFrame(
        [
            fc("D-000001", "android_like", "B", 180.0, 171.0, 153.0, 60.0, 180.0),
            fc("D-000002", "iphone_like", "A", 400.0, 380.0, 340.0, 90.0, 352.0),
            fc("D-000003", "laptop_like", "A", 700.0, 665.0, 595.0, 150.0, 630.0),
            fc("D-000004", "android_like", "B", 217.0, 206.0, 184.0, 52.0, 217.0),
            fc("D-000005", "iphone_like", "D", 120.0, 114.0, 102.0, 85.0, 200.0),
        ]
    )

    purchase_orders = pd.DataFrame(
        [
            dict(po_number="PO-000001", supplier="Supplier-B", supplier_contract_id="SC-01", model="A-Gen05",
                 order_date=_d("2026-05-01"), promised_date=_d("2026-05-20"), delivered_date=_d("2026-05-20"),
                 qty_ordered=50, qty_delivered=50, unit_price=600.0, benchmark_price=590.0,
                 price_drop_date=_d("2026-06-10"), price_drop_amount=20.0, is_synthetic=True, source_file="purchase_orders.csv"),
            dict(po_number="PO-000002", supplier="Supplier-B", supplier_contract_id="SC-01", model="A-Gen05",
                 order_date=_d("2026-04-15"), promised_date=_d("2026-05-05"), delivered_date=_d("2026-05-08"),
                 qty_ordered=50, qty_delivered=50, unit_price=600.0, benchmark_price=590.0,
                 price_drop_date=_d("2026-05-25"), price_drop_amount=20.0, is_synthetic=True, source_file="purchase_orders.csv"),
            dict(po_number="PO-000003", supplier="Supplier-C", supplier_contract_id=None, model="L-Gen04",
                 order_date=_d("2026-03-01"), promised_date=_d("2026-03-20"), delivered_date=_d("2026-03-22"),
                 qty_ordered=10, qty_delivered=10, unit_price=1500.0, benchmark_price=1450.0,
                 price_drop_date=None, price_drop_amount=None, is_synthetic=True, source_file="purchase_orders.csv"),
        ]
    )

    supplier_contracts = pd.DataFrame(
        [
            dict(supplier_contract_id="SC-01", supplier="Supplier-B", category="hardware", start_date=_d("2024-08-15"),
                 end_date=_d("2026-08-15"), auto_renewal=False, notice_days=30, price_protection=True,
                 price_protection_days=45, payment_terms_days=45, spend_under_contract=100000.0,
                 is_synthetic=True, source_file="supplier_contracts.csv"),
            dict(supplier_contract_id="SC-02", supplier="Supplier-A", category="hardware", start_date=_d("2024-07-01"),
                 end_date=_d("2027-06-30"), auto_renewal=False, notice_days=60, price_protection=False,
                 price_protection_days=None, payment_terms_days=30, spend_under_contract=200000.0,
                 is_synthetic=True, source_file="supplier_contracts.csv"),
            dict(supplier_contract_id="SC-03", supplier="Supplier-G", category="logistics", start_date=_d("2024-07-10"),
                 end_date=_d("2026-07-10"), auto_renewal=True, notice_days=30, price_protection=False,
                 price_protection_days=None, payment_terms_days=30, spend_under_contract=300000.0,
                 is_synthetic=True, source_file="supplier_contracts.csv"),
        ]
    )

    advisories = pd.DataFrame(
        [
            dict(advisory_id="ADV-000001", as_of=as_of, run_id="rv-2026-06-30", kind="sell_before_launch",
                 subject_type="device", subject_id="D-000002", confidence="medium",
                 payload_json=json.dumps({"rv_now": 400.0, "rv_after": 350.0, "drop_pct": 0.125, "delta_eur": 27.0,
                                          "days_to_launch": 77, "next_launch_date": "2026-09-15"}),
                 note="test advisory", threshold_key="sell_before_launch_lookahead_days",
                 threshold_owner="Head of Recommerce (test)"),
        ]
    )

    return {
        "devices": devices,
        "rental_contracts": rental_contracts,
        "events": events,
        "device_pnl": device_pnl,
        "rv_forecast_current": rv_forecast_current,
        "purchase_orders": purchase_orders,
        "supplier_contracts": supplier_contracts,
        "advisories": advisories,
    }


def build_fixture_db(as_of: date = AS_OF):
    """In-memory DuckDB with the full schema and the fixture frames loaded."""
    con = db.connect(":memory:")
    db.create_schema(con)
    for table, frame in fixture_frames(as_of).items():
        db.write_df(con, table, frame, mode="replace")
    return con


__all__ = [
    "ROOT",
    "AS_OF",
    "RUN_ID",
    "FOUNDATION_STUBBED",
    "EXPECTED_THRESHOLD_KEYS",
    "V02_THRESHOLD_KEYS",
    "TEST_THRESHOLDS_YAML",
    "thresholds_from_tmp",
    "shipped_thresholds",
    "make_assumptions",
    "channel_kwargs",
    "net",
    "sample_advisory",
    "fixture_frames",
    "build_fixture_db",
    "FEE_PCT",
    "FEE_FIXED",
    "DAYS_TO_CASH",
    "HOLDING",
]
