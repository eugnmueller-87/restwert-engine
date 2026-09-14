"""Supplier contracts, PO linking and indirect spend (SPEC.md section 3.2).

Supplier contracts: 6 hardware contracts (one per hardware supplier, category
``hardware``, 24 to 36 months, ``price_protection`` true for 4 with claim windows
30/45/60 days, payment terms 30/45/60, ``spend_under_contract`` = 12 x the
supplier's monthly PO value) and 8 indirect contracts (one per indirect category,
suppliers ``Supplier-G .. Supplier-L``, some ``auto_renewal``, notice 30 to 90 days).
End dates: about 30 % within 6 months after ``as_of``, about 20 % already expired,
the rest later. Ids ``SC-01`` ...

``link_purchase_orders`` sets ``purchase_orders.supplier_contract_id`` to the
hardware contract of the supplier when ``order_date`` lies within the contract
dates, else NULL. The NULL share therefore follows from the date overlap of a
24 to 36 month contract with a four-year PO history; it is not forced to a
target.

Indirect spend: ``indirect_rows_per_month`` invoice lines per month from
``history_start`` to ``as_of``; supplier is the category's contracted supplier
(70 %) or a random indirect supplier; amount lognormal with median 1800 EUR;
``has_po`` Bernoulli(``indirect_has_po``); ``has_contract`` true 90 % for the
contracted supplier and with a small residual probability otherwise (chosen so
the overall share lands near ``indirect_has_contract``); a share
``indirect_saving_share`` carries ``saving = amount * U(0.03, 0.12)`` of which
``indirect_confirmed_share`` are confirmed by controlling.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.dates import add_months, month_floor

SUPPLIER_CONTRACT_COLUMNS = [
    "supplier_contract_id", "supplier", "category", "start_date", "end_date", "auto_renewal", "notice_days",
    "price_protection", "price_protection_days", "payment_terms_days", "spend_under_contract",
]
INDIRECT_COLUMNS = [
    "spend_id", "invoice_date", "category", "supplier", "amount", "has_po", "has_contract",
    "saving", "saving_confirmed_by_controlling", "saving_type", "baseline_amount",
]

HARDWARE_PRICE_PROTECTED: int = 4
PRICE_PROTECTION_DAYS_OPTIONS: tuple[int, ...] = (30, 45, 60)
PAYMENT_TERMS_OPTIONS: tuple[int, ...] = (30, 45, 60)
INDIRECT_AMOUNT_MEDIAN_EUR: float = 1800.0
INDIRECT_AMOUNT_SIGMA: float = 0.8
CONTRACTED_SUPPLIER_SHARE: float = 0.70
CONTRACTED_HAS_CONTRACT_SHARE: float = 0.90
RENEWAL_HORIZON_MONTHS: int = 6


def _end_buckets(n: int) -> list[str]:
    """Bucket labels for ``n`` contracts: ~30 % 'soon', ~20 % 'expired', rest 'later'."""
    n_soon = max(1, int(round(0.30 * n)))
    n_expired = max(1, int(round(0.20 * n)))
    buckets = ["soon"] * n_soon + ["expired"] * n_expired
    buckets += ["later"] * (n - len(buckets))
    return buckets[:n]


def _draw_end_date(bucket: str, as_of: date, rng: np.random.Generator) -> date:
    horizon = add_months(as_of, RENEWAL_HORIZON_MONTHS)
    if bucket == "soon":
        return as_of + timedelta(days=int(rng.integers(15, (horizon - as_of).days + 1)))
    if bucket == "expired":
        return as_of - timedelta(days=int(rng.integers(30, 400)))
    return horizon + timedelta(days=int(rng.integers(30, 540)))


def _start_before_as_of(end: date, term_months: int, as_of: date) -> date:
    """Contract start ``term_months`` before ``end``, moved earlier so every contract is
    already running at ``as_of`` (a contract that starts in the future is not in force)."""
    start = add_months(end, -term_months)
    latest_start = add_months(as_of, -3)
    return min(start, latest_start)


def build_supplier_contracts(
    cfg: GeneratorConfig, rng: np.random.Generator, purchase_orders: pd.DataFrame | None = None
) -> pd.DataFrame:
    """6 hardware + 8 indirect supplier contracts (see module docstring)."""
    rows: list[dict] = []
    n_hw = len(cfg.suppliers_hardware)
    hw_buckets = _end_buckets(n_hw)
    hw_buckets = list(rng.permutation(hw_buckets))
    po_value_per_month: dict[str, float] = {}
    if purchase_orders is not None and len(purchase_orders):
        po = purchase_orders
        value = po["qty_delivered"].astype(float) * po["unit_price"].astype(float)
        span_months = max(1.0, (po["order_date"].max() - po["order_date"].min()).days / 30.4375)
        for sup, v in value.groupby(po["supplier"]).sum().items():
            po_value_per_month[str(sup)] = float(v) / span_months
    for i, sup in enumerate(cfg.suppliers_hardware):
        end = _draw_end_date(str(hw_buckets[i]), cfg.as_of, rng)
        term = int(rng.integers(24, 37))
        start = _start_before_as_of(end, term, cfg.as_of)
        monthly = po_value_per_month.get(sup, 25000.0)
        rows.append(
            {
                "supplier": sup,
                "category": "hardware",
                "start_date": start,
                "end_date": end,
                "auto_renewal": bool(rng.random() < 0.30),
                "notice_days": int(rng.choice([60, 90, 120])),
                "price_protection": i < HARDWARE_PRICE_PROTECTED,
                "price_protection_days": int(rng.choice(PRICE_PROTECTION_DAYS_OPTIONS)) if i < HARDWARE_PRICE_PROTECTED else None,
                "payment_terms_days": int(rng.choice(PAYMENT_TERMS_OPTIONS)),
                "spend_under_contract": round(12.0 * monthly, 2),
            }
        )
    cats = list(cfg.indirect_categories)
    ind_buckets = list(rng.permutation(_end_buckets(len(cats))))
    for j, cat in enumerate(cats):
        sup = cfg.suppliers_indirect[j % len(cfg.suppliers_indirect)]
        end = _draw_end_date(str(ind_buckets[j]), cfg.as_of, rng)
        term = int(rng.integers(12, 37))
        start = _start_before_as_of(end, term, cfg.as_of)
        annual = float(np.exp(rng.normal(np.log(120000.0), 0.7)))
        rows.append(
            {
                "supplier": sup,
                "category": cat,
                "start_date": start,
                "end_date": end,
                "auto_renewal": bool(rng.random() < 0.50),
                "notice_days": int(rng.integers(30, 91)),
                "price_protection": False,
                "price_protection_days": None,
                "payment_terms_days": int(rng.choice(PAYMENT_TERMS_OPTIONS)),
                "spend_under_contract": round(annual, 2),
            }
        )
    df = pd.DataFrame(rows)
    df.insert(0, "supplier_contract_id", [f"SC-{i + 1:02d}" for i in range(len(df))])
    return df[SUPPLIER_CONTRACT_COLUMNS]


def link_purchase_orders(purchase_orders: pd.DataFrame, supplier_contracts: pd.DataFrame) -> pd.DataFrame:
    """Set ``supplier_contract_id`` where ``order_date`` falls inside the supplier's hardware contract."""
    po = purchase_orders.copy()
    hw = supplier_contracts[supplier_contracts["category"] == "hardware"]
    by_supplier = {r["supplier"]: r for _, r in hw.iterrows()}
    linked: list[str | None] = []
    for _, r in po.iterrows():
        c = by_supplier.get(r["supplier"])
        if c is not None and c["start_date"] <= r["order_date"] <= c["end_date"]:
            linked.append(str(c["supplier_contract_id"]))
        else:
            linked.append(None)
    po["supplier_contract_id"] = linked
    return po


def build_indirect_spend(
    cfg: GeneratorConfig, supplier_contracts: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Monthly indirect invoice lines from ``history_start`` to ``as_of``."""
    contracted: dict[str, str] = {
        str(r["category"]): str(r["supplier"])
        for _, r in supplier_contracts[supplier_contracts["category"] != "hardware"].iterrows()
    }
    p_else = (cfg.indirect_has_contract - CONTRACTED_SUPPLIER_SHARE * CONTRACTED_HAS_CONTRACT_SHARE) / (
        1.0 - CONTRACTED_SUPPLIER_SHARE
    )
    p_else = float(min(max(p_else, 0.0), 1.0))
    cats = list(cfg.indirect_categories)
    mix = dict(getattr(cfg, "indirect_saving_type_mix", None) or {"hard_price_reduction": 1.0})
    type_names = list(mix)
    total_p = float(sum(mix.values())) or 1.0
    type_probs = [float(mix[t]) / total_p for t in type_names]
    rows: list[dict] = []
    cur = month_floor(cfg.history_start)
    while cur <= cfg.as_of:
        ndays = calendar.monthrange(cur.year, cur.month)[1]
        for _ in range(cfg.indirect_rows_per_month):
            inv = cur + timedelta(days=int(rng.integers(0, ndays)))
            if inv > cfg.as_of:
                inv = cfg.as_of
            cat = str(cats[int(rng.integers(0, len(cats)))])
            use_contracted = cat in contracted and rng.random() < CONTRACTED_SUPPLIER_SHARE
            supplier = contracted[cat] if use_contracted else str(cfg.suppliers_indirect[int(rng.integers(0, len(cfg.suppliers_indirect)))])
            amount = round(float(np.exp(rng.normal(np.log(INDIRECT_AMOUNT_MEDIAN_EUR), INDIRECT_AMOUNT_SIGMA))), 2)
            has_po = bool(rng.random() < cfg.indirect_has_po)
            has_contract = bool(rng.random() < (CONTRACTED_HAS_CONTRACT_SHARE if use_contracted else p_else))
            saving = 0.0
            confirmed = False
            saving_type = None
            baseline = None
            if rng.random() < cfg.indirect_saving_share:
                saving = round(amount * float(rng.uniform(0.03, 0.12)), 2)
                confirmed = bool(rng.random() < cfg.indirect_confirmed_share)
                saving_type = str(type_names[int(rng.choice(len(type_names), p=type_probs))])
                baseline = round(amount + saving, 2)  # what would have been paid: saving = baseline - amount
            rows.append(
                {
                    "invoice_date": inv,
                    "category": cat,
                    "supplier": supplier,
                    "amount": amount,
                    "has_po": has_po,
                    "has_contract": has_contract,
                    "saving": saving,
                    "saving_confirmed_by_controlling": confirmed,
                    "saving_type": saving_type,
                    "baseline_amount": baseline,
                }
            )
        cur = add_months(cur, 1)
    df = pd.DataFrame(rows, columns=[c for c in INDIRECT_COLUMNS if c != "spend_id"])
    df = df.sort_values(["invoice_date"], kind="stable").reset_index(drop=True)
    df.insert(0, "spend_id", [f"IS-{i + 1:06d}" for i in range(len(df))])
    return df[INDIRECT_COLUMNS]


__all__ = [
    "build_supplier_contracts",
    "link_purchase_orders",
    "build_indirect_spend",
    "SUPPLIER_CONTRACT_COLUMNS",
    "INDIRECT_COLUMNS",
]
