"""Injected defects: the landing files must give ingest something to refuse (SPEC_v0.2 section 5.4).

``inject(world, cfg, rng)`` mutates the landing frames after everything else and
returns the counts per defect (also stored in ``world.defects_injected``). Order of
injection: serial typos, orphan freight, deleted credit notes, then the duplicates, so
that a copy is always a copy of the final row:

* ``unknown_serial``: ``wms/shipments`` outbound rows get one hex digit of their serial
  changed (a serial no goods receipt minted, unresolved ``unknown_serial``);
* ``identical_duplicate``: rows of transactional feeds are repeated unchanged in the
  next delivery period of the same feed (``duplicates_identical``, skipped at ingest);
* ``conflicting_duplicate``: ``portal/rental_invoices`` rows are repeated in the next
  delivery with ``amount_eur + 0.01`` (``duplicate_conflict``, the first delivery wins);
* ``missing_credit_note``: credit notes of sold orders are deleted (the chain ends at
  ``sold_at``, ``credited_at`` stays empty, the data quality KPI sees it);
* ``orphan_freight``: freight invoice lines point at ``po_line + 90`` (``unknown_po_line``;
  the ledger lacks that freight, the conform step lacks it the same way).

Shares come from ``cfg.defects``; every defect is injected at least once when its
feed has rows, so that a small fleet still exercises every reject path. Duplicates
are only injected where a next delivery period exists before ``as_of`` (otherwise
they could never be written and the count would be wrong). Reference feeds and the
contracts register are never touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from restwert.lakegen.config import LakeConfig
from restwert.lakegen.writer import FEEDS, TRANSACTIONAL_FEEDS, assign_periods, delivery_periods

if TYPE_CHECKING:  # pragma: no cover
    from restwert.lakegen import World

DEFECT_KINDS: tuple[str, ...] = (
    "unknown_serial", "identical_duplicate", "conflicting_duplicate", "missing_credit_note", "orphan_freight",
)
ORPHAN_LINE_OFFSET: int = 90
CONFLICT_DELTA_EUR: float = 0.01
HEX = "0123456789ABCDEF"


def _k(share: float, n: int) -> int:
    """At least one when the feed has rows, else ``round(share * n)`` capped at ``n``."""
    if n <= 0:
        return 0
    return int(min(n, max(1, round(float(share) * n))))


def _pick(rng: np.random.Generator, candidates: np.ndarray, k: int) -> np.ndarray:
    if k <= 0 or len(candidates) == 0:
        return np.zeros(0, dtype=int)
    chosen = rng.choice(candidates, size=min(k, len(candidates)), replace=False)
    return np.sort(np.asarray(chosen, dtype=int))


def typo_serial(serial: str, rng: np.random.Generator, taken: set[str]) -> str:
    """Change one hex digit of the 8-hex tail to a different one; never an existing serial."""
    head, tail = serial[:-8], serial[-8:]
    for _ in range(64):
        pos = int(rng.integers(0, 8))
        new_digit = HEX[int(rng.integers(0, 16))]
        if new_digit == tail[pos]:
            continue
        candidate = head + tail[:pos] + new_digit + tail[pos + 1:]
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not build a serial typo")


def _shiftable(df: pd.DataFrame, key: str, periods, as_of) -> np.ndarray:
    """Row positions whose copy would land in an existing next period."""
    idx = assign_periods(df.drop(columns=[c for c in df.columns if c.startswith("_")]), FEEDS[key], periods, as_of)
    ok = (idx >= 0) & (idx + 1 < len(periods))
    return np.flatnonzero(ok)


def inject(world: "World", cfg: LakeConfig, rng: np.random.Generator) -> dict[str, int]:
    """Mutate ``world.frames`` in place (see module docstring); return the counts per defect."""
    d = cfg.defects
    as_of = cfg.as_of
    periods = delivery_periods(cfg)
    counts: dict[str, int] = {k: 0 for k in DEFECT_KINDS}
    frames = world.frames
    taken: set[str] = set(frames["erp/goods_receipts"]["serial"].astype(str))

    # ---------------------------------------------------------------- unknown_serial on outbound shipments
    ship = frames["wms/shipments"]
    outbound = np.flatnonzero((ship["direction"] == "outbound").to_numpy())
    chosen = _pick(rng, outbound, _k(d.unknown_serial_share, len(outbound)))
    if len(chosen):
        serials = ship["serial"].astype(str).to_numpy().copy()
        for pos in chosen:
            serials[pos] = typo_serial(serials[pos], rng, taken)
        ship = ship.copy()
        ship["serial"] = serials
        frames["wms/shipments"] = ship
        counts["unknown_serial"] = int(len(chosen))

    # ---------------------------------------------------------------- orphan freight lines
    key = "erp/supplier_invoices"
    inv = frames[key]
    freight = np.flatnonzero((inv["line_kind"] == "freight").to_numpy())
    chosen = _pick(rng, freight, _k(d.orphan_freight_share, len(freight)))
    if len(chosen):
        inv = inv.copy()
        lines = inv["po_line"].astype(int).to_numpy().copy()
        lines[chosen] = lines[chosen] + ORPHAN_LINE_OFFSET
        inv["po_line"] = lines
        frames[key] = inv
        counts["orphan_freight"] = int(len(chosen))

    # ---------------------------------------------------------------- missing credit notes
    key = "recommerce/credit_notes"
    notes = frames[key]
    if len(notes):
        original = np.arange(len(notes))
        chosen = _pick(rng, original, _k(d.missing_credit_note_share, len(original)))
        if len(chosen):
            deleted_orders = set(notes["order_id"].astype(str).iloc[chosen])
            keep = ~notes["order_id"].astype(str).isin(deleted_orders)
            frames[key] = notes[keep].reset_index(drop=True)
            counts["missing_credit_note"] = int(len(deleted_orders))

    # ---------------------------------------------------------------- identical duplicates on every transactional feed
    total_identical = 0
    largest: tuple[str, int] = ("", 0)
    for key in TRANSACTIONAL_FEEDS:
        df = frames.get(key)
        if df is None or len(df) == 0:
            continue
        cand = _shiftable(df, key, periods, as_of)
        if len(cand) > largest[1]:
            largest = (key, len(cand))
        k = int(min(len(cand), round(float(d.identical_duplicate_share) * len(df))))
        chosen = _pick(rng, cand, k)
        if len(chosen) == 0:
            continue
        copies = df.iloc[chosen].copy()
        copies["_delivery_shift"] = 1
        base = df.copy()
        if "_delivery_shift" not in base.columns:
            base["_delivery_shift"] = 0
        frames[key] = pd.concat([base, copies], ignore_index=True)
        total_identical += int(len(chosen))
    if total_identical == 0 and largest[0]:
        key = largest[0]
        df = frames[key]
        cand = _shiftable(df, key, periods, as_of)
        chosen = _pick(rng, cand, 1)
        copies = df.iloc[chosen].copy()
        copies["_delivery_shift"] = 1
        base = df.copy()
        base["_delivery_shift"] = 0
        frames[key] = pd.concat([base, copies], ignore_index=True)
        total_identical = int(len(chosen))
    counts["identical_duplicate"] = total_identical

    # ---------------------------------------------------------------- conflicting duplicates on rental invoices
    key = "portal/rental_invoices"
    inv = frames[key]
    if len(inv):
        original = np.flatnonzero((inv["_delivery_shift"] == 0).to_numpy()) if "_delivery_shift" in inv.columns else np.arange(len(inv))
        cand = np.intersect1d(original, _shiftable(inv, key, periods, as_of))
        chosen = _pick(rng, cand, _k(d.conflicting_duplicate_share, len(cand)))
        if len(chosen):
            copies = inv.iloc[chosen].copy()
            copies["amount_eur"] = (copies["amount_eur"].astype(float) + CONFLICT_DELTA_EUR).round(2)
            copies["_delivery_shift"] = 1
            base = inv.copy()
            if "_delivery_shift" not in base.columns:
                base["_delivery_shift"] = 0
            frames[key] = pd.concat([base, copies], ignore_index=True)
            counts["conflicting_duplicate"] = int(len(chosen))

    world.defects_injected = counts
    return counts


__all__ = ["inject", "typo_serial", "DEFECT_KINDS", "ORPHAN_LINE_OFFSET", "CONFLICT_DELTA_EUR"]
