# -*- coding: utf-8 -*-
"""Laufzeit gegen Restwert: every number from outputs/market_*.csv (public), studies_de.json (sourced) or the lake (simulated)."""
import json, sys, pathlib, math
from _roles import de_roles  # noqa: E402
import pandas as pd, duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
TERMS = [12, 24, 36, 48]
cur = pd.read_csv(REPO / "outputs" / "market_curves.csv")
anc = pd.read_csv(REPO / "outputs" / "market_anchors.csv")
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

def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v

def curve(group, pop):
    r = cur[(cur["group"] == group) & (cur["population"] == pop)]
    if r.empty:
        return None
    r = r.iloc[0]
    return {"n": int(r["n"]), "age_min": float(r["age_min"]), "age_max": float(r["age_max"]), "slope": clean(r["slope_per_month"]),
            "intercept": clean(r["intercept"]), "fit": r["fit_quality"], "q": {t: clean(r[f"q_{t}"]) for t in TERMS},
            "monthly": clean(r["monthly_depreciation_pct"])}

apple_ask = curve("Smartphone / Apple", "marketplace"); apple_bid = curve("Smartphone / Apple", "tradein")
fam = {f: curve(f, "marketplace") for f in ["Smartphone", "Tablet", "Laptop"]}
fam_bid = {f: curve(f, "tradein") for f in ["Smartphone", "Tablet", "Laptop"]}

# raw Apple smartphone anchors by year of age (median, QTY), grade B asks and trade-in bids
ap = anc[(anc["oem"] == "Apple") & (anc["family"] == "Smartphone")].copy()
ap["jahr"] = (ap["age_months"] // 12).astype(int) + 1
def buckets(df):
    g = df.groupby("jahr")["realisation"].agg(["size", "median", "min", "max"])
    return {int(k): {"qty": int(v["size"]), "med": float(v["median"]), "lo": float(v["min"]), "hi": float(v["max"])} for k, v in g.iterrows()}
raw_ask = buckets(ap[ap["grade"] == "B"]); raw_bid = buckets(ap[ap["grade"] == "TRADEIN"])
points = [{"age": float(r["age_months"]), "q": float(r["realisation"]), "grade": r["grade"], "model": r["model_name"], "spec": r["spec_used"] if isinstance(r["spec_used"], str) else "", "kind": "bid" if r["grade"] == "TRADEIN" else "ask"}
          for _, r in ap[ap["grade"].isin(["B", "TRADEIN"])].iterrows()]

# studies: year-1 and year-3 residual (100 minus loss), trade-in bids abroad
jahre = []
for s in studies["iphone_jahre"]:
    vals = list(s["werte"].values())
    jahre.append({"quelle": s["quelle"], "datum": s["datum"], "markt": s["markt"], "url": s["url"], "monate": s["monate"],
                  "modelle": ", ".join(f"{k} {100 - v:.0f} %" for k, v in s["werte"].items()), "lo": 100 - max(vals), "hi": 100 - min(vals)})

# simulated cycle costs and rent per term, iPhone-like family
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
sim = con.execute("""select term_months, count(*) n, avg(purchase_price) p, avg(rrp_gross_eur) rrp, avg(rrp_net_eur) rrp_net, avg(tco_eur - purchase_price) c,
    avg(rental_revenue) rent, avg(monthly_rate) rate, avg(realised_rv) rv, avg(lifecycle_result_eur) m, avg(months_billed) mb,
    sum(case when contract_end_effective < contract_end_planned then 1 else 0 end) early,
    avg(case when contract_end_effective < contract_end_planned then lifecycle_result_eur end) m_early,
    avg(case when not (contract_end_effective < contract_end_planned) then lifecycle_result_eur end) m_full
    from silver.device_ledger where is_closed and model_family='iphone_like' group by 1 order by 1""").df()
simall = con.execute("select avg(tco_eur - purchase_price) c, avg(rrp_gross_eur) rrp, avg(rrp_net_eur) rrp_net, avg(purchase_price) p, avg(purchase_price / rrp_net_eur) buy_net, count(*) n from silver.device_ledger where is_closed and model_family='iphone_like'").df().iloc[0]
cost_share = float(simall["c"]) / float(simall["rrp_net"])      # simulated costs to sale in % of RRP, both without VAT (ledger amounts are net)
cost_share_by_term = {int(r["term_months"]): float(r["c"]) / float(r["rrp_net"]) for _, r in sim.iterrows()}
sim_buy_net = float(simall["buy_net"])                             # the simulation's own purchase price in % of net RRP
buy_share = 1.0 - disc                                             # purchase price in % of RRP (placeholder discount)
sim_rows = [{k: clean(v) for k, v in r.items()} for r in sim.to_dict("records")]

# break-even per term: rent must cover (purchase minus residual) plus the cycle costs, spread over the months
rows = []
prev_q = buy_share
for t in TERMS:
    q = apple_ask["q"][t]; extrap = t < apple_ask["age_min"] or t > apple_ask["age_max"]
    loss = buy_share - q if q is not None else None
    year_pm = ((prev_q - q) / 12) if q is not None else None      # loss inside this year only, per month
    prev_q = q if q is not None else prev_q
    bid_in_span = apple_bid["age_min"] <= t <= apple_bid["age_max"]
    rows.append({"t": t, "q": q, "extrap": extrap, "year_pm": year_pm, "qbid": apple_bid["q"][t] if bid_in_span else None, "extrap_bid": not bid_in_span,
                 "raw": raw_ask.get(t // 12), "rawbid": raw_bid.get(t // 12),
                 "loss": loss, "loss_pm": (loss / t) if loss is not None else None,
                 "cost_share": cost_share_by_term.get(t, cost_share), "cost_pm": cost_share_by_term.get(t, cost_share) / t,
                 "need_pm": ((loss + cost_share_by_term.get(t, cost_share)) / t) if loss is not None else None,
                 "factor_sim": factors.get(t)})
base = [r for r in rows if r["t"] == 24][0]["need_pm"]
for r in rows:
    r["factor_derived"] = (r["need_pm"] / base) if (r["need_pm"] is not None and base) else None
# a second derivation for 12 months from the studies' year-1 floor (trade-in bids abroad), because the German curve has no evidence under 18 months
j12 = [j for j in jahre if j["monate"] == 12]
floor12 = min(j["lo"] for j in j12) / 100.0; ceil12 = max(j["hi"] for j in j12) / 100.0
c12 = cost_share_by_term.get(12, cost_share)
alt12 = {"lo_q": floor12, "hi_q": ceil12, "need_lo": (buy_share - ceil12 + c12) / 12, "need_hi": (buy_share - floor12 + c12) / 12}
alt12["factor_lo"] = alt12["need_lo"] / base; alt12["factor_hi"] = alt12["need_hi"] / base

famrows = []
for f, c in fam.items():
    if not c or c["slope"] is None:
        continue
    famrows.append({"family": f, "n": c["n"], "age_min": c["age_min"], "age_max": c["age_max"], "monthly": c["monthly"],
                    "q": {t: c["q"][t] for t in TERMS}, "extrap": {t: (t < c["age_min"] or t > c["age_max"]) for t in TERMS},
                    "loss_pm": {t: ((buy_share - c["q"][t]) / t if c["q"][t] is not None else None) for t in TERMS}})

data = {"today": TODAY, "apple_ask": apple_ask, "apple_bid": apple_bid, "rows": rows, "alt12": alt12, "jahre": jahre, "points": points,
        "sim": sim_rows, "cost_share": cost_share, "cost_share_by_term": cost_share_by_term, "buy_share": buy_share, "disc": disc, "sim_n": int(simall["n"]), "sim_cost": float(simall["c"]), "sim_rrp": float(simall["rrp"]), "sim_rrp_net": float(simall["rrp_net"]), "sim_buy_net": sim_buy_net,
        "factors": factors, "fam": famrows, "base_need": base}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/term.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(de_roles(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":"))) + "\n")
print(OUT.name + ": geschrieben")
