"""Decision queue tab: advisories with owner, filters, inputs expander, recompute button.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The "Recompute decisions" button calls ``decisions.runner.run_all_decisions``
exactly like ``python -m restwert decide``. It appends to ``decision_log``
(deduplicated by input hash) and rebuilds ``decision_queue``. Nothing else.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import data

SHOW_COLS = [
    "priority",
    "rule_id",
    "rule_name",
    "outcome",
    "subject_type",
    "subject_id",
    "explanation",
    "threshold_key",
    "threshold_value",
    "threshold_owner",
    "due_date",
    "value_at_stake_eur",
    "advisory_kind",
    "advisory_confidence",
]


def _recompute(con, as_of: date) -> str:
    from restwert.config import load_assumptions, load_thresholds
    from restwert.decisions.runner import run_all_decisions

    thr = load_thresholds()
    a = load_assumptions()
    _, summary = run_all_decisions(con, as_of, thr, a)
    counts = " ".join(f"{k}={v}" for k, v in (summary.counts or {}).items())
    return f"run {summary.run_id}: {counts}"


def render(con, as_of: date) -> None:
    """Render the decision queue tab."""
    db_path = data.current_db_path()
    q = data.queue(db_path)

    top = st.columns([3, 1])
    with top[1]:
        if st.button("Recompute decisions", help="Same as `python -m restwert decide`; appends to the log, rebuilds the queue."):
            try:
                msg = _recompute(con, as_of)
                data.clear_cache()
                st.success(msg)
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - shown to the user, never swallowed
                st.error(f"recompute failed: {type(exc).__name__}: {exc}")
    with top[0]:
        if q.empty:
            st.info("The decision queue is empty. Run `python -m restwert decide` or press Recompute.")
            return
        st.caption(
            f"{len(q)} queued records from run {q['run_id'].iloc[0]}; every record names the rule, the threshold and its owner."
        )

    owners = sorted(q["threshold_owner"].dropna().unique())
    rules = sorted(q["rule_id"].dropna().unique())
    prios = sorted(int(p) for p in q["priority"].dropna().unique())
    f1, f2, f3 = st.columns(3)
    sel_owner = f1.multiselect("Owner", owners, default=owners)
    sel_rule = f2.multiselect("Rule", rules, default=rules)
    sel_prio = f3.multiselect("Priority", prios, default=prios)
    view = q[q["threshold_owner"].isin(sel_owner) & q["rule_id"].isin(sel_rule) & q["priority"].isin(sel_prio)]

    by_owner = view.groupby("threshold_owner").agg(records=("decision_id", "size"), value_at_stake=("value_at_stake_eur", "sum"))
    st.dataframe(by_owner.style.format({"value_at_stake": "{:,.0f}"}), width="stretch")

    cols = [c for c in SHOW_COLS if c in view.columns]
    st.dataframe(view[cols], hide_index=True, width="stretch", height=420)

    st.subheader("Inspect one record")
    if view.empty:
        st.caption("no record matches the filters")
        return
    labels = (view["rule_id"].astype(str) + " " + view["subject_id"].astype(str) + " : " + view["outcome"].astype(str)).tolist()
    choice = st.selectbox("Record", range(len(labels)), format_func=lambda i: labels[i])
    row = view.iloc[int(choice)]
    with st.expander("inputs_json (everything the rule read)", expanded=True):
        raw = row.get("inputs_json")
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            st.caption("no decision_log row joined (log missing?)")
        else:
            try:
                st.json(json.loads(raw))
            except (TypeError, ValueError):
                st.code(str(raw))
        meta = {
            "decision_id": row.get("decision_id"),
            "rule_id": row.get("rule_id"),
            "rule_version": row.get("rule_version"),
            "threshold_key": row.get("threshold_key"),
            "threshold_value": row.get("threshold_value"),
            "threshold_unit": row.get("threshold_unit"),
            "threshold_owner": row.get("threshold_owner"),
            "due_date": str(row.get("due_date")),
            "value_at_stake_eur": row.get("value_at_stake_eur"),
        }
        st.json({k: (None if v is None or (isinstance(v, float) and pd.isna(v)) else v) for k, v in meta.items()})
        adv = row.get("advisory_json")
        if adv is not None and not (isinstance(adv, float) and pd.isna(adv)):
            st.caption("advisory attached (never changes the outcome):")
            try:
                st.json(json.loads(adv))
            except (TypeError, ValueError):
                st.code(str(adv))
