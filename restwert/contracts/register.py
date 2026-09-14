"""Contracts register, renewal calendar and contract coverage (SPEC 7.4).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Pure functions on pandas frames plus one runner that reads and writes DuckDB.
This module deliberately imports nothing from ``restwert.kpi`` (the KPI
package imports ``contract_coverage`` from here, so the dependency must point
one way only).

Choices where the spec is silent (all documented here):

- Register ``status`` is derived the same way for both contract types:
  ``expired`` when ``end_date < as_of``, else ``active``. For rental rows
  ``end_date`` is the effective end ``coalesce(actual_end_date, end_date)``.
- Rental contracts carry no auto-renewal field in the source table, so
  ``auto_renewal`` is ``False`` for every rental row.
- ``renewal_calendar`` keeps a row when ``as_of <= end_date <= horizon_end``
  OR ``as_of <= notice_deadline <= horizon_end`` with
  ``horizon_end = add_months(as_of, horizon_months)``; both bounds inclusive.
  ``action_required = notice_deadline <= add_months(as_of, 2) or auto_renewal``.
- ``contract_coverage`` windows hardware spend by ``delivered_date`` and
  indirect spend by ``invoice_date``, trailing 12 months, both inclusive.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.config import Thresholds
from restwert.dates import add_months
from restwert.records import KpiValue, RunSummary

REGISTER_COLUMNS: tuple[str, ...] = (
    "contract_type",
    "contract_id",
    "counterparty",
    "category",
    "start_date",
    "end_date",
    "notice_days",
    "notice_deadline",
    "auto_renewal",
    "price_protection",
    "price_protection_days",
    "payment_terms_days",
    "annual_value",
    "status",
)

CALENDAR_COLUMNS: tuple[str, ...] = (
    "contract_type",
    "contract_id",
    "counterparty",
    "as_of",
    "end_date",
    "notice_days",
    "notice_deadline",
    "days_to_notice_deadline",
    "days_to_end",
    "auto_renewal",
    "annual_value",
    "action_required",
    "month_bucket",
)

BREAKDOWN_COLUMNS: tuple[str, ...] = ("dimension", "dimension_value", "value", "numerator", "denominator", "n")


# ---------------------------------------------------------------------------
# local coercion helpers (kept local on purpose, see module docstring)
# ---------------------------------------------------------------------------


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.map(lambda v: float(v) if isinstance(v, Decimal) else v), errors="coerce").astype("float64")


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _bool(series: pd.Series) -> pd.Series:
    return series.map(lambda v: bool(v) if v is not None and v == v else False).astype(bool)


def _dates_to_objects(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """datetime64 columns to python ``date`` objects (NaT -> None) for DuckDB DATE columns."""

    for col in columns:
        if col in frame.columns:
            series = pd.to_datetime(frame[col], errors="coerce")
            frame[col] = series.map(lambda v: v.date() if pd.notna(v) else None).astype(object)
    return frame


def _empty(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in columns})


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def contracts_register(
    supplier_contracts: pd.DataFrame,
    rental_contracts: pd.DataFrame,
    as_of: date,
    rental_notice_days: int,
) -> pd.DataFrame:
    """Union supplier and rental contracts into the ``contracts_register`` shape.

    Supplier rows: ``contract_type='supplier'``, counterparty = supplier,
    annual_value = spend_under_contract, notice_deadline = end_date - notice_days.
    Rental rows: ``contract_type='rental'``, counterparty = customer_id,
    category 'rental', notice_days = ``rental_notice_days``,
    annual_value = monthly_rate * 12, end_date = coalesce(actual_end_date, end_date),
    price_protection / price_protection_days / payment_terms_days NULL.
    """

    parts: list[pd.DataFrame] = []

    if supplier_contracts is not None and not supplier_contracts.empty:
        s = supplier_contracts.copy()
        s_end = _dt(s["end_date"])
        s_notice = _num(s["notice_days"]).fillna(0).astype(int)
        sup = pd.DataFrame(
            {
                "contract_type": "supplier",
                "contract_id": s["supplier_contract_id"].astype(str),
                "counterparty": s["supplier"].astype(str),
                "category": s["category"].astype(object) if "category" in s.columns else None,
                "start_date": _dt(s["start_date"]),
                "end_date": s_end,
                "notice_days": s_notice,
                "notice_deadline": s_end - pd.to_timedelta(s_notice, unit="D"),
                "auto_renewal": _bool(s["auto_renewal"]) if "auto_renewal" in s.columns else False,
                "price_protection": s["price_protection"].astype(object) if "price_protection" in s.columns else None,
                "price_protection_days": s["price_protection_days"].astype(object) if "price_protection_days" in s.columns else None,
                "payment_terms_days": s["payment_terms_days"].astype(object) if "payment_terms_days" in s.columns else None,
                "annual_value": _num(s["spend_under_contract"]),
            }
        )
        parts.append(sup)

    if rental_contracts is not None and not rental_contracts.empty:
        r = rental_contracts.copy()
        planned_end = _dt(r["end_date"])
        actual_end = _dt(r["actual_end_date"]) if "actual_end_date" in r.columns else pd.Series(pd.NaT, index=r.index)
        r_end = actual_end.where(actual_end.notna(), planned_end)
        notice = int(rental_notice_days)
        ren = pd.DataFrame(
            {
                "contract_type": "rental",
                "contract_id": r["contract_id"].astype(str),
                "counterparty": r["customer_id"].astype(str),
                "category": "rental",
                "start_date": _dt(r["start_date"]),
                "end_date": r_end,
                "notice_days": notice,
                "notice_deadline": r_end - pd.Timedelta(days=notice),
                "auto_renewal": False,
                "price_protection": None,
                "price_protection_days": None,
                "payment_terms_days": None,
                "annual_value": _num(r["monthly_rate"]) * 12.0,
            }
        )
        parts.append(ren)

    if not parts:
        return _empty(REGISTER_COLUMNS)

    reg = pd.concat(parts, ignore_index=True)
    reg["status"] = np.where(reg["end_date"] < pd.Timestamp(as_of), "expired", "active")
    reg["annual_value"] = reg["annual_value"].round(2)
    reg = reg[list(REGISTER_COLUMNS)].sort_values(["contract_type", "contract_id"]).reset_index(drop=True)
    return reg


def renewal_calendar(register: pd.DataFrame, as_of: date, horizon_months: int = 6) -> pd.DataFrame:
    """Contracts that end or reach their notice deadline within the horizon.

    Boundaries are inclusive on both ends: ``end_date == add_months(as_of,
    horizon_months)`` is in, one day later is out.
    """

    if register is None or register.empty:
        return _empty(CALENDAR_COLUMNS)
    reg = register.copy()
    reg["end_date"] = _dt(reg["end_date"])
    reg["notice_deadline"] = _dt(reg["notice_deadline"])
    lo = pd.Timestamp(as_of)
    hi = pd.Timestamp(add_months(as_of, horizon_months))
    in_end = reg["end_date"].notna() & (reg["end_date"] >= lo) & (reg["end_date"] <= hi)
    in_notice = reg["notice_deadline"].notna() & (reg["notice_deadline"] >= lo) & (reg["notice_deadline"] <= hi)
    sel = reg[in_end | in_notice].copy()
    if sel.empty:
        return _empty(CALENDAR_COLUMNS)
    action_cutoff = pd.Timestamp(add_months(as_of, 2))
    auto = _bool(sel["auto_renewal"]) if "auto_renewal" in sel.columns else pd.Series(False, index=sel.index)
    cal = pd.DataFrame(
        {
            "contract_type": sel["contract_type"].astype(str),
            "contract_id": sel["contract_id"].astype(str),
            "counterparty": sel["counterparty"].astype(object),
            "as_of": pd.Timestamp(as_of),
            "end_date": sel["end_date"],
            "notice_days": sel["notice_days"].astype(object),
            "notice_deadline": sel["notice_deadline"],
            "days_to_notice_deadline": (sel["notice_deadline"] - lo).dt.days.astype("Int64"),
            "days_to_end": (sel["end_date"] - lo).dt.days.astype("Int64"),
            "auto_renewal": auto,
            "annual_value": _num(sel["annual_value"]) if "annual_value" in sel.columns else np.nan,
            "action_required": ((sel["notice_deadline"] <= action_cutoff) & sel["notice_deadline"].notna()) | auto,
            "month_bucket": sel["end_date"].dt.strftime("%Y-%m"),
        }
    )
    cal = cal[list(CALENDAR_COLUMNS)].sort_values(["end_date", "contract_type", "contract_id"]).reset_index(drop=True)
    return cal


# ---------------------------------------------------------------------------
# coverage (single definition shared with KPI_PROC_SPEND_UNDER_CONTRACT)
# ---------------------------------------------------------------------------


def contract_coverage(purchase_orders: pd.DataFrame | None, indirect_spend: pd.DataFrame | None, as_of: date) -> KpiValue:
    """Share of spend under contract, trailing 12 months.

    numerator = sum(po.qty_delivered * po.unit_price where supplier_contract_id is not null)
                + sum(indirect_spend.amount where has_contract)
    denominator = sum(all PO value with delivered_date not null) + sum(all indirect amount)
    Hardware windowed by delivered_date, indirect by invoice_date. Breakdown by
    ``spend_type`` in (hardware, indirect). Not measurable on an empty denominator.
    """

    start = add_months(as_of, -12) + timedelta(days=1)
    lo, hi = pd.Timestamp(start), pd.Timestamp(as_of)

    hw_contracted = 0.0
    hw_total = 0.0
    hw_n = 0
    notes: list[str] = []
    if purchase_orders is not None and not purchase_orders.empty:
        need = ("qty_delivered", "unit_price", "supplier_contract_id", "delivered_date")
        missing = [c for c in need if c not in purchase_orders.columns]
        if missing:
            notes.append(f"purchase_orders lacks {', '.join(missing)}; hardware spend ignored")
        else:
            po = purchase_orders.copy()
            po["delivered_date"] = _dt(po["delivered_date"])
            po["value"] = _num(po["qty_delivered"]) * _num(po["unit_price"])
            in_win = po["delivered_date"].notna() & (po["delivered_date"] >= lo) & (po["delivered_date"] <= hi)
            po = po[in_win & po["value"].notna()]
            hw_total = float(po["value"].sum())
            hw_contracted = float(po.loc[po["supplier_contract_id"].notna(), "value"].sum())
            hw_n = int(len(po))
    else:
        notes.append("no purchase orders")

    ind_contracted = 0.0
    ind_total = 0.0
    ind_n = 0
    if indirect_spend is not None and not indirect_spend.empty:
        need = ("amount", "has_contract", "invoice_date")
        missing = [c for c in need if c not in indirect_spend.columns]
        if missing:
            notes.append(f"indirect_spend lacks {', '.join(missing)}; indirect spend ignored")
        else:
            ind = indirect_spend.copy()
            ind["invoice_date"] = _dt(ind["invoice_date"])
            ind["amount"] = _num(ind["amount"])
            in_win = ind["invoice_date"].notna() & (ind["invoice_date"] >= lo) & (ind["invoice_date"] <= hi)
            ind = ind[in_win & ind["amount"].notna()]
            ind_total = float(ind["amount"].sum())
            ind_contracted = float(ind.loc[_bool(ind["has_contract"]), "amount"].sum())
            ind_n = int(len(ind))
    else:
        notes.append("no indirect spend")

    numerator = hw_contracted + ind_contracted
    denominator = hw_total + ind_total
    n = hw_n + ind_n
    if denominator == 0 or not np.isfinite(denominator):
        return KpiValue.not_measurable(
            "no delivered PO value and no indirect spend between "
            f"{start.isoformat()} and {as_of.isoformat()}" + (f" ({'; '.join(notes)})" if notes else "")
        )
    rows = [
        {"dimension": "spend_type", "dimension_value": "hardware", "value": (hw_contracted / hw_total) if hw_total else None,
         "numerator": hw_contracted, "denominator": hw_total, "n": hw_n},
        {"dimension": "spend_type", "dimension_value": "indirect", "value": (ind_contracted / ind_total) if ind_total else None,
         "numerator": ind_contracted, "denominator": ind_total, "n": ind_n},
    ]
    breakdown = pd.DataFrame.from_records(rows, columns=list(BREAKDOWN_COLUMNS))
    note = (
        f"hardware {hw_contracted:,.2f} of {hw_total:,.2f} EUR under contract; indirect {ind_contracted:,.2f} of "
        f"{ind_total:,.2f} EUR; window {start.isoformat()} to {as_of.isoformat()}"
    )
    if notes:
        note += "; " + "; ".join(notes)
    return KpiValue(
        value=numerator / denominator,
        numerator=numerator,
        denominator=denominator,
        n=n,
        status="ok",
        note=note,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _read_optional(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    if not db.table_exists(con, table):
        return pd.DataFrame()
    return db.read_df(con, f"SELECT * FROM {table}")


def run_contracts(con: duckdb.DuckDBPyConnection, as_of: date, thr: Thresholds) -> RunSummary:
    """Read supplier and rental contracts, write ``contracts_register`` and ``renewal_calendar``.

    ``rental_notice_days`` comes from ``thresholds.yaml`` (owner recorded there).
    Both tables are rebuilt (``write_df`` mode replace). No side effect beyond
    the two tables and the runs row.
    """

    started = datetime.now(timezone.utc)
    run_id = db.new_run(con, "contracts", None, as_of, None)
    notice = thr.get("rental_notice_days", as_of=as_of)
    rental_notice_days = int(notice.value)

    supplier_contracts = _read_optional(con, "supplier_contracts")
    rental_contracts = _read_optional(con, "rental_contracts")

    register = contracts_register(supplier_contracts, rental_contracts, as_of, rental_notice_days)
    calendar = renewal_calendar(register, as_of, horizon_months=6)

    reg_out = _dates_to_objects(register.copy(), ("start_date", "end_date", "notice_deadline"))
    cal_out = _dates_to_objects(calendar.copy(), ("as_of", "end_date", "notice_deadline"))
    for col in ("days_to_notice_deadline", "days_to_end"):
        if col in cal_out.columns:
            cal_out[col] = cal_out[col].astype(object).where(cal_out[col].notna(), None)

    n_reg = db.write_df(con, "contracts_register", reg_out, mode="replace")
    n_cal = db.write_df(con, "renewal_calendar", cal_out, mode="replace")

    counts = {
        "contracts_register": int(n_reg),
        "renewal_calendar": int(n_cal),
        "supplier_contracts": int(len(supplier_contracts)),
        "rental_contracts": int(len(rental_contracts)),
        "action_required": int(calendar["action_required"].sum()) if not calendar.empty else 0,
    }
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="contracts",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=(finished - started).total_seconds(),
        counts=counts,
        notes=[f"rental_notice_days={rental_notice_days} (owner: {notice.owner})"],
    )
