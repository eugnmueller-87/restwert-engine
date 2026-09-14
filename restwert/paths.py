"""Repository paths (SPEC.md section 2.2).

All paths are derived from the location of this file, so the package works
from a plain checkout without installation.
"""

from __future__ import annotations

from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = ROOT / "config"
DATA_DIR: Path = ROOT / "data"
RAW_CSV_DIR: Path = DATA_DIR / "raw_csv"
OUTPUTS_DIR: Path = ROOT / "outputs"
DOCS_DIR: Path = ROOT / "docs"
DEFAULT_DB: Path = DATA_DIR / "restwert.duckdb"

# v0.2 data lake (SPEC_v0.2.md section 3.1)
LAKE_DIR: Path = DATA_DIR / "lake"
LAKE_RAW_DIR: Path = LAKE_DIR / "raw"
LAKE_BRONZE_DIR: Path = LAKE_DIR / "bronze"
LAKE_SILVER_DIR: Path = LAKE_DIR / "silver"
LAKE_GOLD_DIR: Path = LAKE_DIR / "gold"
CATALOGUE_DIR: Path = DATA_DIR / "catalogue"
MARKET_CURVES_CSV: Path = OUTPUTS_DIR / "market_curves.csv"
LAKE_CONFIG: Path = CONFIG_DIR / "lake.yaml"

__all__ = [
    "ROOT",
    "CONFIG_DIR",
    "DATA_DIR",
    "RAW_CSV_DIR",
    "OUTPUTS_DIR",
    "DOCS_DIR",
    "DEFAULT_DB",
    "LAKE_DIR",
    "LAKE_RAW_DIR",
    "LAKE_BRONZE_DIR",
    "LAKE_SILVER_DIR",
    "LAKE_GOLD_DIR",
    "CATALOGUE_DIR",
    "MARKET_CURVES_CSV",
    "LAKE_CONFIG",
]
