"""Gold KPI registry (SPEC_v0.2 section 8.3, decision D15).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The v0.1 registry (``restwert.kpi.registry``) is frozen at 20 KPIs in five
areas and pinned by tests. The v0.2 cycle KPIs live here, in a separate
registry keyed by the eight cycle pages, and write ``gold.kpi_values`` and
``gold.kpi_breakdown``. Every spec carries the owner of the page it sits on.

``register_gold`` applies the same validation as v0.1 ``kpi.registry.register``
(no duplicate id, closed page list, closed direction list, no empty text, at
least one source table, ``min_n >= 1``). The shared helpers of the v0.1
registry (``load_table``, ``to_numeric``, ``ratio``, ``make_breakdown``,
``trailing_window``, ``KpiValue``) are reused unchanged and re-exported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

import duckdb

from restwert.config import KpiTargets
from restwert.kpi.registry import (  # noqa: F401  (re-exported helpers)
    BREAKDOWN_COLUMNS,
    DIRECTIONS,
    KpiValue,
    as_bool,
    fmt_window,
    load_table,
    make_breakdown,
    ok,
    ratio,
    ratio_breakdown,
    to_datetime,
    to_numeric,
    trailing_window,
    window_mask,
)

GoldKpiFn = Callable[[duckdb.DuckDBPyConnection, date, KpiTargets], KpiValue]

PAGES: tuple[str, ...] = (
    "0 Data",
    "1 Purchase",
    "2 TCO",
    "3 Residual estimate",
    "4 Resale",
    "5 Result",
    "6 Levers",
    "7 Contracts",
)

# The named human who owns the numbers of each page (placeholder names, as in the config files).
PAGE_OWNERS: dict[str, str] = {
    "0 Data": "Data owner (name)",
    "1 Purchase": "Head of Procurement (name)",
    "2 TCO": "CFO (name)",
    "3 Residual estimate": "Head of Recommerce (name)",
    "4 Resale": "Head of Recommerce (name)",
    "5 Result": "CFO (name)",
    "6 Levers": "CFO (name)",
    "7 Contracts": "Category Manager Hardware (name)",
}


@dataclass(frozen=True)
class GoldKpiSpec:
    """Static description of one gold KPI; the function itself is ``fn``."""

    kpi_id: str
    name: str
    page: str
    definition: str
    formula_text: str
    source_tables: tuple[str, ...]
    unit: str
    direction: Literal["up", "down", "zero", "one"]
    min_n: int
    owner: str
    fn: GoldKpiFn


GOLD_KPI_REGISTRY: dict[str, GoldKpiSpec] = {}
GOLD_KPI_TREE: list[tuple[str, list[str]]] = [(page, []) for page in PAGES]


def register_gold(
    *,
    kpi_id: str,
    name: str,
    page: str,
    definition: str,
    formula_text: str,
    source_tables: tuple[str, ...] | list[str],
    unit: str,
    direction: Literal["up", "down", "zero", "one"],
    min_n: int = 1,
    owner: str | None = None,
) -> Callable[[GoldKpiFn], GoldKpiFn]:
    """Decorator that registers a gold KPI function under ``kpi_id`` on one cycle page.

    ``owner`` defaults to the page owner. Raises ``ValueError`` on a duplicate
    id, an unknown page or direction, an empty descriptive field, no source
    table or ``min_n < 1``, so a half-described KPI never reaches the catalogue.
    """

    if kpi_id in GOLD_KPI_REGISTRY:
        raise ValueError(f"gold KPI id {kpi_id} registered twice")
    if page not in PAGES:
        raise ValueError(f"gold KPI {kpi_id}: page {page!r} is not one of {PAGES}")
    if direction not in DIRECTIONS:
        raise ValueError(f"gold KPI {kpi_id}: direction {direction!r} is not one of {DIRECTIONS}")
    for field_name, text in (("name", name), ("definition", definition), ("formula_text", formula_text), ("unit", unit)):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"gold KPI {kpi_id}: {field_name} must be a non-empty string")
    if not source_tables:
        raise ValueError(f"gold KPI {kpi_id}: source_tables must not be empty")
    if min_n < 1:
        raise ValueError(f"gold KPI {kpi_id}: min_n must be >= 1")
    resolved_owner = (owner or PAGE_OWNERS[page]).strip()
    if not resolved_owner:
        raise ValueError(f"gold KPI {kpi_id}: owner must be non-empty")

    def decorator(fn: GoldKpiFn) -> GoldKpiFn:
        spec = GoldKpiSpec(
            kpi_id=kpi_id,
            name=name,
            page=page,
            definition=definition.strip(),
            formula_text=formula_text.strip(),
            source_tables=tuple(source_tables),
            unit=unit,
            direction=direction,
            min_n=min_n,
            owner=resolved_owner,
            fn=fn,
        )
        GOLD_KPI_REGISTRY[kpi_id] = spec
        for tree_page, ids in GOLD_KPI_TREE:
            if tree_page == page:
                ids.append(kpi_id)
        return fn

    return decorator


def page_order() -> list[str]:
    """Gold KPI ids in page order (0 Data first), then any id not in the tree."""

    ordered = [kpi_id for _, ids in GOLD_KPI_TREE for kpi_id in ids]
    ordered += [kpi_id for kpi_id in GOLD_KPI_REGISTRY if kpi_id not in ordered]
    return ordered


__all__ = [
    "GoldKpiSpec",
    "GoldKpiFn",
    "GOLD_KPI_REGISTRY",
    "GOLD_KPI_TREE",
    "PAGES",
    "PAGE_OWNERS",
    "register_gold",
    "page_order",
    "KpiValue",
    "load_table",
    "to_numeric",
    "to_datetime",
    "as_bool",
    "ratio",
    "ratio_breakdown",
    "make_breakdown",
    "trailing_window",
    "window_mask",
    "fmt_window",
    "ok",
    "BREAKDOWN_COLUMNS",
    "DIRECTIONS",
]
