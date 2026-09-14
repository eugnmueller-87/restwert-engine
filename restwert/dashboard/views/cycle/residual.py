"""3 Residual estimate: what will the rented fleet be worth at lease end? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: rented serials, sum of the fleet-model estimate at lease end (decides),
sum of the public anchor at lease end (ask, upper bound, advises), estimate vs
anchor (KPI_RES_ESTIMATE_VS_ANCHOR). Chart: two bars per (family, oem) as share
of net RRP. Table: ``gold.estimate_vs_anchor``. Folded: the per-serial estimate
block and the v0.1 curve figure for one model.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "What will the rented fleet be worth at lease end?"
RENTED = ("rented", "awaiting_return")
SERIAL_COLS = ("serial", "oem", "catalogue_family", "model_name", "storage_gb", "contract_end_planned", "grade_used", "grade_source",
               "estimate_months_at_lease_end", "estimate_rv_lease_end", "estimate_rv_today", "estimate_run_id", "estimate_rv_source",
               "estimate_fit_quality", "anchor_curve_group", "anchor_fit_quality", "anchor_rv_lease_end", "estimate_vs_anchor_ratio",
               "rrp_net_eur", "purchase_price")


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Residual estimate page."""
    db_path = c.db_path()
    e = data.estimate_vs_anchor(db_path)
    dl = data.device_ledger(db_path)
    if e.empty and dl.empty:
        c.question(QUESTION)
        c.missing("gold.estimate_vs_anchor and silver.device_ledger")
        return
    kpis = data.gold_kpi_values(db_path, as_of)
    grade_owner = c.assumption_owner("expected_grade_at_return")

    c.question(QUESTION)
    st.caption(
        "The fleet model decides, the anchor curve advises. Estimate = purchase price x forecast grid ratio at the "
        "expected grade and the months since launch at lease end (plus the expected return-to-sale days). "
        "Anchor = public marketplace ask for the same family and manufacturer, an upper bound, NULL outside the anchor age range."
    )
    family, oem = c.filter_row(dl, "residual")
    dl_f = c.apply_filter(dl, family, oem)
    rented = dl_f[dl_f["lifecycle_status"].astype(str).isin(RENTED)] if not dl_f.empty else pd.DataFrame()
    rented = c.to_num(rented, ["estimate_rv_lease_end", "anchor_rv_lease_end", "rrp_net_eur"])
    est_sum = c.sum_or_none(rented["estimate_rv_lease_end"]) if not rented.empty else None
    with_anchor = rented[rented["anchor_rv_lease_end"].notna()] if not rented.empty else pd.DataFrame()
    anchor_sum = c.sum_or_none(with_anchor["anchor_rv_lease_end"]) if not with_anchor.empty else None
    e_f = e
    if family != "all":
        e_f = e_f[e_f["catalogue_family"].astype(str) == family]
    if oem != "all":
        e_f = e_f[e_f["oem"].astype(str) == oem]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Rented serials (incl. awaiting return)", f"{len(rented):,}")
        st.caption(f"{len(with_anchor):,} of them inside the anchor age range")
    with col2:
        st.metric("Estimate at lease end, fleet model (decides)", c.eur0(est_sum))
        c.estimate_caption("expected_grade_at_return", grade_owner)
    with col3:
        st.metric("Anchor at lease end, public ask (upper bound, advises)", c.eur0(anchor_sum))
        st.caption("summed over the serials with an anchor only; asks contain the refurbisher's margin")
    with col4:
        if family == "all" and oem == "all":
            c.gold_tile(kpis, "KPI_RES_ESTIMATE_VS_ANCHOR", "Estimate vs anchor (same serials)")
        else:
            est_same = c.sum_or_none(with_anchor["estimate_rv_lease_end"]) if not with_anchor.empty else None
            st.metric("Estimate vs anchor (filter, same serials)", c.pct(est_same / anchor_sum) if est_same is not None and anchor_sum else "n/a")
            st.caption("below 100 percent: the fleet model sits under the public ask, as a realised price should")

    st.plotly_chart(charts.estimate_vs_anchor_figure(e_f), width="stretch")
    st.caption("the fleet model decides, the anchor curve advises; anchor NULL outside the anchor age range")

    st.markdown("**Estimate vs anchor per catalogue family and manufacturer**")
    c.table(c.round2(c.to_num(e_f, ["sum_estimate_lease_end", "sum_anchor_lease_end", "mean_estimate_ratio", "mean_anchor_ratio", "estimate_vs_anchor_ratio"])) if not e_f.empty else pd.DataFrame())

    if not rented.empty:
        cols = [x for x in SERIAL_COLS if x in rented.columns]
        c.folded(
            f"Per serial: grade used, months at lease end, run id, sources ({len(rented):,} rented)",
            c.round2(rented[cols]),
            note="grade_source: refurbished / inspected / assumption; estimate_rv_source names the grid or fallback the number came from",
        )
    with st.expander("Residual value curve of one model (v0.1 forecast grid, realised points)", expanded=False):
        grid = data.grid(db_path)
        points = data.realised_curve_points(db_path)
        if grid.empty:
            st.caption("no rv_forecast_grid yet")
        else:
            fams = sorted(grid["model_family"].astype(str).unique())
            f1, f2, f3 = st.columns(3)
            fam = f1.selectbox("model family", fams, index=0, key="residual_fam")
            grades = sorted(grid.loc[grid["model_family"].astype(str) == fam, "grade"].astype(str).unique())
            grade = f2.selectbox("grade", grades, index=min(1, len(grades) - 1), key="residual_grade")
            models = sorted(grid.loc[grid["model_family"].astype(str) == fam, "model"].astype(str).unique())
            model = f3.selectbox("model", models, index=0, key="residual_model")
            st.plotly_chart(charts.rv_curve_figure(grid, points, fam, grade, model), width="stretch")
