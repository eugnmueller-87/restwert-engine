"""Prognoseguete: data/forecast.json fuer den Tab "Prognoseguete" der Restwert Engine (Version 4).

Liest nur, was der Motor schon rechnet: outputs/rv_forecast_error_monthly.csv (Fehlerreihe je Monat und
Geraeteart, SPEC 5.5), outputs/backtest_result.csv (Rueckblick-Test), outputs/rv_forecast_of_record.csv
(welche Prognose bei Rueckgabe galt), outputs/advisories.csv (ADV02), outputs/kpi_values.csv
(KPI_TOP_RV_FORECAST_ERROR), die Tabelle forecast_runs in data/restwert.duckdb (die Laeufe),
config/thresholds.yaml (forecast_recalibration_bias_pct) und config/kpi_targets.yaml (Ziel). Keine Zahl
wird getippt. Aufruf: python make_forecast_data.py <REPO> <OUT.json> <TODAY>
"""
from __future__ import annotations

import json
from _roles import de_roles  # noqa: E402
import math
import pathlib
import sys

import duckdb
import pandas as pd
import yaml

REPO = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
TODAY = sys.argv[3]
MIN_ROWS = 10  # restwert.forecast.error_series.MIN_ROWS_FOR_METRICS

FAM_LABEL = {"*": "alle Gerätearten", "iphone_like": "iPhone", "android_like": "Android-Smartphone",
             "tablet_like": "Tablet", "laptop_like": "Laptop"}
FAM_ORDER = ["*", "iphone_like", "android_like", "tablet_like", "laptop_like"]


def fl(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, 6)


def iv(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0
    return 0 if math.isnan(x) else int(x)


def own(s):
    return str(s or "").replace(" (name)", "").strip()


def month_str(v):
    return pd.Timestamp(v).strftime("%Y-%m")


# ---- the error series
e = pd.read_csv(REPO / "outputs" / "rv_forecast_error_monthly.csv")
e["month"] = pd.to_datetime(e["month"])
series = []
for r in e.sort_values(["month", "model_family"]).itertuples(index=False):
    series.append({
        "month": month_str(r.month), "fam": r.model_family,
        "n_sales": iv(r.n_sales), "n_fc": iv(r.n_with_forecast), "n_as_is": iv(r.n_excluded_as_is),
        "mape": fl(r.mape), "bias": fl(r.bias), "wape": fl(r.wape), "mae": fl(r.mae_eur), "real": fl(r.realisation_ratio),
        "sum_real": fl(r.sum_realised), "sum_fc": fl(r.sum_forecast),
        "mape_ch": fl(r.mape_channel_adjusted), "bias_ch": fl(r.bias_channel_adjusted),
        "n_mkt": iv(r.n_marketplace), "mape_mkt": fl(r.mape_marketplace), "bias_mkt": fl(r.bias_marketplace),
        "runs": None if pd.isna(r.run_ids_used) else str(r.run_ids_used),
    })

m = e[e["mape"].notna()]
fam_summary = []
for fam in FAM_ORDER:
    g = m[m["model_family"] == fam]
    all_g = e[e["model_family"] == fam]
    if len(all_g) == 0:
        continue
    fam_summary.append({
        "fam": fam, "months": int(len(g)), "months_thin": int((all_g["n_with_forecast"] > 0).sum() - len(g)),
        "n_sales": iv(all_g["n_sales"].sum()), "n_fc": iv(all_g["n_with_forecast"].sum()), "n_as_is": iv(all_g["n_excluded_as_is"].sum()),
        "mape": fl(g["mape"].mean()) if len(g) else None, "bias": fl(g["bias"].mean()) if len(g) else None,
        "wape": fl(g["wape"].mean()) if len(g) else None, "mae": fl(g["mae_eur"].mean()) if len(g) else None,
        "mape_ch": fl(g["mape_channel_adjusted"].mean()) if len(g) else None, "bias_ch": fl(g["bias_channel_adjusted"].mean()) if len(g) else None,
        "mape_mkt": fl(g["mape_marketplace"].mean()) if g["mape_marketplace"].notna().any() else None,
        "first": month_str(g["month"].min()) if len(g) else None, "last": month_str(g["month"].max()) if len(g) else None,
    })

# ---- the KPI of the latest complete month (business view) and its target
k = pd.read_csv(REPO / "outputs" / "kpi_values.csv")
krow = k[k["kpi_id"] == "KPI_TOP_RV_FORECAST_ERROR"]
targets = yaml.safe_load(open(REPO / "config" / "kpi_targets.yaml", encoding="utf-8"))
target = None
target_owner = ""
if isinstance(targets, dict):
    t = targets.get("targets") or {}
    target = t.get("KPI_TOP_RV_FORECAST_ERROR")
    target_owner = own(targets.get("targets_owner", ""))
latest = None
star = e[(e["model_family"] == "*") & e["mape"].notna()].sort_values("month")
as_of = pd.Timestamp(krow.iloc[0]["as_of"]) if len(krow) else pd.Timestamp(TODAY)
floor = as_of.to_period("M").to_timestamp()
complete = star[star["month"] < floor]
if len(complete):
    r = complete.iloc[-1]
    latest = {
        "month": month_str(r["month"]), "mape": fl(r["mape"]), "bias": fl(r["bias"]), "wape": fl(r["wape"]), "mae": fl(r["mae_eur"]),
        "real": fl(r["realisation_ratio"]), "n_sales": iv(r["n_sales"]), "n_fc": iv(r["n_with_forecast"]), "n_as_is": iv(r["n_excluded_as_is"]),
        "mape_ch": fl(r["mape_channel_adjusted"]), "bias_ch": fl(r["bias_channel_adjusted"]),
        "n_mkt": iv(r["n_marketplace"]), "mape_mkt": fl(r["mape_marketplace"]), "bias_mkt": fl(r["bias_marketplace"]),
        "sum_real": fl(r["sum_realised"]), "sum_fc": fl(r["sum_forecast"]),
        "kpi_value": fl(krow.iloc[0]["value"]) if len(krow) else None,
        "kpi_status": str(krow.iloc[0]["status"]) if len(krow) and "status" in krow.columns else "",
        "target": fl(target), "target_owner": target_owner or "CFO",
    }

# ---- ADV02: trailing three complete months, channel-adjusted bias against the threshold
thr = yaml.safe_load(open(REPO / "config" / "thresholds.yaml", encoding="utf-8"))
tblock = None
for section in thr.values() if isinstance(thr, dict) else []:
    if isinstance(section, dict) and "forecast_recalibration_bias_pct" in section:
        tblock = section["forecast_recalibration_bias_pct"]
        break
adv_thr = fl(tblock.get("value")) if isinstance(tblock, dict) else None
adv_owner = own(tblock.get("owner", "")) if isinstance(tblock, dict) else ""
tail3 = complete[complete["bias_channel_adjusted"].notna()].tail(3)
adv = pd.read_csv(REPO / "outputs" / "advisories.csv")
fired = adv[adv["kind"] == "forecast_calibration"] if "kind" in adv.columns else adv.iloc[0:0]
adv02 = {
    "threshold": adv_thr, "owner": adv_owner or "Head of Recommerce",
    "months": [month_str(x) for x in tail3["month"]],
    "mean_bias_ch": fl(tail3["bias_channel_adjusted"].mean()) if len(tail3) else None,
    "mean_bias": fl(tail3["bias"].mean()) if len(tail3) else None,
    "n_fc": iv(tail3["n_with_forecast"].sum()) if len(tail3) else 0,
    "fired": int(len(fired)),
    "fired_notes": [str(x) for x in fired["note"].head(3)] if len(fired) else [],
}

# ---- backtest
b = pd.read_csv(REPO / "outputs" / "backtest_result.csv")
backtest = []
for r in b.itertuples(index=False):
    backtest.append({
        "id": str(r.backtest_id), "cutoff": str(r.cutoff), "fam": str(r.model_family), "n_train": iv(r.n_train), "n_test": iv(r.n_test),
        "mape": fl(r.mape), "bias": fl(r.bias), "wape": fl(r.wape), "mae": fl(r.mae_eur), "rmse_log": fl(r.rmse_log),
        "train_max": str(r.train_max_sale_date), "test_min": str(r.test_min_sale_date),
    })
backtest.sort(key=lambda x: FAM_ORDER.index(x["fam"]) if x["fam"] in FAM_ORDER else 99)

# ---- forecast of record: coverage
rec = pd.read_csv(REPO / "outputs" / "rv_forecast_of_record.csv")
missing = rec[rec["is_missing"].astype(bool)]
reasons = {str(k): int(v) for k, v in missing["missing_reason"].value_counts().items()}
record = {
    "n_returns": int(len(rec)), "n_with": int((~rec["is_missing"].astype(bool)).sum()), "n_missing": int(len(missing)), "reasons": reasons,
    "first_return": str(pd.to_datetime(rec["return_date"]).min().date()), "last_return": str(pd.to_datetime(rec["return_date"]).max().date()),
    "n_runs_used": int(rec["run_id"].dropna().nunique()),
    "grades": {str(k): int(v) for k, v in rec["grade_used"].dropna().value_counts().sort_index().items()},
}

# ---- the runs (registry) and the current forecast
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
runs_df = con.execute("select run_id, as_of, method, n_train_total, n_train_json, fit_quality_json from forecast_runs order by as_of").df()
runs = []
for r in runs_df.itertuples(index=False):
    n_train = json.loads(r.n_train_json) if isinstance(r.n_train_json, str) else {}
    fq = json.loads(r.fit_quality_json) if isinstance(r.fit_quality_json, str) else {}
    runs.append({"id": str(r.run_id), "as_of": str(pd.Timestamp(r.as_of).date()), "method": str(r.method), "n_train": iv(r.n_train_total),
                 "by_fam": {k: iv(v) for k, v in n_train.items() if k != "pooled"}, "fit": fq})
current = {str(k): int(v) for k, v in con.execute("select fit_quality, count(*) from rv_forecast_current group by 1").fetchall()}
n_current = int(con.execute("select count(*) from rv_forecast_current").fetchone()[0])
con.close()

data = {
    "today": TODAY, "as_of": str(as_of.date()), "min_rows": MIN_ROWS, "fam_label": FAM_LABEL, "fam_order": FAM_ORDER,
    "latest": latest, "adv02": adv02, "series": series, "fam_summary": fam_summary, "backtest": backtest, "record": record,
    "runs": runs, "n_runs": len(runs), "first_run": runs[0]["as_of"] if runs else None, "last_run": runs[-1]["as_of"] if runs else None,
    "method": runs[-1]["method"] if runs else "", "current": current, "n_current": n_current,
    "months_with_metrics": int(len(star)), "first_metric_month": month_str(star["month"].min()) if len(star) else None,
    "last_metric_month": month_str(star["month"].max()) if len(star) else None,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(de_roles(json.dumps(data, ensure_ascii=False, separators=(",", ":"))) + "\n", encoding="utf-8", newline="\n")
print("forecast.json:", len(series), "Zeilen Fehlerreihe,", len(backtest), "Rueckblick-Zeilen,", len(runs), "Laeufe, letzter Monat", latest and latest["month"],
      "MAPE", latest and latest["mape"], "Ziel", latest and latest["target"], "ADV02", adv02["mean_bias_ch"], "gegen", adv02["threshold"])
