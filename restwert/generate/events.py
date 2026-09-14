"""Damage, repair, replacement and return events (SPEC.md section 3.2, ``build_events``).

Per rental contract the number of damages is Poisson with rate
``damage_rate_pa * exposure_days / 365.25`` over the rented interval (start to
``min(effective end, as_of)``). Each damage:

* damage type from {screen .45, battery .20, housing .20, water .05, other .10};
  quote ``U(repair_cost_min, repair_cost_max)`` scaled by type (water 1.6, screen 1.2).
* damages dated within 30 days before ``as_of`` stay OPEN (``resolved = false``,
  ``cost = quote``) with probability ``min(1, open_damage_share * history_days / 30)``,
  which on the shipped config means every damage of the last 30 days is still an
  open quote. These rows feed rule R01.
* otherwise with probability ``repair_share`` a ``repair`` event 3 to 10 days later
  carries the cost; the damage is ``resolved = true, cost = 0``.
* else a ``replacement``: cost 18.00 shipping, the damaged device returns 3 to 12
  days later (grade C or D), its contract is closed with ``status = replaced``, and
  a spare of the same family gets a new contract (same customer, same ``end_date``,
  ``start_date`` = damage date, ``replaces_contract_id`` = old). Replacement
  contracts themselves are only ever repaired, never replaced again (depth 1).
  With no spare left the damage is repaired instead.

Every contract that ended by ``as_of`` and was not replaced produces a ``return``
event 2 to 15 days after its effective end, cost 9.50 reverse logistics,
``grade_pre_return`` from ``grade_pre_return_mix``, ``grade_inspected`` shifted one
grade worse with probability ``grade_drift_worse`` or one better with
``grade_drift_better``, ``wipe_certificate`` true 97 %.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.generate.rentals import CONTRACT_COLUMNS

DAMAGE_TYPE_MIX: tuple[tuple[str, float], ...] = (
    ("screen", 0.45), ("battery", 0.20), ("housing", 0.20), ("water", 0.05), ("other", 0.10),
)
DAMAGE_COST_FACTOR: dict[str, float] = {"water": 1.6, "screen": 1.2}
REPLACEMENT_SHIPPING_EUR: float = 18.00
RETURN_LOGISTICS_EUR: float = 9.50
WIPE_CERTIFICATE_SHARE: float = 0.97
OPEN_DAMAGE_WINDOW_DAYS: int = 30
REPLACED_GRADE_C_SHARE: float = 0.60
GRADE_ORDER = "ABCD"
_TYPE_RANK = {"damage": 0, "repair": 1, "replacement": 2, "return": 3}

EVENT_COLUMNS = [
    "event_id", "serial", "contract_id", "event_type", "event_date", "cost", "damage_type", "resolved",
    "replacement_serial", "return_date", "grade_pre_return", "grade_inspected", "wipe_certificate", "note",
]


def shift_grade(grade: str, delta: int) -> str:
    """Move a grade ``delta`` steps (positive = worse), clipped to A..D."""
    i = GRADE_ORDER.index(grade)
    return GRADE_ORDER[min(max(i + delta, 0), len(GRADE_ORDER) - 1)]


def _event(**kw) -> dict:
    base = {c: None for c in EVENT_COLUMNS}
    base["cost"] = 0.0
    base.update(kw)
    return base


def _draw_grades(cfg: GeneratorConfig, rng: np.random.Generator) -> tuple[str, str]:
    grades = list(cfg.grade_pre_return_mix)
    p = np.array([cfg.grade_pre_return_mix[g] for g in grades], dtype=float)
    p = p / p.sum()
    pre = str(grades[int(rng.choice(len(grades), p=p))])
    u = rng.random()
    if u < cfg.grade_drift_worse:
        inspected = shift_grade(pre, +1)
    elif u < cfg.grade_drift_worse + cfg.grade_drift_better:
        inspected = shift_grade(pre, -1)
    else:
        inspected = pre
    return pre, inspected


def build_events(
    cfg: GeneratorConfig, devices: pd.DataFrame, contracts: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return ``(events, contracts_updated, devices_updated)``."""
    devices = devices.copy()
    fam_of: dict[str, str] = dict(zip(devices["serial"], devices["model_family"]))
    as_of: date = cfg.as_of
    if "_spare" in devices.columns:
        spare_mask = devices["_spare"].astype(bool) & devices["contract_id"].isna()
    else:
        spare_mask = devices["contract_id"].isna()
    spare_pool: dict[str, list[str]] = {}
    for serial, fam in zip(devices.loc[spare_mask, "serial"], devices.loc[spare_mask, "model_family"]):
        spare_pool.setdefault(fam, []).append(serial)
    for fam in spare_pool:
        spare_pool[fam].sort()

    clist: list[dict] = contracts.to_dict("records")
    for c in clist:
        if c.get("actual_end_date") is not None and pd.isna(c.get("actual_end_date")):
            c["actual_end_date"] = None
    new_contracts: list[dict] = []
    next_rc = [len(clist)]
    events: list[dict] = []
    spare_contract_of: dict[str, str] = {}

    history_days = max((as_of - cfg.history_start).days, 1)
    p_open_recent = min(1.0, cfg.open_damage_share * history_days / OPEN_DAMAGE_WINDOW_DAYS)
    recent_from = as_of - timedelta(days=OPEN_DAMAGE_WINDOW_DAYS)
    dmg_types = [t for t, _ in DAMAGE_TYPE_MIX]
    dmg_p = np.array([p for _, p in DAMAGE_TYPE_MIX], dtype=float)

    def process(c: dict, allow_replacement: bool) -> None:
        serial = c["serial"]
        fam = fam_of[serial]
        fc = cfg.families[fam]
        start: date = c["start_date"]
        eff_end: date = min(c["actual_end_date"] or c["end_date"], as_of)
        exposure = (eff_end - start).days
        if exposure <= 0:
            return
        n_dmg = int(rng.poisson(fc.damage_rate_pa * exposure / 365.25))
        if n_dmg == 0:
            return
        offsets = np.sort(rng.integers(0, exposure, n_dmg))
        for off in offsets:
            d = start + timedelta(days=int(off))
            dtype = str(dmg_types[int(rng.choice(len(dmg_types), p=dmg_p))])
            quote = round(float(rng.uniform(fc.repair_cost_min, fc.repair_cost_max)) * DAMAGE_COST_FACTOR.get(dtype, 1.0), 2)
            common = {"serial": serial, "contract_id": c["contract_id"]}
            if d >= recent_from and rng.random() < p_open_recent:
                events.append(_event(event_type="damage", event_date=d, cost=quote, damage_type=dtype, resolved=False,
                                     note="open quote", **common))
                continue
            do_repair = rng.random() < fc.repair_share
            spare: str | None = None
            if not do_repair and allow_replacement:
                pool = spare_pool.get(fam)
                if pool:
                    spare = pool.pop(0)
            if spare is None:
                rdate = d + timedelta(days=int(rng.integers(3, 11)))
                if rdate > as_of:
                    events.append(_event(event_type="damage", event_date=d, cost=quote, damage_type=dtype, resolved=False,
                                         note="open quote", **common))
                    continue
                events.append(_event(event_type="damage", event_date=d, cost=0.0, damage_type=dtype, resolved=True, **common))
                events.append(_event(event_type="repair", event_date=rdate, cost=quote, damage_type=dtype, **common))
                continue
            return_date = d + timedelta(days=int(rng.integers(3, 13)))
            if return_date > as_of:
                spare_pool[fam].insert(0, spare)
                events.append(_event(event_type="damage", event_date=d, cost=quote, damage_type=dtype, resolved=False,
                                     note="open quote", **common))
                continue
            grade = "C" if rng.random() < REPLACED_GRADE_C_SHARE else "D"
            wipe = bool(rng.random() < WIPE_CERTIFICATE_SHARE)
            events.append(_event(event_type="damage", event_date=d, cost=0.0, damage_type=dtype, resolved=True, **common))
            events.append(_event(event_type="replacement", event_date=d, cost=REPLACEMENT_SHIPPING_EUR, damage_type=dtype,
                                 replacement_serial=spare, **common))
            events.append(_event(event_type="return", event_date=return_date, cost=RETURN_LOGISTICS_EUR,
                                 return_date=return_date, grade_pre_return=grade, grade_inspected=grade,
                                 wipe_certificate=wipe, note="returned after replacement", **common))
            c["actual_end_date"] = return_date
            c["status"] = "replaced"
            next_rc[0] += 1
            new_id = f"RC-{next_rc[0]:06d}"
            new_contracts.append(
                {
                    "contract_id": new_id,
                    "serial": spare,
                    "customer_id": c["customer_id"],
                    "start_date": d,
                    "term_months": int(c["term_months"]),
                    "monthly_rate": float(c["monthly_rate"]),
                    "end_date": c["end_date"],
                    "actual_end_date": None,
                    "status": "active" if c["end_date"] > as_of else "ended",
                    "replaces_contract_id": c["contract_id"],
                }
            )
            spare_contract_of[spare] = new_id
            return

    for c in clist:
        process(c, allow_replacement=True)
    for c in list(new_contracts):
        process(c, allow_replacement=False)

    all_contracts = clist + new_contracts
    for c in all_contracts:
        if c["status"] == "replaced":
            continue
        eff = c["actual_end_date"] or c["end_date"]
        if eff > as_of:
            continue
        rdate = eff + timedelta(days=int(rng.integers(2, 16)))
        pre, inspected = _draw_grades(cfg, rng)
        wipe = bool(rng.random() < WIPE_CERTIFICATE_SHARE)
        events.append(_event(serial=c["serial"], contract_id=c["contract_id"], event_type="return", event_date=rdate,
                             cost=RETURN_LOGISTICS_EUR, return_date=rdate, grade_pre_return=pre,
                             grade_inspected=inspected, wipe_certificate=wipe, note="contract end return"))

    ev = pd.DataFrame(events, columns=EVENT_COLUMNS)
    if len(ev):
        ev["_rank"] = ev["event_type"].map(_TYPE_RANK)
        ev = ev.sort_values(["event_date", "serial", "_rank"], kind="stable").drop(columns="_rank").reset_index(drop=True)
        ev["event_id"] = [f"EV-{i + 1:06d}" for i in range(len(ev))]
    contracts_out = pd.DataFrame(all_contracts, columns=CONTRACT_COLUMNS)
    contracts_out = contracts_out.sort_values("contract_id").reset_index(drop=True)
    if spare_contract_of:
        devices["contract_id"] = [
            spare_contract_of.get(s, cid) for s, cid in zip(devices["serial"], devices["contract_id"])
        ]
    return ev[EVENT_COLUMNS], contracts_out, devices


__all__ = ["build_events", "EVENT_COLUMNS", "shift_grade", "REPLACEMENT_SHIPPING_EUR", "RETURN_LOGISTICS_EUR"]
