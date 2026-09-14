"""Inventory tab: aging buckets, WIP histogram, weeks of cover, TCO per term (12, 24, 36, 48 months).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data

KPI_IDS = ["KPI_INV_WEEKS_OF_COVER", "KPI_INV_AGING_90", "KPI_INV_AGING_180", "KPI_INV_WIP_DAYS", "KPI_INV_WRITE_DOWN_PCT"]


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the inventory tab."""
    db_path = data.current_db_path()
    pnl = data.device_pnl(db_path)
    kpis = data.kpi_values(db_path, as_of)

    if not pnl.empty and "lifecycle_status" in pnl.columns:
        counts = pnl["lifecycle_status"].value_counts()
        cols = st.columns(len(counts) if len(counts) else 1)
        for i, (status, n) in enumerate(counts.items()):
            cols[i % len(cols)].metric(str(status), int(n))
    else:
        st.info("No device_pnl yet. Run `python -m restwert pnl`.")

    st.subheader("Inventory KPIs")
    cols = st.columns(len(KPI_IDS))
    for i, kpi_id in enumerate(KPI_IDS):
        row = charts.status_series_from_kpis(kpis, kpi_id)
        with cols[i]:
            if row is None:
                st.metric(kpi_id, "n/a")
            else:
                t = row.get("target")
                charts.kpi_tile(str(row["name"]), row, None if t is None or pd.isna(t) else float(t))

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.aging_figure(pnl), width="stretch")
        b = charts.aging_buckets(pnl)
        if not b.empty:
            st.dataframe(b.style.format({"book_value": "{:,.0f}"}), hide_index=True, width="stretch")
    with c2:
        st.plotly_chart(charts.wip_hist_figure(pnl), width="stretch")

    st.subheader("Weeks of cover by family")
    woc = data.kpi_breakdown(db_path, "KPI_INV_WEEKS_OF_COVER", as_of)
    if woc.empty:
        st.caption("no breakdown available")
    else:
        st.dataframe(
            woc[["dimension_value", "value", "numerator", "denominator"]].rename(
                columns={"dimension_value": "family", "value": "weeks", "numerator": "in stock", "denominator": "sales per week"}
            ),
            hide_index=True,
            width="stretch",
        )

    st.subheader("TCO per model: 12, 24, 36 and 48 months")
    tco = data.tco(db_path)
    if tco.empty:
        st.info("No tco_per_model yet.")
        return
    keep = ["model", "model_family", "term_months", "landed_cost_avg", "forecast_rv_at_end", "tco", "tco_per_month", "monthly_rate_avg", "gap_rate_minus_tco_per_month", "inputs_source"]
    keep = [c for c in keep if c in tco.columns]
    wide = tco[keep].pivot_table(
        index=["model_family", "model"],
        columns="term_months",
        values=[c for c in ("tco", "tco_per_month", "gap_rate_minus_tco_per_month", "forecast_rv_at_end") if c in keep],
        aggfunc="first",
    )
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reset_index()
    st.dataframe(wide.style.format(precision=0, thousands=","), hide_index=True, width="stretch")
    st.caption(
        "tco = landed cost - forecast residual value at end + expected repair, refurbishment, logistics and fees. "
        "gap = average monthly rate - tco per month. inputs_source says whether the cost inputs were realised or assumptions."
    )
