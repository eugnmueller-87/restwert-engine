"""Contracts register and indirect spend of the lake (SPEC_v0.2 section 5.4).

``build_register`` renders one register row per entry of
``restwert.contracts.counterparties.COUNTERPARTIES`` (22 rows: the ten catalogue
manufacturers with their exact public names, every other party role-only). The
counterparty list and its placeholder terms are owned by module 5; this module
draws only what a register needs on top: contract dates, notice days, auto renewal,
volume commitments and the spend under contract. ``TERMS_NOTE`` sits on every row.

Dates (design, not from any provider): manufacturers and resellers run framework
agreements that started before ``history_start`` and are still in force at ``as_of``
(so purchase orders across the history can reference them), except that two
manufacturers end within six months after ``as_of`` (the renewal calendar has work)
and one smaller manufacturer expired before ``as_of`` (rule R06 fires and its later
purchase orders show up as spend without a contract).

``spend_under_contract_eur`` is filled AFTER the purchase step by
``fill_register_spend``: 12 x the mean monthly purchase order value of the counterparty
for manufacturers and resellers; for the other roles a design band per category scaled
by fleet size (there are no purchase orders for them; the band is a placeholder).

``build_indirect`` wraps the v0.1 ``build_indirect_spend`` with role-only suppliers and
renames the columns to the ``finance/indirect_spend`` landing shape.

The register is built BEFORE the purchase step in the rng order (purchase orders need
the contract ids), which is why ``build_register`` draws nothing that depends on the
fleet; the fleet-dependent spend is deterministic arithmetic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.contracts.counterparties import COUNTERPARTIES, TERMS_NOTE, is_allowed_name
from restwert.dates import add_months
from restwert.generate.procurement import build_indirect_spend
from restwert.lakegen.config import LakeConfig

REGISTER_COLUMNS: tuple[str, ...] = (
    "contract_id", "counterparty_name", "counterparty_role", "counterparty_is_public", "category", "start_date",
    "end_date", "notice_days", "auto_renewal", "price_protection", "price_protection_days", "claim_window_days",
    "warranty_months", "rebate_tiers_json", "volume_commitment_units", "payment_terms_days", "sla_json",
    "spend_under_contract_eur", "terms_note",
)
INDIRECT_COLUMNS: tuple[str, ...] = (
    "spend_id", "invoice_date", "category", "supplier_name", "amount_eur", "has_po", "has_contract", "saving_eur",
    "saving_confirmed_by_controlling", "saving_type", "baseline_amount_eur",
)

NOTICE_DAYS_OPTIONS: tuple[int, ...] = (30, 60, 90, 120)
AUTO_RENEWAL_SHARE: float = 0.40
N_ENDING_SOON: int = 2
# manufacturers that may be drawn as the expired one (the two largest shares are kept in force by design)
EXPIRED_CANDIDATES: tuple[str, ...] = ("Google", "Motorola", "Fairphone", "HMD Global (Nokia)", "Dell", "HP", "Microsoft", "Lenovo")
# annual spend design bands (EUR at 5000 serials) for roles without purchase orders; scaled by fleet size
SPEND_BAND_BY_CATEGORY: dict[str, float] = {
    "hardware": 40000.0,          # only the rugged-device OEM (no purchase orders in the fleet)
    "connectivity": 90000.0,
    "refurbishment": 60000.0,
    "repair": 45000.0,
    "logistics": 70000.0,
    "resale_channel": 30000.0,
    "financing": 120000.0,
    "security_software": 25000.0,
}
SPEND_BAND_REFERENCE_SERIALS: int = 5000
FRAMEWORK_MIN_MONTHS_AFTER_AS_OF: int = 7
FRAMEWORK_MAX_MONTHS_AFTER_AS_OF: int = 30

# indirect categories (v0.1 SpendCategory values) -> contracted role-only supplier
INDIRECT_CONTRACTED_SUPPLIER: dict[str, str] = {
    "logistics": "Logistics partner (role-only)",
    "repair": "Refurbishment and repair partner (role-only)",
    "refurbishment": "Refurbishment and repair partner (role-only)",
    "software": "Software vendor (role-only)",
    "marketing": "Marketing agency (role-only)",
    "facilities": "Facilities provider (role-only)",
    "consulting": "Consulting firm (role-only)",
    "packaging": "Packaging supplier (role-only)",
}


def build_register(cfg: LakeConfig, rng: np.random.Generator, as_of: date) -> pd.DataFrame:
    """One row per counterparty; ``spend_under_contract_eur`` is 0 until ``fill_register_spend``."""
    manufacturers = [cp.name for cp in COUNTERPARTIES if cp.role == "manufacturer"]
    soon = [str(x) for x in rng.choice(np.array(manufacturers, dtype=object), size=N_ENDING_SOON, replace=False)]
    candidates = [m for m in EXPIRED_CANDIDATES if m in manufacturers and m not in soon]
    expired = str(candidates[int(rng.integers(0, len(candidates)))])
    rows: list[dict] = []
    for i, cp in enumerate(COUNTERPARTIES):
        if cp.role == "manufacturer" and cp.name in soon:
            end = as_of + timedelta(days=int(rng.integers(15, 181)))
        elif cp.role == "manufacturer" and cp.name == expired:
            end = as_of - timedelta(days=int(rng.integers(30, 201)))
        else:
            end = add_months(as_of, int(rng.integers(FRAMEWORK_MIN_MONTHS_AFTER_AS_OF, FRAMEWORK_MAX_MONTHS_AFTER_AS_OF + 1)))
            end = end + timedelta(days=int(rng.integers(0, 28)))
        if cp.role in ("manufacturer", "reseller", "rugged_oem"):
            start = add_months(cfg.history_start, -int(rng.integers(1, 13)))
        else:
            start = add_months(cfg.history_start, int(rng.integers(-12, 19)))
        if start >= end:
            start = add_months(end, -24)
        volume = None
        if cp.rebate_tiers:
            volume = int(rng.integers(200, 1501))
        rows.append(
            {
                "contract_id": f"CTR-{i + 1:03d}",
                "counterparty_name": cp.name,
                "counterparty_role": cp.role,
                "counterparty_is_public": bool(cp.is_public),
                "category": cp.category,
                "start_date": start,
                "end_date": end,
                "notice_days": int(NOTICE_DAYS_OPTIONS[int(rng.integers(0, len(NOTICE_DAYS_OPTIONS)))]),
                "auto_renewal": bool(rng.random() < AUTO_RENEWAL_SHARE),
                "price_protection": bool(cp.price_protection),
                "price_protection_days": cp.price_protection_days,
                "claim_window_days": cp.claim_window_days,
                "warranty_months": cp.warranty_months,
                "rebate_tiers_json": json.dumps(cp.rebate_tiers, sort_keys=True) if cp.rebate_tiers else None,
                "volume_commitment_units": volume,
                "payment_terms_days": int(cp.payment_terms_days),
                "sla_json": json.dumps(cp.sla, sort_keys=True) if cp.sla else None,
                "spend_under_contract_eur": 0.0,
                "terms_note": TERMS_NOTE,
            }
        )
    register = pd.DataFrame(rows, columns=list(REGISTER_COLUMNS))
    assert all(is_allowed_name(n) for n in register["counterparty_name"])
    return register


def register_index(register: pd.DataFrame) -> dict[tuple[str, str], list[dict]]:
    """Register rows as plain dicts keyed by (counterparty_name, role), for fast in-force lookups."""
    out: dict[tuple[str, str], list[dict]] = {}
    for rec in register.to_dict("records"):
        rec = {k: (None if (v is None or (isinstance(v, float) and np.isnan(v))) else v) for k, v in rec.items()}
        out.setdefault((str(rec["counterparty_name"]), str(rec["counterparty_role"])), []).append(rec)
    return out


def contract_in_force(
    register: pd.DataFrame | dict[tuple[str, str], list[dict]], counterparty_name: str, role: str, on: date
) -> dict | None:
    """The register row (as a dict) of ``counterparty_name`` with that role in force on ``on``, else None."""
    index = register if isinstance(register, dict) else register_index(register)
    for rec in index.get((str(counterparty_name), str(role)), []):
        if rec["start_date"] <= on <= rec["end_date"]:
            return rec
    return None


def fill_register_spend(
    register: pd.DataFrame, po_headers: pd.DataFrame, po_lines: pd.DataFrame, n_serials: int
) -> pd.DataFrame:
    """Set ``spend_under_contract_eur``: 12 x mean monthly PO value per counterparty, else a design band."""
    out = register.copy()
    monthly_value: dict[str, float] = {}
    if len(po_headers) and len(po_lines):
        value = po_lines["qty_ordered"].astype(float) * po_lines["unit_price_eur"].astype(float)
        by_po = value.groupby(po_lines["po_number"]).sum()
        headers = po_headers.set_index("po_number")
        names = headers.loc[by_po.index, "supplier_name"]
        span_months = max(1.0, (po_headers["order_date"].max() - po_headers["order_date"].min()).days / 30.4375)
        for name, v in by_po.groupby(names.values).sum().items():
            monthly_value[str(name)] = float(v) / span_months
    scale = max(float(n_serials), 1.0) / float(SPEND_BAND_REFERENCE_SERIALS)
    spend: list[float] = []
    for _, r in out.iterrows():
        if r["counterparty_role"] in ("manufacturer", "reseller") and r["counterparty_name"] in monthly_value:
            spend.append(round(12.0 * monthly_value[str(r["counterparty_name"])], 2))
        else:
            spend.append(round(SPEND_BAND_BY_CATEGORY.get(str(r["category"]), 30000.0) * scale, 2))
    out["spend_under_contract_eur"] = spend
    return out


def build_indirect(cfg: LakeConfig, register: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """v0.1 ``build_indirect_spend`` with role-only suppliers, in the ``finance/indirect_spend`` shape."""
    contracted = pd.DataFrame(
        [
            {"supplier": INDIRECT_CONTRACTED_SUPPLIER.get(str(cat), str(cfg.suppliers_indirect[0])), "category": str(cat)}
            for cat in cfg.indirect_categories
        ]
    )
    v01 = build_indirect_spend(cfg, contracted, rng)
    out = v01.rename(
        columns={
            "supplier": "supplier_name",
            "amount": "amount_eur",
            "saving": "saving_eur",
            "baseline_amount": "baseline_amount_eur",
        }
    )
    out = out[list(INDIRECT_COLUMNS)].reset_index(drop=True)
    assert all(is_allowed_name(n) for n in out["supplier_name"].unique())
    return out


__all__ = [
    "REGISTER_COLUMNS",
    "INDIRECT_COLUMNS",
    "SPEND_BAND_BY_CATEGORY",
    "INDIRECT_CONTRACTED_SUPPLIER",
    "build_register",
    "register_index",
    "contract_in_force",
    "fill_register_spend",
    "build_indirect",
]
