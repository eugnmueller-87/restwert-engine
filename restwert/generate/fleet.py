"""Fleet, purchase orders and benchmarks (SPEC.md sections 3.1 and 3.2, ``build_devices``).

Rules (all synthetic design parameters):

* ``n_devices`` split by ``share_of_fleet``, plus 5 % spares per family. Spares are
  bought like any other device but get no initial rental contract; the events
  builder deploys them as replacements. The returned devices frame carries a
  helper column ``_spare`` that ``generate_all`` drops before returning.
* Purchase dates uniform by month between ``history_start`` and ``purchase_end``.
* Model = latest generation launched before ``purchase_date`` (80 %) or the
  previous one (20 %).
* ``purchase_price = round(list_price * (1 - U(discount_min, discount_max)), 2)``,
  ``landed_cost = round(purchase_price * (1 + freight_duty_pct), 2)``.
* One PO per (cohort month, family, supplier, model); OTIF variance and short
  deliveries per ``otif_on_time_share`` / ``po_short_delivery_share``.
  ``supplier_contract_id`` is linked later by ``procurement.link_purchase_orders``.
* Benchmarks: one row per model, ``list_price * 0.85 * 1.03``, explicitly labelled
  as a generator band midpoint and not a market figure.
"""

from __future__ import annotations

import bisect
import calendar
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.config import GeneratorConfig
from restwert.dates import add_months, month_floor

COLOURS: tuple[str, ...] = ("black", "silver", "blue", "graphite", "white")
SPARE_SHARE: float = 0.05
CHANNEL_IN_MIX: tuple[tuple[str, float], ...] = (("distributor", 0.60), ("oem_direct", 0.30), ("refurb_buyback", 0.10))
BENCHMARK_BAND_FACTOR: float = 0.85          # design parameter: band midpoint of the discount range
BENCHMARK_LANDED_FACTOR: float = 1.03        # design parameter: freight and duty uplift
BENCHMARK_SOURCE_NOTE = "synthetic: generator band midpoint, not a market figure"
PRICE_DROP_WINDOW_DAYS: int = 45

DEVICE_COLUMNS = [
    "serial", "model_family", "model", "storage_gb", "colour", "launch_date", "purchase_date",
    "purchase_price", "landed_cost", "supplier", "channel_in", "po_number", "contract_id",
]
PO_COLUMNS = [
    "po_number", "supplier", "supplier_contract_id", "model", "order_date", "promised_date", "delivered_date",
    "qty_ordered", "qty_delivered", "unit_price", "benchmark_price", "price_drop_date", "price_drop_amount",
]
BENCHMARK_COLUMNS = ["model", "valid_from", "landed_cost_benchmark", "source_note"]


def _cohort_months(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = month_floor(start)
    last = month_floor(end)
    while cur <= last:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def _family_counts(cfg: GeneratorConfig, n: int) -> dict[str, int]:
    fams = list(cfg.families)
    shares = np.array([cfg.families[f].share_of_fleet for f in fams], dtype=float)
    shares = shares / shares.sum()
    counts = np.floor(n * shares).astype(int)
    counts[int(np.argmax(shares))] += n - int(counts.sum())
    return {f: int(c) for f, c in zip(fams, counts)}


def build_devices(
    cfg: GeneratorConfig, catalogue: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return ``(devices, purchase_orders, benchmarks)``.

    ``devices`` carries the helper column ``_spare`` (bool) and ``contract_id = None``;
    ``purchase_orders.supplier_contract_id`` is ``None`` until linked.
    """
    months = _cohort_months(cfg.history_start, cfg.purchase_end)
    counts = _family_counts(cfg, cfg.n_devices)
    channel_names = [c for c, _ in CHANNEL_IN_MIX]
    channel_p = np.array([p for _, p in CHANNEL_IN_MIX])
    rows: list[dict] = []
    for fam, count in counts.items():
        fc = cfg.families[fam]
        n_spare = int(math.ceil(count * SPARE_SHARE)) if count > 0 else 0
        n_total = count + n_spare
        cat = catalogue[catalogue["model_family"] == fam].sort_values("launch_date").reset_index(drop=True)
        if cat.empty:
            raise ValueError(f"catalogue has no model for family {fam}")
        launches: list[date] = list(cat["launch_date"])
        month_idx = rng.integers(0, len(months), n_total)
        for i in range(n_total):
            m0 = months[int(month_idx[i])]
            ndays = calendar.monthrange(m0.year, m0.month)[1]
            pdate = m0 + timedelta(days=int(rng.integers(0, ndays)))
            k = bisect.bisect_left(launches, pdate)  # launches strictly before pdate
            idx = max(k - 1, 0)
            if idx > 0 and rng.random() < 0.20:
                idx -= 1
            model_row = cat.iloc[idx]
            storage = int(rng.choice(fc.storage_options))
            colour = str(rng.choice(COLOURS))
            disc = float(rng.uniform(fc.discount_min, fc.discount_max))
            purchase_price = round(float(model_row["list_price"]) * (1.0 - disc), 2)
            landed_cost = round(purchase_price * (1.0 + fc.freight_duty_pct), 2)
            supplier = str(rng.choice(cfg.suppliers_hardware))
            channel_in = str(channel_names[int(rng.choice(len(channel_names), p=channel_p))])
            rows.append(
                {
                    "model_family": fam,
                    "model": str(model_row["model"]),
                    "storage_gb": storage,
                    "colour": colour,
                    "launch_date": model_row["launch_date"],
                    "purchase_date": pdate,
                    "purchase_price": purchase_price,
                    "landed_cost": landed_cost,
                    "supplier": supplier,
                    "channel_in": channel_in,
                    "po_number": None,
                    "contract_id": None,
                    "_spare": bool(i >= count),
                }
            )
    devices = pd.DataFrame(rows)
    devices = devices.sort_values(["purchase_date"], kind="stable").reset_index(drop=True)
    devices.insert(0, "serial", [f"D-{i + 1:06d}" for i in range(len(devices))])

    purchase_orders = _build_purchase_orders(cfg, devices, catalogue, rng)
    benchmarks = _build_benchmarks(catalogue)
    devices = devices[DEVICE_COLUMNS + ["_spare"]]
    return devices, purchase_orders, benchmarks


def _build_purchase_orders(
    cfg: GeneratorConfig, devices: pd.DataFrame, catalogue: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    list_price = dict(zip(catalogue["model"], catalogue["list_price"]))
    launches_by_family: dict[str, list[date]] = {
        fam: sorted(list(grp["launch_date"])) for fam, grp in catalogue.groupby("model_family")
    }
    cohort = devices["purchase_date"].map(lambda d: f"{d.year:04d}-{d.month:02d}")
    keys = pd.DataFrame(
        {
            "cohort": cohort,
            "model_family": devices["model_family"],
            "supplier": devices["supplier"],
            "model": devices["model"],
        }
    )
    grouped = keys.groupby(["cohort", "model_family", "supplier", "model"], sort=True).indices
    po_rows: list[dict] = []
    po_of_device = np.empty(len(devices), dtype=object)
    n = 0
    for (coh, fam, supplier, model), idx in grouped.items():
        n += 1
        po_number = f"PO-{n:06d}"
        idx = np.asarray(idx)
        po_of_device[idx] = po_number
        qty = int(len(idx))
        qty_delivered = qty
        if rng.random() < cfg.po_short_delivery_share:
            qty_delivered = max(1, int(round(qty * float(rng.uniform(0.90, 0.97)))))
        min_purchase: date = min(devices["purchase_date"].iloc[idx])
        order_date = min_purchase - timedelta(days=21)
        promised_date = order_date + timedelta(days=int(rng.integers(14, 31)))
        if rng.random() < cfg.otif_on_time_share:
            delivered_date = promised_date
        else:
            delivered_date = promised_date + timedelta(days=int(rng.integers(1, 22)))
        unit_price = round(float(devices["purchase_price"].iloc[idx].mean()), 2)
        benchmark_price = round(float(list_price[model]) * BENCHMARK_BAND_FACTOR, 2)
        price_drop_date = None
        price_drop_amount = None
        eligible_launch = None
        for ld in launches_by_family.get(fam, []):
            if delivered_date < ld <= delivered_date + timedelta(days=PRICE_DROP_WINDOW_DAYS):
                eligible_launch = ld
                break
        if eligible_launch is not None and rng.random() < cfg.price_drop_share:
            price_drop_date = eligible_launch
            price_drop_amount = round(unit_price * cfg.price_drop_pct, 2)
        po_rows.append(
            {
                "po_number": po_number,
                "supplier": supplier,
                "supplier_contract_id": None,
                "model": model,
                "order_date": order_date,
                "promised_date": promised_date,
                "delivered_date": delivered_date,
                "qty_ordered": qty,
                "qty_delivered": qty_delivered,
                "unit_price": unit_price,
                "benchmark_price": benchmark_price,
                "price_drop_date": price_drop_date,
                "price_drop_amount": price_drop_amount,
            }
        )
    devices["po_number"] = po_of_device
    return pd.DataFrame(po_rows, columns=PO_COLUMNS)


def _build_benchmarks(catalogue: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "model": r["model"],
            "valid_from": r["launch_date"],
            "landed_cost_benchmark": round(float(r["list_price"]) * BENCHMARK_BAND_FACTOR * BENCHMARK_LANDED_FACTOR, 2),
            "source_note": BENCHMARK_SOURCE_NOTE,
        }
        for _, r in catalogue.iterrows()
    ]
    return pd.DataFrame(rows, columns=BENCHMARK_COLUMNS)


__all__ = [
    "build_devices",
    "DEVICE_COLUMNS",
    "PO_COLUMNS",
    "BENCHMARK_COLUMNS",
    "SPARE_SHARE",
    "BENCHMARK_SOURCE_NOTE",
]
