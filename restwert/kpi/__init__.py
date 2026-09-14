"""KPI package of the Restwert Engine (SPEC section 7.1 to 7.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

What lives here
---------------
- ``registry``: ``KpiSpec``, the ``register`` decorator, ``KPI_REGISTRY`` (the 18
  KPI ids of SPEC 7.2) and ``KPI_TREE`` (five areas, Top first), plus small
  shared helpers for reading DuckDB tables defensively.
- ``top``, ``procurement``, ``inventory``, ``recommerce``, ``indirect``: one
  function per KPI, each reading only DuckDB tables and returning a
  ``records.KpiValue``.
- ``compute``: ``compute_one``, ``compute_all`` (kpi_values + kpi_breakdown
  frames) and ``run_kpis`` (writes both tables and the catalogue).
- ``catalogue``: ``render_catalogue`` and ``write_catalogue`` which generate
  ``docs/KPI_CATALOGUE.md`` from the registry. The markdown is never hand-edited.

Honesty rule enforced by every KPI function: a KPI whose denominator is 0 or
whose required column is missing or entirely NULL returns
``status='not_measurable'`` and ``value=None``, never 0.

Importing this package imports every KPI module so that ``KPI_REGISTRY`` is
complete after ``import restwert.kpi``.
"""

from __future__ import annotations

from restwert.kpi import registry as registry  # noqa: F401  (order matters: registry first)
from restwert.kpi import top as top  # noqa: F401
from restwert.kpi import procurement as procurement  # noqa: F401
from restwert.kpi import inventory as inventory  # noqa: F401
from restwert.kpi import recommerce as recommerce  # noqa: F401
from restwert.kpi import indirect as indirect  # noqa: F401
from restwert.kpi.registry import KPI_REGISTRY, KPI_TREE, KpiSpec, register, trailing_window
from restwert.kpi.compute import compute_all, compute_one, run_kpis
from restwert.kpi.catalogue import render_catalogue, write_catalogue

__all__ = [
    "KPI_REGISTRY",
    "KPI_TREE",
    "KpiSpec",
    "register",
    "trailing_window",
    "compute_all",
    "compute_one",
    "run_kpis",
    "render_catalogue",
    "write_catalogue",
]
