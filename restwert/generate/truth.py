"""Hidden ground-truth residual value curve (SPEC.md section 3.1, ``true_rv_ratio``).

Used ONLY by the synthetic generator and by tests. The forecaster never sees
this function; it has to recover the curve from realised resale prices.

The curve is deliberately NOT the fitted log-linear form, so that the fit has
structured error to report:

* piecewise slope: ``lambda`` up to 24 months since launch, ``lambda_after_24`` after
* one multiplicative ``step`` per launch of a newer generation
* grade multipliers, channel multipliers, a storage power term
* grade D additionally loses ``0.4 %`` per month (a grade x age interaction)

Every parameter is a synthetic design parameter from ``config/generator.yaml``.
No market benchmark is quoted anywhere.
"""

from __future__ import annotations

import math

from restwert.config import GeneratorConfig

RATIO_MIN = 0.02
RATIO_MAX = 1.10


def true_rv_ratio(
    family: str,
    months_since_launch: float,
    n_launches_since: int,
    grade: str,
    storage_gb: int,
    channel: str,
    cfg: GeneratorConfig,
    noise: float = 0.0,
) -> float:
    """Ground-truth ratio ``resale price / purchase price``.

    ``decay = exp(-lambda * min(m, 24) - lambda_after_24 * max(m - 24, 0))``
    ``ratio = base * decay * step ** n_launches * grade_mult * channel_mult
              * (storage / base_storage) ** storage_exp * exp(noise)``
    grade D additionally ``* (1 - 0.004 * m)``; result clipped to [0.02, 1.10].
    """
    fc = cfg.families[family]
    t = fc.truth
    m = max(float(months_since_launch), 0.0)
    decay = math.exp(-t["lambda"] * min(m, 24.0) - t["lambda_after_24"] * max(m - 24.0, 0.0))
    grade_mult = t[f"grade_{grade}"]
    channel_mult = cfg.channel_mult[channel]
    storage_term = (float(storage_gb) / float(fc.base_storage_gb)) ** t["storage_exp"]
    ratio = (
        t["base"]
        * decay
        * (t["step"] ** max(int(n_launches_since), 0))
        * grade_mult
        * channel_mult
        * storage_term
        * math.exp(noise)
    )
    if grade == "D":
        ratio *= max(1.0 - 0.004 * m, 0.0)
    return float(min(max(ratio, RATIO_MIN), RATIO_MAX))


__all__ = ["true_rv_ratio", "RATIO_MIN", "RATIO_MAX"]
