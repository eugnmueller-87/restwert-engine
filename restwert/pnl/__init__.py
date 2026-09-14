"""Restwert Engine, module 2: lifecycle P&L, book value, TCO and aggregation.

Implements spec section 4 (4.1 lifecycle, 4.2 book value, 4.3 TCO, 4.4 aggregation,
4.5 assumptions). The only cross-module inputs are DuckDB tables: ``rv_forecast_current``
and ``rv_forecast_grid`` (module 3) and ``write_down_ledger`` (module 4). Nothing in this
package sends, orders, lists or emails anything.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every forward-looking number the P&L uses comes from ``config/assumptions.yaml`` where
each block names its owner. The forecast residual value is an advisory input to the
open-device margin; the realised margin of a closed device uses realised numbers only.
"""

from restwert.pnl.aggregate import AGGREGATE_DIMENSIONS, PNL_AGGREGATE_COLUMNS, aggregate_pnl
from restwert.pnl.book_value import planned_rv, straight_line_book_value
from restwert.pnl.lifecycle import (
    DEVICE_PNL_COLUMNS,
    build_device_pnl,
    derive_status,
    lifecycle_margin,
    months_billed,
    run_pnl,
)
from restwert.pnl.tco import TCO_COLUMNS, tco_per_model

__all__ = [
    "AGGREGATE_DIMENSIONS",
    "DEVICE_PNL_COLUMNS",
    "PNL_AGGREGATE_COLUMNS",
    "TCO_COLUMNS",
    "aggregate_pnl",
    "build_device_pnl",
    "derive_status",
    "lifecycle_margin",
    "months_billed",
    "planned_rv",
    "run_pnl",
    "straight_line_book_value",
    "tco_per_model",
]
