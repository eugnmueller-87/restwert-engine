"""5 Result: do we make money per device, and on which cohort? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The page shows three numbers and keeps them apart: the closed result (booked
lines plus flagged estimates), the open fleet "if liquidated today" and the open fleet
"projected at lease end". The two open numbers are estimates on different
questions; they are never summed with each other nor with the closed result,
and no label on this page reads "total result" (decision D13, tested).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "Do we make money per device, and on which cohort?"
NEVER_ADD = "two different numbers, neither is a closed result, never add them"
# Column labels as the owner reads them: QTY for counts, and rent is what the customer paid over the term.
DISPLAY_LABELS = {
    "n": "QTY (all devices)",
    "n_closed": "QTY closed",
    "n_open": "QTY open",
    "sum_result_closed": "result, sum EUR",
    "mean_result_closed": "result per device EUR",
    "result_pct_of_landed_closed": "result in % of landed cost",
    "rent_per_closed_device": "rent paid by the customer, term total, per device EUR",
    "rv_per_closed_device": "realised residual value (gross resale price) per device EUR",
    "tco_per_closed_device": "TCO per device EUR (landed cost plus every cost line to the sale)",
    "sum_pp_credit_closed": "price protection credits, sum EUR",
}
CLOSED_COLS = ("cohort_value", "n", "n_closed", "sum_result_closed", "mean_result_closed", "result_pct_of_landed_closed",
               "rent_per_closed_device", "rv_per_closed_device", "tco_per_closed_device", "sum_pp_credit_closed")
OPEN_COLS = ("cohort_value", "n_open", "sum_liquidation_today_open", "mean_liquidation_today_open",
             "sum_projected_lease_end_open", "mean_projected_lease_end_open")
SERIAL_COLS = ("serial", "oem", "catalogue_family", "model_name", "cohort_month", "term_months", "lifecycle_status", "is_closed",
               "rental_revenue", "realised_rv", "price_protection_credit_eur", "landed_cost", "tco_eur", "lifecycle_result_eur",
               "result_pct_of_landed", "result_if_liquidated_today", "result_projected_at_lease_end", "projected_label",
               "expected_remaining_cost", "resale_channel", "closed_date")
FORMULAS = """
**Closed cycle (sold or scrapped):**
`lifecycle_result_eur = SUM(amount_eur)` over the serial's ledger lines, revenue positive and cost negative
`= rental_revenue + realised_rv + price_protection_credit - landed_cost - (staging + outbound + service + return + wipe_grading + refurbishment + holding + channel_fee)`

**Open cycle, if liquidated today:**
`result_if_liquidated_today = SUM(lines to date) + estimate_rv_today x (1 - marketplace fee share) - marketplace fixed fee`

**Open cycle, projected at lease end:**
`result_projected_at_lease_end = SUM(lines to date) + remaining_contracted_rent + estimate_rv_lease_end x (1 - fee share) - fixed fee - expected_remaining_cost`
(`projected_label` says "projected at lease end" for rented devices and "projected at sale" for devices already back)

**Bridge to v0.1:**
`lifecycle_result_eur = result_v01_basis_eur - (staging + outbound_shipping + wipe_grading + holding_cost + support + mdm_operations) + price_protection_credit`,
and `result_v01_basis_eur` equals `device_pnl.lifecycle_margin` to the cent (silver.reconciliation).
"""


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Result page."""
    db_path = c.db_path()
    kinds = data.result_cohort_kinds(db_path)
    dl = data.device_ledger(db_path)
    if not kinds and dl.empty:
        c.question(QUESTION)
        c.missing("gold.result_by_cohort and silver.device_ledger")
        return
    kpis = data.gold_kpi_values(db_path, as_of)

    c.question(QUESTION)
    st.caption(
        "Closed cycles are booked lines plus the flagged estimates (holding cost per stock phase and, until the credit "
        "note arrives, the channel fee). Open cycles carry two estimates that answer different questions; the page keeps "
        "all three apart."
    )
    family, oem = c.filter_row(dl, "result")
    dl_f = c.apply_filter(dl, family, oem)
    dl_f = c.to_num(dl_f, ["lifecycle_result_eur", "result_if_liquidated_today", "result_projected_at_lease_end", "landed_cost"])
    closed = dl_f[dl_f["is_closed"].astype(bool)] if not dl_f.empty and "is_closed" in dl_f.columns else pd.DataFrame()
    open_ = dl_f[~dl_f["is_closed"].astype(bool)] if not dl_f.empty and "is_closed" in dl_f.columns else pd.DataFrame()
    recent_closed = closed[c.trailing_mask(closed["closed_date"], as_of)] if not closed.empty and "closed_date" in closed.columns else closed
    n_recent = len(recent_closed)
    sum_recent = c.sum_or_none(recent_closed["lifecycle_result_eur"]) if n_recent else None
    liq = c.sum_or_none(open_["result_if_liquidated_today"]) if not open_.empty else None
    proj = c.sum_or_none(open_["result_projected_at_lease_end"]) if not open_.empty else None
    n_liq = int(open_["result_if_liquidated_today"].notna().sum()) if not open_.empty else 0
    n_proj = int(open_["result_projected_at_lease_end"].notna().sum()) if not open_.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if family == "all" and oem == "all":
            c.gold_tile(kpis, "KPI_RSLT_CLOSED_PER_DEVICE", f"Closed result per device, 12 months (n={n_recent:,})")
        else:
            st.metric(f"Closed result per device, 12 months (n={n_recent:,})", c.eur0(sum_recent / n_recent) if sum_recent is not None and n_recent else "n/a")
    with col2:
        st.metric("Sum of closed results (12 months)", c.eur0(sum_recent))
        st.caption(f"{len(closed):,} closed serials in the filter, all time")
    with col3:
        st.metric(f"Open fleet, result if liquidated today (sum, n={n_liq:,})", c.eur0(liq))
        st.caption("lines to date plus the estimate of today's residual value net of marketplace fees")
    with col4:
        st.metric(f"Open fleet, result projected at lease end (sum, n={n_proj:,})", c.eur0(proj))
        st.caption("remaining contracted rent plus the estimate at lease end minus expected remaining cost")
    st.caption(NEVER_ADD)

    kind = st.selectbox("cohort kind", kinds or ["purchase_month"], index=(kinds.index("purchase_month") if "purchase_month" in kinds else 0), key="result_kind")
    r = data.result_by_cohort(db_path, kind)
    r = c.to_num(r, [x for x in r.columns if x.startswith(("sum_", "mean_", "n", "result_", "rent_", "rv_", "tco_"))])
    st.plotly_chart(charts.result_by_cohort_figure(r, kind), width="stretch")

    if r.empty:
        st.caption("no rows")
    else:
        st.markdown(f"**Closed cycles by {kind}**: one number per cohort")
        c.table(c.round2(r[[x for x in CLOSED_COLS if x in r.columns]]).rename(columns=DISPLAY_LABELS))
        st.caption("sum_result_closed = rent + realised residual value + price protection credit - TCO, per row (identity tested)")
        c.folded(
            f"Open cycles by {kind}: two estimates, kept apart",
            c.round2(r[[x for x in OPEN_COLS if x in r.columns]]).rename(columns=DISPLAY_LABELS),
            note="liquidation today and projection at lease end answer different questions; " + NEVER_ADD,
        )

    if not dl_f.empty:
        cols = [x for x in SERIAL_COLS if x in dl_f.columns]
        c.folded(f"Per serial ({len(dl_f):,} in the filter)", c.round2(dl_f[cols]), max_rows=3000)
    with st.expander("The formulas", expanded=False):
        st.markdown(FORMULAS)
