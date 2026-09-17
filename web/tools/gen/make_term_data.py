# -*- coding: utf-8 -*-
"""Laufzeit gegen Restwert, je Serie: every number from outputs/market_*.csv (public), studies_de.json (sourced),
the catalogue (public) or the lake (simulated).

Since 17.09.2026 (the owner's question "Hier startet die Seite mit iPhone nach 12 Monaten, warum?"): the tab is no
longer wired to Apple smartphones. One entry per series (device family x manufacturer, the finest level that has a
public age curve with slope and intercept in outputs/market_curves.csv) with its curve, its evidence count and age span,
its models from the catalogue (default: the youngest generation, latest launch without successor, from the data), the
cost share per term of its simulation bucket (silver.device_ledger knows four buckets: iphone_like, android_like,
tablet_like, laptop_like; nothing finer exists in the lake), and the rent factor for every pair of terms.
The default series is the one with the most marketplace evidence; the default term the one with the most closed
cycles in that bucket. The motor (web/engine/term.js) types no number; it picks from this file."""
import json, sys, pathlib, math
from datetime import date
from _roles import de_roles  # noqa: E402
import pandas as pd, duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
TERMS = [12, 24, 36, 48]
as_of = date.fromisoformat(TODAY)
cur = pd.read_csv(REPO / "outputs" / "market_curves.csv")
anc = pd.read_csv(REPO / "outputs" / "market_anchors.csv")
models = pd.read_csv(REPO / "data" / "catalogue" / "models.csv", comment="#")
variants = pd.read_csv(REPO / "data" / "catalogue" / "variants.csv", comment="#")
studies = json.load(open(pathlib.Path(__file__).parent / "studies_de.json", encoding="utf-8"))
lake = yaml.safe_load(open(REPO / "config" / "lake.yaml", encoding="utf-8"))
assumptions = yaml.safe_load(open(REPO / "config" / "assumptions.yaml", encoding="utf-8"))
def _find(d, key):
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _find(v, key)
            if r is not None:
                return r
    return None
_pd = _find(assumptions, "purchase_discount_pct")
disc = float(_pd["value"] if isinstance(_pd, dict) else _pd)   # no silent default: the placeholder must exist in the file
factors = {int(k): float(v) for k, v in lake["term_rate_factor"].items()}
buy_share = 1.0 - disc                                             # purchase price in % of RRP (placeholder discount)
FAM_LABEL = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "tablet_like": "Tablet", "laptop_like": "Laptop"}
# the published year studies are iPhone studies (studies_de.json, iphone_jahre); no other series has a sourced year value
STUDIES_BY_SERIES = {"Smartphone / Apple": studies["iphone_jahre"]}

def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v

def months_between(d0, d1):
    return (d1.year - d0.year) * 12 + (d1.month - d0.month) + (d1.day - d0.day) / 30.4

def launch_date(v):
    if not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v if len(v) == 10 else v + "-15")
    except ValueError:
        return None

def bucket_of(family, oem):
    """the simulation bucket of a series: the same mapping make_device_data.py uses"""
    if family == "Smartphone":
        return "iphone_like" if oem == "Apple" else "android_like"
    return {"Tablet": "tablet_like", "Laptop": "laptop_like"}.get(family, "android_like")

def curve(group, pop):
    r = cur[(cur["group"] == group) & (cur["population"] == pop)]
    if r.empty:
        return None
    r = r.iloc[0]
    c = {"n": int(r["n"]), "age_min": clean(r["age_min"]), "age_max": clean(r["age_max"]), "slope": clean(r["slope_per_month"]),
         "intercept": clean(r["intercept"]), "fit": r["fit_quality"], "q": {t: clean(r[f"q_{t}"]) for t in TERMS},
         "monthly": clean(r["monthly_depreciation_pct"])}
    if c["slope"] is None or c["intercept"] is None:
        c["fit"] = "no_fit"; c["q"] = {t: None for t in TERMS}
    c["extrap"] = {t: (c["q"][t] is None or c["age_min"] is None or t < c["age_min"] or t > c["age_max"]) for t in TERMS}
    return c

def in_span(c, t):
    return c is not None and c["q"][t] is not None and c["age_min"] is not None and c["age_min"] <= t <= c["age_max"]

def buckets(df):
    if df.empty:
        return {}
    g = df.groupby("jahr")["realisation"].agg(["size", "median", "min", "max"])
    return {int(k): {"qty": int(v["size"]), "med": float(v["median"]), "lo": float(v["min"]), "hi": float(v["max"])} for k, v in g.iterrows()}

# ---- the simulation: closed cycles per bucket and term (costs to sale, rent, margins)
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
sim_all = con.execute("""select model_family fam, term_months, count(*) n, avg(purchase_price) p, avg(rrp_gross_eur) rrp, avg(rrp_net_eur) rrp_net, avg(tco_eur - purchase_price) c,
    avg(rental_revenue) rent, avg(monthly_rate) rate, avg(realised_rv) rv, avg(lifecycle_result_eur) m, avg(months_billed) mb,
    sum(case when contract_end_effective < contract_end_planned then 1 else 0 end) early,
    avg(case when contract_end_effective < contract_end_planned then lifecycle_result_eur end) m_early,
    avg(case when not (contract_end_effective < contract_end_planned) then lifecycle_result_eur end) m_full
    from silver.device_ledger where is_closed group by 1, 2 order by 1, 2""").df()
sim_tot = con.execute("""select model_family fam, avg(tco_eur - purchase_price) c, avg(rrp_gross_eur) rrp, avg(rrp_net_eur) rrp_net, avg(purchase_price) p,
    avg(purchase_price / rrp_net_eur) buy_net, count(*) n from silver.device_ledger where is_closed group by 1""").df()
sim_by_bucket, tot_by_bucket = {}, {}
for fam, g in sim_all.groupby("fam"):
    sim_by_bucket[fam] = [{k: clean(v) for k, v in r.items() if k != "fam"} for r in g.to_dict("records")]
for _, r in sim_tot.iterrows():
    tot_by_bucket[r["fam"]] = {"n": int(r["n"]), "cost": float(r["c"]), "rrp": float(r["rrp"]), "rrp_net": float(r["rrp_net"]), "buy_net": float(r["buy_net"]),
                               "cost_share": float(r["c"]) / float(r["rrp_net"])}     # costs to sale in % of RRP, both without VAT (ledger amounts are net)
cost_share_by = {fam: {r["term_months"]: r["c"] / r["rrp_net"] for r in rows} for fam, rows in sim_by_bucket.items()}

# ---- the catalogue: models with a priced variant and a launch date, per series
priced = set(variants[variants["rrp_eur_launch_de"].notna()]["slug"])
cat = {}
for _, m in models.iterrows():
    if m["slug"] not in priced:
        continue
    ld = launch_date(m["launch_date_de"])
    if ld is None:
        continue
    cat.setdefault((m["family"], m["oem"]), []).append({
        "slug": m["slug"], "model": m["model_name"], "series": m["series"] if isinstance(m["series"], str) else "",
        "launch": ld.isoformat(), "launch_kind": m["launch_date_kind"], "age": round(months_between(ld, as_of), 1),
        "successor": m["successor"] if isinstance(m["successor"], str) else "",
        "successor_date": m["successor_launch_date"] if isinstance(m["successor_launch_date"], str) else ""})

# ---- the studies: year values per term (100 minus the loss named there), trade-in bids abroad
def study_rows(lst):
    out = []
    for s in lst:
        vals = list(s["werte"].values())
        out.append({"quelle": s["quelle"], "datum": s["datum"], "markt": s["markt"], "url": s["url"], "monate": s["monate"],
                    "modelle": ", ".join(f"{k} {100 - v:.0f} %" for k, v in s["werte"].items()), "lo": 100 - max(vals), "hi": 100 - min(vals)})
    return out

# ---- one entry per series
series = []
for (family, oem), mlist in sorted(cat.items()):
    key = f"{family} / {oem}"; fam = bucket_of(family, oem)
    ask = curve(key, "marketplace"); bid = curve(key, "tradein"); famc = curve(family, "marketplace")
    ap = anc[(anc["oem"] == oem) & (anc["family"] == family)].copy()
    ap["jahr"] = (ap["age_months"] // 12).astype(int) + 1
    if ask is None:      # no curve row at all: the series is in the catalogue, the market table has nothing for it
        ask = {"n": int((ap["grade"] != "TRADEIN").sum()), "age_min": None, "age_max": None, "slope": None, "intercept": None, "fit": "no_fit",
               "q": {t: None for t in TERMS}, "monthly": None, "extrap": {t: True for t in TERMS}}
    raw_ask = buckets(ap[ap["grade"] == "B"]); raw_bid = buckets(ap[ap["grade"] == "TRADEIN"])
    points = [{"slug": r["slug"], "age": float(r["age_months"]), "q": float(r["realisation"]), "model": r["model_name"], "spec": r["spec_used"] if isinstance(r["spec_used"], str) else "",
               "kind": "bid" if r["grade"] == "TRADEIN" else "ask"} for _, r in ap[ap["grade"].isin(["B", "TRADEIN"])].iterrows()]
    cs = cost_share_by.get(fam, {}); tot = tot_by_bucket.get(fam)
    cost_share = tot["cost_share"] if tot else None
    # break-even per term: rent must cover (purchase minus residual) plus the cycle costs, spread over the months
    rows = []; prev_q = buy_share; prev_extrap = False
    for t in TERMS:
        q = ask["q"][t]; extrap = ask["extrap"][t]
        loss = (buy_share - q) if q is not None else None
        year_pm = ((prev_q - q) / 12) if q is not None else None      # loss inside this year only, per month
        c_t = cs.get(t, cost_share)
        rows.append({"t": t, "q": q, "extrap": extrap, "year_pm": year_pm, "year_extrap": extrap or prev_extrap,
                     "qbid": bid["q"][t] if in_span(bid, t) else None, "extrap_bid": not in_span(bid, t),
                     "raw": raw_ask.get(t // 12), "rawbid": raw_bid.get(t // 12),
                     "loss": loss, "loss_pm": (loss / t) if loss is not None else None,
                     "cost_share": c_t, "cost_pm": (c_t / t) if c_t is not None else None,
                     "need_pm": ((loss + c_t) / t) if (loss is not None and c_t is not None) else None,
                     "factor_sim": factors.get(t)})
        prev_q = q if q is not None else prev_q; prev_extrap = extrap
    need = {r["t"]: r["need_pm"] for r in rows}
    for r in rows:      # the factor against every other term: rent per month of this term over rent per month of the other
        r["factor"] = {t2: (r["need_pm"] / need[t2]) if (r["need_pm"] is not None and need[t2]) else None for t2 in TERMS}
    # studies where the family has sourced year values: a second derivation of the term from trade-in bids abroad
    jahre = study_rows(STUDIES_BY_SERIES.get(key, []))
    stud = {}
    for t in TERMS:
        jt = [j for j in jahre if j["monate"] == t]
        if not jt or cs.get(t, cost_share) is None:
            continue
        lo = min(j["lo"] for j in jt) / 100.0; hi = max(j["hi"] for j in jt) / 100.0; c_t = cs.get(t, cost_share)
        need_lo = (buy_share - hi + c_t) / t; need_hi = (buy_share - lo + c_t) / t
        stud[t] = {"lo_q": lo, "hi_q": hi, "need_lo": need_lo, "need_hi": need_hi, "n": len(jt),
                   "factor_lo": {t2: (need_lo / need[t2]) if need[t2] else None for t2 in TERMS},
                   "factor_hi": {t2: (need_hi / need[t2]) if need[t2] else None for t2 in TERMS}}
    # the youngest generation of the series: latest launch without a successor in the catalogue; same day, the first slug
    mlist = sorted(mlist, key=lambda m: (m["launch"], m["slug"]))
    open_ = [m for m in mlist if not m["successor"]]
    latest = max(m["launch"] for m in (open_ or mlist))
    default = sorted([m for m in (open_ or mlist) if m["launch"] == latest], key=lambda m: m["slug"])[0]
    series.append({"key": key, "family": family, "oem": oem, "fam": fam, "fam_label": FAM_LABEL.get(fam, fam),
                   "ask": ask, "bid": bid, "fam_curve": famc, "models": mlist, "default_slug": default["slug"],
                   "points": points, "rows": rows, "studies": stud, "jahre": jahre,
                   "sim": sim_by_bucket.get(fam, []), "sim_n": tot["n"] if tot else 0, "sim_cost": tot["cost"] if tot else None,
                   "sim_rrp": tot["rrp"] if tot else None, "sim_rrp_net": tot["rrp_net"] if tot else None, "sim_buy_net": tot["buy_net"] if tot else None,
                   "cost_share": cost_share})

# default: the series with the most marketplace evidence among the usable fits; the term with the most closed cycles in its bucket
usable = [s for s in series if s["ask"]["fit"] == "ok"] or series
def_series = max(usable, key=lambda s: s["ask"]["n"])
def_term = max(def_series["sim"], key=lambda r: (r["n"], -r["term_months"]))["term_months"] if def_series["sim"] else TERMS[0]

# the family curves (all manufacturers) for the table "Dasselbe je Geraeteart"
famrows = []
for f in ["Smartphone", "Tablet", "Laptop"]:
    c = curve(f, "marketplace")
    if not c or c["slope"] is None:
        continue
    famrows.append({"family": f, "n": c["n"], "age_min": c["age_min"], "age_max": c["age_max"], "monthly": c["monthly"], "fit": c["fit"],
                    "q": {t: c["q"][t] for t in TERMS}, "extrap": {t: c["extrap"][t] for t in TERMS},
                    "loss_pm": {t: ((buy_share - c["q"][t]) / t if c["q"][t] is not None else None) for t in TERMS}})

data = {"today": TODAY, "terms": TERMS, "buy_share": buy_share, "disc": disc, "factors": factors,
        "default": {"series": def_series["key"], "term": int(def_term)},
        "series": series, "fam": famrows, "fam_label": FAM_LABEL}

# ---- write the data of this tab (the shell in web/app.js and the motor in web/engine/term.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(de_roles(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":"))) + "\n")
print(OUT.name + ": geschrieben")
