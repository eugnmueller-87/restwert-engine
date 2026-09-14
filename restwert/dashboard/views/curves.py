"""Residual value curves tab: forecast line with band, realised points, backtest table.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.enums import FAMILIES, GRADES


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the curves tab."""
    db_path = data.current_db_path()
    grid = data.grid(db_path)
    points = data.realised_curve_points(db_path)

    families = list(FAMILIES)
    if not grid.empty:
        families = sorted(set(grid["model_family"]))
    c1, c2, c3 = st.columns(3)
    family = c1.selectbox("Family", families, index=0)
    grade = c2.selectbox("Grade", list(GRADES), index=1)
    models = ["all models"]
    if not grid.empty:
        models += sorted(grid.loc[grid["model_family"] == family, "model"].unique())
    model_choice = c3.selectbox("Model", models, index=0)
    model = None if model_choice == "all models" else model_choice

    st.plotly_chart(charts.rv_curve_figure(grid, points, family, grade, model), width="stretch")
    st.caption(
        "Line: point forecast exp(mu) on the marketplace baseline and base storage; shaded band: "
        "80 percent interval exp(mu +/- 1.28 sigma). Points: realised gross price / purchase price per "
        "channel. As-Is sales are not in the training set. A grade with no training support (grade D "
        "when every grade-D sale went As Is) shows the family As-Is ratio as a flat line and is "
        "labelled fit_quality unsupported_grade."
    )

    if not grid.empty:
        gsel = grid[(grid["model_family"] == family) & (grid["grade"] == grade)]
        if not gsel.empty:
            info = gsel.groupby("model").agg(
                n_train=("n_train", "first"), fit_quality=("fit_quality", "first"), run_id=("run_id", "first")
            )
            st.caption(
                "Fit: "
                + "; ".join(
                    f"{m}: {r.fit_quality} on {int(r.n_train) if pd.notna(r.n_train) else 0} rows"
                    for m, r in info.iterrows()
                )
                + f" (run {info['run_id'].iloc[0]})"
            )

    st.subheader("Backtest (time split, latest run)")
    bt = data.backtest(db_path)
    if bt.empty:
        st.info("No backtest yet. Run `python -m restwert forecast`.")
        return
    show = bt[
        [
            "model_family",
            "cutoff",
            "n_train",
            "n_test",
            "mape",
            "bias",
            "wape",
            "mae_eur",
            "rmse_log",
            "train_max_sale_date",
            "test_min_sale_date",
        ]
    ].copy()
    st.dataframe(
        show.style.format({"mape": "{:.1%}", "bias": "{:+.1%}", "wape": "{:.1%}", "mae_eur": "{:,.0f}", "rmse_log": "{:.3f}"}),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Train sales end at the cutoff, test sales start after it; launches after the cutoff enter "
        "the features only through the calendar rule. This backtest measures the PRICING MODEL given "
        "the realised channel, the grade at sale and the actual sale date of every test row. The "
        "monthly error series on the Overview tab measures the OPERATIONAL forecast made at return "
        "(marketplace baseline, grade at return, expected sale date), so it is higher by design; the "
        "two numbers are not meant to agree. On synthetic data the error measures recovery of a "
        "synthetic curve, not market accuracy."
    )
