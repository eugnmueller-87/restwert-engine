"""Device lifecycle status, billing, lifecycle margin and the ``device_pnl`` table (spec 4.1).

Pure functions first (``derive_status``, ``months_billed``, ``lifecycle_margin``,
``build_device_pnl``), then ``run_pnl`` which is the only function touching DuckDB.

Formulas (spec section 9)::

    months_billed         = months_between(start_date, min(coalesce(actual_end_date, end_date), as_of)), floor 0, full months
    rental_revenue        = sum_contracts(monthly_rate * months_billed)
    service_and_logistics = repair + replacement_logistics + return_logistics + refurb + channel_fees
    lifecycle_margin      = rental_revenue - (landed_cost - realised_rv) - service_and_logistics   [closed only]
    margin_if_liquidated_today = rental_revenue - (landed_cost - forecast_rv) - service_and_logistics
                                 - forecast_rv * marketplace fee_pct                                 [open only]
    book_value            = max(book_value_sl - write_down_cum(before as_of), 0)                     [open only; 0 when closed]

Choices where the spec is silent (kept simple and stated here):

* Event costs count when ``event_date <= as_of``; a WIP refurbishment counts only once its
  ``end_date <= as_of``. Open damage quotes (``resolved = false``) are never cost.
* With more than one return event per serial, the latest ``return_date`` wins; with more
  than one refurbishment the latest ``end_date`` wins; with more than one resale the earliest
  ``sale_date`` wins (the generator writes at most one of each per serial).
* Resale columns (channel, sale date, gross price, fees) are filled only when the device is
  ``sold`` at ``as_of``; a sale dated after ``as_of`` is not yet a sale.
* ``margin_if_liquidated_today`` (the spec calls it ``margin_open_forecast``) is what the
  name says: revenue billed so far, cost to date and the residual value the device would
  fetch at ``as_of``, less the marketplace percentage fee on it (the fixed fee is ignored).
  It is NOT a lifecycle forecast: remaining contracted rent and the residual value at the
  end of the contract are not in it, so a device early in its rental shows a negative
  number by construction. The column was renamed so nobody reads it as a forecast.
* Closed lifecycles (sold, scrapped) carry ``book_value_sl = write_down_cum = book_value = 0``:
  the asset is derecognised at close, so a sum of ``book_value`` over the export is the
  balance of the open fleet and nothing else.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from restwert.dates import months_between, months_between_float, quarter_label
from restwert.pnl.book_value import planned_rv, straight_line_book_value

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

    from restwert.config import Assumptions
    from restwert.records import RunSummary


CLOSED_STATUSES: frozenset[str] = frozenset({"sold", "scrapped"})

# Every column of the device_pnl DDL, in DDL order (spec section 2.8).
DEVICE_PNL_COLUMNS: list[str] = [
    "serial", "as_of", "model_family", "model", "storage_gb",
    "purchase_date", "cohort", "launch_date", "months_since_launch",
    "landed_cost", "purchase_price",
    "months_billed", "rental_revenue",
    "return_date", "grade_inspected", "sellable_date", "refurb_outcome", "grade_current",
    "resale_channel", "sale_date", "resale_price_gross", "resale_fees",
    "realised_rv", "forecast_rv",
    "repair_cost", "replacement_logistics_cost", "return_logistics_cost",
    "refurb_cost", "channel_fees", "service_and_logistics_cost",
    "lifecycle_margin", "lifecycle_margin_pct_of_landed", "margin_if_liquidated_today",
    "lifecycle_status", "is_closed", "closed_date",
    "days_in_stock", "days_in_wip",
    "book_value_sl", "write_down_cum", "book_value",
]


# --------------------------------------------------------------------------------------
# small coercion helpers (DuckDB and CSV frames arrive with mixed dtypes)
# --------------------------------------------------------------------------------------

def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _to_date(v: Any) -> date | None:
    """Coerce a scalar (date, datetime, Timestamp, numpy datetime64, ISO string) to ``date``."""
    if _is_missing(v):
        return None
    if isinstance(v, datetime):  # pd.Timestamp is a datetime subclass
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, np.datetime64):
        return pd.Timestamp(v).date()
    if isinstance(v, str):
        return date.fromisoformat(v.strip()[:10])
    raise TypeError(f"cannot interpret {v!r} as a date")


def _to_float(v: Any, default: float | None = None) -> float | None:
    if _is_missing(v):
        return default
    return float(v)


def _to_int(v: Any) -> int | None:
    if _is_missing(v):
        return None
    return int(v)


def _to_str(v: Any) -> str | None:
    if _is_missing(v):
        return None
    return str(v)


def _to_bool(v: Any) -> bool | None:
    if _is_missing(v):
        return None
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "t"}
    return bool(v)


def _date_col(df: pd.DataFrame, col: str) -> list[date | None]:
    if col not in df.columns:
        return [None] * len(df)
    return [_to_date(v) for v in df[col].tolist()]


def _float_col(df: pd.DataFrame, col: str, default: float = 0.0) -> list[float]:
    if col not in df.columns:
        return [default] * len(df)
    return [float(_to_float(v, default)) for v in df[col].tolist()]


def _r2(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


# --------------------------------------------------------------------------------------
# pure functions
# --------------------------------------------------------------------------------------

def derive_status(
    *,
    as_of: date,
    has_sale: bool,
    sale_date: date | None,
    refurb_outcome: str | None,
    refurb_end: date | None,
    return_date: date | None,
    contract_start: date | None,
    contract_end_effective: date | None,
) -> str:
    """Deterministic lifecycle status at ``as_of``; first matching rule wins.

    1. ``sold``: has_sale and sale_date <= as_of
    2. ``scrapped``: refurb_outcome == 'scrap' and refurb_end <= as_of
    3. ``in_stock``: refurb_end <= as_of (outcome sellable or as_is)
    4. ``wip``: return_date <= as_of
    5. ``rented``: contract_start <= as_of < contract_end_effective
    6. ``awaiting_return``: contract_end_effective <= as_of
    7. ``not_deployed`` otherwise

    ``contract_end_effective`` is ``coalesce(actual_end_date, end_date)`` of the LATEST
    contract of the serial by ``start_date``. Boundaries: a sale on ``as_of`` is sold, a
    refurbishment ending on ``as_of`` is in stock, a contract ending on ``as_of`` is
    awaiting return.
    """
    if has_sale and sale_date is not None and sale_date <= as_of:
        return "sold"
    if refurb_outcome == "scrap" and refurb_end is not None and refurb_end <= as_of:
        return "scrapped"
    if refurb_end is not None and refurb_end <= as_of:
        return "in_stock"
    if return_date is not None and return_date <= as_of:
        return "wip"
    if (
        contract_start is not None
        and contract_end_effective is not None
        and contract_start <= as_of < contract_end_effective
    ):
        return "rented"
    if contract_end_effective is not None and contract_end_effective <= as_of:
        return "awaiting_return"
    return "not_deployed"


def months_billed(
    start_date: date,
    end_date: date,
    actual_end_date: date | None,
    as_of: date,
    *,
    pro_rata: bool = False,
) -> int:
    """Full months billed on one contract up to ``as_of``.

    ``months_between(start_date, min(coalesce(actual_end_date, end_date), as_of))``, floored
    at 0. v0.1 bills full months only (``assumptions.billing.pro_rata`` is read by
    ``build_device_pnl`` and passed here); ``pro_rata=True`` raises ``NotImplementedError``.
    """
    if pro_rata:
        raise NotImplementedError("pro rata billing is not implemented in v0.1 (assumptions.billing.pro_rata)")
    effective_end = actual_end_date if actual_end_date is not None else end_date
    stop = min(effective_end, as_of)
    if stop <= start_date:
        return 0
    return max(months_between(start_date, stop), 0)


def lifecycle_margin(
    rental_revenue: float,
    landed_cost: float,
    realised_rv: float,
    service_and_logistics_cost: float,
) -> float:
    """``rental_revenue - (landed_cost - realised_rv) - service_and_logistics_cost``.

    ``realised_rv`` is the GROSS resale price; channel fees sit inside
    ``service_and_logistics_cost`` and are therefore counted exactly once.
    """
    return float(rental_revenue) - (float(landed_cost) - float(realised_rv)) - float(service_and_logistics_cost)


# --------------------------------------------------------------------------------------
# per-table summaries used by build_device_pnl
# --------------------------------------------------------------------------------------

def _contract_summary(contracts: pd.DataFrame | None, as_of: date, pro_rata: bool) -> dict[str, dict[str, Any]]:
    """Per serial: months_billed, rental_revenue, latest contract start and effective end."""
    out: dict[str, dict[str, Any]] = {}
    if contracts is None or len(contracts) == 0:
        return out
    serials = contracts["serial"].astype(str).tolist()
    starts = _date_col(contracts, "start_date")
    ends = _date_col(contracts, "end_date")
    actual_ends = _date_col(contracts, "actual_end_date")
    rates = _float_col(contracts, "monthly_rate")
    for serial, s, e, ae, rate in zip(serials, starts, ends, actual_ends, rates):
        if s is None or e is None:
            continue
        m = months_billed(s, e, ae, as_of, pro_rata=pro_rata)
        rec = out.setdefault(
            serial,
            {"months_billed": 0, "rental_revenue": 0.0, "contract_start": None, "contract_end_effective": None,
             "_latest_start": None},
        )
        rec["months_billed"] += m
        rec["rental_revenue"] += rate * m
        if rec["_latest_start"] is None or s >= rec["_latest_start"]:
            rec["_latest_start"] = s
            rec["contract_start"] = s
            rec["contract_end_effective"] = ae if ae is not None else e
    return out


def _event_summary(events: pd.DataFrame | None, as_of: date) -> dict[str, dict[str, Any]]:
    """Per serial: cost buckets (event_date <= as_of) and the latest return event."""
    out: dict[str, dict[str, Any]] = {}
    if events is None or len(events) == 0:
        return out
    serials = events["serial"].astype(str).tolist()
    types = [(_to_str(t) or "").lower() for t in events["event_type"].tolist()]
    event_dates = _date_col(events, "event_date")
    costs = _float_col(events, "cost")
    return_dates = _date_col(events, "return_date")
    g_pre = [_to_str(v) for v in events["grade_pre_return"].tolist()] if "grade_pre_return" in events.columns else [None] * len(events)
    g_insp = [_to_str(v) for v in events["grade_inspected"].tolist()] if "grade_inspected" in events.columns else [None] * len(events)
    wipes = [_to_bool(v) for v in events["wipe_certificate"].tolist()] if "wipe_certificate" in events.columns else [None] * len(events)
    for i, serial in enumerate(serials):
        ed = event_dates[i]
        if ed is not None and ed > as_of:
            continue
        rec = out.setdefault(
            serial,
            {"repair_cost": 0.0, "replacement_logistics_cost": 0.0, "return_logistics_cost": 0.0,
             "return_date": None, "grade_pre_return": None, "grade_inspected": None, "wipe_certificate": None},
        )
        t = types[i]
        if t == "repair":
            rec["repair_cost"] += costs[i]
        elif t == "replacement":
            rec["replacement_logistics_cost"] += costs[i]
        elif t == "return":
            rec["return_logistics_cost"] += costs[i]
            rd = return_dates[i]
            if rd is not None and (rec["return_date"] is None or rd >= rec["return_date"]):
                rec["return_date"] = rd
                rec["grade_pre_return"] = g_pre[i]
                rec["grade_inspected"] = g_insp[i]
                rec["wipe_certificate"] = wipes[i]
        # damage rows: quotes are never cost, resolved damages carry cost 0 by convention
    return out


def _refurb_summary(refurb: pd.DataFrame | None, as_of: date) -> dict[str, dict[str, Any]]:
    """Per serial: latest refurbishment (by end_date) and finished refurbishment cost."""
    out: dict[str, dict[str, Any]] = {}
    if refurb is None or len(refurb) == 0:
        return out
    serials = refurb["serial"].astype(str).tolist()
    ends = _date_col(refurb, "end_date")
    costs = _float_col(refurb, "cost")
    outcomes = [(_to_str(v) or "").lower() or None for v in refurb["outcome"].tolist()]
    grades = [_to_str(v) for v in refurb["grade_out"].tolist()] if "grade_out" in refurb.columns else [None] * len(refurb)
    for serial, e, c, o, g in zip(serials, ends, costs, outcomes, grades):
        rec = out.setdefault(serial, {"refurb_end": None, "refurb_outcome": None, "grade_out": None, "refurb_cost": 0.0})
        if e is not None and e <= as_of:
            rec["refurb_cost"] += c
        if e is not None and (rec["refurb_end"] is None or e >= rec["refurb_end"]):
            rec["refurb_end"] = e
            rec["refurb_outcome"] = o
            rec["grade_out"] = g
    return out


def _resale_summary(resale: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    """Per serial: the earliest resale row (channel, sale_date, gross price, fees)."""
    out: dict[str, dict[str, Any]] = {}
    if resale is None or len(resale) == 0:
        return out
    serials = resale["serial"].astype(str).tolist()
    dates_ = _date_col(resale, "sale_date")
    prices = _float_col(resale, "price")
    fees = _float_col(resale, "fees")
    channels = [_to_str(v) for v in resale["channel"].tolist()] if "channel" in resale.columns else [None] * len(resale)
    for serial, d, p, f, ch in zip(serials, dates_, prices, fees, channels):
        prev = out.get(serial)
        if prev is None or (d is not None and (prev["sale_date"] is None or d < prev["sale_date"])):
            out[serial] = {"sale_date": d, "price": p, "fees": f, "channel": ch}
    return out


def _forecast_map(rv_current: pd.DataFrame | None) -> dict[str, float]:
    if rv_current is None or len(rv_current) == 0 or "forecast_rv" not in rv_current.columns:
        return {}
    out: dict[str, float] = {}
    for serial, v in zip(rv_current["serial"].astype(str).tolist(), rv_current["forecast_rv"].tolist()):
        fv = _to_float(v)
        if fv is not None:
            out[serial] = fv
    return out


def _write_down_map(ledger: pd.DataFrame | None, as_of: date) -> dict[str, float]:
    """Cumulative write-downs per serial from ledger rows with ``ledger.as_of < as_of`` (strictly earlier runs)."""
    out: dict[str, float] = {}
    if ledger is None or len(ledger) == 0:
        return out
    serials = ledger["serial"].astype(str).tolist()
    ledger_as_of = _date_col(ledger, "as_of")
    amounts = _float_col(ledger, "amount")
    for serial, d, amt in zip(serials, ledger_as_of, amounts):
        if d is not None and d < as_of:
            out[serial] = out.get(serial, 0.0) + amt
    return out


def _billing_pro_rata(a: "Assumptions") -> bool:
    try:
        return bool(a.get("billing", "pro_rata"))
    except KeyError:
        return False


def _marketplace_fee_pct(a: "Assumptions") -> float:
    try:
        return float(a.get("channel_fees", "marketplace")["fee_pct"])
    except (KeyError, TypeError):
        return 0.0


# --------------------------------------------------------------------------------------
# build_device_pnl
# --------------------------------------------------------------------------------------

def build_device_pnl(
    devices: pd.DataFrame,
    contracts: pd.DataFrame | None,
    events: pd.DataFrame | None,
    refurb: pd.DataFrame | None,
    resale: pd.DataFrame | None,
    rv_current: pd.DataFrame | None,
    ledger: pd.DataFrame | None,
    a: "Assumptions",
    as_of: date,
) -> pd.DataFrame:
    """Pure: source frames in, one ``device_pnl`` row per device out (all DDL columns, DDL order).

    ``rv_current`` (module 3) and ``ledger`` (module 4) may be ``None``: then ``forecast_rv``
    and ``margin_if_liquidated_today`` are NULL and ``write_down_cum`` is 0. Money columns are
    rounded to 2 decimals; ratios are left unrounded.
    """
    pro_rata = _billing_pro_rata(a)
    if pro_rata:
        raise NotImplementedError("pro rata billing is not implemented in v0.1 (assumptions.billing.pro_rata)")

    con_sum = _contract_summary(contracts, as_of, pro_rata)
    ev_sum = _event_summary(events, as_of)
    rf_sum = _refurb_summary(refurb, as_of)
    rs_sum = _resale_summary(resale)
    fc_map = _forecast_map(rv_current)
    wd_map = _write_down_map(ledger, as_of)
    mkt_fee_pct = _marketplace_fee_pct(a)

    planned_ratio_cache: dict[str, float] = {}
    depreciation_cache: dict[str, int] = {}

    def _planned(family: str, landed: float) -> float:
        if family not in planned_ratio_cache:
            planned_ratio_cache[family] = float(a.get("planned_rv_ratio", family))
        return round(landed * planned_ratio_cache[family], 2)

    def _depreciation(family: str) -> int:
        if family not in depreciation_cache:
            depreciation_cache[family] = int(a.get("depreciation_months", family))
        return depreciation_cache[family]

    rows: list[dict[str, Any]] = []
    if devices is None or len(devices) == 0:
        return pd.DataFrame(columns=DEVICE_PNL_COLUMNS)

    d_serials = devices["serial"].astype(str).tolist()
    d_family = [_to_str(v) for v in devices["model_family"].tolist()]
    d_model = [_to_str(v) for v in devices["model"].tolist()]
    d_storage = [_to_int(v) for v in devices["storage_gb"].tolist()] if "storage_gb" in devices.columns else [None] * len(devices)
    d_launch = _date_col(devices, "launch_date")
    d_purchase = _date_col(devices, "purchase_date")
    d_landed = _float_col(devices, "landed_cost")
    d_price = _float_col(devices, "purchase_price")

    for i, serial in enumerate(d_serials):
        family = d_family[i] or ""
        landed = d_landed[i]
        purchase_price = d_price[i]
        launch = d_launch[i]
        purchase = d_purchase[i]

        c = con_sum.get(serial, {})
        e = ev_sum.get(serial, {})
        r = rf_sum.get(serial, {})
        s = rs_sum.get(serial)

        months = int(c.get("months_billed", 0))
        revenue = float(c.get("rental_revenue", 0.0))
        return_date = e.get("return_date")
        refurb_end = r.get("refurb_end")
        refurb_outcome = r.get("refurb_outcome")

        status = derive_status(
            as_of=as_of,
            has_sale=s is not None,
            sale_date=s["sale_date"] if s is not None else None,
            refurb_outcome=refurb_outcome,
            refurb_end=refurb_end,
            return_date=return_date,
            contract_start=c.get("contract_start"),
            contract_end_effective=c.get("contract_end_effective"),
        )
        is_closed = status in CLOSED_STATUSES
        sold = status == "sold"

        repair_cost = float(e.get("repair_cost", 0.0))
        repl_cost = float(e.get("replacement_logistics_cost", 0.0))
        ret_cost = float(e.get("return_logistics_cost", 0.0))
        refurb_cost = float(r.get("refurb_cost", 0.0))
        channel_fees = float(s["fees"]) if sold and s is not None else 0.0
        service = repair_cost + repl_cost + ret_cost + refurb_cost + channel_fees

        if sold and s is not None:
            realised_rv: float | None = float(s["price"])
            resale_channel = s["channel"]
            sale_date = s["sale_date"]
            resale_price_gross: float | None = float(s["price"])
            resale_fees: float | None = float(s["fees"])
        elif status == "scrapped":
            realised_rv = 0.0
            resale_channel = sale_date = resale_price_gross = resale_fees = None
        else:
            realised_rv = None
            resale_channel = sale_date = resale_price_gross = resale_fees = None

        if is_closed and realised_rv is not None:
            margin: float | None = lifecycle_margin(revenue, landed, realised_rv, service)
            margin_pct: float | None = (margin / landed) if landed else None
        else:
            margin = None
            margin_pct = None

        forecast_rv: float | None = None
        margin_open: float | None = None
        if not is_closed:
            fv = fc_map.get(serial)
            if fv is not None:
                forecast_rv = fv
                margin_open = revenue - (landed - fv) - service - fv * mkt_fee_pct

        sellable_date = refurb_end if (refurb_end is not None and refurb_outcome != "scrap") else None
        days_in_stock = (as_of - sellable_date).days if (status == "in_stock" and sellable_date is not None) else None
        days_in_wip = (as_of - return_date).days if (status == "wip" and return_date is not None) else None

        if sold:
            closed_date = sale_date
        elif status == "scrapped":
            closed_date = refurb_end
        else:
            closed_date = None

        grade_inspected = e.get("grade_inspected")
        grade_current = r.get("grade_out") or grade_inspected or e.get("grade_pre_return")

        if is_closed:
            # derecognised at close: no book value on a sold or scrapped device
            book_sl = 0.0
            wd_cum = 0.0
            book_value = 0.0
        else:
            if purchase is not None:
                planned = _planned(family, landed)
                book_sl = straight_line_book_value(landed, planned, purchase, _depreciation(family), as_of)
            else:
                book_sl = round(landed, 2)
            wd_cum = round(wd_map.get(serial, 0.0), 2)
            book_value = round(max(book_sl - wd_cum, 0.0), 2)

        rows.append({
            "serial": serial,
            "as_of": as_of,
            "model_family": family or None,
            "model": d_model[i],
            "storage_gb": d_storage[i],
            "purchase_date": purchase,
            "cohort": quarter_label(purchase) if purchase is not None else None,
            "launch_date": launch,
            "months_since_launch": months_between_float(launch, as_of) if launch is not None else None,
            "landed_cost": _r2(landed),
            "purchase_price": _r2(purchase_price),
            "months_billed": months,
            "rental_revenue": _r2(revenue),
            "return_date": return_date,
            "grade_inspected": grade_inspected,
            "sellable_date": sellable_date,
            "refurb_outcome": refurb_outcome,
            "grade_current": grade_current,
            "resale_channel": resale_channel,
            "sale_date": sale_date,
            "resale_price_gross": _r2(resale_price_gross),
            "resale_fees": _r2(resale_fees),
            "realised_rv": _r2(realised_rv),
            "forecast_rv": _r2(forecast_rv),
            "repair_cost": _r2(repair_cost),
            "replacement_logistics_cost": _r2(repl_cost),
            "return_logistics_cost": _r2(ret_cost),
            "refurb_cost": _r2(refurb_cost),
            "channel_fees": _r2(channel_fees),
            "service_and_logistics_cost": _r2(service),
            "lifecycle_margin": _r2(margin),
            "lifecycle_margin_pct_of_landed": margin_pct,
            "margin_if_liquidated_today": _r2(margin_open),
            "lifecycle_status": status,
            "is_closed": bool(is_closed),
            "closed_date": closed_date,
            "days_in_stock": days_in_stock,
            "days_in_wip": days_in_wip,
            "book_value_sl": book_sl,
            "write_down_cum": wd_cum,
            "book_value": book_value,
        })

    return pd.DataFrame(rows, columns=DEVICE_PNL_COLUMNS)


# --------------------------------------------------------------------------------------
# run_pnl: the only DuckDB-touching function of the package
# --------------------------------------------------------------------------------------

def _nullable(df: pd.DataFrame) -> pd.DataFrame:
    """Object frame with ``None`` for every missing value, so DuckDB writes NULL, never NaN."""
    return df.astype(object).where(pd.notna(df), None)


def _read_optional(con: "duckdb.DuckDBPyConnection", table: str) -> pd.DataFrame | None:
    from restwert.db import read_df, table_exists

    if not table_exists(con, table):
        return None
    return read_df(con, f"SELECT * FROM {table}")


def run_pnl(con: "duckdb.DuckDBPyConnection", as_of: date, a: "Assumptions") -> "RunSummary":
    """Read source and forecast tables, write ``device_pnl``, ``tco_per_model`` (one row per model and term in ``config.TERM_MONTHS``: 12, 24, 36, 48) and ``pnl_aggregate``.

    Reads ``rv_forecast_current`` and ``rv_forecast_grid`` (module 3) and ``write_down_ledger``
    (module 4) when they exist; missing tables degrade to NULL forecasts and zero write-downs.
    ``generator.yaml`` families are passed to ``tco_per_model`` when the config loads, else
    ``None``. No side effects beyond the three replaced tables and the ``runs`` row.
    """
    from restwert.config import TERM_MONTHS
    from restwert.db import finish_run, new_run, read_df, write_df
    from restwert.pnl.aggregate import AGGREGATE_DIMENSIONS, aggregate_pnl
    from restwert.pnl.tco import tco_per_model
    from restwert.records import RunSummary

    started = datetime.now(UTC)
    run_id = new_run(con, "pnl", None, as_of, None)
    notes: list[str] = []

    devices = read_df(con, "SELECT * FROM devices")
    contracts = read_df(con, "SELECT * FROM rental_contracts")
    events = read_df(con, "SELECT * FROM events")
    refurb = read_df(con, "SELECT * FROM refurbishment")
    resale = read_df(con, "SELECT * FROM resale")

    rv_current = _read_optional(con, "rv_forecast_current")
    if rv_current is None or len(rv_current) == 0:
        notes.append("rv_forecast_current missing or empty: forecast_rv and margin_if_liquidated_today are NULL")
    ledger = _read_optional(con, "write_down_ledger")
    if ledger is None:
        notes.append("write_down_ledger missing: write_down_cum is 0")
    rv_grid = _read_optional(con, "rv_forecast_grid")
    if rv_grid is None or len(rv_grid) == 0:
        rv_grid = pd.DataFrame()
        notes.append("rv_forecast_grid missing or empty: TCO uses planned_rv_ratio from assumptions")

    cfg_families = None
    try:
        from restwert.config import load_generator_config

        cfg_families = load_generator_config().families
    except Exception as exc:  # config is optional for the TCO; state why it was skipped
        notes.append(f"generator.yaml families not loaded ({type(exc).__name__}); TCO uses assumptions only")

    device_pnl = build_device_pnl(devices, contracts, events, refurb, resale, rv_current, ledger, a, as_of)
    n_pnl = write_df(con, "device_pnl", _nullable(device_pnl), mode="replace")

    tco_frames = [
        tco_per_model(devices, events, refurb, resale, contracts, rv_grid, a, cfg_families, term, as_of)
        for term in TERM_MONTHS
    ]
    tco = pd.concat(tco_frames, ignore_index=True) if tco_frames else pd.DataFrame()
    n_tco = write_df(con, "tco_per_model", _nullable(tco), mode="replace") if len(tco) else 0

    agg_frames = [aggregate_pnl(device_pnl, by) for by in AGGREGATE_DIMENSIONS]
    agg = pd.concat(agg_frames, ignore_index=True)
    n_agg = write_df(con, "pnl_aggregate", _nullable(agg), mode="replace") if len(agg) else 0

    status_counts = device_pnl["lifecycle_status"].value_counts().to_dict() if len(device_pnl) else {}
    counts = {
        "device_pnl": int(n_pnl),
        "tco_per_model": int(n_tco),
        "pnl_aggregate": int(n_agg),
        "closed": int(device_pnl["is_closed"].sum()) if len(device_pnl) else 0,
        **{f"status_{k}": int(v) for k, v in status_counts.items()},
    }
    finish_run(con, run_id, counts)
    finished = datetime.now(UTC)
    return RunSummary(
        command="pnl",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=(finished - started).total_seconds(),
        counts=counts,
        notes=notes,
    )
