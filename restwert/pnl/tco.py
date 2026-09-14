"""Total cost of ownership per model over a rental term (spec section 4.3).

::

    tco_T          = landed_avg - forecast_rv_at_end + E[repair] + E[refurb] + E[logistics] + E[fees]
    tco_per_month  = tco_T / T
    gap            = monthly_rate_avg - tco_per_month

Expected costs come from realised data when the family has at least
``assumptions.min_n_for_realised_inputs`` rows of that kind, else from the owner-named
fallback blocks in ``config/assumptions.yaml``. ``inputs_source`` records which:
``'realised'`` when every cost input came from realised data, ``'assumptions'`` when every
cost input came from a fallback, ``'mixed'`` otherwise. The forecast residual value ratio
comes from ``rv_forecast_grid`` (module 3) and is a forecast, neither realised nor an
assumption; when the grid has no row for the model, ``planned_rv_ratio`` from the
assumptions is used ON LANDED COST (that is how the ratio is defined in
``assumptions.yaml`` and in ``book_value.py``) and the row is classed as an assumption
input.

Valuation basis (one basis, stated on every row)
------------------------------------------------
* ``forecast_rv_at_end`` is the marketplace-baseline forecast for the family's expected
  grade at ``rv_months_at_end = months since launch at purchase + T + return-to-sale
  days``, read from the grid at the model's base storage. ``forecast_rv_source`` says
  where it came from (``grid``, ``grid_clipped``, ``planned_ratio_on_landed_cost``).
* When ``rv_months_at_end`` lies outside the grid horizon the nearest grid month is used,
  ``rv_clipped_to_grid`` is True, ``rv_months_used`` shows the month actually read and the
  row is downgraded to ``inputs_source = 'mixed'``: a clipped residual value is overstated
  and must not be read as realised-input truth. The grid runs to 120 months
  (``forecast.registry.GRID_MONTHS``), a horizon derived from the shipped device ledger and
  not from a purchase rule: the older pool of the generation draw buys devices well after
  launch, and the 84-month horizon that assumed "24 months since launch at purchase plus the
  longest term" left 24 open serials clipped at 85 to 94 months (measured on
  ``silver.device_ledger``, 2026-09-14; the derivation is in ``registry.GRID_MONTHS``).
  120 = the measured maximum after catalogue round 4 (110 months, an iPad Air of 2020 bought
  in late 2025 on a 48-month term) plus 6 months reserve, rounded up to a full year. Whether
  a row is clipped is a fact of the data, stated per row, never assumed away.
* ``expected_channel_fees`` applies the MARKETPLACE fee share to that marketplace price:
  the realised marketplace fee ratio (fees / gross price over the family's marketplace
  sales) when at least ``min_n`` such sales exist, else the marketplace ``fee_pct``
  assumption. A blended fee over buyout, wholesale and As-Is sales would mix bases.
* Not corrected here, stated instead: the grid is evaluated at base storage while the
  model's devices may carry more; and realised recovery reflects the channel mix, which
  the marketplace baseline does not (see ``KPI_REC_RV_REALISATION``). Both are a few
  percent of the residual value.

Choices where the spec is silent:

* ``months_at_end`` uses Python's ``round`` (banker's rounding on exact .5).
* Rented device-years for the damage rate are ``sum(months_billed) / 12`` over the family's
  contracts up to ``as_of``; a family with 0 device-years falls back to the assumption.
* The realised logistics input is the mean of (return + replacement logistics cost) over the
  family's devices that have at least one such event; ``min_n`` counts those devices.
* The realised marketplace fee ratio also requires ``min_n`` marketplace sales, else the
  marketplace ``fee_pct`` assumption.
* ``tco_per_month`` is left unrounded in the frame (DuckDB rounds to DECIMAL(12,2) on write)
  so that ``tco_per_month * T == tco`` holds in tests; money inputs are rounded to 2.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from restwert.dates import DAYS_PER_MONTH, months_between_float
from restwert.pnl.lifecycle import _date_col, _float_col, _is_missing, _to_float, _to_str, months_billed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from restwert.config import Assumptions, FamilyConfig


TCO_COLUMNS: list[str] = [
    "model", "term_months", "as_of", "model_family", "n_devices",
    "landed_cost_avg", "purchase_price_avg", "months_since_launch_at_purchase_avg",
    "grade_assumed", "forecast_rv_ratio_at_end", "forecast_rv_at_end",
    "expected_repair_cost", "expected_refurb_cost", "expected_logistics_cost", "expected_channel_fees",
    "tco", "tco_per_month", "monthly_rate_avg", "gap_rate_minus_tco_per_month",
    "inputs_source", "forecast_rv_source", "rv_months_at_end", "rv_months_used", "rv_clipped_to_grid",
]

DEFAULT_MIN_N = 30


def _min_n(a: "Assumptions") -> int:
    try:
        return int(a.get("min_n_for_realised_inputs"))
    except KeyError:
        return DEFAULT_MIN_N


def _family_realised_inputs(
    dev: pd.DataFrame,
    events: pd.DataFrame | None,
    refurb: pd.DataFrame | None,
    resale: pd.DataFrame | None,
    contracts: pd.DataFrame | None,
    as_of: date,
) -> dict[str, dict[str, Any]]:
    """Realised per-family statistics (counts included so the caller can apply ``min_n``)."""
    fam_of = dict(zip(dev["serial"], dev["model_family"]))
    stats: dict[str, dict[str, Any]] = {}

    def rec(f: str) -> dict[str, Any]:
        return stats.setdefault(
            f,
            {"n_damage": 0, "n_repair": 0, "sum_repair_cost": 0.0, "device_years": 0.0,
             "n_refurb": 0, "sum_refurb_cost": 0.0, "logistics_by_device": {},
             "n_sales": 0, "sum_price": 0.0, "sum_fees": 0.0,
             "n_sales_marketplace": 0, "sum_price_marketplace": 0.0, "sum_fees_marketplace": 0.0},
        )

    if contracts is not None and len(contracts):
        starts = _date_col(contracts, "start_date")
        ends = _date_col(contracts, "end_date")
        aends = _date_col(contracts, "actual_end_date")
        for serial, s, e, ae in zip(contracts["serial"].astype(str), starts, ends, aends):
            f = fam_of.get(serial)
            if f is None or s is None or e is None:
                continue
            rec(f)["device_years"] += months_billed(s, e, ae, as_of) / 12.0

    if events is not None and len(events):
        types = [(_to_str(t) or "").lower() for t in events["event_type"].tolist()]
        edates = _date_col(events, "event_date")
        costs = _float_col(events, "cost")
        for serial, t, ed, c in zip(events["serial"].astype(str), types, edates, costs):
            f = fam_of.get(serial)
            if f is None or (ed is not None and ed > as_of):
                continue
            r = rec(f)
            if t == "damage":
                r["n_damage"] += 1
            elif t == "repair":
                r["n_repair"] += 1
                r["sum_repair_cost"] += c
            elif t in ("return", "replacement"):
                r["logistics_by_device"][serial] = r["logistics_by_device"].get(serial, 0.0) + c

    if refurb is not None and len(refurb):
        costs = _float_col(refurb, "cost")
        for serial, c in zip(refurb["serial"].astype(str), costs):
            f = fam_of.get(serial)
            if f is None:
                continue
            r = rec(f)
            r["n_refurb"] += 1
            r["sum_refurb_cost"] += c

    if resale is not None and len(resale):
        sdates = _date_col(resale, "sale_date")
        prices = _float_col(resale, "price")
        fees = _float_col(resale, "fees")
        channels = [_to_str(c) for c in resale["channel"].tolist()] if "channel" in resale.columns else [None] * len(resale)
        for serial, d, p, fee, ch in zip(resale["serial"].astype(str), sdates, prices, fees, channels):
            f = fam_of.get(serial)
            if f is None or (d is not None and d > as_of):
                continue
            r = rec(f)
            r["n_sales"] += 1
            r["sum_price"] += p
            r["sum_fees"] += fee
            if ch == "marketplace":
                r["n_sales_marketplace"] += 1
                r["sum_price_marketplace"] += p
                r["sum_fees_marketplace"] += fee

    return stats


def _grid_ratio(rv_grid: pd.DataFrame | None, model: str, grade: str, months: int) -> tuple[float, int, bool] | None:
    """``(forecast_rv_ratio, month used, clipped)`` of the grid row (model, grade, months), or ``None``.

    ``clipped`` is True when ``months`` lies outside the grid's month range and the nearest
    edge month was read instead; the caller records that on the TCO row.
    """
    if rv_grid is None or len(rv_grid) == 0:
        return None
    needed = {"model", "grade", "months_since_launch", "forecast_rv_ratio"}
    if not needed.issubset(rv_grid.columns):
        return None
    sub = rv_grid[(rv_grid["model"].astype(str) == model) & (rv_grid["grade"].astype(str) == grade)]
    if len(sub) == 0:
        return None
    m = sub["months_since_launch"].astype(int)
    used = int(min(max(months, int(m.min())), int(m.max())))
    idx = (m - used).abs().idxmin()
    ratio = _to_float(sub.loc[idx, "forecast_rv_ratio"])
    if ratio is None:
        return None
    return float(ratio), used, used != int(months)


def tco_per_model(
    devices: pd.DataFrame,
    events: pd.DataFrame | None,
    refurb: pd.DataFrame | None,
    resale: pd.DataFrame | None,
    contracts: pd.DataFrame | None,
    rv_grid: pd.DataFrame | None,
    a: "Assumptions",
    cfg_families: "dict[str, FamilyConfig] | None",
    term_months: int,
    as_of: date,
) -> pd.DataFrame:
    """One ``tco_per_model`` row per model (with at least one device) for ``term_months``.

    ``cfg_families`` (generator launch calendar) is accepted for interface stability and may
    be ``None``; v0.1 needs nothing from it because months since launch come from
    ``devices.launch_date``.
    """
    T = int(term_months)
    if devices is None or len(devices) == 0:
        return pd.DataFrame(columns=TCO_COLUMNS)

    dev = pd.DataFrame({
        "serial": devices["serial"].astype(str),
        "model": [_to_str(v) for v in devices["model"].tolist()],
        "model_family": [_to_str(v) for v in devices["model_family"].tolist()],
        "landed_cost": _float_col(devices, "landed_cost"),
        "purchase_price": _float_col(devices, "purchase_price"),
    })
    launches = _date_col(devices, "launch_date")
    purchases = _date_col(devices, "purchase_date")
    dev["msl_at_purchase"] = [
        months_between_float(l, p) if (l is not None and p is not None) else float("nan")
        for l, p in zip(launches, purchases)
    ]

    min_n = _min_n(a)
    fam_stats = _family_realised_inputs(dev, events, refurb, resale, contracts, as_of)

    # monthly rate per model: contracts with term T, else all contracts of the model
    rate_by_model_term: dict[tuple[str, int], list[float]] = {}
    rate_by_model: dict[str, list[float]] = {}
    if contracts is not None and len(contracts):
        model_of = dict(zip(dev["serial"], dev["model"]))
        rates = _float_col(contracts, "monthly_rate")
        terms = contracts["term_months"].tolist() if "term_months" in contracts.columns else [None] * len(contracts)
        for serial, rate, term in zip(contracts["serial"].astype(str), rates, terms):
            model = model_of.get(serial)
            if model is None:
                continue
            rate_by_model.setdefault(model, []).append(rate)
            if not _is_missing(term):
                rate_by_model_term.setdefault((model, int(term)), []).append(rate)

    def _mkt_fee_pct() -> float:
        try:
            return float(a.get("channel_fees", "marketplace")["fee_pct"])
        except (KeyError, TypeError):
            return 0.0

    rows: list[dict[str, Any]] = []
    for model, g in dev.groupby("model", sort=True):
        if model is None:
            continue
        family = g["model_family"].mode().iloc[0] if g["model_family"].notna().any() else None
        family = str(family) if family is not None else ""
        n = int(len(g))
        landed_avg = float(g["landed_cost"].mean())
        price_avg = float(g["purchase_price"].mean())
        msl_series = g["msl_at_purchase"].dropna()
        msl_avg = float(msl_series.mean()) if len(msl_series) else 0.0

        sources: list[str] = []
        fs = fam_stats.get(family, {})

        # forecast residual value at the end of the term (see module docstring: valuation basis)
        grade_assumed = str(a.get("expected_grade_at_return", family))
        rts_days = float(a.get("expected_return_to_sale_days", family))
        months_at_end = int(round(msl_avg + T + rts_days / DAYS_PER_MONTH))
        hit = _grid_ratio(rv_grid, str(model), grade_assumed, months_at_end)
        if hit is not None:
            ratio_at_end, months_used, clipped = hit
            forecast_rv_at_end = round(ratio_at_end * price_avg, 2)
            rv_source = "grid_clipped" if clipped else "grid"
            if clipped:
                sources.append("grid_clipped")  # never 'realised': the value is overstated
        else:
            ratio_at_end = float(a.get("planned_rv_ratio", family))
            months_used, clipped = months_at_end, False
            forecast_rv_at_end = round(ratio_at_end * landed_avg, 2)  # planned ratio is defined on landed cost
            rv_source = "planned_ratio_on_landed_cost"
            sources.append("assumptions")

        # repair
        n_repair = int(fs.get("n_repair", 0))
        n_damage = int(fs.get("n_damage", 0))
        device_years = float(fs.get("device_years", 0.0))
        if n_repair >= min_n and n_damage > 0 and device_years > 0:
            damage_rate_pa = n_damage / device_years
            repair_share = n_repair / n_damage
            mean_repair_cost = fs["sum_repair_cost"] / n_repair
            sources.append("realised")
        else:
            damage_rate_pa = float(a.get("damage_rate_pa_fallback", family))
            repair_share = float(a.get("repair_share_fallback", family))
            mean_repair_cost = float(a.get("repair_cost_fallback_eur", family))
            sources.append("assumptions")
        expected_repair = round(damage_rate_pa * (T / 12.0) * repair_share * mean_repair_cost, 2)

        # refurbishment
        n_refurb = int(fs.get("n_refurb", 0))
        if n_refurb >= min_n:
            expected_refurb = round(fs["sum_refurb_cost"] / n_refurb, 2)
            sources.append("realised")
        else:
            expected_refurb = round(float(a.get("refurb_cost_fallback_eur", family)), 2)
            sources.append("assumptions")

        # logistics
        log_by_dev: dict[str, float] = fs.get("logistics_by_device", {})
        if len(log_by_dev) >= min_n:
            expected_logistics = round(sum(log_by_dev.values()) / len(log_by_dev), 2)
            sources.append("realised")
        else:
            expected_logistics = round(float(a.get("logistics_cost_fallback_eur", family)), 2)
            sources.append("assumptions")

        # channel fees: marketplace fee share on the marketplace-baseline price (one basis)
        n_mkt = int(fs.get("n_sales_marketplace", 0))
        sum_price_mkt = float(fs.get("sum_price_marketplace", 0.0))
        if n_mkt >= min_n and sum_price_mkt > 0:
            fee_pct = fs["sum_fees_marketplace"] / sum_price_mkt
            sources.append("realised")
        else:
            fee_pct = _mkt_fee_pct()
            sources.append("assumptions")
        expected_fees = round(forecast_rv_at_end * fee_pct, 2)

        tco = round(landed_avg - forecast_rv_at_end + expected_repair + expected_refurb + expected_logistics + expected_fees, 2)
        tco_per_month = tco / T

        rates = rate_by_model_term.get((str(model), T)) or rate_by_model.get(str(model))
        monthly_rate_avg = round(sum(rates) / len(rates), 2) if rates else None
        gap = (monthly_rate_avg - tco_per_month) if monthly_rate_avg is not None else None

        uniq = set(sources)
        inputs_source = "realised" if uniq == {"realised"} else "assumptions" if uniq == {"assumptions"} else "mixed"

        rows.append({
            "model": str(model),
            "term_months": T,
            "as_of": as_of,
            "model_family": family or None,
            "n_devices": n,
            "landed_cost_avg": round(landed_avg, 2),
            "purchase_price_avg": round(price_avg, 2),
            "months_since_launch_at_purchase_avg": msl_avg,
            "grade_assumed": grade_assumed,
            "forecast_rv_ratio_at_end": ratio_at_end,
            "forecast_rv_at_end": forecast_rv_at_end,
            "expected_repair_cost": expected_repair,
            "expected_refurb_cost": expected_refurb,
            "expected_logistics_cost": expected_logistics,
            "expected_channel_fees": expected_fees,
            "tco": tco,
            "tco_per_month": tco_per_month,
            "monthly_rate_avg": monthly_rate_avg,
            "gap_rate_minus_tco_per_month": gap,
            "inputs_source": inputs_source,
            "forecast_rv_source": rv_source,
            "rv_months_at_end": int(months_at_end),
            "rv_months_used": int(months_used),
            "rv_clipped_to_grid": bool(clipped),
        })

    return pd.DataFrame(rows, columns=TCO_COLUMNS)
