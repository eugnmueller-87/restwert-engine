"""Contracts register v2: ``silver.contracts`` and the three gold contract tables (SPEC_v0.2 section 8.2).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Pure functions on pandas frames (the bronze tables as read from DuckDB) plus
one runner. v0.1 ``restwert.contracts.register`` is reused unchanged:
``run_contracts_v2`` calls ``run_contracts`` first (so ``contracts_register``
and ``renewal_calendar`` keep feeding R05 and R06), and ``renewal_calendar_v2``
applies the v0.1 ``renewal_calendar`` logic to the v2 register so that
``action_required`` can never drift between the two.

Choices where the spec is silent (all documented here and in docs/CONTRACTS.md):

- ``status``: ``expired`` when ``end_date < as_of``, ``future`` when
  ``start_date > as_of``, else ``active``.
- ``action_required`` on ``silver.contracts`` is exactly the v0.1 calendar
  verdict: the row lies inside the six-month horizon AND (its notice deadline
  falls within two months OR it auto-renews). A row outside the horizon is
  ``False``.
- ``spend_actual_12m_eur`` is windowed on the trailing 12 months
  (``add_months(as_of, -12) + 1 day`` to ``as_of``, inclusive) and derived per
  role from the transaction that names the counterparty: received unit value
  of PO lines whose header carries ``contract_ref`` (manufacturer, reseller,
  rugged OEM); credit note gross of the ``marketplace`` channel (marketplace);
  the matching ledger line types (refurbishment and repair by register
  category; outbound, return and replacement logistics); indirect invoices
  (carrier, financing, mobile threat defense). Where a source carries no
  counterparty reference (credit notes, indirect rows whose supplier name does
  not match), the category total is split equally over the register rows of
  that category and the docs say so. Nothing matched gives NULL, never 0.
- ``n_serials_under_contract`` counts every goods receipt on the contract
  (all time), ``covers_oems`` lists the manufacturers of the slugs bought on
  it (all time) so that a reseller contract shows which manufacturers it
  actually covers.
- ``coverage_by_oem`` attributes reseller purchase orders to the manufacturer
  of the slug: a reseller PO of Apple devices is Apple spend and counts as
  covered when the reseller contract was in force at the order date.
- ``price_protection_window_open``: the register row's ``price_protection_days``
  and ``claim_window_days`` are the authority; a PO line's own
  ``price_protection_days`` is only used when the register row has none.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.config import Thresholds
from restwert.contracts.counterparties import TERMS_NOTE
from restwert.contracts.register import renewal_calendar, run_contracts
from restwert.dates import add_months
from restwert.records import RunSummary

SILVER_CONTRACT_COLUMNS: tuple[str, ...] = (
    "contract_id",
    "counterparty_name",
    "counterparty_role",
    "counterparty_is_public",
    "category",
    "start_date",
    "end_date",
    "notice_days",
    "notice_deadline",
    "auto_renewal",
    "price_protection",
    "price_protection_days",
    "claim_window_days",
    "warranty_months",
    "rebate_tiers_json",
    "volume_commitment_units",
    "payment_terms_days",
    "sla_json",
    "spend_under_contract_eur",
    "spend_actual_12m_eur",
    "spend_actual_vs_planned_pct",
    "covers_oems",
    "n_serials_under_contract",
    "status",
    "days_to_notice_deadline",
    "days_to_end",
    "action_required",
    "terms_note",
    "is_synthetic",
    "as_of",
)

COVERAGE_COLUMNS: tuple[str, ...] = (
    "oem",
    "n_units",
    "spend_total",
    "spend_under_contract",
    "coverage_pct",
    "spend_direct",
    "spend_via_reseller",
    "n_contracts_in_force",
    "next_notice_deadline",
    "as_of",
)

CALENDAR_V2_COLUMNS: tuple[str, ...] = (
    "contract_id",
    "counterparty_name",
    "counterparty_role",
    "category",
    "end_date",
    "notice_days",
    "notice_deadline",
    "days_to_notice_deadline",
    "days_to_end",
    "auto_renewal",
    "spend_under_contract_eur",
    "spend_actual_12m_eur",
    "price_protection_days",
    "claim_window_days",
    "price_protection_window_open",
    "action_required",
    "month_bucket",
    "as_of",
)

REBATE_COLUMNS: tuple[str, ...] = (
    "contract_id",
    "counterparty_name",
    "spend_12m_eur",
    "current_tier_pct",
    "next_tier_from_eur",
    "next_tier_pct",
    "gap_to_next_tier_eur",
    "as_of",
)

HARDWARE_ROLES: tuple[str, ...] = ("manufacturer", "reseller", "rugged_oem")
LOGISTICS_LINE_TYPES: tuple[str, ...] = ("outbound_shipping", "return_logistics", "replacement_logistics")
INDIRECT_ROLES: tuple[str, ...] = ("carrier", "financing", "mtd")
MARKETPLACE_CHANNEL = "marketplace"


# ---------------------------------------------------------------------------
# coercion helpers (local on purpose: this module must not import restwert.kpi)
# ---------------------------------------------------------------------------


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.map(lambda v: float(v) if isinstance(v, Decimal) else v), errors="coerce").astype("float64")


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _bool(series: pd.Series) -> pd.Series:
    return series.map(lambda v: bool(v) if v is not None and v == v else False).astype(bool)


def _empty(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in columns})


def _has(frame: pd.DataFrame | None, *columns: str) -> bool:
    return frame is not None and not frame.empty and all(c in frame.columns for c in columns)


def _window(as_of: date, months: int = 12) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = add_months(as_of, -months) + timedelta(days=1)
    return pd.Timestamp(start), pd.Timestamp(as_of)


def _nullable_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def _to_date_objects(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """datetime64 columns to python ``date`` objects (NaT -> None) for DuckDB DATE columns."""

    for col in columns:
        if col in frame.columns:
            series = pd.to_datetime(frame[col], errors="coerce")
            frame[col] = series.map(lambda v: v.date() if pd.notna(v) else None).astype(object)
    return frame


# ---------------------------------------------------------------------------
# received unit value: the one purchase transaction every hardware measure uses
# ---------------------------------------------------------------------------


def received_units(
    po_headers: pd.DataFrame | None,
    po_lines: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
) -> pd.DataFrame:
    """One row per received serial with its PO line price and PO header fields.

    Columns: serial, po_number, po_line, received_at, slug, storage_gb,
    unit_price_eur, supplier_id, supplier_name, supplier_role, contract_ref,
    order_date. Empty frame with these columns when an input is missing.
    """

    cols = [
        "serial", "po_number", "po_line", "received_at", "slug", "storage_gb", "unit_price_eur",
        "supplier_id", "supplier_name", "supplier_role", "contract_ref", "order_date",
    ]
    if not _has(goods_receipts, "serial", "po_number", "po_line", "received_at") or not _has(
        po_lines, "po_number", "po_line", "unit_price_eur"
    ):
        return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
    gr = goods_receipts[["serial", "po_number", "po_line", "received_at"]].copy()
    gr["po_number"] = gr["po_number"].astype(str)
    gr["po_line"] = _num(gr["po_line"]).astype("Int64")
    gr["received_at"] = _dt(gr["received_at"])
    pl = po_lines.copy()
    pl["po_number"] = pl["po_number"].astype(str)
    pl["po_line"] = _num(pl["po_line"]).astype("Int64")
    pl["unit_price_eur"] = _num(pl["unit_price_eur"])
    keep = ["po_number", "po_line", "unit_price_eur"]
    for c in ("slug", "storage_gb"):
        if c in pl.columns:
            keep.append(c)
    merged = gr.merge(pl[keep], on=["po_number", "po_line"], how="left")
    if _has(po_headers, "po_number"):
        ph = po_headers.copy()
        ph["po_number"] = ph["po_number"].astype(str)
        hcols = ["po_number"] + [c for c in ("supplier_id", "supplier_name", "supplier_role", "contract_ref", "order_date") if c in ph.columns]
        merged = merged.merge(ph[hcols], on="po_number", how="left")
    for c in cols:
        if c not in merged.columns:
            merged[c] = None
    if "order_date" in merged.columns:
        merged["order_date"] = _dt(merged["order_date"])
    return merged[cols]


def _in_force_at(ctr: pd.DataFrame, contract_ref: pd.Series, at: pd.Series) -> pd.Series:
    """True where ``contract_ref`` names a register row with ``start_date <= at <= end_date``."""

    if ctr is None or ctr.empty or "contract_id" not in ctr.columns:
        return pd.Series(False, index=contract_ref.index)
    reg = ctr[["contract_id", "start_date", "end_date"]].copy()
    reg["contract_id"] = reg["contract_id"].astype(str)
    reg["start_date"] = _dt(reg["start_date"])
    reg["end_date"] = _dt(reg["end_date"])
    reg = reg.drop_duplicates("contract_id").set_index("contract_id")
    ref = contract_ref.astype(object).where(contract_ref.notna(), None).map(lambda v: None if v is None else str(v))
    start = ref.map(reg["start_date"].to_dict())
    end = ref.map(reg["end_date"].to_dict())
    start = pd.to_datetime(start, errors="coerce")
    end = pd.to_datetime(end, errors="coerce")
    at = pd.to_datetime(at, errors="coerce")
    return (ref.notna() & start.notna() & end.notna() & (start <= at) & (at <= end)).fillna(False).astype(bool)


# ---------------------------------------------------------------------------
# silver.contracts
# ---------------------------------------------------------------------------


def _register_shape(c: pd.DataFrame) -> pd.DataFrame:
    """The v2 register in the v0.1 ``contracts_register`` shape (supplier rows only)."""

    return pd.DataFrame(
        {
            "contract_type": "supplier",
            "contract_id": c["contract_id"].astype(str),
            "counterparty": c["counterparty_name"].astype(str),
            "category": c["category"].astype(object),
            "start_date": c["start_date"],
            "end_date": c["end_date"],
            "notice_days": c["notice_days"],
            "notice_deadline": c["notice_deadline"],
            "auto_renewal": c["auto_renewal"],
            "price_protection": c["price_protection"].astype(object),
            "price_protection_days": c["claim_window_days"].astype(object),
            "payment_terms_days": c["payment_terms_days"].astype(object),
            "annual_value": c["spend_under_contract_eur"],
            "status": c["status"],
        }
    )


def _spend_hardware(c: pd.DataFrame, units: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> tuple[pd.Series, pd.Series]:
    """(spend in window, serials all time) keyed by contract id for hardware roles."""

    if units.empty or units["contract_ref"].isna().all():
        return pd.Series(dtype="float64"), pd.Series(dtype="int64")
    u = units[units["contract_ref"].notna()].copy()
    u["contract_ref"] = u["contract_ref"].astype(str)
    n_serials = u.groupby("contract_ref")["serial"].nunique()
    win = u[(u["received_at"] >= lo) & (u["received_at"] <= hi) & u["unit_price_eur"].notna()]
    spend = win.groupby("contract_ref")["unit_price_eur"].sum()
    return spend, n_serials


def _spend_marketplace(c: pd.DataFrame, credit_notes: pd.DataFrame | None, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Series:
    """Marketplace credit note gross in the window, split equally over the marketplace rows."""

    rows = c[c["counterparty_role"] == "marketplace"]
    out = pd.Series(np.nan, index=rows.index, dtype="float64")
    if rows.empty or not _has(credit_notes, "channel", "credited_at", "gross_eur"):
        return out
    cn = credit_notes.copy()
    cn["credited_at"] = _dt(cn["credited_at"])
    cn["gross_eur"] = _num(cn["gross_eur"])
    sel = cn[(cn["channel"].astype(str) == MARKETPLACE_CHANNEL) & (cn["credited_at"] >= lo) & (cn["credited_at"] <= hi)]
    if sel.empty:
        return out
    total = float(sel["gross_eur"].sum())
    active = rows[rows["status"] == "active"]
    targets = active if not active.empty else rows
    out.loc[targets.index] = round(total / len(targets), 2)
    return out


def _spend_lines(c: pd.DataFrame, lines: pd.DataFrame | None, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Series:
    """Ledger line magnitudes in the window for refurb_repair (by category) and logistics rows."""

    rows = c[c["counterparty_role"].isin(["refurb_repair", "logistics"])]
    out = pd.Series(np.nan, index=rows.index, dtype="float64")
    if rows.empty or not _has(lines, "line_type", "event_date", "amount_eur"):
        return out
    ll = lines.copy()
    ll["event_date"] = _dt(ll["event_date"])
    ll["amount_eur"] = _num(ll["amount_eur"]).abs()
    ll = ll[(ll["event_date"] >= lo) & (ll["event_date"] <= hi)]
    if ll.empty:
        return out
    by_type = ll.groupby(ll["line_type"].astype(str))["amount_eur"].sum()
    for idx, row in rows.iterrows():
        if row["counterparty_role"] == "refurb_repair":
            types = [str(row["category"])]
        else:
            types = list(LOGISTICS_LINE_TYPES)
        present = [t for t in types if t in by_type.index]
        if present:
            out.loc[idx] = round(float(by_type.loc[present].sum()), 2)
    return out


def _spend_indirect(c: pd.DataFrame, indirect: pd.DataFrame | None, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Series:
    """Indirect invoices in the window: by supplier name when it matches, else category total split equally."""

    rows = c[c["counterparty_role"].isin(INDIRECT_ROLES)]
    out = pd.Series(np.nan, index=rows.index, dtype="float64")
    if rows.empty or not _has(indirect, "invoice_date", "category", "amount_eur"):
        return out
    ind = indirect.copy()
    ind["invoice_date"] = _dt(ind["invoice_date"])
    ind["amount_eur"] = _num(ind["amount_eur"])
    ind = ind[(ind["invoice_date"] >= lo) & (ind["invoice_date"] <= hi)]
    if ind.empty:
        return out
    names = ind["supplier_name"].astype(str) if "supplier_name" in ind.columns else pd.Series("", index=ind.index)
    by_name = ind.groupby(names)["amount_eur"].sum()
    by_cat = ind.groupby(ind["category"].astype(str))["amount_eur"].sum()
    for idx, row in rows.iterrows():
        name = str(row["counterparty_name"])
        if name in by_name.index:
            out.loc[idx] = round(float(by_name.loc[name]), 2)
            continue
        cat = str(row["category"])
        if cat in by_cat.index:
            n_same = int((rows["category"].astype(str) == cat).sum())
            out.loc[idx] = round(float(by_cat.loc[cat]) / max(n_same, 1), 2)
    return out


def _covers_oems(units: pd.DataFrame, cat_models: pd.DataFrame | None) -> pd.Series:
    """Comma list of manufacturers of the slugs bought on each contract (keyed by contract id)."""

    if units.empty or units["contract_ref"].isna().all() or not _has(cat_models, "slug", "oem"):
        return pd.Series(dtype=object)
    slug_oem = cat_models.drop_duplicates("slug").set_index(cat_models["slug"].astype(str))["oem"].astype(str)
    u = units[units["contract_ref"].notna() & units["slug"].notna()].copy()
    u["oem"] = u["slug"].astype(str).map(slug_oem)
    u = u[u["oem"].notna()]
    if u.empty:
        return pd.Series(dtype=object)
    return u.groupby(u["contract_ref"].astype(str))["oem"].agg(lambda s: ", ".join(sorted(set(s))))


def contracts_v2(
    ctr: pd.DataFrame,
    po_headers: pd.DataFrame | None,
    po_lines: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
    credit_notes: pd.DataFrame | None,
    lines: pd.DataFrame | None,
    indirect: pd.DataFrame | None,
    as_of: date,
    cat_models: pd.DataFrame | None = None,
    horizon_months: int = 6,
) -> pd.DataFrame:
    """Build ``silver.contracts`` from ``bronze.ctr_register`` and the transactions that name a counterparty.

    Every register row survives; the added columns are status, the notice
    deadline, days to deadline and end, the v0.1 ``action_required`` verdict,
    the trailing-12-month actual spend per role, ``covers_oems`` and
    ``n_serials_under_contract``. See the module docstring for every choice.
    """

    if ctr is None or ctr.empty:
        return _empty(SILVER_CONTRACT_COLUMNS)
    c = ctr.copy().reset_index(drop=True)
    c["contract_id"] = c["contract_id"].astype(str)
    c["start_date"] = _dt(c["start_date"])
    c["end_date"] = _dt(c["end_date"])
    c["notice_days"] = _num(c["notice_days"]).fillna(0).astype(int)
    c["notice_deadline"] = c["end_date"] - pd.to_timedelta(c["notice_days"], unit="D")
    for col in ("auto_renewal", "price_protection", "counterparty_is_public"):
        c[col] = _bool(c[col]) if col in c.columns else False
    for col in ("price_protection_days", "claim_window_days", "warranty_months", "volume_commitment_units"):
        c[col] = _nullable_int(c[col]) if col in c.columns else pd.Series(pd.NA, index=c.index, dtype="Int64")
    c["payment_terms_days"] = _num(c["payment_terms_days"]).fillna(0).astype(int) if "payment_terms_days" in c.columns else 0
    c["spend_under_contract_eur"] = _num(c["spend_under_contract_eur"]).fillna(0.0) if "spend_under_contract_eur" in c.columns else 0.0
    for col in ("rebate_tiers_json", "sla_json"):
        if col not in c.columns:
            c[col] = None
    if "terms_note" not in c.columns or c["terms_note"].isna().all():
        c["terms_note"] = TERMS_NOTE
    if "is_synthetic" in c.columns:
        c["is_synthetic"] = _bool(c["is_synthetic"])
    else:
        # conservative default: an unlabelled register is treated as synthetic, never as real
        c["is_synthetic"] = True

    ts = pd.Timestamp(as_of)
    c["status"] = np.where(c["end_date"] < ts, "expired", np.where(c["start_date"] > ts, "future", "active"))
    c["days_to_notice_deadline"] = (c["notice_deadline"] - ts).dt.days.astype("Int64")
    c["days_to_end"] = (c["end_date"] - ts).dt.days.astype("Int64")

    # action_required: exactly the v0.1 calendar verdict on the v0.1 shape
    cal = renewal_calendar(_register_shape(c), as_of, horizon_months=horizon_months)
    action = pd.Series(False, index=c.index)
    if not cal.empty:
        flagged = cal.loc[_bool(cal["action_required"]), "contract_id"].astype(str)
        action = c["contract_id"].isin(set(flagged))
    c["action_required"] = action.astype(bool)

    # actual spend, trailing 12 months, per role
    lo, hi = _window(as_of, 12)
    units = received_units(po_headers, po_lines, goods_receipts)
    spend = pd.Series(np.nan, index=c.index, dtype="float64")
    n_serials = pd.Series(pd.NA, index=c.index, dtype="Int64")
    hw_spend, hw_serials = _spend_hardware(c, units, lo, hi)
    hw_rows = c["counterparty_role"].isin(HARDWARE_ROLES)
    if not hw_serials.empty:
        mapped = c.loc[hw_rows, "contract_id"].map(hw_serials)
        n_serials.loc[hw_rows] = mapped.fillna(0).astype("Int64")
        spend.loc[hw_rows] = c.loc[hw_rows, "contract_id"].map(hw_spend).astype("float64")
        # a contract with receipts but none in the window has spent 0 in the window; one with no receipts at all stays NULL
        had_any = c.loc[hw_rows, "contract_id"].isin(set(hw_serials.index))
        fill_zero = hw_rows.copy()
        fill_zero.loc[hw_rows] = had_any.values & spend.loc[hw_rows].isna().values
        spend.loc[fill_zero] = 0.0
    mk = _spend_marketplace(c, credit_notes, lo, hi)
    spend.loc[mk.index] = mk
    ln = _spend_lines(c, lines, lo, hi)
    spend.loc[ln.index] = ln
    ind = _spend_indirect(c, indirect, lo, hi)
    spend.loc[ind.index] = ind
    c["spend_actual_12m_eur"] = spend.round(2)
    c["n_serials_under_contract"] = n_serials
    planned = c["spend_under_contract_eur"].astype("float64")
    c["spend_actual_vs_planned_pct"] = np.where(
        (planned > 0) & c["spend_actual_12m_eur"].notna(), c["spend_actual_12m_eur"] / planned.where(planned > 0, np.nan), np.nan
    )
    covers = _covers_oems(units, cat_models)
    c["covers_oems"] = c["contract_id"].map(covers).astype(object).where(lambda s: s.notna(), None)
    c["as_of"] = ts
    out = c[list(SILVER_CONTRACT_COLUMNS)].sort_values(["counterparty_role", "counterparty_name", "contract_id"]).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# gold.contract_coverage_by_oem
# ---------------------------------------------------------------------------


def coverage_by_oem(
    po_headers: pd.DataFrame | None,
    po_lines: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
    cat_models: pd.DataFrame | None,
    ctr: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Share of received unit value under a contract in force, per manufacturer of the slug.

    A reseller purchase order of Apple devices is Apple spend; it is covered
    when the reseller contract named on the header was in force at the order
    date. ``spend_direct`` / ``spend_via_reseller`` split the total by
    ``supplier_role``. ``n_contracts_in_force`` counts the manufacturer's own
    contract plus every contract its purchase orders referenced, when in force
    at ``as_of``; ``next_notice_deadline`` is the earliest deadline still ahead.
    """

    units = received_units(po_headers, po_lines, goods_receipts)
    ts = pd.Timestamp(as_of)
    lo, hi = _window(as_of, 12)
    reg = ctr.copy() if ctr is not None and not ctr.empty else pd.DataFrame()
    if not reg.empty:
        reg["contract_id"] = reg["contract_id"].astype(str)
        reg["start_date"] = _dt(reg["start_date"])
        reg["end_date"] = _dt(reg["end_date"])
        reg["notice_days"] = _num(reg["notice_days"]).fillna(0).astype(int)
        reg["notice_deadline"] = reg["end_date"] - pd.to_timedelta(reg["notice_days"], unit="D")
        reg["in_force"] = (reg["start_date"] <= ts) & (ts <= reg["end_date"])

    if _has(cat_models, "slug", "oem") and not units.empty:
        slug_oem = cat_models.drop_duplicates("slug").set_index(cat_models["slug"].astype(str))["oem"].astype(str)
        units["oem"] = units["slug"].astype(object).map(lambda v: slug_oem.get(str(v)) if v is not None and v == v else None)
    else:
        units["oem"] = None
    units["oem"] = units["oem"].fillna("(unknown slug)")
    units["covered"] = _in_force_at(reg, units["contract_ref"], units["order_date"]) if not reg.empty else False
    win = units[(units["received_at"] >= lo) & (units["received_at"] <= hi) & units["unit_price_eur"].notna()].copy()

    oems: list[str] = sorted(set(win["oem"].astype(str)))
    if not reg.empty and "counterparty_role" in reg.columns:
        oems = sorted(set(oems) | set(reg.loc[reg["counterparty_role"] == "manufacturer", "counterparty_name"].astype(str)))
    rows: list[dict[str, Any]] = []
    for oem in oems:
        grp = win[win["oem"].astype(str) == oem]
        total = float(grp["unit_price_eur"].sum()) if not grp.empty else 0.0
        under = float(grp.loc[grp["covered"], "unit_price_eur"].sum()) if not grp.empty else 0.0
        role = grp["supplier_role"].astype(str) if not grp.empty else pd.Series(dtype=str)
        direct = float(grp.loc[role == "manufacturer", "unit_price_eur"].sum()) if not grp.empty else 0.0
        via = float(grp.loc[role == "reseller", "unit_price_eur"].sum()) if not grp.empty else 0.0
        ids: set[str] = set()
        if not reg.empty:
            own = reg[(reg["counterparty_role"].astype(str) == "manufacturer") & (reg["counterparty_name"].astype(str) == oem)]
            ids |= set(own["contract_id"])
            refs = units.loc[(units["oem"].astype(str) == oem) & units["contract_ref"].notna(), "contract_ref"].astype(str)
            ids |= set(refs)
            in_force = reg[reg["contract_id"].isin(ids) & reg["in_force"]]
            ahead = in_force.loc[in_force["notice_deadline"] >= ts, "notice_deadline"]
            n_in_force = int(len(in_force))
            next_deadline = ahead.min() if not ahead.empty else pd.NaT
        else:
            n_in_force, next_deadline = 0, pd.NaT
        rows.append(
            {
                "oem": oem,
                "n_units": int(len(grp)),
                "spend_total": round(total, 2) if not grp.empty else None,
                "spend_under_contract": round(under, 2) if not grp.empty else None,
                "coverage_pct": (under / total) if total > 0 else None,
                "spend_direct": round(direct, 2) if not grp.empty else None,
                "spend_via_reseller": round(via, 2) if not grp.empty else None,
                "n_contracts_in_force": n_in_force,
                "next_notice_deadline": next_deadline,
                "as_of": ts,
            }
        )
    if not rows:
        return _empty(COVERAGE_COLUMNS)
    out = pd.DataFrame.from_records(rows, columns=list(COVERAGE_COLUMNS))
    out["next_notice_deadline"] = pd.to_datetime(out["next_notice_deadline"], errors="coerce")
    return out


# ---------------------------------------------------------------------------
# gold.renewal_calendar_v2
# ---------------------------------------------------------------------------


def price_protection_windows_open(
    contracts: pd.DataFrame,
    po_lines: pd.DataFrame | None,
    price_changes: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
    as_of: date,
    po_headers: pd.DataFrame | None = None,
) -> pd.Series:
    """Per contract id: True when a PO line on it saw a price drop inside its protection window whose claim window is still open.

    Window: ``valid_from`` in ``(received, received + price_protection_days]``
    with ``received`` = the last receipt of the PO line; open when
    ``valid_from + claim_window_days >= as_of``. The register row's days are
    the authority, the PO line's ``price_protection_days`` only fills a gap.
    """

    ids = contracts["contract_id"].astype(str)
    out = pd.Series(False, index=ids.values, dtype=bool)
    if not _has(po_lines, "po_number", "po_line", "slug", "storage_gb") or not _has(price_changes, "slug", "storage_gb", "valid_from"):
        return out
    if not _has(goods_receipts, "po_number", "po_line", "received_at"):
        return out
    pl = po_lines.copy()
    pl["po_number"] = pl["po_number"].astype(str)
    pl["po_line"] = _num(pl["po_line"]).astype("Int64")
    if "contract_ref" not in pl.columns or "supplier_id" not in pl.columns:
        if not _has(po_headers, "po_number", "contract_ref"):
            return out
        ph = po_headers.copy()
        ph["po_number"] = ph["po_number"].astype(str)
        hcols = ["po_number", "contract_ref"] + (["supplier_id"] if "supplier_id" in ph.columns else [])
        pl = pl.drop(columns=[c for c in ("contract_ref", "supplier_id") if c in pl.columns]).merge(ph[hcols], on="po_number", how="left")
    if "supplier_id" not in pl.columns:
        pl["supplier_id"] = None
    pl = pl[pl["contract_ref"].notna()].copy()
    if pl.empty:
        return out
    pl["contract_ref"] = pl["contract_ref"].astype(str)
    gr = goods_receipts.copy()
    gr["po_number"] = gr["po_number"].astype(str)
    gr["po_line"] = _num(gr["po_line"]).astype("Int64")
    gr["received_at"] = _dt(gr["received_at"])
    last = gr.groupby(["po_number", "po_line"], as_index=False)["received_at"].max()
    pl = pl.merge(last, on=["po_number", "po_line"], how="inner")
    if pl.empty:
        return out
    terms = contracts[["contract_id", "price_protection_days", "claim_window_days"]].copy()
    terms["contract_id"] = terms["contract_id"].astype(str)
    terms = terms.drop_duplicates("contract_id").set_index("contract_id")
    pl["ppd"] = pd.to_numeric(pl["contract_ref"].map(terms["price_protection_days"]), errors="coerce")
    if "price_protection_days" in pl.columns:
        pl["ppd"] = pl["ppd"].fillna(pd.to_numeric(pl["price_protection_days"], errors="coerce"))
    pl["cwd"] = pd.to_numeric(pl["contract_ref"].map(terms["claim_window_days"]), errors="coerce")
    pl = pl[pl["ppd"].notna() & (pl["ppd"] > 0)].copy()
    if pl.empty:
        return out
    pc = price_changes.copy()
    pc["valid_from"] = _dt(pc["valid_from"])
    pc["slug"] = pc["slug"].astype(str)
    pc["storage_gb"] = _num(pc["storage_gb"]).astype("Int64")
    pl["slug"] = pl["slug"].astype(str)
    pl["storage_gb"] = _num(pl["storage_gb"]).astype("Int64")
    keys = ["slug", "storage_gb"]
    if "supplier_id" in pc.columns and pl["supplier_id"].notna().any():
        pc["supplier_id"] = pc["supplier_id"].astype(str)
        pl["supplier_id"] = pl["supplier_id"].astype(object).map(lambda v: None if v is None or v != v else str(v))
        keys = ["supplier_id", "slug", "storage_gb"]
    m = pl.merge(pc[keys + ["valid_from"]], on=keys, how="inner")
    if m.empty:
        return out
    ts = pd.Timestamp(as_of)
    lower = m["received_at"]
    upper = m["received_at"] + pd.to_timedelta(m["ppd"], unit="D")
    inside = (m["valid_from"] > lower) & (m["valid_from"] <= upper)
    claim_end = m["valid_from"] + pd.to_timedelta(m["cwd"].fillna(0), unit="D")
    still_open = claim_end >= ts
    hit = m.loc[inside & still_open, "contract_ref"].unique()
    out.loc[out.index.isin(hit)] = True
    return out


def renewal_calendar_v2(
    contracts: pd.DataFrame,
    po_lines: pd.DataFrame | None,
    price_changes: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
    as_of: date,
    horizon_months: int = 6,
    po_headers: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """v0.1 ``renewal_calendar`` on ``silver.contracts`` plus role, category, spend and price protection columns.

    ``action_required`` is the v0.1 verdict, untouched. ``price_protection_window_open``
    comes from ``price_protection_windows_open``.
    """

    if contracts is None or contracts.empty:
        return _empty(CALENDAR_V2_COLUMNS)
    c = contracts.copy()
    c["contract_id"] = c["contract_id"].astype(str)
    c["start_date"] = _dt(c["start_date"])
    c["end_date"] = _dt(c["end_date"])
    c["notice_deadline"] = _dt(c["notice_deadline"])
    c["auto_renewal"] = _bool(c["auto_renewal"])
    c["price_protection"] = _bool(c["price_protection"]) if "price_protection" in c.columns else False
    c["spend_under_contract_eur"] = _num(c["spend_under_contract_eur"])
    if "status" not in c.columns:
        c["status"] = np.where(c["end_date"] < pd.Timestamp(as_of), "expired", "active")
    cal = renewal_calendar(_register_shape(c), as_of, horizon_months=horizon_months)
    if cal.empty:
        return _empty(CALENDAR_V2_COLUMNS)
    extra_cols = ["contract_id", "counterparty_name", "counterparty_role", "category", "spend_under_contract_eur"]
    for col in ("spend_actual_12m_eur", "price_protection_days", "claim_window_days"):
        if col in c.columns:
            extra_cols.append(col)
    extra = c[extra_cols].drop_duplicates("contract_id")
    cal["contract_id"] = cal["contract_id"].astype(str)
    out = cal.merge(extra, on="contract_id", how="left")
    for col in ("spend_actual_12m_eur", "price_protection_days", "claim_window_days"):
        if col not in out.columns:
            out[col] = None
    open_windows = price_protection_windows_open(c, po_lines, price_changes, goods_receipts, as_of, po_headers=po_headers)
    out["price_protection_window_open"] = out["contract_id"].map(open_windows).fillna(False).astype(bool)
    out["as_of"] = pd.Timestamp(as_of)
    out["action_required"] = _bool(out["action_required"])
    out["spend_actual_12m_eur"] = _num(out["spend_actual_12m_eur"]) if out["spend_actual_12m_eur"].notna().any() else out["spend_actual_12m_eur"]
    for col in ("price_protection_days", "claim_window_days"):
        out[col] = _nullable_int(out[col])
    out = out[list(CALENDAR_V2_COLUMNS)].sort_values(["end_date", "contract_id"]).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# gold.rebate_progress
# ---------------------------------------------------------------------------


def _parse_tiers(value: Any) -> list[dict[str, float]]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        text = str(value).strip()
        if not text or text.lower() in ("none", "nan", "null"):
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    tiers: list[dict[str, float]] = []
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, dict) and "from_eur" in t and "pct" in t:
            tiers.append({"from_eur": float(t["from_eur"]), "pct": float(t["pct"])})
    return sorted(tiers, key=lambda t: t["from_eur"])


def rebate_progress(
    contracts: pd.DataFrame,
    po_headers: pd.DataFrame | None,
    po_lines: pd.DataFrame | None,
    goods_receipts: pd.DataFrame | None,
    as_of: date,
) -> pd.DataFrame:
    """For register rows with rebate tiers: trailing-12-month spend, current tier, next tier and the gap.

    ``spend_12m_eur`` is the received unit value on the contract in the window;
    when no purchase data is passed it falls back to ``spend_actual_12m_eur`` of
    the register row, and stays NULL (with every tier column NULL) when neither
    exists. A contract already in the top tier has NULL next tier and NULL gap.
    """

    if contracts is None or contracts.empty or "rebate_tiers_json" not in contracts.columns:
        return _empty(REBATE_COLUMNS)
    c = contracts.copy()
    c["contract_id"] = c["contract_id"].astype(str)
    c["tiers"] = c["rebate_tiers_json"].map(_parse_tiers)
    c = c[c["tiers"].map(len) > 0]
    if c.empty:
        return _empty(REBATE_COLUMNS)
    units = received_units(po_headers, po_lines, goods_receipts)
    lo, hi = _window(as_of, 12)
    spend_by_contract: pd.Series | None = None
    if not units.empty and units["contract_ref"].notna().any():
        u = units[units["contract_ref"].notna() & (units["received_at"] >= lo) & (units["received_at"] <= hi)]
        spend_by_contract = u.groupby(u["contract_ref"].astype(str))["unit_price_eur"].sum()
    rows: list[dict[str, Any]] = []
    for _, row in c.iterrows():
        spend: float | None = None
        if spend_by_contract is not None:
            spend = float(spend_by_contract.get(row["contract_id"], 0.0))
        elif "spend_actual_12m_eur" in c.columns and pd.notna(row.get("spend_actual_12m_eur")):
            spend = float(row["spend_actual_12m_eur"])
        tiers = row["tiers"]
        if spend is None:
            current = next_from = next_pct = gap = None
        else:
            reached = [t for t in tiers if t["from_eur"] <= spend]
            ahead = [t for t in tiers if t["from_eur"] > spend]
            current = reached[-1]["pct"] if reached else 0.0
            next_from = ahead[0]["from_eur"] if ahead else None
            next_pct = ahead[0]["pct"] if ahead else None
            gap = round(next_from - spend, 2) if next_from is not None else None
        rows.append(
            {
                "contract_id": row["contract_id"],
                "counterparty_name": str(row["counterparty_name"]),
                "spend_12m_eur": None if spend is None else round(spend, 2),
                "current_tier_pct": current,
                "next_tier_from_eur": next_from,
                "next_tier_pct": next_pct,
                "gap_to_next_tier_eur": gap,
                "as_of": pd.Timestamp(as_of),
            }
        )
    out = pd.DataFrame.from_records(rows, columns=list(REBATE_COLUMNS))
    for col in ("spend_12m_eur", "current_tier_pct", "next_tier_from_eur", "next_tier_pct", "gap_to_next_tier_eur"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    return out.sort_values("contract_id").reset_index(drop=True)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _read_optional(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame | None:
    if not db.table_exists(con, table):
        return None
    return db.read_df(con, f"SELECT * FROM {table}")


def _for_duckdb(frame: pd.DataFrame, date_cols: Iterable[str]) -> pd.DataFrame:
    out = _to_date_objects(frame.copy(), date_cols)
    for col in out.columns:
        if str(out[col].dtype) == "Int64":
            out[col] = out[col].astype(object).where(out[col].notna(), None)
    return out


def run_contracts_v2(con: duckdb.DuckDBPyConnection, as_of: date, thr: Thresholds) -> RunSummary:
    """v0.1 ``run_contracts`` first, then ``silver.contracts`` and the three gold contract tables.

    Without ``bronze.ctr_register`` (a v0.1 database) only the v0.1 part runs
    and the summary says so. No side effect beyond the tables and the runs row.
    """

    from restwert.lake.schema_lake import create_lake_schema

    started = datetime.now(timezone.utc)
    v1 = run_contracts(con, as_of, thr)
    counts: dict[str, int] = dict(v1.counts)
    notes: list[str] = list(v1.notes)
    if not db.table_exists(con, "bronze.ctr_register"):
        notes.append("bronze.ctr_register missing: register v2 skipped (v0.1 database)")
        finished = datetime.now(timezone.utc)
        return RunSummary(
            command="contracts",
            run_id=v1.run_id,
            started_at=started,
            finished_at=finished,
            seconds=(finished - started).total_seconds(),
            counts=counts,
            notes=notes,
        )
    create_lake_schema(con, drop_layers=())
    run_id = db.new_run(con, "contracts_v2", None, as_of, None)

    ctr = _read_optional(con, "bronze.ctr_register")
    po_headers = _read_optional(con, "bronze.erp_purchase_orders")
    po_lines = _read_optional(con, "bronze.erp_po_lines")
    goods_receipts = _read_optional(con, "bronze.erp_goods_receipts")
    credit_notes = _read_optional(con, "bronze.rc_credit_notes")
    price_changes = _read_optional(con, "bronze.erp_price_changes")
    indirect = _read_optional(con, "bronze.fin_indirect_spend")
    cat_models = _read_optional(con, "bronze.cat_models")
    lines = _read_optional(con, "silver.ledger_lines")
    if lines is None or lines.empty:
        notes.append("silver.ledger_lines missing or empty: refurbishment, repair and logistics spend stay NULL (run ledger first)")

    contracts = contracts_v2(ctr, po_headers, po_lines, goods_receipts, credit_notes, lines, indirect, as_of, cat_models=cat_models)
    coverage = coverage_by_oem(po_headers, po_lines, goods_receipts, cat_models, ctr, as_of)
    calendar = renewal_calendar_v2(contracts, po_lines, price_changes, goods_receipts, as_of, po_headers=po_headers)
    rebates = rebate_progress(contracts, po_headers, po_lines, goods_receipts, as_of)

    n_contracts = db.write_df(con, "silver.contracts", _for_duckdb(contracts, ("start_date", "end_date", "notice_deadline", "as_of")), mode="replace")
    n_cov = db.write_df(con, "gold.contract_coverage_by_oem", _for_duckdb(coverage, ("next_notice_deadline", "as_of")), mode="replace")
    n_cal = db.write_df(con, "gold.renewal_calendar_v2", _for_duckdb(calendar, ("end_date", "notice_deadline", "as_of")), mode="replace")
    n_reb = db.write_df(con, "gold.rebate_progress", _for_duckdb(rebates, ("as_of",)), mode="replace")

    counts.update(
        {
            "silver_contracts": int(n_contracts),
            "contract_coverage_by_oem": int(n_cov),
            "renewal_calendar_v2": int(n_cal),
            "rebate_progress": int(n_reb),
            "action_required_v2": int(contracts["action_required"].sum()) if not contracts.empty else 0,
            "price_protection_window_open": int(calendar["price_protection_window_open"].sum()) if not calendar.empty else 0,
        }
    )
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="contracts",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=(finished - started).total_seconds(),
        counts=counts,
        notes=notes,
    )


__all__ = [
    "SILVER_CONTRACT_COLUMNS",
    "COVERAGE_COLUMNS",
    "CALENDAR_V2_COLUMNS",
    "REBATE_COLUMNS",
    "received_units",
    "contracts_v2",
    "coverage_by_oem",
    "price_protection_windows_open",
    "renewal_calendar_v2",
    "rebate_progress",
    "run_contracts_v2",
]
