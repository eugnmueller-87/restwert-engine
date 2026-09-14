"""Purchase orders, receipts, supplier invoices and price changes (SPEC_v0.2 section 5.4).

``build_purchase(cfg, fleet, register, rng)`` turns the fleet into what an ERP would
export:

* one purchase order per (order month, supplier, catalogue family):
  ``PO-<yyyy>-<seq:06d>``, ``contract_ref`` = the register contract of the counterparty
  when in force at the order date (90 % of manufacturer POs, 100 % of reseller POs,
  the rest empty = not under contract), promised = order + 21 days, DAP, payment
  terms from the contract (default 30);
* one PO line per (PO, slug, storage_gb): every serial of the line shares the unit
  price and colour of its first serial; ``price_protection_days`` from the contract;
* one goods receipt per serial at ``order_date + U(14, 35)`` days 10:00; 10 % of the
  lines with at least two serials are short deliveries (their last serial lands 20
  days later on a second receipt); one ``gr_number`` per (line, delivery day);
* supplier invoices ``INV-<yyyy>-<seq:06d>``: one invoice per receipt with a ``unit``
  line per serial (``invoice_date = received + U(2, 10)``), plus on the line's first
  receipt one ``freight`` line (``qty_received x U(freight_per_unit_eur)``) and, for
  "IT reseller B (role-only)", one ``duty`` line (``duty_pct_reseller_b x qty x unit price``);
* price changes ``PC-<seq:05d>`` for ``price_drop_share`` of (supplier, slug, storage):
  one drop of ``price_drop_pct`` at the slug's successor launch date when known and
  after the first order, else a uniform 1 to 12 months after the first order;
* price protection credits: for every PO line whose drop falls inside
  ``(delivered, delivered + price_protection_days]`` (delivered = the line's last
  receipt), with probability ``price_protection_claim_share`` an invoice with one
  ``price_protection_credit`` line (``qty = qty_received``, ``amount = drop x qty``,
  ``invoice_date = valid_from + U(10, 40)``, only when ``<= as_of``); the rest stay
  unclaimed for lever L02 to measure.

Freight and duty are allocated to the serials of a PO line with
``lake.common.allocate_cents`` in serial order (the only allocation, decision D7), so
the fleet's ``landed_cost_eur`` equals what the conform step and the ledger compute.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from restwert.dates import add_months
from restwert.lake.common import allocate_cents
from restwert.lakegen.config import LakeConfig
from restwert.lakegen.contracts import contract_in_force, register_index

PO_COLUMNS: tuple[str, ...] = (
    "po_number", "supplier_id", "supplier_name", "supplier_role", "contract_ref", "order_date", "promised_date",
    "currency", "incoterm", "payment_terms_days",
)
PO_LINE_COLUMNS: tuple[str, ...] = (
    "po_number", "po_line", "slug", "storage_gb", "colour", "qty_ordered", "unit_price_eur", "price_protection_days",
    "order_date",
)
GOODS_RECEIPT_COLUMNS: tuple[str, ...] = ("gr_number", "po_number", "po_line", "serial", "received_at", "warehouse")
INVOICE_COLUMNS: tuple[str, ...] = (
    "invoice_number", "invoice_line", "supplier_id", "po_number", "po_line", "serial", "invoice_date", "line_kind",
    "qty", "amount_eur", "currency",
)
PRICE_CHANGE_COLUMNS: tuple[str, ...] = (
    "change_id", "supplier_id", "slug", "storage_gb", "valid_from", "old_unit_price_eur", "new_unit_price_eur",
)

PROMISED_DAYS: int = 21
RECEIPT_DAYS: tuple[int, int] = (14, 35)
SHORT_DELIVERY_EXTRA_DAYS: int = 20
INVOICE_DAYS: tuple[int, int] = (2, 10)
CLAIM_DAYS: tuple[int, int] = (10, 40)
MANUFACTURER_CONTRACT_SHARE: float = 0.90
RECEIPT_TIME: time = time(10, 0)
WAREHOUSE: str = "WH-01"
CURRENCY: str = "EUR"
INCOTERM: str = "DAP"
RESELLER_B: str = "IT reseller B (role-only)"


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def build_purchase(
    cfg: LakeConfig, fleet: pd.DataFrame, register: pd.DataFrame, rng: np.random.Generator
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Return ``({feed key: frame}, fleet)`` with the fleet extended by PO, receipt and landed cost columns."""
    fleet = fleet.copy().reset_index(drop=True)
    po_headers: list[dict] = []
    po_lines: list[dict] = []
    receipts: list[dict] = []          # one per serial (with a delivery group key)
    invoices: list[dict] = []
    price_changes: list[dict] = []

    # ---------------------------------------------------------------- purchase orders and lines
    keys = pd.DataFrame(
        {
            "month": fleet["order_date"].map(_month_key),
            "supplier": fleet["supplier_name"],
            "family": fleet["catalogue_family"],
        }
    )
    groups = keys.groupby(["month", "supplier", "family"], sort=True).indices
    po_seq: dict[int, int] = {}
    po_of_row = np.empty(len(fleet), dtype=object)
    line_of_row = np.zeros(len(fleet), dtype=int)
    unit_price_of_row = fleet["unit_price_eur"].astype(float).to_numpy().copy()
    colour_of_row = fleet["colour"].astype(str).to_numpy().copy()
    line_index: dict[tuple[str, int], dict] = {}

    order_dates = list(fleet["order_date"])
    slugs_all = fleet["slug"].astype(str).to_numpy()
    storage_all = fleet["storage_gb"].astype(int).to_numpy()
    roles = fleet["supplier_role"].astype(str).to_numpy()
    supplier_ids = fleet["supplier_id"].astype(str).to_numpy()
    serials_all = fleet["serial"].astype(str).to_numpy()
    register_rows = register_index(register)
    for (month, supplier, family), idx in groups.items():
        idx = np.sort(np.asarray(idx))
        first_row = int(idx[0])
        order_date: date = min(order_dates[i] for i in idx)
        year = order_date.year
        po_seq[year] = po_seq.get(year, 0) + 1
        po_number = f"PO-{year}-{po_seq[year]:06d}"
        role = str(roles[first_row])
        contract = contract_in_force(register_rows, str(supplier), role, order_date)
        contract_ref = None
        if contract is not None and (role == "reseller" or rng.random() < MANUFACTURER_CONTRACT_SHARE):
            contract_ref = str(contract["contract_id"])
        payment_terms = int(contract["payment_terms_days"]) if contract_ref is not None else 30
        ppd = None
        if contract_ref is not None and bool(contract["price_protection"]) and contract["price_protection_days"] is not None:
            ppd = int(contract["price_protection_days"])
        supplier_id = str(supplier_ids[first_row])
        po_headers.append(
            {
                "po_number": po_number,
                "supplier_id": supplier_id,
                "supplier_name": str(supplier),
                "supplier_role": role,
                "contract_ref": contract_ref,
                "order_date": order_date,
                "promised_date": order_date + timedelta(days=PROMISED_DAYS),
                "currency": CURRENCY,
                "incoterm": INCOTERM,
                "payment_terms_days": payment_terms,
            }
        )
        line_map: dict[tuple[str, int], list[int]] = {}
        for i in idx:
            line_map.setdefault((str(slugs_all[i]), int(storage_all[i])), []).append(int(i))
        for n_line, ((slug, storage), lrows) in enumerate(sorted(line_map.items()), start=1):
            rows = np.asarray(lrows, dtype=int)
            line_first = int(rows[0])
            unit_price = float(unit_price_of_row[line_first])
            colour = str(colour_of_row[line_first])
            unit_price_of_row[rows] = unit_price
            colour_of_row[rows] = colour
            po_of_row[rows] = po_number
            line_of_row[rows] = n_line
            po_lines.append(
                {
                    "po_number": po_number,
                    "po_line": n_line,
                    "slug": str(slug),
                    "storage_gb": int(storage),
                    "colour": colour,
                    "qty_ordered": int(len(rows)),
                    "unit_price_eur": unit_price,
                    "price_protection_days": ppd,
                    "order_date": order_date,
                }
            )
            line_index[(po_number, n_line)] = {
                "supplier_id": supplier_id,
                "supplier_name": str(supplier),
                "slug": str(slug),
                "storage_gb": int(storage),
                "unit_price": unit_price,
                "order_date": order_date,
                "ppd": ppd,
                "rows": rows,
            }
            # ------------------------------------------------------ goods receipts of the line
            offset = int(rng.integers(RECEIPT_DAYS[0], RECEIPT_DAYS[1] + 1))
            main_day = order_date + timedelta(days=offset)
            short = len(rows) >= 2 and rng.random() < cfg.po_short_delivery_share
            for k, r in enumerate(rows):
                late = short and k == len(rows) - 1
                day = main_day + timedelta(days=SHORT_DELIVERY_EXTRA_DAYS) if late else main_day
                receipts.append(
                    {
                        "po_number": po_number,
                        "po_line": n_line,
                        "serial": str(serials_all[r]),
                        "received_at": datetime.combine(day, RECEIPT_TIME),
                        "warehouse": WAREHOUSE,
                        "_row": int(r),
                        "_delivery": 1 if late else 0,
                    }
                )

    fleet["po_number"] = po_of_row
    fleet["po_line"] = line_of_row
    fleet["unit_price_eur"] = np.round(unit_price_of_row, 2)
    fleet["colour"] = colour_of_row

    # ---------------------------------------------------------------- GR numbers per (line, delivery)
    gr = pd.DataFrame(receipts)
    gr = gr.sort_values(["received_at", "po_number", "po_line", "serial"], kind="stable").reset_index(drop=True)
    gr_seq: dict[int, int] = {}
    gr_number_of_group: dict[tuple[str, int, int], str] = {}
    gr_numbers: list[str] = []
    for po_number, po_line, delivery, received_at in zip(gr["po_number"], gr["po_line"], gr["_delivery"], gr["received_at"]):
        key = (str(po_number), int(po_line), int(delivery))
        if key not in gr_number_of_group:
            year = received_at.year
            gr_seq[year] = gr_seq.get(year, 0) + 1
            gr_number_of_group[key] = f"GR-{year}-{gr_seq[year]:06d}"
        gr_numbers.append(gr_number_of_group[key])
    gr["gr_number"] = gr_numbers
    received_of_row = {int(r): ts for r, ts in zip(gr["_row"], gr["received_at"])}
    gr_of_row = {int(r): g for r, g in zip(gr["_row"], gr["gr_number"])}
    fleet["gr_number"] = [gr_of_row[i] for i in range(len(fleet))]
    fleet["received_at"] = [received_of_row[i] for i in range(len(fleet))]

    # ---------------------------------------------------------------- invoices per receipt group
    freight_lo, freight_hi = (float(x) for x in cfg.freight_per_unit_eur)
    inv_groups: list[dict] = []
    for key, grp in gr.groupby(["po_number", "po_line", "_delivery"], sort=True):
        po_number, po_line, delivery = str(key[0]), int(key[1]), int(key[2])
        li = line_index[(po_number, po_line)]
        received_day = grp["received_at"].iloc[0].date()
        inv_date = received_day + timedelta(days=int(rng.integers(INVOICE_DAYS[0], INVOICE_DAYS[1] + 1)))
        lines: list[dict] = []
        for serial in grp["serial"]:
            lines.append({"serial": str(serial), "line_kind": "unit", "qty": 1, "amount_eur": li["unit_price"]})
        if delivery == 0:
            qty_received = int(len(li["rows"]))
            freight = round(qty_received * float(rng.uniform(freight_lo, freight_hi)), 2)
            lines.append({"serial": None, "line_kind": "freight", "qty": qty_received, "amount_eur": freight})
            if li["supplier_name"] == RESELLER_B:
                duty = round(float(cfg.duty_pct_reseller_b) * qty_received * li["unit_price"], 2)
                lines.append({"serial": None, "line_kind": "duty", "qty": qty_received, "amount_eur": duty})
        inv_groups.append(
            {"invoice_date": inv_date, "po_number": po_number, "po_line": po_line, "supplier_id": li["supplier_id"], "lines": lines}
        )

    # ---------------------------------------------------------------- price changes
    combos = sorted({(li["supplier_id"], li["slug"], li["storage_gb"]) for li in line_index.values()})
    first_order: dict[tuple[str, str, int], date] = {}
    first_price: dict[tuple[str, str, int], float] = {}
    successor_of: dict[str, date | None] = {}
    for (po_number, po_line), li in sorted(line_index.items()):
        combo = (li["supplier_id"], li["slug"], li["storage_gb"])
        if combo not in first_order or li["order_date"] < first_order[combo]:
            first_order[combo] = li["order_date"]
            first_price[combo] = li["unit_price"]
    for slug, succ in zip(fleet["slug"], fleet["successor_launch_date"]):
        successor_of[str(slug)] = None if succ is None or pd.isna(succ) else succ
    drops: dict[tuple[str, str, int], tuple[date, float, float]] = {}
    for combo in combos:
        if rng.random() >= float(cfg.price_drop_share):
            continue
        succ = successor_of.get(combo[1])
        fo = first_order[combo]
        if succ is not None and succ > fo:
            valid_from = succ
        else:
            valid_from = add_months(fo, int(rng.integers(1, 13)))
        old = float(first_price[combo])
        new = round(old * (1.0 - float(cfg.price_drop_pct)), 2)
        drops[combo] = (valid_from, old, new)
    for combo, (valid_from, old, new) in sorted(drops.items(), key=lambda kv: (kv[1][0], kv[0])):
        if valid_from > cfg.as_of:
            continue
        price_changes.append(
            {
                "change_id": f"PC-{len(price_changes) + 1:05d}",
                "supplier_id": combo[0],
                "slug": combo[1],
                "storage_gb": combo[2],
                "valid_from": valid_from,
                "old_unit_price_eur": old,
                "new_unit_price_eur": new,
            }
        )

    # ---------------------------------------------------------------- price protection credits
    last_receipt_of_line = gr.groupby(["po_number", "po_line"])["received_at"].max()
    for (po_number, po_line), li in sorted(line_index.items()):
        if li["ppd"] is None:
            continue
        combo = (li["supplier_id"], li["slug"], li["storage_gb"])
        if combo not in drops:
            continue
        valid_from, old, new = drops[combo]
        delivered = last_receipt_of_line.loc[(po_number, po_line)].date()
        if not (delivered < valid_from <= delivered + timedelta(days=int(li["ppd"]))):
            continue
        if rng.random() >= float(cfg.price_protection_claim_share):
            continue
        inv_date = valid_from + timedelta(days=int(rng.integers(CLAIM_DAYS[0], CLAIM_DAYS[1] + 1)))
        if inv_date > cfg.as_of:
            continue
        qty_received = int(len(li["rows"]))
        inv_groups.append(
            {
                "invoice_date": inv_date,
                "po_number": po_number,
                "po_line": po_line,
                "supplier_id": li["supplier_id"],
                "lines": [
                    {
                        "serial": None,
                        "line_kind": "price_protection_credit",
                        "qty": qty_received,
                        "amount_eur": round((old - new) * qty_received, 2),
                    }
                ],
            }
        )

    inv_groups.sort(key=lambda g: (g["invoice_date"], g["po_number"], g["po_line"], g["lines"][0]["line_kind"]))
    inv_seq: dict[int, int] = {}
    for g in inv_groups:
        year = g["invoice_date"].year
        inv_seq[year] = inv_seq.get(year, 0) + 1
        number = f"INV-{year}-{inv_seq[year]:06d}"
        for n, line in enumerate(g["lines"], start=1):
            invoices.append(
                {
                    "invoice_number": number,
                    "invoice_line": n,
                    "supplier_id": g["supplier_id"],
                    "po_number": g["po_number"],
                    "po_line": g["po_line"],
                    "serial": line["serial"],
                    "invoice_date": g["invoice_date"],
                    "line_kind": line["line_kind"],
                    "qty": int(line["qty"]),
                    "amount_eur": float(line["amount_eur"]),
                    "currency": CURRENCY,
                }
            )

    # ---------------------------------------------------------------- landed cost per serial (cent-exact)
    inv_df = pd.DataFrame(invoices, columns=list(INVOICE_COLUMNS))
    freight_of_line = inv_df[inv_df["line_kind"] == "freight"].groupby(["po_number", "po_line"])["amount_eur"].sum()
    duty_of_line = inv_df[inv_df["line_kind"] == "duty"].groupby(["po_number", "po_line"])["amount_eur"].sum()
    freight_share = np.zeros(len(fleet))
    duty_share = np.zeros(len(fleet))
    serials = fleet["serial"].astype(str).to_numpy()
    for (po_number, po_line), li in line_index.items():
        rows = np.asarray(li["rows"])
        order = rows[np.argsort(serials[rows], kind="stable")]
        n = int(len(order))
        f_total = float(freight_of_line.get((po_number, po_line), 0.0))
        d_total = float(duty_of_line.get((po_number, po_line), 0.0))
        freight_share[order] = allocate_cents(f_total, n) if f_total else [0.0] * n
        duty_share[order] = allocate_cents(d_total, n) if d_total else [0.0] * n
    fleet["freight_share_eur"] = np.round(freight_share, 2)
    fleet["duty_share_eur"] = np.round(duty_share, 2)
    fleet["landed_cost_eur"] = np.round(fleet["unit_price_eur"].astype(float) + freight_share + duty_share, 2)

    frames = {
        "erp/purchase_orders": pd.DataFrame(po_headers, columns=list(PO_COLUMNS)),
        "erp/po_lines": pd.DataFrame(po_lines, columns=list(PO_LINE_COLUMNS)),
        "erp/goods_receipts": gr[list(GOODS_RECEIPT_COLUMNS)].reset_index(drop=True),
        "erp/supplier_invoices": inv_df,
        "erp/price_changes": pd.DataFrame(price_changes, columns=list(PRICE_CHANGE_COLUMNS)),
    }
    return frames, fleet


__all__ = [
    "build_purchase",
    "PO_COLUMNS",
    "PO_LINE_COLUMNS",
    "GOODS_RECEIPT_COLUMNS",
    "INVOICE_COLUMNS",
    "PRICE_CHANGE_COLUMNS",
    "RESELLER_B",
]
