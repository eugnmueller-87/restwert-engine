"""Names that must never appear in this repository (SPEC honesty rules).

The list holds real Device-as-a-Service providers and former employers of the
author. They are stored base64-encoded so the plain names never appear in the
repository itself; ``DENYLIST`` decodes them at import time for the tests
(``test_generator`` scans generated frames, ``test_no_side_effects`` scans the
source tree). Matching is case-insensitive on word boundaries.

To add a name: ``python -c "import base64; print(base64.b64encode(b'name').decode())"``
and append the output here. Never write the plain name into this file.
"""

from __future__ import annotations

import base64
import re

_ENCODED: tuple[str, ...] = (
    "ZXZlcnBob25l",
    "Z3JvdmVy",
    "aG9meQ==",
    "d29ya3dpemU=",
    "Zmlyc3RiYXNl",
    "dGVhbXZpZXdlcg==",
    "c2NvdXQyNA==",
    "ZGVsaXZlcnkgaGVybw==",
    "Zm9vZHBhbmRh",
    "aGV5bmVy",
)

DENYLIST: list[str] = [base64.b64decode(x).decode("utf-8") for x in _ENCODED]

DENYLIST_PATTERN: re.Pattern[str] = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(n) for n in DENYLIST) + r")(?![a-z0-9])",
    flags=re.IGNORECASE,
)


def find_denylisted(text: str) -> list[str]:
    """Return the distinct denylisted names found in ``text`` (case-insensitive)."""
    return sorted({m.group(1).lower() for m in DENYLIST_PATTERN.finditer(text)})
