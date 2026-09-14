"""The Cycle pages of the v0.2 dashboard (SPEC_v0.2 9.3): one question per page.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

0 Data -> 1 Purchase -> 2 TCO -> 3 Residual estimate -> 4 Resale -> 5 Result ->
6 Levers -> 7 Contracts. Each module exposes ``render(con, as_of)`` and a
``QUESTION``; ``_common`` holds the shared helpers.
"""

from restwert.dashboard.views.cycle import (
    contracts_v2,
    data_page,
    levers,
    purchase,
    resale,
    residual,
    result,
    tco,
)

__all__ = ["data_page", "purchase", "tco", "residual", "resale", "result", "levers", "contracts_v2"]
