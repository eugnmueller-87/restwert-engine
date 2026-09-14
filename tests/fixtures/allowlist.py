"""Allowed counterparty and supplier names (SPEC_v0.2 decision D17).

A name is allowed when it is one of the exact ``oem`` strings of
``data/catalogue/models.csv`` (the manufacturers, public through the catalogue)
or when it ends with ``" (role-only)"`` (reseller, carrier partner, refurbishment
and repair partner, logistics partner, marketplace channel, financing partner,
mobile threat defense partner). Anything else is an offender: the honesty rule
says no real DaaS provider and no real partner company name anywhere.

``MANUFACTURERS`` and ``ROLE_ONLY_SUFFIX`` come from ``restwert.lake.common``
when the lake package is present; before it lands the same values are held here
verbatim (spec 3.4), so the fixture works in every build state.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

_OEM_CODES_FALLBACK: dict[str, str] = {
    "Apple": "APL",
    "Samsung": "SAM",
    "Google": "GOO",
    "Motorola": "MOT",
    "Fairphone": "FPH",
    "HMD Global (Nokia)": "HMD",
    "Lenovo": "LEN",
    "Dell": "DEL",
    "HP": "HPI",
    "Microsoft": "MSF",
}

try:
    from restwert.lake.common import MANUFACTURERS, ROLE_ONLY_SUFFIX
except ImportError:  # the lake package has not landed in this checkout
    MANUFACTURERS: tuple[str, ...] = tuple(sorted(_OEM_CODES_FALLBACK))
    ROLE_ONLY_SUFFIX = " (role-only)"


def is_allowed(name: object) -> bool:
    """True for a catalogue manufacturer or a role-only name; False for anything else (NULL included)."""
    if name is None:
        return False
    try:
        if pd.isna(name):
            return False
    except (TypeError, ValueError):
        pass
    text = str(name).strip()
    if not text:
        return False
    return text in MANUFACTURERS or text.endswith(ROLE_ONLY_SUFFIX)


def scan_frame_names(df: pd.DataFrame, columns: Iterable[str]) -> list[str]:
    """Distinct offending values found in the given columns of ``df`` (missing columns are skipped)."""
    offenders: set[str] = set()
    if df is None or df.empty:
        return []
    for col in columns:
        if col not in df.columns:
            continue
        for value in df[col].dropna().unique():
            if not is_allowed(value):
                offenders.add(f"{col}={value!s}")
    return sorted(offenders)
