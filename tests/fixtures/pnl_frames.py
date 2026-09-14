"""Hand-built frames for ``tests/test_pnl.py`` (spec section 4.6, owned by module pnl).

Every frame carries every column of the source DDL in DDL order, so the same fixtures work
against ``db.write_df`` into a real schema and against the pure functions. All rows are
``is_synthetic = True``. No real company, customer or supplier name appears.

``ensure_foundation()`` makes the foundation importable when module 1 is not finished yet:
it puts the repository root on ``sys.path`` and installs a minimal, spec-shaped stand-in for
``restwert.config`` and ``restwert.db`` ONLY when the real module is missing (spec section
10 allows a test-local stub, never a package-level one). ``restwert.dates`` and
``restwert.records`` are always the real ones.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 6, 30)

# ----------------------------------------------------------------------------------------
# foundation availability
# ----------------------------------------------------------------------------------------

def _stub_config() -> types.ModuleType:
    import yaml
    from pydantic import BaseModel, ConfigDict

    mod = types.ModuleType("restwert.config")

    class AssumptionBlock(BaseModel):
        model_config = ConfigDict(extra="ignore")
        owner: str
        value: float | int | str | bool | None = None
        values: dict[str, Any] | None = None
        note: str = ""

    class Assumptions(BaseModel):
        version: int
        blocks: dict[str, AssumptionBlock]

        def get(self, key: str, sub: str | None = None) -> Any:
            if key not in self.blocks:
                raise KeyError(f"assumption {key!r} missing")
            b = self.blocks[key]
            if b.values is not None:
                if sub is None:
                    return b.values
                if sub not in b.values:
                    raise KeyError(f"assumption {key!r} has no entry {sub!r}")
                return b.values[sub]
            return b.value

        def owner(self, key: str) -> str:
            if key not in self.blocks:
                raise KeyError(f"assumption {key!r} missing")
            return self.blocks[key].owner

    def load_assumptions(path: Path = ROOT / "config" / "assumptions.yaml") -> Assumptions:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Assumptions(**raw)

    def load_generator_config(path: Path | None = None):  # pragma: no cover - stub only
        raise FileNotFoundError("generator.yaml not available in the test stub")

    mod.AssumptionBlock = AssumptionBlock
    mod.Assumptions = Assumptions
    mod.load_assumptions = load_assumptions
    mod.load_generator_config = load_generator_config
    mod.__stub__ = True
    return mod


def _stub_db() -> types.ModuleType:
    import duckdb

    mod = types.ModuleType("restwert.db")

    def connect(path: str = ":memory:", read_only: bool = False):
        return duckdb.connect(str(path), read_only=read_only)

    def create_schema(con, drop_derived: bool = False) -> None:
        con.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id VARCHAR PRIMARY KEY, command VARCHAR NOT NULL, "
            "started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, seed INTEGER, as_of DATE NOT NULL, "
            "thresholds_sha256 VARCHAR, counts_json VARCHAR)"
        )

    def table_exists(con, table: str) -> bool:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()
        return bool(row and row[0])

    def write_df(con, table: str, df: pd.DataFrame, mode: str = "replace") -> int:
        con.register("_stub_df", df)
        if not table_exists(con, table):
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM _stub_df")
        else:
            if mode == "replace":
                con.execute(f"DELETE FROM {table}")
            con.execute(f"INSERT INTO {table} SELECT * FROM _stub_df")
        con.unregister("_stub_df")
        return int(len(df))

    def append_rows(con, table: str, df: pd.DataFrame) -> int:
        return write_df(con, table, df, mode="append")

    def read_df(con, sql: str, params: list | None = None) -> pd.DataFrame:
        return con.execute(sql, params or []).df()

    def new_run(con, command: str, seed, as_of: date, thresholds_sha256) -> str:
        from datetime import datetime, timezone
        from uuid import uuid4

        create_schema(con)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        run_id = f"{command}-{now:%Y%m%d%H%M%S}-{uuid4().hex[:4]}"
        con.execute(
            "INSERT INTO runs (run_id, command, started_at, seed, as_of, thresholds_sha256) VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, command, now, seed, as_of, thresholds_sha256],
        )
        return run_id

    def finish_run(con, run_id: str, counts: dict[str, int]) -> None:
        import json
        from datetime import datetime, timezone

        con.execute(
            "UPDATE runs SET finished_at = ?, counts_json = ? WHERE run_id = ?",
            [datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(counts), run_id],
        )

    def is_synthetic(con) -> bool:  # pragma: no cover - stub only
        return True

    for fn in (connect, create_schema, table_exists, write_df, append_rows, read_df, new_run, finish_run, is_synthetic):
        setattr(mod, fn.__name__, fn)
    mod.__stub__ = True
    return mod


_STUBS = {"restwert.config": _stub_config, "restwert.db": _stub_db}


def ensure_foundation() -> dict[str, bool]:
    """Import the foundation; install spec-shaped stand-ins only for modules that do not exist.

    Returns ``{module_name: is_stub}`` so a test can report which path it exercised.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    importlib.import_module("restwert")
    used: dict[str, bool] = {}
    for name, maker in _STUBS.items():
        if name in sys.modules:
            used[name] = bool(getattr(sys.modules[name], "__stub__", False))
            continue
        try:
            importlib.import_module(name)
            used[name] = False
        except ModuleNotFoundError as exc:
            if exc.name != name:  # a dependency of the real module is missing: do not mask it
                raise
            mod = maker()
            sys.modules[name] = mod
            setattr(sys.modules["restwert"], name.rsplit(".", 1)[1], mod)
            used[name] = True
    return used


# ----------------------------------------------------------------------------------------
# source table column lists (DDL order, spec section 2.8)
# ----------------------------------------------------------------------------------------

DEVICE_COLS = [
    "serial", "model_family", "model", "storage_gb", "colour", "launch_date", "purchase_date",
    "purchase_price", "landed_cost", "supplier", "channel_in", "po_number", "contract_id",
    "is_synthetic", "source_file",
]
CONTRACT_COLS = [
    "contract_id", "serial", "customer_id", "start_date", "term_months", "monthly_rate",
    "end_date", "actual_end_date", "status", "replaces_contract_id", "is_synthetic", "source_file",
]
EVENT_COLS = [
    "event_id", "serial", "contract_id", "event_type", "event_date", "cost", "damage_type",
    "resolved", "replacement_serial", "return_date", "grade_pre_return", "grade_inspected",
    "wipe_certificate", "note", "is_synthetic", "source_file",
]
REFURB_COLS = [
    "refurb_id", "serial", "start_date", "end_date", "days", "cost", "grade_out", "outcome",
    "is_synthetic", "source_file",
]
RESALE_COLS = [
    "sale_id", "serial", "channel", "sale_date", "price", "fees", "buyer_type", "grade_at_sale",
    "is_synthetic", "source_file",
]
RV_CURRENT_COLS = [
    "serial", "as_of", "run_id", "model", "model_family", "grade_used", "grade_source",
    "months_since_launch", "n_launches_since", "forecast_rv_ratio", "forecast_rv",
    "forecast_rv_employee_buyout", "forecast_rv_b2b_wholesale", "forecast_rv_as_is",
    "forecast_rv_grade_b", "fit_quality", "n_train",
]
LEDGER_COLS = [
    "write_down_id", "serial", "as_of", "rule_id", "decision_id", "book_value_before", "amount",
    "book_value_after", "threshold_owner", "run_id",
]
GRID_COLS = [
    "run_id", "model", "model_family", "grade", "months_since_launch", "n_launches_since",
    "storage_gb", "forecast_rv_ratio", "ratio_low", "ratio_high", "list_price",
    "forecast_rv_on_list", "n_train", "fit_quality",
]


def _frame(cols: list[str], rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


# ----------------------------------------------------------------------------------------
# row builders
# ----------------------------------------------------------------------------------------

_counter = {"ev": 0, "rf": 0, "s": 0, "rc": 0, "wd": 0}


def _next(prefix: str) -> str:
    _counter[prefix] += 1
    return f"{prefix.upper()}-{_counter[prefix]:06d}"


def device(serial: str, family: str = "iphone_like", model: str = "P-Gen05", *, storage_gb: int = 128,
           launch_date: date = date(2023, 9, 15), purchase_date: date = date(2024, 1, 10),
           purchase_price: float = 772.95, landed_cost: float = 800.0, contract_id: str | None = None) -> dict:
    return {
        "serial": serial, "model_family": family, "model": model, "storage_gb": storage_gb, "colour": "black",
        "launch_date": launch_date, "purchase_date": purchase_date, "purchase_price": purchase_price,
        "landed_cost": landed_cost, "supplier": "Supplier-A", "channel_in": "distributor",
        "po_number": "PO-000001", "contract_id": contract_id, "is_synthetic": True, "source_file": "devices.csv",
    }


def contract(serial: str, start: date, term: int, rate: float, *, contract_id: str | None = None,
             actual_end: date | None = None, status: str = "ended", customer: str = "CUST-0001",
             replaces: str | None = None, end: date | None = None) -> dict:
    from restwert.dates import add_months

    return {
        "contract_id": contract_id or _next("rc"), "serial": serial, "customer_id": customer,
        "start_date": start, "term_months": term, "monthly_rate": rate,
        "end_date": end if end is not None else add_months(start, term), "actual_end_date": actual_end,
        "status": status, "replaces_contract_id": replaces, "is_synthetic": True, "source_file": "rental_contracts.csv",
    }


def event(serial: str, event_type: str, event_date: date, cost: float = 0.0, *, contract_id: str | None = None,
          damage_type: str | None = None, resolved: bool | None = None, replacement_serial: str | None = None,
          return_date: date | None = None, grade_pre_return: str | None = None, grade_inspected: str | None = None,
          wipe_certificate: bool | None = None) -> dict:
    return {
        "event_id": _next("ev"), "serial": serial, "contract_id": contract_id, "event_type": event_type,
        "event_date": event_date, "cost": cost, "damage_type": damage_type, "resolved": resolved,
        "replacement_serial": replacement_serial, "return_date": return_date,
        "grade_pre_return": grade_pre_return, "grade_inspected": grade_inspected,
        "wipe_certificate": wipe_certificate, "note": None, "is_synthetic": True, "source_file": "events.csv",
    }


def refurb(serial: str, start: date, end: date, cost: float, grade_out: str = "B", outcome: str = "sellable") -> dict:
    return {
        "refurb_id": _next("rf"), "serial": serial, "start_date": start, "end_date": end,
        "days": (end - start).days, "cost": cost, "grade_out": grade_out, "outcome": outcome,
        "is_synthetic": True, "source_file": "refurbishment.csv",
    }


def resale(serial: str, sale_date: date, price: float, fees: float, channel: str = "marketplace",
           buyer_type: str = "consumer", grade: str = "B") -> dict:
    return {
        "sale_id": _next("s"), "serial": serial, "channel": channel, "sale_date": sale_date, "price": price,
        "fees": fees, "buyer_type": buyer_type, "grade_at_sale": grade, "is_synthetic": True, "source_file": "resale.csv",
    }


def rv_current_row(serial: str, forecast_rv: float, *, as_of: date = AS_OF, model: str = "P-Gen05",
                   family: str = "iphone_like", grade: str = "B") -> dict:
    return {
        "serial": serial, "as_of": as_of, "run_id": f"rv-{as_of:%Y-%m-%d}", "model": model, "model_family": family,
        "grade_used": grade, "grade_source": "inspected", "months_since_launch": 33.0, "n_launches_since": 2,
        "forecast_rv_ratio": round(forecast_rv / 772.95, 4), "forecast_rv": forecast_rv,
        "forecast_rv_employee_buyout": forecast_rv * 0.95, "forecast_rv_b2b_wholesale": forecast_rv * 0.85,
        "forecast_rv_as_is": forecast_rv * 0.4, "forecast_rv_grade_b": forecast_rv,
        "fit_quality": "family", "n_train": 500,
    }


def ledger_row(serial: str, as_of: date, amount: float, before: float = 300.0) -> dict:
    return {
        "write_down_id": _next("wd"), "serial": serial, "as_of": as_of, "rule_id": "R03",
        "decision_id": f"dec-{_counter['wd']:04d}", "book_value_before": before, "amount": amount,
        "book_value_after": before - amount, "threshold_owner": "CFO (name)", "run_id": f"decide-{as_of:%Y%m%d}",
    }


def grid_rows(model: str, family: str, grade: str, months: range, ratio_at: Any) -> list[dict]:
    return [
        {
            "run_id": "rv-2026-06-30", "model": model, "model_family": family, "grade": grade,
            "months_since_launch": m, "n_launches_since": m // 12, "storage_gb": 128,
            "forecast_rv_ratio": ratio_at(m), "ratio_low": ratio_at(m) * 0.9, "ratio_high": ratio_at(m) * 1.1,
            "list_price": 999.0, "forecast_rv_on_list": ratio_at(m) * 999.0, "n_train": 500, "fit_quality": "family",
        }
        for m in months
    ]


# ----------------------------------------------------------------------------------------
# the cases from spec 4.1 and 4.6
# ----------------------------------------------------------------------------------------

def empty_frames() -> dict[str, pd.DataFrame]:
    return {
        "devices": _frame(DEVICE_COLS, []),
        "rental_contracts": _frame(CONTRACT_COLS, []),
        "events": _frame(EVENT_COLS, []),
        "refurbishment": _frame(REFURB_COLS, []),
        "resale": _frame(RESALE_COLS, []),
    }


def hand_case_1() -> dict[str, pd.DataFrame]:
    """rate 30 x 24 = 720; landed 800; resale 250 gross, fees 25; repair 60; refurb 40; return 15 -> margin 30."""
    d = "D-000001"
    return {
        "devices": _frame(DEVICE_COLS, [device(d, landed_cost=800.0, purchase_price=772.95, contract_id="RC-000001")]),
        "rental_contracts": _frame(CONTRACT_COLS, [contract(d, date(2024, 2, 1), 24, 30.0, contract_id="RC-000001")]),
        "events": _frame(EVENT_COLS, [
            event(d, "damage", date(2024, 8, 1), 0.0, damage_type="screen", resolved=True),
            event(d, "repair", date(2024, 8, 5), 60.0),
            event(d, "return", date(2026, 2, 3), 15.0, return_date=date(2026, 2, 5),
                  grade_pre_return="B", grade_inspected="B", wipe_certificate=True),
        ]),
        "refurbishment": _frame(REFURB_COLS, [refurb(d, date(2026, 2, 8), date(2026, 2, 15), 40.0, "B", "sellable")]),
        "resale": _frame(RESALE_COLS, [resale(d, date(2026, 3, 1), 250.0, 25.0, "marketplace")]),
    }


def hand_case_2() -> dict[str, pd.DataFrame]:
    """rate 25 x 24 = 600; landed 820; resale 310; repair 45; refurb 30; logistics 12; fees 31 -> margin -28."""
    d = "D-000002"
    return {
        "devices": _frame(DEVICE_COLS, [device(d, landed_cost=820.0, purchase_price=792.27, contract_id="RC-000002")]),
        "rental_contracts": _frame(CONTRACT_COLS, [contract(d, date(2024, 3, 1), 24, 25.0, contract_id="RC-000002")]),
        "events": _frame(EVENT_COLS, [
            event(d, "damage", date(2025, 1, 10), 0.0, damage_type="battery", resolved=True),
            event(d, "repair", date(2025, 1, 15), 45.0),
            event(d, "return", date(2026, 3, 3), 12.0, return_date=date(2026, 3, 5),
                  grade_pre_return="A", grade_inspected="B", wipe_certificate=True),
        ]),
        "refurbishment": _frame(REFURB_COLS, [refurb(d, date(2026, 3, 8), date(2026, 3, 14), 30.0, "B", "sellable")]),
        "resale": _frame(RESALE_COLS, [resale(d, date(2026, 4, 2), 310.0, 31.0, "marketplace")]),
    }


def replacement_case() -> dict[str, pd.DataFrame]:
    """Device D-000010 rented 10 months at 20 then replaced by spare D-000011 for the remaining 14 months."""
    d1, d2 = "D-000010", "D-000011"
    start = date(2024, 3, 1)
    replaced_on = date(2025, 1, 1)
    return {
        "devices": _frame(DEVICE_COLS, [
            device(d1, landed_cost=800.0, contract_id="RC-000010"),
            device(d2, landed_cost=800.0, contract_id="RC-000011", purchase_date=date(2024, 1, 20)),
        ]),
        "rental_contracts": _frame(CONTRACT_COLS, [
            contract(d1, start, 24, 20.0, contract_id="RC-000010", actual_end=replaced_on, status="replaced"),
            contract(d2, replaced_on, 14, 20.0, contract_id="RC-000011", status="ended",
                     replaces="RC-000010", end=date(2026, 3, 1)),
        ]),
        "events": _frame(EVENT_COLS, [
            event(d1, "damage", date(2024, 12, 28), 0.0, damage_type="water", resolved=True, contract_id="RC-000010"),
            event(d1, "replacement", replaced_on, 18.0, replacement_serial=d2, contract_id="RC-000010"),
            event(d1, "return", replaced_on, 9.5, return_date=date(2025, 1, 8), grade_pre_return="C",
                  grade_inspected="C", wipe_certificate=True, contract_id="RC-000010"),
            event(d2, "return", date(2026, 3, 3), 9.5, return_date=date(2026, 3, 6), grade_pre_return="B",
                  grade_inspected="B", wipe_certificate=True, contract_id="RC-000011"),
        ]),
        "refurbishment": _frame(REFURB_COLS, []),
        "resale": _frame(RESALE_COLS, []),
    }


def open_device_case() -> dict[str, pd.DataFrame]:
    """One in-stock device (returned, refurbished, unsold) and one still rented; used for forecast and book value."""
    d_stock, d_rented = "D-000020", "D-000021"
    return {
        "devices": _frame(DEVICE_COLS, [
            device(d_stock, landed_cost=800.0, purchase_date=date(2024, 1, 10), contract_id="RC-000020"),
            device(d_rented, landed_cost=800.0, purchase_date=date(2024, 12, 30), contract_id="RC-000021"),
        ]),
        "rental_contracts": _frame(CONTRACT_COLS, [
            contract(d_stock, date(2024, 2, 1), 24, 30.0, contract_id="RC-000020"),
            contract(d_rented, date(2025, 1, 15), 36, 30.0, contract_id="RC-000021", status="active"),
        ]),
        "events": _frame(EVENT_COLS, [
            event(d_stock, "repair", date(2024, 8, 5), 60.0),
            event(d_stock, "return", date(2026, 2, 3), 15.0, return_date=date(2026, 2, 5),
                  grade_pre_return="A", grade_inspected="B", wipe_certificate=True),
            # open quote dated before as_of must not count as cost
            event(d_rented, "damage", date(2026, 6, 20), 89.0, damage_type="screen", resolved=False),
        ]),
        "refurbishment": _frame(REFURB_COLS, [refurb(d_stock, date(2026, 2, 8), date(2026, 3, 1), 40.0, "B", "sellable")]),
        "resale": _frame(RESALE_COLS, []),
    }


def tco_fixture() -> dict[str, pd.DataFrame]:
    """Two models. P-Gen01 (iphone_like, 2 devices, avg landed 850 / purchase 820), A-Gen01 (android_like, 1 device).

    Purchase dates equal launch dates so months since launch at purchase is 0 and the hand
    arithmetic stays clean. The android grid stops at month 30 to test clipping.
    """
    launch_p = date(2024, 9, 15)
    launch_a = date(2024, 2, 15)
    devices = _frame(DEVICE_COLS, [
        device("D-000101", "iphone_like", "P-Gen01", launch_date=launch_p, purchase_date=launch_p,
               purchase_price=770.0, landed_cost=800.0, contract_id="RC-000101"),
        device("D-000102", "iphone_like", "P-Gen01", launch_date=launch_p, purchase_date=launch_p,
               purchase_price=870.0, landed_cost=900.0, contract_id="RC-000102"),
        device("D-000103", "android_like", "A-Gen01", launch_date=launch_a, purchase_date=launch_a,
               purchase_price=480.0, landed_cost=500.0, contract_id="RC-000103"),
    ])
    contracts = _frame(CONTRACT_COLS, [
        contract("D-000101", launch_p, 24, 34.0, contract_id="RC-000101", status="active"),
        contract("D-000102", launch_p, 36, 40.0, contract_id="RC-000102", status="active"),
        contract("D-000103", launch_a, 24, 22.0, contract_id="RC-000103", status="active"),
    ])
    grid = pd.DataFrame(
        grid_rows("P-Gen01", "iphone_like", "B", range(0, 49), lambda m: round(0.9 - 0.01 * m, 4))
        + grid_rows("A-Gen01", "android_like", "B", range(0, 31), lambda m: round(0.8 - 0.01 * m, 4)),
        columns=GRID_COLS,
    )
    return {
        "devices": devices,
        "rental_contracts": contracts,
        "events": _frame(EVENT_COLS, []),
        "refurbishment": _frame(REFURB_COLS, []),
        "resale": _frame(RESALE_COLS, []),
        "rv_grid": grid,
    }


def tco_realised_events(n: int = 30, cost: float = 100.0) -> pd.DataFrame:
    """``n`` damage + ``n`` repair events on the P-Gen01 devices, each repair costing ``cost``."""
    rows = []
    for i in range(n):
        serial = "D-000101" if i % 2 == 0 else "D-000102"
        day = date(2025, 1, 1) + pd.Timedelta(days=7 * i).to_pytimedelta()
        rows.append(event(serial, "damage", day, 0.0, damage_type="screen", resolved=True))
        rows.append(event(serial, "repair", day + pd.Timedelta(days=3).to_pytimedelta(), cost))
    return _frame(EVENT_COLS, rows)


def load_test_assumptions():
    """The shipped ``config/assumptions.yaml`` through the (real or stub) loader."""
    ensure_foundation()
    from restwert.config import load_assumptions

    return load_assumptions(ROOT / "config" / "assumptions.yaml")
