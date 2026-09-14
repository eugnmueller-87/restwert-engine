"""2 TCO: what does one device cost us from order to cash, line by line? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: TCO per closed device (KPI_TCO_PER_CLOSED_DEVICE), TCO excluding landed
cost, landed share of TCO, estimate share (every flagged estimate line: holding
cost per stock phase with the CFO owner, channel fees without a credit note, PO
prices without a unit invoice).
Chart: stacked mean line magnitude per cohort, estimate lines hatched. Table:
``gold.tco_by_cohort`` pivot line type x cohort. Folded: the ledger lines of one
serial with source_ref and allocation_basis; the v0.1 ``tco_per_model`` plan.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "What does one device cost us from order to cash, line by line?"
COST_TYPES = ("purchase_price", "freight", "duty", "staging", "outbound_shipping", "repair", "replacement_logistics",
              "return_logistics", "wipe_grading", "refurbishment", "holding_cost", "channel_fee")


def _pivot(t: pd.DataFrame, kind: str, order: tuple[str, ...]) -> pd.DataFrame:
    d = c.to_num(t[t["cohort_kind"] == kind], ["mean_eur", "sum_eur", "n_devices", "estimate_eur"])
    if d.empty:
        return pd.DataFrame()
    pv = d.pivot_table(index="line_type", columns="cohort_value", values="mean_eur", aggfunc="first")
    rows = [x for x in order if x in pv.index] + [x for x in pv.index if x not in order]
    pv = pv.reindex(rows).round(2)
    if "estimate_eur" in d.columns:
        d["estimate_eur"] = pd.to_numeric(d["estimate_eur"], errors="coerce").fillna(0.0)
        by_type = d.groupby("line_type").agg(est=("estimate_eur", "sum"), tot=("sum_eur", "sum"))
        share = (by_type["est"] / by_type["tot"].where(by_type["tot"] != 0)).fillna(0.0)
        labels = {t: ("all" if v >= 0.999 else f"{v:.0%}" if v > 0 else "no") for t, v in share.items()}
        pv.insert(0, "estimate", [labels.get(r, "no") for r in pv.index])
    else:
        est = d.groupby("line_type")["is_estimate"].first().astype(bool) if "is_estimate" in d.columns else pd.Series(False, index=pv.index)
        pv.insert(0, "estimate", ["yes" if bool(est.get(r, False)) else "no" for r in pv.index])
    n = d.groupby("cohort_value")["n_devices"].first()
    pv.loc["n closed devices"] = ["n"] + [float(n.get(col, 0)) for col in pv.columns[1:]]
    return pv.reset_index().rename(columns={"line_type": "line type (mean EUR per closed device)"})


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the TCO page."""
    db_path = c.db_path()
    t = data.tco_by_cohort(db_path)
    dl = data.device_ledger(db_path)
    if t.empty and dl.empty:
        c.question(QUESTION)
        c.missing("gold.tco_by_cohort and silver.device_ledger")
        return
    kpis = data.gold_kpi_values(db_path, as_of)
    order = c.ledger_order()
    owner = c.assumption_owner("holding_cost_per_day_eur")

    c.question(QUESTION)
    st.caption(
        "TCO is a sum of ledger lines with a source_ref each; the only rate is holding cost, booked per stock phase "
        "(inbound: goods receipt to shipment; return: return receipt to sellable; sale: sellable to sold; each phase "
        "days x holding_cost_per_day_eur) and flagged is_estimate. The channel fee is an estimate until the credit "
        "note arrives, the PO price until the unit invoice arrives; both are flagged the same way."
    )
    with st.expander("The deposited definition: what is in the TCO, what is out, what is still missing", expanded=False):
        try:
            from restwert.paths import DOCS_DIR

            st.markdown((DOCS_DIR / "TCO_DEFINITION.md").read_text(encoding="utf-8"))
        except OSError as exc:  # noqa: PERF203 - a missing doc must not break the page
            st.caption(f"docs/TCO_DEFINITION.md not readable: {exc}")
    st.caption("Known gaps, stated: first-level support and MDM operations per device-month are provider costs that are not booked yet; the result per device is overstated by exactly these two blocks until they enter as owned allocations.")
    family, oem = c.filter_row(dl, "tco")
    dl_f = c.apply_filter(dl, family, oem)
    closed = dl_f[dl_f["is_closed"].astype(bool)] if not dl_f.empty and "is_closed" in dl_f.columns else pd.DataFrame()
    closed = c.to_num(closed, ["tco_eur", "tco_excl_landed_eur", "landed_cost", "holding_cost_eur", "tco_transactional_eur"])
    n_closed = len(closed)
    tco_sum = c.sum_or_none(closed["tco_eur"]) if n_closed else None
    excl = c.sum_or_none(closed["tco_excl_landed_eur"]) if n_closed else None
    landed = c.sum_or_none(closed["landed_cost"]) if n_closed else None
    hold = c.sum_or_none(closed["holding_cost_eur"]) if n_closed else None

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if family == "all" and oem == "all":
            c.gold_tile(kpis, "KPI_TCO_PER_CLOSED_DEVICE", "TCO per closed device (12 months)")
        else:
            st.metric("TCO per closed device (filter, all time)", c.eur0(tco_sum / n_closed) if tco_sum is not None and n_closed else "n/a")
            st.caption(f"n={n_closed:,} closed serials in the filter")
    with col2:
        st.metric("TCO excluding landed cost, per closed device", c.eur0(excl / n_closed) if excl is not None and n_closed else "n/a")
        st.caption("staging, shipping, service, return, wipe and grading, refurbishment, holding, channel fees")
    with col3:
        st.metric("Landed cost share of TCO", c.pct(landed / tco_sum) if landed is not None and tco_sum else "n/a")
        st.caption("purchase price plus freight and duty per received unit")
    with col4:
        est = c.sum_or_none(closed["tco_eur"] - closed["tco_transactional_eur"]) if n_closed else None
        st.metric("Estimate share of TCO", c.pct(est / tco_sum) if est is not None and tco_sum else "n/a")
        c.estimate_caption("holding_cost_per_day_eur", owner)
        st.caption(f"holding cost {c.eur0(hold)} plus estimated channel fees and pending PO prices; tco_eur - tco_transactional_eur")

    kinds = [k for k in ("catalogue_family", "oem", "model_family", "term_months", "purchase_quarter", "resale_channel") if not t.empty and k in set(t["cohort_kind"])]
    kind = st.selectbox("cohort", kinds or ["catalogue_family"], index=0, key="tco_kind")
    st.plotly_chart(charts.tco_stack_figure(t, kind, order), width="stretch")

    st.markdown(f"**Mean EUR per closed device by line type and {kind}**")
    c.table(_pivot(t, kind, order) if not t.empty else pd.DataFrame())
    st.caption("estimate: share of the line type's EUR that comes from flagged estimate lines (all: holding cost; a percentage: channel fees without a credit note or PO prices without a unit invoice)")

    if not dl_f.empty:
        serials = dl_f["serial"].astype(str).tolist()
        with st.expander("Ledger lines of one serial (source_ref, allocation basis, estimate flag)", expanded=False):
            serial = st.selectbox("serial", serials[:5000], index=0, key="tco_serial")
            lines = data.ledger_lines(db_path, serial)
            if lines.empty:
                st.caption("no ledger lines for this serial")
            else:
                cols = [x for x in ("event_date", "line_type", "line_class", "amount_eur", "source_system", "source_ref", "allocation_basis",
                                    "is_estimate", "assumption_key", "assumption_owner", "counterparty", "contract_ref") if x in lines.columns]
                shown = c.round2(lines[cols])
                st.dataframe(shown, hide_index=True, width="stretch")
                total = c.sum_or_none(lines["amount_eur"])
                st.caption(f"signed sum of {len(lines)} lines = {total:,.2f} EUR (revenue positive, cost negative); this is lifecycle_result_eur on a closed cycle")
    plan = data.tco(db_path)
    c.folded(
        "v0.1 TCO per model and term (a plan, not a sum of lines)",
        c.round2(plan) if not plan.empty else plan,
        note="tco_per_model applies planned rates to a model and term; the tables above sum booked lines per serial. Compare, never add.",
    )
