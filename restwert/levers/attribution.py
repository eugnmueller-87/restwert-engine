"""The seven levers L01..L07 (spec v0.2 section 7.2).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

A lever is actual minus a named reference on one ledger component, in EUR per device, with
``delta_eur >= 0`` meaning money left on the table. Each lever function takes one row of
``silver.device_ledger`` plus the reference it needs and returns a ``LeverResult``. Below
``lever_reference_min_n`` the reference is refused and the lever is ``is_attributed = false``
with ``delta_eur`` NULL: never guessed. ``counterfactual_json`` stores every input used.

Deterministic arithmetic only: no model runs here. The forecast enters as a stored number
(the forecast of record, the grid), never as a call into the fitter.

Choices where the spec is silent, all stated on the record:

* L01 is computed as ``effective_price - reference_price`` with ``effective_price =
  purchase_price - price_protection_credit_eur`` (a credit received is a purchase price
  reduction) and ``reference_price = round(rrp_net x (1 - d_ref), 2)``, which equals the
  spec's ``(d_ref - discount_vs_rrp_pct) x rrp_net`` up to cent rounding and makes the
  additivity identity exact. A device that beat the reference contributes 0 (floored), and
  the identity values it at its own price (the reference price is capped at the actual price).
* L03 values EVERY channel, the actual one included, at the forecast of record times the
  run's channel factor, net of the channel's fees and of holding cost over its days to cash,
  so the lever is zero when the device went through the best admissible channel. The gap
  between the record and what the actual channel realised is forecast accuracy, not a
  channel decision: it is stored in the record as ``realised_vs_record_gap_eur`` and read
  on the Resale page (``KPI_RSL_REALISED_VS_RECORD``), never inside the lever. Buyout
  eligibility is judged at the sale date against ``employee_buyout_window_days`` after the
  effective contract end, as R02 judges it at ``as_of``.
* L04: the grade part is read only when the grid orders the grades at that (model, month)
  (A >= B >= C >= D) and neither grade is an unsupported grade (``fit_quality =
  unsupported_grade``, the As-Is fallback); otherwise the lever is not attributed and the
  record says why, with the repair part kept for reading. When only one of the two grades at
  return is known the grade part is 0 and the record says so; the repair part reads the grid
  at the device's ``grade_used`` and at the months since launch on the repair line's event date.
* L05 uses ``grade_out`` (else grade_inspected, else grade_used) and rounds months to the
  nearest grid month; in-stock devices are measured at ``as_of``. The parameter that moves it
  is ``expected_return_to_sale_days`` (an assumption, owner Head of Recommerce); the rule that
  reacts to aged stock is R03 with its own threshold ``aging_days_90``; the summary names both.
* L07 compares the terms per month of term: ``median(lifecycle_result / term_months)`` of the
  other term minus the same of this term, times this term's months, floored at 0. The same
  value lands on every serial of the cohort and the summary reports it ONCE PER COHORT (a
  policy comparison, not money per device); the signed gap is in the record.
* n_reference: the fleet group size for L01, L06, L07 (this term's n for L07); 1 for L02
  (the serial's own PO line); the number of admissible channels for L03; the number of grid
  rows read for L04 and L05.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import pandas as pd

from restwert.config import Assumptions, Thresholds
from restwert.dates import days_between
from restwert.decisions.rules import SALE_CHANNELS
from restwert.levers.references import (
    GridIndex,
    admissible_channels,
    age_bucket,
    family_realisation_median,
    grade_at_sale_of,
    grid_fit_quality,
    grid_grades_monotone,
    grid_ratio,
    half_year,
    months_at,
    reference_discount,
    term_result_medians,
    to_date,
    to_float,
    to_str,
)
from restwert.ledger.lines import V01_BRIDGE_LINE_TYPES

DEFAULT_MIN_N = 10

#: Columns of ``gold.levers_per_device`` (DDL order).
PER_DEVICE_COLUMNS: tuple[str, ...] = (
    "serial",
    "lever_id",
    "delta_eur",
    "actual_value",
    "reference_value",
    "reference_source",
    "n_reference",
    "is_attributed",
    "additive",
    "basis",
    "event_date",
    "counterfactual_json",
    "as_of",
)

ADDITIVITY_COLUMNS: tuple[str, ...] = (
    "serial",
    "result_v01_basis_eur",
    "l01_delta",
    "l02_delta",
    "l01_identity",
    "l02_identity",
    "lhs",
    "rhs",
    "diff",
)


@dataclass(frozen=True)
class LeverSpec:
    """Metadata of one lever: the component it moves, its basis, the owned threshold, the rule and the reference parameter.

    ``threshold_key`` / ``rule_id`` name the rule (or advisory) that reacts and the threshold it
    reads; ``reference_key`` names the ``config/assumptions.yaml`` parameter the lever's
    arithmetic actually uses (its owner is resolved at summary time), ``None`` when the
    reference is the serial's own data. ``summary_basis`` says how the fleet EUR per year is
    formed: ``per_serial`` sums the attributed serials, ``per_cohort`` counts each cohort's
    value once (L07: a policy comparison, not money per device).
    """

    lever_id: str
    name: str
    component: str
    basis: Literal["fleet", "forecast_of_record", "grid"]
    additive: bool
    threshold_key: str
    threshold_sub: Literal["oem", "model_family", None]
    rule_id: str
    reference_sentence: str
    reference_key: str | None = None
    summary_basis: Literal["per_serial", "per_cohort"] = "per_serial"


LEVERS: dict[str, LeverSpec] = {
    "L01": LeverSpec(
        lever_id="L01",
        name="purchase_discount",
        component="purchase_price",
        basis="fleet",
        additive=True,
        threshold_key="purchase_discount_floor_pct",
        threshold_sub="oem",
        rule_id="R07",
        reference_sentence=(
            "the 75th percentile of the discount vs net launch RRP (net of price protection credits) "
            "over the fleet's own purchases of the same manufacturer, supplier role and purchase "
            "half-year (else the manufacturer over all time)"
        ),
        reference_key="lever_reference_min_n",
    ),
    "L02": LeverSpec(
        lever_id="L02",
        name="price_protection",
        component="price_protection_credit",
        basis="fleet",
        additive=True,
        threshold_key="price_protection_min_claim_eur",
        threshold_sub=None,
        rule_id="R05",
        reference_sentence=(
            "the price protection credit the PO line was entitled to; missed claims count, "
            "claimed and not applicable ones are 0, open windows are not attributed"
        ),
    ),
    "L03": LeverSpec(
        lever_id="L03",
        name="channel_choice",
        component="resale_gross + channel_fee",
        basis="forecast_of_record",
        additive=False,
        threshold_key="channel_min_net_uplift_eur",
        threshold_sub=None,
        rule_id="R02",
        reference_sentence=(
            "the best net over the channels R02 would have admitted minus the net of the channel "
            "used, both valued at the forecast of record times the run's channel factor, net of "
            "fees and holding cost to cash; zero when the best channel was used"
        ),
        reference_key="channel_fees",
    ),
    "L04": LeverSpec(
        lever_id="L04",
        name="grade_and_repair",
        component="resale_gross, repair, refurbishment",
        basis="grid",
        additive=False,
        threshold_key="repair_max_share_of_rv",
        threshold_sub="model_family",
        rule_id="R01",
        reference_sentence=(
            "the grid value at the grade the customer declared versus the grade inspected (read only "
            "where the grid orders the grades and neither grade is unsupported), plus every repair "
            "line above the family's maximum share of the grid value at repair time"
        ),
    ),
    "L05": LeverSpec(
        lever_id="L05",
        name="aging",
        component="holding_cost, resale_gross",
        basis="grid",
        additive=False,
        threshold_key="aging_days_90",
        threshold_sub=None,
        rule_id="R03",
        reference_sentence=(
            "the sale at the family's expected return-to-sale days (expected_return_to_sale_days, "
            "owner Head of Recommerce): holding cost of the excess days plus the grid value lost "
            "between the expected and the actual sale month; R03 and aging_days_90 are the rule "
            "and threshold that react to aged stock"
        ),
        reference_key="expected_return_to_sale_days",
    ),
    "L06": LeverSpec(
        lever_id="L06",
        name="manufacturer_mix",
        component="resale_gross",
        basis="fleet",
        additive=False,
        threshold_key="oem_realisation_gap_pct",
        threshold_sub=None,
        rule_id="ADV03",
        reference_sentence=(
            "the median realised share of net RRP over the fleet's own sales of the same catalogue "
            "family, 6-month age bucket and grade at sale; negative when the manufacturer beat it"
        ),
        reference_key="lever_reference_min_n",
    ),
    "L07": LeverSpec(
        lever_id="L07",
        name="term_length",
        component="lifecycle_result",
        basis="fleet",
        additive=False,
        threshold_key="term_result_gap_alert_eur",
        threshold_sub=None,
        rule_id="ADV04",
        reference_sentence=(
            "the median closed result per month of term of the best other contract term (of 12, 24, 36 "
            "and 48 months) inside the same segment and purchase half-year, times this term's months; the same value on every "
            "serial of the cohort and counted once per cohort in the summary (a policy comparison)"
        ),
        reference_key="lever_reference_min_n",
        summary_basis="per_cohort",
    ),
}


@dataclass
class LeverResult:
    """One lever on one serial."""

    lever_id: str
    delta_eur: float | None
    actual_value: float | None
    reference_value: float | None
    reference_source: str
    n_reference: int
    is_attributed: bool
    event_date: date | None
    counterfactual: dict[str, Any] = field(default_factory=dict)

    def to_row(self, serial: str, as_of: date) -> dict[str, Any]:
        spec = LEVERS[self.lever_id]
        return {
            "serial": serial,
            "lever_id": self.lever_id,
            "delta_eur": None if self.delta_eur is None else round(float(self.delta_eur), 2),
            "actual_value": self.actual_value,
            "reference_value": self.reference_value,
            "reference_source": self.reference_source,
            "n_reference": int(self.n_reference),
            "is_attributed": bool(self.is_attributed),
            "additive": spec.additive,
            "basis": spec.basis,
            "event_date": self.event_date,
            "counterfactual_json": json.dumps(self.counterfactual, sort_keys=True, default=str),
            "as_of": as_of,
        }


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _get(row: Any, col: str) -> Any:
    if isinstance(row, dict):
        return row.get(col)
    if isinstance(row, pd.Series):
        return row[col] if col in row.index else None
    return getattr(row, col, None)


def _f(row: Any, col: str) -> float | None:
    return to_float(_get(row, col))


def _d(row: Any, col: str) -> date | None:
    return to_date(_get(row, col))


def _s(row: Any, col: str) -> str | None:
    return to_str(_get(row, col))


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _r2(x: float) -> float:
    return round(float(x), 2)


def _not_attributed(lever_id: str, reason: str, event_date: date | None, source: str = "none", **cf: Any) -> LeverResult:
    return LeverResult(
        lever_id=lever_id,
        delta_eur=None,
        actual_value=None,
        reference_value=None,
        reference_source=source,
        n_reference=0,
        is_attributed=False,
        event_date=event_date,
        counterfactual={"not_attributed_reason": reason, **cf},
    )


def _month_int(m: float | None) -> int | None:
    return None if m is None else int(round(m))


# --------------------------------------------------------------------------------------
# L01 purchase discount
# --------------------------------------------------------------------------------------


def lever_purchase_discount(row: Any, ref: pd.Series | dict | None) -> LeverResult:
    """L01: ``(purchase_price - credit) - rrp_net x (1 - d_ref)`` floored at 0, ``d_ref`` the p75 of the fleet group.

    ``ref`` is the serial's row of :func:`references.reference_discount` (``ref_pct``,
    ``reference_source``, ``n_reference``, ``group_label``); ``None`` or a NaN ``ref_pct``
    means the group was below ``lever_reference_min_n`` and the lever is not attributed.
    """
    purchase = _d(row, "purchase_date")
    invoiced = _f(row, "purchase_price")
    credit = _f(row, "price_protection_credit_eur") or 0.0
    rrp = _f(row, "rrp_net_eur")
    if invoiced is None or rrp is None or rrp <= 0:
        return _not_attributed("L01", "no purchase price or net RRP on the serial", purchase)
    price = _r2(invoiced - credit)  # the credit received is a purchase price reduction
    ref_pct = to_float(_get(ref, "ref_pct")) if ref is not None else None
    if ref_pct is None:
        return _not_attributed(
            "L01",
            "fleet group below lever_reference_min_n",
            purchase,
            source=str(_get(ref, "reference_source") or "none") if ref is not None else "none",
            n_group=int(to_float(_get(ref, "n_reference")) or 0) if ref is not None else 0,
        )
    pct = (rrp - price) / rrp
    ref_price = _r2(rrp * (1.0 - ref_pct))
    delta = _r2(max(0.0, price - ref_price))
    n = int(to_float(_get(ref, "n_reference")) or 0)
    source = f"fleet:p75:{_get(ref, 'reference_source')}"
    return LeverResult(
        lever_id="L01",
        delta_eur=delta,
        actual_value=pct,
        reference_value=ref_pct,
        reference_source=source,
        n_reference=n,
        is_attributed=True,
        event_date=purchase,
        counterfactual={
            "group": str(_get(ref, "group_label") or ""),
            "n": n,
            "discount_vs_rrp_pct": pct,
            "reference_discount_pct_p75": ref_pct,
            "rrp_net_eur": _r2(rrp),
            "purchase_price": _r2(price),
            "purchase_price_invoiced": _r2(invoiced),
            "price_protection_credit_eur": _r2(credit),
            "reference_purchase_price": ref_price,
            "reference_purchase_price_capped": _r2(min(price, ref_price)),
            "formula": "(d_ref - discount_vs_rrp_pct) x rrp_net with the price net of the credit, floored at 0",
        },
    )


# --------------------------------------------------------------------------------------
# L02 price protection
# --------------------------------------------------------------------------------------


def lever_price_protection(row: Any) -> LeverResult:
    """L02: the missed price protection credit per device; claimed and n/a are 0, open is not attributed."""
    purchase = _d(row, "purchase_date")
    status = (_s(row, "price_protection_status") or "").lower()
    claimable = _f(row, "price_protection_claimable_eur") or 0.0
    credited = _f(row, "price_protection_credit_eur") or 0.0
    common = {
        "price_protection_status": status or None,
        "price_protection_claimable_eur": _r2(claimable),
        "price_protection_credit_eur": _r2(credited),
        "price_protection_days": _get(row, "price_protection_days"),
    }
    if status == "missed":
        return LeverResult(
            lever_id="L02",
            delta_eur=_r2(claimable),
            actual_value=_r2(credited),
            reference_value=_r2(claimable),
            reference_source="po_line:missed",
            n_reference=1,
            is_attributed=True,
            event_date=purchase,
            counterfactual={**common, "formula": "claimable credit when the window closed without a claim"},
        )
    if status == "claimed":
        return LeverResult("L02", 0.0, _r2(credited), _r2(credited), "claimed", 1, True, purchase, {**common})
    if status in ("not_applicable", "n/a", "na"):
        return LeverResult("L02", 0.0, 0.0, 0.0, "n/a", 1, True, purchase, {**common})
    if status == "open":
        return _not_attributed("L02", "claim window still open", purchase, source="open", **common)
    return _not_attributed("L02", "price protection status unknown", purchase, **common)


# --------------------------------------------------------------------------------------
# L03 channel choice
# --------------------------------------------------------------------------------------


def _channel_assumptions(a: Assumptions) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    fee_pct: dict[str, float] = {}
    fee_fixed: dict[str, float] = {}
    days: dict[str, float] = {}
    for c in SALE_CHANNELS:
        try:
            block = a.get("channel_fees", c)
        except KeyError:
            continue
        fee_pct[c] = float(block.get("fee_pct", 0.0))
        fee_fixed[c] = float(block.get("fee_fixed_eur", 0.0))
        days[c] = float(block.get("days_to_cash", 0.0))
    return fee_pct, fee_fixed, days


def lever_channel(row: Any, rv_record_row: Any, thr: Thresholds, a: Assumptions, as_of: date) -> LeverResult:
    """L03: ``best_net - net[actual_channel]`` over the channels R02 would have admitted, floored at 0.

    ``net_c = rv_of_record x channel_factor_c x (1 - fee_pct_c) - fee_fixed_c - holding_per_day x days_to_cash_c``
    for EVERY channel, the actual one included, with factors from ``rv_forecast_of_record``
    (marketplace 1.0; As Is valued at ``as_is_ratio_fallback[family] x purchase_price``). Both
    sides sit on the same basis, so a device sold through the best admissible channel carries
    0. What the actual channel realised against the record is forecast accuracy, kept in the
    record as ``realised_vs_record_gap_eur`` and never added to the lever.
    """
    sale = _d(row, "sale_date")
    status = _s(row, "lifecycle_status")
    if status != "sold" or sale is None:
        return _not_attributed("L03", "not a sold serial", sale)
    rv = to_float(_get(rv_record_row, "forecast_rv")) if rv_record_row is not None else None
    missing_raw = _get(rv_record_row, "is_missing") if rv_record_row is not None else True
    try:
        missing = False if missing_raw is None or pd.isna(missing_raw) else bool(missing_raw)
    except (TypeError, ValueError):
        missing = bool(missing_raw)
    if rv is None or rv <= 0 or missing:
        return _not_attributed("L03", "no forecast of record for the serial", sale, source="forecast_of_record:missing")
    family = _s(row, "model_family") or ""
    price = _f(row, "purchase_price")
    resale_net = _f(row, "resale_net")
    actual_channel = _s(row, "resale_channel")
    if resale_net is None or actual_channel is None:
        return _not_attributed("L03", "no resale net or channel on the serial", sale)

    fee_pct, fee_fixed, days = _channel_assumptions(a)
    holding = float(a.get("holding_cost_per_day_eur"))
    window = int(thr.get("employee_buyout_window_days", as_of=as_of).value)
    end = _d(row, "contract_end_effective")
    buyout_eligible = end is not None and 0 <= days_between(end, sale) <= window
    grade = grade_at_sale_of(row) or ""
    admissible = admissible_channels(grade, buyout_eligible, days, thr, as_of)
    if not admissible:
        return _not_attributed("L03", "no admissible channel", sale)

    f_emp = to_float(_get(rv_record_row, "channel_factor_employee_buyout"))
    f_b2b = to_float(_get(rv_record_row, "channel_factor_b2b_wholesale"))
    try:
        as_is_ratio = float(a.get("as_is_ratio_fallback", family))
    except KeyError:
        as_is_ratio = None
    def _value(c: str) -> float | None:
        if c == "marketplace":
            return rv
        if c == "employee_buyout":
            return rv * (f_emp if f_emp is not None else 1.0)
        if c == "b2b_wholesale":
            return rv * (f_b2b if f_b2b is not None else 1.0)
        if c == "as_is":
            return None if (as_is_ratio is None or price is None) else as_is_ratio * price
        return None

    def _net(c: str, v: float) -> float:
        return v * (1.0 - fee_pct.get(c, 0.0)) - fee_fixed.get(c, 0.0) - holding * days.get(c, 0.0)

    values: dict[str, float] = {}
    for c in sorted(set(admissible) | {actual_channel}, key=lambda c: (SALE_CHANNELS.index(c) if c in SALE_CHANNELS else 99, c)):
        v = _value(c)
        if v is not None:
            values[c] = v
    nets = {c: _net(c, v) for c, v in values.items()}
    admissible_nets = {c: n for c, n in nets.items() if c in admissible}
    if not admissible_nets:
        return _not_attributed("L03", "no channel value could be formed", sale)
    if actual_channel not in nets:
        return _not_attributed("L03", f"no record value for the channel used ({actual_channel})", sale)
    best = sorted(admissible_nets, key=lambda c: (-admissible_nets[c], days.get(c, 0.0), c))[0]
    actual_net = nets[actual_channel]
    delta = _r2(max(0.0, admissible_nets[best] - actual_net))
    gross = _f(row, "resale_gross")
    realised_gap = _r2(gross - values[actual_channel]) if gross is not None else None
    return LeverResult(
        lever_id="L03",
        delta_eur=delta,
        actual_value=_r2(actual_net),
        reference_value=_r2(admissible_nets[best]),
        reference_source=f"forecast_of_record:{_get(rv_record_row, 'run_id')}:{best}",
        n_reference=len(admissible_nets),
        is_attributed=True,
        event_date=sale,
        counterfactual={
            "forecast_rv_of_record": _r2(rv),
            "run_id": to_str(_get(rv_record_row, "run_id")),
            "channel_factors": {"marketplace": 1.0, "employee_buyout": f_emp, "b2b_wholesale": f_b2b, "as_is_ratio": as_is_ratio},
            "grade_at_sale": grade,
            "buyout_eligible": bool(buyout_eligible),
            "admissible_channels": admissible,
            "actual_channel_admissible": actual_channel in admissible,
            "net_by_channel": {c: _r2(v) for c, v in nets.items()},
            "best_channel": best,
            "actual_channel": actual_channel,
            "resale_gross": _r2(gross) if gross is not None else None,
            "resale_net": _r2(resale_net),
            "realised_vs_record_gap_eur": realised_gap,
            "realised_vs_record_note": "forecast accuracy (gross realised minus the record value of the channel used); not part of the lever, see KPI_RSL_REALISED_VS_RECORD",
            "days_to_cash_by_channel": {c: days.get(c, 0.0) for c in nets},
            "holding_cost_per_day": holding,
            "formula": "net[best admissible channel] - net[actual channel], both at the record, floored at 0",
        },
    )


# --------------------------------------------------------------------------------------
# L04 grade and repair
# --------------------------------------------------------------------------------------


def lever_grade_repair(
    row: Any,
    lines_of_serial: pd.DataFrame | None,
    rv_grid: pd.DataFrame | GridIndex | None,
    thr: Thresholds,
    as_of: date | None = None,
) -> LeverResult:
    """L04: grade part (declared vs inspected on the grid at return) plus repair lines above the family share.

    Grade part ``purchase_price x (grid(model, grade_declared, m_ret) - grid(model, grade_inspected, m_ret))``
    is not floored (a better inspection shows negative). It is read only when the grid orders
    the grades at (model, m_ret) (A >= B >= C >= D) and neither grade is unsupported on the
    grid (``fit_quality = unsupported_grade``); otherwise the lever is not attributed with
    the reason. Repair part
    ``sum max(0, repair - repair_max_share_of_rv[family] x purchase_price x grid(model, grade_used, m_repair))``.
    """
    closed = _d(row, "closed_date")
    ret = _d(row, "return_date")
    is_closed = bool(_get(row, "is_closed") or False)
    if not is_closed or ret is None:
        return _not_attributed("L04", "not a closed serial with a return", closed)
    as_of = as_of or _d(row, "as_of") or closed
    model = _s(row, "slug") or _s(row, "model")
    family = _s(row, "model_family") or ""
    price = _f(row, "purchase_price")
    launch = _d(row, "launch_date")
    if model is None or price is None or launch is None:
        return _not_attributed("L04", "no model, purchase price or launch date", closed)
    try:
        share = thr.get("repair_max_share_of_rv", family, as_of=as_of)
    except (KeyError, ValueError) as exc:
        return _not_attributed("L04", f"threshold unavailable: {exc}", closed)

    g_decl = _s(row, "grade_declared")
    g_insp = _s(row, "grade_inspected")
    grade_used = _s(row, "grade_used") or g_insp or g_decl
    m_ret = _month_int(months_at(launch, ret))
    reads = 0
    grade_part = 0.0
    grade_note = "declared vs inspected"
    r_decl = r_insp = None
    refusal: str | None = None
    grid_at_return: dict[str, float] = {}
    if g_decl and g_insp and m_ret is not None:
        r_decl = grid_ratio(rv_grid, model, g_decl, m_ret)
        r_insp = grid_ratio(rv_grid, model, g_insp, m_ret)
        if r_decl is None or r_insp is None:
            return _not_attributed("L04", "model not on the forecast grid", closed, source="grid")
        reads += 2
        if g_decl != g_insp:
            unsupported = [g for g in (g_decl, g_insp) if grid_fit_quality(rv_grid, model, g) == "unsupported_grade"]
            monotone, grid_at_return = grid_grades_monotone(rv_grid, model, m_ret)
            if unsupported:
                refusal = f"unsupported grade on the grid ({', '.join(sorted(set(unsupported)))}: the As-Is fallback, not a fit)"
            elif not monotone:
                refusal = "grid not monotone in grade at (model, month): a worse grade valued above a better one"
        grade_part = price * (r_decl - r_insp)
    else:
        grade_note = "grade part 0: declared or inspected grade missing"

    repairs: list[dict[str, Any]] = []
    repair_part = 0.0
    if lines_of_serial is not None and len(lines_of_serial):
        sub = lines_of_serial[lines_of_serial["line_type"].astype(str) == "repair"]
        for ln in sub.itertuples(index=False):
            amount = abs(to_float(getattr(ln, "amount_eur", None)) or 0.0)
            ev = to_date(getattr(ln, "event_date", None))
            m_rep = _month_int(months_at(launch, ev)) if ev is not None else m_ret
            if m_rep is None or grade_used is None:
                continue
            r_rep = grid_ratio(rv_grid, model, grade_used, m_rep)
            if r_rep is None:
                return _not_attributed("L04", "model not on the forecast grid", closed, source="grid")
            if refusal is None and grid_fit_quality(rv_grid, model, grade_used) == "unsupported_grade":
                refusal = f"unsupported grade on the grid ({grade_used}: the As-Is fallback, not a fit)"
            reads += 1
            limit = float(share.value) * price * r_rep
            excess = max(0.0, amount - limit)
            repair_part += excess
            repairs.append(
                {
                    "source_ref": to_str(getattr(ln, "source_ref", None)),
                    "event_date": _iso(ev),
                    "repair_eur": _r2(amount),
                    "months_since_launch": m_rep,
                    "grade_used": grade_used,
                    "grid_ratio": r_rep,
                    "limit_eur": _r2(limit),
                    "excess_eur": _r2(excess),
                }
            )
    delta = _r2(grade_part + repair_part)
    counterfactual = {
        "model": model,
        "purchase_price": _r2(price),
        "grade_declared": g_decl,
        "grade_inspected": g_insp,
        "months_since_launch_at_return": m_ret,
        "grid_ratio_declared": r_decl,
        "grid_ratio_inspected": r_insp,
        "grid_ratios_at_return": {g: round(v, 6) for g, v in grid_at_return.items()},
        "grade_part_eur": _r2(grade_part),
        "grade_note": grade_note,
        "repair_max_share_of_rv": float(share.value),
        "repair_max_share_owner": share.owner,
        "repairs": repairs,
        "repair_part_eur": _r2(repair_part),
        "formula": "purchase_price x (grid(declared) - grid(inspected)) + sum max(0, repair - share x purchase_price x grid(grade_used))",
    }
    if refusal is not None:
        return _not_attributed("L04", refusal, closed, source="grid", **counterfactual)
    return LeverResult(
        lever_id="L04",
        delta_eur=delta,
        actual_value=_r2(price * r_insp) if r_insp is not None else None,
        reference_value=_r2(price * r_decl) if r_decl is not None else None,
        reference_source="grid:declared_vs_inspected+repair_share",
        n_reference=reads,
        is_attributed=True,
        event_date=closed,
        counterfactual=counterfactual,
    )


# --------------------------------------------------------------------------------------
# L05 aging
# --------------------------------------------------------------------------------------


def lever_aging(row: Any, rv_grid: pd.DataFrame | GridIndex | None, a: Assumptions, as_of: date) -> LeverResult:
    """L05: excess days in stock times holding cost plus the grid value lost between the expected and the actual sale month.

    ``excess_days = max(0, days_sellable_to_sold - expected_return_to_sale_days[family])``; sold
    serials are measured at the sale date, in-stock serials at ``as_of``.
    """
    status = _s(row, "lifecycle_status")
    sale = _d(row, "sale_date")
    sellable = _d(row, "sellable_date")
    if status == "sold" and sale is not None:
        measured = sale
    elif status == "in_stock":
        measured = as_of
    else:
        return _not_attributed("L05", "not a sold or in-stock serial", sale)
    if sellable is None:
        return _not_attributed("L05", "no sellable date on the serial", measured)
    family = _s(row, "model_family") or ""
    model = _s(row, "slug") or _s(row, "model")
    price = _f(row, "purchase_price")
    launch = _d(row, "launch_date")
    grade = _s(row, "grade_out") or _s(row, "grade_inspected") or _s(row, "grade_used")
    if model is None or price is None or launch is None or grade is None:
        return _not_attributed("L05", "no model, purchase price, launch date or grade", measured)
    try:
        expected_days = float(a.get("expected_return_to_sale_days", family))
    except KeyError:
        return _not_attributed("L05", f"no expected_return_to_sale_days for {family}", measured)
    holding = float(a.get("holding_cost_per_day_eur"))

    days_in_stock = days_between(sellable, measured)
    excess = max(0, int(days_in_stock) - int(round(expected_days)))
    m_expected = _month_int(months_at(launch, sellable + timedelta(days=int(round(expected_days)))))
    m_actual = _month_int(months_at(launch, measured))
    r_exp = grid_ratio(rv_grid, model, grade, m_expected) if m_expected is not None else None
    r_act = grid_ratio(rv_grid, model, grade, m_actual) if m_actual is not None else None
    if r_exp is None or r_act is None:
        return _not_attributed("L05", "model not on the forecast grid", measured, source="grid")
    holding_part = excess * holding
    value_part = price * max(0.0, r_exp - r_act)
    delta = _r2(holding_part + value_part)
    return LeverResult(
        lever_id="L05",
        delta_eur=delta,
        actual_value=float(days_in_stock),
        reference_value=float(expected_days),
        reference_source="grid:expected_return_to_sale_days",
        n_reference=2,
        is_attributed=True,
        event_date=measured,
        counterfactual={
            "lifecycle_status": status,
            "model": model,
            "grade_out": grade,
            "sellable_date": _iso(sellable),
            "measured_at": _iso(measured),
            "days_sellable_to_sold": int(days_in_stock),
            "expected_return_to_sale_days": expected_days,
            "expected_days_owner": a.owner("expected_return_to_sale_days"),
            "excess_days": int(excess),
            "holding_cost_per_day": holding,
            "holding_part_eur": _r2(holding_part),
            "months_expected": m_expected,
            "months_actual": m_actual,
            "grid_ratio_expected": r_exp,
            "grid_ratio_actual": r_act,
            "purchase_price": _r2(price),
            "value_part_eur": _r2(value_part),
            "formula": "excess_days x holding_per_day + purchase_price x max(0, grid(m_expected) - grid(m_actual))",
        },
    )


# --------------------------------------------------------------------------------------
# L06 manufacturer mix
# --------------------------------------------------------------------------------------


def lever_oem_mix(row: Any, family_ref: pd.Series | dict | None) -> LeverResult:
    """L06: ``(median_ratio_family_bucket_grade - resale_gross / rrp_net) x rrp_net``, not floored.

    ``family_ref`` is the row of :func:`references.family_realisation_median` for the serial's
    (catalogue_family, age bucket at sale, grade at sale); ``None`` means below ``2 x min_n``.
    """
    sale = _d(row, "sale_date")
    status = _s(row, "lifecycle_status")
    gross = _f(row, "resale_gross")
    rrp = _f(row, "rrp_net_eur")
    launch = _d(row, "launch_date")
    if status != "sold" or sale is None or gross is None:
        return _not_attributed("L06", "not a sold serial", sale)
    if rrp is None or rrp <= 0 or launch is None:
        return _not_attributed("L06", "no net RRP or launch date", sale)
    grade = grade_at_sale_of(row)
    bucket = age_bucket(months_at(launch, sale) or 0.0)
    ref = to_float(_get(family_ref, "median_ratio")) if family_ref is not None else None
    if ref is None or grade is None:
        return _not_attributed(
            "L06",
            "family group below 2 x lever_reference_min_n",
            sale,
            catalogue_family=_s(row, "catalogue_family"),
            age_bucket=bucket,
            grade_at_sale=grade,
        )
    realised = gross / rrp
    n = int(to_float(_get(family_ref, "n")) or 0)
    delta = _r2((ref - realised) * rrp)
    return LeverResult(
        lever_id="L06",
        delta_eur=delta,
        actual_value=realised,
        reference_value=ref,
        reference_source="fleet:median:family+age_bucket+grade",
        n_reference=n,
        is_attributed=True,
        event_date=sale,
        counterfactual={
            "oem": _s(row, "oem"),
            "catalogue_family": _s(row, "catalogue_family"),
            "age_bucket": bucket,
            "grade_at_sale": grade,
            "n": n,
            "realised_ratio": realised,
            "median_ratio_family": ref,
            "gap_ratio": ref - realised,
            "rrp_net_eur": _r2(rrp),
            "resale_gross": _r2(gross),
            "formula": "(median_ratio - resale_gross / rrp_net) x rrp_net, not floored",
        },
    )


# --------------------------------------------------------------------------------------
# L07 term length
# --------------------------------------------------------------------------------------


TermIndex = dict[tuple[str, str], dict[int, tuple[float, int, float]]]


def term_index(term_ref: pd.DataFrame | None) -> TermIndex:
    """``{(model_family, purchase_half_year): {term_months: (median_result_eur, n, median_result_per_month_eur)}}``.

    Built from :func:`references.term_result_medians`; a frame without the per-month column
    derives it as ``median_result_eur / term_months``.
    """
    out: TermIndex = {}
    if term_ref is None or len(term_ref) == 0:
        return out
    for r in term_ref.to_dict("records"):
        key = (str(r["model_family"]), str(r["purchase_half_year"]))
        term = int(r["term_months"])
        per_month = to_float(r.get("median_result_per_month_eur"))
        if per_month is None:
            per_month = float(r["median_result_eur"]) / term if term else 0.0
        out.setdefault(key, {})[term] = (float(r["median_result_eur"]), int(r["n"]), float(per_month))
    return out


def lever_term(row: Any, term_ref: pd.DataFrame | TermIndex | None) -> LeverResult:
    """L07: ``(median_per_month(other term) - median_per_month(this term)) x this term's months``, floored at 0.

    ``median_per_month`` is the cohort's median of ``lifecycle_result_eur / term_months``
    inside (model_family, purchase half-year), so the terms are compared on the same footing
    (a 36-month term earns twelve more months of rent than a 24-month one). ``term_ref`` holds
    the rows of :func:`references.term_result_medians` (already filtered to groups with
    ``n >= min_n``), or the :func:`term_index` built from them; this term and at least one
    other term must be present. With several other terms (the simulation knows 12, 24, 36 and
    48 months) the best other per-month median is the reference. The same value lands on
    every serial of the cohort; the summary counts it once per cohort.
    """
    closed = _d(row, "closed_date")
    is_closed = bool(_get(row, "is_closed") or False)
    purchase = _d(row, "purchase_date")
    term = _f(row, "term_months")
    family = _s(row, "model_family")
    if not is_closed or purchase is None or term is None or family is None:
        return _not_attributed("L07", "not a closed serial with a term and purchase date", closed)
    term = int(term)
    hy = half_year(purchase)
    index = term_ref if isinstance(term_ref, dict) else term_index(term_ref)
    if not index:
        return _not_attributed("L07", "no term medians", closed, model_family=family, purchase_half_year=hy)
    cohort = index.get((family, hy), {})
    this = cohort.get(term)
    others = {t: v for t, v in cohort.items() if t != term}
    if this is None or not others:
        return _not_attributed(
            "L07",
            "this term or every other term below lever_reference_min_n in the cohort",
            closed,
            model_family=family,
            purchase_half_year=hy,
            term_months=term,
            terms_with_reference=sorted(cohort),
        )
    other_term = sorted(others, key=lambda t: (-others[t][2], t))[0]
    other_median, other_n, other_per_month = others[other_term]
    this_median, this_n, this_per_month = this
    gap_per_month = other_per_month - this_per_month
    gap = gap_per_month * term
    delta = _r2(max(0.0, gap))
    return LeverResult(
        lever_id="L07",
        delta_eur=delta,
        actual_value=round(this_per_month, 4),
        reference_value=round(other_per_month, 4),
        reference_source=f"fleet:median_per_month:term_{other_term}",
        n_reference=this_n,
        is_attributed=True,
        event_date=closed,
        counterfactual={
            "model_family": family,
            "purchase_half_year": hy,
            "this_term": term,
            "other_term": other_term,
            "median_result_this_term": this_median,
            "median_result_other_term": other_median,
            "median_result_per_month_this_term": round(this_per_month, 4),
            "median_result_per_month_other_term": round(other_per_month, 4),
            "n_this_term": this_n,
            "n_other_term": other_n,
            "gap_per_month_signed_eur": round(gap_per_month, 4),
            "gap_signed_eur": _r2(gap),
            "summary_basis": "per_cohort",
            "formula": "(median_per_month(other term) - median_per_month(this term)) x this_term, floored at 0; a policy comparison counted once per cohort",
        },
    )


# --------------------------------------------------------------------------------------
# attribute everything
# --------------------------------------------------------------------------------------


def _min_n(a: Assumptions) -> int:
    try:
        return int(a.get("lever_reference_min_n"))
    except KeyError:
        return DEFAULT_MIN_N


def _repair_lines_by_serial(lines: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    if lines is None or len(lines) == 0 or "line_type" not in lines.columns:
        return {}
    sub = lines[lines["line_type"].astype(str) == "repair"]
    if len(sub) == 0:
        return {}
    return {str(k): g for k, g in sub.groupby(sub["serial"].astype(str))}


def attribute_all(
    dl: pd.DataFrame,
    lines: pd.DataFrame | None,
    timeline: pd.DataFrame | None,
    rv_of_record: pd.DataFrame | None,
    rv_grid: pd.DataFrame | GridIndex | None,
    thr: Thresholds,
    a: Assumptions,
    as_of: date,
) -> pd.DataFrame:
    """``gold.levers_per_device``: one row per (serial, lever) for every eligible serial.

    ``timeline`` is accepted for interface stability (the ledger already carries the day
    counts the levers need). Eligibility per lever is in the section 7.2 table; a serial that
    is eligible but whose reference is below ``lever_reference_min_n`` gets a row with
    ``is_attributed = false``.
    """
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=list(PER_DEVICE_COLUMNS))
    min_n = _min_n(a)
    grid = rv_grid if isinstance(rv_grid, GridIndex) else GridIndex(rv_grid)

    disc = reference_discount(dl, min_n)
    disc_by_serial = {str(r["serial"]): r for r in disc.to_dict("records")} if len(disc) else {}
    fam_ref = family_realisation_median(dl, 2 * min_n)
    fam_key = {
        (str(r["catalogue_family"]), str(r["age_bucket"]), str(r["grade_at_sale"])): r for r in fam_ref.to_dict("records")
    }
    term_ref = term_index(term_result_medians(dl, min_n))
    repairs = _repair_lines_by_serial(lines)
    record_by_serial: dict[str, dict] = {}
    if rv_of_record is not None and len(rv_of_record):
        record_by_serial = {str(r["serial"]): r for r in rv_of_record.to_dict("records")}

    rows: list[dict[str, Any]] = []
    for row in dl.to_dict("records"):
        serial = str(row.get("serial"))
        status = to_str(row.get("lifecycle_status"))
        is_closed = bool(row.get("is_closed") or False)
        sold = status == "sold"

        # L01: every received serial
        if to_date(row.get("received_at")) is not None or to_date(row.get("purchase_date")) is not None:
            rows.append(lever_purchase_discount(row, disc_by_serial.get(serial)).to_row(serial, as_of))
            # L02: every serial with a purchase (status decides the value)
            rows.append(lever_price_protection(row).to_row(serial, as_of))
        if sold:
            rows.append(lever_channel(row, record_by_serial.get(serial), thr, a, as_of).to_row(serial, as_of))
        if is_closed and to_date(row.get("return_date")) is not None:
            rows.append(lever_grade_repair(row, repairs.get(serial), grid, thr, as_of).to_row(serial, as_of))
        if sold or status == "in_stock":
            rows.append(lever_aging(row, grid, a, as_of).to_row(serial, as_of))
        if sold:
            launch = to_date(row.get("launch_date"))
            sale = to_date(row.get("sale_date"))
            key = None
            if launch is not None and sale is not None:
                key = (
                    str(row.get("catalogue_family")),
                    age_bucket(months_at(launch, sale) or 0.0),
                    str(grade_at_sale_of(row)),
                )
            rows.append(lever_oem_mix(row, fam_key.get(key) if key else None).to_row(serial, as_of))
        if is_closed:
            rows.append(lever_term(row, term_ref).to_row(serial, as_of))

    out = pd.DataFrame(rows, columns=list(PER_DEVICE_COLUMNS))
    if len(out):
        out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce")
        out["as_of"] = pd.to_datetime(out["as_of"])
        out["delta_eur"] = pd.to_numeric(out["delta_eur"], errors="coerce").astype(float)
        out["actual_value"] = pd.to_numeric(out["actual_value"], errors="coerce").astype(float)
        out["reference_value"] = pd.to_numeric(out["reference_value"], errors="coerce").astype(float)
        out["n_reference"] = out["n_reference"].astype(int)
        out["is_attributed"] = out["is_attributed"].astype(bool)
        out["additive"] = out["additive"].astype(bool)
        out = out.sort_values(["serial", "lever_id"]).reset_index(drop=True)
    return out


def _lines_sums(lines: pd.DataFrame | None) -> dict[str, tuple[float, float, float]]:
    """Per serial from ``silver.ledger_lines``: (sum of non-bridge lines, purchase_price magnitude, credit magnitude)."""
    if lines is None or len(lines) == 0 or not {"serial", "line_type", "amount_eur"}.issubset(lines.columns):
        return {}
    f = pd.DataFrame({
        "serial": lines["serial"].astype(str),
        "line_type": lines["line_type"].astype(str),
        "amount": pd.to_numeric(lines["amount_eur"], errors="coerce").fillna(0.0).astype(float),
    })
    f["basis"] = f["amount"].where(~f["line_type"].isin(V01_BRIDGE_LINE_TYPES), 0.0)
    f["price"] = f["amount"].abs().where(f["line_type"] == "purchase_price", 0.0)
    f["credit"] = f["amount"].abs().where(f["line_type"] == "price_protection_credit", 0.0)
    g = f.groupby("serial")[["basis", "price", "credit"]].sum()
    return {str(k): (float(v["basis"]), float(v["price"]), float(v["credit"])) for k, v in g.iterrows()}


def check_additivity(
    dl: pd.DataFrame, per_device: pd.DataFrame, lines: pd.DataFrame | None = None, tol: float = 0.01
) -> pd.DataFrame:
    """Rows breaking the additive identity; empty when fine.

    For every closed serial ``result_v01_basis + L01 + L02`` (the left side: the device ledger's
    stored basis plus the two stored deltas) must equal the right side rebuilt from
    ``silver.ledger_lines`` with the purchase paid at the capped reference price and the
    missed credit received::

        rhs = sum(non-bridge lines other than purchase_price)
              - credit_received - min(effective_price, round(rrp_net x (1 - d_ref), 2))
              + claimable (when the price protection status is missed)

    with ``effective_price = purchase_price - credit_received`` (the credit is a purchase price
    reduction, see L01) and ``d_ref`` the stored reference. The right side never reads
    ``result_v01_basis_eur``, ``purchase_price`` or a delta from the device ledger, so a wrong
    delta or a basis that drifted from the lines surfaces as a non-zero ``diff``; a stored
    ``purchase_price`` that does not match its purchase line is reported as a violation too
    (``diff`` = stored minus line). Without ``lines`` (hand tests) the right side falls back to the
    device ledger's basis and purchase price and the check degrades to a formula consistency
    test; ``lines_used`` in the returned frame's attrs says which one ran. A lever that is
    not attributed contributes 0 on both sides.
    """
    cols = list(ADDITIVITY_COLUMNS)
    if dl is None or len(dl) == 0 or per_device is None or len(per_device) == 0:
        return pd.DataFrame(columns=cols)
    closed = dl[dl["is_closed"].fillna(False).astype(bool)] if "is_closed" in dl.columns else dl
    if len(closed) == 0:
        return pd.DataFrame(columns=cols)
    pdv = per_device[per_device["lever_id"].isin(["L01", "L02"])]
    by_serial: dict[str, dict[str, dict]] = {}
    for r in pdv.to_dict("records"):
        by_serial.setdefault(str(r["serial"]), {})[str(r["lever_id"])] = r
    sums = _lines_sums(lines)
    lines_used = bool(sums)

    rows: list[dict[str, Any]] = []
    for row in closed.to_dict("records"):
        serial = str(row.get("serial"))
        levers = by_serial.get(serial, {})
        basis = to_float(row.get("result_v01_basis_eur")) or 0.0
        rrp = to_float(row.get("rrp_net_eur")) or 0.0
        if lines_used:
            line_basis, price, credit = sums.get(serial, (0.0, 0.0, 0.0))
        else:
            line_basis = basis
            price = to_float(row.get("purchase_price")) or 0.0
            credit = to_float(row.get("price_protection_credit_eur")) or 0.0
        effective = round(price - credit, 2)
        l01 = levers.get("L01")
        l02 = levers.get("L02")
        l01_delta = to_float(l01.get("delta_eur")) if l01 and bool(l01.get("is_attributed")) else 0.0
        l02_delta = to_float(l02.get("delta_eur")) if l02 and bool(l02.get("is_attributed")) else 0.0
        l01_delta = l01_delta or 0.0
        l02_delta = l02_delta or 0.0
        # the purchase the identity pays: the capped reference price when L01 is attributed, else the price itself
        paid = effective
        if l01 and bool(l01.get("is_attributed")):
            d_ref = to_float(l01.get("reference_value"))
            if d_ref is not None and rrp > 0:
                paid = min(effective, round(rrp * (1.0 - d_ref), 2))
        l01_identity = round(effective - paid, 2)
        l02_identity = 0.0
        if l02 and bool(l02.get("is_attributed")):
            status = (to_str(row.get("price_protection_status")) or "").lower()
            if status == "missed":
                l02_identity = to_float(row.get("price_protection_claimable_eur")) or 0.0
        lhs = basis + l01_delta + l02_delta
        # rhs from the lines: every non-bridge line except the purchase, the credit netted, the capped reference paid
        rhs = (line_basis + price) - credit - paid + l02_identity
        diff = round(lhs - rhs, 4)
        if lines_used and abs(diff) <= tol:
            # the identity can hold on a purchase line that drifted from the device ledger; the stored price must match its line
            dl_price = to_float(row.get("purchase_price"))
            if dl_price is not None and abs(dl_price - price) > tol:
                diff = round(dl_price - price, 4)
        if abs(diff) > tol:
            rows.append(
                {
                    "serial": serial,
                    "result_v01_basis_eur": basis,
                    "l01_delta": l01_delta,
                    "l02_delta": l02_delta,
                    "l01_identity": round(l01_identity, 2),
                    "l02_identity": round(l02_identity, 2),
                    "lhs": round(lhs, 2),
                    "rhs": round(rhs, 2),
                    "diff": diff,
                }
            )
    out = pd.DataFrame(rows, columns=cols)
    out.attrs["lines_used"] = lines_used
    return out


__all__ = [
    "PER_DEVICE_COLUMNS",
    "ADDITIVITY_COLUMNS",
    "LeverSpec",
    "LEVERS",
    "LeverResult",
    "lever_purchase_discount",
    "lever_price_protection",
    "lever_channel",
    "lever_grade_repair",
    "lever_aging",
    "lever_oem_mix",
    "lever_term",
    "term_index",
    "attribute_all",
    "check_additivity",
]
