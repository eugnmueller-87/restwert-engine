"""The 17 ledger line types and the pure builder of ``silver.ledger_lines`` (SPEC_v0.2 6.1).

Every line is one bronze transaction with a ``source_ref`` of the form
``<system>:<external_ref>``; the exceptions are flagged on the row itself:

* ``holding_cost`` is an estimate by design (``days x holding_cost_per_day_eur``), one line
  per stock phase of the serial (inbound: receipt to shipment; return: return receipt to
  sellable; sale: sellable to sold; ``HOLDING_PHASES``), ``is_estimate = true``,
  ``allocation_basis = days_x_rate``;
* ``support`` and ``mdm_operations`` are allocations of team cost (TCO_DEFINITION section 3):
  one estimate line per billed rental month of the serial (``support_cost_per_device_month_eur``
  on every rental invoice, ``mdm_cost_per_device_month_eur`` only when the staging log marks
  the serial ``mdm_enrolled``), ``allocation_basis = months_x_rate``, ``source_ref``
  ``assumptions:<key>:<serial>:<invoice_id>`` so the line stays tied to the invoice it rides on;
* ``purchase_price`` falls back to the PO line price while the unit invoice is
  pending (``assumption_key = po_line_price_pending_invoice``);
* ``channel_fee`` falls back to the channel fee assumption while the credit note
  is missing (``assumption_key = channel_fees``).

Freight, duty and price protection credits are the only allocated lines: an
invoice line of the PO line is split to the cent over the received serials of that
line in serial order (``common.allocate_cents``: floor per unit, remainder on the
last serial), ``allocation_basis = per_unit_of_po_line``.

Sign convention (decision D4): revenue positive, cost negative, so the lifecycle
result of a serial is ``SUM(amount_eur)``. Every amount is net of VAT.

Rules: a line is booked only when its ``event_date <= as_of``; open ticket quotes
are never lines; a scrapped device has no ``resale_gross`` line; a sale dated after
``as_of`` is not a sale.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from restwert.lake.common import line_id as _line_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

    from restwert.config import Assumptions


LINE_TYPES: dict[str, str] = {  # line_type -> line_class, in cycle order
    "purchase_price": "cost",
    "freight": "cost",
    "duty": "cost",
    "staging": "cost",
    "outbound_shipping": "cost",
    "rental_revenue": "revenue",
    "support": "cost",
    "mdm_operations": "cost",
    "repair": "cost",
    "replacement_logistics": "cost",
    "return_logistics": "cost",
    "wipe_grading": "cost",
    "refurbishment": "cost",
    "holding_cost": "cost",
    "resale_gross": "revenue",
    "channel_fee": "cost",
    "price_protection_credit": "revenue",
}
LEDGER_ORDER: tuple[str, ...] = tuple(LINE_TYPES)
ESTIMATE_LINE_TYPES: tuple[str, ...] = ("holding_cost", "support", "mdm_operations")
V01_BRIDGE_LINE_TYPES: tuple[str, ...] = (
    "staging",
    "outbound_shipping",
    "wipe_grading",
    "holding_cost",
    "support",
    "mdm_operations",
    "price_protection_credit",
)
LANDED_LINE_TYPES: tuple[str, ...] = ("purchase_price", "freight", "duty")
COST_LINE_TYPES: tuple[str, ...] = tuple(t for t, c in LINE_TYPES.items() if c == "cost")
REVENUE_LINE_TYPES: tuple[str, ...] = tuple(t for t, c in LINE_TYPES.items() if c == "revenue")

#: Every column of ``silver.ledger_lines`` in DDL order.
LEDGER_LINE_COLUMNS: list[str] = [
    "line_id", "serial", "line_type", "line_class", "amount_eur", "event_date", "period_month",
    "source_system", "source_table", "source_ref", "delivery_id", "allocation_basis", "is_estimate",
    "assumption_key", "assumption_owner", "counterparty", "counterparty_role", "contract_ref",
    "as_of", "is_synthetic",
]

PENDING_INVOICE_KEY = "po_line_price_pending_invoice"
PENDING_INVOICE_OWNER = "Head of Procurement (name)"
CHANNEL_FEE_KEY = "channel_fees"
HOLDING_COST_KEY = "holding_cost_per_day_eur"
SUPPORT_COST_KEY = "support_cost_per_device_month_eur"
MDM_COST_KEY = "mdm_cost_per_device_month_eur"

BRONZE_SHORT_NAMES: tuple[str, ...] = (
    "cat_models", "cat_variants", "mkt_curves", "erp_purchase_orders", "erp_po_lines", "erp_goods_receipts",
    "erp_supplier_invoices", "erp_price_changes", "wms_staging_log", "wms_shipments", "portal_rental_contracts",
    "portal_rental_invoices", "sd_tickets", "ret_receipts", "rf_work_orders", "rc_orders", "rc_credit_notes",
    "ctr_register", "fin_indirect_spend",
)


@dataclass
class BronzeFrames:
    """Every bronze table by short name; a missing table is an empty frame."""

    cat_models: pd.DataFrame
    cat_variants: pd.DataFrame
    mkt_curves: pd.DataFrame
    erp_purchase_orders: pd.DataFrame
    erp_po_lines: pd.DataFrame
    erp_goods_receipts: pd.DataFrame
    erp_supplier_invoices: pd.DataFrame
    erp_price_changes: pd.DataFrame
    wms_staging_log: pd.DataFrame
    wms_shipments: pd.DataFrame
    portal_rental_contracts: pd.DataFrame
    portal_rental_invoices: pd.DataFrame
    sd_tickets: pd.DataFrame
    ret_receipts: pd.DataFrame
    rf_work_orders: pd.DataFrame
    rc_orders: pd.DataFrame
    rc_credit_notes: pd.DataFrame
    ctr_register: pd.DataFrame
    fin_indirect_spend: pd.DataFrame

    @classmethod
    def empty(cls, **frames: pd.DataFrame) -> "BronzeFrames":
        """A ``BronzeFrames`` with empty frames everywhere except the ones given (tests)."""
        kwargs = {f.name: frames.get(f.name, pd.DataFrame()) for f in fields(cls)}
        return cls(**kwargs)


def read_bronze_frames(con: "duckdb.DuckDBPyConnection") -> BronzeFrames:
    """Read every ``bronze.<name>`` table into a ``BronzeFrames``; absent tables are empty."""
    from restwert.db import read_df, table_exists

    frames: dict[str, pd.DataFrame] = {}
    for name in BRONZE_SHORT_NAMES:
        table = f"bronze.{name}"
        frames[name] = read_df(con, f'SELECT * FROM "bronze"."{name}"') if table_exists(con, table) else pd.DataFrame()
    return BronzeFrames(**frames)


# --------------------------------------------------------------------------------------
# coercion helpers (bronze frames arrive from DuckDB, from CSV or hand-built in tests)
# --------------------------------------------------------------------------------------

def _ts(df: pd.DataFrame, col: str) -> pd.Series:
    """Column as ``datetime64[ns]`` floored to the day (NaT when missing or absent).

    The booking rule ``event_date <= as_of`` compares DATES: a work order finished at
    17:00 on the ``as_of`` day is booked, exactly as v0.1 counts its ``end_date``.
    """
    if col not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    s = pd.to_datetime(df[col], errors="coerce")
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_localize(None)
    return s.astype("datetime64[ns]").dt.normalize()


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    """Column as float (NaN when missing or absent)."""
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").astype("float64")


def _txt(df: pd.DataFrame, col: str) -> pd.Series:
    """Column as object strings (None when missing or absent)."""
    if col not in df.columns:
        return pd.Series([None] * len(df), index=df.index, dtype=object)
    s = df[col]
    mask = s.isna()
    out = pd.Series([None] * len(s), index=df.index, dtype=object)
    if (~mask).any():
        out[~mask] = s[~mask].astype(str).to_numpy()
    return out


def _missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _empty_lines() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_LINE_COLUMNS)


def _frame(**cols: Any) -> pd.DataFrame:
    """Assemble a partial lines frame; missing optional columns are filled by ``_finalise``."""
    return pd.DataFrame(cols)


_OPTIONAL_DEFAULTS: dict[str, Any] = {
    "delivery_id": None,
    "allocation_basis": "direct",
    "is_estimate": False,
    "assumption_key": None,
    "assumption_owner": None,
    "counterparty": None,
    "counterparty_role": None,
    "contract_ref": None,
}


def _signed(amount: pd.Series, line_type: str) -> pd.Series:
    sign = 1.0 if LINE_TYPES[line_type] == "revenue" else -1.0
    return (amount.abs().round(2) * sign).round(2)


def _po_header_map(b: BronzeFrames) -> pd.DataFrame:
    """``po_number -> supplier_name, supplier_role, contract_ref`` (empty when no headers)."""
    ph = b.erp_purchase_orders
    if ph is None or len(ph) == 0:
        return pd.DataFrame(columns=["po_number", "supplier_name", "supplier_role", "contract_ref"])
    out = pd.DataFrame({
        "po_number": _txt(ph, "po_number"),
        "supplier_name": _txt(ph, "supplier_name"),
        "supplier_role": _txt(ph, "supplier_role"),
        "contract_ref": _txt(ph, "contract_ref"),
    })
    return out.drop_duplicates("po_number")


# --------------------------------------------------------------------------------------
# per line type builders (each returns a partial lines frame, possibly empty)
# --------------------------------------------------------------------------------------

def _purchase_price_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    """Unit invoice lines per serial; PO line price (flagged) while the invoice is pending."""
    inv = b.erp_supplier_invoices
    gr = b.erp_goods_receipts
    headers = _po_header_map(b)
    parts: list[pd.DataFrame] = []
    invoiced: set[str] = set()
    if inv is not None and len(inv):
        kind = _txt(inv, "line_kind").fillna("").str.lower()
        serial = _txt(inv, "serial")
        inv_date = _ts(inv, "invoice_date")
        keep = (kind == "unit") & serial.notna() & (inv_date <= as_of)
        u = inv[keep]
        if len(u):
            po = _txt(u, "po_number")
            f = _frame(
                serial=serial[keep].to_numpy(),
                line_type="purchase_price",
                amount_eur=_signed(_num(u, "amount_eur"), "purchase_price").to_numpy(),
                event_date=inv_date[keep].to_numpy(),
                source_system="erp",
                source_table="erp_supplier_invoices",
                source_ref=("erp:" + _txt(u, "invoice_number") + "/" + _num(u, "invoice_line").astype("Int64").astype(str)).to_numpy(),
                delivery_id=_txt(u, "delivery_id").to_numpy(),
                po_number=po.to_numpy(),
            )
            f = f.merge(headers, on="po_number", how="left")
            f["counterparty"] = f["supplier_name"]
            f["counterparty_role"] = f["supplier_role"]
            parts.append(f.drop(columns=["po_number", "supplier_name", "supplier_role"]))
            invoiced = set(f["serial"].astype(str))
    if gr is not None and len(gr) and b.erp_po_lines is not None and len(b.erp_po_lines):
        rec_at = _ts(gr, "received_at")
        serial = _txt(gr, "serial")
        pending = gr[(rec_at <= as_of) & serial.notna() & ~serial.isin(invoiced)]
        if len(pending):
            pl = b.erp_po_lines
            pl_key = pd.DataFrame({
                "po_number": _txt(pl, "po_number"),
                "po_line": _num(pl, "po_line").astype("Int64"),
                "unit_price_eur": _num(pl, "unit_price_eur"),
            }).drop_duplicates(["po_number", "po_line"])
            p = pd.DataFrame({
                "serial": _txt(pending, "serial").to_numpy(),
                "po_number": _txt(pending, "po_number").to_numpy(),
                "po_line": _num(pending, "po_line").astype("Int64").to_numpy(),
                "event_date": rec_at[pending.index].to_numpy(),
                "delivery_id": _txt(pending, "delivery_id").to_numpy(),
            })
            p = p.merge(pl_key, on=["po_number", "po_line"], how="inner").merge(headers, on="po_number", how="left")
            if len(p):
                f = _frame(
                    serial=p["serial"].to_numpy(),
                    line_type="purchase_price",
                    amount_eur=_signed(p["unit_price_eur"], "purchase_price").to_numpy(),
                    event_date=p["event_date"].to_numpy(),
                    source_system="erp",
                    source_table="erp_po_lines",
                    source_ref=("erp:" + p["po_number"] + "/" + p["po_line"].astype(str)).to_numpy(),
                    delivery_id=p["delivery_id"].to_numpy(),
                    allocation_basis="direct",
                    is_estimate=True,
                    assumption_key=PENDING_INVOICE_KEY,
                    assumption_owner=PENDING_INVOICE_OWNER,
                    counterparty=p["supplier_name"].to_numpy(),
                    counterparty_role=p["supplier_role"].to_numpy(),
                    contract_ref=p["contract_ref"].to_numpy(),
                )
                parts.append(f)
    return pd.concat(parts, ignore_index=True) if parts else _empty_lines()


_ALLOCATED_KINDS: dict[str, str] = {
    "freight": "freight",
    "duty": "duty",
    "price_protection_credit": "price_protection_credit",
}


def _allocated_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    """Freight, duty and price protection credit invoice lines split per received serial of the PO line."""
    inv = b.erp_supplier_invoices
    gr = b.erp_goods_receipts
    if inv is None or len(inv) == 0 or gr is None or len(gr) == 0:
        return _empty_lines()
    kind = _txt(inv, "line_kind").fillna("").str.lower()
    inv_date = _ts(inv, "invoice_date")
    keep = kind.isin(list(_ALLOCATED_KINDS)) & (inv_date <= as_of)
    u = inv[keep]
    if len(u) == 0:
        return _empty_lines()
    lines = pd.DataFrame({
        "line_type": kind[keep].map(_ALLOCATED_KINDS).to_numpy(),
        "po_number": _txt(u, "po_number").to_numpy(),
        "po_line": _num(u, "po_line").astype("Int64").to_numpy(),
        "amount": _num(u, "amount_eur").abs().to_numpy(),
        "event_date": inv_date[keep].to_numpy(),
        "source_ref": ("erp:" + _txt(u, "invoice_number") + "/" + _num(u, "invoice_line").astype("Int64").astype(str)).to_numpy(),
        "delivery_id": _txt(u, "delivery_id").to_numpy(),
    })
    rec = pd.DataFrame({
        "po_number": _txt(gr, "po_number"),
        "po_line": _num(gr, "po_line").astype("Int64"),
        "serial": _txt(gr, "serial"),
    }).dropna(subset=["serial"]).sort_values("serial", kind="stable").reset_index(drop=True)
    rec["rank"] = rec.groupby(["po_number", "po_line"]).cumcount()
    rec["n"] = rec.groupby(["po_number", "po_line"])["serial"].transform("size")
    m = lines.merge(rec, on=["po_number", "po_line"], how="inner")
    if len(m) == 0:
        return _empty_lines()
    cents = np.rint(m["amount"].to_numpy() * 100.0).astype(np.int64)
    n = m["n"].to_numpy().astype(np.int64)
    base = cents // n
    rem = cents - base * n
    share = base + np.where(m["rank"].to_numpy() == n - 1, rem, 0)
    m["amount_eur"] = share / 100.0
    headers = _po_header_map(b)
    m = m.merge(headers, on="po_number", how="left")
    signs = m["line_type"].map(lambda t: 1.0 if LINE_TYPES[t] == "revenue" else -1.0)
    return _frame(
        serial=m["serial"].to_numpy(),
        line_type=m["line_type"].to_numpy(),
        amount_eur=(m["amount_eur"] * signs).round(2).to_numpy(),
        event_date=m["event_date"].to_numpy(),
        source_system="erp",
        source_table="erp_supplier_invoices",
        source_ref=m["source_ref"].to_numpy(),
        delivery_id=m["delivery_id"].to_numpy(),
        allocation_basis="per_unit_of_po_line",
        counterparty=m["supplier_name"].to_numpy(),
        counterparty_role=m["supplier_role"].to_numpy(),
        contract_ref=m["contract_ref"].to_numpy(),
    )


def _staging_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    st = b.wms_staging_log
    if st is None or len(st) == 0:
        return _empty_lines()
    at = _ts(st, "staged_at")
    keep = (at <= as_of) & _txt(st, "serial").notna()
    u = st[keep]
    if len(u) == 0:
        return _empty_lines()
    return _frame(
        serial=_txt(u, "serial").to_numpy(),
        line_type="staging",
        amount_eur=_signed(_num(u, "staging_cost_eur"), "staging").to_numpy(),
        event_date=at[keep].to_numpy(),
        source_system="wms",
        source_table="wms_staging_log",
        source_ref=("wms:" + _txt(u, "staging_id")).to_numpy(),
        delivery_id=_txt(u, "delivery_id").to_numpy(),
    )


_SHIPMENT_LINES: tuple[tuple[str, str, str], ...] = (
    # (direction, line_type, serial column of the device that carries the cost)
    ("outbound", "outbound_shipping", "serial"),
    ("replacement_out", "replacement_logistics", "related_serial"),
    ("return", "return_logistics", "serial"),
)


def _shipment_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    sh = b.wms_shipments
    if sh is None or len(sh) == 0:
        return _empty_lines()
    at = _ts(sh, "shipped_at")
    direction = _txt(sh, "direction").fillna("").str.lower()
    parts = []
    for d, line_type, serial_col in _SHIPMENT_LINES:
        serial = _txt(sh, serial_col)
        keep = (direction == d) & (at <= as_of) & serial.notna()
        u = sh[keep]
        if len(u) == 0:
            continue
        parts.append(_frame(
            serial=serial[keep].to_numpy(),
            line_type=line_type,
            amount_eur=_signed(_num(u, "cost_eur"), line_type).to_numpy(),
            event_date=at[keep].to_numpy(),
            source_system="wms",
            source_table="wms_shipments",
            source_ref=("wms:" + _txt(u, "shipment_id")).to_numpy(),
            delivery_id=_txt(u, "delivery_id").to_numpy(),
            counterparty=_txt(u, "carrier_ref").to_numpy(),
            counterparty_role="logistics",
            contract_ref=_txt(u, "rental_contract_ref").to_numpy(),
        ))
    return pd.concat(parts, ignore_index=True) if parts else _empty_lines()


def _rental_revenue_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    ri = b.portal_rental_invoices
    if ri is None or len(ri) == 0:
        return _empty_lines()
    at = _ts(ri, "invoice_date")
    keep = (at <= as_of) & _txt(ri, "serial").notna()
    u = ri[keep]
    if len(u) == 0:
        return _empty_lines()
    customer = pd.DataFrame(columns=["contract_id", "customer_id"])
    pc = b.portal_rental_contracts
    if pc is not None and len(pc):
        customer = pd.DataFrame({"contract_id": _txt(pc, "contract_id"), "customer_id": _txt(pc, "customer_id")}).drop_duplicates("contract_id")
    f = pd.DataFrame({
        "serial": _txt(u, "serial").to_numpy(),
        "contract_id": _txt(u, "contract_id").to_numpy(),
        "amount": _num(u, "amount_eur").to_numpy(),
        "event_date": at[keep].to_numpy(),
        "invoice_id": _txt(u, "invoice_id").to_numpy(),
        "delivery_id": _txt(u, "delivery_id").to_numpy(),
    }).merge(customer, on="contract_id", how="left")
    return _frame(
        serial=f["serial"].to_numpy(),
        line_type="rental_revenue",
        amount_eur=_signed(f["amount"], "rental_revenue").to_numpy(),
        event_date=f["event_date"].to_numpy(),
        source_system="portal",
        source_table="portal_rental_invoices",
        source_ref=("portal:" + f["invoice_id"]).to_numpy(),
        delivery_id=f["delivery_id"].to_numpy(),
        counterparty=f["customer_id"].to_numpy(),
        counterparty_role="customer",
        contract_ref=f["contract_id"].to_numpy(),
    )


def _repair_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    tk = b.sd_tickets
    if tk is None or len(tk) == 0:
        return _empty_lines()
    closed = _ts(tk, "closed_at")
    res = _txt(tk, "resolution").fillna("").str.lower()
    keep = (res == "repair") & closed.notna() & (closed <= as_of) & _txt(tk, "serial").notna()
    u = tk[keep]
    if len(u) == 0:
        return _empty_lines()
    return _frame(
        serial=_txt(u, "serial").to_numpy(),
        line_type="repair",
        amount_eur=_signed(_num(u, "repair_cost_eur").fillna(0.0), "repair").to_numpy(),
        event_date=closed[keep].to_numpy(),
        source_system="servicedesk",
        source_table="sd_tickets",
        source_ref=("servicedesk:" + _txt(u, "ticket_id")).to_numpy(),
        delivery_id=_txt(u, "delivery_id").to_numpy(),
        counterparty=_txt(u, "repair_partner_ref").to_numpy(),
        counterparty_role="refurb_repair",
        contract_ref=_txt(u, "contract_id").to_numpy(),
    )


def _wipe_grading_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    rr = b.ret_receipts
    if rr is None or len(rr) == 0:
        return _empty_lines()
    at = _ts(rr, "returned_at")
    keep = (at <= as_of) & _txt(rr, "serial").notna()
    u = rr[keep]
    if len(u) == 0:
        return _empty_lines()
    return _frame(
        serial=_txt(u, "serial").to_numpy(),
        line_type="wipe_grading",
        amount_eur=_signed(_num(u, "wipe_grading_cost_eur").fillna(0.0), "wipe_grading").to_numpy(),
        event_date=at[keep].to_numpy(),
        source_system="returns",
        source_table="ret_receipts",
        source_ref=("returns:" + _txt(u, "receipt_id")).to_numpy(),
        delivery_id=_txt(u, "delivery_id").to_numpy(),
        contract_ref=_txt(u, "contract_id").to_numpy(),
    )


def _refurbishment_lines(b: BronzeFrames, as_of: pd.Timestamp) -> pd.DataFrame:
    wo = b.rf_work_orders
    if wo is None or len(wo) == 0:
        return _empty_lines()
    at = _ts(wo, "finished_at")
    keep = at.notna() & (at <= as_of) & _txt(wo, "serial").notna()
    u = wo[keep]
    if len(u) == 0:
        return _empty_lines()
    return _frame(
        serial=_txt(u, "serial").to_numpy(),
        line_type="refurbishment",
        amount_eur=_signed(_num(u, "cost_eur").fillna(0.0), "refurbishment").to_numpy(),
        event_date=at[keep].to_numpy(),
        source_system="refurb",
        source_table="rf_work_orders",
        source_ref=("refurb:" + _txt(u, "work_order_id")).to_numpy(),
        delivery_id=_txt(u, "delivery_id").to_numpy(),
        counterparty=_txt(u, "partner_ref").to_numpy(),
        counterparty_role="refurb_repair",
    )


def _fee_assumptions(a: "Assumptions") -> dict[str, dict[str, float]]:
    try:
        raw = a.get("channel_fees")
    except KeyError:
        return {}
    out: dict[str, dict[str, float]] = {}
    if isinstance(raw, dict):
        for ch, block in raw.items():
            if isinstance(block, dict):
                out[str(ch)] = {
                    "fee_pct": float(block.get("fee_pct", 0.0) or 0.0),
                    "fee_fixed_eur": float(block.get("fee_fixed_eur", 0.0) or 0.0),
                    "days_to_cash": float(block.get("days_to_cash", 0.0) or 0.0),
                }
    return out


def _fee_owner(a: "Assumptions") -> str:
    try:
        return str(a.owner("channel_fees"))
    except KeyError:
        return "Head of Recommerce (name)"


def _resale_lines(b: BronzeFrames, a: "Assumptions", as_of: pd.Timestamp) -> pd.DataFrame:
    """``resale_gross`` per order sold by ``as_of`` and the ``channel_fee`` of the credit note or the assumption."""
    ro = b.rc_orders
    if ro is None or len(ro) == 0:
        return _empty_lines()
    sold = _ts(ro, "sold_at")
    keep = sold.notna() & (sold <= as_of) & _txt(ro, "serial").notna()
    u = ro[keep]
    if len(u) == 0:
        return _empty_lines()
    orders = pd.DataFrame({
        "serial": _txt(u, "serial").to_numpy(),
        "order_id": _txt(u, "order_id").to_numpy(),
        "channel": _txt(u, "channel").to_numpy(),
        "gross": _num(u, "gross_price_eur").to_numpy(),
        "event_date": sold[keep].to_numpy(),
        "delivery_id": _txt(u, "delivery_id").to_numpy(),
    })
    gross = _frame(
        serial=orders["serial"].to_numpy(),
        line_type="resale_gross",
        amount_eur=_signed(orders["gross"], "resale_gross").to_numpy(),
        event_date=orders["event_date"].to_numpy(),
        source_system="recommerce",
        source_table="rc_orders",
        source_ref=("recommerce:" + orders["order_id"]).to_numpy(),
        delivery_id=orders["delivery_id"].to_numpy(),
        counterparty=orders["channel"].to_numpy(),
        counterparty_role="marketplace",
    )
    cn = b.rc_credit_notes
    notes = pd.DataFrame(columns=["order_id", "credit_note_id", "fee_total", "cn_delivery_id", "credited_at"])
    if cn is not None and len(cn):
        credited = _ts(cn, "credited_at")
        # a credit note dated after as_of does not exist yet: the fee falls back to the assumption
        booked = cn[credited.notna() & (credited <= as_of)]
        notes = pd.DataFrame({
            "order_id": _txt(booked, "order_id"),
            "credit_note_id": _txt(booked, "credit_note_id"),
            "fee_total": (_num(booked, "fee_pct_eur").fillna(0.0) + _num(booked, "fee_fixed_eur").fillna(0.0)).round(2),
            "cn_delivery_id": _txt(booked, "delivery_id"),
            "credited_at": credited[booked.index],
        }).sort_values("credit_note_id", kind="stable").drop_duplicates("order_id")
    f = orders.merge(notes, on="order_id", how="left")
    fees = _fee_assumptions(a)
    owner = _fee_owner(a)
    pct = f["channel"].map(lambda c: fees.get(str(c), {}).get("fee_pct", 0.0)).astype(float)
    fixed = f["channel"].map(lambda c: fees.get(str(c), {}).get("fee_fixed_eur", 0.0)).astype(float)
    has_note = f["credit_note_id"].notna()
    fee_amount = np.where(has_note, f["fee_total"].fillna(0.0), (f["gross"] * pct + fixed).round(2))
    # the credit note's own date is the event date of a booked fee; the assumption fee is dated at the sale
    fee_event = pd.to_datetime(f["credited_at"], errors="coerce").where(has_note, pd.to_datetime(f["event_date"]))
    fee = _frame(
        serial=f["serial"].to_numpy(),
        line_type="channel_fee",
        amount_eur=_signed(pd.Series(fee_amount), "channel_fee").to_numpy(),
        event_date=fee_event.to_numpy(),
        source_system=np.where(has_note, "recommerce", "assumptions"),
        source_table=np.where(has_note, "rc_credit_notes", "assumptions.channel_fees"),
        source_ref=np.where(has_note, "recommerce:" + f["credit_note_id"].fillna(""), "assumptions:channel_fees[" + f["channel"].fillna("") + "]"),
        delivery_id=np.where(has_note, f["cn_delivery_id"], None),
        is_estimate=~has_note.to_numpy(),
        assumption_key=np.where(has_note, None, CHANNEL_FEE_KEY),
        assumption_owner=np.where(has_note, None, owner),
        counterparty=f["channel"].to_numpy(),
        counterparty_role="marketplace",
    )
    return pd.concat([gross, fee], ignore_index=True)


#: The three stock phases that carry holding cost, each ``(phase, start step, end step,
#: status still in the phase)`` of ``silver.serial_timeline``. A phase runs from its start
#: step to ``min(end step, as_of)``. An end step that is still missing at ``as_of`` keeps
#: the device in the phase ONLY when its lifecycle status says it is there (a not yet
#: shipped device is ``not_deployed``, a device in refurbishment is ``wip``, a device on the
#: shelf is ``in_stock``); a missing end step on any other status is a broken chain (the
#: Data page shows it) and the phase is not costed. A scrapped device's return phase is not
#: costed either: the timeline carries no scrap date and the ledger never guesses one.
HOLDING_PHASES: tuple[tuple[str, str, str, str], ...] = (
    ("inbound", "received_at", "shipped_at", "not_deployed"),
    ("return", "returned_at", "sellable_at", "wip"),
    ("sale", "sellable_at", "sold_at", "in_stock"),
)


def holding_cost_lines(timeline: pd.DataFrame, rate_per_day: float, owner: str, as_of: date) -> pd.DataFrame:
    """One estimated ``holding_cost`` line per serial and stock phase with ``days > 0``.

    Phases (``HOLDING_PHASES``): inbound ``received_at -> shipped_at``, return
    ``returned_at -> sellable_at``, sale ``sellable_at -> sold_at``; every end is capped at
    ``as_of`` and a missing end means the device is still in the phase. The only rate in the
    ledger: ``days x rate_per_day``, ``is_estimate = true``, ``allocation_basis =
    days_x_rate``, ``assumption_key = holding_cost_per_day_eur``; ``source_ref`` names the
    phase (``assumptions:holding_cost_per_day_eur:<serial>:<phase>``) so the three lines of
    one serial stay distinct.
    """
    if timeline is None or len(timeline) == 0 or "serial" not in timeline.columns:
        return _empty_lines()
    as_of_ts = pd.Timestamp(as_of)
    status = _txt(timeline, "lifecycle_status").fillna("").str.lower()
    parts: list[pd.DataFrame] = []
    for phase, start_col, end_col, open_status in HOLDING_PHASES:
        if start_col not in timeline.columns:
            continue
        start = _ts(timeline, start_col)
        end = _ts(timeline, end_col)
        stop = end.where(end.notna() & (end < as_of_ts), as_of_ts)
        # a phase without its end step is still open only while the status says the device is in it
        keep = start.notna() & (start <= as_of_ts) & (end.notna() | (status == open_status))
        days = (stop - start).dt.days
        keep &= days > 0
        u = timeline[keep]
        if len(u) == 0:
            continue
        serial = _txt(u, "serial")
        amount = (days[keep].astype(float) * float(rate_per_day)).round(2)
        parts.append(_frame(
            serial=serial.to_numpy(),
            line_type="holding_cost",
            amount_eur=_signed(amount, "holding_cost").to_numpy(),
            event_date=stop[keep].to_numpy(),
            source_system="assumptions",
            source_table="silver.serial_timeline",
            source_ref=("assumptions:" + HOLDING_COST_KEY + ":" + serial + ":" + phase).to_numpy(),
            delivery_id=None,
            allocation_basis="days_x_rate",
            is_estimate=True,
            assumption_key=HOLDING_COST_KEY,
            assumption_owner=owner,
        ))
    return pd.concat(parts, ignore_index=True) if parts else _empty_lines()


def mdm_enrolled_serials(b: BronzeFrames) -> set[str]:
    """Serials whose staging log marks ``mdm_enrolled`` true (any staging row of the serial)."""
    st = b.wms_staging_log
    if st is None or len(st) == 0 or "mdm_enrolled" not in st.columns:
        return set()
    flag = st["mdm_enrolled"]
    if flag.dtype != bool:
        flag = flag.astype(str).str.strip().str.lower().isin(("true", "1", "yes", "y", "t"))
    return set(_txt(st[flag.to_numpy()], "serial").dropna().astype(str))


def service_allocation_lines(
    b: BronzeFrames,
    support_rate: float,
    mdm_rate: float,
    owner: str,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Team cost allocated per billed rental month: ``support`` on every rental invoice of the
    serial, ``mdm_operations`` on the invoices of serials the staging log marks ``mdm_enrolled``.

    Both are estimates by construction (``is_estimate = true``, ``allocation_basis =
    months_x_rate``, ``assumption_key`` the rate's key, ``assumption_owner`` its owner) and
    exist only where a rental invoice exists: no invoice, no allocation. Dated on the invoice,
    ``source_ref = assumptions:<key>:<serial>:<invoice_id>`` so the lines of one serial stay
    distinct and each one names the invoice month it rides on. A rate of 0 books nothing.
    """
    ri = b.portal_rental_invoices
    if ri is None or len(ri) == 0 or (support_rate <= 0 and mdm_rate <= 0):
        return _empty_lines()
    at = _ts(ri, "invoice_date")
    keep = (at <= as_of) & _txt(ri, "serial").notna()
    u = ri[keep]
    if len(u) == 0:
        return _empty_lines()
    serial = _txt(u, "serial")
    invoice = _txt(u, "invoice_id").fillna("")
    contract = _txt(u, "contract_id")
    when = at[keep]
    parts: list[pd.DataFrame] = []

    def block(mask: pd.Series, line_type: str, key: str, rate: float) -> None:
        if rate <= 0 or not bool(mask.any()):
            return
        n = int(mask.sum())
        parts.append(_frame(
            serial=serial[mask].to_numpy(),
            line_type=line_type,
            amount_eur=_signed(pd.Series(np.full(n, float(rate))), line_type).to_numpy(),
            event_date=when[mask].to_numpy(),
            source_system="assumptions",
            source_table="bronze.portal_rental_invoices",
            source_ref=("assumptions:" + key + ":" + serial[mask] + ":" + invoice[mask]).to_numpy(),
            delivery_id=None,
            allocation_basis="months_x_rate",
            is_estimate=True,
            assumption_key=key,
            assumption_owner=owner,
            contract_ref=contract[mask].to_numpy(),
        ))

    all_rows = pd.Series(True, index=u.index)
    block(all_rows, "support", SUPPORT_COST_KEY, support_rate)
    enrolled = mdm_enrolled_serials(b)
    block(serial.isin(enrolled), "mdm_operations", MDM_COST_KEY, mdm_rate)
    return pd.concat(parts, ignore_index=True) if parts else _empty_lines()


# --------------------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------------------

def _finalise(parts: list[pd.DataFrame], as_of: date, is_synthetic: bool) -> pd.DataFrame:
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return _empty_lines()
    df = pd.concat(parts, ignore_index=True)
    for col, default in _OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].where(pd.notna(df[col]), default) if default is not None else df[col].where(pd.notna(df[col]), None)
    df["serial"] = df["serial"].astype(str)
    df["line_type"] = df["line_type"].astype(str)
    df["line_class"] = df["line_type"].map(LINE_TYPES)
    df["amount_eur"] = pd.to_numeric(df["amount_eur"], errors="coerce").fillna(0.0).round(2)
    ev = pd.to_datetime(df["event_date"], errors="coerce")
    df = df[ev.notna()].copy()
    ev = ev[ev.notna()].dt.normalize()
    df["event_date"] = ev
    df["period_month"] = ev.dt.to_period("M").dt.to_timestamp()
    df["is_estimate"] = df["is_estimate"].astype(bool)
    df["source_system"] = df["source_system"].astype(str)
    df["source_table"] = df["source_table"].astype(str)
    df["source_ref"] = df["source_ref"].astype(str)
    df["allocation_basis"] = df["allocation_basis"].astype(str)
    df["as_of"] = pd.Timestamp(as_of)
    df["is_synthetic"] = bool(is_synthetic)
    df["line_id"] = [
        _line_id(s, t, sy, r, d)
        for s, t, sy, r, d in zip(df["serial"], df["line_type"], df["source_system"], df["source_ref"], ev.dt.date)
    ]
    df = df.drop_duplicates(["serial", "line_type", "source_system", "source_ref"], keep="first")
    order = {t: i for i, t in enumerate(LEDGER_ORDER)}
    df["_o"] = df["line_type"].map(order)
    df = df.sort_values(["serial", "event_date", "_o", "source_ref"], kind="stable").drop(columns="_o")
    df = df[LEDGER_LINE_COLUMNS].reset_index(drop=True)
    for c in ("delivery_id", "assumption_key", "assumption_owner", "counterparty", "counterparty_role", "contract_ref"):
        df[c] = df[c].astype(object).where(pd.notna(df[c]), None)
    df["amount_eur"] = df["amount_eur"].astype(float)
    return df


def build_ledger_lines(
    b: BronzeFrames,
    timeline: pd.DataFrame | None,
    a: "Assumptions",
    as_of: date,
    is_synthetic: bool,
) -> pd.DataFrame:
    """Pure: bronze frames and the serial timeline in, ``silver.ledger_lines`` rows out.

    Fourteen line types come from bronze transactions; ``holding_cost`` comes from the
    timeline and the ``holding_cost_per_day_eur`` assumption; ``support`` and
    ``mdm_operations`` ride on the rental invoices with their per-device-month rates
    (``service_allocation_lines``). Lines dated after ``as_of`` are not booked. The frame
    carries every DDL column, ``amount_eur`` signed.
    """
    as_of_ts = pd.Timestamp(as_of)
    try:
        rate = float(a.get("holding_cost_per_day_eur"))
        owner = str(a.owner("holding_cost_per_day_eur"))
    except KeyError:
        rate, owner = 0.0, "CFO (name)"
    try:
        support_rate = float(a.get(SUPPORT_COST_KEY))
        service_owner = str(a.owner(SUPPORT_COST_KEY))
    except KeyError:
        support_rate, service_owner = 0.0, "Head of Service Operations (name)"
    try:
        mdm_rate = float(a.get(MDM_COST_KEY))
    except KeyError:
        mdm_rate = 0.0
    parts = [
        _purchase_price_lines(b, as_of_ts),
        _allocated_lines(b, as_of_ts),
        _staging_lines(b, as_of_ts),
        _shipment_lines(b, as_of_ts),
        _rental_revenue_lines(b, as_of_ts),
        _repair_lines(b, as_of_ts),
        _wipe_grading_lines(b, as_of_ts),
        _refurbishment_lines(b, as_of_ts),
        _resale_lines(b, a, as_of_ts),
        holding_cost_lines(timeline, rate, owner, as_of) if rate > 0 else _empty_lines(),
        service_allocation_lines(b, support_rate, mdm_rate, service_owner, as_of_ts),
    ]
    return _finalise(parts, as_of, is_synthetic)


def lines_of(lines: pd.DataFrame, serial: str) -> pd.DataFrame:
    """The lines of one serial sorted by ``event_date`` then ``LEDGER_ORDER``."""
    if lines is None or len(lines) == 0:
        return _empty_lines()
    sub = lines[lines["serial"].astype(str) == str(serial)].copy()
    if len(sub) == 0:
        return _empty_lines()
    order = {t: i for i, t in enumerate(LEDGER_ORDER)}
    sub["_o"] = sub["line_type"].map(order)
    sub["_d"] = pd.to_datetime(sub["event_date"], errors="coerce")  # tolerant of frames read back from DuckDB
    return sub.sort_values(["_d", "_o", "source_ref"], kind="stable").drop(columns=["_o", "_d"]).reset_index(drop=True)


__all__ = [
    "LINE_TYPES",
    "LEDGER_ORDER",
    "ESTIMATE_LINE_TYPES",
    "V01_BRIDGE_LINE_TYPES",
    "LANDED_LINE_TYPES",
    "COST_LINE_TYPES",
    "REVENUE_LINE_TYPES",
    "LEDGER_LINE_COLUMNS",
    "PENDING_INVOICE_KEY",
    "CHANNEL_FEE_KEY",
    "HOLDING_COST_KEY",
    "SUPPORT_COST_KEY",
    "MDM_COST_KEY",
    "BronzeFrames",
    "read_bronze_frames",
    "build_ledger_lines",
    "holding_cost_lines",
    "service_allocation_lines",
    "mdm_enrolled_serials",
    "lines_of",
]
