"""The where-to-tighten table and the cohort view (spec v0.2 section 7.3), plus docs/LEVERS.md.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``levers_summary`` produces one row per lever: how many serials were eligible and attributed,
the mean and p90 EUR per device, the EUR per year on the fleet (trailing 12 months by the
lever's event date; L07 counted once per cohort), that sum as a share of the lever's own
basis (``share_of_lever_basis`` over ``lever_basis_eur``, named in ``lever_basis``), the
threshold that the reacting rule reads (key, value, unit, owner resolved through
``Thresholds.get`` at run time), the rule or advisory that reacts, the assumption the
lever's arithmetic rests on and its owner (``reference_key``, ``reference_owner``), the
reference sentence and a rank. No total row is ever produced: the levers sit on
overlapping components and do not add up, and no column relates a lever to the fleet's
loss as if it explained it.

Choices where the spec is silent:

* The trailing window is ``as_of - 365 days < event_date <= as_of``.
* p90 is pandas ``quantile(0.90)`` (linear interpolation) over attributed serials.
* ``share_of_lever_basis`` is measured on the population the lever measures: for L01 and
  L02 the landed cost of the same purchases in the window; for L03 to L07 the absolute
  closed result of the same serials in the window (closed serials only, so in-stock L05
  rows are in the EUR but not in the share). It replaces the spec's
  ``share_of_closed_loss`` (one denominator for seven levers on different populations,
  whose shares summed to several hundred percent and read as an attribution).
* For a per-oem or per-family threshold the sub key is the oem (family) with the largest
  ``eur_fleet_per_year`` among attributed rows; when no row is attributed the first key of
  the threshold's ``values`` map is shown, so the owner is still named.
* A lever with no attributed row keeps NULL EUR columns and ranks last (ties by lever id).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from restwert.config import Assumptions, Thresholds
from restwert.levers.attribution import LEVERS, LeverSpec
from restwert.levers.references import to_date, to_float

#: Columns of ``gold.levers_summary`` (DDL order).
SUMMARY_COLUMNS: tuple[str, ...] = (
    "lever_id",
    "lever_name",
    "component",
    "basis",
    "additive",
    "n_eligible",
    "n_attributed",
    "eur_per_device",
    "eur_per_device_p90",
    "eur_fleet_per_year",
    "share_of_lever_basis",
    "lever_basis_eur",
    "lever_basis",
    "threshold_key",
    "threshold_value",
    "threshold_unit",
    "threshold_owner",
    "rule_id",
    "reference_key",
    "reference_owner",
    "reference_sentence",
    "rank",
    "as_of",
)

#: The denominator of ``share_of_lever_basis`` per lever: the population the lever measures.
LEVER_BASIS: dict[str, str] = {
    "L01": "landed cost of the same purchases in the window",
    "L02": "landed cost of the same purchases in the window",
    "L03": "absolute closed result of the same serials in the window",
    "L04": "absolute closed result of the same serials in the window",
    "L05": "absolute closed result of the same serials in the window (in-stock rows in the EUR, not in the share)",
    "L06": "absolute closed result of the same serials in the window",
    "L07": "absolute closed result of the same serials in the window (EUR counted once per cohort)",
}

#: Columns of ``gold.levers_by_cohort``.
COHORT_COLUMNS: tuple[str, ...] = (
    "cohort_kind",
    "cohort_value",
    "lever_id",
    "n_attributed",
    "sum_delta_eur",
    "mean_delta_eur",
    "as_of",
)

DEFAULT_COHORT_KINDS: tuple[str, ...] = (
    "oem",
    "catalogue_family",
    "model_family",
    "term_months",
    "purchase_quarter",
    "resale_channel",
)

#: cohort kind -> device_ledger column
_COHORT_SOURCE: dict[str, str] = {
    "oem": "oem",
    "catalogue_family": "catalogue_family",
    "model_family": "model_family",
    "term_months": "term_months",
    "purchase_quarter": "cohort_quarter",
    "purchase_month": "cohort_month",
    "resale_channel": "resale_channel",
    "supplier_role": "supplier_role",
    "customer_id": "customer_id",
}

TRAILING_DAYS = 365


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _window_mask(event_dates: pd.Series, as_of: date) -> pd.Series:
    ev = pd.to_datetime(event_dates, errors="coerce")
    end = pd.Timestamp(as_of)
    start = end - pd.Timedelta(days=TRAILING_DAYS)
    return (ev > start) & (ev <= end)


def _attributed(per_device: pd.DataFrame) -> pd.DataFrame:
    if per_device is None or len(per_device) == 0:
        return pd.DataFrame(columns=["serial", "lever_id", "delta_eur", "event_date", "is_attributed"])
    f = per_device[per_device["is_attributed"].astype(bool)].copy()
    f["delta_eur"] = pd.to_numeric(f["delta_eur"], errors="coerce").astype(float)
    return f[f["delta_eur"].notna()]


# --------------------------------------------------------------------------------------
# cohorts
# --------------------------------------------------------------------------------------


def levers_by_cohort(
    per_device: pd.DataFrame,
    dl: pd.DataFrame,
    kinds: tuple[str, ...] = DEFAULT_COHORT_KINDS,
    as_of: date | None = None,
) -> pd.DataFrame:
    """``gold.levers_by_cohort``: attributed deltas summed and averaged per (cohort kind, value, lever)."""
    empty = pd.DataFrame(columns=list(COHORT_COLUMNS))
    att = _attributed(per_device)
    if len(att) == 0 or dl is None or len(dl) == 0:
        return empty
    if as_of is None and "as_of" in att.columns:
        as_of = to_date(att["as_of"].iloc[0])
    keep = ["serial"] + [c for c in {_COHORT_SOURCE.get(k, k) for k in kinds} if c in dl.columns]
    keys = dl[keep].copy()
    keys["serial"] = keys["serial"].astype(str)
    att["serial"] = att["serial"].astype(str)
    merged = att.merge(keys, on="serial", how="left")
    frames: list[pd.DataFrame] = []
    for kind in kinds:
        col = _COHORT_SOURCE.get(kind, kind)
        if col not in merged.columns:
            continue
        sub = merged[merged[col].notna()].copy()
        if len(sub) == 0:
            continue
        if col == "cohort_month":
            sub["_cohort"] = pd.to_datetime(sub[col], errors="coerce").dt.strftime("%Y-%m")
        elif col == "term_months":
            sub["_cohort"] = pd.to_numeric(sub[col], errors="coerce").astype("Int64").astype(str)
        else:
            sub["_cohort"] = sub[col].astype(str)
        g = sub.groupby(["_cohort", "lever_id"])["delta_eur"].agg(["count", "sum", "mean"]).reset_index()
        g.columns = ["cohort_value", "lever_id", "n_attributed", "sum_delta_eur", "mean_delta_eur"]
        g.insert(0, "cohort_kind", kind)
        frames.append(g)
    if not frames:
        return empty
    out = pd.concat(frames, ignore_index=True)
    out["n_attributed"] = out["n_attributed"].astype(int)
    out["sum_delta_eur"] = out["sum_delta_eur"].round(2)
    out["mean_delta_eur"] = out["mean_delta_eur"].round(2)
    out["as_of"] = pd.Timestamp(as_of) if as_of is not None else pd.NaT
    return out[list(COHORT_COLUMNS)].sort_values(["cohort_kind", "cohort_value", "lever_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------------------


def _sub_key_with_largest_yearly_eur(
    window_rows: pd.DataFrame, dl: pd.DataFrame, sub_col: str, thr: Thresholds, key: str
) -> str | None:
    """The oem (or family) whose attributed rows carry the largest trailing-12-month EUR."""
    spec = thr.thresholds.get(key)
    candidates = list(spec.values) if spec is not None and spec.values is not None else []
    if len(window_rows) and sub_col in dl.columns:
        keys = dl[["serial", sub_col]].copy()
        keys["serial"] = keys["serial"].astype(str)
        m = window_rows.merge(keys, on="serial", how="left")
        m = m[m[sub_col].notna()]
        if len(m):
            by = m.groupby(m[sub_col].astype(str))["delta_eur"].sum().sort_values(ascending=False)
            for sub in by.index:
                if not candidates or sub in candidates:
                    return str(sub)
    return candidates[0] if candidates else None


def _resolve_threshold(spec: LeverSpec, sub: str | None, thr: Thresholds, as_of: date) -> dict[str, str]:
    """threshold key, value, unit and owner through ``Thresholds.get`` (never a hard-coded owner)."""
    try:
        t = thr.get(spec.threshold_key, sub, as_of=as_of)
        value = _fmt_value(t.value)
        if sub is not None:
            value = f"{sub}: {value}"
        return {"threshold_key": t.resolved_key, "threshold_value": value, "threshold_unit": t.unit, "threshold_owner": t.owner}
    except (KeyError, ValueError) as exc:
        return {
            "threshold_key": spec.threshold_key,
            "threshold_value": f"unresolved: {exc}",
            "threshold_unit": "n/a",
            "threshold_owner": "MISSING IN thresholds.yaml",
        }


def _reference_owner(spec: LeverSpec, a: Assumptions | None) -> tuple[str, str]:
    """``(reference_key, reference_owner)``: the assumption the lever's arithmetic rests on and who owns it."""
    if spec.reference_key is None:
        return "n/a", "the serial's own data"
    if a is None:
        try:
            from restwert.config import load_assumptions

            a = load_assumptions()
        except Exception:  # noqa: BLE001 - the owner is then reported as unknown, never invented
            return spec.reference_key, "MISSING IN assumptions.yaml"
    try:
        return spec.reference_key, str(a.owner(spec.reference_key))
    except KeyError:
        return spec.reference_key, "MISSING IN assumptions.yaml"


def _cohort_key_of(counterfactual_json: str) -> str | None:
    """The L07 cohort key ``model_family|purchase_half_year|this_term`` from the record."""
    import json

    try:
        cf = json.loads(counterfactual_json or "{}")
    except (TypeError, ValueError):
        return None
    if cf.get("model_family") is None or cf.get("purchase_half_year") is None:
        return None
    return f"{cf.get('model_family')}|{cf.get('purchase_half_year')}|{cf.get('this_term')}"


def _fleet_eur(win: pd.DataFrame, spec: LeverSpec) -> float:
    """EUR per year of the window rows: a sum per serial, or once per cohort for ``summary_basis = per_cohort``."""
    if len(win) == 0:
        return 0.0
    if spec.summary_basis == "per_cohort" and "counterfactual_json" in win.columns:
        keyed = win.assign(_cohort=win["counterfactual_json"].map(_cohort_key_of))
        keyed = keyed[keyed["_cohort"].notna()]
        once = keyed.drop_duplicates("_cohort")
        return float(once["delta_eur"].sum())
    return float(win["delta_eur"].sum())


def _lever_basis_eur(win: pd.DataFrame, dl: pd.DataFrame, lever_id: str) -> tuple[float, float]:
    """``(eur in the share's numerator, basis eur)`` on the population the lever measures."""
    if len(win) == 0 or dl is None or len(dl) == 0:
        return 0.0, 0.0
    keys = dl[["serial"] + [c for c in ("landed_cost", "lifecycle_result_eur", "is_closed") if c in dl.columns]].copy()
    keys["serial"] = keys["serial"].astype(str)
    m = win.merge(keys, on="serial", how="left")
    if lever_id in ("L01", "L02"):
        basis = pd.to_numeric(m.get("landed_cost"), errors="coerce").fillna(0.0)
        return float(m["delta_eur"].sum()), float(basis.sum())
    closed = m.get("is_closed")
    closed = closed.fillna(False).astype(bool) if closed is not None else pd.Series(False, index=m.index)
    res = pd.to_numeric(m.get("lifecycle_result_eur"), errors="coerce")
    keep = closed & res.notna()
    return float(m.loc[keep, "delta_eur"].sum()), float(res[keep].abs().sum())


def levers_summary(
    per_device: pd.DataFrame, dl: pd.DataFrame, thr: Thresholds, as_of: date, a: Assumptions | None = None
) -> pd.DataFrame:
    """``gold.levers_summary``: the where-to-tighten table, one row per lever, ranked, never a total."""
    att = _attributed(per_device)
    if len(att):
        att["serial"] = att["serial"].astype(str)
        att["_in_window"] = _window_mask(att["event_date"], as_of)

    rows: list[dict[str, Any]] = []
    for lever_id, spec in LEVERS.items():
        pdv = per_device[per_device["lever_id"] == lever_id] if per_device is not None and len(per_device) else pd.DataFrame()
        n_eligible = int(len(pdv))
        sub_att = att[att["lever_id"] == lever_id] if len(att) else pd.DataFrame()
        n_att = int(len(sub_att))
        eur_dev = float(sub_att["delta_eur"].mean()) if n_att else None
        p90 = float(sub_att["delta_eur"].quantile(0.90)) if n_att else None
        win = sub_att[sub_att["_in_window"]] if n_att else pd.DataFrame()
        eur_year = _fleet_eur(win, spec) if len(win) else (0.0 if n_att else None)
        num, basis_eur = _lever_basis_eur(win, dl, lever_id) if len(win) else (0.0, 0.0)
        if spec.summary_basis == "per_cohort" and len(win):
            num = float(eur_year or 0.0)
        share = (num / basis_eur) if basis_eur > 0 else None

        sub_key: str | None = None
        if spec.threshold_sub is not None:
            sub_key = _sub_key_with_largest_yearly_eur(win if len(win) else sub_att, dl, spec.threshold_sub, thr, spec.threshold_key)
        ref_key, ref_owner = _reference_owner(spec, a)
        rows.append(
            {
                "lever_id": lever_id,
                "lever_name": spec.name,
                "component": spec.component,
                "basis": spec.basis,
                "additive": bool(spec.additive),
                "n_eligible": n_eligible,
                "n_attributed": n_att,
                "eur_per_device": None if eur_dev is None else round(eur_dev, 2),
                "eur_per_device_p90": None if p90 is None else round(p90, 2),
                "eur_fleet_per_year": None if eur_year is None else round(eur_year, 2),
                "share_of_lever_basis": share,
                "lever_basis_eur": round(basis_eur, 2) if len(win) else None,
                "lever_basis": LEVER_BASIS[lever_id],
                **_resolve_threshold(spec, sub_key, thr, as_of),
                "rule_id": spec.rule_id,
                "reference_key": ref_key,
                "reference_owner": ref_owner,
                "reference_sentence": spec.reference_sentence,
                "as_of": as_of,
            }
        )
    out = pd.DataFrame(rows, columns=[c for c in SUMMARY_COLUMNS if c != "rank"])
    out["_sort"] = pd.to_numeric(out["eur_fleet_per_year"], errors="coerce").fillna(-np.inf)
    out = out.sort_values(["_sort", "lever_id"], ascending=[False, True]).drop(columns=["_sort"]).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1, dtype=int)
    out["as_of"] = pd.to_datetime(out["as_of"])
    for c in ("eur_per_device", "eur_per_device_p90", "eur_fleet_per_year", "share_of_lever_basis", "lever_basis_eur"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    return out[list(SUMMARY_COLUMNS)]


# --------------------------------------------------------------------------------------
# docs/LEVERS.md
# --------------------------------------------------------------------------------------

_FORMULAS: dict[str, str] = {
    "L01": "`(d_ref - discount_vs_rrp_pct) x rrp_net`, floored at 0, with the purchase price net of the price protection credit received; `d_ref` = p75 of `discount_vs_rrp_pct` over the fleet group (oem, supplier role, purchase half-year) with n >= min_n, else the oem over all time with n >= min_n, else not attributed",
    "L02": "`price_protection_claimable_eur` when `price_protection_status = missed`; 0 when claimed or not applicable (reference `claimed` / `n/a`); an open window is not attributed",
    "L03": "`net[best admissible channel] - net[actual channel]`, floored at 0, with `net_c = rv_of_record x channel_factor_c x (1 - fee_pct_c) - fee_fixed_c - holding_per_day x days_to_cash_c` for EVERY channel, the actual one included (the R02 filter decides admissibility: grade, buyout window after the effective contract end, maximum days to cash, never the last channel); zero when the best channel was used. What the actual channel realised against the record (`realised_vs_record_gap_eur` in the record) is forecast accuracy and belongs to `KPI_RSL_REALISED_VS_RECORD`, never to the lever",
    "L04": "grade part `purchase_price x (grid(model, grade_declared, m_ret) - grid(model, grade_inspected, m_ret))` at the months since launch at return, NOT floored, read only where the grid orders the grades (A >= B >= C >= D at that model and month) and neither grade is unsupported (`fit_quality = unsupported_grade`, the As-Is fallback); otherwise the lever is not attributed and the record says why; plus repair part `sum over repair lines of max(0, repair - repair_max_share_of_rv[family] x purchase_price x grid(model, grade_used, m_repair))`",
    "L05": "`excess_days x holding_per_day + purchase_price x max(0, grid(grade_out, m_expected) - grid(grade_out, m_actual))`; `excess_days = max(0, days_sellable_to_sold - expected_return_to_sale_days[family])`; in-stock devices measured at as_of",
    "L06": "`(median_ratio_family_bucket_grade - resale_gross / rrp_net) x rrp_net`, NOT floored; the median runs over the fleet's own sold devices of the same (catalogue family, 6-month age bucket at sale, grade at sale) with n >= 2 x min_n; summarised by oem",
    "L07": "`(median(result / term_months) of the best other term - the same of this term) x this term's months` inside (model_family, purchase half-year) over the terms 12, 24, 36 and 48 months, this term and the other term n >= min_n, floored at 0; per month of term so the longer term is not credited with its extra months of rent; the same value on every serial of the cohort and counted ONCE PER COHORT in the summary: a policy comparison, not money per device",
}

_ELIGIBLE: dict[str, str] = {
    "L01": "every received serial",
    "L02": "every received serial (status decides the value)",
    "L03": "sold serials with a forecast of record",
    "L04": "closed serials with a return",
    "L05": "sold serials and in-stock serials",
    "L06": "sold serials",
    "L07": "closed serials",
}

#: One hand serial, worked through every lever (the numbers are hand-picked, not from any data).
_WORKED_EXAMPLE = """## Worked example: one hand serial

Serial `SN-EXAMPLE`, an android_like smartphone: net RRP 700.00, purchase price 595.00
(discount 15.0 %), 24-month term, returned declared B and inspected C, one repair line of
120.00 at month 20, refurbished to grade C, sellable on day 0, sold on day 50 through the
marketplace at 210.00 gross with 27.70 fees (net 182.30), credit note 28 days after the sale.
Assumptions: holding cost 0.30 per day, marketplace fee 12 % + 2.50, 28 days to cash,
expected return-to-sale days 35, `repair_max_share_of_rv[android_like]` 0.35. Grid ratios at
the months in question: grade B 0.40 and grade C 0.32 at return (month 26); grade C 0.32 at
month 20; grade C 0.31 at the expected sale month and 0.30 at the actual sale month.

| lever | reference | arithmetic | delta_eur |
|---|---|---|---|
| L01 | p75 discount of the group = 0.20 (n = 40); no price protection credit received | reference price 700 x 0.80 = 560.00; 595.00 - 560.00 | 35.00 |
| L02 | status `missed`, claimable 25.00 | the credit the PO line was entitled to | 25.00 |
| L03 | forecast of record 260.00, channel factors buyout 0.90 and b2b 0.97, fees buyout 0 %, marketplace 12 % + 2.50, b2b 3 %; admissible: employee_buyout, marketplace, b2b_wholesale; sold on the marketplace | nets at the record: buyout 260 x 0.90 - 0.30 x 14 = 229.80; marketplace 260 x 0.88 - 2.50 - 0.30 x 28 = 217.90; b2b 260 x 0.97 x 0.97 - 0.30 x 45 = 231.13; 231.13 - 217.90 (the realised 210.00 against the record 260.00 is forecast accuracy, not in the lever) | 13.23 |
| L04 | grid at return: A 0.46, B 0.40, C 0.32, D 0.15 (ordered, none unsupported); repair limit 0.35 x 595 x 0.32 = 66.64 | grade part 595 x (0.40 - 0.32) = 47.60; repair part max(0, 120.00 - 66.64) = 53.36 | 100.96 |
| L05 | expected 35 days | excess 15 days x 0.30 = 4.50; value part 595 x max(0, 0.31 - 0.30) = 5.95 | 10.45 |
| L06 | family median ratio 0.33 (n = 25) at bucket 24-30, grade C | (0.33 - 210 / 700) x 700 = (0.33 - 0.30) x 700 | 21.00 |
| L07 | best other term of the cohort (12, 24, 36 or 48 months, by median result per month): the 36-month cohort median result per month 40.00 / 36 = 1.1111 vs this serial's 24-month median -5.00 / 24 = -0.2083 (n = 12 and 15) | (1.1111 - (-0.2083)) x 24, floored at 0; counted once for the cohort in the summary | 31.67 |

These seven numbers do not add up to one figure: L01 and L02 sit on disjoint purchase
components and are additive; L03 to L07 overlap on the resale line and the grid, and each is
read on its own against its named reference.
"""


def render_levers_md(thr: Thresholds) -> str:
    """Render ``docs/LEVERS.md`` from ``LEVERS`` and the thresholds file (owners read at render time)."""
    lines: list[str] = []
    lines.append("# Levers: where to tighten, and who owns the screw")
    lines.append("")
    lines.append("> THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.")
    lines.append("")
    lines.append(
        "A lever is actual minus a named reference on one ledger component, in EUR per device, "
        "with `delta_eur >= 0` meaning money left on the table. The reference is a row set of the "
        "fleet itself (a percentile or a median of the provider's own devices), the forecast of "
        "record, or the forecast grid. Never an external benchmark. **A lever is a reference, not "
        "a counterfactual fact**: it says how far a device sits from a stated reference, not what "
        "would have happened."
    )
    lines.append("")
    lines.append(
        "Below `lever_reference_min_n` (`config/assumptions.yaml`, owner CFO) a lever is "
        "`is_attributed = false` with `delta_eur` NULL, never guessed. `counterfactual_json` on every "
        "row stores every input used: the reference group and its n, the two values, and the months "
        "and grades read from the grid. Rendered by `restwert.levers.summary.render_levers_md` on "
        f"`config/thresholds.yaml` version {thr.version}; owners come from that file at render time."
    )
    lines.append("")
    lines.append("## The seven levers")
    lines.append("")
    lines.append("| id | name | component | basis | additive | eligible serials | delta_eur per device |")
    lines.append("|---|---|---|---|---|---|---|")
    for lever_id, spec in LEVERS.items():
        lines.append(
            f"| {lever_id} | {spec.name} | {spec.component} | {spec.basis} | "
            f"{'yes' if spec.additive else 'no'} | {_ELIGIBLE[lever_id]} | {_FORMULAS[lever_id]} |"
        )
    lines.append("")
    lines.append("## Threshold, owner, reacting rule and reference parameter per lever")
    lines.append("")
    lines.append(
        "`rule` is the rule (R01, R02, R03, R05, R07) or advisory (ADV03, ADV04) that REACTS to the "
        "lever's component and the threshold it reads; an advisory never acts, it asks a human. "
        "`reference parameter` is the `config/assumptions.yaml` key the lever's own arithmetic rests "
        "on, with its owner: for L05 that is `expected_return_to_sale_days` (Head of Recommerce), not "
        "`aging_days_90`, which only tells R03 when to write down aged stock."
    )
    lines.append("")
    lines.append("| id | threshold | value | unit | owner | rule | reference parameter | reference |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for lever_id, spec in LEVERS.items():
        tspec = thr.thresholds.get(spec.threshold_key)
        if tspec is None:
            value, unit, owner = "MISSING IN YAML", "", "MISSING IN YAML"
        elif tspec.values is not None:
            value = ", ".join(f"{k}: {_fmt_value(v)}" for k, v in tspec.values.items())
            unit, owner = tspec.unit, tspec.owner
        else:
            value, unit, owner = _fmt_value(tspec.value), tspec.unit, tspec.owner
        ref_key, ref_owner = _reference_owner(spec, None)
        ref_text = f"`{ref_key}` ({ref_owner})" if spec.reference_key else ref_owner
        lines.append(
            f"| {lever_id} | `{spec.threshold_key}` | {value} | {unit} | {owner} | {spec.rule_id} | {ref_text} | "
            f"{spec.reference_sentence} |"
        )
    lines.append("")
    lines.append("## Additivity")
    lines.append("")
    lines.append(
        "Levers do not add up. Only L01 and L02 sit on disjoint components (purchase price and the "
        "price protection credit); for every closed serial the identity "
        "`result_v01_basis + L01 + L02 == the same result with the purchase paid at the (capped) "
        "reference price and the missed credit received` must hold. `check_additivity` evaluates the "
        "right side from `silver.ledger_lines` (every non-bridge line except the purchase, the credit "
        "received netted, the capped reference price paid, the missed credit added) and the left side "
        "from the device ledger's stored basis and the two stored deltas, so a wrong delta, a basis that "
        "drifted from the lines or a purchase price that does not match its invoice line all surface; "
        "without the lines (hand tests) it degrades to a formula consistency test and says so. The run "
        "refuses to report success with a violation; the count `additivity_violations` in the run "
        "summary must be 0. L03 to L07 overlap on the resale line and on the grid and are read one at "
        "a time. `gold.levers_summary` therefore never carries a total row, and the dashboard says so "
        "next to the table."
    )
    lines.append("")
    lines.append("## The where-to-tighten table (`gold.levers_summary`)")
    lines.append("")
    lines.append(
        "Per lever: `n_eligible`, `n_attributed`, `eur_per_device` (mean delta over attributed "
        "serials), `eur_per_device_p90`, `eur_fleet_per_year` (sum of delta over attributed serials "
        "whose event date lies in the trailing 12 months before as_of: purchase date for L01 and L02, "
        "sale date for L03, L05 and L06, closed date for L04 and L07, as_of for in-stock L05 rows; L07 "
        "counted ONCE PER COHORT, not per serial), `share_of_lever_basis` (that sum over "
        "`lever_basis_eur`, the population the lever itself measures and names in `lever_basis`: the "
        "landed cost of the same purchases for L01 and L02, the absolute closed result of the same "
        "serials for L03 to L07; NULL when the basis is zero; the ratio can exceed 1 because a lever "
        "is measured on every attributed serial, not only on the ones that lost money, and it never "
        "says that the lever explains the fleet's loss), the threshold key, value, unit and owner "
        "resolved through `Thresholds.get` (per-oem keys shown for the oem with the largest yearly EUR "
        "as `oem: value`), `rule_id` (the rule or advisory that reacts), `reference_key` and "
        "`reference_owner` (the assumption the arithmetic rests on), the reference sentence and `rank` "
        "by `eur_fleet_per_year`."
    )
    lines.append("")
    lines.append("## Rule R07 and the advisories ADV03 and ADV04")
    lines.append("")
    lines.append(
        "R07 `purchase_discount_floor` is a pure rule on every PO line of the ledger: "
        "`discount = 1 - unit_price / rrp_net`; at or above `purchase_discount_floor_pct[oem]` "
        "nothing happens (`at_or_above_floor`, logged, not queued); below it the line is queued at "
        "priority 2 with `value_at_stake = (unit_price - rrp_net x (1 - floor)) x qty`, positive "
        "when the rule fires (as `restwert.decisions.rules` and docs/DECISION_RULES.md compute it). ADV03 "
        "`manufacturer_mix` asks the category manager to review the allocation when a manufacturer's "
        "mean L06 gap over the trailing 12 months exceeds `oem_realisation_gap_pct`; ADV04 `term_gap` "
        "asks the CFO to review the term policy when a cohort's L07 gap reaches "
        "`term_result_gap_alert_eur`. Both are advisories: priority 3 in the queue, never an outcome."
    )
    lines.append("")
    lines.extend(_WORKED_EXAMPLE.rstrip("\n").split("\n"))
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "SUMMARY_COLUMNS",
    "LEVER_BASIS",
    "COHORT_COLUMNS",
    "DEFAULT_COHORT_KINDS",
    "TRAILING_DAYS",
    "levers_by_cohort",
    "levers_summary",
    "render_levers_md",
]
