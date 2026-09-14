"""The five gold tables of the ledger (SPEC_v0.2 6.5): cohorts, TCO, purchase, anchor, resale.

Every table is deterministic arithmetic over ``silver.device_ledger`` (and the lines or
bronze where stated); ratios are computed from sums, never averaged. The identity

    sum_result_closed = sum_rental_revenue_closed + sum_realised_rv_closed + sum_pp_credit_closed - sum_tco_closed

holds per row of ``gold.result_by_cohort`` because ``lifecycle_result_eur`` is the signed
sum of the lines and ``tco_eur`` is the sum of every cost line. Closed and open columns
are never added into one number: no table has a ``result_total`` column.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from restwert.ledger.device_ledger import TCO_COLUMN_OF
from restwert.ledger.lines import COST_LINE_TYPES, ESTIMATE_LINE_TYPES, BronzeFrames

COHORT_KINDS: tuple[str, ...] = (
    "purchase_month", "purchase_quarter", "oem", "catalogue_family", "model_family",
    "term_months", "resale_channel", "supplier_role", "customer_id",
)
TCO_COHORT_KINDS: tuple[str, ...] = ("catalogue_family", "oem", "model_family", "term_months")

#: cohort kind -> device_ledger column
COHORT_COLUMN: dict[str, str] = {
    "purchase_month": "cohort_month",
    "purchase_quarter": "cohort_quarter",
    "oem": "oem",
    "catalogue_family": "catalogue_family",
    "model_family": "model_family",
    "term_months": "term_months",
    "resale_channel": "resale_channel",
    "supplier_role": "supplier_role",
    "customer_id": "customer_id",
}

RESULT_BY_COHORT_COLUMNS: list[str] = [
    "cohort_kind", "cohort_value", "n", "n_closed", "n_open", "sum_result_closed", "mean_result_closed",
    "result_pct_of_landed_closed", "sum_rental_revenue_closed", "sum_realised_rv_closed", "sum_pp_credit_closed",
    "sum_tco_closed", "sum_landed_closed", "tco_per_closed_device", "rv_per_closed_device", "rent_per_closed_device",
    "sum_liquidation_today_open", "mean_liquidation_today_open", "sum_projected_lease_end_open",
    "mean_projected_lease_end_open", "as_of",
]
TCO_BY_COHORT_COLUMNS: list[str] = ["cohort_kind", "cohort_value", "line_type", "n_devices", "mean_eur", "sum_eur", "estimate_eur", "is_estimate", "as_of"]
PURCHASE_BY_OEM_MONTH_COLUMNS: list[str] = [
    "oem", "purchase_month", "supplier_role", "n_units", "sum_rrp_net", "sum_unit_price", "sum_freight_duty", "sum_landed",
    "discount_vs_rrp_pct", "landed_vs_rrp_pct", "ppv_vs_po_eur", "share_under_contract", "pp_claimable_eur", "pp_credited_eur", "as_of",
]
ESTIMATE_VS_ANCHOR_COLUMNS: list[str] = [
    "catalogue_family", "oem", "n_rented", "n_with_anchor", "sum_estimate_lease_end", "sum_anchor_lease_end",
    "mean_estimate_ratio", "mean_anchor_ratio", "estimate_vs_anchor_ratio", "anchor_curve_group", "anchor_fit_quality", "as_of",
]
RESALE_BY_CHANNEL_GRADE_COLUMNS: list[str] = [
    "channel", "grade_at_sale", "n", "sum_gross", "sum_fees", "sum_net", "sum_refurb", "sum_estimate_of_record",
    "realised_vs_record_ratio", "median_days_return_to_cash", "n_credit_note_missing", "as_of",
]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").astype("float64")


def _r2(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else round(f + 0.0, 2)


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0 or np.isnan(den) or np.isnan(num):
        return None
    return float(num) / float(den)


def _cohort_value(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v)


def _with_cohort(dl: pd.DataFrame, kind: str) -> pd.DataFrame:
    if kind not in COHORT_COLUMN:
        raise KeyError(f"unknown cohort kind {kind!r}; known: {COHORT_KINDS}")
    col = COHORT_COLUMN[kind]
    d = dl.copy()
    d["_cohort"] = d[col].map(_cohort_value) if col in d.columns else None
    return d[d["_cohort"].notna()]


def _nullable(df: pd.DataFrame, bool_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    out = df.astype(object).where(pd.notna(df), None)
    for c in bool_cols:
        out[c] = out[c].astype(bool)
    return out


# --------------------------------------------------------------------------------------
# result by cohort
# --------------------------------------------------------------------------------------

def result_by_cohort(dl: pd.DataFrame, kind: str, as_of: date) -> pd.DataFrame:
    """``gold.result_by_cohort`` rows for one cohort kind; every ratio from sums."""
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=RESULT_BY_COHORT_COLUMNS)
    d = _with_cohort(dl, kind)
    if len(d) == 0:
        return pd.DataFrame(columns=RESULT_BY_COHORT_COLUMNS)
    closed = d["is_closed"].astype(bool)
    d = d.assign(
        _closed=closed,
        _result=_num(d, "lifecycle_result_eur").where(closed),
        _rent=_num(d, "rental_revenue").where(closed),
        _rv=_num(d, "realised_rv").where(closed),
        _pp=_num(d, "price_protection_credit_eur").where(closed),
        _tco=_num(d, "tco_eur").where(closed),
        _landed=_num(d, "landed_cost").where(closed),
        _liq=_num(d, "result_if_liquidated_today").where(~closed),
        _proj=_num(d, "result_projected_at_lease_end").where(~closed),
    )
    rows: list[dict[str, Any]] = []
    for value, g in d.groupby("_cohort", sort=True):
        n = int(len(g))
        n_closed = int(g["_closed"].sum())
        n_open = n - n_closed
        sum_result = float(g["_result"].sum()) if n_closed else None
        sum_rent = float(g["_rent"].sum()) if n_closed else None
        sum_rv = float(g["_rv"].sum()) if n_closed else None
        sum_pp = float(g["_pp"].sum()) if n_closed else None
        sum_tco = float(g["_tco"].sum()) if n_closed else None
        sum_landed = float(g["_landed"].sum()) if n_closed else None
        n_liq = int(g["_liq"].notna().sum())
        n_proj = int(g["_proj"].notna().sum())
        rows.append({
            "cohort_kind": kind,
            "cohort_value": str(value),
            "n": n,
            "n_closed": n_closed,
            "n_open": n_open,
            "sum_result_closed": _r2(sum_result),
            "mean_result_closed": _r2(sum_result / n_closed) if n_closed else None,
            "result_pct_of_landed_closed": _ratio(sum_result, sum_landed) if n_closed else None,
            "sum_rental_revenue_closed": _r2(sum_rent),
            "sum_realised_rv_closed": _r2(sum_rv),
            "sum_pp_credit_closed": _r2(sum_pp),
            "sum_tco_closed": _r2(sum_tco),
            "sum_landed_closed": _r2(sum_landed),
            "tco_per_closed_device": _r2(sum_tco / n_closed) if n_closed else None,
            "rv_per_closed_device": _r2(sum_rv / n_closed) if n_closed else None,
            "rent_per_closed_device": _r2(sum_rent / n_closed) if n_closed else None,
            "sum_liquidation_today_open": _r2(float(g["_liq"].sum())) if n_liq else None,
            "mean_liquidation_today_open": _r2(float(g["_liq"].sum()) / n_liq) if n_liq else None,
            "sum_projected_lease_end_open": _r2(float(g["_proj"].sum())) if n_proj else None,
            "mean_projected_lease_end_open": _r2(float(g["_proj"].sum()) / n_proj) if n_proj else None,
            "as_of": as_of,
        })
    return _nullable(pd.DataFrame(rows, columns=RESULT_BY_COHORT_COLUMNS))


# --------------------------------------------------------------------------------------
# TCO by cohort (closed devices only)
# --------------------------------------------------------------------------------------

def _estimated_magnitudes(lines: pd.DataFrame | None) -> pd.DataFrame:
    """Per (serial, line_type): the summed magnitude of the lines flagged ``is_estimate``."""
    if lines is None or len(lines) == 0 or "is_estimate" not in lines.columns:
        return pd.DataFrame(columns=["serial", "line_type", "est"])
    est = lines[lines["is_estimate"].astype(bool)]
    if len(est) == 0:
        return pd.DataFrame(columns=["serial", "line_type", "est"])
    out = pd.DataFrame({
        "serial": est["serial"].astype(str),
        "line_type": est["line_type"].astype(str),
        "est": pd.to_numeric(est["amount_eur"], errors="coerce").abs().fillna(0.0),
    })
    return out.groupby(["serial", "line_type"], as_index=False)["est"].sum()


def tco_by_cohort(dl: pd.DataFrame, lines: pd.DataFrame | None, kind: str, as_of: date) -> pd.DataFrame:
    """Per (cohort, cost line type): mean and sum of the magnitude over the cohort's CLOSED devices.

    ``n_devices`` is the number of closed devices of the cohort (so the means stack to the
    TCO per closed device). ``estimate_eur`` is the part of ``sum_eur`` that comes from lines
    flagged ``is_estimate`` (read from ``lines``: holding cost always, the channel fee until
    the credit note arrives, the PO price until the unit invoice arrives) and ``is_estimate``
    is true when that part is above zero; without ``lines`` only the always-estimated types
    (``ESTIMATE_LINE_TYPES``) are flagged. The magnitudes come from the wide frame's columns,
    which are the sums of the lines.
    """
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=TCO_BY_COHORT_COLUMNS)
    d = _with_cohort(dl, kind)
    d = d[d["is_closed"].astype(bool)]
    if len(d) == 0:
        return pd.DataFrame(columns=TCO_BY_COHORT_COLUMNS)
    serials = d["serial"].astype(str)
    est_lines = _estimated_magnitudes(lines)
    mags: dict[str, pd.Series] = {}
    ests: dict[str, pd.Series] = {}
    for t in COST_LINE_TYPES:
        col = TCO_COLUMN_OF[t]
        if col in d.columns:
            mags[t] = _num(d, col).fillna(0.0)
        elif lines is not None and len(lines):
            sub = lines[lines["line_type"] == t]
            per = pd.to_numeric(sub["amount_eur"], errors="coerce").abs().groupby(sub["serial"].astype(str)).sum()
            mags[t] = serials.map(per).fillna(0.0)
        else:
            mags[t] = pd.Series(0.0, index=d.index)
        if len(est_lines):
            per_est = est_lines[est_lines["line_type"] == t].set_index("serial")["est"]
            ests[t] = serials.map(per_est).fillna(0.0)
        else:
            ests[t] = mags[t] if t in ESTIMATE_LINE_TYPES else pd.Series(0.0, index=d.index)
    rows: list[dict[str, Any]] = []
    for value, g in d.groupby("_cohort", sort=True):
        n = int(len(g))
        for t in COST_LINE_TYPES:
            total = float(mags[t].loc[g.index].sum())
            estimated = float(ests[t].loc[g.index].sum())
            rows.append({
                "cohort_kind": kind,
                "cohort_value": str(value),
                "line_type": t,
                "n_devices": n,
                "mean_eur": _r2(total / n) if n else None,
                "sum_eur": _r2(total),
                "estimate_eur": _r2(estimated),
                "is_estimate": bool(estimated > 0.0) or t in ESTIMATE_LINE_TYPES,
                "as_of": as_of,
            })
    return _nullable(pd.DataFrame(rows, columns=TCO_BY_COHORT_COLUMNS), ("is_estimate",))


# --------------------------------------------------------------------------------------
# purchase by manufacturer and month
# --------------------------------------------------------------------------------------

def _unit_invoice_ppv(b: BronzeFrames | None, as_of: date) -> pd.Series:
    """Per serial: unit invoice amount minus PO line unit price (NaN without a unit invoice)."""
    if b is None or b.erp_supplier_invoices is None or len(b.erp_supplier_invoices) == 0 or b.erp_po_lines is None or len(b.erp_po_lines) == 0:
        return pd.Series(dtype="float64")
    inv = b.erp_supplier_invoices
    kind = inv["line_kind"].astype(str).str.lower() if "line_kind" in inv.columns else pd.Series("", index=inv.index)
    inv_date = pd.to_datetime(inv["invoice_date"], errors="coerce") if "invoice_date" in inv.columns else pd.Series(pd.NaT, index=inv.index)
    u = inv[(kind == "unit") & inv["serial"].notna() & (inv_date <= pd.Timestamp(as_of))]
    if len(u) == 0:
        return pd.Series(dtype="float64")
    u = pd.DataFrame({
        "serial": u["serial"].astype(str),
        "po_number": u["po_number"].astype(str),
        "po_line": pd.to_numeric(u["po_line"], errors="coerce"),
        "amount": pd.to_numeric(u["amount_eur"], errors="coerce"),
    })
    pl = pd.DataFrame({
        "po_number": b.erp_po_lines["po_number"].astype(str),
        "po_line": pd.to_numeric(b.erp_po_lines["po_line"], errors="coerce"),
        "po_price": pd.to_numeric(b.erp_po_lines["unit_price_eur"], errors="coerce"),
    }).drop_duplicates(["po_number", "po_line"])
    m = u.merge(pl, on=["po_number", "po_line"], how="left")
    m["ppv"] = m["amount"] - m["po_price"]
    return m.groupby("serial")["ppv"].sum()


def purchase_by_oem_month(dl: pd.DataFrame, b: BronzeFrames | None, as_of: date) -> pd.DataFrame:
    """``gold.purchase_by_oem_month``: units, RRP, prices and price protection per (oem, purchase month, supplier role).

    ``discount_vs_rrp_pct = 1 - (sum_unit_price - pp_credited_eur) / sum_rrp_net`` and
    ``landed_vs_rrp_pct = (sum_landed - pp_credited_eur) / sum_rrp_net``: a price protection
    credit is a purchase price reduction (``sum_unit_price`` itself stays the invoiced price).
    ``ppv_vs_po_eur`` = sum over the units of (unit invoice amount - PO line unit price);
    ``share_under_contract`` = units whose PO carries a ``contract_ref`` / units.
    """
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=PURCHASE_BY_OEM_MONTH_COLUMNS)
    d = dl.copy()
    d = d[d["oem"].notna() & d["cohort_month"].notna()]
    if len(d) == 0:
        return pd.DataFrame(columns=PURCHASE_BY_OEM_MONTH_COLUMNS)
    d["_role"] = d["supplier_role"].fillna("unknown").astype(str)
    d["_month"] = d["cohort_month"].map(_cohort_value)
    ppv = _unit_invoice_ppv(b, as_of)
    d["_ppv"] = d["serial"].astype(str).map(ppv).astype("float64")
    d["_rrp"] = _num(d, "rrp_net_eur")
    d["_credit"] = _num(d, "price_protection_credit_eur").fillna(0.0)
    d["_price"] = _num(d, "purchase_price").fillna(0.0)
    d["_effective"] = d["_price"] - d["_credit"]  # the credit is a purchase price reduction
    d["_fd"] = _num(d, "freight_eur").fillna(0.0) + _num(d, "duty_eur").fillna(0.0)
    d["_landed"] = _num(d, "landed_cost").fillna(0.0)
    d["_contract"] = d["contract_ref"].notna()
    d["_claim"] = _num(d, "price_protection_claimable_eur").fillna(0.0)
    rows: list[dict[str, Any]] = []
    for (oem, month, role), g in d.groupby(["oem", "_month", "_role"], sort=True):
        n = int(len(g))
        sum_rrp = float(g["_rrp"].sum(skipna=True))
        sum_price = float(g["_price"].sum())
        sum_effective = float(g["_effective"].sum())
        sum_fd = float(g["_fd"].sum())
        sum_landed = float(g["_landed"].sum())
        sum_credit = float(g["_credit"].sum())
        rows.append({
            "oem": str(oem),
            "purchase_month": date.fromisoformat(str(month)),
            "supplier_role": str(role),
            "n_units": n,
            "sum_rrp_net": _r2(sum_rrp),
            "sum_unit_price": _r2(sum_price),
            "sum_freight_duty": _r2(sum_fd),
            "sum_landed": _r2(sum_landed),
            "discount_vs_rrp_pct": (1.0 - sum_effective / sum_rrp) if sum_rrp else None,
            "landed_vs_rrp_pct": ((sum_landed - sum_credit) / sum_rrp) if sum_rrp else None,
            "ppv_vs_po_eur": _r2(float(g["_ppv"].sum(skipna=True))) if g["_ppv"].notna().any() else None,
            "share_under_contract": float(g["_contract"].sum()) / n if n else None,
            "pp_claimable_eur": _r2(float(g["_claim"].sum())),
            "pp_credited_eur": _r2(float(g["_credit"].sum())),
            "as_of": as_of,
        })
    return _nullable(pd.DataFrame(rows, columns=PURCHASE_BY_OEM_MONTH_COLUMNS))


# --------------------------------------------------------------------------------------
# estimate vs anchor (rented fleet)
# --------------------------------------------------------------------------------------

def estimate_vs_anchor(dl: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """``gold.estimate_vs_anchor``: the rented fleet's estimate at lease end beside the public anchor.

    Per (catalogue_family, oem) over rented and awaiting_return devices: ``sum_estimate_lease_end``
    over every device with an estimate, ``sum_anchor_lease_end`` over the devices with an anchor,
    the mean ratios to net RRP, and ``estimate_vs_anchor_ratio`` = sum of the estimate over the
    devices WITH an anchor / sum of the anchor (like with like). The anchor is a refurbisher ask,
    an upper bound; it advises only.
    """
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=ESTIMATE_VS_ANCHOR_COLUMNS)
    d = dl[dl["lifecycle_status"].isin(["rented", "awaiting_return"]) & dl["catalogue_family"].notna() & dl["oem"].notna()].copy()
    if len(d) == 0:
        return pd.DataFrame(columns=ESTIMATE_VS_ANCHOR_COLUMNS)
    d["_est"] = _num(d, "estimate_rv_lease_end")
    d["_anc"] = _num(d, "anchor_rv_lease_end")
    d["_rrp"] = _num(d, "rrp_net_eur")
    rows: list[dict[str, Any]] = []
    for (fam, oem), g in d.groupby(["catalogue_family", "oem"], sort=True):
        with_anchor = g[g["_anc"].notna()]
        est_ratio = (g["_est"] / g["_rrp"]).replace([np.inf, -np.inf], np.nan).dropna()
        anc_ratio = (with_anchor["_anc"] / with_anchor["_rrp"]).replace([np.inf, -np.inf], np.nan).dropna()
        est_on_anchor = float(with_anchor["_est"].sum(skipna=True))
        sum_anchor = float(with_anchor["_anc"].sum())
        group = with_anchor["anchor_curve_group"].dropna().astype(str)
        fit = with_anchor["anchor_fit_quality"].dropna().astype(str)
        rows.append({
            "catalogue_family": str(fam),
            "oem": str(oem),
            "n_rented": int(len(g)),
            "n_with_anchor": int(len(with_anchor)),
            "sum_estimate_lease_end": _r2(float(g["_est"].sum(skipna=True))),
            "sum_anchor_lease_end": _r2(sum_anchor) if len(with_anchor) else None,
            "mean_estimate_ratio": float(est_ratio.mean()) if len(est_ratio) else None,
            "mean_anchor_ratio": float(anc_ratio.mean()) if len(anc_ratio) else None,
            "estimate_vs_anchor_ratio": (est_on_anchor / sum_anchor) if sum_anchor else None,
            "anchor_curve_group": group.iloc[0] if len(group) else None,
            "anchor_fit_quality": fit.iloc[0] if len(fit) else None,
            "as_of": as_of,
        })
    return _nullable(pd.DataFrame(rows, columns=ESTIMATE_VS_ANCHOR_COLUMNS))


# --------------------------------------------------------------------------------------
# resale by channel and grade (trailing 12 months)
# --------------------------------------------------------------------------------------

def resale_by_channel_grade(dl: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """``gold.resale_by_channel_grade`` over devices sold in the trailing 12 months.

    ``grade_at_sale`` comes from the frame's extra column when present (the resale order's
    grade), else the refurbished grade, else the inspected grade. ``realised_vs_record_ratio``
    = sum of gross over sales with a forecast of record / sum of those records.
    """
    if dl is None or len(dl) == 0:
        return pd.DataFrame(columns=RESALE_BY_CHANNEL_GRADE_COLUMNS)
    d = dl[dl["lifecycle_status"] == "sold"].copy()
    d["_sale"] = pd.to_datetime(d["sale_date"], errors="coerce")
    window_start = pd.Timestamp(as_of - timedelta(days=365))
    d = d[d["_sale"].notna() & (d["_sale"] > window_start) & (d["_sale"] <= pd.Timestamp(as_of))]
    if len(d) == 0:
        return pd.DataFrame(columns=RESALE_BY_CHANNEL_GRADE_COLUMNS)
    grade = d["grade_at_sale"] if "grade_at_sale" in d.columns else pd.Series(None, index=d.index, dtype=object)
    grade = grade.where(grade.notna(), d["grade_out"]).where(lambda s: s.notna(), d["grade_inspected"])
    d["_grade"] = grade.fillna("unknown").astype(str)
    d["_channel"] = d["resale_channel"].fillna("unknown").astype(str)
    d["_gross"] = _num(d, "resale_gross").fillna(0.0)
    d["_fees"] = _num(d, "channel_fee_eur").fillna(0.0)
    d["_refurb"] = _num(d, "refurb_eur").fillna(0.0)
    d["_record"] = _num(d, "estimate_rv_of_record")
    d["_days"] = _num(d, "days_return_to_cash")
    d["_cn_missing"] = d["credited_at"].isna()
    rows: list[dict[str, Any]] = []
    for (channel, g_at_sale), g in d.groupby(["_channel", "_grade"], sort=True):
        with_record = g[g["_record"].notna() & (g["_record"] > 0)]
        sum_record = float(with_record["_record"].sum())
        days = g["_days"].dropna()
        rows.append({
            "channel": str(channel),
            "grade_at_sale": str(g_at_sale),
            "n": int(len(g)),
            "sum_gross": _r2(float(g["_gross"].sum())),
            "sum_fees": _r2(float(g["_fees"].sum())),
            "sum_net": _r2(float(g["_gross"].sum() - g["_fees"].sum())),
            "sum_refurb": _r2(float(g["_refurb"].sum())),
            "sum_estimate_of_record": _r2(sum_record) if len(with_record) else None,
            "realised_vs_record_ratio": (float(with_record["_gross"].sum()) / sum_record) if sum_record else None,
            "median_days_return_to_cash": float(days.median()) if len(days) else None,
            "n_credit_note_missing": int(g["_cn_missing"].sum()),
            "as_of": as_of,
        })
    return _nullable(pd.DataFrame(rows, columns=RESALE_BY_CHANNEL_GRADE_COLUMNS))


__all__ = [
    "COHORT_KINDS",
    "TCO_COHORT_KINDS",
    "COHORT_COLUMN",
    "result_by_cohort",
    "tco_by_cohort",
    "purchase_by_oem_month",
    "estimate_vs_anchor",
    "resale_by_channel_grade",
]
