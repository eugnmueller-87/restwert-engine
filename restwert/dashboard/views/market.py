"""Realisation tab: which device keeps what share of its price, from public anchors, and how it is computed.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

One question, one table, one chart. Everything else is folded away.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from restwert.market.anchors import load_anchors
from restwert.market.curves import fit_curves
from restwert.market.run import DEFAULT_PURCHASE_DISCOUNT

OEM_COLOURS = {
    "Apple": "#1f77b4",
    "Samsung": "#2ca02c",
    "Google": "#d62728",
    "Motorola": "#9467bd",
    "Fairphone": "#17becf",
    "Nokia": "#8c564b",
    "Lenovo": "#e377c2",
    "Dell": "#7f7f7f",
    "HP": "#bcbd22",
    "Microsoft": "#ff7f0e",
}
GRADE_SYMBOL = {"A": "circle", "B": "diamond", "C": "square", "D": "x", "TRADEIN": "triangle-down"}


@st.cache_data(show_spinner=False)
def _anchors() -> pd.DataFrame:
    return load_anchors()


@st.cache_data(show_spinner=False)
def _curves(anchors: pd.DataFrame) -> pd.DataFrame:
    return fit_curves(anchors)


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * float(x):.0f} %"


def realisation_figure(anchors: pd.DataFrame, curves: pd.DataFrame, family: str) -> go.Figure:
    """Points: every anchor of the family (colour = manufacturer, symbol = grade). Line: grade-B marketplace curve."""
    fig = go.Figure()
    a = anchors[anchors["family"] == family]
    for oem, g in a.groupby("oem", sort=True):
        fig.add_trace(
            go.Scatter(
                x=g["age_months"],
                y=g["realisation"],
                mode="markers",
                name=str(oem),
                marker=dict(
                    color=OEM_COLOURS.get(str(oem), "#333333"),
                    symbol=[GRADE_SYMBOL.get(gr, "circle") for gr in g["grade"]],
                    size=9,
                    opacity=0.85,
                ),
                text=[f"{m} {s} | {c} | {p:.0f} EUR vs RRP {r:.0f}" for m, s, c, p, r in zip(
                    g["model_name"], g["spec_used"], g["condition"], g["price_eur"], g["rrp_eur_launch_de"])],
                hovertemplate="%{text}<br>age %{x:.1f} months<br>realisation %{y:.0%}<extra></extra>",
            )
        )
    for pop, dash, label in (("marketplace", "solid", "grade B, marketplace ask"), ("tradein", "dot", "trade-in bid")):
        c = curves[(curves["group_kind"] == "family") & (curves["group"] == family) & (curves["population"] == pop)]
        if c.empty or c.iloc[0]["slope_per_month"] is None or pd.isna(c.iloc[0]["slope_per_month"]):
            continue
        r = c.iloc[0]
        x = np.linspace(0, 48, 49)
        y = np.exp(float(r["intercept"]) + float(r["slope_per_month"]) * x)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=f"fit: {label} (n={int(r['n'])}, {r['fit_quality']})",
                                 line=dict(color="#333333", dash=dash, width=2)))
    for h in (12, 24, 36, 48):  # the contract terms of the simulation (config.TERM_MONTHS)
        fig.add_vline(x=h, line=dict(color="#bbbbbb", dash="dash"), annotation_text=f"{h} m", annotation_position="top")
    fig.update_layout(
        title=f"{family}: used price today / launch RRP, by model age",
        xaxis_title="months since German launch",
        yaxis_title="realisation (share of launch RRP)",
        yaxis=dict(tickformat=".0%", range=[0, 1.05]),
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=40, r=20, t=80, b=40),
    )
    return fig


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the realisation tab from the public anchors on disk (no database needed)."""
    anchors = _anchors()
    if anchors.empty:
        st.info("No public anchors on disk yet: data/catalogue/models.csv, variants.csv and data/anchors/used_prices.csv. "
                "Run `python -m restwert market` after adding them.")
        return
    curves = _curves(anchors)
    fam_rows = curves[(curves["group_kind"] == "family") & (curves["population"] == "marketplace")].set_index("group")

    st.subheader("Which device keeps what share of its price")
    st.caption(
        f"{len(anchors)} public price observations on {anchors['slug'].nunique()} models, "
        f"latest seen {pd.to_datetime(anchors['date_seen']).max().date().isoformat()}. "
        "Realisation = used price today / manufacturer RRP at German launch, gross EUR. Nothing here is a provider's number."
    )
    cols = st.columns(max(1, len(fam_rows)))
    for col, (fam, r) in zip(cols, fam_rows.iterrows()):
        col.metric(f"{fam}: grade B at 36 months", _pct(r["q_36"]), help=f"n={int(r['n'])}, ages {r['age_min']} to {r['age_max']} months, fit {r['fit_quality']}")
        col.caption(f"at 24 months {_pct(r['q_24'])}, about {_pct(r['monthly_depreciation_pct'])} per month")

    families = sorted(anchors["family"].unique())
    family = st.selectbox("Family", families, index=0)
    st.plotly_chart(realisation_figure(anchors, curves, family), width="stretch")

    st.markdown("**By manufacturer, grade B, marketplace ask**")
    oem = curves[(curves["group_kind"] == "family_oem") & (curves["population"] == "marketplace")].copy()
    oem = oem[oem["group"].str.startswith(family + " /")]
    show = pd.DataFrame({
        "manufacturer": oem["group"].str.split(" / ").str[-1],
        "n": oem["n"].astype(int),
        "age range": oem["age_min"].astype(str) + " to " + oem["age_max"].astype(str),
        "per month": oem["monthly_depreciation_pct"].map(_pct),
        "q(24)": oem["q_24"].map(_pct),
        "q(36)": oem["q_36"].map(_pct),
        "fit": oem["fit_quality"],
    })
    st.dataframe(show, hide_index=True, width="stretch")

    with st.expander("The anchors behind the numbers (one row per public price, with source)"):
        a = anchors[anchors["family"] == family].sort_values(["oem", "model_name", "age_months"])
        st.dataframe(
            a[["model_name", "spec_used", "condition", "grade", "age_months", "rrp_eur_launch_de", "price_eur",
               "realisation", "source_kind", "date_seen", "source_url"]],
            hide_index=True,
            width="stretch",
            column_config={
                "realisation": st.column_config.NumberColumn(format="%.0f %%", help="price / RRP"),
                "source_url": st.column_config.LinkColumn("source"),
                "age_months": st.column_config.NumberColumn(format="%.1f"),
            },
        )

    with st.expander("How it is computed"):
        st.markdown(
            "1. **Launch RRP** per model and storage from the manufacturer's German press release or trade press (URL and date per row).\n"
            "2. **Used price today** per model, storage and condition from public refurbished marketplaces (asks) and trade-in pages (bids), one row per observation.\n"
            "3. **Realisation** = used price / launch RRP of the same storage; both gross EUR, Germany. Condition labels are mapped to grades A to D; trade-in bids stay their own population.\n"
            "4. **Curve** per family and per manufacturer: ln(q) = a + b * age + grade offset, ordinary least squares, grade B as reference. Groups under 6 anchors or under 6 months of age spread get no curve; small or upward-sloping fits are labelled thin.\n"
            f"5. **Against purchase price**: the provider's buying price is not public. `purchase_discount_pct` in config/assumptions.yaml "
            f"(placeholder {DEFAULT_PURCHASE_DISCOUNT:.0%}) turns realisation vs RRP into realisation vs purchase price: q / (1 - discount).\n"
            "6. **Rental price** is not public and is not used on this page.\n\n"
            "Marketplace asks contain the refurbisher's margin; trade-in bids are a floor. A provider's own realisation lies between them and is measured in the fleet model, not here."
        )
