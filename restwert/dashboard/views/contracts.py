"""Contracts tab: renewal calendar (next 6 months), register, coverage.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data

COVERAGE_KPI = "KPI_PROC_SPEND_UNDER_CONTRACT"


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the contracts tab."""
    db_path = data.current_db_path()
    cal = data.renewal_calendar(db_path)
    reg = data.contracts_register(db_path)
    kpis = data.kpi_values(db_path, as_of)

    c1, c2, c3 = st.columns(3)
    cov = charts.status_series_from_kpis(kpis, COVERAGE_KPI)
    with c1:
        if cov is None:
            st.metric("Share of spend under contract", "n/a")
        else:
            t = cov.get("target")
            charts.kpi_tile("Share of spend under contract", cov, None if t is None or pd.isna(t) else float(t))
    with c2:
        st.metric("Contracts in renewal window", 0 if cal.empty else int(len(cal)))
        if not cal.empty:
            st.caption(f"{int(cal['action_required'].astype(bool).sum())} need action, {int(cal['auto_renewal'].astype(bool).sum())} auto-renew")
    with c3:
        if reg.empty:
            st.metric("Contracts in register", 0)
        else:
            st.metric("Contracts in register", int(len(reg)))
            st.caption(", ".join(f"{k}: {v}" for k, v in reg["contract_type"].value_counts().items()))

    st.subheader("Renewal calendar, next 6 months")
    if cal.empty:
        st.info("No renewal_calendar yet. Run `python -m restwert contracts`.")
    else:
        sup_only = st.checkbox("Supplier contracts only", value=True)
        shown = cal[cal["contract_type"] == "supplier"] if sup_only and (cal["contract_type"] == "supplier").any() else cal
        st.plotly_chart(charts.renewal_calendar_figure(shown), width="stretch")
        cols = [
            "month_bucket",
            "contract_type",
            "contract_id",
            "counterparty",
            "end_date",
            "notice_deadline",
            "days_to_notice_deadline",
            "days_to_end",
            "auto_renewal",
            "annual_value",
            "action_required",
        ]
        cols = [c for c in cols if c in shown.columns]
        st.dataframe(shown[cols].sort_values(["end_date"]), hide_index=True, width="stretch")

    st.subheader("Contracts register")
    if reg.empty:
        st.info("No contracts_register yet.")
        return
    types = sorted(reg["contract_type"].unique())
    sel = st.multiselect("Type", types, default=[t for t in types if t == "supplier"] or types)
    status_opts = sorted(reg["status"].dropna().unique())
    sel_status = st.multiselect("Status", status_opts, default=status_opts)
    view = reg[reg["contract_type"].isin(sel) & reg["status"].isin(sel_status)]
    cols = [
        "contract_type",
        "contract_id",
        "counterparty",
        "category",
        "start_date",
        "end_date",
        "notice_days",
        "notice_deadline",
        "auto_renewal",
        "price_protection",
        "price_protection_days",
        "payment_terms_days",
        "annual_value",
        "status",
    ]
    cols = [c for c in cols if c in view.columns]
    st.dataframe(view[cols], hide_index=True, width="stretch", height=400)
    st.caption(
        "Rental contracts: notice days come from thresholds.yaml (rental_notice_days, owner Head of Customer Success), "
        "annual value = monthly rate x 12. Supplier contracts: annual value = spend under contract."
    )
