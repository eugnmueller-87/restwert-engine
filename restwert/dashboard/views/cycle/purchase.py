"""1 Purchase: what did we pay per device against the net launch RRP, and to whom? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: units received (12 months), discount vs RRP (KPI_PUR_DISCOUNT_VS_RRP),
landed vs RRP (KPI_PUR_LANDED_VS_RRP), price protection claimed vs missed.
Chart: discount by manufacturer over purchase month. Table:
``gold.purchase_by_oem_month`` aggregated per manufacturer and supplier role.
Folded: per PO line, price changes inside the window, R07 queue rows.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "What did we pay per device against the net launch RRP, and to whom?"
MONEY = ["sum_rrp_net", "sum_unit_price", "sum_freight_duty", "sum_landed", "ppv_vs_po_eur", "pp_claimable_eur", "pp_credited_eur", "n_units"]


def _per_oem_role(p: pd.DataFrame) -> pd.DataFrame:
    g = p.groupby(["oem", "supplier_role"], as_index=False)[MONEY].sum()
    g["discount_vs_rrp_pct"] = 1.0 - g["sum_unit_price"] / g["sum_rrp_net"].replace(0, np.nan)
    g["landed_vs_rrp_pct"] = g["sum_landed"] / g["sum_rrp_net"].replace(0, np.nan)
    g["share_of_units"] = g["n_units"] / g["n_units"].sum() if g["n_units"].sum() else np.nan
    cols = ["oem", "supplier_role", "n_units", "share_of_units", "sum_rrp_net", "sum_unit_price", "discount_vs_rrp_pct",
            "sum_freight_duty", "sum_landed", "landed_vs_rrp_pct", "ppv_vs_po_eur", "pp_claimable_eur", "pp_credited_eur"]
    return c.round2(g[cols]).sort_values(["oem", "supplier_role"])


def _po_lines(dl: pd.DataFrame) -> pd.DataFrame:
    keep = [x for x in ("po_number", "po_line", "supplier_name", "supplier_role", "contract_ref", "oem", "model_name", "storage_gb",
                        "order_date", "rrp_net_eur", "purchase_price", "discount_vs_rrp_pct", "freight_eur", "duty_eur", "landed_cost",
                        "price_protection_status", "price_protection_claimable_eur", "price_protection_credit_eur") if x in dl.columns]
    d = c.to_num(dl[keep], ["rrp_net_eur", "purchase_price", "discount_vs_rrp_pct", "freight_eur", "duty_eur", "landed_cost",
                            "price_protection_claimable_eur", "price_protection_credit_eur"])
    agg = {k: "first" for k in keep if k not in ("po_number", "po_line")}
    for k in ("rrp_net_eur", "purchase_price", "freight_eur", "duty_eur", "landed_cost", "price_protection_claimable_eur", "price_protection_credit_eur"):
        if k in agg:
            agg[k] = "sum"
    agg["discount_vs_rrp_pct"] = "mean"
    g = d.groupby(["po_number", "po_line"], as_index=False).agg(units=("purchase_price", "size"), **{k: (k, v) for k, v in agg.items()})
    return c.round2(g)


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the Purchase page."""
    db_path = c.db_path()
    p = data.purchase_by_oem_month(db_path)
    dl = data.device_ledger(db_path)
    if p.empty and dl.empty:
        c.question(QUESTION)
        c.missing("gold.purchase_by_oem_month and silver.device_ledger")
        return
    kpis = data.gold_kpi_values(db_path, as_of)

    c.question(QUESTION)
    st.caption(
        "Catalogue RRP is gross; every amount here is net (rrp_net = rrp / (1 + vat_rate), owner CFO). "
        "Discount = 1 - unit price / net RRP, landed = unit price + freight + duty allocated to the cent per received unit."
    )
    family, oem = c.filter_row(dl, "purchase")
    dl_f = c.apply_filter(dl, family, oem)
    p_f = p if oem == "all" else p[p["oem"].astype(str) == oem]
    if family != "all" and not dl_f.empty and "oem" in dl_f.columns:
        p_f = p_f[p_f["oem"].isin(set(dl_f["oem"].astype(str)))]
    p_f = c.to_num(p_f, MONEY)

    recent = dl_f[c.trailing_mask(dl_f["received_at"], as_of)] if not dl_f.empty and "received_at" in dl_f.columns else dl_f
    recent = c.to_num(recent, ["price_protection_claimable_eur", "price_protection_credit_eur"])
    claimed = c.sum_or_none(recent["price_protection_credit_eur"]) if not recent.empty else None
    missed = None
    if not recent.empty and "price_protection_status" in recent.columns:
        missed = c.sum_or_none(recent.loc[recent["price_protection_status"].astype(str) == "missed", "price_protection_claimable_eur"]) or 0.0
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Units received (12 months)", f"{len(recent):,}")
        st.caption(f"{dl_f['po_number'].nunique() if 'po_number' in dl_f.columns else 0:,} purchase orders in the filter, all time")
    with col2:
        c.gold_tile(kpis, "KPI_PUR_DISCOUNT_VS_RRP", "Discount vs net RRP (12 months)")
    with col3:
        c.gold_tile(kpis, "KPI_PUR_LANDED_VS_RRP", "Landed cost vs net RRP (12 months)")
    with col4:
        st.metric("Price protection claimed vs missed (12 months)", f"{c.eur0(claimed)} / {c.eur0(missed)}")
        st.caption("credited by the supplier / claimable but not claimed inside the claim window")
        cap = c.gold_row(kpis, "KPI_PUR_PRICE_PROTECTION_CAPTURE")
        if cap is not None:
            if (cap.get("status") or "ok") == "ok" and not c.is_nan(cap.get("value")):
                st.caption(f"capture ratio {c.pct(cap.get('value'))} (n={int(cap.get('n') or 0)})")
            else:
                st.caption(f"capture ratio n/a: {str(cap.get('note') or 'not measurable')[:200]}")

    st.plotly_chart(charts.discount_by_oem_figure(p_f), width="stretch")

    st.markdown("**Purchases per manufacturer and supplier role**")
    if p_f.empty:
        st.caption("no rows")
    else:
        c.table(_per_oem_role(p_f))

    if not dl_f.empty:
        c.folded("Per PO line (grouped from silver.device_ledger)", _po_lines(dl_f))
        pc_cols = [x for x in ("po_number", "po_line", "oem", "model_name", "supplier_name", "received_at", "price_protection_days",
                               "price_protection_status", "price_protection_claimable_eur", "price_protection_credit_eur") if x in dl_f.columns]
        inside = dl_f[dl_f["price_protection_status"].astype(str).isin(["claimed", "open", "missed"])] if "price_protection_status" in dl_f.columns else pd.DataFrame()
        c.folded(
            f"Price changes inside the protection window ({len(inside):,} serials)",
            c.round2(inside[pc_cols]) if not inside.empty else pd.DataFrame(),
            note="status claimed = credit note exists, open = claim window still running, missed = window closed without a credit",
        )
    q = data.queue_for_rules(db_path, ("R07",))
    c.folded(
        f"R07 purchase discount floor: open queue rows ({len(q):,})",
        q,
        note="a PO line whose discount is below the manufacturer's floor (threshold purchase_discount_floor_pct, owner Head of Procurement)",
    )
