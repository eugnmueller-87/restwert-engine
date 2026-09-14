"""Forwarder: ``streamlit run restwert/app/streamlit_app.py [-- --db <path>]``.

Runs the same page as ``restwert/dashboard/app.py`` (the spec location). Streamlit
executes this file as a script, so the repository root goes on ``sys.path`` first.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import restwert.dashboard.app  # noqa: E402,F401  (builds the page on import)
