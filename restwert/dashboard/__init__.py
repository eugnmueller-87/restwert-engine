"""Streamlit dashboard for the Restwert Engine (SPEC section 8.3, module 6).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

One app with tabs (``app.py`` + ``views/``), cached readers in ``data.py``,
plotly figure builders in ``charts.py``. The dashboard reads DuckDB tables and
writes nothing except the two explicit actions: "Recompute decisions" (same
call as ``python -m restwert decide``) and the export buttons (same call as
``python -m restwert export``).
"""
