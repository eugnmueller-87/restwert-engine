"""Gold KPIs of the v0.2 device cycle (SPEC_v0.2 section 8.3, decision D15).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

What lives here
---------------
- ``registry``: ``GoldKpiSpec``, the ``register_gold`` decorator,
  ``GOLD_KPI_REGISTRY`` (exactly 14 ids) and ``PAGES`` (the eight cycle
  pages, 0 Data to 7 Contracts), each page with a named owner. The v0.1
  helpers of ``restwert.kpi.registry`` are reused and re-exported.
- ``kpis``: one function per gold KPI, each reading DuckDB tables only and
  returning a ``records.KpiValue``.
- ``run``: ``compute_one_gold``, ``compute_all_gold`` and ``run_gold_kpis``
  (writes ``gold.kpi_values`` and ``gold.kpi_breakdown``).
- ``catalogue``: ``render_gold_catalogue`` and ``write_gold_catalogue`` which
  generate ``docs/GOLD_KPIS.md`` from the registry. Never hand-edited.

The v0.1 KPI registry stays frozen at 20 KPIs in five areas; nothing here
registers into it. Importing this package imports the KPI module so that
``GOLD_KPI_REGISTRY`` is complete after ``import restwert.gold``.
"""

from __future__ import annotations

from restwert.gold import registry as registry  # noqa: F401  (order matters: registry first)
from restwert.gold import kpis as kpis  # noqa: F401
from restwert.gold.registry import GOLD_KPI_REGISTRY, GOLD_KPI_TREE, PAGE_OWNERS, PAGES, GoldKpiSpec, page_order, register_gold
from restwert.gold.run import compute_all_gold, compute_one_gold, run_gold_kpis
from restwert.gold.catalogue import render_gold_catalogue, write_gold_catalogue

__all__ = [
    "GOLD_KPI_REGISTRY",
    "GOLD_KPI_TREE",
    "PAGES",
    "PAGE_OWNERS",
    "GoldKpiSpec",
    "register_gold",
    "page_order",
    "compute_all_gold",
    "compute_one_gold",
    "run_gold_kpis",
    "render_gold_catalogue",
    "write_gold_catalogue",
]
