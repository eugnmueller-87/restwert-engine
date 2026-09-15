# -*- coding: utf-8 -*-
"""Geraet nachschlagen: one device (model and storage) with its public evidence, the tool's residual value forecast,
its fleet in the simulation, and a per-term calculator that says from which month the device earns its keep."""
import json, sys, pathlib, math
from datetime import date
import pandas as pd, duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
TERMS = [12, 24, 36, 48]
as_of = date.fromisoformat(TODAY)

def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v

def months_between(d0, d1):
    return (d1.year - d0.year) * 12 + (d1.month - d0.month) + (d1.day - d0.day) / 30.4

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
vat = float(_find(assumptions, "vat_rate")["value"]) if isinstance(_find(assumptions, "vat_rate"), dict) else float(_find(assumptions, "vat_rate") or 0.19)
factors = {int(k): float(v) for k, v in lake["term_rate_factor"].items()}
rate_pct = {k: float(v["monthly_rate_pct_of_landed"]) for k, v in lake["families"].items()}
disc_oem = {k: [float(x) for x in v] for k, v in lake["discount_by_oem"].items()}
FAM_LABEL = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "tablet_like": "Tablet", "laptop_like": "Laptop"}

models = pd.read_csv(REPO / "data" / "catalogue" / "models.csv", comment="#")
variants = pd.read_csv(REPO / "data" / "catalogue" / "variants.csv", comment="#")
anchors = pd.read_csv(REPO / "outputs" / "market_anchors.csv")
curves = pd.read_csv(REPO / "outputs" / "market_curves.csv")
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)

# the forecast grid, grade B, one ratio per month per model (share of the purchase price)
grid = con.execute("select model, model_family, months_since_launch m, forecast_rv_ratio r, ratio_low lo, ratio_high hi, fit_quality from rv_forecast_grid where grade='B' and run_id=(select run_id from forecast_runs order by as_of desc limit 1) order by model, m").df()
grid_by_model = {}
for slug, g in grid.groupby("model"):
    grid_by_model[slug] = {"family": g["model_family"].iloc[0], "fit": g["fit_quality"].iloc[0], "r": [round(float(x), 5) for x in g["r"]], "lo": [round(float(x), 5) for x in g["lo"]], "hi": [round(float(x), 5) for x in g["hi"]]}

# the simulated fleet per slug and term, and per family and term (costs, purchase share)
fleet = con.execute("""select slug, term_months, count(*) n, sum(case when is_closed then 1 else 0 end) closed,
  avg(case when is_closed then purchase_price end) p, avg(case when is_closed then monthly_rate end) rate,
  avg(purchase_price) p_all, avg(monthly_rate) rate_all, avg(rrp_net_eur) rrp_net,
  avg(case when is_closed then rental_revenue end) rent, avg(case when is_closed then realised_rv end) rv,
  avg(case when is_closed then tco_eur - purchase_price end) c, avg(case when is_closed then lifecycle_result_eur end) m,
  sum(case when is_closed and lifecycle_result_eur > 0 then 1 else 0 end) pos,
  sum(case when is_closed and contract_end_effective < contract_end_planned then 1 else 0 end) early
  from silver.device_ledger where term_months is not null group by 1, 2 order by 1, 2""").df()
fleet_by_slug = {}
for slug, g in fleet.groupby("slug"):
    fleet_by_slug[slug] = [{k: clean(v) for k, v in r.items() if k != "slug"} for r in g.to_dict("records")]
# the exact variant (storage) for the calculator's defaults: purchase price and rate over every device, costs over closed
var_fleet = con.execute("""select slug, storage_gb, term_months, count(*) n, avg(purchase_price) p, avg(monthly_rate) rate, avg(rrp_net_eur) rrp_net,
  sum(case when is_closed then 1 else 0 end) closed, avg(case when is_closed then tco_eur - purchase_price end) c
  from silver.device_ledger where term_months is not null and storage_gb is not null group by 1, 2, 3""").df()
var_by_key = {}
for _, r in var_fleet.iterrows():
    var_by_key[f"{r['slug']}|{int(r['storage_gb'])}|{int(r['term_months'])}"] = {"n": int(r["n"]), "p": clean(r["p"]), "rate": clean(r["rate"]), "rrp_net": clean(r["rrp_net"]), "closed": int(r["closed"]), "c": clean(r["c"])}
not_deployed = dict(con.execute("select slug, count(*) from silver.device_ledger where term_months is null group by 1").fetchall())
fam_term = con.execute("""select model_family, term_months, count(*) n, avg(tco_eur - purchase_price) c, avg(purchase_price / rrp_net_eur) buy
  from silver.device_ledger where is_closed group by 1, 2""").df()
fam_cost = {}
for _, r in fam_term.iterrows():
    fam_cost.setdefault(r["model_family"], {})[int(r["term_months"])] = {"c": round(float(r["c"]), 2), "buy": round(float(r["buy"]), 4), "n": int(r["n"])}
slug_family = dict(con.execute("select distinct slug, model_family from silver.device_ledger where slug is not null").fetchall())

# catalogue: one entry per priced variant, with its model
priced = variants[variants["rrp_eur_launch_de"].notna()].copy()
mrows = {r["slug"]: r for _, r in models.iterrows()}
def launch_date(v):
    if not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v if len(v) == 10 else v + "-15")
    except ValueError:
        return None
def fam_key(slug, family_label):
    if slug in slug_family:
        return slug_family[slug]
    if slug in grid_by_model:
        return grid_by_model[slug]["family"]
    return {"Smartphone": "android_like", "Tablet": "tablet_like", "Laptop": "laptop_like"}.get(family_label, "android_like")
devices = []
for _, v in priced.iterrows():
    m = mrows.get(v["slug"])
    if m is None:
        continue
    ld = launch_date(m["launch_date_de"])
    if ld is None:
        continue
    fk = fam_key(v["slug"], m["family"])
    if m["oem"] == "Apple" and m["family"] == "Smartphone":
        fk = "iphone_like"
    devices.append({
        "id": f"{v['slug']}|{v['spec']}", "slug": v["slug"], "label": f"{m['model_name']}, {v['spec']}", "model": m["model_name"], "spec": v["spec"],
        "oem": m["oem"], "family": m["family"], "fam": fk, "series": m["series"] if isinstance(m["series"], str) else "",
        "launch": ld.isoformat(), "launch_kind": m["launch_date_kind"], "launch_url": m["launch_source_url"] if isinstance(m["launch_source_url"], str) else "",
        "age": round(months_between(ld, as_of), 1), "rrp": float(v["rrp_eur_launch_de"]), "rrp_url": v["rrp_source_url"] if isinstance(v["rrp_source_url"], str) else "",
        "rrp_date": v["rrp_source_date"] if isinstance(v["rrp_source_date"], str) else "", "storage": clean(v["storage_gb"]),
        "successor": m["successor"] if isinstance(m["successor"], str) else "", "successor_date": m["successor_launch_date"] if isinstance(m["successor_launch_date"], str) else "",
    })
devices.sort(key=lambda d: (d["family"], d["oem"], d["label"]))

anc_by_slug = {}
for slug, g in anchors.groupby("slug"):
    anc_by_slug[slug] = [{"spec": r["spec_used"] if isinstance(r["spec_used"], str) else "", "grade": r["grade"], "condition": r["condition"], "age": round(float(r["age_months"]), 1),
                          "price": float(r["price_eur"]), "rrp": float(r["rrp_eur_launch_de"]), "q": round(float(r["realisation"]), 4), "kind": r["source_kind"], "url": r["source_url"], "date": r["date_seen"]} for _, r in g.iterrows()]
curve_rows = {}
for _, c in curves[curves["population"] == "marketplace"].iterrows():
    curve_rows[c["group"]] = {"n": int(c["n"]), "age_min": clean(c["age_min"]), "age_max": clean(c["age_max"]), "q": {t: clean(c[f"q_{t}"]) for t in TERMS}, "fit": c["fit_quality"], "slope": clean(c["slope_per_month"]), "intercept": clean(c["intercept"])}

data = {"today": TODAY, "vat": vat, "factors": factors, "rate_pct": rate_pct, "disc_oem": disc_oem, "fam_label": FAM_LABEL, "fam_cost": fam_cost,
        "devices": devices, "grid": grid_by_model, "fleet": fleet_by_slug, "var_fleet": var_by_key, "not_deployed": not_deployed, "anchors": anc_by_slug, "curves": curve_rows}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/device.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben")
