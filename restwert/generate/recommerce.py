"""Refurbishment and resale (SPEC.md section 3.2, ``build_refurbishment`` / ``build_resale``).

Refurbishment: for every return with ``return_date <= as_of``: ``start_date =
return_date + U(1, 5)``, days from ``refurb_days[grade_inspected]``, cost from
``refurb_cost[grade]``, ``grade_out = grade_inspected`` improved one grade with
probability 0.10 (B..D only), outcome ``sellable`` for A..C, for D ``scrap`` with
``scrap_share_of_d`` else ``as_is``. Rows with ``end_date > as_of`` are written
(device is WIP at ``as_of``). At most one row per serial.

Resale: for every refurbishment with ``outcome != scrap`` and ``end_date <= as_of``:
channel from ``channel_mix_by_grade[grade_out]``, ``sale_date = end_date +
U(days_to_sale[channel])``, written only if ``sale_date <= as_of`` (else in stock).
A share ``slow_mover_share`` (design parameter, default 4 %) draws its days to sale
from ``slow_mover_days`` (90 to 400) instead, so that the synthetic fleet has aging
stock for the aging KPIs and the write-down rule R03.
``price = round(purchase_price * true_rv_ratio(...noise=N(0, truth.noise)), 2)``,
``fees = round(price * fee_pct + fee_fixed_eur, 2)``, buyer type by channel.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.dates import months_between_float
from restwert.generate.events import shift_grade
from restwert.generate.truth import true_rv_ratio

GRADE_IMPROVE_SHARE: float = 0.10
BUYER_TYPE_BY_CHANNEL: dict[str, str] = {
    "employee_buyout": "employee",
    "marketplace": "consumer",
    "b2b_wholesale": "trader",
    "as_is": "recycler",
}

REFURB_COLUMNS = ["refurb_id", "serial", "start_date", "end_date", "days", "cost", "grade_out", "outcome"]
RESALE_COLUMNS = ["sale_id", "serial", "channel", "sale_date", "price", "fees", "buyer_type", "grade_at_sale"]


def build_refurbishment(
    cfg: GeneratorConfig, events: pd.DataFrame, devices: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """One refurbishment per returned device (``return_date <= as_of``)."""
    returns = events[(events["event_type"] == "return")].copy()
    returns = returns[returns["return_date"].notna()]
    returns = returns[returns["return_date"] <= cfg.as_of]
    returns = returns.sort_values(["return_date", "serial"], kind="stable").drop_duplicates("serial", keep="first")
    rows: list[dict] = []
    for _, r in returns.iterrows():
        grade_in = str(r["grade_inspected"] or r["grade_pre_return"] or "B")
        start = r["return_date"] + timedelta(days=int(rng.integers(1, 6)))
        lo, hi = cfg.refurb_days[grade_in]
        days = int(rng.integers(int(lo), int(hi) + 1))
        clo, chi = cfg.refurb_cost[grade_in]
        cost = round(float(rng.uniform(clo, chi)), 2)
        grade_out = grade_in
        if grade_in != "A" and rng.random() < GRADE_IMPROVE_SHARE:
            grade_out = shift_grade(grade_in, -1)
        if grade_out == "D":
            outcome = "scrap" if rng.random() < cfg.scrap_share_of_d else "as_is"
        else:
            outcome = "sellable"
        rows.append(
            {
                "serial": r["serial"],
                "start_date": start,
                "end_date": start + timedelta(days=days),
                "days": days,
                "cost": cost,
                "grade_out": grade_out,
                "outcome": outcome,
            }
        )
    refurb = pd.DataFrame(rows, columns=[c for c in REFURB_COLUMNS if c != "refurb_id"])
    refurb = refurb.sort_values(["start_date", "serial"], kind="stable").reset_index(drop=True)
    refurb.insert(0, "refurb_id", [f"RF-{i + 1:06d}" for i in range(len(refurb))])
    return refurb[REFURB_COLUMNS]


def _launches_since(launches: list[date], launch_date: date, until: date) -> int:
    """Count launches ``l`` with ``launch_date < l <= until``."""
    return bisect.bisect_right(launches, until) - bisect.bisect_right(launches, launch_date)


def build_resale(
    cfg: GeneratorConfig,
    devices: pd.DataFrame,
    refurb: pd.DataFrame,
    events: pd.DataFrame,
    catalogue: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """At most one sale per serial, priced on the hidden ground-truth curve."""
    dev = devices.set_index("serial")
    launches_by_family: dict[str, list[date]] = {
        fam: sorted(list(grp["launch_date"])) for fam, grp in catalogue.groupby("model_family")
    }
    cand = refurb[(refurb["outcome"] != "scrap") & (refurb["end_date"] <= cfg.as_of)]
    cand = cand.sort_values(["end_date", "serial"], kind="stable").drop_duplicates("serial", keep="first")
    rows: list[dict] = []
    for _, r in cand.iterrows():
        serial = r["serial"]
        d = dev.loc[serial]
        fam = str(d["model_family"])
        grade = str(r["grade_out"])
        mix = cfg.channel_mix_by_grade[grade]
        channels = list(mix)
        p = np.array([mix[c] for c in channels], dtype=float)
        p = p / p.sum()
        channel = str(channels[int(rng.choice(len(channels), p=p))])
        lo, hi = cfg.days_to_sale[channel]
        if rng.random() < cfg.slow_mover_share:
            lo, hi = cfg.slow_mover_days
        sale_date = r["end_date"] + timedelta(days=int(rng.integers(int(lo), int(hi) + 1)))
        noise = float(rng.normal(0.0, cfg.families[fam].truth["noise"]))
        if sale_date > cfg.as_of:
            continue
        msl = months_between_float(d["launch_date"], sale_date)
        n_launches = _launches_since(launches_by_family.get(fam, []), d["launch_date"], sale_date)
        ratio = true_rv_ratio(fam, msl, n_launches, grade, int(d["storage_gb"]), channel, cfg, noise=noise)
        price = round(float(d["purchase_price"]) * ratio, 2)
        fees = round(price * cfg.fee_pct[channel] + cfg.fee_fixed_eur[channel], 2)
        rows.append(
            {
                "serial": serial,
                "channel": channel,
                "sale_date": sale_date,
                "price": price,
                "fees": fees,
                "buyer_type": BUYER_TYPE_BY_CHANNEL[channel],
                "grade_at_sale": grade,
            }
        )
    resale = pd.DataFrame(rows, columns=[c for c in RESALE_COLUMNS if c != "sale_id"])
    resale = resale.sort_values(["sale_date", "serial"], kind="stable").reset_index(drop=True)
    resale.insert(0, "sale_id", [f"S-{i + 1:06d}" for i in range(len(resale))])
    return resale[RESALE_COLUMNS]


__all__ = ["build_refurbishment", "build_resale", "REFURB_COLUMNS", "RESALE_COLUMNS", "BUYER_TYPE_BY_CHANNEL"]
