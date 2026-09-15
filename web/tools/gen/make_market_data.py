# -*- coding: utf-8 -*-
"""Build the Restwertrealisierung page from outputs/market_anchors.csv and market_curves.csv."""
import json, sys, pathlib, math
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
a = pd.read_csv(REPO / "outputs" / "market_anchors.csv")
c = pd.read_csv(REPO / "outputs" / "market_curves.csv")

def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    return v

anchors = [{k: clean(v) for k, v in r.items()} for r in a[["slug", "model_name", "oem", "family", "spec_used", "condition", "grade", "age_months", "rrp_eur_launch_de", "price_eur", "realisation", "source_kind", "source_url", "date_seen"]].to_dict("records")]
curves = [{k: clean(v) for k, v in r.items()} for r in c.to_dict("records")]
b = a[a["grade"] == "B"]
ser = b.groupby(["family", "oem", "series"]).agg(qty=("realisation", "size"), monate=("age_months", "median"), mitte=("realisation", "median"), lo=("realisation", "min"), hi=("realisation", "max")).reset_index()
ser = ser[ser["qty"] >= 3].sort_values(["family", "monate"])
series = [{k: clean(v) for k, v in r.items()} for r in ser.to_dict("records")]
studies = json.load(open(pathlib.Path(__file__).parent / "studies_de.json", encoding="utf-8"))
exrow = a[(a["model_name"] == "iPhone 15") & (a["spec_used"] == "128 GB") & (a["grade"] == "B")].sort_values("date_seen").iloc[-1]
example = {"model": "iPhone 15 mit 128 GB", "rrp": int(round(exrow["rrp_eur_launch_de"])), "price": int(round(exrow["price_eur"])), "pct": int(round(100 * exrow["realisation"])), "date": str(exrow["date_seen"])}
data = {"today": TODAY, "example": example, "anchors": anchors, "curves": curves, "series": series, "studies": studies,
        "n_models": int(a["slug"].nunique()), "n_anchors": int(len(a)), "n_oem": int(a["oem"].nunique())}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/market.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben")
