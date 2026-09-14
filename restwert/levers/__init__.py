"""Levers: where to tighten, in EUR per device, against a named reference (spec v0.2 section 7).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

A lever is actual minus a named reference on one ledger component. The references are
rows of the fleet anyone can list (a percentile or a median of the provider's own devices),
the forecast of record, or the forecast grid. Never an external benchmark. Below
``lever_reference_min_n`` (assumptions.yaml) a lever is not attributed, never guessed.
Levers do not add up; the ``additive`` flag says which ones sit on disjoint components.

Modules:

* ``references``   fleet references (p75 discount, family realisation medians, term medians),
                   the grid lookup and the R02 channel filter
* ``attribution``  the seven levers L01..L07, ``attribute_all`` and ``check_additivity``
* ``summary``      ``levers_by_cohort``, ``levers_summary`` (the where-to-tighten table) and
                   the renderer of ``docs/LEVERS.md``
* ``run``          ``run_levers``: silver in, gold out, plus the ADV03 / ADV04 advisories

Nothing here orders, lists, mails or writes to a partner.
"""

from __future__ import annotations

from restwert.levers.attribution import LEVERS, LeverResult, LeverSpec, attribute_all, check_additivity
from restwert.levers.run import run_levers
from restwert.levers.summary import levers_by_cohort, levers_summary, render_levers_md

__all__ = [
    "LEVERS",
    "LeverSpec",
    "LeverResult",
    "attribute_all",
    "check_additivity",
    "levers_by_cohort",
    "levers_summary",
    "render_levers_md",
    "run_levers",
]
