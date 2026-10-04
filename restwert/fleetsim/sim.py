"""The fleet simulation: one event log per serial, the rest derived (see __init__ for the rule).

Vectorised with numpy over the whole fleet; the only loops run over purchase months and
manufacturers (model choice) and over the report tables. 400,000 serials run in well under a
minute on a laptop.

Conventions
- Dates are numpy datetime64[D]; a month is 30.4375 days when a term is converted.
- Every random draw comes from one seeded Generator, so a run is reproducible by seed.
- No real names beyond the catalogue manufacturers; the company is "the DaaS provider".
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from restwert.paths import CATALOGUE_DIR, CONFIG_DIR, DATA_DIR, MARKET_CURVES_CSV, OUTPUTS_DIR, ROOT

DAYS_PER_MONTH = 30.4375
FAMILIES = ("Smartphone", "Tablet", "Laptop")
GRADES = ("A", "B", "C", "D")
STATIONS = (
    "eingang",
    "mdm_freigabe",
    "loeschung_grading",
    "reparatur",
    "aufbereitung",
    "verkaufsfaehig",
    "swap_puffer",
    "neugeraet",
)
STATION_LABEL = {
    "eingang": "Eingang und Sperre",
    "mdm_freigabe": "MDM-Freigabe durch den Altkunden",
    "loeschung_grading": "Löschung und Grading",
    "reparatur": "Reparatur",
    "aufbereitung": "Aufbereitung für den Zweitzyklus",
    "verkaufsfaehig": "Verkaufsfähig, wartet auf Abverkauf",
    "swap_puffer": "Swap-Puffer (Ersatzgeräte)",
    "neugeraet": "Neugerät, vor Auslieferung",
}
AGING_BUCKETS = [(0, 30, "0 bis 30 Tage"), (31, 60, "31 bis 60"), (61, 90, "61 bis 90"),
                 (91, 180, "91 bis 180"), (181, 365, "181 bis 365"), (366, 10**6, "über 365")]


# ---------------------------------------------------------------- config and inputs

def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or (CONFIG_DIR / "fleetsim.yaml")
    with open(p, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["_path"] = str(p)
    return cfg


def _read_public_csv(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    return pd.DataFrame(list(csv.DictReader(lines)))


def load_catalogue(catalogue_dir: Path = CATALOGUE_DIR) -> pd.DataFrame:
    """Priced variants joined with their model: the pool a serial is drawn from."""
    models = _read_public_csv(catalogue_dir / "models.csv")
    variants = _read_public_csv(catalogue_dir / "variants.csv")
    variants = variants[variants["rrp_eur_launch_de"].astype(str).str.strip() != ""].copy()
    variants["rrp_eur_launch_de"] = variants["rrp_eur_launch_de"].astype(float)
    cat = variants.merge(models[["slug", "model_name", "oem", "family", "series", "launch_date_de"]], on="slug", how="inner")
    cat = cat[cat["family"].isin(FAMILIES)].copy()
    cat["launch_date"] = pd.to_datetime(cat["launch_date_de"], errors="coerce")
    cat = cat.dropna(subset=["launch_date"]).reset_index(drop=True)
    cat["variant_id"] = np.arange(len(cat))
    cat["storage_gb"] = pd.to_numeric(cat["storage_gb"], errors="coerce")
    return cat[["variant_id", "slug", "model_name", "spec", "storage_gb", "oem", "family", "series", "launch_date", "rrp_eur_launch_de"]]


def load_curves(path: Path = MARKET_CURVES_CSV) -> dict[str, dict[str, float]]:
    """Family curves, marketplace population: log(price/RRP) = intercept + slope * age_months, grade offsets in log space."""
    out: dict[str, dict[str, float]] = {}
    if not path.exists():
        return out
    df = pd.read_csv(path)
    df = df[(df["population"] == "marketplace") & (df["group_kind"].isin(["family", "all"]))]
    for _, r in df.iterrows():
        key = str(r["group"])
        out[key] = {
            "intercept": float(r["intercept"]),
            "slope": float(r["slope_per_month"]),
            "A": float(r["grade_A_offset"]) if pd.notna(r["grade_A_offset"]) else 0.0,
            "C": float(r["grade_C_offset"]) if pd.notna(r["grade_C_offset"]) else -0.15,
            "D": float(r["grade_D_offset"]) if pd.notna(r["grade_D_offset"]) else -0.30,
        }
    return out


# ---------------------------------------------------------------- helpers

def _months_to_days(m: np.ndarray | float) -> np.ndarray:
    return np.asarray(np.round(np.asarray(m, dtype=float) * DAYS_PER_MONTH), dtype="timedelta64[D]")


def _choice_from_mix(rng: np.random.Generator, mix: dict[Any, float], n: int) -> np.ndarray:
    keys = list(mix.keys())
    p = np.asarray([float(mix[k]) for k in keys])
    p = p / p.sum()
    idx = rng.choice(len(keys), size=n, p=p)
    return np.asarray(keys, dtype=object)[idx]


def _fmt_int(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", ".")


def _uniform_days(rng: np.random.Generator, lo_hi: list[float], n: int) -> np.ndarray:
    lo, hi = float(lo_hi[0]), float(lo_hi[1])
    return np.asarray(np.round(rng.uniform(lo, hi, size=n)), dtype="timedelta64[D]")


@dataclass
class FleetSim:
    cfg: dict[str, Any]
    catalogue: pd.DataFrame
    curves: dict[str, dict[str, float]]
    seed: int | None = None
    rng: np.random.Generator = field(init=False)
    as_of: np.datetime64 = field(init=False)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed if self.seed is not None else int(self.cfg.get("seed", 42)))
        self.as_of = np.datetime64(str(self.cfg["as_of"]), "D")

    # ------------------------------------------------------------ small vectorised rules

    def support_end(self, oem: np.ndarray, launch: np.ndarray) -> np.ndarray:
        years = np.full(len(oem), 5.0)
        for name, rule in self.cfg["support_years"].items():
            m = oem == name
            years[m] = float(rule["default"])
            if "from_launch" in rule:
                m2 = m & (launch >= np.datetime64(str(rule["from_launch"]), "D"))
                years[m2] = float(rule["from_launch_years"])
        return launch + _months_to_days(years * 12)

    def residual_share(self, family: np.ndarray, age_months: np.ndarray, grade: np.ndarray) -> np.ndarray:
        """Share of gross launch RRP a device fetches on the marketplace at that age and grade."""
        out = np.empty(len(family))
        for fam in np.unique(family):
            c = self.curves.get(str(fam)) or self.curves.get("all") or {"intercept": -0.36, "slope": -0.0094, "A": 0.09, "C": -0.15, "D": -0.31}
            m = family == fam
            log = c["intercept"] + c["slope"] * np.clip(age_months[m], 6, 84)
            off = np.zeros(m.sum())
            g = grade[m]
            off[g == "A"] = c["A"]
            off[g == "C"] = c["C"]
            off[g == "D"] = c["D"]
            out[m] = np.exp(log + off)
        return np.clip(out, 0.03, 0.98)

    def book_share(self, age_months: np.ndarray) -> np.ndarray:
        life = float(self.cfg["book_life_months"])
        floor = float(self.cfg["book_floor_share"])
        return np.maximum(floor, 1.0 - np.clip(age_months, 0, None) / life)

    def grade_at_return(self, n: int) -> np.ndarray:
        g = _choice_from_mix(self.rng, self.cfg["grade_mix_at_return"], n).astype(str)
        worse = self.rng.random(n) < float(self.cfg["grade_drift_worse"])
        order = np.asarray(GRADES)
        idx = np.searchsorted(order, g)
        idx = np.where(worse, np.minimum(idx + 1, 3), idx)
        return order[idx]

    def disposition(self, cycle_no: np.ndarray, grade: np.ndarray, support_left_m: np.ndarray, age_m: np.ndarray) -> np.ndarray:
        """What happens after this return: zweitmiete | reparatur | restverkauf | recycling."""
        n = len(cycle_no)
        out = np.full(n, "restverkauf", dtype=object)
        rec = self.rng.random(n) < float(self.cfg["recycling_share"])
        ok2 = (cycle_no == 1) & (support_left_m >= float(self.cfg["min_support_left_for_cycle2_months"])) & (age_m <= float(self.cfg["cycle2_max_age_at_return_months"]))
        out[ok2 & np.isin(grade, ["A", "B"])] = "zweitmiete"
        out[ok2 & (grade == "C")] = "reparatur"
        out[rec | (grade == "D")] = np.where(rec[rec | (grade == "D")], "recycling", "restverkauf")
        return out

    # ------------------------------------------------------------ model choice

    def choose_models(self, family: np.ndarray, oem: np.ndarray, purchase: np.ndarray) -> np.ndarray:
        """variant_id per serial: launched at least 14 days before purchase, recent launches preferred."""
        cat = self.catalogue
        out = np.full(len(family), -1)
        pm = purchase.astype("datetime64[M]")
        keys = pd.DataFrame({"family": family, "oem": oem, "pm": pm})
        for (fam, o, month), idx in keys.groupby(["family", "oem", "pm"]).indices.items():
            cutoff = np.datetime64(month, "D") - np.timedelta64(14, "D")
            pool = cat[(cat["family"] == fam) & (cat["oem"] == o) & (cat["launch_date"].values.astype("datetime64[D]") <= cutoff)]
            if pool.empty:
                pool = cat[(cat["family"] == fam) & (cat["launch_date"].values.astype("datetime64[D]") <= cutoff)]
            if pool.empty:
                pool = cat[cat["family"] == fam].nsmallest(5, "launch_date")
            age_days = (cutoff - pool["launch_date"].values.astype("datetime64[D]")).astype(float)
            w = np.exp(-np.clip(age_days, 0, None) / 365.0 * 1.2)
            w = w / w.sum()
            out[idx] = self.rng.choice(pool["variant_id"].values, size=len(idx), p=w)
        return out

    def oem_for(self, family: np.ndarray) -> np.ndarray:
        cat = self.catalogue
        out = np.empty(len(family), dtype=object)
        for fam in FAMILIES:
            m = family == fam
            if not m.any():
                continue
            pool = cat[cat["family"] == fam]
            counts = pool.groupby("oem").size()
            if fam == "Smartphone":
                apple = self.rng.random(m.sum()) < float(self.cfg["apple_share_of_smartphones"])
                others = counts.drop(index="Apple", errors="ignore")
                pick = self.rng.choice(others.index.values, size=m.sum(), p=(others / others.sum()).values)
                out[m] = np.where(apple, "Apple", pick)
            else:
                out[m] = self.rng.choice(counts.index.values, size=m.sum(), p=(counts / counts.sum()).values)
        return out

    # ------------------------------------------------------------ the build

    def build(self) -> dict[str, pd.DataFrame]:
        cfg = self.cfg
        rng = self.rng
        n_c = int(cfg["n_in_circulation"])
        n_w = int(cfg["n_in_warehouse"])

        # ---- circulation: family, cycle, terms, dates
        fam_c = _choice_from_mix(rng, cfg["family_mix"], n_c).astype(str)
        cycle = np.where(rng.random(n_c) < float(cfg["share_cycle2_in_circulation"]), 2, 1)
        t1 = np.empty(n_c, dtype=int)
        for fam in FAMILIES:
            m = fam_c == fam
            t1[m] = _choice_from_mix(rng, cfg["term_mix_cycle1"][fam], m.sum()).astype(int)
        t2 = _choice_from_mix(rng, cfg["term_mix_cycle2"], n_c).astype(int)
        term_now = np.where(cycle == 2, t2, t1)
        elapsed_days = np.round(rng.uniform(0, 1, n_c) * term_now * DAYS_PER_MONTH).astype("timedelta64[D]")
        start_now = self.as_of - elapsed_days
        planned_end = start_now + _months_to_days(term_now)
        # history of second-cycle devices: first contract ran its full term, then the warehouse
        refurb_gap = _uniform_days(rng, [20, 60], n_c)
        end1 = np.where(cycle == 2, start_now - refurb_gap, np.datetime64("NaT", "D"))
        start1 = np.where(cycle == 2, end1 - _months_to_days(t1), start_now)
        purchase = start1 - _uniform_days(rng, [7, 30], n_c)
        oem_c = self.oem_for(fam_c)
        variant = self.choose_models(fam_c, oem_c, purchase)

        # ---- money for circulation
        cat_idx = self.catalogue.set_index("variant_id")
        rrp = cat_idx.loc[variant, "rrp_eur_launch_de"].values.astype(float)
        launch = cat_idx.loc[variant, "launch_date"].values.astype("datetime64[D]")
        # the catalogue starts in 2020: a serial cannot be bought before its model launched
        purchase = np.maximum(purchase, launch + np.timedelta64(14, "D"))
        start1 = np.maximum(start1, purchase + np.timedelta64(7, "D"))
        disc = np.empty(n_c)
        for o, (lo, hi) in cfg["discount_by_oem"].items():
            m = oem_c == o
            disc[m] = rng.uniform(lo, hi, m.sum())
        price_net = rrp / (1 + float(cfg["vat"])) * (1 - disc)
        rent_share = np.asarray([cfg["rent_share_of_landed_per_month"][f] for f in fam_c])
        trf = np.asarray([cfg["term_rate_factor"][int(t)] for t in t1])
        rent1 = price_net * rent_share * trf
        rent_now = np.where(cycle == 2, rent1 * float(cfg["rent2_share_of_rent1"]), rent1)

        # ---- expected return of the running contract
        early = rng.random(n_c) < float(cfg["early_termination_rate"])
        days_left = (planned_end - self.as_of).astype(float)
        early_days = np.round(rng.uniform(1, np.maximum(days_left, 2))).astype("timedelta64[D]")
        exp_return = np.where(early, self.as_of + early_days, planned_end)
        end_reason = np.where(early, "vorzeitig", "planmaessig")
        age_at_return_m = (exp_return - purchase).astype(float) / DAYS_PER_MONTH
        supp_end = self.support_end(oem_c, launch)
        supp_left_m = (supp_end - exp_return).astype(float) / DAYS_PER_MONTH
        grade_exp = self.grade_at_return(n_c)
        dispo = self.disposition(cycle, grade_exp, supp_left_m, age_at_return_m)

        age_now_m = (self.as_of - purchase).astype(float) / DAYS_PER_MONTH
        book_now = price_net * self.book_share(age_now_m)
        resid_now = rrp / (1 + float(cfg["vat"])) * self.residual_share(fam_c, age_now_m, np.full(n_c, "B", dtype=object))
        resid_at_return = rrp / (1 + float(cfg["vat"])) * self.residual_share(fam_c, age_at_return_m, grade_exp)

        devices_c = pd.DataFrame({
            "serial": np.array([f"S{i:07d}" for i in range(n_c)]),
            "variant_id": variant, "family": fam_c, "oem": oem_c,
            "purchase_date": purchase, "purchase_price_net_eur": np.round(price_net, 2), "rrp_gross_eur": rrp,
            "launch_date": launch, "support_end": supp_end,
            "location": "kunde", "state": np.where(cycle == 2, "zweitmiete", "erstmiete"), "cycle_no": cycle,
            "state_since": start_now, "days_in_state": (self.as_of - start_now).astype(int),
            "grade": None, "battery_health": np.round(np.clip(1.0 - float(cfg["battery_health_loss_per_month"]) * age_now_m + rng.normal(0, 0.03, n_c), 0.5, 1.0), 3),
            "age_months": np.round(age_now_m, 1),
            "book_value_eur": np.round(book_now, 2), "residual_forecast_eur": np.round(resid_now, 2),
            "expected_return_date": exp_return, "expected_end_reason": end_reason,
            "expected_grade_at_return": grade_exp, "support_left_at_return_months": np.round(supp_left_m, 1),
            "disposition_at_return": dispo, "residual_at_return_eur": np.round(resid_at_return, 2),
        })

        contracts_c = pd.DataFrame({
            "serial": devices_c["serial"], "cycle_no": cycle, "start_date": start_now, "term_months": term_now,
            "planned_end": planned_end, "actual_end": np.datetime64("NaT", "D"), "end_reason": None,
            "rent_eur_month": np.round(rent_now, 2), "status": "running",
        })
        m2 = cycle == 2
        t1_eff = np.where(m2, np.round((end1.astype("datetime64[D]") - start1).astype(float) / DAYS_PER_MONTH), t1).astype(int)
        contracts_prev = pd.DataFrame({
            "serial": devices_c["serial"][m2].values, "cycle_no": 1, "start_date": start1[m2], "term_months": t1_eff[m2],
            "planned_end": end1[m2], "actual_end": end1[m2], "end_reason": "planmaessig",
            "rent_eur_month": np.round(rent1[m2], 2), "status": "ended",
        })

        # ---- the flow that fills the warehouse (per month, as of today)
        cal = self.return_calendar(devices_c, months=12)
        ret_pm = float(cal["summe_rueckgaben"].mean())
        dmg_pa = np.asarray([cfg["damage_rate_pa"][f] for f in fam_c]).mean()
        dmg_pm = n_c * dmg_pa / 12.0
        repair_share = float(cfg["repair_share_of_damage"])
        to_cycle2_share = float(np.isin(dispo, ["zweitmiete"]).mean())
        to_repair_share = float((dispo == "reparatur").mean())
        to_sale_share = float((dispo == "restverkauf").mean())
        to_rec_share = float((dispo == "recycling").mean())
        d = cfg["dwell_days"]
        mean = lambda lh: (float(lh[0]) + float(lh[1])) / 2.0  # noqa: E731
        refurb_mean = np.mean([mean(v) for v in d["aufbereitung"].values()])
        flow_stock = {
            "eingang": (ret_pm + dmg_pm) * mean(d["eingang"]) / DAYS_PER_MONTH,
            "mdm_freigabe": ret_pm * mean(d["mdm_freigabe"]) / DAYS_PER_MONTH,
            "loeschung_grading": ret_pm * mean(d["loeschung_grading"]) / DAYS_PER_MONTH,
            "reparatur": (ret_pm * to_repair_share + dmg_pm * repair_share) * mean(d["reparatur"]) / DAYS_PER_MONTH,
            "aufbereitung": ret_pm * (to_cycle2_share + to_repair_share * float(cfg["cycle2_share_after_repair_grade_c"])) * refurb_mean / DAYS_PER_MONTH,
            "verkaufsfaehig": ret_pm * (to_sale_share + to_repair_share * (1 - float(cfg["cycle2_share_after_repair_grade_c"]))) * (mean(d["verkaufsfaehig"]) * (1 - float(cfg["slow_mover_share"])) + mean(cfg["slow_mover_days"]) * float(cfg["slow_mover_share"])) / DAYS_PER_MONTH,
            "swap_puffer": n_c * float(cfg["swap_buffer_share_of_circulation"]),
            "neugeraet": (ret_pm * (to_sale_share + to_rec_share) + dmg_pm * (1 - repair_share)) * mean(d["neugeraet"]) / DAYS_PER_MONTH,
        }
        natural = float(sum(flow_stock.values()))
        counts = {k: int(round(v)) for k, v in flow_stock.items()}
        topup = n_w - sum(counts.values())
        if topup > 0:
            split = cfg["warehouse_topup_split"]
            add_n = int(round(topup * float(split["neugeraet"])))
            counts["neugeraet"] += add_n
            counts["verkaufsfaehig"] += topup - add_n
            self.notes.append(
                f"Der Fluss erklärt {_fmt_int(natural)} Geräte im Lager (Rückläufer {_fmt_int(ret_pm)} je Monat plus Defekte {_fmt_int(dmg_pm)} je Monat, "
                f"mal Liegezeit je Station, plus Swap-Puffer). Die Anweisung sagt {_fmt_int(n_w)}. Die Differenz von {_fmt_int(topup)} Geräten steht als "
                f"Vorratskauf (Neugerät, {_fmt_int(add_n)}) und Abverkaufsstau (verkaufsfähig, {_fmt_int(topup - add_n)}) im Lager, Aufteilung Platzhalter. "
                f"{_fmt_int(n_w)} im Lager bei {_fmt_int(ret_pm + dmg_pm)} Zugängen je Monat hieße {str(round(n_w / (ret_pm + dmg_pm), 1)).replace('.', ',')} Monate mittlere Liegezeit. "
                "Das ist die erste Frage an das Haus, kein Befund über das Haus."
            )
        elif topup < 0:
            scale = n_w / sum(counts.values())
            counts = {k: int(round(v * scale)) for k, v in counts.items()}
            self.notes.append(f"Der Fluss erklärt {_fmt_int(natural)} Geräte, die Anweisung sagt {_fmt_int(n_w)}; alle Stationen proportional auf {_fmt_int(n_w)} gekürzt.")
        diff = n_w - sum(counts.values())
        counts["verkaufsfaehig"] += diff
        self.topup = max(topup, 0)
        self.flow_stock = flow_stock
        self.flow_rates = {"rueckgaben_je_monat": ret_pm, "defekte_je_monat": dmg_pm, "anteil_zweitmiete": to_cycle2_share,
                           "anteil_reparatur_c": to_repair_share, "anteil_restverkauf": to_sale_share, "anteil_recycling": to_rec_share}

        # ---- warehouse devices
        station = np.concatenate([np.full(v, k, dtype=object) for k, v in counts.items()])
        n_wh = len(station)
        rng.shuffle(station)
        fam_w = _choice_from_mix(rng, cfg["family_mix"], n_wh).astype(str)
        # where it came from: a return after cycle 1 or 2, a defect, or a fresh purchase
        origin = np.full(n_wh, "rueckgabe_1", dtype=object)
        cyc_w = np.ones(n_wh, dtype=int)
        is_new = station == "neugeraet"
        origin[is_new] = "neukauf"
        cyc_w[is_new] = 0
        from2 = (~is_new) & (rng.random(n_wh) < (to_sale_share + to_rec_share) / max(to_sale_share + to_rec_share + to_cycle2_share + to_repair_share, 1e-9) * 0.6)
        origin[from2] = "rueckgabe_2"
        cyc_w[from2] = 2
        is_rep = station == "reparatur"
        defect = is_rep & (rng.random(n_wh) < (dmg_pm * repair_share) / max(dmg_pm * repair_share + ret_pm * to_repair_share, 1e-9))
        origin[defect] = "defekt"
        swap = station == "swap_puffer"
        origin[swap & (rng.random(n_wh) < 0.5)] = "neukauf"
        cyc_w[origin == "neukauf"] = 0

        # dwell in the current station
        since_days = np.zeros(n_wh)
        for k in STATIONS:
            m = station == k
            if not m.any():
                continue
            if k == "aufbereitung":
                since_days[m] = rng.uniform(0, max(v[1] for v in d["aufbereitung"].values()), m.sum())
            elif k == "swap_puffer":
                since_days[m] = rng.uniform(0, 120, m.sum())
            else:
                since_days[m] = rng.uniform(0, float(d[k][1]), m.sum())
        # the top-up: bought ahead or stuck in sale, older than the flow would make them
        if self.topup > 0:
            idx_new = np.flatnonzero(is_new)
            idx_sell = np.flatnonzero(station == "verkaufsfaehig")
            k_new = int(round(self.topup * float(cfg["warehouse_topup_split"]["neugeraet"])))
            k_sell = self.topup - k_new
            pick_new = rng.choice(idx_new, size=min(k_new, len(idx_new)), replace=False)
            pick_sell = rng.choice(idx_sell, size=min(k_sell, len(idx_sell)), replace=False)
            since_days[pick_new] = rng.uniform(0, 120, len(pick_new))
            since_days[pick_sell] = rng.uniform(30, 400, len(pick_sell))
            origin[pick_new] = "vorratskauf"
            origin[pick_sell] = np.where(origin[pick_sell] == "rueckgabe_2", "rueckgabe_2", "rueckgabe_1")
        slow = (station == "verkaufsfaehig") & (rng.random(n_wh) < float(cfg["slow_mover_share"]))
        since_days[slow] = rng.uniform(*cfg["slow_mover_days"], slow.sum())
        state_since = self.as_of - np.round(since_days).astype("timedelta64[D]")

        # age and history of warehouse devices
        term_w1 = np.empty(n_wh, dtype=int)
        for fam in FAMILIES:
            m = fam_w == fam
            term_w1[m] = _choice_from_mix(rng, cfg["term_mix_cycle1"][fam], m.sum()).astype(int)
        term_w2 = _choice_from_mix(rng, cfg["term_mix_cycle2"], n_wh).astype(int)
        # the return that brought it here happened `since_days` ago plus the stations before this one
        stations_before = {"eingang": 0, "mdm_freigabe": 10, "loeschung_grading": 22, "reparatur": 24, "aufbereitung": 30, "verkaufsfaehig": 30, "swap_puffer": 40, "neugeraet": 0}
        back_days = np.asarray([stations_before[s] for s in station], dtype=float)
        return_date = state_since - np.round(back_days).astype("timedelta64[D]")
        early_w = rng.random(n_wh) < float(cfg["early_termination_rate"])
        used1 = np.where(early_w, rng.uniform(float(cfg["early_termination_age_min_months"]), term_w1), term_w1).astype(float)
        used2 = np.where(early_w, rng.uniform(float(cfg["early_termination_age_min_months"]), term_w2), term_w2).astype(float)
        defect_age = rng.uniform(1, term_w1, n_wh)
        months_at_customer = np.where(origin == "defekt", defect_age, np.where(cyc_w == 2, used1 + used2, np.where(cyc_w == 1, used1, 0.0)))
        gap2 = np.where(cyc_w == 2, rng.uniform(20, 60, n_wh), 0.0)
        purchase_w = return_date - np.round(months_at_customer * DAYS_PER_MONTH + gap2).astype("timedelta64[D]") - _uniform_days(rng, [7, 30], n_wh)
        purchase_w = np.where(origin == "neukauf", self.as_of - np.round(since_days).astype("timedelta64[D]") - _uniform_days(rng, [0, 10], n_wh), purchase_w)
        purchase_w = np.where(origin == "vorratskauf", state_since - _uniform_days(rng, [0, 10], n_wh), purchase_w)
        oem_w = self.oem_for(fam_w)
        variant_w = self.choose_models(fam_w, oem_w, purchase_w)
        rrp_w = cat_idx.loc[variant_w, "rrp_eur_launch_de"].values.astype(float)
        launch_w = cat_idx.loc[variant_w, "launch_date"].values.astype("datetime64[D]")
        purchase_w = np.maximum(purchase_w, launch_w + np.timedelta64(14, "D"))
        disc_w = np.empty(n_wh)
        for o, (lo, hi) in cfg["discount_by_oem"].items():
            m = oem_w == o
            disc_w[m] = rng.uniform(lo, hi, m.sum())
        price_w = rrp_w / (1 + float(cfg["vat"])) * (1 - disc_w)
        grade_w = self.grade_at_return(n_wh)
        grade_w[origin == "neukauf"] = "A"
        grade_w[origin == "vorratskauf"] = "A"
        grade_w[(station == "swap_puffer") & (origin != "neukauf")] = np.where(rng.random(((station == "swap_puffer") & (origin != "neukauf")).sum()) < 0.6, "A", "B")
        grade_w[station == "reparatur"] = "C"
        age_w_m = (self.as_of - purchase_w).astype(float) / DAYS_PER_MONTH
        supp_end_w = self.support_end(oem_w, launch_w)
        supp_left_w = (supp_end_w - self.as_of).astype(float) / DAYS_PER_MONTH
        book_w = price_w * self.book_share(age_w_m)
        resid_w = rrp_w / (1 + float(cfg["vat"])) * self.residual_share(fam_w, age_w_m, grade_w)
        fee = float(cfg["channel_fee_share"]["marketplace"])
        # what happens next for a warehouse device
        next_w = np.full(n_wh, "", dtype=object)
        next_w[station == "neugeraet"] = "erstmiete"
        next_w[station == "swap_puffer"] = "swap"
        next_w[station == "aufbereitung"] = "zweitmiete"
        next_w[station == "verkaufsfaehig"] = "restverkauf"
        undecided = np.isin(station, ["eingang", "mdm_freigabe", "loeschung_grading", "reparatur"])
        next_w[undecided] = self.disposition(cyc_w[undecided], grade_w[undecided], supp_left_w[undecided], age_w_m[undecided])
        next_w[(station == "reparatur")] = np.where(rng.random((station == "reparatur").sum()) < float(cfg["cycle2_share_after_repair_grade_c"]), "zweitmiete", "restverkauf")
        next_w[(cyc_w == 2) & undecided] = "restverkauf"
        repair_cost = np.zeros(n_wh)
        m = station == "reparatur"
        for fam, (lo, hi) in cfg["repair_cost_eur"].items():
            mm = m & (fam_w == fam)
            repair_cost[mm] = rng.uniform(lo, hi, mm.sum())

        devices_w = pd.DataFrame({
            "serial": np.array([f"S{i + n_c:07d}" for i in range(n_wh)]),
            "variant_id": variant_w, "family": fam_w, "oem": oem_w,
            "purchase_date": purchase_w, "purchase_price_net_eur": np.round(price_w, 2), "rrp_gross_eur": rrp_w,
            "launch_date": launch_w, "support_end": supp_end_w,
            "location": "lager", "state": station.astype(str), "cycle_no": cyc_w,
            "state_since": state_since, "days_in_state": (self.as_of - state_since).astype(int),
            "grade": grade_w.astype(str), "battery_health": np.round(np.clip(1.0 - float(cfg["battery_health_loss_per_month"]) * age_w_m + rng.normal(0, 0.03, n_wh), 0.5, 1.0), 3),
            "age_months": np.round(age_w_m, 1),
            "book_value_eur": np.round(book_w, 2), "residual_forecast_eur": np.round(resid_w, 2),
            "expected_return_date": np.datetime64("NaT", "D"), "expected_end_reason": None,
            "expected_grade_at_return": None, "support_left_at_return_months": np.round(supp_left_w, 1),
            "disposition_at_return": next_w.astype(str), "residual_at_return_eur": np.round(resid_w * (1 - fee), 2),
        })
        devices_w["origin"] = origin.astype(str)
        devices_w["repair_cost_estimate_eur"] = np.round(repair_cost, 2)
        devices_c["origin"] = np.where(cycle == 2, "zweitmiete_laufend", "erstmiete_laufend")
        devices_c["repair_cost_estimate_eur"] = 0.0

        # past contracts of warehouse devices
        rows = []
        for cyc in (1, 2):
            m = cyc_w >= cyc
            if not m.any():
                continue
            term = np.where(cyc == 1, term_w1, term_w2)[m]
            used = np.where(cyc == 1, used1, used2)[m]
            if cyc == 1:
                end = np.where(cyc_w[m] == 2, return_date[m] - np.round(gap2[m] + used2[m] * DAYS_PER_MONTH).astype("timedelta64[D]"), return_date[m])
                end = np.where(origin[m] == "defekt", return_date[m], end)
            else:
                end = return_date[m]
            start = end - np.round(np.where(origin[m] == "defekt", defect_age[m], used) * DAYS_PER_MONTH).astype("timedelta64[D]")
            # a model launched after the nominal start shortens the first contract; the serial keeps its chronology
            start = np.maximum(start, purchase_w[m] + np.timedelta64(7, "D"))
            end = np.maximum(end, start + np.timedelta64(30, "D"))
            reason = np.where(origin[m] == "defekt", "defekt", np.where(early_w[m] & (cyc == cyc_w[m]), "vorzeitig", "planmaessig"))
            rent = price_w[m] * np.asarray([cfg["rent_share_of_landed_per_month"][f] for f in fam_w[m]]) * np.asarray([cfg["term_rate_factor"][int(t)] for t in term])
            if cyc == 2:
                rent = rent * float(cfg["rent2_share_of_rent1"])
            rows.append(pd.DataFrame({"serial": devices_w["serial"].values[m], "cycle_no": cyc, "start_date": start, "term_months": term,
                                      "planned_end": start + _months_to_days(term), "actual_end": end, "end_reason": reason,
                                      "rent_eur_month": np.round(rent, 2), "status": "ended"}))
        contracts_w = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=contracts_c.columns)

        devices = pd.concat([devices_c, devices_w], ignore_index=True)
        contracts = pd.concat([contracts_prev, contracts_c, contracts_w], ignore_index=True)
        contracts = contracts.sort_values(["serial", "cycle_no"]).reset_index(drop=True)
        events = self.events(devices, contracts)
        cal48 = self.return_calendar(devices_c, months=48)
        return {"devices": devices, "contracts": contracts, "events": events, "return_calendar": cal48,
                "warehouse_by_station": self.warehouse_by_station(devices_w),
                "warehouse_aging": self.warehouse_aging(devices_w),
                "writedown_risk": self.writedown_risk(devices),
                "repairs": self.repairs(devices_w),
                "kpis": self.kpis(devices, devices_w, cal48)}

    # ------------------------------------------------------------ derived tables

    def events(self, devices: pd.DataFrame, contracts: pd.DataFrame) -> pd.DataFrame:
        ev = []
        ev.append(pd.DataFrame({"serial": devices["serial"], "event_date": devices["purchase_date"], "event": "einkauf", "cycle_no": 0,
                                "detail": devices["oem"] + " " + devices["family"], "amount_eur": -devices["purchase_price_net_eur"]}))
        ev.append(pd.DataFrame({"serial": contracts["serial"], "event_date": contracts["start_date"], "event": np.where(contracts["cycle_no"] == 2, "zweitmiete_start", "erstmiete_start"),
                                "cycle_no": contracts["cycle_no"], "detail": contracts["term_months"].astype(str) + " Monate", "amount_eur": 0.0}))
        ended = contracts[contracts["status"] == "ended"]
        ev.append(pd.DataFrame({"serial": ended["serial"], "event_date": ended["actual_end"], "event": "rueckgabe", "cycle_no": ended["cycle_no"],
                                "detail": ended["end_reason"], "amount_eur": 0.0}))
        wh = devices[devices["location"] == "lager"]
        ev.append(pd.DataFrame({"serial": wh["serial"], "event_date": wh["state_since"], "event": "station_" + wh["state"], "cycle_no": wh["cycle_no"],
                                "detail": "Grade " + wh["grade"].astype(str), "amount_eur": -wh["repair_cost_estimate_eur"]}))
        out = pd.concat(ev, ignore_index=True)
        out["event_date"] = pd.to_datetime(out["event_date"])
        out = out.dropna(subset=["event_date"]).sort_values(["serial", "event_date"]).reset_index(drop=True)
        return out

    def return_calendar(self, devices_c: pd.DataFrame, months: int = 48) -> pd.DataFrame:
        """Per month: what comes back, from which cycle, and what the rule does with it. Plus defect returns (analytic)."""
        d = devices_c
        ret = pd.to_datetime(d["expected_return_date"]).dt.to_period("M")
        # the current month counts (its remaining days), so that no running contract is dropped
        start = pd.Period(pd.Timestamp(self.as_of), "M")
        idx = pd.period_range(start, periods=months, freq="M")
        g = pd.DataFrame({"m": ret, "cycle": d["cycle_no"].values, "dispo": d["disposition_at_return"].values, "reason": d["expected_end_reason"].values})
        g = g[g["m"].isin(idx)]
        out = pd.DataFrame(index=idx)
        out["aus_erstmiete"] = g[g["cycle"] == 1].groupby("m").size().reindex(idx, fill_value=0)
        out["aus_zweitmiete"] = g[g["cycle"] == 2].groupby("m").size().reindex(idx, fill_value=0)
        out["davon_vorzeitig"] = g[g["reason"] == "vorzeitig"].groupby("m").size().reindex(idx, fill_value=0)
        for k, col in (("zweitmiete", "naechster_schritt_zweitmiete"), ("reparatur", "naechster_schritt_reparatur_c"), ("restverkauf", "naechster_schritt_restverkauf"), ("recycling", "naechster_schritt_recycling")):
            out[col] = g[g["dispo"] == k].groupby("m").size().reindex(idx, fill_value=0)
        out["summe_rueckgaben"] = out["aus_erstmiete"] + out["aus_zweitmiete"]
        n_c = len(d)
        dmg_pa = np.asarray([self.cfg["damage_rate_pa"][f] for f in d["family"].values]).mean()
        out["defekte_zur_reparatur"] = int(round(n_c * dmg_pa / 12.0 * float(self.cfg["repair_share_of_damage"])))
        out["defekte_ersetzt"] = int(round(n_c * dmg_pa / 12.0 * (1 - float(self.cfg["repair_share_of_damage"]))))
        out.index.name = "monat"
        out = out.reset_index()
        out["monat"] = out["monat"].astype(str)
        return out

    def warehouse_by_station(self, w: pd.DataFrame) -> pd.DataFrame:
        rate = float(self.cfg["capital_cost_rate_pa"])
        g = w.groupby("state").agg(geraete=("serial", "size"), buchwert_eur=("book_value_eur", "sum"), restwert_eur=("residual_forecast_eur", "sum"),
                                   liegetage_mittel=("days_in_state", "mean"), liegetage_p90=("days_in_state", lambda s: float(np.percentile(s, 90))))
        g = g.reindex([s for s in STATIONS if s in g.index])
        g["kapitalkosten_eur_je_tag"] = g["buchwert_eur"] * rate / 365.0
        g["abschreibungsrisiko_eur"] = w.assign(r=np.maximum(0.0, w["book_value_eur"] - w["residual_at_return_eur"])).groupby("state")["r"].sum().reindex(g.index)
        g.insert(0, "station", [STATION_LABEL[s] for s in g.index])
        g["anteil"] = g["geraete"] / g["geraete"].sum()
        return g.reset_index().rename(columns={"state": "state_key"}).round(2)

    def warehouse_aging(self, w: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for lo, hi, label in AGING_BUCKETS:
            m = (w["days_in_state"] >= lo) & (w["days_in_state"] <= hi)
            for st in STATIONS:
                mm = m & (w["state"] == st)
                if mm.any():
                    rows.append({"altersstufe": label, "state_key": st, "station": STATION_LABEL[st], "geraete": int(mm.sum()),
                                 "buchwert_eur": round(float(w.loc[mm, "book_value_eur"].sum()), 2)})
        return pd.DataFrame(rows)

    def writedown_risk(self, devices: pd.DataFrame) -> pd.DataFrame:
        d = devices.copy()
        d["risiko_eur"] = np.maximum(0.0, d["book_value_eur"] - d["residual_at_return_eur"])
        cat = self.catalogue.set_index("variant_id")
        d["modell"] = cat.loc[d["variant_id"], "model_name"].values
        g = d.groupby(["location", "family", "modell"]).agg(geraete=("serial", "size"), buchwert_eur=("book_value_eur", "sum"),
                                                              restwert_eur=("residual_at_return_eur", "sum"), risiko_eur=("risiko_eur", "sum")).reset_index()
        g["risiko_je_geraet_eur"] = g["risiko_eur"] / g["geraete"]
        return g.sort_values("risiko_eur", ascending=False).round(2)

    def repairs(self, w: pd.DataFrame) -> pd.DataFrame:
        r = w[w["state"] == "reparatur"]
        g = r.groupby(["family", "origin"]).agg(geraete=("serial", "size"), kosten_schaetzung_eur=("repair_cost_estimate_eur", "sum"),
                                                tage_mittel=("days_in_state", "mean")).reset_index()
        return g.round(2)

    def kpis(self, devices: pd.DataFrame, w: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
        rate = float(self.cfg["capital_cost_rate_pa"])
        sale_pm = float(cal["naechster_schritt_restverkauf"].head(12).mean()) + float(cal["naechster_schritt_reparatur_c"].head(12).mean()) * (1 - float(self.cfg["cycle2_share_after_repair_grade_c"]))
        dep_pm = sale_pm + float(cal["naechster_schritt_recycling"].head(12).mean())
        swap_pm = float(cal["defekte_zur_reparatur"].iloc[0]) + float(cal["defekte_ersetzt"].iloc[0])
        sell = w[w["state"] == "verkaufsfaehig"]
        new = w[w["state"] == "neugeraet"]
        swap = w[w["state"] == "swap_puffer"]
        rows = [
            ("Bestand im Lager", int(len(w)), "Geräte", "Anweisung des Owners; der Fluss erklärt " + _fmt_int(sum(self.flow_stock.values()))),
            ("Bestand im Umlauf", int((devices["location"] == "kunde").sum()), "Geräte", "davon Zweitmiete " + _fmt_int(int((devices['state'] == 'zweitmiete').sum()))),
            ("Verfügbarkeit: Swap-Puffer in Monaten", round(len(swap) / max(swap_pm, 1e-9), 1), "Monate", f"Puffer {_fmt_int(len(swap))} gegen {_fmt_int(swap_pm)} Defekte je Monat"),
            ("Verfügbarkeit: Neugeräte in Monaten", round(len(new) / max(dep_pm, 1e-9), 1), "Monate", f"{_fmt_int(len(new))} Neugeräte gegen {_fmt_int(dep_pm)} Abgänge je Monat, die ersetzt werden"),
            ("Kapitalbindung Lager, Buchwert", round(float(w["book_value_eur"].sum()), 0), "EUR", "Summe Buchwert aller Geräte im Lager"),
            ("Kapitalkosten Lager je Tag", round(float(w["book_value_eur"].sum()) * rate / 365.0, 0), "EUR/Tag", f"Buchwert mal {rate:.0%} je Jahr, Platzhalter"),
            ("Reichweite verkaufsfähiger Bestand", round(len(sell) / max(sale_pm, 1e-9), 1), "Monate", f"{_fmt_int(len(sell))} verkaufsfähig gegen {_fmt_int(sale_pm)} Verkäufe je Monat aus dem Rücklauf"),
            ("Aging: Anteil über 90 Tage im Lager", round(float((w["days_in_state"] > 90).mean()), 3), "Anteil", f"{_fmt_int(int((w['days_in_state'] > 90).sum()))} Geräte"),
            ("Aging: Anteil über 180 Tage im Lager", round(float((w["days_in_state"] > 180).mean()), 3), "Anteil", f"{_fmt_int(int((w['days_in_state'] > 180).sum()))} Geräte"),
            ("Abschreibungsrisiko Lager, Verkaufsware", round(float(np.maximum(0.0, w.loc[w["disposition_at_return"] == "restverkauf", "book_value_eur"] - w.loc[w["disposition_at_return"] == "restverkauf", "residual_at_return_eur"]).sum()), 0), "EUR", f"Buchwert über Netto-Restwert bei {_fmt_int(int((w['disposition_at_return'] == 'restverkauf').sum()))} Geräten, deren nächster Schritt der Verkauf ist; Neugeräte und Swap-Puffer werden vermietet, nicht verkauft, und stehen nicht drin"),
            ("Neugeräte im Lager, Buchwert", round(float(w.loc[w["state"] == "neugeraet", "book_value_eur"].sum()), 0), "EUR", "Vorratskauf; ihr Risiko ist Aging, nicht der Restwert von heute"),
            ("Abschreibungsrisiko Umlauf bei Rückgabe", round(float(np.maximum(0.0, devices.loc[devices["location"] == "kunde", "book_value_eur"] - devices.loc[devices["location"] == "kunde", "residual_at_return_eur"] * (1 - float(self.cfg["channel_fee_share"]["marketplace"]))).sum()), 0), "EUR", "Buchwert heute über Netto-Restwert am erwarteten Rückgabetag"),
            ("Rückgaben je Monat, nächste 12 Monate", round(float(cal["summe_rueckgaben"].head(12).mean()), 0), "Geräte", "planmäßig plus vorzeitig, ohne Defekte"),
            ("Defekte je Monat", round(swap_pm, 0), "Geräte", "davon zur Reparatur " + _fmt_int(float(cal['defekte_zur_reparatur'].iloc[0]))),
            ("Reparaturbestand", int((w["state"] == "reparatur").sum()), "Geräte", f"Kostenschätzung {_fmt_int(float(w['repair_cost_estimate_eur'].sum()))} EUR"),
        ]
        return pd.DataFrame(rows, columns=["kennzahl", "wert", "einheit", "herleitung"])


# ---------------------------------------------------------------- run and write

def run(cfg: dict[str, Any], out_dir: Path | None = None, db_path: Path | None = None, seed: int | None = None) -> dict[str, Any]:
    catalogue = load_catalogue()
    curves = load_curves(ROOT / cfg.get("residual_curve_file", "outputs/market_curves.csv"))
    sim = FleetSim(cfg, catalogue, curves, seed=seed)
    tables = sim.build()
    out_dir = out_dir or (OUTPUTS_DIR / "fleetsim")
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_path or (DATA_DIR / "fleet_mock.duckdb")
    _write_duckdb(db_path, tables, catalogue)
    for name in ("return_calendar", "warehouse_by_station", "warehouse_aging", "writedown_risk", "repairs", "kpis"):
        tables[name].to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8")
    report = _report(sim, tables, db_path)
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")
    return {"tables": tables, "db": db_path, "out_dir": out_dir, "report": report, "notes": sim.notes}


def _write_duckdb(db_path: Path, tables: dict[str, pd.DataFrame], catalogue: pd.DataFrame) -> None:
    import duckdb

    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        con.register("catalogue_df", catalogue)
        con.execute("CREATE TABLE catalogue AS SELECT * FROM catalogue_df")
        for name in ("devices", "contracts", "events", "return_calendar", "warehouse_by_station", "warehouse_aging", "writedown_risk", "repairs", "kpis"):
            con.register(f"{name}_df", tables[name])
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM {name}_df")
        con.execute("CREATE VIEW v_lager AS SELECT d.*, c.model_name, c.spec FROM devices d JOIN catalogue c USING (variant_id) WHERE d.location = 'lager'")
        con.execute("CREATE VIEW v_umlauf AS SELECT d.*, c.model_name, c.spec FROM devices d JOIN catalogue c USING (variant_id) WHERE d.location = 'kunde'")
        con.execute("""CREATE VIEW v_rueckgaben_je_monat AS
            SELECT strftime(expected_return_date, '%Y-%m') AS monat, cycle_no, disposition_at_return AS naechster_schritt, count(*) AS geraete,
                   round(sum(book_value_eur), 0) AS buchwert_eur, round(sum(residual_at_return_eur), 0) AS restwert_bei_rueckgabe_eur
            FROM devices WHERE location = 'kunde' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""")
    finally:
        con.close()


def _report(sim: FleetSim, t: dict[str, pd.DataFrame], db_path: Path) -> str:
    cfg = sim.cfg
    cal = t["return_calendar"]
    st = t["warehouse_by_station"]
    k = t["kpis"]
    lines = [
        f"# Flottensimulation, Stand {cfg['as_of']}",
        "",
        f"Synthetische Flotte von {_fmt_int(len(t['devices']))} Seriennummern: {_fmt_int(cfg['n_in_circulation'])} im Umlauf, {_fmt_int(cfg['n_in_warehouse'])} im Lager (Anweisung des Owners vom 21.09.2026). "
        f"Katalog: {len(sim.catalogue)} bepreiste Ausstattungen, Restwertkurven aus öffentlichen Ankern. Jede Zahl hier ist abgeleitet, keine ist eine Tatsache über ein Haus. "
        f"Parameter und Owner: `{Path(cfg['_path']).name}`. Datenbank: `{db_path.name}` (Tabellen devices, contracts, events, Sichten v_lager, v_umlauf, v_rueckgaben_je_monat).",
        "",
        "## Der eine Befund zuerst",
        "",
    ]
    lines += [f"- {n}" for n in sim.notes] or ["- Der Fluss erklärt den Lagerbestand ohne Aufschlag."]
    lines += ["", "## Kennzahlen", "", "| Kennzahl | Wert | Einheit | Herleitung |", "|---|---:|---|---|"]
    for _, r in k.iterrows():
        v = r["wert"]
        vs = f"{v:.1%}".replace(".", ",") if r["einheit"] == "Anteil" else f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(v, float) and r["einheit"] == "Monate" else _fmt_int(v) if isinstance(v, (int, float)) else str(v)
        lines.append(f"| {r['kennzahl']} | {vs} | {r['einheit']} | {r['herleitung']} |")
    lines += ["", "## Was wann zurückkommt (nächste 24 Monate, Umlauf)", "",
              "| Monat | aus Erstmiete | aus Zweitmiete | davon vorzeitig | → Zweitmiete | → Reparatur (C) | → Restverkauf | → Recycling | Defekte zur Reparatur |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in cal.head(24).iterrows():
        lines.append(f"| {r['monat']} | {_fmt_int(r['aus_erstmiete'])} | {_fmt_int(r['aus_zweitmiete'])} | {_fmt_int(r['davon_vorzeitig'])} | {_fmt_int(r['naechster_schritt_zweitmiete'])} | {_fmt_int(r['naechster_schritt_reparatur_c'])} | {_fmt_int(r['naechster_schritt_restverkauf'])} | {_fmt_int(r['naechster_schritt_recycling'])} | {_fmt_int(r['defekte_zur_reparatur'])} |")
    tot = cal.head(24)
    lines.append(f"| **Summe 24 Monate** | {_fmt_int(tot['aus_erstmiete'].sum())} | {_fmt_int(tot['aus_zweitmiete'].sum())} | {_fmt_int(tot['davon_vorzeitig'].sum())} | {_fmt_int(tot['naechster_schritt_zweitmiete'].sum())} | {_fmt_int(tot['naechster_schritt_reparatur_c'].sum())} | {_fmt_int(tot['naechster_schritt_restverkauf'].sum())} | {_fmt_int(tot['naechster_schritt_recycling'].sum())} | {_fmt_int(tot['defekte_zur_reparatur'].sum())} |")
    lines += ["", "Regel je Rückgabe (Platzhalter, Owner Recommerce): nach Zyklus 1 mit Grade A oder B, mindestens 24 Monaten Support-Rest und höchstens 40 Monaten Alter in die Zweitmiete; Grade C erst in die Reparatur, danach zur Hälfte Zweitmiete; Grade D und alles nach Zyklus 2 in den Restverkauf; 4 Prozent Recycling.",
              "", "## Lager nach Station", "",
              "| Station | Geräte | Anteil | Buchwert EUR | Restwert EUR | Liegetage Mittel | Liegetage P90 | Kapitalkosten EUR je Tag | Abschreibungsrisiko EUR |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in st.iterrows():
        lines.append(f"| {r['station']} | {_fmt_int(r['geraete'])} | {r['anteil']:.0%} | {_fmt_int(r['buchwert_eur'])} | {_fmt_int(r['restwert_eur'])} | {r['liegetage_mittel']:.0f} | {r['liegetage_p90']:.0f} | {_fmt_int(r['kapitalkosten_eur_je_tag'])} | {_fmt_int(r['abschreibungsrisiko_eur'])} |")
    ag = t["warehouse_aging"].groupby("altersstufe", sort=False).agg(geraete=("geraete", "sum"), buchwert_eur=("buchwert_eur", "sum"))
    order = [b[2] for b in AGING_BUCKETS]
    ag = ag.reindex([o for o in order if o in ag.index])
    lines += ["", "## Aging im Lager", "", "| Altersstufe (Liegetage) | Geräte | Buchwert EUR |", "|---|---:|---:|"]
    for label, r in ag.iterrows():
        lines.append(f"| {label} | {_fmt_int(r['geraete'])} | {_fmt_int(r['buchwert_eur'])} |")
    wr = t["writedown_risk"]
    top = wr[wr["location"] == "lager"].head(10)
    lines += ["", "## Abschreibungsrisiko, zehn größte Modelle im Lager", "", "| Modell | Familie | Geräte | Buchwert EUR | Netto-Restwert EUR | Risiko EUR | je Gerät |", "|---|---|---:|---:|---:|---:|---:|"]
    for _, r in top.iterrows():
        lines.append(f"| {r['modell']} | {r['family']} | {_fmt_int(r['geraete'])} | {_fmt_int(r['buchwert_eur'])} | {_fmt_int(r['restwert_eur'])} | {_fmt_int(r['risiko_eur'])} | {r['risiko_je_geraet_eur']:.0f} |")
    rp = t["repairs"]
    lines += ["", "## Reparatur", "", "| Familie | Herkunft | Geräte | Kostenschätzung EUR | Tage im Status, Mittel |", "|---|---|---:|---:|---:|"]
    for _, r in rp.iterrows():
        lines.append(f"| {r['family']} | {r['origin']} | {_fmt_int(r['geraete'])} | {_fmt_int(r['kosten_schaetzung_eur'])} | {r['tage_mittel']:.0f} |")
    fr = sim.flow_rates
    lines += ["", "## Der Fluss, aus dem das Lager folgt", "",
              f"- Rückgaben aus laufenden Verträgen: {_fmt_int(fr['rueckgaben_je_monat'])} je Monat (Mittel der nächsten zwölf Monate); Defekte: {_fmt_int(fr['defekte_je_monat'])} je Monat.",
              f"- Nächster Schritt der Rückgaben nach Regel: Zweitmiete {fr['anteil_zweitmiete']:.0%}, Reparatur Grade C {fr['anteil_reparatur_c']:.0%}, Restverkauf {fr['anteil_restverkauf']:.0%}, Recycling {fr['anteil_recycling']:.0%}.",
              "- Bestand je Station aus Zugang mal Liegezeit (Little): " + ", ".join(f"{STATION_LABEL[k]} {_fmt_int(v)}" for k, v in sim.flow_stock.items()) + ".",
              "", "## Was hier fehlt und nur das Haus weiß", "",
              "- Der Anteil der Zweitmiete am Umlauf und ihre Miete (Platzhalter 25 Prozent, 70 Prozent der Erstmiete).",
              "- Die tatsächliche Liegezeit je Station, vor allem die MDM-Freigabe durch den Altkunden.",
              "- Ob Neugeräte auf Lager gekauft werden und wie groß der Swap-Puffer je Modell und Region ist.",
              "- Reparaturpreise zu Partnerkonditionen und die Schwelle für den Akkutausch.",
              "- Der Kapitalkostensatz der Fazilität und die Abschreibungsregel je Geräteart."]
    return "\n".join(lines) + "\n"
