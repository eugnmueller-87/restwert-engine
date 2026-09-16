"""The real catalogue as the fleet's model pool (SPEC_v0.2 section 5.2).

``data/catalogue/models.csv`` (233 public models with DE launch dates) and
``variants.csv`` (628 variants, 553 with a public launch RRP, gross EUR) are read
through ``restwert.market.anchors.read_tables``. A slug is *usable* when it has a
parsed launch date and at least one priced variant (210 of 233). The fleet draws
from the *pool*: one row per priced variant of a usable slug whose ``storage_gb`` column
is filled (a PO line needs a storage_gb, and the ingest resolves ``slug + storage_gb``
against the typed variants column); one pool row per (slug, storage): the cheapest priced variant
with that storage, the rule ``market.anchors`` applies when it matches a used price to
a launch RRP. A usable slug whose priced variants state no storage stays
usable for the conformed ``model_catalogue`` but is not drawn; it is listed in
``excluded`` with reason ``no_storage_on_priced_variant`` and in ``SYNTHETIC.md``.

Nothing here is synthetic: the pool is a re-shaped copy of public rows plus the net
RRP (``rrp_net = round(rrp_gross / (1 + vat_rate), 2)``, decision D5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from restwert.lake.common import fleet_family, rrp_net
from restwert.lakegen.config import CATALOGUE_DIR
from restwert.market.anchors import read_tables

POOL_COLUMNS: tuple[str, ...] = (
    "slug", "model_name", "oem", "family", "series", "launch_date", "successor_launch_date",
    "spec", "storage_gb", "rrp_gross", "rrp_net", "fleet_family", "base_storage_gb",
)
EXCLUDED_REASONS: tuple[str, ...] = ("no_launch_date", "no_priced_variant", "no_storage_on_priced_variant")

# the v0.1 devices shape (restwert.generate.fleet.DEVICE_COLUMNS) plus the spare flag
V01_DEVICE_COLUMNS: tuple[str, ...] = (
    "serial", "model_family", "model", "storage_gb", "colour", "launch_date", "purchase_date",
    "purchase_price", "landed_cost", "supplier", "channel_in", "po_number", "contract_id", "_spare",
)


@dataclass(frozen=True)
class FleetCatalogue:
    """The public catalogue re-shaped for the generator (see module docstring)."""

    models: pd.DataFrame        # every models.csv row + launch_date (parsed) + fleet_family + usable flag
    variants: pd.DataFrame      # every variants.csv row + storage_gb_parsed + rrp (numeric)
    pool: pd.DataFrame          # one row per (usable slug, storage): the cheapest priced variant (POOL_COLUMNS)
    excluded: pd.DataFrame      # slug, oem, family, reason
    vat_rate: float = 0.19      # the VAT rate rrp_net was computed with

    @property
    def n_usable(self) -> int:
        return int(self.models["usable"].sum())


def parse_catalogue_date(value) -> date | None:
    """ISO date or ``YYYY-MM`` (takes the 15th, the ``market.anchors`` rule); empty -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    if len(s) == 7:
        s = s + "-15"
    try:
        return pd.Timestamp(s).date()
    except (ValueError, TypeError):
        return None


def load_fleet_catalogue(catalogue_dir: Path = CATALOGUE_DIR, vat_rate: float = 0.19) -> FleetCatalogue:
    """Read the two catalogue CSVs and build models, variants, pool and excluded."""
    t = read_tables(Path(catalogue_dir))
    models = t.models.copy()
    variants = t.variants.copy()
    if models.empty:
        raise FileNotFoundError(f"catalogue models.csv not found or empty under {catalogue_dir}")

    models["launch_date"] = models["launch_date_de"].map(parse_catalogue_date)
    models["successor_launch_date"] = models["successor_launch_date"].map(parse_catalogue_date)
    models["fleet_family"] = [
        fleet_family(str(f), str(o)) if str(f) in ("Smartphone", "Tablet", "Laptop") else None
        for f, o in zip(models["family"], models["oem"])
    ]

    variants["rrp"] = pd.to_numeric(variants["rrp_eur_launch_de"], errors="coerce")
    # only an explicit storage_gb counts: the ingest resolves a PO line's slug + storage_gb against
    # the typed cat_variants column, and a storage parsed out of the spec text is not in that column
    variants["storage_gb_parsed"] = [
        (int(s) if pd.notna(s) and str(s).strip() not in ("", "None") else None)
        for s in variants["storage_gb"]
    ]
    priced = variants[variants["rrp"].notna() & (variants["rrp"] > 0)]
    priced_slugs = set(priced["slug"].astype(str))
    with_storage = priced[priced["storage_gb_parsed"].notna()]
    storage_slugs = set(with_storage["slug"].astype(str))

    models["usable"] = models["launch_date"].notna() & models["slug"].astype(str).isin(priced_slugs)

    excluded_rows: list[dict] = []
    for _, m in models.iterrows():
        slug = str(m["slug"])
        if m["launch_date"] is None or pd.isna(m["launch_date"]):
            excluded_rows.append({"slug": slug, "oem": m["oem"], "family": m["family"], "reason": "no_launch_date"})
        elif slug not in priced_slugs:
            excluded_rows.append({"slug": slug, "oem": m["oem"], "family": m["family"], "reason": "no_priced_variant"})
        elif slug not in storage_slugs:
            excluded_rows.append(
                {"slug": slug, "oem": m["oem"], "family": m["family"], "reason": "no_storage_on_priced_variant"}
            )
    excluded = pd.DataFrame(excluded_rows, columns=["slug", "oem", "family", "reason"])

    usable = models[models["usable"]].set_index("slug")
    base_storage = with_storage.sort_values(["slug", "rrp", "storage_gb_parsed"], kind="stable").drop_duplicates("slug")
    base_storage_of = dict(zip(base_storage["slug"].astype(str), base_storage["storage_gb_parsed"].astype(int)))
    pool_rows: list[dict] = []
    for _, v in with_storage.iterrows():
        slug = str(v["slug"])
        if slug not in usable.index:
            continue
        m = usable.loc[slug]
        gross = float(v["rrp"])
        pool_rows.append(
            {
                "slug": slug,
                "model_name": str(m["model_name"]),
                "oem": str(m["oem"]),
                "family": str(m["family"]),
                "series": None if pd.isna(m["series"]) else str(m["series"]),
                "launch_date": m["launch_date"],
                "successor_launch_date": m["successor_launch_date"],
                "spec": str(v["spec"]),
                "storage_gb": int(v["storage_gb_parsed"]),
                "rrp_gross": round(gross, 2),
                "rrp_net": rrp_net(gross, vat_rate),
                "fleet_family": str(m["fleet_family"]),
                "base_storage_gb": int(base_storage_of[slug]),
            }
        )
    pool = pd.DataFrame(pool_rows, columns=list(POOL_COLUMNS))
    # one row per (slug, storage): the cheapest priced variant with that storage, the rule the anchors
    # module applies when it matches a used price to a launch RRP; a PO line is keyed by slug + storage
    pool = pool.sort_values(["slug", "storage_gb", "rrp_gross", "spec"], kind="stable").drop_duplicates(["slug", "storage_gb"], keep="first")
    pool = pool.sort_values(["family", "oem", "launch_date", "slug", "storage_gb", "spec"], kind="stable").reset_index(drop=True)
    return FleetCatalogue(models=models, variants=variants, pool=pool, excluded=excluded, vat_rate=float(vat_rate))


def latest_slugs(pool: pd.DataFrame, family: str, oem: str, before: date) -> list[str]:
    """Distinct slugs of (catalogue family, oem) launched on or before ``before``, newest first."""
    sub = pool[(pool["family"] == family) & (pool["oem"] == oem) & (pool["launch_date"] <= before)]
    if sub.empty:
        return []
    firsts = sub.drop_duplicates("slug", keep="first").sort_values(["launch_date", "slug"], ascending=[False, True], kind="stable")
    return [str(s) for s in firsts["slug"]]


def to_v01_devices_frame(fleet: pd.DataFrame) -> pd.DataFrame:
    """Re-shape the lake fleet into the v0.1 ``devices`` frame the v0.1 builders take.

    ``purchase_date = received_at::date``, ``landed_cost`` as computed by the purchase
    step (unit price plus the cent-exact freight and duty shares), ``model = slug``,
    ``model_family = fleet_family``, ``channel_in`` manufacturer -> ``oem_direct``,
    reseller -> ``distributor``. ``contract_id`` is None until the rentals step.
    """
    out = pd.DataFrame(
        {
            "serial": fleet["serial"].astype(str),
            "model_family": fleet["fleet_family"].astype(str),
            "model": fleet["slug"].astype(str),
            "storage_gb": fleet["storage_gb"].astype(int),
            "colour": fleet["colour"].astype(str),
            "launch_date": list(fleet["launch_date"]),
            "purchase_date": [ts.date() if isinstance(ts, datetime) else ts for ts in fleet["received_at"]],
            "purchase_price": fleet["unit_price_eur"].astype(float),
            "landed_cost": fleet["landed_cost_eur"].astype(float),
            "supplier": fleet["supplier_name"].astype(str),
            "channel_in": ["oem_direct" if r == "manufacturer" else "distributor" for r in fleet["supplier_role"]],
            "po_number": [f"{p}-{l}" for p, l in zip(fleet["po_number"], fleet["po_line"])],
            "contract_id": [None] * len(fleet),
            "_spare": fleet["_spare"].astype(bool),
        }
    )
    return out[list(V01_DEVICE_COLUMNS)].reset_index(drop=True)


__all__ = [
    "FleetCatalogue",
    "POOL_COLUMNS",
    "EXCLUDED_REASONS",
    "V01_DEVICE_COLUMNS",
    "parse_catalogue_date",
    "load_fleet_catalogue",
    "latest_slugs",
    "to_v01_devices_frame",
]
