"""Plotly figure builders and the KPI tile (SPEC 8.3, SPEC_v0.2 9.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Pure functions frame -> ``go.Figure`` so they can be unit-tested without a
browser. ``kpi_tile`` is the one Streamlit-rendering helper here. The v0.2
figures (one per Cycle page) sit at the end of the file; every one of them
returns an "no data yet" placeholder on an empty frame instead of raising.
Estimate bars are hatched so a reader never mistakes a rate for a transaction.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

FAMILY_COLOURS = {
    "iphone_like": "#1f77b4",
    "android_like": "#2ca02c",
    "laptop_like": "#9467bd",
    "tablet_like": "#d62728",
    "*": "#333333",
}
CATALOGUE_FAMILY_COLOURS = {"Smartphone": "#1f77b4", "Tablet": "#d62728", "Laptop": "#9467bd"}
POSITIVE = "#2ca02c"
NEGATIVE = "#d62728"
ESTIMATE_PATTERN = "/"
_PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]
CHANNEL_COLOURS = {
    "employee_buyout": "#ff7f0e",
    "marketplace": "#1f77b4",
    "b2b_wholesale": "#2ca02c",
    "as_is": "#7f7f7f",
}
GREY = "#9a9a9a"


def _empty_figure(title: str, message: str = "no data yet") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title=title,
        annotations=[dict(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=320,
    )
    return fig


def forecast_error_figure(df: pd.DataFrame) -> go.Figure:
    """MAPE line plus bias bars per month for ``model_family = '*'``.

    ``df`` is ``rv_forecast_error_monthly``; rows of other families are ignored.
    Months with NULL metrics (fewer than 10 sales with a forecast) show as gaps.
    The bars and the solid line are the business view (marketplace baseline vs every
    channel); when the table carries the channel-adjusted columns a dashed line shows
    the model view (``mape_channel_adjusted``) and a marker series its bias, so a reader
    sees how much of the business error is channel mix.
    """
    if df is None or df.empty or "model_family" not in df.columns:
        return _empty_figure("Residual value forecast error")
    d = df[df["model_family"] == "*"].copy()
    if d.empty:
        d = df.copy()
    d["month"] = pd.to_datetime(d["month"])
    d = d.sort_values("month")
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=d["month"],
            y=d["bias"],
            name="bias (forecast vs realised)",
            marker_color=np.where(d["bias"].fillna(0) >= 0, "#d62728", "#2ca02c"),
            opacity=0.55,
            hovertemplate="%{x|%Y-%m}<br>bias %{y:.1%}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=d["month"],
            y=d["mape"],
            name="MAPE, business view (marketplace baseline vs all channels)",
            mode="lines+markers",
            line=dict(color="#1f77b4", width=2),
            hovertemplate="%{x|%Y-%m}<br>MAPE %{y:.1%}<extra></extra>",
            customdata=d[["n_with_forecast"]].to_numpy(),
        )
    )
    if "mape_channel_adjusted" in d.columns and d["mape_channel_adjusted"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=d["month"],
                y=d["mape_channel_adjusted"],
                name="MAPE, model view (channel-adjusted)",
                mode="lines",
                line=dict(color="#1f77b4", width=2, dash="dash"),
                hovertemplate="%{x|%Y-%m}<br>channel-adjusted MAPE %{y:.1%}<extra></extra>",
            )
        )
    if "bias_channel_adjusted" in d.columns and d["bias_channel_adjusted"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=d["month"],
                y=d["bias_channel_adjusted"],
                name="bias, model view (channel-adjusted; ADV02 tests this)",
                mode="markers",
                marker=dict(color="#7f7f7f", symbol="diamond", size=8),
                hovertemplate="%{x|%Y-%m}<br>channel-adjusted bias %{y:.1%}<extra></extra>",
            )
        )
    fig.update_layout(
        title="Residual value forecast error by month (all families)",
        yaxis=dict(title="ratio", tickformat=".0%", zeroline=True),
        xaxis=dict(title="month of sale"),
        barmode="overlay",
        legend=dict(orientation="h", y=1.1),
        height=380,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def rv_curve_figure(
    grid: pd.DataFrame, points: pd.DataFrame, family: str, grade: str, model: str | None = None
) -> go.Figure:
    """Forecast curve (line + 80 percent band) per model and realised scatter points.

    ``grid`` is ``rv_forecast_grid``; ``points`` comes from
    ``data.realised_curve_points``. Filtered to ``family`` and ``grade``; when
    ``model`` is given only that model's curve is drawn.
    """
    title = f"Residual value ratio vs months since launch: {family}, grade {grade}"
    fig = go.Figure()
    g = pd.DataFrame()
    if grid is not None and not grid.empty:
        g = grid[(grid["model_family"] == family) & (grid["grade"] == grade)]
        if model:
            g = g[g["model"] == model]
    p = pd.DataFrame()
    if points is not None and not points.empty:
        p = points[(points["model_family"] == family) & (points["grade"] == grade)]
        if model:
            p = p[p["model"] == model]
    if g.empty and p.empty:
        return _empty_figure(title)

    models = sorted(g["model"].unique()) if not g.empty else []
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
    for i, m in enumerate(models):
        gm = g[g["model"] == m].sort_values("months_since_launch")
        colour = palette[i % len(palette)]
        if "ratio_low" in gm.columns and gm["ratio_low"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=pd.concat([gm["months_since_launch"], gm["months_since_launch"][::-1]]),
                    y=pd.concat([gm["ratio_high"], gm["ratio_low"][::-1]]),
                    fill="toself",
                    fillcolor=colour,
                    opacity=0.12,
                    line=dict(width=0),
                    hoverinfo="skip",
                    showlegend=False,
                    name=f"{m} band",
                )
            )
        fig.add_trace(
            go.Scatter(
                x=gm["months_since_launch"],
                y=gm["forecast_rv_ratio"],
                mode="lines",
                name=f"{m} forecast",
                line=dict(color=colour, width=2),
                hovertemplate=f"{m}<br>month %{{x}}<br>ratio %{{y:.2f}}<extra></extra>",
            )
        )
    if not p.empty:
        for ch, pc in p.groupby("channel"):
            fig.add_trace(
                go.Scatter(
                    x=pc["months_since_launch"],
                    y=pc["rv_ratio"],
                    mode="markers",
                    name=f"realised {ch}",
                    marker=dict(color=CHANNEL_COLOURS.get(ch, GREY), size=6, opacity=0.6),
                    text=pc["model"],
                    hovertemplate="%{text}<br>month %{x:.1f}<br>ratio %{y:.2f}<extra></extra>",
                )
            )
    fig.update_layout(
        title=title,
        xaxis=dict(title="months since launch"),
        yaxis=dict(title="resale price / purchase price", rangemode="tozero"),
        height=460,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def aging_buckets(device_pnl: pd.DataFrame) -> pd.DataFrame:
    """Units and book value of in-stock devices in the buckets 0-90, 91-180, >180 days."""
    cols = ["bucket", "units", "book_value"]
    if device_pnl is None or device_pnl.empty or "lifecycle_status" not in device_pnl.columns:
        return pd.DataFrame(columns=cols)
    d = device_pnl[device_pnl["lifecycle_status"] == "in_stock"].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)
    days = pd.to_numeric(d["days_in_stock"], errors="coerce").fillna(0)
    bucket = pd.cut(days, bins=[-1, 90, 180, np.inf], labels=["0-90", "91-180", ">180"])
    bv = pd.to_numeric(d.get("book_value"), errors="coerce").fillna(0.0)
    out = (
        pd.DataFrame({"bucket": bucket, "book_value": bv})
        .groupby("bucket", observed=False)
        .agg(units=("book_value", "size"), book_value=("book_value", "sum"))
        .reset_index()
    )
    out["bucket"] = out["bucket"].astype(str)
    return out[cols]


def aging_figure(device_pnl: pd.DataFrame) -> go.Figure:
    """Bar chart of in-stock aging buckets in units (bars) and book value (line, right axis)."""
    b = aging_buckets(device_pnl)
    if b.empty:
        return _empty_figure("Inventory aging (in stock)")
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=b["bucket"],
            y=b["units"],
            name="units",
            marker_color=["#2ca02c", "#ff7f0e", "#d62728"],
            hovertemplate="%{x} days<br>%{y} units<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=b["bucket"],
            y=b["book_value"],
            name="book value EUR",
            mode="lines+markers",
            yaxis="y2",
            line=dict(color="#333333"),
            hovertemplate="%{x} days<br>%{y:,.0f} EUR<extra></extra>",
        )
    )
    fig.update_layout(
        title="Inventory aging (in stock): units and book value",
        xaxis=dict(title="days in stock"),
        yaxis=dict(title="units"),
        yaxis2=dict(title="book value EUR", overlaying="y", side="right", showgrid=False),
        height=360,
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    return fig


def wip_hist_figure(device_pnl: pd.DataFrame) -> go.Figure:
    """Histogram of ``days_in_wip`` for devices currently in WIP."""
    if device_pnl is None or device_pnl.empty or "days_in_wip" not in device_pnl.columns:
        return _empty_figure("WIP days (return to sellable)")
    d = device_pnl[device_pnl["lifecycle_status"] == "wip"]
    days = pd.to_numeric(d["days_in_wip"], errors="coerce").dropna()
    if days.empty:
        return _empty_figure("WIP days (return to sellable)", "no device in WIP at as_of")
    fig = go.Figure(
        go.Histogram(
            x=days,
            nbinsx=20,
            marker_color="#1f77b4",
            hovertemplate="%{x} days<br>%{y} devices<extra></extra>",
        )
    )
    fig.update_layout(
        title=f"WIP: days since return for {len(days)} devices in WIP",
        xaxis=dict(title="days in WIP"),
        yaxis=dict(title="devices"),
        height=340,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def renewal_calendar_figure(cal: pd.DataFrame) -> go.Figure:
    """Timeline: one horizontal bar per contract from notice deadline to end date.

    Colour: red when ``action_required``, blue otherwise; auto-renewal marked
    with a diamond at the end date.
    """
    if cal is None or cal.empty:
        return _empty_figure("Renewal calendar (next 6 months)")
    d = cal.copy()
    d["end_date"] = pd.to_datetime(d["end_date"])
    d["notice_deadline"] = pd.to_datetime(d["notice_deadline"])
    d["as_of"] = pd.to_datetime(d["as_of"])
    d = d.sort_values("end_date")
    d["label"] = d["contract_type"].astype(str) + " " + d["contract_id"].astype(str)
    if len(d) > 60:
        d = d.head(60)
    start = d["notice_deadline"].where(d["notice_deadline"].notna(), d["as_of"])
    start = start.where(start >= d["as_of"], d["as_of"])
    start = start.where(start <= d["end_date"], d["end_date"])
    width_ms = (d["end_date"] - start).dt.total_seconds() * 1000.0
    width_ms = width_ms.clip(lower=24 * 3600 * 1000.0)
    colours = np.where(d["action_required"].astype(bool), "#d62728", "#1f77b4")
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            base=start,
            x=width_ms,
            y=d["label"],
            orientation="h",
            marker_color=colours,
            name="notice window to end",
            customdata=np.stack(
                [
                    d["counterparty"].astype(str),
                    d["days_to_notice_deadline"].astype("float").fillna(np.nan),
                    d["days_to_end"].astype("float"),
                    pd.to_numeric(d["annual_value"], errors="coerce").fillna(0.0),
                ],
                axis=1,
            ),
            hovertemplate=(
                "%{y}<br>counterparty %{customdata[0]}<br>days to notice %{customdata[1]:.0f}"
                "<br>days to end %{customdata[2]:.0f}<br>annual value %{customdata[3]:,.0f} EUR<extra></extra>"
            ),
        )
    )
    auto = d[d["auto_renewal"].astype(bool)]
    if not auto.empty:
        fig.add_trace(
            go.Scatter(
                x=auto["end_date"],
                y=auto["label"],
                mode="markers",
                marker=dict(symbol="diamond", size=10, color="#ff7f0e"),
                name="auto-renewal",
                hoverinfo="skip",
            )
        )
    as_of = d["as_of"].iloc[0]
    fig.add_vline(x=as_of, line=dict(color="#333333", dash="dot"))
    fig.update_layout(
        title="Renewal calendar: notice deadline to contract end (next 6 months)",
        xaxis=dict(type="date", title="date"),
        yaxis=dict(autorange="reversed", title=""),
        height=max(320, 22 * len(d) + 120),
        legend=dict(orientation="h", y=1.05),
        margin=dict(l=160, r=20, t=60, b=40),
    )
    return fig


def _fmt_value(value: Any, unit: str | None) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    unit = (unit or "").lower()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit in ("ratio", "%", "pct", "share"):
        return f"{v:.1%}"
    if unit == "eur":
        return f"{v:,.0f} EUR"
    if unit in ("days", "weeks", "months"):
        return f"{v:.1f} {unit}"
    if unit == "count":
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def kpi_tile(label: str, kv: Any, target: float | None = None) -> None:
    """Render one KPI as a Streamlit metric; grey caption when not measurable.

    ``kv`` is a ``records.KpiValue`` or a ``kpi_values`` row (``pd.Series``).
    Reads ``value``, ``numerator``, ``denominator``, ``n``, ``status``, ``note``,
    ``unit`` when present.
    """
    import streamlit as st

    def _get(name: str, default: Any = None) -> Any:
        if isinstance(kv, pd.Series):
            return kv.get(name, default)
        return getattr(kv, name, default)

    value = _get("value")
    unit = _get("unit", "")
    status = _get("status", "ok") or "ok"
    n = _get("n", 0)
    num = _get("numerator")
    den = _get("denominator")
    note = _get("note", "") or ""
    ok = status == "ok"
    shown = _fmt_value(value, unit) if ok or value is not None else "n/a"
    delta = None
    if target is not None and value is not None and ok:
        try:
            gap = float(value) - float(target)
            delta = f"{gap:+.1%} vs target" if (unit or "").lower() == "ratio" else f"{gap:+,.1f} vs target"
        except (TypeError, ValueError):
            delta = None
    st.metric(label=label, value=shown if ok else f"({shown})", delta=delta, delta_color="off")
    parts = []
    if num is not None and den is not None and not (pd.isna(num) or pd.isna(den)):
        parts.append(f"{float(num):,.1f} / {float(den):,.1f}")
    parts.append(f"n={int(n) if n is not None and not pd.isna(n) else 0}")
    if target is not None:
        parts.append(f"target {_fmt_value(target, unit)}")
    if not ok:
        parts.append(f"status: {status}")
    st.caption(" | ".join(parts))
    if note:
        st.caption(str(note)[:240])


def status_series_from_kpis(df: pd.DataFrame, kpi_id: str) -> pd.Series | None:
    """Pick one KPI row from a ``kpi_values`` frame, or None."""
    if df is None or df.empty or "kpi_id" not in df.columns:
        return None
    rows = df[df["kpi_id"] == kpi_id]
    if rows.empty:
        return None
    return rows.iloc[0]


def as_date(value: Any) -> date | None:
    """Coerce a pandas / string date to ``datetime.date``."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return pd.Timestamp(value).date()


# --------------------------------------------------------------------------- v0.2 figures (one per Cycle page)


def _num(s: Any) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def ingest_bars_figure(summary: pd.DataFrame) -> go.Figure:
    """Data page: horizontal bars per feed of rows new, duplicates and unresolved (``gold.ingest_summary``)."""
    title = "Ingestion per feed: new rows, duplicates, unresolved"
    if summary is None or summary.empty or "feed" not in summary.columns:
        return _empty_figure(title)
    d = summary.copy()
    d["label"] = d["source_system"].astype(str) + "/" + d["feed"].astype(str)
    d = d.sort_values("label")
    dup = _num(d.get("duplicates_identical", 0)).fillna(0) + _num(d.get("duplicates_conflict", 0)).fillna(0)
    fig = go.Figure()
    fig.add_trace(go.Bar(y=d["label"], x=_num(d["rows_new"]).fillna(0), name="rows new", orientation="h", marker_color="#1f77b4"))
    fig.add_trace(go.Bar(y=d["label"], x=dup, name="duplicates (identical + conflict)", orientation="h", marker_color="#ff7f0e"))
    fig.add_trace(go.Bar(y=d["label"], x=_num(d["n_unresolved"]).fillna(0), name="unresolved", orientation="h", marker_color=NEGATIVE))
    fig.update_layout(
        title=title,
        barmode="stack",
        xaxis=dict(title="rows"),
        yaxis=dict(autorange="reversed", title=""),
        height=max(360, 20 * len(d) + 120),
        legend=dict(orientation="h", y=1.08),
        margin=dict(l=200, r=20, t=60, b=40),
    )
    return fig


def chain_quality_heatmap(cq: pd.DataFrame, step_order: tuple[str, ...] | None = None) -> go.Figure:
    """Data page (folded): share of serials with each timeline step, lifecycle status x step."""
    title = "Timestamp chain: share of serials with each step, per lifecycle status"
    if cq is None or cq.empty or "step" not in cq.columns:
        return _empty_figure(title)
    d = cq.copy()
    d["share_present"] = _num(d["share_present"])
    present = list(dict.fromkeys(d["step"].astype(str)))
    steps = [s for s in (step_order or ()) if s in present] + [s for s in present if s not in (step_order or ())]
    statuses = sorted(d["lifecycle_status"].astype(str).unique())
    z = d.pivot_table(index="lifecycle_status", columns="step", values="share_present", aggfunc="first").reindex(index=statuses, columns=steps)
    exp = d.pivot_table(index="lifecycle_status", columns="step", values="is_expected", aggfunc="first").reindex(index=statuses, columns=steps)
    text = np.where(exp.fillna(False).astype(bool).to_numpy(), "expected", "not expected")
    fig = go.Figure(
        go.Heatmap(
            z=z.to_numpy(),
            x=steps,
            y=statuses,
            colorscale="Blues",
            zmin=0,
            zmax=1,
            text=text,
            hovertemplate="%{y} / %{x}<br>share present %{z:.0%}<br>%{text}<extra></extra>",
            colorbar=dict(title="share", tickformat=".0%"),
        )
    )
    fig.update_layout(title=title, height=360, margin=dict(l=120, r=20, t=60, b=80), xaxis=dict(tickangle=-30))
    return fig


def discount_by_oem_figure(p: pd.DataFrame) -> go.Figure:
    """Purchase page: discount vs net launch RRP by manufacturer over purchase month (``gold.purchase_by_oem_month``)."""
    title = "Discount vs net launch RRP by manufacturer and purchase month (unit-weighted, both routes)"
    if p is None or p.empty or "purchase_month" not in p.columns:
        return _empty_figure(title)
    d = p.copy()
    d["purchase_month"] = pd.to_datetime(d["purchase_month"])
    for c in ("sum_rrp_net", "sum_unit_price", "n_units"):
        d[c] = _num(d[c]).fillna(0.0)
    g = d.groupby(["oem", "purchase_month"], as_index=False).agg(
        sum_rrp_net=("sum_rrp_net", "sum"), sum_unit_price=("sum_unit_price", "sum"), n_units=("n_units", "sum")
    )
    g["discount"] = 1.0 - g["sum_unit_price"] / g["sum_rrp_net"].replace(0, np.nan)
    fig = go.Figure()
    for i, (oem, go_) in enumerate(g.groupby("oem", sort=True)):
        go_ = go_.sort_values("purchase_month")
        fig.add_trace(
            go.Scatter(
                x=go_["purchase_month"],
                y=go_["discount"],
                mode="lines+markers",
                name=str(oem),
                line=dict(color=_PALETTE[i % len(_PALETTE)]),
                customdata=go_[["n_units"]].to_numpy(),
                hovertemplate=f"{oem}<br>%{{x|%Y-%m}}<br>discount %{{y:.1%}}<br>units %{{customdata[0]:.0f}}<extra></extra>",
            )
        )
    fig.update_layout(
        title=title,
        xaxis=dict(title="purchase month"),
        yaxis=dict(title="discount vs net RRP", tickformat=".0%", rangemode="tozero"),
        height=400,
        legend=dict(orientation="h", y=-0.25),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def tco_stack_figure(tco: pd.DataFrame, cohort_kind: str, line_order: tuple[str, ...]) -> go.Figure:
    """TCO page: stacked bar per cohort of the mean line magnitude per line type; estimate lines hatched."""
    title = f"Mean cost per closed device by {cohort_kind}, line by line (hatched = estimate)"
    if tco is None or tco.empty or "cohort_kind" not in tco.columns:
        return _empty_figure(title)
    d = tco[tco["cohort_kind"] == cohort_kind].copy()
    if d.empty:
        return _empty_figure(title, f"no rows for cohort kind {cohort_kind}")
    d["mean_eur"] = _num(d["mean_eur"]).fillna(0.0)
    d["cohort_value"] = d["cohort_value"].astype(str)
    cohorts = sorted(d["cohort_value"].unique())
    present = set(d["line_type"].astype(str))
    types = [t for t in line_order if t in present] + sorted(present - set(line_order))
    fig = go.Figure()
    for i, lt in enumerate(types):
        dl = d[d["line_type"] == lt].set_index("cohort_value")
        y = [float(dl["mean_eur"].get(c, 0.0)) for c in cohorts]
        est = bool(dl["is_estimate"].astype(bool).any()) if "is_estimate" in dl.columns else False
        n = [int(_num(dl["n_devices"]).get(c, 0) or 0) if "n_devices" in dl.columns else 0 for c in cohorts]
        fig.add_trace(
            go.Bar(
                x=cohorts,
                y=y,
                name=lt + (" (estimate)" if est else ""),
                marker=dict(color=_PALETTE[i % len(_PALETTE)], pattern_shape=ESTIMATE_PATTERN if est else ""),
                customdata=np.array(n),
                hovertemplate=f"{lt}<br>%{{x}}<br>%{{y:,.2f}} EUR per device<br>n=%{{customdata}}<extra></extra>",
            )
        )
    fig.update_layout(
        title=title,
        barmode="stack",
        xaxis=dict(title=cohort_kind),
        yaxis=dict(title="EUR per closed device"),
        height=440,
        legend=dict(orientation="h", y=-0.3),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def estimate_vs_anchor_figure(e: pd.DataFrame) -> go.Figure:
    """Residual page: per (family, oem) the fleet estimate (decides) and the anchor (advises, hatched) as share of RRP."""
    title = "Residual value at lease end as share of net RRP: fleet model (decides) vs public anchor (advises)"
    if e is None or e.empty or "oem" not in e.columns:
        return _empty_figure(title)
    d = e.copy()
    d["label"] = d["catalogue_family"].astype(str) + " / " + d["oem"].astype(str)
    d = d.sort_values(["catalogue_family", "oem"])
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=d["label"],
            y=_num(d["mean_estimate_ratio"]),
            name="fleet model estimate (decides)",
            marker_color="#1f77b4",
            customdata=_num(d["n_rented"]).fillna(0).to_numpy(),
            hovertemplate="%{x}<br>estimate %{y:.1%} of net RRP<br>rented serials %{customdata:.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=d["label"],
            y=_num(d["mean_anchor_ratio"]),
            name="public anchor, ask = upper bound (advises)",
            marker=dict(color="#9a9a9a", pattern_shape=ESTIMATE_PATTERN),
            customdata=np.stack([_num(d["n_with_anchor"]).fillna(0), d["anchor_fit_quality"].astype(str)], axis=1),
            hovertemplate="%{x}<br>anchor %{y:.1%} of net RRP<br>with anchor %{customdata[0]:.0f}<br>fit %{customdata[1]}<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        barmode="group",
        yaxis=dict(title="share of net RRP", tickformat=".0%", rangemode="tozero"),
        xaxis=dict(tickangle=-30),
        height=440,
        legend=dict(orientation="h", y=1.08),
        margin=dict(l=40, r=20, t=80, b=100),
    )
    return fig


def realised_vs_record_scatter(sold: pd.DataFrame) -> go.Figure:
    """Resale page: realised gross price vs the estimate of record at return, coloured by channel, identity line."""
    title = "Realised gross resale price vs the estimate on record at return"
    if sold is None or sold.empty or "resale_gross" not in sold.columns:
        return _empty_figure(title)
    d = sold.copy()
    d["x"] = _num(d["estimate_rv_of_record"])
    d["y"] = _num(d["resale_gross"])
    d = d.dropna(subset=["x", "y"])
    if d.empty:
        return _empty_figure(title, "no sale with an estimate of record")
    if len(d) > 4000:
        d = d.sample(4000, random_state=0)
    name_col = "model_name" if "model_name" in d.columns else "slug"
    fig = go.Figure()
    for ch, dc in d.groupby("resale_channel", sort=True):
        fig.add_trace(
            go.Scatter(
                x=dc["x"],
                y=dc["y"],
                mode="markers",
                name=str(ch),
                marker=dict(color=CHANNEL_COLOURS.get(str(ch), GREY), size=6, opacity=0.55),
                text=dc["serial"].astype(str) + " " + dc[name_col].astype(str),
                hovertemplate="%{text}<br>record %{x:,.0f} EUR<br>realised %{y:,.0f} EUR<extra></extra>",
            )
        )
    top = float(max(d["x"].max(), d["y"].max()))
    fig.add_trace(go.Scatter(x=[0, top], y=[0, top], mode="lines", name="identity (realised = record)", line=dict(color="#333333", dash="dot")))
    fig.update_layout(
        title=title,
        xaxis=dict(title="estimate of record at return, EUR"),
        yaxis=dict(title="realised gross, EUR"),
        height=440,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def result_by_cohort_figure(r: pd.DataFrame, kind: str) -> go.Figure:
    """Result page: mean closed result per device by cohort, coloured by sign, n in the hover."""
    title = f"Mean closed lifecycle result per device by {kind}"
    if r is None or r.empty or "cohort_value" not in r.columns:
        return _empty_figure(title)
    d = r.copy()
    d["mean"] = _num(d["mean_result_closed"])
    d = d.dropna(subset=["mean"]).sort_values("cohort_value")
    if d.empty:
        return _empty_figure(title, "no closed device in this cohort kind")
    fig = go.Figure(
        go.Bar(
            x=d["cohort_value"].astype(str),
            y=d["mean"],
            marker_color=np.where(d["mean"] >= 0, POSITIVE, NEGATIVE),
            customdata=np.stack([_num(d["n_closed"]).fillna(0), _num(d["n_open"]).fillna(0)], axis=1),
            hovertemplate="%{x}<br>mean closed result %{y:,.0f} EUR<br>closed n=%{customdata[0]:.0f} | open n=%{customdata[1]:.0f} (not in this bar)<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        xaxis=dict(title=kind, tickangle=-30 if len(d) > 8 else 0),
        yaxis=dict(title="EUR per closed device", zeroline=True),
        height=420,
        margin=dict(l=40, r=20, t=60, b=80),
    )
    return fig


def levers_bar_figure(s: pd.DataFrame) -> go.Figure:
    """Levers page: horizontal bar of EUR per year on the fleet per lever; additive levers in one colour."""
    title = "Where to tighten: EUR per year on the fleet, per lever (levers do not add up)"
    if s is None or s.empty or "lever_id" not in s.columns:
        return _empty_figure(title)
    d = s.copy()
    d["eur"] = _num(d["eur_fleet_per_year"]).fillna(0.0)
    d = d.sort_values("eur", ascending=True)
    additive = d["additive"].astype(bool) if "additive" in d.columns else pd.Series(False, index=d.index)
    label = d["lever_id"].astype(str) + " " + d["lever_name"].astype(str)
    fig = go.Figure(
        go.Bar(
            y=label,
            x=d["eur"],
            orientation="h",
            marker_color=np.where(additive, "#1f77b4", "#ff7f0e"),
            customdata=np.stack(
                [_num(d["eur_per_device"]).fillna(0), _num(d["n_attributed"]).fillna(0), d["threshold_owner"].astype(str)], axis=1
            ),
            hovertemplate="%{y}<br>%{x:,.0f} EUR per year<br>%{customdata[0]:,.0f} EUR per device, n=%{customdata[1]:.0f}<br>owner %{customdata[2]}<extra></extra>",
        )
    )
    fig.add_annotation(
        text="blue = additive (purchase side), orange = reference against a fleet row, not additive",
        x=0, y=1.08, xref="paper", yref="paper", showarrow=False, xanchor="left",
    )
    fig.update_layout(
        title=title,
        xaxis=dict(title="EUR per year (trailing 12 months)"),
        yaxis=dict(title=""),
        height=max(320, 40 * len(d) + 140),
        margin=dict(l=220, r=20, t=80, b=40),
    )
    return fig


def levers_heatmap_figure(lc: pd.DataFrame, kind: str = "oem") -> go.Figure:
    """Levers page (folded): mean delta per device, lever x cohort value of one cohort kind."""
    title = f"Mean lever delta per device by {kind}"
    if lc is None or lc.empty or "cohort_kind" not in lc.columns:
        return _empty_figure(title)
    d = lc[lc["cohort_kind"] == kind].copy()
    if d.empty:
        return _empty_figure(title, f"no rows for cohort kind {kind}")
    d["mean_delta_eur"] = _num(d["mean_delta_eur"])
    z = d.pivot_table(index="lever_id", columns="cohort_value", values="mean_delta_eur", aggfunc="first")
    fig = go.Figure(
        go.Heatmap(
            z=z.to_numpy(),
            x=[str(c) for c in z.columns],
            y=[str(i) for i in z.index],
            colorscale="RdBu_r",
            zmid=0,
            hovertemplate="%{y} / %{x}<br>%{z:,.0f} EUR per device<extra></extra>",
            colorbar=dict(title="EUR"),
        )
    )
    fig.update_layout(title=title, height=360, margin=dict(l=80, r=20, t=60, b=80), xaxis=dict(tickangle=-30))
    return fig


def coverage_bar_figure(c: pd.DataFrame) -> go.Figure:
    """Contracts page: purchase spend under a contract in force, per manufacturer (trailing 12 months)."""
    title = "Purchase spend under a contract in force, per manufacturer (trailing 12 months)"
    if c is None or c.empty or "oem" not in c.columns:
        return _empty_figure(title)
    d = c.copy()
    d["coverage_pct"] = _num(d["coverage_pct"])
    d["spend_total"] = _num(d["spend_total"]).fillna(0.0)
    d = d.sort_values("spend_total", ascending=False)
    fig = go.Figure(
        go.Bar(
            x=d["oem"].astype(str),
            y=d["coverage_pct"],
            marker_color=np.where(d["coverage_pct"].fillna(0) >= 0.8, POSITIVE, "#ff7f0e"),
            customdata=np.stack(
                [d["spend_total"], _num(d["spend_under_contract"]).fillna(0), _num(d["n_contracts_in_force"]).fillna(0)], axis=1
            ),
            hovertemplate="%{x}<br>coverage %{y:.0%}<br>spend %{customdata[0]:,.0f} EUR, under contract %{customdata[1]:,.0f} EUR<br>contracts in force %{customdata[2]:.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        yaxis=dict(title="share of spend under contract", tickformat=".0%", range=[0, 1.05]),
        xaxis=dict(title=""),
        height=380,
        margin=dict(l=40, r=20, t=60, b=60),
    )
    return fig
