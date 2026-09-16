# -*- coding: utf-8 -*-
"""Build the Geraetekreislauf page (synthetic simulation) from the v0.2 gold and silver tables."""
import json, sys, pathlib
from _roles import de_roles  # noqa: E402
import duckdb, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)

def rows(sql):
    df = con.execute(sql).df()
    return json.loads(df.to_json(orient="records"))

closed = con.execute("""select count(*) n, sum(case when lifecycle_result_eur>0 then 1 else 0 end) profit,
  round(avg(lifecycle_result_eur)) mean_eur, round(sum(lifecycle_result_eur)) sum_eur,
  round(avg(purchase_price)) purchase, round(avg(tco_eur - purchase_price)) cost_to_sale,
  round(avg(rental_revenue)) rent, round(avg(realised_rv)) rv
  from silver.device_ledger where is_closed""").df().iloc[0].to_dict()
cred = con.execute("select sum(case when price_protection_credit_eur>0 then 1 else 0 end) n, round(avg(price_protection_credit_eur),2) mean from silver.device_ledger where is_closed").df().iloc[0].to_dict()
closed["cred_n"] = int(cred["n"] or 0); closed["cred_mean"] = float(cred["mean"] or 0.0)
open_ = con.execute("select count(*) n, round(sum(result_if_liquidated_today)) liq, round(sum(result_projected_at_lease_end)) proj from silver.device_ledger where not is_closed").df().iloc[0].to_dict()

SEL = """count(*) n_closed, round(avg(purchase_price)) purchase, round(avg(tco_eur - purchase_price)) cost_to_sale,
      round(avg(rental_revenue)) rent, round(avg(realised_rv)) rv, round(avg(lifecycle_result_eur)) mean_eur,
      round(100*sum(lifecycle_result_eur)/sum(purchase_price),1) pct_purchase"""
fam = rows(f'select catalogue_family as "family", {SEL} from silver.device_ledger where is_closed group by 1 order by mean_eur desc')
fam_oem = rows(f'select catalogue_family as "family", oem, {SEL} from silver.device_ledger where is_closed group by 1,2 order by 1, mean_eur desc')
def simple(col, label):
    return {"label": label, "rows": rows(f"select cast({col} as varchar) as cohort_value, {SEL} from silver.device_ledger where is_closed and {col} is not null group by 1 order by mean_eur desc")}
others = [simple("term_months", "Nach Laufzeit"), simple("resale_channel", "Nach Verkaufskanal"), simple("supplier_role", "Nach Bezugsweg")]
levers = rows("select lever_id, lever_name, component, n_attributed, round(eur_per_device,1) eur_per_device, round(eur_fleet_per_year) eur_fleet_per_year, threshold_key, threshold_owner, rule_id from gold.levers_summary order by eur_fleet_per_year desc")
tco = rows("""select line_type, round(-sum(amount_eur)) eur, count(*) n_lines, count(distinct serial) n_devices,
  round(-sum(amount_eur)/(select count(*) from silver.device_ledger where is_closed), 2) eur_per_closed,
  round(-sum(amount_eur)/count(distinct serial), 2) eur_per_affected
  from silver.ledger_lines where serial in (select serial from silver.device_ledger where is_closed) and amount_eur<0
  group by line_type order by eur desc""")
tco = [t for t in tco if t["line_type"] != "purchase_price"]
tco_total = {"eur": sum(t["eur"] for t in tco), "n_lines": sum(t["n_lines"] for t in tco), "n_devices": int(closed["n"]), "eur_per_closed": sum(t["eur_per_closed"] for t in tco)}
chain = float(con.execute("select round(100.0*sum(case when chain_complete then 1 else 0 end)/count(*),1) from silver.device_ledger").fetchone()[0])
ingest = con.execute("select sum(rows_read) rows_read, sum(n_unresolved) unresolved, sum(n_files) files from gold.ingest_summary").df().iloc[0].to_dict()
data = {"today": TODAY, "closed": closed, "open": open_, "fam": fam, "fam_oem": fam_oem, "others": others, "levers": levers, "tco": tco, "tco_total": tco_total, "chain_pct": chain, "ingest": ingest}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/cycle.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(de_roles(json.dumps(data, ensure_ascii=False, default=float, separators=(",", ":"))) + "\n")
print(OUT.name + ": geschrieben")
