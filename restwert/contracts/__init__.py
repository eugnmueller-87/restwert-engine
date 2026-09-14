"""Contracts register of the Restwert Engine (SPEC section 7.4).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``register.contracts_register`` unions supplier contracts and rental contracts
into one register with a notice deadline per row; ``register.renewal_calendar``
lists what ends or must be noticed inside the next six months and flags
``action_required``; ``register.contract_coverage`` is the single definition of
"share of spend under contract" that the procurement KPI reuses;
``register.run_contracts`` writes ``contracts_register`` and
``renewal_calendar`` to DuckDB. Nothing here sends a notice or renews anything.
"""

from __future__ import annotations

from restwert.contracts.register import contract_coverage, contracts_register, renewal_calendar, run_contracts

__all__ = ["contracts_register", "renewal_calendar", "contract_coverage", "run_contracts"]
