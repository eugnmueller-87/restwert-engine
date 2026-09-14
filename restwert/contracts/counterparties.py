"""Counterparties of the contracts register v2 (SPEC_v0.2 section 8.1).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The list is the allowlist. Two kinds of names exist and nothing else:

* the ten manufacturers exactly as ``data/catalogue/models.csv`` spells them
  (public, the catalogue already carries them), and
* role-only parties whose name ends in ``" (role-only)"``: the reseller, the
  carrier partner, the refurbishment and repair partner, the logistics
  partner, the marketplace channels, the financing partners, the mobile threat
  defense partner and the rugged-device OEM.

Every term on every row (price protection, claim window, warranty, rebate
tiers, SLA, payment terms) is a synthetic placeholder and ``TERMS_NOTE`` says
so on every register row. No term is from any provider.

The generator (module 2) draws ``contracts/register`` from ``COUNTERPARTIES``;
``is_allowed_name`` is the gate the honesty tests apply to every supplier and
counterparty name in landing files and bronze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from restwert.lake.common import MANUFACTURERS, ROLE_ONLY_SUFFIX

TERMS_NOTE = (
    "synthetic placeholder terms; the counterparty list is public (manufacturers from the catalogue, "
    "every other party role-only); no term is from any provider"
)

# Rebate tiers (placeholders) on four manufacturers: from_eur is the 12-month spend from which pct applies.
_REBATE_TIERS: list[dict[str, float]] = [
    {"from_eur": 0, "pct": 0.0},
    {"from_eur": 250000, "pct": 0.01},
    {"from_eur": 750000, "pct": 0.02},
]

_MANUFACTURER_SLA: dict[str, Any] = {"delivery_lead_days": 14, "doa_replacement_days": 10}


@dataclass(frozen=True)
class Counterparty:
    """One counterparty of the register with its placeholder terms.

    ``price_protection_days`` is the window after receipt inside which a
    price drop counts; ``claim_window_days`` is how long after the drop the
    credit can still be claimed (v0.1 ``price_protection_days`` = this value,
    decision D16). ``None`` means the counterparty grants no such term.
    """

    name: str
    role: str
    category: str
    is_public: bool
    sla: dict[str, Any] = field(default_factory=dict)
    price_protection_days: int | None = None
    claim_window_days: int | None = None
    warranty_months: int | None = None
    rebate_tiers: list[dict[str, float]] | None = None
    payment_terms_days: int = 30

    @property
    def price_protection(self) -> bool:
        return self.price_protection_days is not None


def _oem(
    name: str,
    *,
    protection: tuple[int, int] | None,
    warranty: int,
    rebate: bool,
    payment: int,
) -> Counterparty:
    return Counterparty(
        name=name,
        role="manufacturer",
        category="hardware",
        is_public=True,
        sla=dict(_MANUFACTURER_SLA),
        price_protection_days=None if protection is None else protection[0],
        claim_window_days=None if protection is None else protection[1],
        warranty_months=warranty,
        rebate_tiers=[dict(t) for t in _REBATE_TIERS] if rebate else None,
        payment_terms_days=payment,
    )


COUNTERPARTIES: tuple[Counterparty, ...] = (
    # ten manufacturers, exact catalogue spelling (public)
    _oem("Apple", protection=(30, 14), warranty=12, rebate=False, payment=30),
    _oem("Samsung", protection=(60, 30), warranty=24, rebate=True, payment=45),
    _oem("Google", protection=(45, 30), warranty=24, rebate=False, payment=30),
    _oem("Motorola", protection=None, warranty=24, rebate=False, payment=30),
    _oem("Fairphone", protection=None, warranty=36, rebate=False, payment=30),
    _oem("HMD Global (Nokia)", protection=None, warranty=24, rebate=False, payment=30),
    _oem("Lenovo", protection=(60, 30), warranty=36, rebate=True, payment=60),
    _oem("Dell", protection=(45, 14), warranty=36, rebate=True, payment=45),
    _oem("HP", protection=(45, 30), warranty=36, rebate=True, payment=60),
    _oem("Microsoft", protection=None, warranty=12, rebate=False, payment=30),
    # rugged-device OEM: hardware contract, no purchase orders in the fleet (no catalogue row)
    Counterparty(
        name="Rugged-device OEM" + ROLE_ONLY_SUFFIX,
        role="rugged_oem",
        category="hardware",
        is_public=False,
        sla=dict(_MANUFACTURER_SLA),
        warranty_months=36,
        payment_terms_days=30,
    ),
    # resellers: covers_oems is derived from the purchase orders routed through them
    Counterparty(
        name="IT reseller A" + ROLE_ONLY_SUFFIX,
        role="reseller",
        category="hardware",
        is_public=False,
        sla={"delivery_lead_days": 10, "doa_replacement_days": 10},
        price_protection_days=30,
        claim_window_days=14,
        warranty_months=12,
        payment_terms_days=30,
    ),
    Counterparty(
        name="IT reseller B" + ROLE_ONLY_SUFFIX,
        role="reseller",
        category="hardware",
        is_public=False,
        sla={"delivery_lead_days": 10, "doa_replacement_days": 10},
        warranty_months=12,
        payment_terms_days=30,
    ),
    # connectivity
    Counterparty(
        name="Carrier partner" + ROLE_ONLY_SUFFIX,
        role="carrier",
        category="connectivity",
        is_public=False,
        sla={"activation_days": 2, "uptime_pct": 0.995},
        payment_terms_days=30,
    ),
    # refurbishment and repair: one row per category
    Counterparty(
        name="Refurbishment and repair partner" + ROLE_ONLY_SUFFIX,
        role="refurb_repair",
        category="refurbishment",
        is_public=False,
        sla={"turnaround_days": 7, "first_time_fix_pct": 0.90},
        payment_terms_days=30,
    ),
    Counterparty(
        name="Refurbishment and repair partner" + ROLE_ONLY_SUFFIX,
        role="refurb_repair",
        category="repair",
        is_public=False,
        sla={"turnaround_days": 7, "first_time_fix_pct": 0.90},
        payment_terms_days=30,
    ),
    # logistics
    Counterparty(
        name="Logistics partner" + ROLE_ONLY_SUFFIX,
        role="logistics",
        category="logistics",
        is_public=False,
        sla={"pickup_within_days": 2, "delivery_days": 3},
        payment_terms_days=30,
    ),
    # resale channels
    Counterparty(
        name="Marketplace channel A" + ROLE_ONLY_SUFFIX,
        role="marketplace",
        category="resale_channel",
        is_public=False,
        sla={"fee_pct": 0.12, "fee_fixed_eur": 2.5, "payout_days": 28},
        payment_terms_days=28,
    ),
    Counterparty(
        name="Marketplace channel B" + ROLE_ONLY_SUFFIX,
        role="marketplace",
        category="resale_channel",
        is_public=False,
        sla={"fee_pct": 0.12, "fee_fixed_eur": 2.5, "payout_days": 28},
        payment_terms_days=28,
    ),
    # financing
    Counterparty(
        name="Financing partner A" + ROLE_ONLY_SUFFIX,
        role="financing",
        category="financing",
        is_public=False,
        sla={"funding_days": 5},
        payment_terms_days=30,
    ),
    Counterparty(
        name="Financing partner B" + ROLE_ONLY_SUFFIX,
        role="financing",
        category="financing",
        is_public=False,
        sla={"funding_days": 5},
        payment_terms_days=30,
    ),
    # security software
    Counterparty(
        name="Mobile threat defense partner" + ROLE_ONLY_SUFFIX,
        role="mtd",
        category="security_software",
        is_public=False,
        sla={"per_device_month_eur": 1.2},
        payment_terms_days=30,
    ),
)

ROLE_ONLY_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(cp.name for cp in COUNTERPARTIES if cp.name.endswith(ROLE_ONLY_SUFFIX))
)

MANUFACTURER_NAMES: tuple[str, ...] = tuple(cp.name for cp in COUNTERPARTIES if cp.role == "manufacturer")

ROLES: tuple[str, ...] = tuple(dict.fromkeys(cp.role for cp in COUNTERPARTIES))

# register roles that the v0.1 conform step turns into ``supplier_contracts`` rows (D16)
SUPPLIER_ROLES: tuple[str, ...] = ("manufacturer", "rugged_oem", "reseller", "refurb_repair", "logistics", "mtd")


def is_allowed_name(name: str) -> bool:
    """True when ``name`` is an exact catalogue manufacturer or ends in the role-only suffix.

    This is the whole allowlist: nothing else may appear as a supplier or
    counterparty name anywhere in landing files or bronze.
    """

    if name is None:
        return False
    text = str(name)
    return text in MANUFACTURERS or text.endswith(ROLE_ONLY_SUFFIX)


def counterparty_by_name(name: str, category: str | None = None) -> Counterparty | None:
    """Look a counterparty up by name (and category when a name has two rows)."""

    for cp in COUNTERPARTIES:
        if cp.name == name and (category is None or cp.category == category):
            return cp
    return None


__all__ = [
    "Counterparty",
    "COUNTERPARTIES",
    "ROLE_ONLY_NAMES",
    "MANUFACTURER_NAMES",
    "ROLES",
    "SUPPLIER_ROLES",
    "TERMS_NOTE",
    "is_allowed_name",
    "counterparty_by_name",
]
