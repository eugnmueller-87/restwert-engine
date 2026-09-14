"""Overview tab: the two top tiles, the forecast error chart and the KPI tree.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data

TOP_MARGIN = "KPI_TOP_LIFECYCLE_MARGIN"
TOP_ERROR = "KPI_TOP_RV_FORECAST_ERROR"
AREA_ORDER = ["Top", "Procurement", "Inventory", "Recommerce", "Indirect"]


def _target(row: pd.Series | None) -> float | None:
    if row is None:
        return None
    t = row.get("target")
    if t is None or pd.isna(t):
        return None
    return float(t)


def _tile_or_placeholder(label: str, row: pd.Series | None) -> None:
    if row is None:
        st.metric(label=label, value="n/a")
        st.caption("no kpi_values row for this as_of; run `python -m restwert kpis`")
        return
    charts.kpi_tile(label, row, _target(row))


def _render_calibration_advisories(adv: pd.DataFrame) -> None:
    """ADV02 rows: the model asks a named human to review it. Shown here so it reaches that human."""
    if adv is None or adv.empty or "kind" not in adv.columns:
        return
    cal = adv[adv["kind"] == "forecast_calibration"]
    if cal.empty:
        st.caption("No forecast calibration advisory (ADV02) in force: the channel-adjusted 3-month bias is inside the threshold.")
        return
    st.markdown("**Forecast calibration advisory (ADV02): a human reviews the model**")
    for _, row in cal.iterrows():
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        st.warning(
            f"{row.get('note')}  \n"
            f"months {payload.get('months')} | n_sales {payload.get('n_sales')} | "
            f"mean bias {payload.get('mean_bias_3m')} vs threshold {payload.get('threshold_bias_pct')} | "
            f"owner: {row.get('threshold_owner')} ({row.get('threshold_key')}) | run {row.get('run_id')}"
        )
    st.caption("Advisory only: nothing in the tool refits or changes a decision because of it. It is also queued as a priority-3 row (ADV02) on the Decisions tab.")


def render(con, as_of: date) -> None:  # noqa: ARG001 - con kept for the common view signature
    """Render the overview tab."""
    db_path = data.current_db_path()
    kpis = data.kpi_values(db_path, as_of)

    st.subheader("Top KPIs")
    c1, c2 = st.columns(2)
    margin_row = charts.status_series_from_kpis(kpis, TOP_MARGIN)
    error_row = charts.status_series_from_kpis(kpis, TOP_ERROR)
    with c1:
        _tile_or_placeholder("Lifecycle margin per device", margin_row)
        if margin_row is not None:
            bd = data.kpi_breakdown(db_path, TOP_MARGIN, as_of)
            if not bd.empty:
                st.dataframe(
                    bd[["dimension_value", "value", "n"]].rename(
                        columns={"dimension_value": "family", "value": "EUR per device"}
                    ),
                    hide_index=True,
                    width="stretch",
                )
    with c2:
        _tile_or_placeholder("Residual value forecast error", error_row)
        if error_row is not None:
            st.caption("MAPE of the latest complete month; bias, WAPE and n in the note above.")

    st.subheader("Residual value forecast error by month")
    st.plotly_chart(charts.forecast_error_figure(data.error_series(db_path)), width="stretch")
    st.caption(
        "Realised gross resale price against the forecast of record (the run in force before the "
        "return date). Positive bias means the forecast was too high, i.e. collateral was overstated. "
        "Bars and solid line: business view, marketplace-baseline forecast against every non-As-Is "
        "channel, so a buyout or wholesale discount counts as error. Dashed line and diamonds: model "
        "view, the same forecast at the channel actually used; the recalibration advisory (ADV02) "
        "tests the model view. The backtest on the curves tab is a third number again: it conditions "
        "on realised channel, grade and sale date and is lower by design."
    )
    _render_calibration_advisories(data.advisories(db_path))

    st.subheader("KPI tree")
    if kpis.empty:
        st.info("No KPI values for this as_of yet. Run `python -m restwert kpis`.")
        return
    areas = [a for a in AREA_ORDER if a in set(kpis["area"])]
    areas += sorted(set(kpis["area"]) - set(areas))
    for area in areas:
        rows = kpis[kpis["area"] == area].sort_values("kpi_id")
        n_ok = int((rows["status"] == "ok").sum())
        with st.expander(f"{area}  ({n_ok} of {len(rows)} measurable)", expanded=(area == "Top")):
            cols = st.columns(min(4, max(1, len(rows))))
            for i, (_, row) in enumerate(rows.iterrows()):
                with cols[i % len(cols)]:
                    charts.kpi_tile(str(row["name"]), row, _target(row))
                    definition = row.get("formula_text") or ""
                    if definition:
                        st.caption(f"formula: {definition}"[:300])
