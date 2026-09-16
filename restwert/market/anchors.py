"""Load the public catalogue and used-price anchors and compute realisation per anchor.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Three CSVs, all public, all with a source URL per row:

* ``data/catalogue/models.csv``    one row per model: oem, family, series, launch date (DE availability)
* ``data/catalogue/variants.csv``  one row per model and spec: storage, RAM, launch RRP (DE, gross EUR)
* ``data/anchors/used_prices.csv`` one row per observed used price: spec, condition, price, URL, date seen

Realisation of an anchor = used price today / launch RRP of the same model and storage,
both gross EUR Germany. Age = months between DE launch and the day the price was seen.
Marketplace prices are retail prices of a refurbisher (upper bound of what a seller
realises); trade-in and buy-back offers are what a buyer pays (lower bound). Both are
kept and labelled, never averaged into one number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from restwert.paths import DATA_DIR

CATALOGUE_DIR: Path = DATA_DIR / "catalogue"
ANCHORS_DIR: Path = DATA_DIR / "anchors"
MODELS_CSV: Path = CATALOGUE_DIR / "models.csv"
VARIANTS_CSV: Path = CATALOGUE_DIR / "variants.csv"
USED_CSV: Path = ANCHORS_DIR / "used_prices.csv"

# Condition label (lower-cased, as printed by the source) -> grade bucket.
# A = like new, B = very good, C = good, D = acceptable or heavily used.
# Buy-back and trade-in offers keep their own bucket because they are a bid, not an ask.
_GRADE_PATTERNS: list[tuple[str, str]] = [
    (r"trade[- ]?in|ankauf|bis zu|buy[- ]?back|verkaufen", "TRADEIN"),
    (r"\bgrade\s*a\b|a-ware|premium|wie neu|neuwertig|hervorragend|like new|excellent|exzellent|mint|top|ovp ge(ö|oe)ffnet|open box|^a$", "A"),
    (r"\bgrade\s*b\b|b-ware|sehr gut|very good|^b$", "B"),
    (r"\bgrade\s*c\b|c-ware|\bgut\b|\bgood\b|befriedigend|^c$", "C"),
    (r"\bgrade\s*d\b|d-ware|akzeptabel|acceptable|stark (gebraucht|benutzt|genutzt)|fair|gebrauchsspuren|^d$", "D"),
]
# A bare letter is the grade itself (hardware-online-shop prints "C"); "OVP geöffnet" is an unused unit in an opened box
# (AfB), asked like new. "StoreDeal" (lapstore: dents, scratches, deformation) stays UNKNOWN on purpose: it is a deal
# label, not a condition grade, and the matcher must not guess one.
GRADE_ORDER = ["A", "B", "C", "D", "TRADEIN"]


def normalise_grade(condition: str | None) -> str:
    """Map a marketplace condition label to A, B, C, D or TRADEIN; unknown labels are 'UNKNOWN'."""
    if condition is None or (isinstance(condition, float) and pd.isna(condition)):
        return "UNKNOWN"
    s = str(condition).strip().lower()
    for pattern, grade in _GRADE_PATTERNS:
        if re.search(pattern, s):
            return grade
    return "UNKNOWN"


_STORAGE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(tb|gb)", re.I)


def _capacities_gb(spec: str | None) -> list[int]:
    if spec is None or (isinstance(spec, float) and pd.isna(spec)):
        return []
    out = []
    for num, unit in _STORAGE_RE.findall(str(spec)):
        value = float(num.replace(",", "."))
        if unit.lower() == "tb":
            value *= 1024
        out.append(int(round(value)))
    return out


def parse_storage_gb(spec: str | None) -> int | None:
    """'256 GB' -> 256, '1 TB' -> 1024, '8 GB / 256 GB' -> 256 (the last capacity is the storage)."""
    caps = _capacities_gb(spec)
    return caps[-1] if caps else None


def parse_ram_gb(spec: str | None) -> int | None:
    """'8 GB / 256 GB' -> 8, 'M2 / 16 GB / 1 TB' -> 16; a single capacity is storage, so None."""
    caps = _capacities_gb(spec)
    if len(caps) >= 2 and caps[0] < caps[-1]:
        return caps[0]
    return None


def months_between(start: date, end: date) -> float:
    """Whole months plus the day fraction, so 24.5 means twenty-four and a half months."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return months + (end.day - start.day) / 30.4375


def _to_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) == 7:  # YYYY-MM: use the 15th
        s = s + "-15"
    try:
        return pd.Timestamp(s).date()
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class AnchorTables:
    models: pd.DataFrame
    variants: pd.DataFrame
    used: pd.DataFrame


def read_tables(catalogue_dir: Path = CATALOGUE_DIR, anchors_dir: Path = ANCHORS_DIR) -> AnchorTables:
    """Read the three CSVs; missing files give empty frames with the expected columns."""

    def _read(path: Path, cols: list[str]) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=cols)
        df = pd.read_csv(path, comment="#", encoding="utf-8")
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df

    models = _read(
        catalogue_dir / "models.csv",
        ["slug", "model_name", "oem", "family", "series", "launch_date_de", "launch_date_kind",
         "launch_source_url", "successor", "successor_launch_date", "notes"],
    )
    variants = _read(
        catalogue_dir / "variants.csv",
        ["slug", "spec", "storage_gb", "ram_gb", "rrp_eur_launch_de", "rrp_source_url", "rrp_source_date"],
    )
    used = _read(
        anchors_dir / "used_prices.csv",
        ["slug", "spec", "condition", "price_eur", "source_url", "date_seen", "source_kind"],
    )
    return AnchorTables(models=models, variants=variants, used=used)


_CPU_TOKEN_RE = re.compile(r"[a-z]*\d[a-z0-9]{2,}")


def _cpu_tokens(spec: str | None) -> set[str]:
    """Processor-like tokens of the first segment of a spec ('Core i5-1335U / 16 GB / 512 GB' -> {'1335u'})."""
    if not spec:
        return set()
    head = str(spec).split("/")[0].lower()
    return {t for t in _CPU_TOKEN_RE.findall(head) if not t.endswith(("gb", "tb"))}


def _shares_cpu_token(offer_spec: str | None, variant_spec: str | None) -> bool:
    return bool(_cpu_tokens(offer_spec) & _cpu_tokens(variant_spec))


def _match_variant(
    variants: pd.DataFrame, slug: str, storage_gb: int | None, ram_gb: int | None, spec: str | None
) -> tuple[pd.Series | None, str]:
    """The priced variant of ``slug`` that the used offer belongs to, and how it was matched.

    ``exact``: same spec string. ``storage_ram``: storage and RAM agree. ``storage``: storage agrees and
    at least one side does not state RAM. No match when the storage differs or both state a different
    RAM: a used price for a bigger configuration divided by the base RRP would overstate realisation,
    so such an offer is dropped and counted rather than guessed.
    """
    v = variants[(variants["slug"] == slug) & variants["rrp_eur_launch_de"].notna()]
    if v.empty:
        return None, "no_priced_variant"
    if spec:
        hit = v[v["spec"].astype(str).str.strip().str.lower() == str(spec).strip().lower()]
        if not hit.empty:
            return hit.iloc[0], "exact"
    if storage_gb is None:
        return None, "no_storage_in_offer"
    same_storage = v[v["storage_gb_parsed"] == storage_gb]
    if same_storage.empty:
        return None, "no_variant_with_this_storage"
    if ram_gb is not None:
        both = same_storage[same_storage["ram_gb_parsed"] == ram_gb]
        if len(both) > 1:
            # several priced variants share storage and RAM (an Intel and an AMD build of the same model):
            # prefer the one whose processor token appears in the offer, never guess between them by price
            cpu_hit = both[[_shares_cpu_token(spec, vs) for vs in both["spec"]]]
            if not cpu_hit.empty:
                return cpu_hit.iloc[0], "storage_ram_cpu"
        if not both.empty:
            return both.iloc[0], "storage_ram"
        unknown = same_storage[same_storage["ram_gb_parsed"].isna()]
        if not unknown.empty:
            return unknown.iloc[0], "storage"
        return None, "ram_mismatch"
    return same_storage.sort_values("rrp_eur_launch_de").iloc[0], "storage"


def load_anchors(
    catalogue_dir: Path = CATALOGUE_DIR,
    anchors_dir: Path = ANCHORS_DIR,
) -> pd.DataFrame:
    """One row per used-price observation joined to its model and launch variant.

    Columns: slug, model_name, oem, family, series, launch_date_de, spec_used, storage_gb,
    condition, grade, price_eur, rrp_eur_launch_de, spec_rrp, age_months, realisation,
    source_kind, source_url, date_seen, match_kind, rrp_source_url.
    Rows without a usable RRP, launch date or price are dropped and counted in ``attrs['dropped']``.
    """
    t = read_tables(catalogue_dir, anchors_dir)
    variants = t.variants.copy()
    variants["storage_gb_parsed"] = [
        (int(s) if pd.notna(s) and str(s).strip() not in ("", "None") else parse_storage_gb(sp))
        for s, sp in zip(variants["storage_gb"], variants["spec"])
    ]
    variants["ram_gb_parsed"] = [
        (int(r) if pd.notna(r) and str(r).strip() not in ("", "None") else parse_ram_gb(sp))
        for r, sp in zip(variants["ram_gb"], variants["spec"])
    ]
    variants["rrp_eur_launch_de"] = pd.to_numeric(variants["rrp_eur_launch_de"], errors="coerce")

    models = t.models.set_index("slug") if not t.models.empty else t.models
    rows: list[dict] = []
    dropped = {"no_model": 0, "no_launch_date": 0, "no_rrp": 0, "no_price": 0, "no_date_seen": 0, "spec_mismatch": 0}
    for _, u in t.used.iterrows():
        slug = u["slug"]
        if models.empty or slug not in models.index:
            dropped["no_model"] += 1
            continue
        m = models.loc[slug]
        launch = _to_date(m["launch_date_de"])
        if launch is None:
            dropped["no_launch_date"] += 1
            continue
        price = pd.to_numeric(u["price_eur"], errors="coerce")
        if pd.isna(price) or price <= 0:
            dropped["no_price"] += 1
            continue
        seen = _to_date(u["date_seen"])
        if seen is None:
            dropped["no_date_seen"] += 1
            continue
        storage = parse_storage_gb(u["spec"])
        ram = parse_ram_gb(u["spec"])
        var, match_kind = _match_variant(variants, slug, storage, ram, u["spec"])
        if var is None:
            dropped["no_rrp" if match_kind == "no_priced_variant" else "spec_mismatch"] += 1
            continue
        rrp = float(var["rrp_eur_launch_de"])
        matched_storage = var["storage_gb_parsed"]
        rows.append(
            {
                "slug": slug,
                "model_name": m["model_name"],
                "oem": m["oem"],
                "family": m["family"],
                "series": m["series"],
                "launch_date_de": launch,
                "spec_used": u["spec"],
                "storage_gb": storage if storage is not None else matched_storage,
                "condition": u["condition"],
                # a buy-back bid is TRADEIN by its source, whatever the portal calls the condition
                "grade": "TRADEIN" if str(u["source_kind"]).strip() == "ankauf-trade-in" else normalise_grade(u["condition"]),
                "price_eur": float(price),
                "rrp_eur_launch_de": rrp,
                "spec_rrp": var["spec"],
                "age_months": round(months_between(launch, seen), 2),
                "realisation": round(float(price) / rrp, 4),
                "source_kind": u["source_kind"],
                "source_url": u["source_url"],
                "date_seen": seen,
                "match_kind": match_kind,
                "rrp_source_url": var["rrp_source_url"],
            }
        )
    df = pd.DataFrame(rows)
    df.attrs["dropped"] = dropped
    df.attrs["n_models"] = int(len(t.models))
    df.attrs["n_variants_priced"] = int(variants["rrp_eur_launch_de"].notna().sum())
    return df


__all__ = [
    "CATALOGUE_DIR",
    "ANCHORS_DIR",
    "GRADE_ORDER",
    "normalise_grade",
    "parse_storage_gb",
    "parse_ram_gb",
    "months_between",
    "read_tables",
    "load_anchors",
]
