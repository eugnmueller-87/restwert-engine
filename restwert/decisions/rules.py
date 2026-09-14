"""Deterministic decision rules R01..R06 (spec section 6.2) and R07 (spec v0.2 section 7.4).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every rule in this file is a pure function: plain inputs plus a ``Thresholds`` object in,
one ``DecisionRecord`` out. No table reads, no side effects, no randomness. The record carries
the rule id, the threshold that fired (key, value, unit, owner), every input the rule read,
an optional advisory from the forecast module and a hash of the inputs, so the same inputs
always produce the same decision and the log can be deduplicated on rerun.

Advisories never change an outcome. They ride along on the record so a human sees them next
to the deterministic decision.

Choices where the spec is silent (documented here, enforced by the tests):

* Money comparisons use a tolerance of ``EPS = 1e-9`` so that a quote equal to
  ``share x rv`` (which is stated as "repair") does not flip on binary floating point noise.
* R02 tie-breaks: among equally fast channels the higher net wins; among equally good nets
  the faster channel wins; the channel name is the final tie-break. When every allowed channel
  is slower than ``channel_max_days_to_cash`` the fastest one is kept (the filter never removes
  the last remaining channel).
* R04 has no grade threshold in the yaml; when the grade condition fails the record carries
  ``replacement_max_months_since_launch`` as the threshold of the rule, and ``outcome_detail``
  names the grade as the first failing condition.
* R02 names the threshold that actually decided the channel: ``as_is_only_grade`` when the
  grade filter left As Is as the only channel, ``employee_buyout_window_days`` when the closed
  buyout window removed a channel that would otherwise have won, ``channel_max_days_to_cash``
  when the days-to-cash filter removed a channel with a better net than the chosen one, and
  ``channel_min_net_uplift_eur`` otherwise. Every threshold the rule read is listed in
  ``inputs["thresholds_consulted"]`` with key, value, unit and owner, so no owner is invisible.
* R06 on a rental contract takes its notice period from the threshold ``rental_notice_days``
  (owner Head of Customer Success), never from a contract field; the record lists it in
  ``inputs`` and in ``thresholds_consulted``.
* Every threshold lookup passes ``as_of``; a threshold whose ``valid_from`` lies after the
  decision date raises instead of firing (see ``config.Thresholds.get``).
* Dates in ``inputs`` are ISO strings, money is a float rounded to cents.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from restwert.config import Thresholds
from restwert.dates import days_between
from restwert.records import Advisory, DecisionRecord, ResolvedThreshold, build_record

RULE_VERSION = "1.0"

#: Tolerance for money and ratio comparisons (equality cases are stated in the spec).
EPS = 1e-9

#: Outcomes per rule that are logged but never queued (spec section 6.2).
NO_ACTION_OUTCOMES: dict[str, set[str]] = {
    "R01": set(),
    "R02": set(),
    "R03": {"none"},
    "R04": {"not_eligible"},
    "R05": {"no_action", "claim_open", "below_min_claim"},
    "R06": {"no_action", "expired"},
    "R07": {"at_or_above_floor"},
}

#: Queue priority 1 outcomes; every other queued outcome is priority 2; advisory-only rows are 3.
PRIORITY_1_OUTCOMES: frozenset[str] = frozenset(
    {
        "write_down_180",
        "claim_reminder",
        "notice_missed_auto_renews",
        "renewal_alert_high_value",
        "claim_window_expired",
    }
)

#: Channels a device can be sold through (spec section 2.1, kept local so this module only
#: depends on the foundation files it really needs).
SALE_CHANNELS: tuple[str, ...] = ("employee_buyout", "marketplace", "b2b_wholesale", "as_is")

VALID_CONTRACT_TYPES: tuple[str, ...] = ("supplier_contract", "rental_contract")


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _iso(d: date | None) -> str | None:
    """Return the ISO string of a date (None stays None)."""
    return d.isoformat() if d is not None else None


def _eur(x: float | None) -> str:
    """Format a money amount for outcome_detail."""
    return "n/a" if x is None else f"{float(x):.2f} EUR"


def _pct(x: float) -> str:
    """Format a ratio threshold as a percentage without trailing noise (0.35 -> '35 %')."""
    return f"{float(x) * 100:g} %"


def _money(x: float | int | None) -> float | None:
    """Coerce to a float rounded to cents; None stays None."""
    return None if x is None else round(float(x), 2)


def _consulted(*thresholds: ResolvedThreshold) -> list[dict[str, Any]]:
    """``inputs["thresholds_consulted"]``: every threshold a rule read, with its owner."""
    return [
        {
            "key": t.resolved_key,
            "value": t.value,
            "unit": t.unit,
            "owner": t.owner,
            "valid_from": t.valid_from.isoformat() if t.valid_from is not None else None,
        }
        for t in thresholds
    ]


# --------------------------------------------------------------------------------------
# R01 repair or not
# --------------------------------------------------------------------------------------


def decide_repair(
    *,
    serial: str,
    family: str,
    repair_quote_eur: float,
    forecast_rv_after_repair_eur: float,
    forecast_rv_as_is_eur: float,
    thr: Thresholds,
    as_of: date,
    run_id: str,
    advisory: Advisory | None = None,
) -> DecisionRecord:
    """R01: repair a damaged device only when the quote is a small enough share of its value.

    Order of tests:
    1. forecast RV after repair below ``repair_min_rv_eur[family]`` -> ``no_repair_below_min_rv``
    2. quote <= ``repair_max_share_of_rv[family]`` x forecast RV after repair -> ``repair``
       (equality counts as repair)
    3. otherwise -> ``no_repair_quote_above_share``

    ``value_at_stake_eur`` = forecast RV after repair - quote - forecast RV As Is, i.e. what the
    repair is worth compared with selling the broken device As Is. The caller values the
    repaired device at the grade-B marketplace forecast; the record says so in
    ``inputs["forecast_rv_after_repair_source"]``.
    """
    min_rv = thr.get("repair_min_rv_eur", family, as_of=as_of)
    share = thr.get("repair_max_share_of_rv", family, as_of=as_of)

    quote = float(repair_quote_eur)
    rv_after = float(forecast_rv_after_repair_eur)
    rv_as_is = float(forecast_rv_as_is_eur)
    limit = float(share.value) * rv_after
    quote_share = (quote / rv_after) if rv_after > 0 else None

    inputs: dict[str, Any] = {
        "serial": serial,
        "family": family,
        "repair_quote_eur": _money(quote),
        "forecast_rv_after_repair_eur": _money(rv_after),
        "forecast_rv_after_repair_source": "grade_b_marketplace_forecast",
        "forecast_rv_as_is_eur": _money(rv_as_is),
        "repair_min_rv_eur": float(min_rv.value),
        "repair_max_share_of_rv": float(share.value),
        "repair_limit_eur": _money(limit),
        "quote_share_of_rv": None if quote_share is None else round(quote_share, 4),
        "thresholds_consulted": _consulted(min_rv, share),
        "as_of": _iso(as_of),
    }

    share_txt = (
        f"{quote_share * 100:.1f} %" if quote_share is not None else "an undefined share"
    )
    if rv_after < float(min_rv.value) - EPS:
        outcome = "no_repair_below_min_rv"
        fired: ResolvedThreshold = min_rv
        detail = (
            f"forecast RV after repair {_eur(rv_after)} is below the minimum "
            f"{_eur(min_rv.value)} for {family}; no repair"
        )
    elif quote <= limit + EPS:
        outcome = "repair"
        fired = share
        detail = (
            f"repair quote {_eur(quote)} is {share_txt} of forecast RV {_eur(rv_after)}, "
            f"at most {_pct(share.value)} for {family}; repair"
        )
    else:
        outcome = "no_repair_quote_above_share"
        fired = share
        detail = (
            f"repair quote {_eur(quote)} is {share_txt} of forecast RV {_eur(rv_after)}, "
            f"above {_pct(share.value)} for {family}; no repair"
        )

    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R01",
        rule_version=RULE_VERSION,
        subject_type="device",
        subject_id=serial,
        outcome=outcome,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=advisory,
        value_at_stake_eur=_money(rv_after - quote - rv_as_is),
        due_date=None,
    )


# --------------------------------------------------------------------------------------
# R02 channel choice
# --------------------------------------------------------------------------------------


def decide_channel(
    *,
    serial: str,
    family: str,
    grade: str,
    forecast_rv_by_channel: dict[str, float],
    fee_pct: dict[str, float],
    fee_fixed_eur: dict[str, float],
    days_to_cash: dict[str, float],
    holding_cost_per_day: float,
    buyout_eligible: bool,
    thr: Thresholds,
    as_of: date,
    run_id: str,
    advisory: Advisory | None = None,
) -> DecisionRecord:
    """R02: pick the resale channel with the best net after fees and time to cash.

    Allowed channels: grade equal to ``as_is_only_grade`` -> As Is only; every other grade ->
    every channel except As Is. ``employee_buyout`` is dropped unless ``buyout_eligible`` (the
    caller derives it from ``employee_buyout_window_days``; the rule records that threshold
    and its owner so the decision is auditable). Channels with ``days_to_cash`` above
    ``channel_max_days_to_cash`` are dropped, but the filter never removes the last remaining
    channel.

    ``net[c] = rv[c] * (1 - fee_pct[c]) - fee_fixed_eur[c] - holding_cost_per_day * days_to_cash[c]``.
    The best net wins over the fastest channel only if it beats it by at least
    ``channel_min_net_uplift_eur`` (equality: the slower, better channel wins). The outcome is
    the chosen channel name. ``value_at_stake_eur`` is the gap between the best and the second
    best net (0 with one channel).

    The threshold on the record is the one that decided the channel (see the module
    docstring); all four thresholds the rule read are in ``inputs["thresholds_consulted"]``.
    """
    uplift = thr.get("channel_min_net_uplift_eur", as_of=as_of)
    max_days = thr.get("channel_max_days_to_cash", as_of=as_of)
    as_is_grade = thr.get("as_is_only_grade", as_of=as_of)
    window = thr.get("employee_buyout_window_days", as_of=as_of)

    channels = [c for c in forecast_rv_by_channel]
    if not channels:
        raise ValueError(f"R02 {serial}: forecast_rv_by_channel is empty")

    excluded: dict[str, str] = {}
    grade_filter_only = False
    buyout_removed = False
    if str(grade) == str(as_is_grade.value):
        allowed = [c for c in channels if c == "as_is"]
        grade_filter_only = True
        for c in channels:
            if c != "as_is":
                excluded[c] = f"grade {grade} is sold As Is only"
        if not allowed:
            raise ValueError(f"R02 {serial}: grade {grade} needs an 'as_is' forecast")
    else:
        allowed = [c for c in channels if c != "as_is"]
        if "as_is" in channels:
            excluded["as_is"] = f"grade {grade} never goes As Is"
        if not buyout_eligible and "employee_buyout" in allowed:
            allowed.remove("employee_buyout")
            buyout_removed = True
            excluded["employee_buyout"] = f"employee buyout window of {window.value} days closed"
        if not allowed:
            raise ValueError(f"R02 {serial}: no channel left after the eligibility filters")

    def _days(c: str) -> float:
        return float(days_to_cash.get(c, 0.0))

    def _net(c: str) -> float:
        return (
            float(forecast_rv_by_channel[c]) * (1.0 - float(fee_pct.get(c, 0.0)))
            - float(fee_fixed_eur.get(c, 0.0))
            - float(holding_cost_per_day) * _days(c)
        )

    net = {c: _net(c) for c in channels}

    # days-to-cash filter, slowest first, never removing the last remaining channel
    removed_by_days: list[str] = []
    for c in sorted(allowed, key=lambda c: (-_days(c), c)):
        if len(allowed) <= 1:
            break
        if _days(c) > float(max_days.value) + EPS:
            allowed.remove(c)
            removed_by_days.append(c)
            excluded[c] = f"{_days(c):g} days to cash above {max_days.value} days"

    fastest = sorted(allowed, key=lambda c: (_days(c), -net[c], c))[0]
    ranked = sorted(allowed, key=lambda c: (-net[c], _days(c), c))
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None

    gain = net[best] - net[fastest]
    if best != fastest and gain >= float(uplift.value) - EPS:
        chosen = best
    else:
        chosen = fastest

    table = {
        c: {
            "rv": _money(forecast_rv_by_channel[c]),
            "fee_pct": float(fee_pct.get(c, 0.0)),
            "fee_fixed": float(fee_fixed_eur.get(c, 0.0)),
            "days_to_cash": _days(c),
            "net": _money(net[c]),
            "allowed": c in allowed,
            "excluded_reason": excluded.get(c),
        }
        for c in channels
    }
    # which threshold decided the channel (see module docstring)
    if grade_filter_only:
        fired: ResolvedThreshold = as_is_grade
    elif buyout_removed and net["employee_buyout"] > net[chosen] + EPS:
        fired = window
    elif any(net[c] > net[chosen] + EPS for c in removed_by_days):
        fired = max_days
    else:
        fired = uplift

    inputs: dict[str, Any] = {
        "serial": serial,
        "family": family,
        "grade": grade,
        "buyout_eligible": bool(buyout_eligible),
        "holding_cost_per_day": float(holding_cost_per_day),
        "channels": table,
        "allowed_channels": list(allowed),
        "fastest_channel": fastest,
        "best_net_channel": best,
        "net_gain_best_over_fastest": _money(gain),
        "channel_min_net_uplift_eur": float(uplift.value),
        "channel_max_days_to_cash": float(max_days.value),
        "as_is_only_grade": str(as_is_grade.value),
        "employee_buyout_window_days": int(window.value),
        "employee_buyout_window_owner": window.owner,
        "decided_by_threshold": fired.resolved_key,
        "thresholds_consulted": _consulted(uplift, max_days, as_is_grade, window),
        "as_of": _iso(as_of),
    }

    if len(allowed) == 1:
        detail = (
            f"{chosen} chosen, the only allowed channel for grade {grade}: "
            f"net {_eur(net[chosen])} after fees, {_days(chosen):g} days to cash"
        )
    elif chosen == best and best != fastest:
        detail = (
            f"{chosen} chosen with net {_eur(net[chosen])} after fees and {_days(chosen):g} days "
            f"to cash: beats the fastest channel {fastest} (net {_eur(net[fastest])}) by "
            f"{_eur(gain)}, at least the {_eur(uplift.value)} uplift threshold"
        )
    elif best != fastest:
        detail = (
            f"{chosen} chosen as the fastest channel with net {_eur(net[chosen])} after fees and "
            f"{_days(chosen):g} days to cash: {best} would net {_eur(gain)} more, below the "
            f"{_eur(uplift.value)} uplift threshold"
        )
    else:
        detail = (
            f"{chosen} chosen: best net {_eur(net[chosen])} after fees and also the fastest "
            f"channel at {_days(chosen):g} days to cash"
        )

    value_at_stake = (net[best] - net[second]) if second is not None else 0.0
    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R02",
        rule_version=RULE_VERSION,
        subject_type="device",
        subject_id=serial,
        outcome=chosen,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=advisory,
        value_at_stake_eur=_money(value_at_stake),
        due_date=None,
    )


# --------------------------------------------------------------------------------------
# R03 aging write-down
# --------------------------------------------------------------------------------------


def decide_aging_write_down(
    *,
    serial: str,
    days_in_stock: int,
    book_value_before: float,
    already_written_down: float,
    thr: Thresholds,
    as_of: date,
    run_id: str,
) -> DecisionRecord:
    """R03: write down sellable stock that sits too long (strictly more than the bucket).

    ``gross = book_value_before + already_written_down``. Above ``aging_days_180`` the target is
    ``gross x age_write_down_pct_180``, above ``aging_days_90`` it is ``gross x age_write_down_pct_90``,
    otherwise no write-down. ``amount = max(target - already_written_down, 0)``, so the write-down
    is cumulative and never booked twice. 90 days -> none, 91 -> write_down_90, 180 -> write_down_90,
    181 -> write_down_180.
    """
    d90 = thr.get("aging_days_90", as_of=as_of)
    d180 = thr.get("aging_days_180", as_of=as_of)
    p90 = thr.get("age_write_down_pct_90", as_of=as_of)
    p180 = thr.get("age_write_down_pct_180", as_of=as_of)

    days = int(days_in_stock)
    before = float(book_value_before)
    already = float(already_written_down or 0.0)
    gross = before + already

    if days > int(d180.value):
        outcome = "write_down_180"
        fired: ResolvedThreshold = d180
        pct_used = float(p180.value)
        target = gross * pct_used
        amount = max(target - already, 0.0)
        detail = (
            f"in stock {days} days, above {d180.value} days: write-down target {_pct(pct_used)} of "
            f"gross book value {_eur(gross)} = {_eur(target)}, already written down {_eur(already)}, "
            f"booking {_eur(amount)}"
        )
    elif days > int(d90.value):
        outcome = "write_down_90"
        fired = d90
        pct_used = float(p90.value)
        target = gross * pct_used
        amount = max(target - already, 0.0)
        detail = (
            f"in stock {days} days, above {d90.value} days: write-down target {_pct(pct_used)} of "
            f"gross book value {_eur(gross)} = {_eur(target)}, already written down {_eur(already)}, "
            f"booking {_eur(amount)}"
        )
    else:
        outcome = "none"
        fired = d90
        pct_used = 0.0
        target = 0.0
        amount = 0.0
        detail = f"in stock {days} days, at most {d90.value} days: no write-down"

    amount = round(amount, 2)
    inputs: dict[str, Any] = {
        "serial": serial,
        "days_in_stock": days,
        "book_value_before": _money(before),
        "already_written_down": _money(already),
        "gross_book_value": _money(gross),
        "aging_days_90": int(d90.value),
        "aging_days_180": int(d180.value),
        "age_write_down_pct_90": float(p90.value),
        "age_write_down_pct_180": float(p180.value),
        "pct_used": pct_used,
        "target_cumulative_write_down": _money(target),
        "amount": amount,
        "book_value_after": _money(before - amount),
        "as_of": _iso(as_of),
    }
    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R03",
        rule_version=RULE_VERSION,
        subject_type="device",
        subject_id=serial,
        outcome=outcome,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=None,
        value_at_stake_eur=amount,
        due_date=None,
    )


# --------------------------------------------------------------------------------------
# R04 grade A as replacement
# --------------------------------------------------------------------------------------


def decide_grade_a_replacement(
    *,
    serial: str,
    family: str,
    grade_inspected: str | None,
    months_since_launch: float,
    storage_gb: int,
    wipe_certificate: bool | None,
    thr: Thresholds,
    as_of: date,
    run_id: str,
) -> DecisionRecord:
    """R04: may a returned device go back out as a replacement for a broken one?

    Conditions in order: inspected grade A; months since launch at most
    ``replacement_max_months_since_launch[family]`` (equality eligible); storage at least
    ``replacement_min_storage_gb[family]``; a wipe certificate when ``replacement_requires_wipe``.
    ``outcome_detail`` names the first failing condition and the record carries its threshold.
    """
    max_months = thr.get("replacement_max_months_since_launch", family, as_of=as_of)
    min_storage = thr.get("replacement_min_storage_gb", family, as_of=as_of)
    requires_wipe = thr.get("replacement_requires_wipe", as_of=as_of)

    months = float(months_since_launch)
    storage = int(storage_gb)
    wipe_ok = (not bool(requires_wipe.value)) or (wipe_certificate is True)

    checks: list[tuple[bool, ResolvedThreshold, str]] = [
        (grade_inspected == "A", max_months, f"grade {grade_inspected or 'unknown'} is not A"),
        (
            months <= float(max_months.value) + EPS,
            max_months,
            f"{months:.1f} months since launch is above {max_months.value} for {family}",
        ),
        (
            storage >= int(min_storage.value),
            min_storage,
            f"{storage} GB storage is below {min_storage.value} GB for {family}",
        ),
        (wipe_ok, requires_wipe, "no wipe certificate on file"),
    ]

    failing = next(((t, msg) for ok, t, msg in checks if not ok), None)
    if failing is None:
        outcome = "eligible_as_replacement"
        fired: ResolvedThreshold = max_months
        detail = (
            f"grade A, {months:.1f} months since launch (max {max_months.value}), {storage} GB "
            f"(min {min_storage.value}), wipe certificate "
            f"{'present' if wipe_certificate else 'not required'}: eligible as replacement"
        )
        first_failing = None
    else:
        outcome = "not_eligible"
        fired, first_failing = failing
        detail = f"not eligible as replacement: {first_failing}"

    inputs: dict[str, Any] = {
        "serial": serial,
        "family": family,
        "grade_inspected": grade_inspected,
        "months_since_launch": round(months, 3),
        "storage_gb": storage,
        "wipe_certificate": wipe_certificate,
        "replacement_max_months_since_launch": float(max_months.value),
        "replacement_min_storage_gb": int(min_storage.value),
        "replacement_requires_wipe": bool(requires_wipe.value),
        "first_failing_condition": first_failing,
        "as_of": _iso(as_of),
    }
    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R04",
        rule_version=RULE_VERSION,
        subject_type="device",
        subject_id=serial,
        outcome=outcome,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=None,
        value_at_stake_eur=None,
        due_date=None,
    )


# --------------------------------------------------------------------------------------
# R05 price protection claim reminder
# --------------------------------------------------------------------------------------


def decide_price_protection_reminder(
    *,
    po_number: str,
    supplier: str,
    has_price_protection: bool,
    price_protection_days: int | None,
    price_drop_date: date | None,
    price_drop_amount: float | None,
    qty_delivered: int,
    thr: Thresholds,
    as_of: date,
    run_id: str,
) -> DecisionRecord:
    """R05: remind the category manager before a price-protection claim window closes.

    Without a clause, a drop date or a window length -> ``no_action``. Otherwise
    ``claim_value = price_drop_amount x qty_delivered``, ``claim_deadline = price_drop_date +
    price_protection_days``, ``days_left = claim_deadline - as_of``. Below
    ``price_protection_min_claim_eur`` -> ``below_min_claim``; negative days -> ``claim_window_expired``;
    days_left at most ``price_protection_reminder_days`` -> ``claim_reminder`` (equality reminds);
    else ``claim_open``. The tool reminds; it never files the claim.
    """
    reminder = thr.get("price_protection_reminder_days", as_of=as_of)
    min_claim = thr.get("price_protection_min_claim_eur", as_of=as_of)

    inputs: dict[str, Any] = {
        "po_number": po_number,
        "supplier": supplier,
        "has_price_protection": bool(has_price_protection),
        "price_protection_days": price_protection_days,
        "price_drop_date": _iso(price_drop_date),
        "price_drop_amount": _money(price_drop_amount),
        "qty_delivered": int(qty_delivered),
        "price_protection_reminder_days": int(reminder.value),
        "price_protection_min_claim_eur": float(min_claim.value),
        "as_of": _iso(as_of),
    }

    if not has_price_protection or price_drop_date is None or price_protection_days is None:
        reason = (
            "no price protection clause on the supplier contract"
            if not has_price_protection
            else "no price drop recorded on the PO"
            if price_drop_date is None
            else "no claim window length on the supplier contract"
        )
        inputs.update({"claim_value_eur": None, "claim_deadline": None, "days_left": None})
        return build_record(
            run_id=run_id,
            as_of=as_of,
            rule_id="R05",
            rule_version=RULE_VERSION,
            subject_type="purchase_order",
            subject_id=po_number,
            outcome="no_action",
            outcome_detail=f"{reason}; nothing to claim for {po_number} at {supplier}",
            thr=reminder,
            inputs=inputs,
            advisory=None,
            value_at_stake_eur=None,
            due_date=None,
        )

    claim_value = round(float(price_drop_amount or 0.0) * int(qty_delivered), 2)
    claim_deadline = price_drop_date + timedelta(days=int(price_protection_days))
    days_left = days_between(as_of, claim_deadline)
    inputs.update(
        {"claim_value_eur": claim_value, "claim_deadline": _iso(claim_deadline), "days_left": days_left}
    )

    if claim_value < float(min_claim.value) - EPS:
        outcome = "below_min_claim"
        fired: ResolvedThreshold = min_claim
        detail = (
            f"claim of {_eur(claim_value)} on {po_number} at {supplier} is below the "
            f"{_eur(min_claim.value)} minimum; not worth the paperwork"
        )
    elif days_left < 0:
        outcome = "claim_window_expired"
        fired = reminder
        detail = (
            f"claim window for {_eur(claim_value)} on {po_number} at {supplier} closed on "
            f"{claim_deadline.isoformat()} ({-days_left} days ago)"
        )
    elif days_left <= int(reminder.value):
        outcome = "claim_reminder"
        fired = reminder
        detail = (
            f"claim {_eur(claim_value)} on {po_number} at {supplier} within {days_left} days, "
            f"window closes {claim_deadline.isoformat()} (reminder at {reminder.value} days)"
        )
    else:
        outcome = "claim_open"
        fired = reminder
        detail = (
            f"claim {_eur(claim_value)} on {po_number} at {supplier} open for {days_left} more days, "
            f"window closes {claim_deadline.isoformat()}"
        )

    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R05",
        rule_version=RULE_VERSION,
        subject_type="purchase_order",
        subject_id=po_number,
        outcome=outcome,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=None,
        value_at_stake_eur=claim_value,
        due_date=claim_deadline,
    )


# --------------------------------------------------------------------------------------
# R06 renewal alert
# --------------------------------------------------------------------------------------


def decide_renewal_alert(
    *,
    contract_type: str,
    contract_id: str,
    counterparty: str,
    end_date: date,
    notice_days: int | None,
    auto_renewal: bool,
    annual_value_eur: float,
    thr: Thresholds,
    as_of: date,
    run_id: str,
) -> DecisionRecord:
    """R06: alert before the notice deadline of a supplier or rental contract.

    ``notice_deadline = end_date - notice_days``, ``d = notice_deadline - as_of``.
    Ended contract -> ``expired``; deadline passed with auto-renewal -> ``notice_missed_auto_renews``;
    deadline passed without -> ``expiring_no_notice_possible``; ``d`` at most
    ``renewal_alert_lead_days`` -> ``renewal_alert`` or ``renewal_alert_high_value`` when the annual
    value reaches ``renewal_high_value_eur``; else ``no_action``.

    ``notice_days`` is the supplier contract's own field. For a ``rental_contract`` the notice
    period is the threshold ``rental_notice_days`` (owner Head of Customer Success), which the
    rule reads itself when ``notice_days`` is ``None`` and always records in ``inputs`` and in
    ``thresholds_consulted``; a supplier contract without a notice period raises.
    """
    if contract_type not in VALID_CONTRACT_TYPES:
        raise ValueError(f"R06 {contract_id}: contract_type must be one of {VALID_CONTRACT_TYPES}")
    lead = thr.get("renewal_alert_lead_days", as_of=as_of)
    high = thr.get("renewal_high_value_eur", as_of=as_of)
    consulted = [lead, high]
    notice_source = "contract_field"
    rental_notice: ResolvedThreshold | None = None
    if contract_type == "rental_contract":
        rental_notice = thr.get("rental_notice_days", as_of=as_of)
        consulted.append(rental_notice)
        if notice_days is None:
            notice_days = int(rental_notice.value)
            notice_source = "threshold rental_notice_days"
    if notice_days is None:
        raise ValueError(f"R06 {contract_id}: a supplier contract needs notice_days")

    notice_deadline = end_date - timedelta(days=int(notice_days))
    d = days_between(as_of, notice_deadline)
    value = float(annual_value_eur or 0.0)

    inputs: dict[str, Any] = {
        "contract_type": contract_type,
        "contract_id": contract_id,
        "counterparty": counterparty,
        "end_date": _iso(end_date),
        "notice_days": int(notice_days),
        "notice_days_source": notice_source,
        "auto_renewal": bool(auto_renewal),
        "annual_value_eur": _money(value),
        "notice_deadline": _iso(notice_deadline),
        "days_to_notice_deadline": d,
        "days_to_end": days_between(as_of, end_date),
        "renewal_alert_lead_days": int(lead.value),
        "renewal_high_value_eur": float(high.value),
        "thresholds_consulted": _consulted(*consulted),
        "as_of": _iso(as_of),
    }
    if rental_notice is not None:
        inputs["rental_notice_days"] = int(rental_notice.value)
        inputs["rental_notice_days_owner"] = rental_notice.owner

    label = f"{contract_type.replace('_', ' ')} {contract_id} with {counterparty}"
    fired: ResolvedThreshold = lead
    if end_date < as_of:
        outcome = "expired"
        detail = f"{label} ended on {end_date.isoformat()}; nothing to renew"
    elif d < 0 and auto_renewal:
        outcome = "notice_missed_auto_renews"
        detail = (
            f"{label}: notice deadline {notice_deadline.isoformat()} passed {-d} days ago and the "
            f"contract auto-renews at {_eur(value)} per year"
        )
    elif d < 0:
        outcome = "expiring_no_notice_possible"
        detail = (
            f"{label}: notice deadline {notice_deadline.isoformat()} passed {-d} days ago, contract "
            f"runs out on {end_date.isoformat()} without renewal"
        )
    elif d <= int(lead.value):
        if value >= float(high.value) - EPS:
            outcome = "renewal_alert_high_value"
            fired = high
            detail = (
                f"{label}: notice deadline {notice_deadline.isoformat()} in {d} days, annual value "
                f"{_eur(value)} at or above {_eur(high.value)}; escalate"
            )
        else:
            outcome = "renewal_alert"
            detail = (
                f"{label}: notice deadline {notice_deadline.isoformat()} in {d} days "
                f"(alert lead {lead.value} days), annual value {_eur(value)}"
            )
    else:
        outcome = "no_action"
        detail = (
            f"{label}: notice deadline {notice_deadline.isoformat()} in {d} days, beyond the "
            f"{lead.value} day alert lead"
        )

    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R06",
        rule_version=RULE_VERSION,
        subject_type=contract_type,
        subject_id=contract_id,
        outcome=outcome,
        outcome_detail=detail,
        thr=fired,
        inputs=inputs,
        advisory=None,
        value_at_stake_eur=_money(value),
        due_date=notice_deadline,
    )


# --------------------------------------------------------------------------------------
# R07 purchase discount floor (v0.2, spec section 7.4)
# --------------------------------------------------------------------------------------


def decide_purchase_floor(
    *,
    po_line_id: str,
    oem: str,
    supplier_name: str,
    supplier_role: str,
    unit_price_eur: float,
    rrp_net_eur: float,
    qty: int,
    thr: Thresholds,
    as_of: date,
    run_id: str,
) -> DecisionRecord:
    """R07: queue a PO line whose discount vs the net launch RRP is below the manufacturer's floor.

    ``discount = 1 - unit_price_eur / rrp_net_eur``; ``floor = purchase_discount_floor_pct[oem]``.
    ``discount >= floor`` (equality passes) -> ``at_or_above_floor`` (logged, not queued);
    else ``below_floor_discount`` with ``value_at_stake_eur = (unit_price - floor_price) x qty``,
    ``floor_price = rrp_net x (1 - floor)``: the money paid above the floor on the whole line.
    Sign choice, stated here because the spec writes the difference the other way round: the
    stake is positive when the rule fires, so the queue (sorted by stake, largest first) ranks
    the most expensive line highest; it is the magnitude of the spec's expression. Subject is
    the PO line (``"<po_number>-<po_line>"``). The tool queues the line for the Head of
    Procurement; it never claims, re-negotiates or orders anything.
    """
    floor = thr.get("purchase_discount_floor_pct", oem, as_of=as_of)
    unit = float(unit_price_eur)
    rrp = float(rrp_net_eur)
    if rrp <= 0:
        raise ValueError(f"R07 {po_line_id}: rrp_net_eur must be positive")
    discount = 1.0 - unit / rrp
    floor_pct = float(floor.value)
    floor_price = rrp * (1.0 - floor_pct)
    n = int(qty)

    inputs: dict[str, Any] = {
        "po_line_id": po_line_id,
        "oem": oem,
        "supplier_name": supplier_name,
        "supplier_role": supplier_role,
        "unit_price_eur": _money(unit),
        "rrp_net_eur": _money(rrp),
        "qty": n,
        "discount_pct": round(discount, 4),
        "purchase_discount_floor_pct": floor_pct,
        "floor_price_eur": _money(floor_price),
        "thresholds_consulted": _consulted(floor),
        "as_of": _iso(as_of),
    }
    if discount >= floor_pct - EPS:
        outcome = "at_or_above_floor"
        detail = (
            f"discount {discount * 100:.1f} % vs floor {floor_pct * 100:.1f} % for {oem} on "
            f"{po_line_id} at {supplier_name}; at or above the floor"
        )
        value_at_stake: float | None = None
    else:
        outcome = "below_floor_discount"
        detail = (
            f"discount {discount * 100:.1f} % vs floor {floor_pct * 100:.1f} % for {oem} on "
            f"{po_line_id} at {supplier_name} ({supplier_role}): {_eur(unit)} paid per unit against a "
            f"floor price of {_eur(floor_price)}, {n} unit(s)"
        )
        value_at_stake = _money((unit - floor_price) * n)

    return build_record(
        run_id=run_id,
        as_of=as_of,
        rule_id="R07",
        rule_version=RULE_VERSION,
        subject_type="purchase_order",
        subject_id=po_line_id,
        outcome=outcome,
        outcome_detail=detail,
        thr=floor,
        inputs=inputs,
        advisory=None,
        value_at_stake_eur=value_at_stake,
        due_date=None,
    )


__all__ = [
    "RULE_VERSION",
    "EPS",
    "NO_ACTION_OUTCOMES",
    "PRIORITY_1_OUTCOMES",
    "SALE_CHANNELS",
    "decide_repair",
    "decide_channel",
    "decide_aging_write_down",
    "decide_grade_a_replacement",
    "decide_price_protection_reminder",
    "decide_renewal_alert",
    "decide_purchase_floor",
]
