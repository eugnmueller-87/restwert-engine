"""7 Contracts: which contracts cover our spend, and which need action? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: coverage by manufacturer (KPI_CTR_COVERAGE_BY_OEM), contracts ending
within 6 months, notice deadlines within ``renewal_alert_lead_days`` (the R06
threshold, owner named in the caption), price protection windows open. Chart:
coverage per manufacturer. Table: ``silver.contracts`` by role under the
TERMS_NOTE banner. Folded: the renewal calendar v2, rebate progress, SLA fields,
R05 and R06 queue rows, the counterparty allowlist.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "Which contracts cover our spend, and which need action?"
REGISTER_COLS = ("counterparty_role", "counterparty_name", "category", "status", "start_date", "end_date", "notice_days", "notice_deadline",
                 "days_to_notice_deadline", "auto_renewal", "action_required", "spend_under_contract_eur", "spend_actual_12m_eur",
                 "spend_actual_vs_planned_pct", "price_protection", "price_protection_days", "claim_window_days", "warranty_months",
                 "payment_terms_days", "covers_oems", "n_serials_under_contract", "contract_id")


def _calendar_for_figure(cal: pd.DataFrame) -> pd.DataFrame:
    """Map ``gold.renewal_calendar_v2`` onto the columns ``charts.renewal_calendar_figure`` reads."""
    d = cal.copy()
    d["contract_type"] = d["counterparty_role"].astype(str)
    d["counterparty"] = d["counterparty_name"].astype(str)
    d["annual_value"] = pd.to_numeric(d["spend_under_contract_eur"], errors="coerce")
    d["days_to_end"] = pd.to_numeric(d["days_to_end"], errors="coerce").fillna(0)
    return d


def _sla_text(contracts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in contracts.iterrows():
        raw = r.get("sla_json")
        if c.is_nan(raw) or not str(raw).strip():
            continue
        try:
            sla = json.loads(str(raw))
        except (TypeError, ValueError):
            sla = {"sla_json": str(raw)}
        rows.append({"counterparty": r.get("counterparty_name"), "role": r.get("counterparty_role"),
                     "sla": ", ".join(f"{k} = {v}" for k, v in sla.items())})
    return pd.DataFrame(rows)


def _allowlist() -> pd.DataFrame:
    try:
        from restwert.contracts.counterparties import COUNTERPARTIES

        return pd.DataFrame([{"name": cp.name, "role": cp.role, "category": cp.category, "public": cp.is_public} for cp in COUNTERPARTIES])
    except ImportError:
        return pd.DataFrame()


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Contracts (v2) page."""
    db_path = c.db_path()
    contracts = data.contracts_v2(db_path)
    if contracts.empty:
        c.question(QUESTION)
        c.missing("silver.contracts")
        return
    kpis = data.gold_kpi_values(db_path, as_of)
    cov = data.coverage_by_oem(db_path)
    cal = data.renewal_calendar_v2(db_path)
    ct = c.to_num(contracts, ["days_to_end", "days_to_notice_deadline", "spend_under_contract_eur", "spend_actual_12m_eur", "spend_actual_vs_planned_pct"])
    ending_6m = int(((ct["days_to_end"] >= 0) & (ct["days_to_end"] <= 183)).sum())
    lead = c.threshold("renewal_alert_lead_days", as_of=as_of)
    lead_days = int(lead[0]) if lead is not None else 60
    lead_owner = lead[1] if lead is not None else "thresholds.yaml not readable, 60 assumed"
    notice_soon = int(((ct["days_to_notice_deadline"] >= 0) & (ct["days_to_notice_deadline"] <= lead_days)).sum())
    pp_open = int(cal["price_protection_window_open"].astype(bool).sum()) if not cal.empty and "price_protection_window_open" in cal.columns else 0

    c.question(QUESTION)
    st.warning(c.terms_note())
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        c.gold_tile(kpis, "KPI_CTR_COVERAGE_BY_OEM", "Purchase spend under contract (12 months)")
    with col2:
        st.metric("Contracts ending within 6 months", f"{ending_6m:,}")
        st.caption(f"{int((ct['status'].astype(str) == 'active').sum()):,} active of {len(ct):,} register rows")
    with col3:
        st.metric(f"Notice deadlines within {lead_days} days", f"{notice_soon:,}")
        st.caption(f"renewal_alert_lead_days = {lead_days} (rule R06, owner {lead_owner}); notice_deadline = end_date - notice_days; auto-renewal rows renew silently past it")
    with col4:
        st.metric("Price protection windows open", f"{pp_open:,}")
        st.caption("a price drop inside the protection window whose claim window is still open at as_of")

    st.plotly_chart(charts.coverage_bar_figure(cov), width="stretch")

    st.markdown("**Register v2 by role** (terms are synthetic placeholders; the counterparty list is public)")
    roles = ["all"] + sorted(ct["counterparty_role"].astype(str).unique())
    role = st.selectbox("role", roles, index=0, key="contracts_role")
    shown = ct if role == "all" else ct[ct["counterparty_role"].astype(str) == role]
    c.table(c.round2(shown[[x for x in REGISTER_COLS if x in shown.columns]]))

    with st.expander("Renewal calendar v2 (days to end per contract)", expanded=False):
        if cal.empty:
            st.caption("no rows")
        else:
            st.plotly_chart(charts.renewal_calendar_figure(_calendar_for_figure(cal)), width="stretch")
    rebates = data.rebate_progress(db_path)
    c.folded("Rebate progress (volume tiers)", c.round2(c.to_num(rebates, ["spend_12m_eur", "current_tier_pct", "next_tier_from_eur", "next_tier_pct", "gap_to_next_tier_eur"])) if not rebates.empty else rebates,
             note="spend in the trailing 12 months against the tier table on the contract; the gap is what moves the next tier")
    c.folded("SLA fields per counterparty (placeholders)", _sla_text(contracts))
    q = data.queue_for_rules(db_path, ("R05", "R06"))
    c.folded(f"R05 price protection and R06 renewal: open queue rows ({len(q):,})", q,
             note="R05 files a claim inside the window, R06 flags a notice deadline; both name the owner of the threshold")
    c.folded("Counterparty allowlist (public manufacturers, every other party role-only)", _allowlist(),
             note="exact catalogue oem strings plus names ending in (role-only); a test fails on anything else")
