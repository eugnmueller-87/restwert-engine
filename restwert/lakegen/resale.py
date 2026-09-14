"""Resale orders and credit notes priced on the calibrated truth (SPEC_v0.2 section 5.4).

For every work order with outcome ``sellable`` or ``as_is`` finished on or before
``as_of`` (at most one per serial): channel by ``channel_mix_by_grade[grade_out]``,
``listed_at = finished_at + U(0, 3)`` days, ``sold_at = listed_at + U(days_to_sale[channel])``
(slow movers, share ``slow_mover_share``, draw ``U(slow_mover_days)`` instead), skipped when
``sold_at > as_of`` (the device is in stock); ``age = months_between_float(launch_date, sold_at)``;
``gross_price_eur = calibrate.true_price(rrp_net, truth_curve(family, oem), age, grade_out,
channel, channel_mult, truth_v2, noise ~ N(0, noise_sigma))``; buyer type by channel;
``order_id = RO-<seq:06d>``.

One credit note per order at ``sold_at + days_to_cash[channel]`` (the channel fee
structure of ``assumptions.yaml``, mirrored in ``lake.yaml`` and tested equal), only
when ``<= as_of``: ``fee_pct_eur = round(gross x fee_pct, 2)``, ``fee_fixed_eur``,
``net_eur = gross - fees``; ``credit_note_id = CN-<seq:06d>``.

The v0.1-shaped ``resale`` frame (sale_id = order_id, price = gross, fees = fee_pct_eur +
fee_fixed_eur) is returned as well so the world carries every v0.1 table.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from restwert.dates import months_between_float
from restwert.generate.recommerce import BUYER_TYPE_BY_CHANNEL
from restwert.lakegen.calibrate import TruthCurve, true_price, truth_curve
from restwert.lakegen.catalogue import FleetCatalogue
from restwert.lakegen.config import LakeConfig

if TYPE_CHECKING:  # pragma: no cover
    from restwert.lakegen import World

ORDER_COLUMNS: tuple[str, ...] = (
    "order_id", "serial", "channel", "listed_at", "sold_at", "gross_price_eur", "buyer_type", "grade_at_sale",
)
CREDIT_NOTE_COLUMNS: tuple[str, ...] = (
    "credit_note_id", "order_id", "serial", "channel", "credited_at", "gross_eur", "fee_pct_eur", "fee_fixed_eur", "net_eur",
)
V01_RESALE_COLUMNS: tuple[str, ...] = ("sale_id", "serial", "channel", "sale_date", "price", "fees", "buyer_type", "grade_at_sale")
T_LISTED = time(10, 0)
T_SOLD = time(12, 0)
T_CREDITED = time(9, 0)


def _d(v) -> date | None:
    if v is None or v is pd.NaT or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


def build_resale(
    cfg: LakeConfig,
    world: "World",
    cat: FleetCatalogue,
    curves: pd.DataFrame,
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """Add ``recommerce/orders`` and ``recommerce/credit_notes`` to ``world.frames`` and return them.

    Reads ``world.fleet`` (``serial, catalogue_family, oem, launch_date, rrp_net``) and
    ``world.frames["refurb/work_orders"]``; writes ``world.v01["resale"]`` and
    ``world.truth_sources`` as well. ``cat`` is kept for the interface (the pool's
    rrp_net already travels on the fleet).
    """
    fleet = world.fleet
    work_orders = world.frames["refurb/work_orders"]
    as_of: date = cfg.as_of
    truth = cfg.truth_v2
    by_serial = {str(rec["serial"]): rec for rec in fleet[["serial", "catalogue_family", "oem", "launch_date", "rrp_net"]].to_dict("records")}
    curve_cache: dict[tuple[str, str], TruthCurve] = {}
    truth_sources: dict[str, str] = {}
    for fam, oem in sorted(set(zip(fleet["catalogue_family"].astype(str), fleet["oem"].astype(str)))):
        c = truth_curve(curves, fam, oem, truth)
        curve_cache[(fam, oem)] = c
        truth_sources[f"{fam} / {oem}"] = c.source

    cand = work_orders[(work_orders["outcome"] != "scrap")].copy()
    cand["finished_day"] = [_d(v) for v in cand["finished_at"]]
    cand = cand[[f is not None and f <= as_of for f in cand["finished_day"]]]
    cand = cand.sort_values(["finished_day", "serial"], kind="stable").drop_duplicates("serial", keep="first")

    orders: list[dict] = []
    for r in cand.itertuples(index=False):
        serial = str(r.serial)
        d = by_serial[serial]
        grade = str(r.grade_out)
        mix = cfg.channel_mix_by_grade[grade]
        channels = sorted(mix)
        p = np.array([float(mix[c]) for c in channels], dtype=float)
        p = p / p.sum()
        channel = str(channels[int(rng.choice(len(channels), p=p))])
        listed = r.finished_day + timedelta(days=int(rng.integers(0, 4)))
        lo, hi = cfg.days_to_sale[channel]
        if rng.random() < float(cfg.slow_mover_share):
            lo, hi = cfg.slow_mover_days
        sold = listed + timedelta(days=int(rng.integers(int(lo), int(hi) + 1)))
        noise = float(rng.normal(0.0, float(truth.noise_sigma)))
        if sold > as_of:
            continue
        fam, oem = str(d["catalogue_family"]), str(d["oem"])
        age = months_between_float(_d(d["launch_date"]), sold)
        gross = true_price(float(d["rrp_net"]), curve_cache[(fam, oem)], age, grade, channel, cfg.channel_mult, truth, noise)
        orders.append(
            {
                "serial": serial,
                "channel": channel,
                "listed_at": datetime.combine(listed, T_LISTED),
                "sold_at": datetime.combine(sold, T_SOLD),
                "gross_price_eur": gross,
                "buyer_type": BUYER_TYPE_BY_CHANNEL[channel],
                "grade_at_sale": grade,
            }
        )
    orders_df = pd.DataFrame(orders, columns=[c for c in ORDER_COLUMNS if c != "order_id"])
    orders_df = orders_df.sort_values(["sold_at", "serial"], kind="stable").reset_index(drop=True)
    orders_df.insert(0, "order_id", [f"RO-{i + 1:06d}" for i in range(len(orders_df))])

    notes: list[dict] = []
    v01_rows: list[dict] = []
    for r in orders_df.itertuples(index=False):
        channel = str(r.channel)
        gross = float(r.gross_price_eur)
        fee_pct_eur = round(gross * float(cfg.fee_pct[channel]), 2)
        fee_fixed = round(float(cfg.fee_fixed_eur[channel]), 2)
        v01_rows.append(
            {
                "sale_id": str(r.order_id),
                "serial": str(r.serial),
                "channel": channel,
                "sale_date": r.sold_at.date(),
                "price": gross,
                "fees": round(fee_pct_eur + fee_fixed, 2),
                "buyer_type": str(r.buyer_type),
                "grade_at_sale": str(r.grade_at_sale),
            }
        )
        credited = r.sold_at.date() + timedelta(days=int(cfg.days_to_cash[channel]))
        if credited > as_of:
            continue
        notes.append(
            {
                "order_id": str(r.order_id),
                "serial": str(r.serial),
                "channel": channel,
                "credited_at": datetime.combine(credited, T_CREDITED),
                "gross_eur": gross,
                "fee_pct_eur": fee_pct_eur,
                "fee_fixed_eur": fee_fixed,
                "net_eur": round(gross - fee_pct_eur - fee_fixed, 2),
            }
        )
    notes_df = pd.DataFrame(notes, columns=[c for c in CREDIT_NOTE_COLUMNS if c != "credit_note_id"])
    notes_df = notes_df.sort_values(["credited_at", "order_id"], kind="stable").reset_index(drop=True)
    notes_df.insert(0, "credit_note_id", [f"CN-{i + 1:06d}" for i in range(len(notes_df))])

    v01_resale = pd.DataFrame(v01_rows, columns=list(V01_RESALE_COLUMNS))
    frames = {
        "recommerce/orders": orders_df[list(ORDER_COLUMNS)],
        "recommerce/credit_notes": notes_df[list(CREDIT_NOTE_COLUMNS)],
    }
    world.frames.update(frames)
    world.v01["resale"] = v01_resale
    world.truth_sources = truth_sources
    return frames


__all__ = ["build_resale", "ORDER_COLUMNS", "CREDIT_NOTE_COLUMNS", "V01_RESALE_COLUMNS"]
