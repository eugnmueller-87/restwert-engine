"""Export tab: buttons that write CSV / parquet plus manifest.json to outputs/.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The buttons call ``export.export_all`` and ``export.write_manifest``, the same
functions as ``python -m restwert export``. Files are written to the local
``outputs/`` folder (or the path typed in the box); nothing leaves the machine.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from restwert.dashboard import data
from restwert.export import EXPORT_TABLES, export_all, write_manifest
from restwert.paths import OUTPUTS_DIR


def _run_export(con, out_dir: Path, fmt: str) -> list[Path]:
    paths = export_all(con, out_dir, fmt)  # type: ignore[arg-type]
    paths.append(write_manifest(out_dir, con, None))
    return paths


def render(con, as_of: date) -> None:  # noqa: ARG001
    """Render the export tab."""
    db_path = data.current_db_path()
    st.caption(
        "Writes one file per derived table for Power BI. Tables: " + ", ".join(EXPORT_TABLES)
    )
    out_text = st.text_input("Output folder", value=str(OUTPUTS_DIR))
    out_dir = Path(out_text)

    counts = data.table_counts(db_path, EXPORT_TABLES)
    missing = [t for t, n in counts.items() if n is None]
    if missing:
        st.warning("Not yet in the database (will be skipped): " + ", ".join(missing))

    c1, c2, c3 = st.columns(3)
    fmt = None
    if c1.button("Export CSV"):
        fmt = "csv"
    if c2.button("Export parquet"):
        fmt = "parquet"
    if c3.button("Export both"):
        fmt = "both"
    if fmt:
        try:
            paths = _run_export(con, out_dir, fmt)
            st.success(f"wrote {len(paths)} files to {out_dir}")
            st.code("\n".join(str(p) for p in paths))
        except Exception as exc:  # noqa: BLE001
            st.error(f"export failed: {type(exc).__name__}: {exc}")

    st.subheader("Row counts")
    st.dataframe(
        {"table": list(counts.keys()), "rows": [("missing" if n is None else n) for n in counts.values()]},
        hide_index=True,
        width="stretch",
    )
