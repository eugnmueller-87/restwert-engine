"""Streamlit entry point: ``python -m restwert dashboard`` or
``streamlit run restwert/dashboard/app.py -- --db data/restwert.duckdb`` (SPEC 8.3, SPEC_v0.2 9.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Streamlit executes this file as a script, so the repository root is put on
``sys.path`` before the package imports.

v0.2 navigates two groups with ``st.navigation``: **Cycle** (Realisation, then
0 Data to 7 Contracts, one question per page) and **Engine** (the v0.1 tabs).
The sidebar keeps the DB path, ``as_of``, the KPI snapshots, the governance
sentence and the reload button, and carries the two headline tiles so they show
on every page.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from restwert import GOVERNANCE_PRINCIPLE, __version__  # noqa: E402
from restwert import db  # noqa: E402
from restwert.dashboard import charts, data  # noqa: E402
from restwert.dashboard.views import CYCLE_PAGES, ENGINE_PAGES  # noqa: E402
from restwert.paths import DEFAULT_DB  # noqa: E402

SYNTHETIC_BANNER = (
    "SYNTHETIC DATA: every number on this page comes from config/lake.yaml (v0.2) or config/generator.yaml (v0.1) "
    "design parameters. The catalogue and the anchor curves are public; no market benchmark, no real customer, "
    "supplier or employer. Owners are role placeholders."
)
FOOTER_NOTE = "On synthetic data the forecast error measures recovery of a synthetic curve, not market accuracy."
TOP_MARGIN = "KPI_TOP_LIFECYCLE_MARGIN"
TOP_ERROR = "KPI_TOP_RV_FORECAST_ERROR"


def _cli_db_default() -> str:
    """``--db`` passed after ``--`` by ``restwert dashboard``; else the default DB."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args, _ = parser.parse_known_args(sys.argv[1:])
    return str(args.db)


def _default_as_of(db_path: str) -> date:
    dates = data.available_as_of(db_path)
    if dates:
        return dates[0]
    try:
        from restwert.config import load_generator_config

        return load_generator_config().as_of
    except Exception:  # noqa: BLE001
        return date.today()


def _headline_tile(label: str, kpis, kpi_id: str) -> None:
    """One of the two v0.1 headline tiles; ``n/a`` when the row is absent."""
    row = charts.status_series_from_kpis(kpis, kpi_id)
    if row is None:
        st.metric(label=label, value="n/a")
        return
    target = row.get("target")
    try:
        target_f = None if target is None or target != target else float(target)
    except (TypeError, ValueError):
        target_f = None
    charts.kpi_tile(label, row, target_f)


def _page(title: str, url_path: str, view, db_path: str, as_of: date):
    """An ``st.Page`` whose function opens a connection, renders the view, and closes it."""

    def _run() -> None:
        con = db.connect(db_path)
        try:
            try:
                view.render(con, as_of)
            except Exception as exc:  # noqa: BLE001 - a broken page must not kill the app
                st.error(f"{type(exc).__name__}: {exc}")
                st.exception(exc)
        finally:
            con.close()

    _run.__name__ = f"page_{url_path.replace('-', '_')}"
    return st.Page(_run, title=title, url_path=url_path)


def main() -> None:
    """Build the page: sidebar with headline tiles, banner, navigation, footer."""
    st.set_page_config(page_title="Restwert Engine", layout="wide")

    with st.sidebar:
        st.title("Restwert Engine")
        st.caption(f"v{__version__}")
        db_path = st.text_input("DuckDB file", value=st.session_state.get("db_path", _cli_db_default()))
        if db_path != st.session_state.get("db_path"):
            st.session_state["db_path"] = db_path
            data.clear_cache()
        exists = Path(db_path).exists()
        if not exists:
            st.error(f"database not found: {db_path}. Run `python -m restwert all` first.")
        as_of_default = _default_as_of(db_path) if exists else date.today()
        as_of = st.date_input("as_of", value=as_of_default)
        avail = data.available_as_of(db_path) if exists else []
        if avail:
            st.caption("KPI snapshots: " + ", ".join(d.isoformat() for d in avail[:6]))
        st.markdown("---")
        kpis = data.kpi_values(db_path, as_of) if exists else None
        _headline_tile("Lifecycle margin per device", kpis, TOP_MARGIN)
        _headline_tile("Residual value forecast error", kpis, TOP_ERROR)
        st.markdown("---")
        st.markdown(f"**{GOVERNANCE_PRINCIPLE}**")
        st.caption(
            "Every decision record names its rule, the threshold value and the human who owns it. "
            "Model output is an advisory attached to the record, never the outcome."
        )
        if st.button("Reload data"):
            data.clear_cache()
            st.rerun()

    if not exists:
        st.stop()

    if data.is_synthetic(db_path):
        st.warning(SYNTHETIC_BANNER)
    st.title("Restwert Engine: asset P&L and residual value")
    st.caption(f"as_of {as_of.isoformat()}  |  {db_path}")

    pages = {
        "Cycle": [_page(title, url, view, db_path, as_of) for title, url, view in CYCLE_PAGES],
        "Engine": [_page(title, url, view, db_path, as_of) for title, url, view in ENGINE_PAGES],
    }
    pg = st.navigation(pages)
    pg.run()

    st.markdown("---")
    st.caption(f"{GOVERNANCE_PRINCIPLE}  |  {FOOTER_NOTE}")


main()
