"""6 Levers: where do we tighten, and who owns the screw? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: additive levers EUR per year (KPI_LEV_ADDITIVE_EUR_PA), the largest
single lever, open queue rows acting on lever rules (R01, R02, R03, R05, R07),
lever rows not attributed. Chart: EUR per year per lever. Table:
``gold.levers_summary`` as is. Folded: the lever x cohort heatmap, the top 50
serials per lever with counterfactual_json, ADV03 and ADV04 rows.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "Where do we tighten, and who owns the screw?"
NOT_ADDITIVE = "levers are references against named fleet rows and do not add up"
LEVER_RULES = ("R01", "R02", "R03", "R05", "R07")
SUMMARY_COLS = ("rank", "lever_id", "lever_name", "component", "basis", "additive", "n_eligible", "n_attributed", "eur_per_device",
                "eur_per_device_p90", "eur_fleet_per_year", "share_of_lever_basis", "lever_basis_eur", "lever_basis", "threshold_key",
                "threshold_value", "threshold_unit", "threshold_owner", "rule_id", "reference_key", "reference_owner", "reference_sentence")


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Levers page."""
    db_path = c.db_path()
    s = data.levers_summary(db_path)
    if s.empty:
        c.question(QUESTION)
        c.missing("gold.levers_summary")
        return
    kpis = data.gold_kpi_values(db_path, as_of)
    s = c.to_num(s, ["eur_per_device", "eur_per_device_p90", "eur_fleet_per_year", "share_of_lever_basis", "lever_basis_eur", "n_eligible", "n_attributed", "rank"])
    largest = s.sort_values("eur_fleet_per_year", ascending=False).iloc[0]
    not_attr = int((s["n_eligible"] - s["n_attributed"]).clip(lower=0).sum())
    q = data.queue_for_rules(db_path, LEVER_RULES)

    c.question(QUESTION)
    st.caption(
        "A lever is actual minus a named reference on one ledger component: the fleet's own p75 discount, the estimate of "
        "record at the best admissible channel, the forecast grid at the declared grade. Deterministic arithmetic, no model. "
        + NOT_ADDITIVE + "."
    )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        c.gold_tile(kpis, "KPI_LEV_ADDITIVE_EUR_PA", "Additive levers, EUR per year")
    with col2:
        st.metric(f"Largest single lever: {largest['lever_id']} {largest['lever_name']}", c.eur0(largest["eur_fleet_per_year"]))
        st.caption(f"owner {largest['threshold_owner']} via {largest['rule_id']} ({largest['threshold_key']}); reference {largest.get('reference_key', 'n/a')} ({largest.get('reference_owner', 'n/a')})")
    with col3:
        st.metric("Open queue rows on lever rules", f"{len(q):,}")
        st.caption("R01 repair, R02 channel, R03 aging, R05 price protection, R07 discount floor")
    with col4:
        st.metric("Lever rows not attributed", f"{not_attr:,}")
        st.caption("reference group below lever_reference_min_n (owner CFO): nothing is guessed")

    st.plotly_chart(charts.levers_bar_figure(s), width="stretch")

    st.markdown("**Where to tighten** (" + NOT_ADDITIVE + ")")
    c.table(c.round2(s[[x for x in SUMMARY_COLS if x in s.columns]]))
    st.caption(
        "share_of_lever_basis = eur_fleet_per_year over lever_basis_eur, the population the lever itself measures "
        "(landed cost of the same purchases for L01 and L02, absolute closed result of the same serials for L03 to L07); "
        "it can exceed 1 because a lever is measured on every attributed serial, not only on the ones that lost money, "
        "and it never says that a lever explains the fleet's loss. L07 is counted once per cohort (a policy comparison). "
        "rule_id is the rule or advisory that reacts; reference_key and reference_owner name the assumption the "
        "arithmetic rests on."
    )

    lc = data.levers_by_cohort(db_path)
    with st.expander("Lever x manufacturer heatmap (mean EUR per attributed device)", expanded=False):
        kinds = sorted(lc["cohort_kind"].astype(str).unique()) if not lc.empty else ["oem"]
        kind = st.selectbox("cohort kind", kinds, index=(kinds.index("oem") if "oem" in kinds else 0), key="levers_kind")
        st.plotly_chart(charts.levers_heatmap_figure(lc, kind), width="stretch")
    with st.expander("Top 50 serials per lever with the counterfactual", expanded=False):
        lever_ids = s.sort_values("rank")["lever_id"].astype(str).tolist()
        lever = st.selectbox("lever", lever_ids, index=0, key="levers_lever")
        per = data.levers_per_device(db_path, lever)
        per = per[per["is_attributed"].astype(bool)] if not per.empty and "is_attributed" in per.columns else per
        if per.empty:
            st.caption("no attributed serial for this lever")
        else:
            cols = [x for x in ("serial", "delta_eur", "actual_value", "reference_value", "reference_source", "n_reference", "basis", "event_date", "counterfactual_json") if x in per.columns]
            st.dataframe(c.round2(c.to_num(per[cols].head(50), ["delta_eur", "actual_value", "reference_value"])), hide_index=True, width="stretch")
    adv = data.advisories(db_path)
    adv = adv[adv["kind"].astype(str).isin(["manufacturer_mix", "term_gap"])] if not adv.empty and "kind" in adv.columns else pd.DataFrame()
    c.folded(
        f"ADV03 manufacturer mix and ADV04 term gap advisories ({len(adv):,})",
        adv,
        note="advisory only: a human reviews the allocation or the term policy; nothing here changes a decision",
    )
