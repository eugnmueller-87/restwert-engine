"""``run_forecast`` orchestration including month-end replay (SPEC 5.7).

Steps
-----
1. Replay: for every month end from the first month end with at least ``min_train``
   non-As-Is sales up to ``as_of`` (plus ``as_of`` itself when it is not a month end),
   fit on what was sold by then and append an immutable run. Existing runs are skipped,
   so a rerun never rewrites history.
2. The latest run at ``as_of`` rebuilds ``rv_forecast_grid`` (0 to 120 months since launch,
   ``registry.GRID_MONTHS``: the ledger's measured maximum of 110 plus 6 months reserve, rounded
   up to a full year; the derivation sits on the constant) and ``rv_forecast_current``.
3. ``rv_forecast_of_record`` and ``rv_forecast_error_monthly`` are rebuilt from the
   immutable runs (deterministic; identical rows on rerun).
4. One time-split backtest at ``month_end(add_months(as_of, -6))`` is appended to
   ``backtest_result``.
5. ``advisories`` = sell-before-launch + calibration (replace).

Nothing here has a side effect outside the DuckDB file.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

import pandas as pd

from restwert import db
from restwert.dates import add_months, month_end, month_ends
from restwert.forecast.advisory import ADVISORY_COLUMNS, calibration_advisory, sell_before_launch
from restwert.forecast.backtest import results_to_frame, time_split_backtest
from restwert.forecast.error_series import build_error_series, forecast_of_record_frame
from restwert.forecast.features import as_date, as_is_frame, build_training_frame, to_datetime_series
from restwert.forecast.model import fit
from restwert.forecast.registry import (
    GRID_MONTHS,
    build_current,
    build_grid,
    latest_run,
    load_all_runs,
    make_run_id,
    run_exists,
    save_run,
)
from restwert.records import RunSummary

MIN_N_PER_FAMILY = 50


def _read(con, table: str) -> pd.DataFrame:
    if not db.table_exists(con, table):
        return pd.DataFrame()
    return db.read_df(con, f"SELECT * FROM {table}")


def replay_month_ends(resale: pd.DataFrame, as_of: date, min_train: int) -> list[date]:
    """Month ends to replay: first month end with >= ``min_train`` non-As-Is sales .. ``as_of``,
    plus ``as_of`` itself when it is not a month end. Empty when no month end qualifies."""
    as_of = as_date(as_of)
    if resale is None or len(resale) == 0:
        return [as_of]
    sales = resale[resale["channel"] != "as_is"].copy()
    sales["sale_date"] = to_datetime_series(sales["sale_date"])
    sales = sales[sales["sale_date"] <= pd.Timestamp(as_of)].sort_values("sale_date")
    if len(sales) < min_train:
        return [as_of]
    first_qualifying_sale = sales["sale_date"].iloc[min_train - 1].date()
    first_me = month_end(first_qualifying_sale)
    out = [me for me in month_ends(first_me, as_of) if me <= as_of]
    if not out or out[-1] != as_of:
        out.append(as_of)
    return out


def replay_runs(con, resale, devices, catalogue, events, a, families_cfg, as_of: date, min_train: int) -> int:
    """Fit and save one run per replay date that does not exist yet. Returns runs added."""
    added = 0
    for me in replay_month_ends(resale, as_of, min_train):
        run_id = make_run_id(me)
        if run_exists(con, run_id):
            continue
        train = build_training_frame(resale, devices, catalogue, events, me, families_cfg)
        rvm = fit(train, as_is_frame(resale, devices, me), me, run_id, a, MIN_N_PER_FAMILY)
        save_run(con, rvm)
        added += 1
    return added


def _non_monotone_grade_cells(grid: pd.DataFrame) -> tuple[int, list[str]]:
    """Count (model, month) cells whose ratios are not non-increasing from grade A to D, with the families hit."""
    need = {"model", "grade", "months_since_launch", "forecast_rv_ratio"}
    if grid is None or len(grid) == 0 or not need.issubset(grid.columns):
        return 0, []
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    g = grid[grid["grade"].astype(str).isin(order)].copy()
    g["_o"] = g["grade"].astype(str).map(order)
    g = g.sort_values(["model", "months_since_launch", "_o"])
    prev = g.groupby(["model", "months_since_launch"])["forecast_rv_ratio"].shift(1)
    bad = g[(prev.notna()) & (g["forecast_rv_ratio"] > prev + 1e-9)]
    if len(bad) == 0:
        return 0, []
    cells = bad.drop_duplicates(["model", "months_since_launch"])
    fams = sorted(cells["model_family"].astype(str).unique()) if "model_family" in cells.columns else []
    return int(len(cells)), fams


def run_forecast(
    con,
    as_of: date,
    a,
    thr,
    cfg,
    replay: bool = True,
    backtest: bool = True,
    min_train: int = 100,
) -> RunSummary:
    """Full forecast chain on the tables in ``con``. See the module docstring for the steps."""
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc)
    as_of = as_date(as_of)
    families_cfg = cfg.families
    run_log_id = db.new_run(con, "forecast", getattr(cfg, "seed", None), as_of, None)
    counts: dict[str, int] = {}
    notes: list[str] = []

    devices = _read(con, "devices")
    resale = _read(con, "resale")
    events = _read(con, "events")
    refurb = _read(con, "refurbishment")
    catalogue = _read(con, "model_catalogue")

    # 1. replay immutable runs
    if replay:
        counts["forecast_runs_added"] = replay_runs(
            con, resale, devices, catalogue, events, a, families_cfg, as_of, min_train
        )
    rvm = latest_run(con, as_of)
    if rvm is None:
        run_id = make_run_id(as_of)
        train = build_training_frame(resale, devices, catalogue, events, as_of, families_cfg)
        rvm = fit(train, as_is_frame(resale, devices, as_of), as_of, run_id, a, MIN_N_PER_FAMILY)
        save_run(con, rvm)
        counts["forecast_runs_added"] = counts.get("forecast_runs_added", 0) + 1
    counts["forecast_runs_total"] = int(db.read_df(con, "SELECT count(*) AS n FROM forecast_runs")["n"].iloc[0])
    notes.append(f"latest run {rvm.run_id} fit_quality={rvm.fit_quality}")
    for note in rvm.notes:
        notes.append(note)

    # 2. grid + current from the latest run
    grid = build_grid(rvm, catalogue, GRID_MONTHS, families_cfg=families_cfg)
    counts["rv_forecast_grid"] = db.write_df(con, "rv_forecast_grid", grid, mode="replace")
    # diagnosis, not a fix: a grid that values a worse grade above a better one at the same (model, month)
    # cannot price a grade difference; lever L04 refuses such reads and this note says how many there are
    n_bad, families_bad = _non_monotone_grade_cells(grid)
    counts["rv_forecast_grid_non_monotone_cells"] = n_bad
    if n_bad:
        notes.append(
            f"grid not monotone in grade on {n_bad} (model, month) cells (families: {', '.join(families_bad)}): the "
            "grade dummies of the fit are confounded (the synthetic grade at sale after refurbishment is not the grade "
            "the truth price was drawn from); L04 refuses the grade part on those cells, the fit itself is unchanged"
        )
    current = build_current(rvm, devices, events, refurb, resale, catalogue, a, families_cfg, as_of)
    counts["rv_forecast_current"] = db.write_df(con, "rv_forecast_current", current, mode="replace")

    # 3. forecast of record + error series (deterministic from immutable runs)
    record = forecast_of_record_frame(events, devices, catalogue, load_all_runs(con), a, families_cfg)
    counts["rv_forecast_of_record"] = db.write_df(con, "rv_forecast_of_record", record, mode="replace")
    errors = build_error_series(resale, devices, record)
    counts["rv_forecast_error_monthly"] = db.write_df(con, "rv_forecast_error_monthly", errors, mode="replace")

    # 4. backtest
    if backtest:
        cutoff = month_end(add_months(as_of, -6))
        results = time_split_backtest(resale, devices, catalogue, events, a, families_cfg, cutoff, MIN_N_PER_FAMILY)
        bt = results_to_frame(results)
        counts["backtest_result"] = db.append_rows(con, "backtest_result", bt) if len(bt) else 0
        if not len(bt):
            notes.append(f"backtest skipped: no sales after cutoff {cutoff.isoformat()}")

    # 5. advisories
    adv = pd.concat(
        [
            sell_before_launch(current, rvm, catalogue, devices, families_cfg, a, thr, as_of),
            calibration_advisory(errors, thr, as_of, rvm.run_id),
        ],
        ignore_index=True,
    )
    adv = adv[ADVISORY_COLUMNS] if len(adv) else pd.DataFrame(columns=ADVISORY_COLUMNS)
    counts["advisories"] = db.write_df(con, "advisories", adv, mode="replace")

    db.finish_run(con, run_log_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="forecast",
        run_id=run_log_id,
        started_at=started,
        finished_at=finished,
        seconds=round(time.perf_counter() - t0, 3),
        counts=counts,
        notes=notes,
    )
