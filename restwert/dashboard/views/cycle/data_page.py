"""0 Data: can we trust the numbers on the next seven pages? (SPEC_v0.2 9.3)

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Tiles: files ingested, bronze rows, unresolved rows (KPI_DATA_UNRESOLVED_SHARE),
serials with a complete chain (KPI_DATA_CHAIN_COMPLETE). Chart: ingestion per
feed. Table: ``gold.ingest_summary``. Folded: unresolved rows by reason with the
raw row, the chain quality heatmap, reconciliation failures, the source contracts
rendered from ``FEEDS``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from restwert.dashboard import charts, data
from restwert.dashboard.views.cycle import _common as c

QUESTION = "Can we trust the numbers on the next seven pages?"


def _feeds_table() -> pd.DataFrame:
    """The source contracts (one row per feed) from ``restwert.lake.feeds.FEEDS`` when present."""
    try:
        from restwert.lake.feeds import FEEDS
    except ImportError:
        return pd.DataFrame()
    specs = list(FEEDS.values()) if isinstance(FEEDS, dict) else list(FEEDS)
    rows = []
    for spec in specs:
        rows.append(
            {
                "feed": spec.key,
                "delivering system": spec.delivering_system,
                "bronze table": spec.bronze_table,
                "business key": ", ".join(spec.business_key),
                "serial column": spec.serial_column or "",
                "order column": spec.order_column or "",
                "required columns": ", ".join(col.name for col in spec.columns if col.required),
                "resolves against": ", ".join(str(r) for r in (spec.resolves or ())),
                "reference": bool(spec.is_reference),
            }
        )
    return pd.DataFrame(rows)


def render(con, as_of: date) -> None:  # noqa: ARG001 - con kept for the common view signature
    """Render the Data page."""
    db_path = c.db_path()
    summary = data.ingest_summary(db_path)
    deliveries = data.deliveries(db_path)
    if summary.empty and deliveries.empty:
        c.question(QUESTION)
        c.missing("bronze.deliveries and gold.ingest_summary")
        return

    kpis = data.gold_kpi_values(db_path, as_of)
    unresolved = data.unresolved(db_path)
    s = c.to_num(summary, ["n_files", "rows_read", "rows_new", "duplicates_identical", "duplicates_conflict", "n_unresolved", "bronze_rows"])

    c.question(QUESTION)
    st.caption(
        "Raw landing files never change; every row is typed, deduplicated and key-resolved into bronze, and a row "
        "that cannot be matched lands in bronze.unresolved with its reason. Nothing is guessed silently."
    )
    n_files = int(len(deliveries)) if not deliveries.empty else int(s["n_files"].sum())
    last = pd.to_datetime(deliveries["delivered_on"]).max().date().isoformat() if not deliveries.empty else "n/a"
    bronze_rows = int(s["bronze_rows"].sum()) if "bronze_rows" in s.columns else 0
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Landing files ingested", f"{n_files:,}")
        st.caption(f"last delivery {last}")
    with col2:
        st.metric("Bronze rows", f"{bronze_rows:,}")
        st.caption(f"{int(s['rows_read'].sum()):,} rows read, {int(s['duplicates_identical'].sum() + s['duplicates_conflict'].sum()):,} duplicates")
    with col3:
        c.gold_tile(kpis, "KPI_DATA_UNRESOLVED_SHARE", "Unresolved rows (share of rows read)", ratio_digits=2)
    with col4:
        c.gold_tile(kpis, "KPI_DATA_CHAIN_COMPLETE", "Serials with a complete timestamp chain")

    st.plotly_chart(charts.ingest_bars_figure(summary), width="stretch")

    st.markdown("**Ingestion summary per feed**")
    show_cols = [x for x in ("source_system", "feed", "delivering_system", "n_files", "last_delivered_on", "rows_read", "rows_new",
                             "duplicates_identical", "duplicates_conflict", "n_unresolved", "bronze_rows") if x in s.columns]
    c.table(s[show_cols])

    if not unresolved.empty:
        by_reason = unresolved.groupby(["feed", "reason_code"], as_index=False).size().rename(columns={"size": "rows"})
        n_conf = int((unresolved["reason_code"].astype(str) == "duplicate_conflict").sum()) if "reason_code" in unresolved.columns else 0
        note = (f"{len(unresolved):,} rows in bronze.unresolved: {len(unresolved) - n_conf:,} could not be typed or key-resolved "
                f"(the tile's numerator) and {n_conf:,} are conflicting duplicates (counted as duplicates, kept here for reading). "
                "A row re-delivered in a new file is listed once per delivery. The raw row travels in row_json so the "
                "source system can fix it")
        with st.expander(f"Unresolved rows by reason ({len(unresolved):,})", expanded=False):
            st.caption(note)
            st.dataframe(by_reason, hide_index=True, width="stretch")
            cols = [x for x in ("feed", "source_file", "row_number", "reason_code", "reason_text", "key_json", "row_json") if x in unresolved.columns]
            st.dataframe(unresolved[cols].head(500), hide_index=True, width="stretch")
    else:
        c.folded("Unresolved rows by reason (0)", pd.DataFrame(), note="every landing row was typed, matched and deduplicated")

    cq = data.chain_quality(db_path)
    with st.expander("Timestamp chain quality (status x step)", expanded=False):
        st.caption(
            "A rented device is complete with four steps, a sold device needs all ten. Expected steps per status are "
            "a pure table in docs/DATA_LAKE.md; a cell below 100 percent on an expected step is a finding."
        )
        st.plotly_chart(charts.chain_quality_heatmap(cq, c.timeline_steps()), width="stretch")

    recon = data.reconciliation_failures(db_path)
    c.folded(
        f"Reconciliation failures ledger vs device_pnl ({len(recon):,})",
        recon,
        note="silver.reconciliation compares the ledger sums with the v0.1 device_pnl to the cent; an empty table means both agree on every serial",
    )
    feeds = _feeds_table()
    c.folded("Source contracts (one row per feed, rendered from code)", feeds, note="the same table is written to docs/DATA_LAKE.md")
