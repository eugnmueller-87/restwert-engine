"""Shared pure helpers of the v0.2 lake. No I/O, no DuckDB."""
from __future__ import annotations
import hashlib, math
from datetime import date, datetime
from restwert.dates import add_months, months_between

FLEET_FAMILIES: tuple[str, ...] = ("iphone_like", "android_like", "tablet_like", "laptop_like")
CATALOGUE_FAMILIES: tuple[str, ...] = ("Smartphone", "Tablet", "Laptop")
OEM_CODES: dict[str, str] = {
    "Apple": "APL", "Samsung": "SAM", "Google": "GOO", "Motorola": "MOT", "Fairphone": "FPH",
    "HMD Global (Nokia)": "HMD", "Lenovo": "LEN", "Dell": "DEL", "HP": "HPI", "Microsoft": "MSF",
}
MANUFACTURERS: tuple[str, ...] = tuple(sorted(OEM_CODES))   # exact oem strings of data/catalogue/models.csv
ROLE_ONLY_SUFFIX = " (role-only)"

def fleet_family(catalogue_family: str, oem: str) -> str:
    """Smartphone+Apple -> iphone_like; Smartphone+other -> android_like; Tablet -> tablet_like; Laptop -> laptop_like."""
    if catalogue_family == "Smartphone":
        return "iphone_like" if oem == "Apple" else "android_like"
    if catalogue_family == "Tablet":
        return "tablet_like"
    if catalogue_family == "Laptop":
        return "laptop_like"
    raise ValueError(f"unknown catalogue family {catalogue_family!r}")

def rrp_net(rrp_gross: float, vat_rate: float) -> float:
    return round(float(rrp_gross) / (1.0 + float(vat_rate)), 2)

def allocate_cents(total: float, n: int) -> list[float]:
    """Split ``total`` EUR over ``n`` units to the cent: floor per unit, remainder on the LAST unit; sums exactly."""
    if n <= 0:
        return []
    cents = int(round(total * 100))
    base, rem = divmod(cents, n)
    out = [base] * n
    out[-1] += rem
    return [c / 100.0 for c in out]

def billing_date(start: date, k: int) -> date:
    """First date d with months_between(start, d) == k (k >= 1): add_months(start, k), plus one day when the day was clamped."""
    d = add_months(start, k)
    if d.day < start.day:
        d = date.fromordinal(d.toordinal() + 1)
    assert months_between(start, d) == k
    return d

def line_id(serial: str, line_type: str, source_system: str, source_ref: str, event_date: date) -> str:
    return hashlib.sha1(f"{serial}|{line_type}|{source_system}|{source_ref}|{event_date.isoformat()}".encode("utf-8")).hexdigest()[:24]

def row_hash(values: list) -> str:
    """sha1 over the content columns of one landing row (strings, '' for missing)."""
    return hashlib.sha1("\x1f".join("" if v is None else str(v) for v in values).encode("utf-8")).hexdigest()

def delivery_id(sha256_hex: str) -> str:
    return sha256_hex[:16]

def to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    return date.fromisoformat(s[:10])
