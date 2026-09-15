"""Shared helpers of the Cycle pages (SPEC_v0.2 9.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Every Cycle page follows one shape: one question (``question``), at most four
tiles (``tiles`` or ``gold_tile``), one chart above the fold, one table, and
everything else folded into collapsed expanders (``folded``). A page whose
tables are missing shows one ``st.info`` and nothing else (``missing``).

EUR without decimals in tiles, two decimals in tables. Every number that comes
from an estimate line names the assumption owner in a caption
(``estimate_caption``).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data

RUN_HINT = "run python -m restwert all"
MAX_TILES = 4
DEFAULT_TERMS_NOTE = (
    "synthetic placeholder terms; the counterparty list is public (manufacturers from the catalogue, "
    "every other party role-only); no term is from any provider"
)
# the 15 ledger line types in ledger order (restwert.ledger.lines.LEDGER_ORDER when module 3 is present)
DEFAULT_LEDGER_ORDER: tuple[str, ...] = (
    "purchase_price",
    "freight",
    "duty",
    "staging",
    "outbound_shipping",
    "rental_revenue",
    "support",
    "mdm_operations",
    "repair",
    "replacement_logistics",
    "return_logistics",
    "wipe_grading",
    "refurbishment",
    "holding_cost",
    "resale_gross",
    "channel_fee",
    "price_protection_credit",
)
DEFAULT_TIMELINE_STEPS: tuple[str, ...] = (
    "ordered_at",
    "received_at",
    "staged_at",
    "shipped_at",
    "returned_at",
    "wiped_at",
    "graded_at",
    "sellable_at",
    "sold_at",
    "credited_at",
)


def ledger_order() -> tuple[str, ...]:
    """``LEDGER_ORDER`` from the ledger package when present, else the spec list."""
    try:
        from restwert.ledger.lines import LEDGER_ORDER

        return tuple(LEDGER_ORDER)
    except ImportError:
        return DEFAULT_LEDGER_ORDER


def timeline_steps() -> tuple[str, ...]:
    """``TIMELINE_STEPS`` from enums when present (v0.2 foundation), else the spec list."""
    try:
        from restwert.enums import TIMELINE_STEPS

        return tuple(TIMELINE_STEPS)
    except ImportError:
        return DEFAULT_TIMELINE_STEPS


def terms_note() -> str:
    """``TERMS_NOTE`` of the counterparties module when present, else the spec sentence."""
    try:
        from restwert.contracts.counterparties import TERMS_NOTE

        return str(TERMS_NOTE)
    except ImportError:
        return DEFAULT_TERMS_NOTE


def question(text: str) -> None:
    """The one question of the page, as the only ``st.subheader``."""
    st.subheader(text)


def missing(what: str = "") -> None:
    """The one line a page shows when its tables are not there yet."""
    st.info(RUN_HINT if not what else f"{RUN_HINT} ({what} missing)")


def is_nan(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def eur0(v: Any) -> str:
    """EUR without decimals for tiles."""
    if is_nan(v):
        return "n/a"
    return f"{float(v):,.0f} EUR"


def pct(v: Any, digits: int = 1) -> str:
    if is_nan(v):
        return "n/a"
    return f"{float(v):.{digits}%}"


def num(v: Any) -> str:
    if is_nan(v):
        return "n/a"
    return f"{float(v):,.0f}"


def days(v: Any) -> str:
    if is_nan(v):
        return "n/a"
    return f"{float(v):.0f} days"


def tiles(items: Iterable[tuple[str, str] | tuple[str, str, str | None]]) -> None:
    """Up to four ``st.metric`` tiles in one row; an optional third element is a caption."""
    items = list(items)[:MAX_TILES]
    if not items:
        return
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        label, value = item[0], item[1]
        caption = item[2] if len(item) > 2 else None
        with col:
            st.metric(label=label, value=value)
            if caption:
                st.caption(caption)


def gold_row(kpis: pd.DataFrame, kpi_id: str) -> pd.Series | None:
    """One ``gold.kpi_values`` row or None."""
    return charts.status_series_from_kpis(kpis, kpi_id)


def gold_tile(kpis: pd.DataFrame, kpi_id: str, label: str, ratio_digits: int | None = None) -> None:
    """A gold KPI as a tile through ``charts.kpi_tile``; a placeholder when the row is absent.

    ``ratio_digits`` renders a ratio KPI with that many decimals (a share of a few
    rows in a hundred thousand would read 0.0 percent with the default one).
    """
    row = gold_row(kpis, kpi_id)
    if row is None:
        st.metric(label=label, value="n/a")
        st.caption(f"no gold.kpi_values row {kpi_id} for this as_of; run `python -m restwert kpis`")
        return
    target = row.get("target")
    status = row.get("status", "ok") or "ok"
    value = row.get("value")
    if status != "ok":
        # below min_n or not measurable: no headline number, the reason instead (the value stays in gold.kpi_values)
        st.metric(label=label, value="n/a")
        note = row.get("note")
        st.caption(f"not measurable: {str(note)[:240]}" if not is_nan(note) and str(note) else "not measurable")
        return
    if ratio_digits is not None and status == "ok" and not is_nan(value):
        st.metric(label=label, value=pct(value, ratio_digits))
        num, den, n = row.get("numerator"), row.get("denominator"), row.get("n")
        parts = []
        if not is_nan(num) and not is_nan(den):
            parts.append(f"{float(num):,.0f} / {float(den):,.0f}")
        parts.append(f"n={int(n) if not is_nan(n) else 0}")
        st.caption(" | ".join(parts))
        note = row.get("note")
        if not is_nan(note) and str(note):
            st.caption(str(note)[:240])
        return
    charts.kpi_tile(label, row, None if is_nan(target) else float(target))


def folded(label: str, frame: pd.DataFrame | None, *, note: str | None = None, max_rows: int = 2000, **kw: Any) -> None:
    """A collapsed expander holding one table (and an optional note above it)."""
    with st.expander(label, expanded=False):
        if note:
            st.caption(note)
        if frame is None or frame.empty:
            st.caption("no rows")
            return
        shown = frame.head(max_rows) if len(frame) > max_rows else frame
        if len(frame) > max_rows:
            st.caption(f"first {max_rows} of {len(frame):,} rows")
        st.dataframe(shown, hide_index=True, width="stretch", **kw)


def table(frame: pd.DataFrame | None, *, max_rows: int = 2000, **kw: Any) -> None:
    """The one table of the page."""
    if frame is None or frame.empty:
        st.caption("no rows")
        return
    shown = frame.head(max_rows) if len(frame) > max_rows else frame
    if len(frame) > max_rows:
        st.caption(f"first {max_rows} of {len(frame):,} rows")
    st.dataframe(shown, hide_index=True, width="stretch", **kw)


def estimate_caption(assumption_key: str, owner: str | None) -> None:
    """Names the owner of the assumption behind an estimate number."""
    st.caption(f"estimate: `{assumption_key}` from config/assumptions.yaml, owner {owner or 'not set'}")


def assumption_owner(key: str) -> str | None:
    """Owner of one assumption block, read from the shipped yaml (None when not readable)."""
    try:
        from restwert.config import load_assumptions

        return str(load_assumptions().owner(key))
    except Exception:  # noqa: BLE001 - a missing block only loses the caption
        return None


def threshold(key: str, as_of: date | None = None, sub: str | None = None) -> tuple[Any, str] | None:
    """``(value, owner)`` of one threshold from the shipped ``config/thresholds.yaml`` (None when not readable)."""
    try:
        from restwert.config import load_thresholds

        t = load_thresholds().get(key, sub, as_of=as_of)
        return t.value, str(t.owner)
    except Exception:  # noqa: BLE001 - a missing threshold only loses the tile's owned value
        return None


def filter_row(dl: pd.DataFrame, key: str) -> tuple[str, str]:
    """Catalogue family and manufacturer filter, both defaulting to ``all``."""
    fam_col = "catalogue_family" if "catalogue_family" in dl.columns else None
    oem_col = "oem" if "oem" in dl.columns else None
    fams = ["all"] + (sorted(dl[fam_col].dropna().astype(str).unique()) if fam_col else [])
    oems = ["all"] + (sorted(dl[oem_col].dropna().astype(str).unique()) if oem_col else [])
    c1, c2, _ = st.columns([1, 1, 3])
    family = c1.selectbox("catalogue family", fams, index=0, key=f"{key}_family")
    oem = c2.selectbox("manufacturer", oems, index=0, key=f"{key}_oem")
    return family, oem


def apply_filter(df: pd.DataFrame, family: str, oem: str) -> pd.DataFrame:
    """Rows matching the filter row (``all`` keeps everything)."""
    if df is None or df.empty:
        return df
    out = df
    if family != "all" and "catalogue_family" in out.columns:
        out = out[out["catalogue_family"].astype(str) == family]
    if oem != "all" and "oem" in out.columns:
        out = out[out["oem"].astype(str) == oem]
    return out


def to_num(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Numeric copies of the given columns (DuckDB DECIMAL arrives as ``Decimal`` objects)."""
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def trailing_mask(series: pd.Series, as_of: date, months: int = 12) -> pd.Series:
    """True where the date lies in ``(as_of - months, as_of]``."""
    d = pd.to_datetime(series, errors="coerce")
    end = pd.Timestamp(as_of)
    start = end - pd.DateOffset(months=months)
    return (d > start) & (d <= end)


def round2(df: pd.DataFrame) -> pd.DataFrame:
    """Two decimals on every float column for tables."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(2)
        elif out[c].dtype == object and len(out[c]) and any(type(v).__name__ == "Decimal" for v in out[c].head(5)):
            out[c] = pd.to_numeric(out[c], errors="coerce").round(2)
    return out


def safe_float(v: Any) -> float | None:
    if is_nan(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def sum_or_none(s: pd.Series) -> float | None:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() == 0:
        return None
    return float(np.nansum(x.to_numpy(dtype=float)))


def db_path() -> str:
    return data.current_db_path()
