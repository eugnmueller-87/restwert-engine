"""Dashboard pages (SPEC 8.3, SPEC_v0.2 9.3). Each module exposes ``render(con, as_of)``.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``TABS`` is the v0.1 tab list, kept verbatim. v0.2 navigates two groups:
``CYCLE_PAGES`` follows the device cycle (Realisation, then 0 Data to
7 Contracts) and ``ENGINE_PAGES`` keeps the v0.1 tabs reachable.
"""

from restwert.dashboard.views import contracts, curves, cycle, decisions, export, inventory, market, overview

TABS: list[tuple[str, object]] = [
    ("Realisation", market),
    ("Overview", overview),
    ("Residual value curves", curves),
    ("Inventory", inventory),
    ("Decision queue", decisions),
    ("Contracts", contracts),
    ("Export", export),
]

CYCLE_PAGES: list[tuple[str, str, object]] = [  # (title, url_path, module)
    ("Realisation", "realisation", market),
    ("0 Data", "data", cycle.data_page),
    ("1 Purchase", "purchase", cycle.purchase),
    ("2 TCO", "tco", cycle.tco),
    ("3 Residual estimate", "residual", cycle.residual),
    ("4 Resale", "resale", cycle.resale),
    ("5 Result", "result", cycle.result),
    ("6 Levers", "levers", cycle.levers),
    ("7 Contracts", "contracts-v2", cycle.contracts_v2),
]

ENGINE_PAGES: list[tuple[str, str, object]] = [
    ("Overview", "overview", overview),
    ("Residual value curves", "curves", curves),
    ("Inventory", "inventory", inventory),
    ("Decision queue", "decisions", decisions),
    ("Contracts (v0.1)", "contracts", contracts),
    ("Export", "export", export),
]

__all__ = [
    "TABS",
    "CYCLE_PAGES",
    "ENGINE_PAGES",
    "market",
    "overview",
    "curves",
    "inventory",
    "decisions",
    "contracts",
    "export",
    "cycle",
]
