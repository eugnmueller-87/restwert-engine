"""Data lake package of the Restwert Engine v0.2 (SPEC_v0.2 section 4).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``common``: pure helpers shared by every v0.2 module (no I/O, no DuckDB).
- ``schema_lake``: the frozen DDL of the bronze, silver and gold schemas.
- ``feeds``, ``ingest``, ``conform``, ``timeline``: module 1 (landing files to
  bronze, bronze to the ten conformed v0.1 tables, the timestamp chain).

Nothing in this package orders, lists, emails or writes to a partner.
"""

from __future__ import annotations

from restwert.lake.common import (
    CATALOGUE_FAMILIES,
    FLEET_FAMILIES,
    MANUFACTURERS,
    OEM_CODES,
    ROLE_ONLY_SUFFIX,
    allocate_cents,
    billing_date,
    delivery_id,
    fleet_family,
    line_id,
    row_hash,
    rrp_net,
    to_date,
)
from restwert.lake.schema_lake import LAKE_DDL, LAKE_SCHEMAS, LAKE_TABLE_ORDER, create_lake_schema, qualified

__all__ = [
    "CATALOGUE_FAMILIES",
    "FLEET_FAMILIES",
    "MANUFACTURERS",
    "OEM_CODES",
    "ROLE_ONLY_SUFFIX",
    "allocate_cents",
    "billing_date",
    "delivery_id",
    "fleet_family",
    "line_id",
    "row_hash",
    "rrp_net",
    "to_date",
    "LAKE_DDL",
    "LAKE_SCHEMAS",
    "LAKE_TABLE_ORDER",
    "create_lake_schema",
    "qualified",
]
