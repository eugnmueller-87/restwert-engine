"""Model catalogue from the launch calendar (SPEC.md section 3.1, ``build_catalogue``).

Generations run from ``first_launch`` by ``launch_cadence_months`` up to
``cfg.as_of``. Model ids are ``P-Gen01`` (iphone_like), ``A-Gen01``
(android_like), ``L-Gen01`` (laptop_like). List prices are drawn uniformly in
the family's band and rounded to end in 9. All synthetic.

Launch slip: when ``cfg.launch_slip_months_max > 0`` every generation after the
first launches a seeded integer number of months early or late (uniform in
``[-max, +max]``). The forecaster only knows the calendar rule for launches
after its ``as_of`` (``restwert.dates``), so a slipped launch is exactly the
launch-timing risk a real fleet carries; without slip the calendar assumption
is always right on synthetic data and the backtest cannot show that risk.
The slip never moves a launch before the previous generation's launch.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.dates import add_months

PREFIX: dict[str, str] = {"iphone_like": "P", "android_like": "A", "laptop_like": "L"}

CATALOGUE_COLUMNS = ["model", "model_family", "generation", "launch_date", "list_price", "base_storage_gb"]


def _round_to_9(x: float) -> float:
    return float(math.floor(x / 10.0) * 10 + 9)


def build_catalogue(cfg: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """One row per (family, generation) launched on or before ``cfg.as_of``."""
    rows: list[dict] = []
    slip_max = int(getattr(cfg, "launch_slip_months_max", 0) or 0)
    for fam, fc in cfg.families.items():
        prefix = PREFIX.get(fam, fam[:1].upper())
        gen = 1
        d = fc.first_launch
        prev: date | None = None
        while d <= cfg.as_of:
            lp = _round_to_9(float(rng.uniform(fc.list_price_min, fc.list_price_max)))
            lp = min(max(lp, fc.list_price_min), fc.list_price_max)
            rows.append(
                {
                    "model": f"{prefix}-Gen{gen:02d}",
                    "model_family": fam,
                    "generation": gen,
                    "launch_date": d,
                    "list_price": round(lp, 2),
                    "base_storage_gb": int(fc.base_storage_gb),
                }
            )
            if fc.launch_cadence_months is None:
                break
            prev = d
            gen += 1
            d = add_months(fc.first_launch, (gen - 1) * fc.launch_cadence_months)
            if slip_max > 0:
                slip = int(rng.integers(-slip_max, slip_max + 1))
                slipped = add_months(d, slip)
                if prev is None or slipped > prev:
                    d = slipped
    df = pd.DataFrame(rows, columns=CATALOGUE_COLUMNS)
    return df.sort_values(["model_family", "generation"]).reset_index(drop=True)


__all__ = ["build_catalogue", "PREFIX", "CATALOGUE_COLUMNS"]
