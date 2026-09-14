"""Run the public-anchor analysis: anchors, curves, summary.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Outputs (``outputs/``):

* ``market_anchors.csv``   every used-price observation with RRP, age and realisation
* ``market_curves.csv``    fitted curves per family and per family/oem, two populations
* ``market_summary.md``    the answer to "where do we land", written from the numbers only

The purchase price of a DaaS provider is not public. ``purchase_discount_pct`` in
``config/assumptions.yaml`` is therefore a labelled placeholder; realisation against
purchase price is reported as realisation / (1 - discount) and says so.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from restwert.market.anchors import ANCHORS_DIR, CATALOGUE_DIR, load_anchors
from restwert.market.curves import fit_curves
from restwert.paths import OUTPUTS_DIR

DEFAULT_PURCHASE_DISCOUNT = 0.12  # placeholder, see config/assumptions.yaml block purchase_discount_pct


def _discount(assumptions) -> tuple[float, str]:
    """Placeholder purchase discount and its owner; the default is used when the block is absent."""
    if assumptions is None:
        return DEFAULT_PURCHASE_DISCOUNT, "placeholder default (no assumptions loaded)"
    try:
        block = assumptions.blocks["purchase_discount_pct"]
        return float(block.value), str(block.owner)
    except (KeyError, AttributeError, TypeError, ValueError):
        return DEFAULT_PURCHASE_DISCOUNT, "placeholder default (block missing)"


def _pct(x) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * float(x):.1f} %"


def write_summary(anchors: pd.DataFrame, curves: pd.DataFrame, discount: float, owner: str, as_of: date, path: Path) -> None:
    """A short markdown answer built only from the two frames; no number is typed by hand."""
    lines: list[str] = []
    lines.append("# Where do we land: residual value realisation from public anchors")
    lines.append("")
    lines.append(f"As of {as_of.isoformat()}. Every anchor is a public price with a URL and the day it was seen. "
                 "Realisation = used price today / manufacturer RRP at launch, both gross EUR, Germany.")
    lines.append("")
    if anchors.empty:
        lines.append("No anchors loaded. Put models.csv and variants.csv under data/catalogue and used_prices.csv under data/anchors.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    dropped = anchors.attrs.get("dropped", {})
    lines.append(f"Anchors used: {len(anchors)} across {anchors['slug'].nunique()} models, "
                 f"{anchors['family'].nunique()} families, {anchors['oem'].nunique()} manufacturers. "
                 f"Dropped: {sum(dropped.values())} ({', '.join(f'{k} {v}' for k, v in dropped.items() if v)}).")
    lines.append("")
    lines.append("## Realisation at 24, 36 and 48 months, grade B, marketplace ask (upper bound)")
    lines.append("")
    lines.append("| Group | n | age range (months) | per month | q(24) | q(36) | q(48) | q(36) vs purchase* | fit |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    mk = curves[(curves["population"] == "marketplace") & (curves["group_kind"].isin(["family", "family_oem"]))]
    for _, r in mk.iterrows():
        q36 = r["q_36"]
        vs_purchase = None if q36 is None or pd.isna(q36) else float(q36) / (1.0 - discount)
        lines.append(
            f"| {r['group']} | {int(r['n'])} | {r['age_min']} to {r['age_max']} | {_pct(r['monthly_depreciation_pct'])} | "
            f"{_pct(r['q_24'])} | {_pct(q36)} | {_pct(r['q_48'])} | {_pct(vs_purchase)} | {r['fit_quality']} |"
        )
    lines.append("")
    lines.append(f"*Purchase price assumed as RRP minus {100 * discount:.0f} % (placeholder, owner: {owner}). "
                 "Not a fact about any provider; change it in config/assumptions.yaml.")
    lines.append("")
    tr = curves[(curves["population"] == "tradein") & (curves["group_kind"].isin(["family", "family_oem"]))]
    if not tr.empty:
        lines.append("## The same, buy-back and trade-in bids (lower bound, what a buyer pays)")
        lines.append("")
        lines.append("| Group | n | age range (months) | per month | q(24) | q(36) | q(48) | fit |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for _, r in tr.iterrows():
            lines.append(
                f"| {r['group']} | {int(r['n'])} | {r['age_min']} to {r['age_max']} | {_pct(r['monthly_depreciation_pct'])} | "
                f"{_pct(r['q_24'])} | {_pct(r['q_36'])} | {_pct(r['q_48'])} | {r['fit_quality']} |"
            )
        lines.append("")
    # spread between populations where both exist per family
    lines.append("## What the numbers say")
    lines.append("")
    fam_mk = mk[mk["group_kind"] == "family"].set_index("group")
    fam_tr = tr[tr["group_kind"] == "family"].set_index("group") if not tr.empty else pd.DataFrame()
    for fam in fam_mk.index:
        q24 = fam_mk.loc[fam, "q_24"]
        q36 = fam_mk.loc[fam, "q_36"]
        q48 = fam_mk.loc[fam, "q_48"]
        if q24 is None or pd.isna(q24):
            lines.append(f"* {fam}: {int(fam_mk.loc[fam, 'n'])} anchors, no curve ({fam_mk.loc[fam, 'fit_quality']}). More anchors needed before any number is quoted.")
            continue
        msg = (f"* {fam}: a grade-B device lists at about {_pct(q24)} of launch RRP after 24 months, {_pct(q36)} after 36 "
               f"and {_pct(q48)} after 48 (a point outside the group's age range is an extrapolation of the fit)")
        if not fam_tr.empty and fam in fam_tr.index and fam_tr.loc[fam, "q_36"] is not None and not pd.isna(fam_tr.loc[fam, "q_36"]):
            gap = float(q36) - float(fam_tr.loc[fam, "q_36"])
            msg += f"; a buyer bids {_pct(fam_tr.loc[fam, 'q_36'])} at 36 months, so the channel decides {_pct(gap)} of RRP per device"
        lines.append(msg + ".")
    oem_mk = mk[(mk["group_kind"] == "family_oem") & (mk["fit_quality"] == "ok")].dropna(subset=["q_36"]).copy()
    oem_mk["family"] = oem_mk["group"].str.split(" / ").str[0]
    for fam, g in oem_mk.groupby("family", sort=True):
        if len(g) < 2:
            continue
        best = g.sort_values("q_36", ascending=False).iloc[0]
        worst = g.sort_values("q_36", ascending=True).iloc[0]
        lines.append(f"* {fam}, by manufacturer at 36 months (grade B, sound fits only): best {best['group'].split(' / ')[-1]} "
                     f"({_pct(best['q_36'])}, n={int(best['n'])}), weakest {worst['group'].split(' / ')[-1]} "
                     f"({_pct(worst['q_36'])}, n={int(worst['n'])}). "
                     "The manufacturer mix of the fleet is a residual value decision, not only a purchase price decision.")
    thin = curves[(curves["fit_quality"] != "ok") & (curves["group_kind"] == "family_oem")]
    if not thin.empty:
        lines.append(f"* Thin or missing curves: {', '.join(sorted(set(thin['group'] + ' (' + thin['population'] + ')')))}. "
                     "These groups get more anchors before they get a forecast.")
    lines.append("")
    lines.append("## Limits, stated")
    lines.append("")
    lines.append("* Marketplace asks include the refurbisher's margin and VAT; a provider selling B2B realises less. Trade-in bids are the floor. The truth for a given provider lies between the two and is only known from its own sales.")
    lines.append("* Age is model age (months since German launch), not device age; a device bought six months after launch and returned after 24 months is 30 months old on this curve.")
    lines.append("* Anchors are one day's snapshot. The fleet model in restwert.forecast learns from realised sales over time; this page is the public sanity check for its level, not a replacement.")
    lines.append("* The rental price is not public and is not used here. Lifecycle margin needs it, so the margin is shown as a function of the rental rate elsewhere, never as one number.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_market(
    out_dir: Path = OUTPUTS_DIR,
    catalogue_dir: Path = CATALOGUE_DIR,
    anchors_dir: Path = ANCHORS_DIR,
    assumptions=None,
    as_of: date | None = None,
) -> dict[str, int | float | str]:
    """Load anchors, fit curves, write the three outputs; returns counts for the CLI table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = as_of or date.today()
    anchors = load_anchors(catalogue_dir, anchors_dir)
    curves = fit_curves(anchors)
    discount, owner = _discount(assumptions)
    anchors_out = anchors.copy()
    if not anchors_out.empty:
        anchors_out["realisation_vs_purchase_placeholder"] = (anchors_out["realisation"] / (1.0 - discount)).round(4)
    anchors_out.to_csv(out_dir / "market_anchors.csv", index=False, encoding="utf-8")
    curves.to_csv(out_dir / "market_curves.csv", index=False, encoding="utf-8")
    write_summary(anchors, curves, discount, owner, as_of, out_dir / "market_summary.md")
    fam = curves[(curves["group_kind"] == "family") & (curves["population"] == "marketplace")]
    return {
        "anchors": int(len(anchors)),
        "models_with_anchor": int(anchors["slug"].nunique()) if not anchors.empty else 0,
        "curves": int(len(curves)),
        "curves_ok": int((curves["fit_quality"] == "ok").sum()) if not curves.empty else 0,
        "families_fitted": int(fam["q_36"].notna().sum()) if not fam.empty else 0,
        "dropped": int(sum(anchors.attrs.get("dropped", {}).values())),
    }


__all__ = ["run_market", "write_summary", "DEFAULT_PURCHASE_DISCOUNT"]
