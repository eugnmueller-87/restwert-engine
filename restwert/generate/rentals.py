"""Rental contracts (SPEC.md section 3.2, ``build_rental_contracts``).

Per non-spare device: ``start_date = purchase_date + U(3, 30)`` days, term drawn
from the family's ``term_mix`` (12, 24, 36 or 48 months, one ``rng.choice`` over the
ascending terms with their shares), ``monthly_rate = round(landed_cost
* monthly_rate_pct_of_landed * term_rate_factor[term], 2)`` (the factor per term,
  24 months = 1.0, from the config), customer from 120 customers with a Zipf-like
weight, ``end_date = add_months(start_date, term)``. A share
``early_termination_rate`` terminates early between month 6 and the planned end
(only terminations that already happened at ``as_of`` are recorded).

Status: ``terminated_early`` when ``actual_end_date`` is set, ``active`` when the
effective end is after ``as_of``, else ``ended``. Replacement contracts are added
later by the events builder.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.dates import add_months

N_CUSTOMERS: int = 120
CUSTOMER_ZIPF_EXPONENT: float = 0.8
EARLY_TERMINATION_MIN_MONTH: int = 6

CONTRACT_COLUMNS = [
    "contract_id", "serial", "customer_id", "start_date", "term_months", "monthly_rate",
    "end_date", "actual_end_date", "status", "replaces_contract_id",
]


def customer_weights(n: int = N_CUSTOMERS, exponent: float = CUSTOMER_ZIPF_EXPONENT) -> np.ndarray:
    w = 1.0 / np.arange(1, n + 1, dtype=float) ** exponent
    return w / w.sum()


def build_rental_contracts(cfg: GeneratorConfig, devices: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """One initial contract per non-spare device, ids ``RC-000001`` in start-date order."""
    if "_spare" in devices.columns:
        pool = devices[~devices["_spare"].astype(bool)]
    else:
        pool = devices
    weights = customer_weights()
    # per family: ascending terms and their probabilities (renormalised so numpy accepts them)
    term_draw: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name, fc in cfg.families.items():
        terms, probs = fc.term_draw()
        term_draw[name] = (np.array(terms, dtype=int), np.array(probs, dtype=float))
    rows: list[dict] = []
    for _, d in pool.iterrows():
        fc = cfg.families[d["model_family"]]
        start = d["purchase_date"] + timedelta(days=int(rng.integers(3, 31)))
        terms, probs = term_draw[d["model_family"]]
        term = int(rng.choice(terms, p=probs))
        rate = round(float(d["landed_cost"]) * fc.monthly_rate_pct_of_landed * float(cfg.term_rate_factor[term]), 2)
        customer = int(rng.choice(N_CUSTOMERS, p=weights)) + 1
        end = add_months(start, term)
        actual_end = None
        if rng.random() < cfg.early_termination_rate:
            earliest = add_months(start, EARLY_TERMINATION_MIN_MONTH)
            span = (end - earliest).days
            if span > 0:
                candidate = earliest + timedelta(days=int(rng.integers(0, span)))
                if candidate <= cfg.as_of and candidate < end:
                    actual_end = candidate
        if actual_end is not None:
            status = "terminated_early"
        elif end > cfg.as_of:
            status = "active"
        else:
            status = "ended"
        rows.append(
            {
                "serial": d["serial"],
                "customer_id": f"CUST-{customer:04d}",
                "start_date": start,
                "term_months": term,
                "monthly_rate": rate,
                "end_date": end,
                "actual_end_date": actual_end,
                "status": status,
                "replaces_contract_id": None,
            }
        )
    contracts = pd.DataFrame(rows)
    contracts = contracts.sort_values(["start_date", "serial"], kind="stable").reset_index(drop=True)
    contracts.insert(0, "contract_id", [f"RC-{i + 1:06d}" for i in range(len(contracts))])
    return contracts[CONTRACT_COLUMNS]


__all__ = ["build_rental_contracts", "CONTRACT_COLUMNS", "N_CUSTOMERS", "customer_weights"]
