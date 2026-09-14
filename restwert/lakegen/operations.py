"""Rentals, staging, shipments, tickets, returns and work orders (SPEC_v0.2 section 5.4).

``build_operations(cfg, world, rng)`` runs the v0.1 builders unchanged on the
v0.1-shaped devices frame (``catalogue.to_v01_devices_frame``):
``build_rental_contracts`` (start = purchase_date + U(3, 30) days), ``build_events``
(damages, repairs, replacements, returns; spares deployed as replacements) and
``build_refurbishment``; then renders what the source systems would export:

* ``portal/rental_contracts``: the contracts, ``monthly_rate_eur = monthly_rate``;
* ``portal/rental_invoices``: per contract one invoice per billed month
  ``k = 1 .. months_between(start, min(coalesce(actual_end, end), as_of))`` dated
  ``lake.common.billing_date(start, k)``, so the count of invoices with
  ``invoice_date <= as_of`` equals v0.1 ``months_billed`` exactly (decision D8);
* ``wms/staging_log``: one staging per contract start (initial and replacement),
  ``staged_at = max(received_at + 1 day, start - U(1, 3) days)``; spares never
  deployed are not staged;
* ``wms/shipments``: ``outbound`` per initial contract (shipped start - 1 day,
  delivered at start), ``replacement_out`` per replacement event (serial = the spare,
  related_serial = the damaged device, shipped on the event date or the day the spare
  was staged when that is later, delivered the same day, cost = the event cost),
  ``return`` per return event (shipped return_date - U(1, 3),
  delivered on the return date, cost = the event cost); carrier role-only;
* ``servicedesk/tickets``: one ticket per damage event with its resolution
  (repair / replace / open), quote, repair cost, replacement serial;
* ``returns/receipts``: one receipt per return event with declared and inspected
  grade, ``inspected_at = returned_at + U(1, 3)`` (never after ``as_of``), wipe
  certificate and ``wiped_at = returned_at + U(0, 2)`` when the event carries one;
* ``refurb/work_orders``: one per finished v0.1 refurbishment (``finished_at <= as_of``;
  a work order still open at ``as_of`` is not in the partner's export yet, the device
  is wip).

Every timestamp gets a fixed clock time per event kind so the files are reproducible
and readable; no timestamp is ever after ``as_of``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from restwert.dates import add_months, month_floor, months_between
from restwert.generate.events import build_events
from restwert.generate.recommerce import build_refurbishment
from restwert.generate.rentals import build_rental_contracts
from restwert.lake.common import billing_date
from restwert.lakegen.catalogue import to_v01_devices_frame
from restwert.lakegen.config import LakeConfig

if TYPE_CHECKING:  # pragma: no cover
    from restwert.lakegen import World

RENTAL_CONTRACT_COLUMNS: tuple[str, ...] = (
    "contract_id", "customer_id", "serial", "start_date", "term_months", "monthly_rate_eur", "end_date",
    "actual_end_date", "status", "replaces_contract_id",
)
RENTAL_INVOICE_COLUMNS: tuple[str, ...] = (
    "invoice_id", "contract_id", "serial", "period_no", "period_month", "invoice_date", "amount_eur",
)
STAGING_COLUMNS: tuple[str, ...] = ("staging_id", "serial", "staged_at", "mdm_enrolled", "staging_cost_eur")
SHIPMENT_COLUMNS: tuple[str, ...] = (
    "shipment_id", "serial", "related_serial", "direction", "shipped_at", "delivered_at", "rental_contract_ref",
    "carrier_ref", "cost_eur",
)
TICKET_COLUMNS: tuple[str, ...] = (
    "ticket_id", "serial", "contract_id", "opened_at", "closed_at", "damage_type", "resolution", "quote_eur",
    "repair_cost_eur", "replacement_serial", "repair_partner_ref",
)
RECEIPT_COLUMNS: tuple[str, ...] = (
    "receipt_id", "serial", "contract_id", "returned_at", "grade_declared", "grade_inspected", "inspected_at",
    "wipe_certificate_id", "wiped_at", "wipe_grading_cost_eur",
)
WORK_ORDER_COLUMNS: tuple[str, ...] = (
    "work_order_id", "serial", "started_at", "finished_at", "cost_eur", "grade_out", "outcome", "partner_ref",
)

CARRIER_REF: str = "Logistics partner (role-only)"
REPAIR_PARTNER_REF: str = "Refurbishment and repair partner (role-only)"
T_STAGED = time(9, 0)
T_SHIPPED = time(8, 0)
T_DELIVERED = time(12, 0)
T_OPENED = time(9, 30)
T_CLOSED = time(16, 0)
T_RETURNED = time(11, 0)
T_INSPECTED = time(14, 0)
T_WIPED = time(15, 0)
T_STARTED = time(8, 0)
T_FINISHED = time(17, 0)
DAMAGE_COST_FACTOR: dict[str, float] = {"water": 1.6, "screen": 1.2}


def _d(v) -> date | None:
    """A python date from a date, Timestamp or None."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


def _ts(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def build_operations(cfg: LakeConfig, world: "World", rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Add the seven operational feeds to ``world.frames`` and the v0.1 frames to ``world.v01``.

    Reads ``world.fleet`` as left by the purchase step (``received_at``, ``landed_cost_eur``).
    Returns the landing frames by feed key.
    """
    fleet = world.fleet
    as_of: date = cfg.as_of
    devices = to_v01_devices_frame(fleet)
    contracts = build_rental_contracts(cfg, devices, rng)
    first_contract = (
        contracts.sort_values(["start_date", "contract_id"], kind="stable")
        .drop_duplicates("serial", keep="first")
        .set_index("serial")["contract_id"]
    )
    devices["contract_id"] = devices["serial"].map(first_contract)
    devices["contract_id"] = [None if pd.isna(v) else str(v) for v in devices["contract_id"]]
    events, contracts, devices = build_events(cfg, devices, contracts, rng)
    refurb = build_refurbishment(cfg, events, devices, rng)

    received_of: dict[str, date] = {str(s): _ts_date(ts) for s, ts in zip(fleet["serial"], fleet["received_at"])}
    fam_of: dict[str, str] = dict(zip(devices["serial"].astype(str), devices["model_family"].astype(str)))

    # ---------------------------------------------------------------- portal contracts and invoices
    portal = pd.DataFrame(
        {
            "contract_id": contracts["contract_id"].astype(str),
            "customer_id": contracts["customer_id"].astype(str),
            "serial": contracts["serial"].astype(str),
            "start_date": [_d(v) for v in contracts["start_date"]],
            "term_months": contracts["term_months"].astype(int),
            "monthly_rate_eur": contracts["monthly_rate"].astype(float).round(2),
            "end_date": [_d(v) for v in contracts["end_date"]],
            "actual_end_date": [_d(v) for v in contracts["actual_end_date"]],
            "status": contracts["status"].astype(str),
            "replaces_contract_id": [None if pd.isna(v) else str(v) for v in contracts["replaces_contract_id"]],
        }
    )[list(RENTAL_CONTRACT_COLUMNS)]

    invoice_rows: list[dict] = []
    for cid, serial, start, end, actual_end, rate in zip(
        portal["contract_id"], portal["serial"], portal["start_date"], portal["end_date"],
        portal["actual_end_date"], portal["monthly_rate_eur"],
    ):
        stop = min(actual_end if actual_end is not None else end, as_of)
        n = months_between(start, stop) if stop > start else 0
        for k in range(1, n + 1):
            invoice_rows.append(
                {
                    "invoice_id": f"RI-{cid[3:]}-{k:03d}",
                    "contract_id": cid,
                    "serial": serial,
                    "period_no": k,
                    "period_month": month_floor(add_months(start, k - 1)),
                    "invoice_date": billing_date(start, k),
                    "amount_eur": float(rate),
                }
            )
    rental_invoices = pd.DataFrame(invoice_rows, columns=list(RENTAL_INVOICE_COLUMNS))

    # ---------------------------------------------------------------- staging and outbound shipments
    staging_rows: list[dict] = []
    shipment_rows: list[dict] = []
    stage_lo, stage_hi = (float(x) for x in cfg.staging_cost_eur)
    out_lo, out_hi = (float(x) for x in cfg.outbound_cost_eur)
    staged_of_contract: dict[str, date] = {}
    for cid, serial, start, replaces in zip(portal["contract_id"], portal["serial"], portal["start_date"], portal["replaces_contract_id"]):
        received = received_of[serial]
        staged_day = max(received + timedelta(days=1), start - timedelta(days=int(rng.integers(1, 4))))
        staged_of_contract[cid] = staged_day
        staging_rows.append(
            {
                "serial": serial,
                "staged_at": _ts(staged_day, T_STAGED),
                "mdm_enrolled": bool(rng.random() < float(cfg.mdm_enrolled_share)),
                "staging_cost_eur": round(float(rng.uniform(stage_lo, stage_hi)), 2),
            }
        )
        if pd.isna(replaces):
            shipment_rows.append(
                {
                    "serial": serial,
                    "related_serial": None,
                    "direction": "outbound",
                    "shipped_at": _ts(start - timedelta(days=1), T_SHIPPED),
                    "delivered_at": _ts(start, T_DELIVERED),
                    "rental_contract_ref": cid,
                    "carrier_ref": CARRIER_REF,
                    "cost_eur": round(float(rng.uniform(out_lo, out_hi)), 2),
                }
            )

    # ---------------------------------------------------------------- tickets, replacement and return shipments, receipts
    next_rc_of_spare: dict[str, str] = {
        str(serial): str(cid)[3:]
        for cid, serial, replaces in zip(portal["contract_id"], portal["serial"], portal["replaces_contract_id"])
        if not pd.isna(replaces)
    }
    ticket_rows: list[dict] = []
    receipt_rows: list[dict] = []
    wipe_lo, wipe_hi = (float(x) for x in cfg.wipe_grading_cost_eur)
    pending: dict[tuple[str, str], list[int]] = {}   # (serial, contract) -> indices of open (resolved) damage tickets
    ev = events.sort_values(["event_date", "serial", "event_id"], kind="stable").reset_index(drop=True)
    for r in ev.itertuples(index=False):
        serial = str(r.serial)
        cid = None if pd.isna(r.contract_id) else str(r.contract_id)
        kind = str(r.event_type)
        d = _d(r.event_date)
        if kind == "damage":
            resolved = bool(r.resolved)
            fc = cfg.families[fam_of[serial]]
            quote = round(float(r.cost), 2) if not resolved else None
            ticket_rows.append(
                {
                    "serial": serial,
                    "contract_id": cid,
                    "opened_at": _ts(d, T_OPENED),
                    "closed_at": None,
                    "damage_type": str(r.damage_type),
                    "resolution": "open",
                    "quote_eur": quote,
                    "repair_cost_eur": None,
                    "replacement_serial": None,
                    "repair_partner_ref": REPAIR_PARTNER_REF,
                    "_fc": fc,
                }
            )
            if resolved:
                pending.setdefault((serial, cid or ""), []).append(len(ticket_rows) - 1)
        elif kind == "repair":
            queue = pending.get((serial, cid or ""), [])
            if queue:
                t = ticket_rows[queue.pop(0)]
                t["closed_at"] = _ts(d, T_CLOSED)
                t["resolution"] = "repair"
                t["repair_cost_eur"] = round(float(r.cost), 2)
                t["quote_eur"] = round(float(r.cost), 2)
        elif kind == "replacement":
            queue = pending.get((serial, cid or ""), [])
            if queue:
                t = ticket_rows[queue.pop(0)]
                fc = t["_fc"]
                t["closed_at"] = _ts(d, T_CLOSED)
                t["resolution"] = "replace"
                t["replacement_serial"] = str(r.replacement_serial)
                factor = DAMAGE_COST_FACTOR.get(str(r.damage_type), 1.0)
                t["quote_eur"] = round(float(rng.uniform(fc.repair_cost_min, fc.repair_cost_max)) * factor, 2)
            # the spare ships on the damage date, never before it was staged (a spare bought after
            # the damage, which the v0.1 spare pool allows, ships as soon as it is ready)
            spare_ready = staged_of_contract.get(f"RC-{next_rc_of_spare.get(str(r.replacement_serial), '')}", d)
            ship_day = max(d, spare_ready)
            shipment_rows.append(
                {
                    "serial": str(r.replacement_serial),
                    "related_serial": serial,
                    "direction": "replacement_out",
                    "shipped_at": _ts(ship_day, T_SHIPPED),
                    "delivered_at": _ts(ship_day, T_DELIVERED),
                    "rental_contract_ref": cid,
                    "carrier_ref": CARRIER_REF,
                    "cost_eur": round(float(r.cost), 2),
                }
            )
        elif kind == "return":
            rd = _d(r.return_date) or d
            if rd > as_of:
                continue
            shipment_rows.append(
                {
                    "serial": serial,
                    "related_serial": None,
                    "direction": "return",
                    "shipped_at": _ts(rd - timedelta(days=int(rng.integers(1, 4))), T_SHIPPED),
                    "delivered_at": _ts(rd, T_DELIVERED),
                    "rental_contract_ref": cid,
                    "carrier_ref": CARRIER_REF,
                    "cost_eur": round(float(r.cost), 2),
                }
            )
            inspected = min(rd + timedelta(days=int(rng.integers(1, 4))), as_of)
            has_wipe = bool(r.wipe_certificate) if not pd.isna(r.wipe_certificate) else False
            wipe_id = None
            wiped_at = None
            if has_wipe:
                wipe_id = f"WIPE-{int(rng.integers(0, 16 ** 8)):08X}"
                wiped_day = min(rd + timedelta(days=int(rng.integers(0, 3))), as_of)
                wiped_at = _ts(wiped_day, T_WIPED)
                inspected = max(inspected, wiped_day)   # grading follows the wipe (the chain stays monotonic)
            receipt_rows.append(
                {
                    "serial": serial,
                    "contract_id": cid,
                    "returned_at": _ts(rd, T_RETURNED),
                    "grade_declared": str(r.grade_pre_return),
                    "grade_inspected": str(r.grade_inspected),
                    "inspected_at": _ts(inspected, T_INSPECTED),
                    "wipe_certificate_id": wipe_id,
                    "wiped_at": wiped_at,
                    "wipe_grading_cost_eur": round(float(rng.uniform(wipe_lo, wipe_hi)), 2),
                }
            )
    for t in ticket_rows:
        t.pop("_fc", None)
        if t["quote_eur"] is None:
            t["quote_eur"] = 0.0

    # ---------------------------------------------------------------- work orders (finished by as_of)
    wo_rows: list[dict] = []
    for r in refurb.itertuples(index=False):
        end = _d(r.end_date)
        if end is None or end > as_of:
            continue
        wo_rows.append(
            {
                "serial": str(r.serial),
                "started_at": _ts(_d(r.start_date), T_STARTED),
                "finished_at": _ts(end, T_FINISHED),
                "cost_eur": round(float(r.cost), 2),
                "grade_out": str(r.grade_out),
                "outcome": str(r.outcome),
                "partner_ref": REPAIR_PARTNER_REF,
            }
        )

    # ---------------------------------------------------------------- ids in event order
    staging = pd.DataFrame(staging_rows, columns=[c for c in STAGING_COLUMNS if c != "staging_id"])
    staging = staging.sort_values(["staged_at", "serial"], kind="stable").reset_index(drop=True)
    staging.insert(0, "staging_id", [f"ST-{i + 1:07d}" for i in range(len(staging))])

    shipments = pd.DataFrame(shipment_rows, columns=[c for c in SHIPMENT_COLUMNS if c != "shipment_id"])
    shipments = shipments.sort_values(["shipped_at", "direction", "serial"], kind="stable").reset_index(drop=True)
    shipments.insert(0, "shipment_id", [f"SH-{i + 1:07d}" for i in range(len(shipments))])

    tickets = pd.DataFrame(ticket_rows, columns=[c for c in TICKET_COLUMNS if c != "ticket_id"])
    tickets = tickets.sort_values(["opened_at", "serial"], kind="stable").reset_index(drop=True)
    tickets.insert(0, "ticket_id", [f"TK-{i + 1:06d}" for i in range(len(tickets))])

    receipts = pd.DataFrame(receipt_rows, columns=[c for c in RECEIPT_COLUMNS if c != "receipt_id"])
    receipts = receipts.sort_values(["returned_at", "serial"], kind="stable").reset_index(drop=True)
    receipts.insert(0, "receipt_id", [f"RR-{i + 1:06d}" for i in range(len(receipts))])

    work_orders = pd.DataFrame(wo_rows, columns=[c for c in WORK_ORDER_COLUMNS if c != "work_order_id"])
    work_orders = work_orders.sort_values(["started_at", "serial"], kind="stable").reset_index(drop=True)
    work_orders.insert(0, "work_order_id", [f"WO-{i + 1:06d}" for i in range(len(work_orders))])

    frames = {
        "portal/rental_contracts": portal,
        "portal/rental_invoices": rental_invoices,
        "wms/staging_log": staging[list(STAGING_COLUMNS)],
        "wms/shipments": shipments[list(SHIPMENT_COLUMNS)],
        "servicedesk/tickets": tickets[list(TICKET_COLUMNS)],
        "returns/receipts": receipts[list(RECEIPT_COLUMNS)],
        "refurb/work_orders": work_orders[list(WORK_ORDER_COLUMNS)],
    }
    world.frames.update(frames)
    world.v01.update({"devices": devices, "rental_contracts": contracts, "events": events, "refurbishment": refurb})
    return frames


def _ts_date(ts) -> date:
    """Date part of a datetime or Timestamp."""
    if isinstance(ts, datetime):
        return ts.date()
    return ts


__all__ = [
    "build_operations",
    "RENTAL_CONTRACT_COLUMNS",
    "RENTAL_INVOICE_COLUMNS",
    "STAGING_COLUMNS",
    "SHIPMENT_COLUMNS",
    "TICKET_COLUMNS",
    "RECEIPT_COLUMNS",
    "WORK_ORDER_COLUMNS",
    "CARRIER_REF",
    "REPAIR_PARTNER_REF",
]
