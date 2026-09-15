"""Device ledger of the Restwert Engine v0.2 (SPEC_v0.2 section 6).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The ledger is the closed device cycle in money: every EUR that a serial earns or
costs is one row of ``silver.ledger_lines`` with a signed amount and a source
reference to one bronze row. The wide ``silver.device_ledger`` folds those lines
into one row per serial and adds the estimates for open cycles;
``silver.reconciliation`` proves that the ledger and the v0.1 ``device_pnl``
agree to the cent; ``cohorts`` rolls the result up into the gold tables.

- ``lines``: the 17 line types and the pure builder of ``silver.ledger_lines``.
- ``result``: pure scalar formulas (closed result, the two open numbers, the
  expected remaining cost, the public anchor).
- ``device_ledger``: one row per serial.
- ``reconcile``: the cent-exact bridge to v0.1 ``device_pnl``.
- ``cohorts``: the five gold tables of the ledger.
- ``run``: ``run_ledger``, the only function that touches DuckDB.

TCO is a sum of lines; the only rate is holding cost and every holding line
says so (``is_estimate = true``, ``assumption_key = holding_cost_per_day_eur``).
Nothing here orders, lists, emails or writes to a partner.
"""

from __future__ import annotations

from restwert.ledger.lines import (
    ESTIMATE_LINE_TYPES,
    LANDED_LINE_TYPES,
    LEDGER_ORDER,
    LINE_TYPES,
    V01_BRIDGE_LINE_TYPES,
    BronzeFrames,
    build_ledger_lines,
    holding_cost_lines,
    lines_of,
    read_bronze_frames,
)
from restwert.ledger.run import run_ledger

__all__ = [
    "LINE_TYPES",
    "LEDGER_ORDER",
    "ESTIMATE_LINE_TYPES",
    "V01_BRIDGE_LINE_TYPES",
    "LANDED_LINE_TYPES",
    "BronzeFrames",
    "read_bronze_frames",
    "build_ledger_lines",
    "holding_cost_lines",
    "lines_of",
    "run_ledger",
]
