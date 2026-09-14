"""The timestamp chain per serial: ``silver.serial_timeline`` and ``gold.chain_quality`` (SPEC_v0.2 section 4.5).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Ten dates per serial in cycle order: ordered, received, staged, shipped,
returned, wiped, graded, sellable, sold, credited. Which of them a serial
must have depends on its lifecycle status (``EXPECTED_STEPS``). A chain is
complete when every expected step is present and the present steps are in
order. The share of serials with a complete chain is the data quality KPI of
the Data page; a missing credit note or a return booked before the shipment
shows up here, never as a silent NULL somewhere downstream.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import duckdb
import numpy as np
import pandas as pd

from restwert import db
from restwert.enums import TIMELINE_STEPS
from restwert.lake.schema_lake import create_lake_schema
from restwert.records import RunSummary

STEPS: tuple[str, ...] = TIMELINE_STEPS

EXPECTED_STEPS: dict[str, tuple[str, ...]] = {
    "not_deployed": ("ordered_at", "received_at"),
    "rented": ("ordered_at", "received_at", "staged_at", "shipped_at"),
    "awaiting_return": ("ordered_at", "received_at", "staged_at", "shipped_at"),
    "wip": ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at"),
    "in_stock": ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at", "wiped_at", "graded_at", "sellable_at"),
    "sold": STEPS,
    "scrapped": ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at", "wiped_at", "graded_at"),
}

_DAY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("days_order_to_receipt", "ordered_at", "received_at"),
    ("days_receipt_to_ship", "received_at", "shipped_at"),
    ("days_return_to_sellable", "returned_at", "sellable_at"),
    ("days_sellable_to_sold", "sellable_at", "sold_at"),
    ("days_sold_to_credited", "sold_at", "credited_at"),
    ("days_return_to_cash", "returned_at", "credited_at"),
)

_EMPTY = pd.DataFrame()


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _first(df: pd.DataFrame, key: str, col: str, ref_col: str, ref_system: str, largest: bool = False) -> pd.DataFrame:
    """Per ``key``: the earliest (or latest) ``col`` with the business key that produced it, as ``value`` / ``ref``."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["value", "ref"])
    w = df[[key, col, ref_col]].copy()
    w["_ts"] = _ts(w[col])
    w = w[w["_ts"].notna()].sort_values([key, "_ts", ref_col], ascending=[True, not largest, True])
    w = w.drop_duplicates(key, keep="first").set_index(key)
    out = pd.DataFrame({"value": w["_ts"], "ref": ref_system + ":" + w[ref_col].astype(str)})
    return out


def _step_frame(bronze: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Raw step timestamps per serial (before the as_of cut) and the source ref per step."""
    gr = bronze.get("erp_goods_receipts", _EMPTY)
    po = bronze.get("erp_purchase_orders", _EMPTY)
    staging = bronze.get("wms_staging_log", _EMPTY)
    ship = bronze.get("wms_shipments", _EMPTY)
    receipts = bronze.get("ret_receipts", _EMPTY)
    wo = bronze.get("rf_work_orders", _EMPTY)
    orders = bronze.get("rc_orders", _EMPTY)
    cn = bronze.get("rc_credit_notes", _EMPTY)

    serials = pd.Index(sorted(gr["serial"].astype(str).unique()) if len(gr) else [], name="serial")
    steps = pd.DataFrame(index=serials)
    refs: dict[str, pd.Series] = {}

    # ordered_at: the header order date of the receipt's PO
    if len(gr):
        g = gr[["serial", "po_number", "received_at"]].copy()
        if len(po):
            g = g.merge(po[["po_number", "order_date"]], on="po_number", how="left")
        else:
            g["order_date"] = None
        g = g.set_index("serial")
        steps["ordered_at"] = _ts(g["order_date"]).reindex(serials)
        refs["ordered_at"] = ("erp:" + g["po_number"].astype(str)).reindex(serials)
        steps["received_at"] = _ts(g["received_at"]).reindex(serials)
        refs["received_at"] = pd.Series(["erp:" + str(s) for s in serials], index=serials, dtype=object)
    for step in ("ordered_at", "received_at"):
        if step not in steps.columns:
            steps[step] = pd.NaT

    def _put(step: str, frame: pd.DataFrame) -> None:
        steps[step] = frame["value"].reindex(serials) if len(frame) else pd.Series(pd.NaT, index=serials)
        refs[step] = frame["ref"].reindex(serials) if len(frame) else pd.Series(None, index=serials, dtype=object)

    _put("staged_at", _first(staging, "serial", "staged_at", "staging_id", "wms"))
    outbound = ship[ship["direction"].isin(["outbound", "replacement_out"])] if len(ship) else _EMPTY
    _put("shipped_at", _first(outbound, "serial", "shipped_at", "shipment_id", "wms"))
    latest_receipt = _first(receipts, "serial", "returned_at", "receipt_id", "returns", largest=True)
    _put("returned_at", latest_receipt)
    if len(receipts) and len(latest_receipt):
        r = receipts.set_index("receipt_id")
        rid = latest_receipt["ref"].str.split(":", n=1).str[1]
        wiped = _ts(r["wiped_at"]).reindex(rid.values)
        graded = _ts(r["inspected_at"]).reindex(rid.values)
        steps["wiped_at"] = pd.Series(wiped.values, index=latest_receipt.index).reindex(serials)
        steps["graded_at"] = pd.Series(graded.values, index=latest_receipt.index).reindex(serials)
        refs["wiped_at"] = latest_receipt["ref"].reindex(serials)
        refs["graded_at"] = latest_receipt["ref"].reindex(serials)
    else:
        steps["wiped_at"] = pd.NaT
        steps["graded_at"] = pd.NaT
    non_scrap = wo[wo["outcome"] != "scrap"] if len(wo) else _EMPTY
    _put("sellable_at", _first(non_scrap, "serial", "finished_at", "work_order_id", "refurb", largest=True))
    _put("sold_at", _first(orders, "serial", "sold_at", "order_id", "recommerce", largest=True))
    _put("credited_at", _first(cn, "serial", "credited_at", "credit_note_id", "recommerce", largest=True))
    for step in STEPS:
        steps[step] = pd.to_datetime(steps[step], errors="coerce")
    return steps[list(STEPS)], refs


def build_timeline(bronze: dict[str, pd.DataFrame], device_status: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """``silver.serial_timeline`` rows for every serial in ``erp_goods_receipts`` (events after ``as_of`` are NULL)."""
    steps, refs = _step_frame(bronze)
    serials = steps.index
    cut = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    for step in STEPS:
        # the chain is a chain of dates: two steps on the same day are in order whatever their clock times
        steps[step] = steps[step].where(steps[step] < cut, pd.NaT).dt.normalize()

    status = pd.Series("not_deployed", index=serials, dtype=object)
    if device_status is not None and len(device_status) and "serial" in device_status.columns:
        ds = device_status.drop_duplicates("serial").set_index("serial")["lifecycle_status"]
        status = ds.reindex(serials).fillna("not_deployed").astype(object)

    present = steps.notna()
    n = len(serials)
    steps_expected = np.zeros(n, dtype="int64")
    steps_present = np.zeros(n, dtype="int64")
    missing_steps: list[str | None] = [None] * n
    first_missing: list[str | None] = [None] * n
    status_arr = status.to_numpy()
    for st, expected in EXPECTED_STEPS.items():
        mask = status_arr == st
        if not mask.any():
            continue
        exp_cols = list(expected)
        pres = present.loc[mask, exp_cols]
        steps_expected[mask] = len(exp_cols)
        steps_present[mask] = pres.sum(axis=1).to_numpy()
        miss = ~pres
        idx = np.flatnonzero(mask)
        for i, row in zip(idx, miss.to_numpy()):
            missing = [c for c, m in zip(exp_cols, row) if m]
            if missing:
                missing_steps[i] = ",".join(missing)
                first_missing[i] = missing[0]
    unknown = ~np.isin(status_arr, list(EXPECTED_STEPS))
    if unknown.any():
        steps_expected[unknown] = 0

    # monotonic: every present step >= the latest earlier present step
    running = pd.Series(pd.NaT, index=serials, dtype="datetime64[ns]")
    running_name = pd.Series(None, index=serials, dtype=object)
    is_monotonic = pd.Series(True, index=serials)
    pair = pd.Series(None, index=serials, dtype=object)
    for step in STEPS:
        cur = steps[step]
        viol = cur.notna() & running.notna() & (cur < running) & is_monotonic
        if viol.any():
            is_monotonic[viol] = False
            pair[viol] = running_name[viol] + ">" + step
        running = cur.where(cur.notna(), running)
        running_name = running_name.where(cur.isna(), step)

    chain_complete = (steps_present == steps_expected) & is_monotonic.to_numpy() & ~unknown
    is_syn = True
    gr = bronze.get("erp_goods_receipts", _EMPTY)
    if len(gr) and "is_synthetic" in gr.columns:
        is_syn = bool(gr["is_synthetic"].iloc[0])

    out = pd.DataFrame(index=serials)
    out["serial"] = serials.astype(str)
    out["as_of"] = as_of
    for step in STEPS:
        out[step] = steps[step].dt.normalize()
    out["lifecycle_status"] = status.to_numpy()
    out["steps_expected"] = steps_expected
    out["steps_present"] = steps_present
    out["missing_steps"] = missing_steps
    out["first_missing_step"] = first_missing
    out["is_monotonic"] = is_monotonic.to_numpy()
    out["non_monotonic_pair"] = pair.to_numpy()
    out["chain_complete"] = chain_complete
    for name, a, b in _DAY_PAIRS:
        delta = (steps[b].dt.normalize() - steps[a].dt.normalize()).dt.days
        out[name] = delta.astype("Int64")
    ref_rows = []
    for serial in serials:
        d = {}
        for step in STEPS:
            if present.at[serial, step] and step in refs:
                ref = refs[step].get(serial)
                if ref is not None and not (isinstance(ref, float) and np.isnan(ref)):
                    d[step] = ref
        ref_rows.append(json.dumps(d, sort_keys=True))
    out["source_refs_json"] = ref_rows
    out["is_synthetic"] = is_syn
    return out.reset_index(drop=True)


def chain_quality(timeline: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """``gold.chain_quality``: per (lifecycle_status, step) the share of serials with the step present."""
    rows = []
    if timeline is None or len(timeline) == 0:
        return pd.DataFrame(columns=["lifecycle_status", "step", "n_serials", "n_present", "share_present", "is_expected", "as_of"])
    for st, grp in timeline.groupby("lifecycle_status", sort=True):
        expected = set(EXPECTED_STEPS.get(str(st), ()))
        n = int(len(grp))
        for step in STEPS:
            n_present = int(grp[step].notna().sum())
            rows.append({
                "lifecycle_status": st, "step": step, "n_serials": n, "n_present": n_present,
                "share_present": (n_present / n) if n else None, "is_expected": step in expected, "as_of": as_of,
            })
    return pd.DataFrame(rows)


def run_timeline(con: duckdb.DuckDBPyConnection, as_of: date) -> RunSummary:
    """Read bronze and ``device_pnl``, write ``silver.serial_timeline`` and ``gold.chain_quality``."""
    from restwert.lake.conform import read_bronze

    started = datetime.now(timezone.utc)
    db.create_schema(con)
    create_lake_schema(con, drop_layers=())
    run_id = db.new_run(con, "timeline", None, as_of, None)
    notes: list[str] = []
    bronze = read_bronze(con)
    if db.table_exists(con, "device_pnl"):
        device_status = db.read_df(con, "SELECT serial, lifecycle_status FROM device_pnl")
        if len(device_status) == 0:
            notes.append("device_pnl is empty: every status is not_deployed (run pnl first)")
    else:
        device_status = pd.DataFrame(columns=["serial", "lifecycle_status"])
        notes.append("device_pnl missing: every status is not_deployed (run pnl first)")
    timeline = build_timeline(bronze, device_status, as_of)
    n_tl = db.write_df(con, "silver.serial_timeline", timeline, mode="replace")
    quality = chain_quality(timeline, as_of)
    n_q = db.write_df(con, "gold.chain_quality", quality, mode="replace")
    status_counts = timeline["lifecycle_status"].value_counts().to_dict() if len(timeline) else {}
    counts = {
        "serial_timeline": int(n_tl),
        "chain_quality": int(n_q),
        "chain_complete": int(timeline["chain_complete"].sum()) if len(timeline) else 0,
        "non_monotonic": int((~timeline["is_monotonic"]).sum()) if len(timeline) else 0,
        **{f"status_{k}": int(v) for k, v in status_counts.items()},
    }
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="timeline", run_id=run_id, started_at=started, finished_at=finished,
        seconds=(finished - started).total_seconds(), counts=counts, notes=notes,
    )


__all__ = ["STEPS", "EXPECTED_STEPS", "build_timeline", "chain_quality", "run_timeline"]
