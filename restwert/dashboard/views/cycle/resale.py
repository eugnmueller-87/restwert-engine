"""4 Resale: what did we actually get back, and how fast? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: sales (12 months), realised vs the estimate of record
(KPI_RSL_REALISED_VS_RECORD), median days return to cash
(KPI_RSL_DAYS_RETURN_TO_CASH), credit notes missing. Chart: realised vs the
estimate of record by channel with the identity line. Table:
``gold.resale_by_channel_grade``. Folded: per sale; grading drift declared vs
inspected.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "What did we actually get back, and how fast?"
SALE_COLS = ("serial", "oem", "catalogue_family", "model_name", "return_date", "grade_declared", "grade_inspected", "grade_out",
             "refurb_eur", "resale_channel", "sale_date", "resale_gross", "channel_fee_eur", "resale_net", "credited_at",
             "days_return_to_sale", "days_return_to_cash", "estimate_rv_of_record", "realised_vs_record_ratio")


def _grading_drift(sold: pd.DataFrame) -> pd.DataFrame:
    if sold.empty or "grade_declared" not in sold.columns or "grade_inspected" not in sold.columns:
        return pd.DataFrame()
    d = sold.dropna(subset=["grade_declared", "grade_inspected"])
    if d.empty:
        return pd.DataFrame()
    pv = pd.crosstab(d["grade_declared"].astype(str), d["grade_inspected"].astype(str))
    pv.index.name = "declared at return \\ inspected"
    return pv.reset_index()


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Resale page."""
    db_path = c.db_path()
    r = data.resale_by_channel_grade(db_path)
    dl = data.device_ledger(db_path)
    if r.empty and dl.empty:
        c.question(QUESTION)
        c.missing("gold.resale_by_channel_grade and silver.device_ledger")
        return
    kpis = data.gold_kpi_values(db_path, as_of)

    c.question(QUESTION)
    st.caption(
        "Realised = gross resale price on the recommerce order; net = gross minus the channel fee on the credit note. "
        "The estimate of record is the forecast in force before the return date, so the comparison is honest about timing."
    )
    family, oem = c.filter_row(dl, "resale")
    dl_f = c.apply_filter(dl, family, oem)
    sold = dl_f[dl_f["sale_date"].notna()] if not dl_f.empty and "sale_date" in dl_f.columns else pd.DataFrame()
    sold = c.to_num(sold, ["resale_gross", "resale_net", "channel_fee_eur", "refurb_eur", "estimate_rv_of_record", "days_return_to_cash", "days_return_to_sale"])
    recent = sold[c.trailing_mask(sold["sale_date"], as_of)] if not sold.empty else sold
    missing_cn = int(recent["credited_at"].isna().sum()) if not recent.empty and "credited_at" in recent.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Sales (12 months)", f"{len(recent):,}")
        st.caption(f"{len(sold):,} sold serials in the filter, all time")
    with col2:
        if family == "all" and oem == "all":
            c.gold_tile(kpis, "KPI_RSL_REALISED_VS_RECORD", "Realised vs estimate of record (12 months)")
        else:
            g = c.sum_or_none(recent["resale_gross"]) if not recent.empty else None
            rec = c.sum_or_none(recent.loc[recent["estimate_rv_of_record"].notna(), "resale_gross"]) if not recent.empty else None
            rr = c.sum_or_none(recent["estimate_rv_of_record"]) if not recent.empty else None
            st.metric("Realised vs estimate of record (filter, 12 months)", c.pct(rec / rr) if rec is not None and rr else "n/a")
            st.caption(f"gross realised {c.eur0(g)} on the sales with a record")
    with col3:
        if family == "all" and oem == "all":
            c.gold_tile(kpis, "KPI_RSL_DAYS_RETURN_TO_CASH", "Median days return to cash (12 months)")
        else:
            med = recent["days_return_to_cash"].median() if not recent.empty else None
            st.metric("Median days return to cash (filter, 12 months)", c.days(med))
    with col4:
        st.metric("Credit notes missing (12 months)", f"{missing_cn:,}")
        st.caption("sold, no credit note yet: the cycle is not closed to cash")

    st.plotly_chart(charts.realised_vs_record_scatter(sold), width="stretch")

    st.markdown("**Resale per channel and grade at sale (trailing 12 months)**")
    r_show = c.round2(c.to_num(r, ["sum_gross", "sum_fees", "sum_net", "sum_refurb", "sum_estimate_of_record", "realised_vs_record_ratio", "median_days_return_to_cash"]))
    c.table(r_show)

    if not sold.empty:
        cols = [x for x in SALE_COLS if x in sold.columns]
        c.folded(f"Per sale ({len(sold):,})", c.round2(sold.sort_values("sale_date", ascending=False)[cols]))
        c.folded(
            "Grading drift: grade declared by the customer vs grade at inspection",
            _grading_drift(sold),
            note="rows = declared at return, columns = inspected; off-diagonal mass is the grading risk the return desk carries",
        )
