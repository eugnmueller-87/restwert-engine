"""The synthetic fleet drawn from the real catalogue (SPEC_v0.2 section 5.4, ``build_fleet``).

One row per serial: ``n`` serials plus 5 % spares per catalogue family (``_spare``).
Per serial (all design parameters from ``config/lake.yaml``):

* order month uniform over [history_start, purchase_end], day uniform inside the month
  (spares: the first six months of the history, a spares stock is bought upfront so that
  a replacement never ships before the spare was received);
* catalogue family by ``fleet_mix``, manufacturer by ``oem_share[family]``;
* slug by model generation, not by newest slug (``_SlugIndex.generations``): the window is
  anchored on the NEWEST launch of (family, oem) on or before the order date, not on the order
  date itself. The CURRENT generation is every slug launched inside the last
  ``generation_window_months`` (config) up to and including that newest launch; the PREVIOUS
  generation is every slug launched in the window before that; everything older is the OLDER
  pool. Anchoring on the newest launch keeps a generation together however long ago it
  launched: an order placed 20 months after the last launch still sees the three slugs
  launched within weeks of each other as one current generation, instead of the single
  latest slug taking the whole ``newest_model_share`` (the order-date anchor with a
  newest-slug fallback did exactly that: one slug dominated the fleet as soon as the last
  launch was older than the window). The draw picks the current
  generation with probability ``newest_model_share``, the previous one with
  ``previous_generation_share`` (config), the older pool otherwise, and then one slug
  UNIFORMLY inside the picked set; a picked set that is empty falls back to the next newer
  set (older -> previous -> current), so an empty previous window hands its share to the
  current generation;
* a manufacturer with no slug launched by then is redrawn up to three times,
  then the order date is redrawn as well (redraws counted in ``fleet.attrs["oem_redraws"]``;
  the catalogue's first laptop launches in January 2022, so early laptop orders move);
* variant uniform over the slug's priced, storage-bearing variants;
* supplier route by ``supplier_route``; the reseller name uniform over ``resellers``;
* discount ``d ~ U(discount_by_oem[oem])`` minus ``U(reseller_markup_pct)`` on the
  reseller route; ``unit_price_eur = round(rrp_net * (1 - d), 2)``;
* ``serial = SN-<OEM code>-<8 hex>`` (unique), colour from a fixed list.

Spares are bought like any other device but get no initial rental contract; the v0.1
events builder deploys them as replacements.
"""

from __future__ import annotations

import bisect
import calendar
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.dates import add_months, month_floor
from restwert.lake.common import CATALOGUE_FAMILIES, OEM_CODES, ROLE_ONLY_SUFFIX
from restwert.lakegen.catalogue import FleetCatalogue
from restwert.lakegen.config import LakeConfig

COLOURS: tuple[str, ...] = ("black", "silver", "blue", "graphite", "white")
SPARE_SHARE: float = 0.05
SPARE_ORDER_MONTHS: int = 6        # spares are ordered inside the first months of the history (stock bought upfront)
# p(previous generation) is cfg.previous_generation_share (config/lake.yaml, owned); the remainder after
# newest_model_share + previous_generation_share goes to the older pool
MAX_OEM_REDRAWS: int = 50          # order-date redraws before giving up
OEM_REDRAWS_PER_DATE: int = 3      # manufacturer redraws before the order date is redrawn as well

FLEET_COLUMNS: tuple[str, ...] = (
    "serial", "_spare", "catalogue_family", "oem", "fleet_family", "slug", "model_name", "series",
    "launch_date", "successor_launch_date", "spec", "storage_gb", "rrp_gross", "rrp_net", "base_storage_gb",
    "colour", "order_date", "supplier_role", "supplier_name", "supplier_id", "discount_pct", "unit_price_eur",
)


def supplier_id_of(supplier_role: str, supplier_name: str, resellers: list[str]) -> str:
    """``SUP-<OEM code>`` for a manufacturer, ``SUP-RSL-A``, ``SUP-RSL-B``, ... for a reseller."""
    if supplier_role == "manufacturer":
        return f"SUP-{OEM_CODES[supplier_name]}"
    idx = list(resellers).index(supplier_name)
    return f"SUP-RSL-{chr(ord('A') + idx)}"


def family_counts(n: int, fleet_mix: dict[str, float]) -> dict[str, int]:
    """Floor ``n * share`` per catalogue family; the remainder goes to the largest share."""
    fams = [f for f in CATALOGUE_FAMILIES if f in fleet_mix]
    shares = np.array([float(fleet_mix[f]) for f in fams], dtype=float)
    shares = shares / shares.sum()
    counts = np.floor(n * shares).astype(int)
    counts[int(np.argmax(shares))] += n - int(counts.sum())
    return {f: int(c) for f, c in zip(fams, counts)}


def _month_list(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = month_floor(start)
    last = month_floor(end)
    while cur <= last:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


class _SlugIndex:
    """Per (family, oem): launch dates ascending with their slugs; ``latest`` and ``generations`` by bisect.

    This index, not ``catalogue.latest_slugs``, is what ``build_fleet`` draws from.
    """

    def __init__(self, pool: pd.DataFrame) -> None:
        self.dates: dict[tuple[str, str], list[date]] = {}
        self.slugs: dict[tuple[str, str], list[str]] = {}
        self.variant_rows: dict[str, list[int]] = {}
        firsts = pool.drop_duplicates("slug", keep="first").sort_values(["launch_date", "slug"], kind="stable")
        for _, r in firsts.iterrows():
            key = (str(r["family"]), str(r["oem"]))
            self.dates.setdefault(key, []).append(r["launch_date"])
            self.slugs.setdefault(key, []).append(str(r["slug"]))
        for i, slug in enumerate(pool["slug"].astype(str)):
            self.variant_rows.setdefault(slug, []).append(i)

    def latest(self, family: str, oem: str, before: date) -> list[str]:
        """Every slug launched on or before ``before``, newest first (empty when none)."""
        key = (family, oem)
        if key not in self.dates:
            return []
        k = bisect.bisect_right(self.dates[key], before)
        return list(reversed(self.slugs[key][:k]))

    def generations(self, family: str, oem: str, order_date: date, window_months: int) -> tuple[list[str], list[str], list[str]]:
        """``(current, previous, older)`` slugs of (family, oem) at ``order_date``, each newest first.

        The window is anchored on ``newest``, the latest launch date of (family, oem) on or
        before ``order_date``, so that a generation stays one set however long ago it launched:

        current  = launched in ``(newest - window, newest]`` (never empty once anything launched:
                   the newest slug itself is inside);
        previous = launched in ``(newest - 2 x window, newest - window]`` (may be empty; the
                   caller's draw then hands its share to the current set);
        older    = launched on or before ``newest - 2 x window``.

        Two models launched on the same day always land in the same set. All three are empty
        only when nothing launched by ``order_date``. An order-date anchor (the earlier rule)
        made the current set empty as soon as the last launch was older than the window, and
        its newest-slug fallback then put a whole manufacturer on one slug.
        """
        key = (family, oem)
        if key not in self.dates:
            return [], [], []
        k = bisect.bisect_right(self.dates[key], order_date)
        if k == 0:
            return [], [], []
        launched = list(zip(self.dates[key][:k], self.slugs[key][:k]))  # ascending by launch date
        window = max(int(window_months), 1)
        newest = launched[-1][0]
        cur_lo = add_months(newest, -window)
        prev_lo = add_months(newest, -2 * window)
        current = [s for d, s in launched if d > cur_lo]
        previous = [s for d, s in launched if prev_lo < d <= cur_lo]
        older = [s for d, s in launched if d <= prev_lo]
        return list(reversed(current)), list(reversed(previous)), list(reversed(older))


def build_fleet(cfg: LakeConfig, cat: FleetCatalogue, rng: np.random.Generator, n: int) -> pd.DataFrame:
    """One row per serial (see module docstring); ``attrs['oem_redraws']`` counts redraws."""
    if n < 1:
        raise ValueError("n serials must be >= 1")
    pool = cat.pool
    if pool.empty:
        raise ValueError("the catalogue pool is empty; no usable slug with a priced, storage-bearing variant")
    index = _SlugIndex(pool)
    pool_records = pool.to_dict("records")
    months = _month_list(cfg.history_start, cfg.purchase_end)
    counts = family_counts(n, cfg.fleet_mix)
    resellers = list(cfg.resellers)
    route_names = ["manufacturer", "reseller"]
    route_p = np.array([float(cfg.supplier_route[k]) for k in route_names], dtype=float)
    markup_lo, markup_hi = (float(x) for x in cfg.reseller_markup_pct)
    window_months = int(cfg.generation_window_months)
    p_current = float(cfg.newest_model_share)
    p_previous = float(cfg.previous_generation_share)
    seen: set[str] = set()
    rows: list[dict] = []
    redraws = 0

    for fam in [f for f in CATALOGUE_FAMILIES if f in counts]:
        count = counts[fam]
        n_spare = int(math.ceil(count * SPARE_SHARE)) if count > 0 else 0
        shares = cfg.oem_share[fam]
        oems = sorted(shares)
        oem_p = np.array([float(shares[o]) for o in oems], dtype=float)
        oem_p = oem_p / oem_p.sum()
        for i in range(count + n_spare):
            slugs: list[str] = []
            tries = 0
            while not slugs:
                if tries > MAX_OEM_REDRAWS:
                    raise ValueError(f"no slug of family {fam} launched inside the order window for any manufacturer")
                # spares are bought upfront: their order month lies in the first half-year of the history
                if i >= count:
                    m0 = months[int(rng.integers(0, min(SPARE_ORDER_MONTHS, len(months))))]
                else:
                    m0 = months[int(rng.integers(0, len(months)))]
                ndays = calendar.monthrange(m0.year, m0.month)[1]
                order_date = m0 + timedelta(days=int(rng.integers(0, ndays)))
                order_date = min(max(order_date, cfg.history_start), cfg.purchase_end)
                oem = str(oems[int(rng.choice(len(oems), p=oem_p))])
                slugs = index.latest(fam, oem, order_date)
                for _ in range(OEM_REDRAWS_PER_DATE):
                    if slugs:
                        break
                    redraws += 1
                    oem = str(oems[int(rng.choice(len(oems), p=oem_p))])
                    slugs = index.latest(fam, oem, order_date)
                tries += 1
            # generation draw: current with newest_model_share, previous with previous_generation_share,
            # else older; then uniform inside the set; an empty set falls back to the next newer one
            sets = index.generations(fam, oem, order_date, window_months)
            u = float(rng.random())
            if u < p_current:
                pick = 0
            elif u < p_current + p_previous:
                pick = 1
            else:
                pick = 2
            while pick > 0 and not sets[pick]:
                pick -= 1
            chosen = sets[pick]
            slug = chosen[int(rng.integers(0, len(chosen)))]
            vrows = index.variant_rows[slug]
            v = pool_records[vrows[int(rng.integers(0, len(vrows)))]]
            route = str(route_names[int(rng.choice(len(route_names), p=route_p))])
            if route == "reseller":
                supplier_name = str(resellers[int(rng.integers(0, len(resellers)))])
            else:
                supplier_name = oem
            lo, hi = (float(x) for x in cfg.discount_by_oem[oem])
            d = float(rng.uniform(lo, hi))
            if route == "reseller":
                d -= float(rng.uniform(markup_lo, markup_hi))
            d = max(d, 0.0)
            colour = str(COLOURS[int(rng.integers(0, len(COLOURS)))])
            while True:
                serial = f"SN-{OEM_CODES[oem]}-{int(rng.integers(0, 16 ** 8)):08X}"
                if serial not in seen:
                    seen.add(serial)
                    break
            rrp_net = float(v["rrp_net"])
            rows.append(
                {
                    "serial": serial,
                    "_spare": bool(i >= count),
                    "catalogue_family": fam,
                    "oem": oem,
                    "fleet_family": str(v["fleet_family"]),
                    "slug": slug,
                    "model_name": str(v["model_name"]),
                    "series": None if v["series"] is None or (isinstance(v["series"], float) and math.isnan(v["series"])) else str(v["series"]),
                    "launch_date": v["launch_date"],
                    "successor_launch_date": v["successor_launch_date"],
                    "spec": str(v["spec"]),
                    "storage_gb": int(v["storage_gb"]),
                    "rrp_gross": float(v["rrp_gross"]),
                    "rrp_net": rrp_net,
                    "base_storage_gb": int(v["base_storage_gb"]),
                    "colour": colour,
                    "order_date": order_date,
                    "supplier_role": route,
                    "supplier_name": supplier_name,
                    "supplier_id": supplier_id_of(route, supplier_name, resellers),
                    "discount_pct": round(d, 6),
                    "unit_price_eur": round(rrp_net * (1.0 - d), 2),
                }
            )
    fleet = pd.DataFrame(rows, columns=list(FLEET_COLUMNS))
    fleet = fleet.sort_values(["order_date", "serial"], kind="stable").reset_index(drop=True)
    fleet.attrs["oem_redraws"] = int(redraws)
    fleet.attrs["n_spares"] = int(fleet["_spare"].sum())
    assert all(s.endswith(ROLE_ONLY_SUFFIX) or s in OEM_CODES for s in fleet["supplier_name"].unique())
    return fleet


__all__ = ["build_fleet", "family_counts", "supplier_id_of", "FLEET_COLUMNS", "COLOURS", "SPARE_SHARE"]
