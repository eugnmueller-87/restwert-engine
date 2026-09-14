"""``silver.device_ledger``: one row per serial, the cycle folded flat (SPEC_v0.2 6.3).

Every money column is a sum of ``silver.ledger_lines`` magnitudes (positive, the sign
lives on the lines); identity, purchase and contract columns come from bronze; the
lifecycle status, the closed date and the quarter cohort come from v0.1 ``device_pnl``
(``silver.reconciliation`` proves both agree); the estimates come from the forecast tables
and the assumptions; the public anchor from ``bronze.mkt_curves``.

Column formulas (L = the serial's booked lines):

* ``purchase_price``, ``freight_eur``, ``duty_eur`` = magnitudes of those line types;
  ``landed_cost = purchase_price + freight + duty``
* ``discount_vs_rrp_eur = rrp_net - (purchase_price - price_protection_credit_eur)``;
  ``discount_vs_rrp_pct`` over ``rrp_net``; ``landed_vs_rrp_pct = (landed_cost -
  price_protection_credit_eur) / rrp_net``: a price protection credit is a purchase price
  reduction in every purchase metric (the ledger keeps its own line and source_ref)
* ``tco_excl_landed_eur`` = staging + outbound_shipping + repair + replacement_logistics
  + return_logistics + wipe_grading + refurbishment + holding_cost + channel_fee
* ``tco_transactional_eur = tco_eur - (every cost line with is_estimate)``: holding cost,
  the channel fee until the credit note arrives, the PO price until the unit invoice
  arrives; ``tco_eur = landed_cost + tco_excl_landed_eur``
* ``months_billed`` = count of ``rental_revenue`` lines; ``rental_revenue`` = their sum
* ``resale_net = resale_gross - channel_fee_eur``
* ``realised_rv`` = ``resale_gross`` when sold, 0.00 when scrapped, NULL otherwise
* ``lifecycle_result_eur = result_closed(L)`` and ``result_v01_basis_eur = result_v01_basis(L)``
  for closed devices, NULL while open
* the two open numbers and the estimates follow ``restwert.ledger.result``

The frame carries every DDL column in DDL order plus ``grade_at_sale`` (from the resale
order; not in the DDL, used by the gold resale table and ignored by ``write_df``).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from restwert.dates import month_floor, months_between_float, quarter_label
from restwert.lake.common import fleet_family, rrp_net
from restwert.ledger.lines import COST_LINE_TYPES, LEDGER_ORDER, BronzeFrames
from restwert.ledger.result import (
    PROJECTED_LABEL_LEASE_END,
    PROJECTED_LABEL_SALE,
    anchor_rv,
    estimate_months_at_lease_end,
    expected_remaining_cost,
    remaining_contracted_rent,
    result_if_liquidated_today,
    result_projected_at_lease_end,
    select_anchor_curve,
)
from restwert.pnl.lifecycle import CLOSED_STATUSES, _to_date

if TYPE_CHECKING:  # pragma: no cover - typing only
    from restwert.config import Assumptions


#: Every column of ``silver.device_ledger`` in DDL order.
DEVICE_LEDGER_COLUMNS: list[str] = [
    "serial", "as_of",
    "slug", "model_name", "oem", "catalogue_family", "model_family", "series",
    "variant_spec", "storage_gb", "rrp_gross_eur", "rrp_net_eur", "launch_date",
    "months_since_launch_at_as_of",
    "po_number", "po_line", "supplier_id", "supplier_name", "supplier_role", "contract_ref",
    "order_date", "received_at", "purchase_date", "cohort_month", "cohort_quarter",
    "price_protection_days", "price_protection_status", "price_protection_claimable_eur",
    "purchase_price", "discount_vs_rrp_eur", "discount_vs_rrp_pct", "freight_eur",
    "duty_eur", "landed_cost", "landed_vs_rrp_pct", "price_protection_credit_eur",
    "staging_eur", "outbound_shipping_eur", "repair_eur", "replacement_logistics_eur",
    "return_logistics_eur", "wipe_grading_eur", "refurb_eur", "holding_cost_eur",
    "channel_fee_eur", "days_in_stock_to_date", "tco_excl_landed_eur", "tco_transactional_eur",
    "tco_eur", "n_lines", "n_estimate_lines",
    "first_contract_id", "customer_id", "term_months", "monthly_rate", "contract_start",
    "contract_end_planned", "contract_end_effective", "months_billed", "months_remaining",
    "rental_revenue", "remaining_contracted_rent",
    "return_date", "grade_declared", "grade_inspected", "wipe_certificate_id", "grade_out",
    "refurb_outcome", "sellable_date", "resale_channel", "listed_at", "sale_date", "credited_at",
    "resale_gross", "resale_net", "days_return_to_sale", "days_return_to_cash",
    "estimate_run_id", "grade_used", "grade_source", "estimate_rv_today",
    "estimate_rv_lease_end", "estimate_months_at_lease_end", "estimate_rv_source", "estimate_fit_quality",
    "estimate_rv_of_record", "anchor_curve_group", "anchor_fit_quality", "anchor_rv_lease_end",
    "estimate_vs_anchor_ratio", "realised_vs_record_ratio",
    "realised_rv", "lifecycle_result_eur", "result_v01_basis_eur", "result_pct_of_landed",
    "result_if_liquidated_today", "result_projected_at_lease_end", "projected_label",
    "expected_remaining_cost", "expected_cost_inputs_source",
    "lifecycle_status", "is_closed", "closed_date", "chain_complete", "is_synthetic",
]

#: Extra frame columns (not in the DDL, dropped by ``write_df``).
EXTRA_COLUMNS: list[str] = ["grade_at_sale"]

#: Date columns of the wide frame (typed ``datetime64`` on the way out, DATE in DuckDB).
DATE_COLUMNS: tuple[str, ...] = (
    "as_of", "launch_date", "order_date", "received_at", "purchase_date", "cohort_month", "contract_start",
    "contract_end_planned", "contract_end_effective", "return_date", "sellable_date", "listed_at", "sale_date",
    "credited_at", "closed_date",
)

#: Magnitude column of the wide frame per line type.
TCO_COLUMN_OF: dict[str, str] = {
    "purchase_price": "purchase_price",
    "freight": "freight_eur",
    "duty": "duty_eur",
    "staging": "staging_eur",
    "outbound_shipping": "outbound_shipping_eur",
    "rental_revenue": "rental_revenue",
    "repair": "repair_eur",
    "replacement_logistics": "replacement_logistics_eur",
    "return_logistics": "return_logistics_eur",
    "wipe_grading": "wipe_grading_eur",
    "refurbishment": "refurb_eur",
    "holding_cost": "holding_cost_eur",
    "resale_gross": "resale_gross",
    "channel_fee": "channel_fee_eur",
    "price_protection_credit": "price_protection_credit_eur",
}
TCO_EXCL_LANDED_TYPES: tuple[str, ...] = (
    "staging", "outbound_shipping", "repair", "replacement_logistics", "return_logistics",
    "wipe_grading", "refurbishment", "holding_cost", "channel_fee",
)

DEFAULT_GRADE_D_OFFSET = -0.60


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------

def _missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _s(v: Any) -> str | None:
    return None if _missing(v) else str(v)


def _fl(v: Any) -> float | None:
    if _missing(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    f = _fl(v)
    return None if f is None else int(f)


def _r2(v: float | None) -> float | None:
    return None if v is None else round(float(v) + 0.0, 2)


def _d(v: Any) -> date | None:
    try:
        return _to_date(v)
    except (TypeError, ValueError):
        return None


def _ts(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    s = pd.to_datetime(df[col], errors="coerce")
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_localize(None)
    return s.astype("datetime64[ns]").dt.normalize()


def _txt(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([None] * len(df), index=df.index, dtype=object)
    return pd.Series([_s(v) for v in df[col].tolist()], index=df.index, dtype=object)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").astype("float64")


def _has(df: pd.DataFrame | None) -> bool:
    return df is not None and len(df) > 0


def _a(a: "Assumptions", key: str, sub: str | None, default: Any) -> Any:
    try:
        return a.get(key, sub)
    except (KeyError, TypeError):
        return default


def sums_by_line_type(lines: pd.DataFrame) -> pd.DataFrame:
    """Serial x line_type pivot of magnitudes (``abs(amount_eur)``), one column per ``LEDGER_ORDER`` type.

    Also carries ``n_lines``, ``n_estimate_lines`` and ``estimate_cost`` (the magnitude of
    every cost line flagged ``is_estimate``: holding cost, the channel fee until the credit
    note arrives, the PO price until the unit invoice arrives) per serial.
    """
    cols = list(LEDGER_ORDER) + ["n_lines", "n_estimate_lines", "estimate_cost", "sum_amount", "n_rent"]
    if lines is None or len(lines) == 0:
        return pd.DataFrame(columns=cols).astype(float)
    amount = pd.to_numeric(lines["amount_eur"], errors="coerce").fillna(0.0)
    df = pd.DataFrame({
        "serial": lines["serial"].astype(str),
        "line_type": lines["line_type"].astype(str),
        "mag": amount.abs(),
        "amount": amount,
        "est": lines["is_estimate"].astype(bool) if "is_estimate" in lines.columns else False,
    })
    df["rent"] = df["line_type"] == "rental_revenue"
    df["est_cost"] = df["mag"].where(df["est"] & df["line_type"].isin(COST_LINE_TYPES), 0.0)
    pv = df.pivot_table(index="serial", columns="line_type", values="mag", aggfunc="sum", fill_value=0.0)
    for t in LEDGER_ORDER:
        if t not in pv.columns:
            pv[t] = 0.0
    pv = pv[list(LEDGER_ORDER)].round(2)
    counts = df.groupby("serial").agg(
        n_lines=("mag", "size"), n_estimate_lines=("est", "sum"), estimate_cost=("est_cost", "sum"),
        sum_amount=("amount", "sum"), n_rent=("rent", "sum"),
    )
    out = pv.join(counts, how="left")
    out["n_lines"] = out["n_lines"].fillna(0).astype(int)
    out["n_estimate_lines"] = out["n_estimate_lines"].fillna(0).astype(int)
    out["estimate_cost"] = out["estimate_cost"].fillna(0.0).round(2)
    out["n_rent"] = out["n_rent"].fillna(0).astype(int)
    out["sum_amount"] = out["sum_amount"].fillna(0.0).round(2)
    return out


# --------------------------------------------------------------------------------------
# lookups built once per run
# --------------------------------------------------------------------------------------

class _GridIndex:
    """``(model, grade) -> (months, ratios, fit_quality)`` with ``grid_lookup`` semantics (months clipped to the range)."""

    def __init__(self, rv_grid: pd.DataFrame | None) -> None:
        self.index: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, str | None]] = {}
        if not _has(rv_grid) or not {"model", "grade", "months_since_launch", "forecast_rv_ratio"}.issubset(rv_grid.columns):
            return
        g = rv_grid[["model", "grade", "months_since_launch", "forecast_rv_ratio"]].copy()
        g["fit_quality"] = rv_grid["fit_quality"] if "fit_quality" in rv_grid.columns else None
        g["model"] = g["model"].astype(str)
        g["grade"] = g["grade"].astype(str)
        g["months_since_launch"] = pd.to_numeric(g["months_since_launch"], errors="coerce")
        g = g.dropna(subset=["months_since_launch"]).sort_values(["model", "grade", "months_since_launch"])
        for (model, grade), sub in g.groupby(["model", "grade"], sort=False):
            months = sub["months_since_launch"].to_numpy(dtype=float)
            ratios = pd.to_numeric(sub["forecast_rv_ratio"], errors="coerce").to_numpy(dtype=float)
            fq = _s(sub["fit_quality"].iloc[0]) if "fit_quality" in sub.columns else None
            self.index[(model, grade)] = (months, ratios, fq)

    def lookup(self, model: str | None, grade: str | None, months: int | None) -> tuple[float, int, bool, str | None] | None:
        """``(ratio, month used, clipped, fit_quality)`` or ``None`` when the grid has no row."""
        if model is None or grade is None or months is None:
            return None
        hit = self.index.get((str(model), str(grade)))
        if hit is None:
            return None
        months_arr, ratios, fq = hit
        used = int(min(max(int(months), int(months_arr.min())), int(months_arr.max())))
        pos = int(np.searchsorted(months_arr, used))
        if pos >= len(months_arr) or int(months_arr[pos]) != used:
            return None
        ratio = float(ratios[pos])
        if np.isnan(ratio):
            return None
        return ratio, used, used != int(months), fq


def _latest_per_serial(df: pd.DataFrame, ts_col: str, as_of: pd.Timestamp | None, keep: str = "last") -> pd.DataFrame:
    """Rows of ``df`` sorted by ``ts_col`` (``<= as_of`` when given), one per serial."""
    if not _has(df) or "serial" not in df.columns:
        return pd.DataFrame(columns=list(df.columns) if df is not None else ["serial"])
    d = df.copy()
    d["_ts"] = _ts(d, ts_col)
    d["serial"] = _txt(d, "serial")
    d = d[d["serial"].notna()]
    if as_of is not None:
        d = d[d["_ts"].notna() & (d["_ts"] <= as_of)]
    d = d.sort_values(["serial", "_ts"], kind="stable").drop_duplicates("serial", keep=keep)
    return d


def _variant_lookup(cat_variants: pd.DataFrame | None) -> dict[tuple[str, int], tuple[str | None, float | None]]:
    """``(slug, storage_gb) -> (spec, rrp_gross)``: the lowest priced variant of that storage."""
    out: dict[tuple[str, int], tuple[str | None, float | None]] = {}
    if not _has(cat_variants):
        return out
    v = pd.DataFrame({
        "slug": _txt(cat_variants, "slug"),
        "storage_gb": _num(cat_variants, "storage_gb"),
        "spec": _txt(cat_variants, "spec"),
        "rrp": _num(cat_variants, "rrp_eur_launch_de"),
    }).dropna(subset=["slug", "storage_gb"])
    v = v.sort_values(["slug", "storage_gb", "rrp"], na_position="last", kind="stable")
    for slug, storage, spec, rrp in zip(v["slug"], v["storage_gb"], v["spec"], v["rrp"]):
        key = (str(slug), int(storage))
        if key in out:
            continue
        out[key] = (spec, None if _missing(rrp) else float(rrp))
    return out


def _price_protection(b: BronzeFrames, base: pd.DataFrame, credited: set[str], as_of: date) -> pd.DataFrame:
    """Per serial: ``price_protection_status`` and ``price_protection_claimable_eur``.

    A drop qualifies when the first ``erp_price_changes`` row of (supplier_id, slug,
    storage_gb) has ``valid_from`` in ``(delivered_date, delivered_date + price_protection_days]``
    with ``delivered_date`` = latest receipt of the PO line. The claim window is the
    register's ``claim_window_days`` of the PO's ``contract_ref`` (the protection days of the
    line when the register has none). Status: ``claimed`` when a credit line exists, ``open``
    while ``valid_from + window > as_of``, ``missed`` after, ``not_applicable`` without a drop.
    """
    out = pd.DataFrame({"serial": base["serial"].astype(str)})
    out["price_protection_status"] = "not_applicable"
    out["price_protection_claimable_eur"] = 0.0
    if not _has(b.erp_price_changes) or not _has(b.erp_goods_receipts):
        out.loc[out["serial"].isin(credited), "price_protection_status"] = "claimed"
        return out
    gr = pd.DataFrame({
        "po_number": _txt(b.erp_goods_receipts, "po_number"),
        "po_line": _num(b.erp_goods_receipts, "po_line"),
        "received_at": _ts(b.erp_goods_receipts, "received_at"),
    })
    delivered = gr.groupby(["po_number", "po_line"])["received_at"].max().rename("delivered_at").reset_index()
    pc = pd.DataFrame({
        "supplier_id": _txt(b.erp_price_changes, "supplier_id"),
        "slug": _txt(b.erp_price_changes, "slug"),
        "storage_gb": _num(b.erp_price_changes, "storage_gb"),
        "valid_from": _ts(b.erp_price_changes, "valid_from"),
        "drop": (_num(b.erp_price_changes, "old_unit_price_eur") - _num(b.erp_price_changes, "new_unit_price_eur")).round(2),
    }).dropna(subset=["valid_from", "supplier_id", "slug", "storage_gb"]).sort_values("valid_from", kind="stable")
    windows = pd.DataFrame(columns=["contract_ref", "claim_window"])
    if _has(b.ctr_register):
        windows = pd.DataFrame({
            "contract_ref": _txt(b.ctr_register, "contract_id"),
            "claim_window": _num(b.ctr_register, "claim_window_days"),
        }).dropna(subset=["contract_ref"]).drop_duplicates("contract_ref")
    po = base[["po_number", "po_line", "supplier_id", "slug", "storage_gb", "price_protection_days", "contract_ref"]].drop_duplicates(["po_number", "po_line"]).copy()
    po["po_line"] = pd.to_numeric(po["po_line"], errors="coerce")
    po["storage_gb"] = pd.to_numeric(po["storage_gb"], errors="coerce")
    po["price_protection_days"] = pd.to_numeric(po["price_protection_days"], errors="coerce")
    po = po.merge(delivered, on=["po_number", "po_line"], how="left").merge(windows, on="contract_ref", how="left")
    po = po[po["price_protection_days"].notna() & (po["price_protection_days"] > 0) & po["delivered_at"].notna()]
    if len(po):
        m = po.merge(pc, on=["supplier_id", "slug", "storage_gb"], how="inner")
        m = m[(m["valid_from"] > m["delivered_at"]) & (m["valid_from"] <= m["delivered_at"] + pd.to_timedelta(m["price_protection_days"], unit="D"))]
        m = m.sort_values("valid_from", kind="stable").drop_duplicates(["po_number", "po_line"], keep="first")
        window = m["claim_window"].where(m["claim_window"].notna(), m["price_protection_days"])
        m["status"] = np.where(m["valid_from"] + pd.to_timedelta(window, unit="D") > pd.Timestamp(as_of), "open", "missed")
        m["claimable"] = m["drop"].fillna(0.0).round(2)
        key = pd.DataFrame({"serial": base["serial"].astype(str), "po_number": base["po_number"], "po_line": pd.to_numeric(base["po_line"], errors="coerce")})
        key = key.merge(m[["po_number", "po_line", "status", "claimable"]], on=["po_number", "po_line"], how="left")
        out["price_protection_status"] = key["status"].fillna("not_applicable").to_numpy()
        out["price_protection_claimable_eur"] = key["claimable"].fillna(0.0).to_numpy()
    out.loc[out["serial"].isin(credited), "price_protection_status"] = "claimed"
    return out


def _family_inputs(b: BronzeFrames, lines: pd.DataFrame, fam_of: dict[str, str], a: "Assumptions", as_of: date) -> dict[str, dict[str, Any]]:
    """Per family: the realised statistics of ``pnl.tco._family_realised_inputs`` plus the wipe_grading mean."""
    from restwert.pnl.tco import _family_realised_inputs, _min_n

    dev = pd.DataFrame({"serial": list(fam_of), "model_family": list(fam_of.values())})
    contracts = None
    if _has(b.portal_rental_contracts):
        contracts = pd.DataFrame({
            "serial": _txt(b.portal_rental_contracts, "serial"),
            "start_date": _ts(b.portal_rental_contracts, "start_date"),
            "end_date": _ts(b.portal_rental_contracts, "end_date"),
            "actual_end_date": _ts(b.portal_rental_contracts, "actual_end_date"),
        })
    ev_parts: list[pd.DataFrame] = []
    if _has(b.sd_tickets):
        opened = _ts(b.sd_tickets, "opened_at")
        keep = opened.notna() & (opened <= pd.Timestamp(as_of))
        ev_parts.append(pd.DataFrame({
            "serial": _txt(b.sd_tickets, "serial")[keep],
            "event_type": "damage",
            "event_date": opened[keep],
            "cost": 0.0,
        }))
    if _has(lines):
        m = lines[lines["line_type"].isin(["repair", "replacement_logistics", "return_logistics"])]
        ev_parts.append(pd.DataFrame({
            "serial": m["serial"].astype(str),
            "event_type": m["line_type"].map({"repair": "repair", "replacement_logistics": "replacement", "return_logistics": "return"}),
            "event_date": pd.to_datetime(m["event_date"]),
            "cost": pd.to_numeric(m["amount_eur"], errors="coerce").abs(),
        }))
    events = pd.concat(ev_parts, ignore_index=True) if ev_parts else None
    refurb = None
    wipe_stats: dict[str, tuple[float, int]] = {}
    if _has(lines):
        rf = lines[lines["line_type"] == "refurbishment"]
        refurb = pd.DataFrame({"serial": rf["serial"].astype(str), "cost": pd.to_numeric(rf["amount_eur"], errors="coerce").abs()})
        wg = lines[lines["line_type"] == "wipe_grading"]
        if len(wg):
            fam = wg["serial"].astype(str).map(fam_of)
            grp = pd.DataFrame({"family": fam, "mag": pd.to_numeric(wg["amount_eur"], errors="coerce").abs()}).dropna(subset=["family"])
            for f, sub in grp.groupby("family"):
                wipe_stats[str(f)] = (float(sub["mag"].mean()), int(len(sub)))
    stats = _family_realised_inputs(dev, events, refurb, None, contracts, as_of)
    min_n = _min_n(a)
    for f in set(fam_of.values()):
        rec = stats.setdefault(f, {})
        mean, n = wipe_stats.get(f, (None, 0))
        rec["wipe_grading_mean"] = mean
        rec["n_wipe"] = n
        rec["min_n"] = min_n
    return stats


# --------------------------------------------------------------------------------------
# build_device_ledger
# --------------------------------------------------------------------------------------

def _serial_map(df: pd.DataFrame | None, cols: list[str], ts_col: str | None = None, as_of: pd.Timestamp | None = None, keep: str = "last") -> dict[str, dict[str, Any]]:
    if not _has(df):
        return {}
    d = _latest_per_serial(df, ts_col, as_of, keep=keep) if ts_col else df
    out: dict[str, dict[str, Any]] = {}
    present = [c for c in cols if c in d.columns]
    for rec in d[["serial"] + present].to_dict("records"):
        out[str(rec["serial"])] = rec
    return out


def build_device_ledger(
    lines: pd.DataFrame,
    timeline: pd.DataFrame | None,
    b: BronzeFrames,
    device_pnl: pd.DataFrame,
    catalogue: pd.DataFrame | None,
    rv_current: pd.DataFrame | None,
    rv_of_record: pd.DataFrame | None,
    rv_grid: pd.DataFrame | None,
    a: "Assumptions",
    lake_truth: Any | None,
    as_of: date,
) -> pd.DataFrame:
    """Pure: lines, timeline, bronze, ``device_pnl`` and the forecast tables in, one row per serial out.

    ``device_pnl`` supplies ``lifecycle_status``, ``is_closed``, ``closed_date`` and the
    quarter cohort; the serial universe is the union of goods receipts and ``device_pnl``.
    ``lake_truth`` (``lakegen.config.TruthV2``) supplies ``grade_d_offset_default`` for the
    anchor; ``None`` falls back to -0.60. Missing forecast tables give NULL estimates.
    """
    as_of_ts = pd.Timestamp(as_of)
    vat_rate = float(_a(a, "vat_rate", None, 0.19) or 0.19)
    fee_mkt = _a(a, "channel_fees", "marketplace", {}) or {}
    fee_pct = float(fee_mkt.get("fee_pct", 0.0) or 0.0)
    fee_fixed = float(fee_mkt.get("fee_fixed_eur", 0.0) or 0.0)
    grade_d_default = float(getattr(lake_truth, "grade_d_offset_default", DEFAULT_GRADE_D_OFFSET)) if lake_truth is not None else DEFAULT_GRADE_D_OFFSET
    is_synth_default = bool(lines["is_synthetic"].iloc[0]) if _has(lines) and "is_synthetic" in lines.columns else True

    # ---- identity: receipts x po lines x po headers x catalogue -------------------------
    if _has(b.erp_goods_receipts):
        base = pd.DataFrame({
            "serial": _txt(b.erp_goods_receipts, "serial"),
            "po_number": _txt(b.erp_goods_receipts, "po_number"),
            "po_line": _num(b.erp_goods_receipts, "po_line"),
            "received_at": _ts(b.erp_goods_receipts, "received_at"),
            "gr_synthetic": b.erp_goods_receipts["is_synthetic"] if "is_synthetic" in b.erp_goods_receipts.columns else is_synth_default,
        }).dropna(subset=["serial"]).drop_duplicates("serial")
    else:
        base = pd.DataFrame(columns=["serial", "po_number", "po_line", "received_at", "gr_synthetic"])
    pnl_serials = device_pnl["serial"].astype(str) if _has(device_pnl) else pd.Series([], dtype=object)
    missing = sorted(set(pnl_serials) - set(base["serial"].astype(str)))
    if missing:
        base = pd.concat([base, pd.DataFrame({"serial": missing})], ignore_index=True)
    base["serial"] = base["serial"].astype(str)

    if _has(b.erp_po_lines):
        pl = pd.DataFrame({
            "po_number": _txt(b.erp_po_lines, "po_number"),
            "po_line": _num(b.erp_po_lines, "po_line"),
            "slug": _txt(b.erp_po_lines, "slug"),
            "storage_gb": _num(b.erp_po_lines, "storage_gb"),
            "po_unit_price": _num(b.erp_po_lines, "unit_price_eur"),
            "price_protection_days": _num(b.erp_po_lines, "price_protection_days"),
        }).drop_duplicates(["po_number", "po_line"])
        base = base.merge(pl, on=["po_number", "po_line"], how="left")
    else:
        for c in ("slug", "storage_gb", "po_unit_price", "price_protection_days"):
            base[c] = None
    if _has(b.erp_purchase_orders):
        ph = pd.DataFrame({
            "po_number": _txt(b.erp_purchase_orders, "po_number"),
            "supplier_id": _txt(b.erp_purchase_orders, "supplier_id"),
            "supplier_name": _txt(b.erp_purchase_orders, "supplier_name"),
            "supplier_role": _txt(b.erp_purchase_orders, "supplier_role"),
            "contract_ref": _txt(b.erp_purchase_orders, "contract_ref"),
            "order_date": _ts(b.erp_purchase_orders, "order_date"),
        }).drop_duplicates("po_number")
        base = base.merge(ph, on="po_number", how="left")
    else:
        for c in ("supplier_id", "supplier_name", "supplier_role", "contract_ref", "order_date"):
            base[c] = None
    if _has(b.cat_models):
        cm = pd.DataFrame({
            "slug": _txt(b.cat_models, "slug"),
            "model_name": _txt(b.cat_models, "model_name"),
            "oem": _txt(b.cat_models, "oem"),
            "catalogue_family": _txt(b.cat_models, "family"),
            "series": _txt(b.cat_models, "series"),
            "cat_launch": _ts(b.cat_models, "launch_date"),
        }).drop_duplicates("slug")
        base = base.merge(cm, on="slug", how="left")
    else:
        for c in ("model_name", "oem", "catalogue_family", "series", "cat_launch"):
            base[c] = None
    variants = _variant_lookup(b.cat_variants)

    # catalogue (main.model_catalogue) fills model_family and launch date when bronze has none
    cat_family: dict[str, str] = {}
    cat_launch: dict[str, Any] = {}
    if _has(catalogue) and "model" in catalogue.columns:
        cat_family = {str(m): _s(f) for m, f in zip(catalogue["model"], catalogue.get("model_family", pd.Series([None] * len(catalogue))))}
        cat_launch = {str(m): _d(l) for m, l in zip(catalogue["model"], catalogue.get("launch_date", pd.Series([None] * len(catalogue))))}

    # ---- per-serial lookups ----------------------------------------------------------------
    sums = sums_by_line_type(lines)
    sums_d: dict[str, dict[str, float]] = {str(k): v for k, v in sums.to_dict("index").items()} if len(sums) else {}
    rent_by_contract: dict[tuple[str, str], int] = {}
    if _has(lines):
        rl = lines[lines["line_type"] == "rental_revenue"]
        if len(rl):
            rent_by_contract = rl.groupby([rl["serial"].astype(str), rl["contract_ref"].astype(str)]).size().to_dict()
    pnl_map = _serial_map(device_pnl, ["lifecycle_status", "is_closed", "closed_date", "cohort", "model_family", "launch_date", "purchase_date"])
    tl_map = _serial_map(timeline, ["sellable_at", "sold_at", "credited_at", "returned_at", "chain_complete", "days_return_to_cash", "days_sellable_to_sold"])
    latest_c = _serial_map(b.portal_rental_contracts, ["contract_id", "customer_id", "term_months", "monthly_rate_eur", "start_date", "end_date", "actual_end_date", "status"], "start_date", None, keep="last")
    first_c = _serial_map(b.portal_rental_contracts, ["contract_id"], "start_date", None, keep="first")
    receipt = _serial_map(b.ret_receipts, ["returned_at", "grade_declared", "grade_inspected", "wipe_certificate_id"], "returned_at", as_of_ts, keep="last")
    work = _serial_map(b.rf_work_orders, ["grade_out", "outcome", "finished_at"], "finished_at", as_of_ts, keep="last")
    orders_df = b.rc_orders
    if _has(orders_df) and _has(b.rc_credit_notes):
        cn = pd.DataFrame({
            "order_id": _txt(b.rc_credit_notes, "order_id"),
            "credited_at": _ts(b.rc_credit_notes, "credited_at"),
        }).sort_values("credited_at", kind="stable").drop_duplicates("order_id")
        orders_df = orders_df.copy()
        orders_df["order_id"] = _txt(orders_df, "order_id")
        orders_df = orders_df.merge(cn, on="order_id", how="left")
    order = _serial_map(orders_df, ["order_id", "channel", "listed_at", "sold_at", "gross_price_eur", "grade_at_sale", "credited_at"], "sold_at", as_of_ts, keep="first")
    cur = _serial_map(rv_current, ["run_id", "forecast_rv", "grade_used", "grade_source", "fit_quality"])
    rec_map = _serial_map(rv_of_record, ["forecast_rv", "is_missing"])
    grid = _GridIndex(rv_grid)
    credited = {s for s, v in sums_d.items() if float(v.get("price_protection_credit", 0.0)) > 0}
    if _has(lines):
        credited |= set(lines.loc[lines["line_type"] == "price_protection_credit", "serial"].astype(str))
    pp_df = _price_protection(b, base, credited, as_of)
    pp_status = dict(zip(pp_df["serial"], pp_df["price_protection_status"]))
    pp_claim = dict(zip(pp_df["serial"], pp_df["price_protection_claimable_eur"]))

    # family per serial (for the expected-cost inputs)
    fam_of: dict[str, str] = {}
    fam_col: list[str | None] = []
    for row in base.itertuples(index=False):
        serial = str(row.serial)
        fam = cat_family.get(str(row.slug)) if row.slug is not None else None
        if fam is None and row.catalogue_family is not None and row.oem is not None:
            try:
                fam = fleet_family(str(row.catalogue_family), str(row.oem))
            except ValueError:
                fam = None
        if fam is None:
            fam = _s(pnl_map.get(serial, {}).get("model_family"))
        fam_col.append(fam)
        if fam is not None:
            fam_of[serial] = fam
    base["model_family"] = fam_col
    inputs_by_family = _family_inputs(b, lines, fam_of, a, as_of)
    curve_cache: dict[tuple[str | None, str | None], pd.Series | None] = {}

    rows: list[dict[str, Any]] = []
    for row in base.itertuples(index=False):
        serial = str(row.serial)
        sums_s = sums_d.get(serial, {})

        def mag(t: str) -> float:
            return round(float(sums_s.get(t, 0.0) or 0.0), 2)

        p = pnl_map.get(serial, {})
        status = _s(p.get("lifecycle_status")) or "not_deployed"
        is_closed = bool(p.get("is_closed")) if not _missing(p.get("is_closed")) else status in CLOSED_STATUSES
        closed_date = _d(p.get("closed_date"))
        tl = tl_map.get(serial, {})

        slug = _s(row.slug)
        storage = _i(row.storage_gb)
        spec, rrp_gross = variants.get((slug, storage), (None, None)) if slug is not None and storage is not None else (None, None)
        rrp_n = rrp_net(rrp_gross, vat_rate) if rrp_gross is not None else None
        launch = _d(row.cat_launch) or cat_launch.get(slug) or _d(p.get("launch_date"))
        received = _d(row.received_at)
        purchase_date = received or _d(p.get("purchase_date"))
        family = _s(row.model_family)

        purchase_price = mag("purchase_price")
        freight = mag("freight")
        duty = mag("duty")
        landed = round(purchase_price + freight + duty, 2)
        pp_credit = mag("price_protection_credit")
        # a price protection credit is a purchase price reduction for every purchase metric;
        # the ledger keeps it as its own revenue line with its own source_ref
        effective_price = round(purchase_price - pp_credit, 2)
        tco_excl = round(sum(mag(t) for t in TCO_EXCL_LANDED_TYPES), 2)
        holding = mag("holding_cost")
        estimate_cost = round(float(sums_s.get("estimate_cost", 0.0) or 0.0), 2)
        tco_transactional = round(landed + tco_excl - estimate_cost, 2)
        tco_eur = round(landed + tco_excl, 2)
        rent = mag("rental_revenue")
        resale_gross = mag("resale_gross")
        fee = mag("channel_fee")
        sum_lines = round(float(sums_s.get("sum_amount", 0.0) or 0.0), 2)
        n_lines = int(sums_s.get("n_lines", 0) or 0)
        n_est = int(sums_s.get("n_estimate_lines", 0) or 0)

        # contract block
        c = latest_c.get(serial, {})
        latest_id = _s(c.get("contract_id"))
        term = _i(c.get("term_months"))
        rate = _fl(c.get("monthly_rate_eur"))
        c_start = _d(c.get("start_date"))
        c_end = _d(c.get("end_date"))
        c_actual = _d(c.get("actual_end_date"))
        c_eff = c_actual or c_end
        active = (_s(c.get("status")) or "").lower() == "active"
        months_billed = int(sums_s.get("n_rent", 0) or 0)
        billed_latest = int(rent_by_contract.get((serial, latest_id), 0)) if latest_id is not None else 0
        remaining_rent, months_remaining = remaining_contracted_rent(rate, term, billed_latest, active and status in ("rented", "awaiting_return"))

        # return, refurb, resale block
        r = receipt.get(serial, {})
        return_date = _d(r.get("returned_at")) or _d(tl.get("returned_at"))
        w = work.get(serial, {})
        grade_out = _s(w.get("grade_out"))
        refurb_outcome = _s(w.get("outcome"))
        sellable_date = _d(tl.get("sellable_at"))
        if sellable_date is None and refurb_outcome is not None and refurb_outcome != "scrap":
            sellable_date = _d(w.get("finished_at"))
        o = order.get(serial, {}) if status == "sold" else {}
        sale_date = _d(o.get("sold_at"))
        credited_at = _d(o.get("credited_at")) or _d(tl.get("credited_at"))
        resale_channel = _s(o.get("channel"))
        grade_at_sale = _s(o.get("grade_at_sale")) or (grade_out or _s(r.get("grade_inspected")) if status == "sold" else None)
        days_return_to_sale = (sale_date - return_date).days if (sale_date and return_date) else None
        days_return_to_cash = _i(tl.get("days_return_to_cash"))
        if days_return_to_cash is None and credited_at and return_date:
            days_return_to_cash = (credited_at - return_date).days
        stop = min(sale_date, as_of) if sale_date else as_of
        days_in_stock = (stop - sellable_date).days if sellable_date and sellable_date <= as_of else None
        if days_in_stock is not None and days_in_stock < 0:
            days_in_stock = 0

        # estimates
        cu = cur.get(serial, {})
        grade_inspected = _s(r.get("grade_inspected"))
        if grade_out is not None:
            grade_used, grade_source = grade_out, "refurbished"
        elif grade_inspected is not None:
            grade_used, grade_source = grade_inspected, "inspected"
        else:
            grade_used, grade_source = _s(_a(a, "expected_grade_at_return", family, "B")) or "B", "assumption"
        estimate_today = _fl(cu.get("forecast_rv")) if not is_closed else None
        estimate_run_id = _s(cu.get("run_id"))
        rts_days = float(_a(a, "expected_return_to_sale_days", family, 35.0) or 35.0)
        months_at_end: int | None = None
        if launch is not None and not is_closed:
            if status in ("rented", "awaiting_return") and c_end is not None:
                months_at_end = estimate_months_at_lease_end(launch, c_end, rts_days)
            elif status in ("wip", "in_stock"):
                months_at_end = estimate_months_at_lease_end(launch, as_of, rts_days)
        est_lease_end: float | None = None
        est_source: str | None = None
        est_fit: str | None = None
        if months_at_end is not None:
            hit = grid.lookup(slug, grade_used, months_at_end)
            if hit is not None:
                ratio, _used, clipped, est_fit = hit
                est_lease_end = _r2(ratio * purchase_price)
                est_source = "grid_clipped" if clipped else "grid"
            else:
                planned = _fl(_a(a, "planned_rv_ratio", family, None))
                if planned is not None:
                    est_lease_end = _r2(planned * landed)
                    est_source = "planned_ratio_on_landed_cost"
        rec = rec_map.get(serial, {})
        est_record = _fl(rec.get("forecast_rv")) if not bool(rec.get("is_missing", False)) else None
        key = (_s(row.catalogue_family), _s(row.oem))
        if key not in curve_cache:
            curve_cache[key] = select_anchor_curve(b.mkt_curves, key[0], key[1])
        anchor_value, anchor_group, anchor_fit = anchor_rv(rrp_n, curve_cache[key], months_at_end, grade_used, grade_d_default)
        est_vs_anchor = (est_lease_end / anchor_value) if (est_lease_end is not None and anchor_value) else None
        realised_vs_record = (resale_gross / est_record) if (status == "sold" and est_record) else None

        # results
        if status == "sold":
            realised_rv: float | None = resale_gross
        elif status == "scrapped":
            realised_rv = 0.0
        else:
            realised_rv = None
        lifecycle_result = sum_lines if is_closed else None
        bridge = round(mag("staging") + mag("outbound_shipping") + mag("wipe_grading") + mag("holding_cost"), 2)
        v01_basis = round(sum_lines + bridge - pp_credit, 2) if is_closed else None
        result_pct = (lifecycle_result / landed) if (lifecycle_result is not None and landed) else None
        liquidation: float | None = None
        projected: float | None = None
        label: str | None = None
        exp_cost: float | None = None
        exp_source: str | None = None
        if not is_closed and estimate_today is not None:
            liquidation = result_if_liquidated_today(sum_lines, estimate_today, fee_pct, fee_fixed)
        if status in ("rented", "awaiting_return", "wip", "in_stock"):
            days_since_return = (as_of - return_date).days if (return_date and return_date <= as_of) else 0
            exp_cost, exp_source = expected_remaining_cost(
                status, family or "", months_remaining, bool(w), inputs_by_family.get(family or "", {"min_n": 30}), a,
                days_since_return=max(days_since_return, 0),
            )
            if est_lease_end is not None:
                projected = result_projected_at_lease_end(sum_lines, remaining_rent, est_lease_end, fee_pct, fee_fixed, exp_cost)
                label = PROJECTED_LABEL_LEASE_END if status in ("rented", "awaiting_return") else PROJECTED_LABEL_SALE

        rows.append({
            "serial": serial,
            "as_of": as_of,
            "slug": slug,
            "model_name": _s(row.model_name),
            "oem": _s(row.oem),
            "catalogue_family": _s(row.catalogue_family),
            "model_family": family,
            "series": _s(row.series),
            "variant_spec": spec,
            "storage_gb": storage,
            "rrp_gross_eur": _r2(rrp_gross),
            "rrp_net_eur": _r2(rrp_n),
            "launch_date": launch,
            "months_since_launch_at_as_of": months_between_float(launch, as_of) if launch else None,
            "po_number": _s(row.po_number),
            "po_line": _i(row.po_line),
            "supplier_id": _s(row.supplier_id),
            "supplier_name": _s(row.supplier_name),
            "supplier_role": _s(row.supplier_role),
            "contract_ref": _s(row.contract_ref),
            "order_date": _d(row.order_date),
            "received_at": received,
            "purchase_date": purchase_date,
            "cohort_month": month_floor(purchase_date) if purchase_date else None,
            "cohort_quarter": _s(p.get("cohort")) or (quarter_label(purchase_date) if purchase_date else None),
            "price_protection_days": _i(row.price_protection_days),
            "price_protection_status": str(pp_status.get(serial, "not_applicable")),
            "price_protection_claimable_eur": float(pp_claim.get(serial, 0.0)),
            "purchase_price": purchase_price,
            "discount_vs_rrp_eur": _r2(rrp_n - effective_price) if rrp_n is not None else None,
            "discount_vs_rrp_pct": ((rrp_n - effective_price) / rrp_n) if rrp_n else None,
            "freight_eur": freight,
            "duty_eur": duty,
            "landed_cost": landed,
            "landed_vs_rrp_pct": ((landed - pp_credit) / rrp_n) if rrp_n else None,
            "price_protection_credit_eur": pp_credit,
            "staging_eur": mag("staging"),
            "outbound_shipping_eur": mag("outbound_shipping"),
            "repair_eur": mag("repair"),
            "replacement_logistics_eur": mag("replacement_logistics"),
            "return_logistics_eur": mag("return_logistics"),
            "wipe_grading_eur": mag("wipe_grading"),
            "refurb_eur": mag("refurbishment"),
            "holding_cost_eur": holding,
            "channel_fee_eur": fee,
            "days_in_stock_to_date": days_in_stock,
            "tco_excl_landed_eur": tco_excl,
            "tco_transactional_eur": tco_transactional,
            "tco_eur": tco_eur,
            "n_lines": n_lines,
            "n_estimate_lines": n_est,
            "first_contract_id": _s(first_c.get(serial, {}).get("contract_id")),
            "customer_id": _s(c.get("customer_id")),
            "term_months": term,
            "monthly_rate": _r2(rate),
            "contract_start": c_start,
            "contract_end_planned": c_end,
            "contract_end_effective": c_eff,
            "months_billed": months_billed,
            "months_remaining": months_remaining,
            "rental_revenue": rent,
            "remaining_contracted_rent": remaining_rent,
            "return_date": return_date,
            "grade_declared": _s(r.get("grade_declared")),
            "grade_inspected": grade_inspected,
            "wipe_certificate_id": _s(r.get("wipe_certificate_id")),
            "grade_out": grade_out,
            "refurb_outcome": refurb_outcome,
            "sellable_date": sellable_date,
            "resale_channel": resale_channel,
            "listed_at": _d(o.get("listed_at")),
            "sale_date": sale_date,
            "credited_at": credited_at if status == "sold" else None,
            "resale_gross": resale_gross if status == "sold" else None,
            "resale_net": _r2(resale_gross - fee) if status == "sold" else None,
            "days_return_to_sale": days_return_to_sale,
            "days_return_to_cash": days_return_to_cash if status == "sold" else None,
            "estimate_run_id": estimate_run_id,
            "grade_used": grade_used,
            "grade_source": grade_source,
            "estimate_rv_today": _r2(estimate_today),
            "estimate_rv_lease_end": est_lease_end,
            "estimate_months_at_lease_end": months_at_end,
            "estimate_rv_source": est_source,
            "estimate_fit_quality": est_fit,
            "estimate_rv_of_record": _r2(est_record),
            "anchor_curve_group": anchor_group,
            "anchor_fit_quality": anchor_fit,
            "anchor_rv_lease_end": anchor_value,
            "estimate_vs_anchor_ratio": est_vs_anchor,
            "realised_vs_record_ratio": realised_vs_record,
            "realised_rv": _r2(realised_rv),
            "lifecycle_result_eur": lifecycle_result,
            "result_v01_basis_eur": v01_basis,
            "result_pct_of_landed": result_pct,
            "result_if_liquidated_today": liquidation,
            "result_projected_at_lease_end": projected,
            "projected_label": label,
            "expected_remaining_cost": exp_cost,
            "expected_cost_inputs_source": exp_source,
            "lifecycle_status": status,
            "is_closed": bool(is_closed),
            "closed_date": closed_date,
            "chain_complete": (bool(tl.get("chain_complete")) if not _missing(tl.get("chain_complete")) else None),
            "is_synthetic": bool(row.gr_synthetic) if not _missing(getattr(row, "gr_synthetic", None)) else is_synth_default,
            "grade_at_sale": grade_at_sale,
        })

    out = pd.DataFrame(rows, columns=DEVICE_LEDGER_COLUMNS + EXTRA_COLUMNS)
    if len(out) == 0:
        return out
    for c in DATE_COLUMNS:
        out[c] = pd.to_datetime(out[c], errors="coerce")
    out = out.sort_values("serial", kind="stable").reset_index(drop=True)
    return out


__all__ = [
    "DEVICE_LEDGER_COLUMNS",
    "EXTRA_COLUMNS",
    "TCO_COLUMN_OF",
    "TCO_EXCL_LANDED_TYPES",
    "DEFAULT_GRADE_D_OFFSET",
    "sums_by_line_type",
    "build_device_ledger",
]
