# -*- coding: utf-8 -*-
"""Build web/data/faq.json: the curated questions of web/tools/gen/faq.json plus every number they need, read from the run.

The answers in faq.json never carry a number that comes from the tool's data; they carry placeholders
{"fact": "<key>", "fmt": "qty|pct|pct1|num|num1|eur|de|text"}. This script computes the facts from outputs/market_curves.csv,
outputs/market_anchors.csv and the public anchor tables (restwert.market.anchors.load_anchors), writes them next to the entries
and records where each one comes from (fact_sources). A placeholder the script cannot fill stays unfilled; the motor then
shows "[Zahl fehlt: key]" and reports it, so a missing number never turns into an invented one.

Fact keys:
  <family>_<oem>_n | _age_min | _age_max | _monthly | _q24 | _q36 | _fit        marketplace curve per family and manufacturer
  <family>_n | _age_min | _age_max | _monthly | _q24 | _q36 | _fit              marketplace curve per family
  anchors_raw, anchors_total, anchors_dropped_total, anchors_dropped_no_rrp, anchors_dropped_spec_mismatch
  anchors_grade_unknown, anchors_tradein, anchors_marketplace
  models_total, models_with_anchors, oems_with_anchors, variants_total, variants_priced, variants_unpriced
  laptop_anchors_raw (rows of used_prices.csv whose slug is a laptop), laptop_variants_unpriced
  curve_min_n, curve_ok_n, curve_min_span (the gate of restwert.market.curves)
  <family>_tradein_n (0 without a trade-in row), <family>_tradein_age_min, <family>_q24_klammer (text: whether the 24-month
  reading lies outside the evidence), anchors_in_curves, truth_q_young_cap, truth_ask_to_realised (config/lake.yaml)
Family and manufacturer names are lowercased ASCII: laptop, smartphone, tablet; apple, dell, hp, lenovo, microsoft, samsung,
google, motorola, fairphone, hmd.
"""
import json
import pathlib
import re
import sys

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
TODAY = sys.argv[3]
sys.path.insert(0, str(REPO))
from restwert.market.anchors import load_anchors, read_tables  # noqa: E402

HERE = pathlib.Path(__file__).parent
CURATED = HERE / "faq.json"
CURVES = REPO / "outputs" / "market_curves.csv"
ANCHORS = REPO / "outputs" / "market_anchors.csv"
FIT_DE = {"ok": "belastbar", "thin": "unsicher", "no_fit": "keine Kurve"}


def slug(s: str) -> str:
    s = str(s).lower()
    s = "hmd" if s.startswith("hmd") else s
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        v = v.item()
    return v


facts: dict = {}
src: dict = {}


def put(key: str, value, where: str) -> None:
    facts[key] = clean(value)
    src[key] = where


curves = pd.read_csv(CURVES)
mp = curves[curves["population"] == "marketplace"]
for r in mp.itertuples():
    if r.group_kind == "family":
        base, where = slug(r.group), f"outputs/market_curves.csv, Zeile group = {r.group}, population = marketplace"
    elif r.group_kind == "family_oem":
        fam, oem = [x.strip() for x in str(r.group).split("/", 1)]
        base, where = f"{slug(fam)}_{slug(oem)}", f"outputs/market_curves.csv, Zeile group = {r.group}, population = marketplace"
    else:
        continue
    put(base + "_n", r.n, where + ", Spalte n")
    put(base + "_age_min", r.age_min, where + ", Spalte age_min")
    put(base + "_age_max", r.age_max, where + ", Spalte age_max")
    put(base + "_monthly", r.monthly_depreciation_pct, where + ", Spalte monthly_depreciation_pct")
    put(base + "_q24", r.q_24, where + ", Spalte q_24")
    put(base + "_q36", r.q_36, where + ", Spalte q_36")
    put(base + "_fit", FIT_DE.get(str(r.fit_quality), str(r.fit_quality)), where + ", Spalte fit_quality")
# trade-in curves per family: 0 when the family has no trade-in row (the FAQ says so with the number)
tr = curves[(curves["population"] == "tradein") & (curves["group_kind"] == "family")]
for fam in sorted(set(mp.loc[mp["group_kind"] == "family", "group"].astype(str))):
    row = tr[tr["group"].astype(str) == fam]
    where = f"outputs/market_curves.csv, Zeile group = {fam}, population = tradein"
    put(slug(fam) + "_tradein_n", int(row["n"].iloc[0]) if len(row) else 0, where + ", Spalte n (0, wenn die Zeile fehlt)")
    put(slug(fam) + "_tradein_age_min", clean(row["age_min"].iloc[0]) if len(row) else None, where + ", Spalte age_min")
    # whether the 24-month reading of the family curve is outside its evidence (the page shows it in brackets then)
    frow = mp[(mp["group_kind"] == "family") & (mp["group"].astype(str) == fam)]
    if len(frow) and pd.notna(frow["age_min"].iloc[0]):
        am = float(frow["age_min"].iloc[0])
        put(slug(fam) + "_q24_klammer", "in Klammern, weil 24 Monate vor dem jüngsten Beleg liegen" if am > 24 else "ohne Klammern, weil 24 Monate innerhalb der Belege liegen",
            f"make_faq_data.py aus outputs/market_curves.csv, Zeile group = {fam}, population = marketplace, Spalte age_min gegen 24")
allrows = curves[curves["group_kind"] == "all"]
put("anchors_in_curves", int(allrows["n"].sum()), "outputs/market_curves.csv, Summe der Spalte n der Zeilen group_kind = all (marketplace und tradein)")
import yaml
with open(REPO / "config" / "lake.yaml", encoding="utf-8") as fh:
    lake = yaml.safe_load(fh)
tv2 = lake.get("truth_v2") or {}
put("truth_q_young_cap", tv2.get("q_young_cap"), "config/lake.yaml, Block truth_v2, Schlüssel q_young_cap")
put("truth_ask_to_realised", tv2.get("ask_to_realised"), "config/lake.yaml, Block truth_v2, Schlüssel ask_to_realised")
put("curve_min_n", 6, "restwert/market/curves.py, fit_curves(min_n=6)")
put("curve_ok_n", 12, "restwert/market/curves.py, fit_quality ok ab 12 Belegen")
put("curve_min_span", 6, "restwert/market/curves.py, fit_curves(min_age_span=6.0)")

a = pd.read_csv(ANCHORS)
put("anchors_total", len(a), "outputs/market_anchors.csv, Anzahl Zeilen")
put("anchors_grade_unknown", int((a["grade"] == "UNKNOWN").sum()), "outputs/market_anchors.csv, Zeilen mit grade = UNKNOWN")
put("anchors_tradein", int((a["grade"] == "TRADEIN").sum()), "outputs/market_anchors.csv, Zeilen mit grade = TRADEIN")
put("anchors_marketplace", int((a["grade"] != "TRADEIN").sum()), "outputs/market_anchors.csv, Zeilen mit grade ungleich TRADEIN")
put("models_with_anchors", a["slug"].nunique(), "outputs/market_anchors.csv, verschiedene slug")
put("oems_with_anchors", a["oem"].nunique(), "outputs/market_anchors.csv, verschiedene oem")

loaded = load_anchors(REPO / "data" / "catalogue", REPO / "data" / "anchors")
dropped = loaded.attrs["dropped"]
tables = read_tables(REPO / "data" / "catalogue", REPO / "data" / "anchors")
raw = tables.used
put("anchors_raw", len(raw), "data/anchors/used_prices.csv, Anzahl Zeilen")
put("anchors_dropped_total", sum(dropped.values()), "restwert.market.anchors.load_anchors, attrs dropped, Summe")
put("anchors_dropped_no_rrp", dropped["no_rrp"], "restwert.market.anchors.load_anchors, attrs dropped no_rrp")
put("anchors_dropped_spec_mismatch", dropped["spec_mismatch"], "restwert.market.anchors.load_anchors, attrs dropped spec_mismatch")
put("models_total", len(tables.models), "data/catalogue/models.csv, Anzahl Zeilen")
var = tables.variants
priced = pd.to_numeric(var["rrp_eur_launch_de"], errors="coerce").notna()
put("variants_total", len(var), "data/catalogue/variants.csv, Anzahl Zeilen")
put("variants_priced", int(priced.sum()), "data/catalogue/variants.csv, Zeilen mit rrp_eur_launch_de")
put("variants_unpriced", int((~priced).sum()), "data/catalogue/variants.csv, Zeilen ohne rrp_eur_launch_de")
lap_slugs = set(tables.models.loc[tables.models["family"].astype(str).str.lower() == "laptop", "slug"])
put("laptop_anchors_raw", int(raw["slug"].isin(lap_slugs).sum()), "data/anchors/used_prices.csv, Zeilen mit Laptop-slug laut models.csv")
put("laptop_variants_unpriced", int((~priced & var["slug"].isin(lap_slugs)).sum()), "data/catalogue/variants.csv, Laptop-Zeilen ohne rrp_eur_launch_de")

curated = json.loads(CURATED.read_text(encoding="utf-8")) if CURATED.exists() else {"entries": []}
entries = curated.get("entries", [])
needed = {s["fact"] for e in entries for s in e.get("antwort", []) if isinstance(s, dict) and s.get("fact")}
missing = sorted(k for k in needed if k not in facts)
if missing:
    print("faq: Platzhalter ohne Wert (der Motor zeigt sie an):", ", ".join(missing))
for e in entries:
    for k in ("id", "gruppe", "frage", "antwort", "quellen"):
        if k not in e:
            raise SystemExit(f"faq.json: entry without {k}: {e.get('id')}")
    text = "".join(s.get("text", "") for s in e["antwort"] if isinstance(s, dict)) + e["frage"]
    if "\u2013" in text or "\u2014" in text:
        raise SystemExit(f"faq.json: dash in entry {e['id']}")

data = {
    "today": TODAY,
    "research_date": curated.get("research_date", TODAY),
    "research_note": curated.get("research_note", ""),
    "facts": {k: facts[k] for k in sorted(facts)},
    "fact_sources": {k: src[k] for k in sorted(src)},
    "entries": entries,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben, " + str(len(entries)) + " Fragen, " + str(len(needed)) + " Platzhalter, " + str(len(facts)) + " Werte berechnet")
