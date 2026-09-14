"""Residual value forecast for the Restwert Engine (SPEC section 5, module 3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

What this package does
----------------------
* ``features``      target ``ln(rv_ratio)`` and the design matrix; the launch-step
                    leakage guard ``n_launches_since``.
* ``model``         ONE method, ``loglinear_step_v1``: per-family OLS via
                    ``numpy.linalg.lstsq``; pooled fallback below 50 rows; ``none``
                    fallback to the planned residual value ratio.
* ``registry``      immutable ``forecast_runs`` (append only), ``rv_forecast_grid``
                    and ``rv_forecast_current`` rebuilt from the latest run.
* ``backtest``      one time split with leakage assertions, metrics per family.
* ``error_series``  forecast of record (run strictly before the return date) and the
                    monthly "Residual value forecast error" leadership sees.
* ``advisory``      sell-before-launch and calibration advisories. Advisory only:
                    nothing here decides, orders, lists or sends anything.
* ``run``           ``run_forecast`` orchestration including month-end replay.

Every forecast number is a median (``exp(mu)``) on the marketplace baseline, on the
device purchase price. On synthetic data the forecast error measures recovery of a
synthetic curve, not market accuracy.
"""

from restwert.forecast.model import ResidualValueModel, fit, predict_ratio  # noqa: F401
from restwert.forecast.run import run_forecast  # noqa: F401

METHOD_ID = "loglinear_step_v1"

__all__ = ["METHOD_ID", "ResidualValueModel", "fit", "predict_ratio", "run_forecast"]
