# -*- coding: utf-8 -*-
"""TCO je Geraet: one model (optionally narrowed to storage and term) with its cost lines from the device ledger,
closed cycles in full, open cycles booked-to-date plus an expectation for the rest from the family-and-term mean.
Usage: python make_tco_page.py <REPO> <OUT> <TODAY>  ->  <OUT>/index.html"""
import json, sys, pathlib, math
from _roles import de_roles  # noqa: E402
from collections import defaultdict
import pandas as pd, duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
TERMS = [12, 24, 36, 48]
COST_LINES = ["purchase_price", "freight", "duty", "staging", "outbound_shipping", "support", "mdm_operations", "repair",
              "replacement_logistics", "return_logistics", "wipe_grading", "refurbishment", "holding_cost", "channel_fee"]
FAM_LABEL = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "tablet_like": "Tablet", "laptop_like": "Laptop"}


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def fl(v):
    """float as is: rounding happens only in the browser at display time (money / eur2), never in the payload"""
    v = clean(v)
    return None if v is None else float(v)


assumptions = yaml.safe_load(open(REPO / "config" / "assumptions.yaml", encoding="utf-8"))["blocks"]
vat = float(assumptions["vat_rate"]["value"])
min_n = int(assumptions["min_n_for_realised_inputs"]["value"])
holding_rate = float(assumptions["holding_cost_per_day_eur"]["value"])
support_rate = float(assumptions["support_cost_per_device_month_eur"]["value"])
mdm_rate = float(assumptions["mdm_cost_per_device_month_eur"]["value"])
alloc_owner = str(assumptions["support_cost_per_device_month_eur"]["owner"]).replace(" (name)", "")
channel_fees = {k: {"pct": float(v["fee_pct"]), "fixed": float(v["fee_fixed_eur"])} for k, v in assumptions["channel_fees"]["values"].items()}

models = pd.read_csv(REPO / "data" / "catalogue" / "models.csv", comment="#")
variants = pd.read_csv(REPO / "data" / "catalogue" / "variants.csv", comment="#")
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)

n_serials = con.execute("select count(*) from silver.device_ledger").fetchone()[0]
est_owner = dict(con.execute("select line_type, min(assumption_owner) from silver.ledger_lines where is_estimate group by 1").fetchall())
est_owner = {k: v.replace(" (name)", "") for k, v in est_owner.items()}
n_ppc_fleet = con.execute("select count(*) from silver.device_ledger where price_protection_credit_eur > 0").fetchone()[0]
slug_family = dict(con.execute("select distinct slug, model_family from silver.device_ledger where slug is not null").fetchall())
slug_fam_label_seen = dict(con.execute("select distinct slug, model_family from silver.device_ledger where slug is not null").fetchall())

# ---- family and term basis: per (fam, term) and (fam, all) the closed devices and their mean per line
fam_n = {}
for fam, term, n in con.execute("select model_family, term_months, count(*) from silver.device_ledger where is_closed and term_months is not null group by 1, 2").fetchall():
    fam_n[(fam, int(term))] = int(n)
for fam, n in con.execute("select model_family, count(*) from silver.device_ledger where is_closed and term_months is not null group by 1").fetchall():
    fam_n[(fam, "all")] = int(n)
fam_lines = defaultdict(lambda: defaultdict(float))
for fam, term, lt, s in con.execute("""select d.model_family, d.term_months, l.line_type, -sum(l.amount_eur)
  from silver.ledger_lines l join silver.device_ledger d using(serial)
  where d.is_closed and d.term_months is not null and l.line_class = 'cost' group by all""").fetchall():
    fam_lines[(fam, int(term))][lt] += float(s)
    fam_lines[(fam, "all")][lt] += float(s)
fam_fee = {}
for fam, term, fee, gross in con.execute("""select model_family, term_months, sum(channel_fee_eur), sum(resale_gross)
  from silver.device_ledger where is_closed and term_months is not null group by 1, 2""").fetchall():
    fam_fee[(fam, int(term))] = (float(fee or 0), float(gross or 0))
for fam, fee, gross in con.execute("""select model_family, sum(channel_fee_eur), sum(resale_gross)
  from silver.device_ledger where is_closed and term_months is not null group by 1""").fetchall():
    fam_fee[(fam, "all")] = (float(fee or 0), float(gross or 0))
fam_kpi = {}
for fam, term, n, p, c, tco_pm in con.execute("""select model_family, term_months, count(*), avg(purchase_price - coalesce(price_protection_credit_eur, 0)),
  avg(tco_eur - purchase_price), avg((tco_eur - coalesce(price_protection_credit_eur, 0)) / term_months)
  from silver.device_ledger where is_closed and term_months is not null group by 1, 2""").fetchall():
    fam_kpi[(fam, int(term))] = (int(n), float(p), float(c), float(tco_pm))
for fam, n, p, c, tco_pm in con.execute("""select model_family, count(*), avg(purchase_price - coalesce(price_protection_credit_eur, 0)),
  avg(tco_eur - purchase_price), avg((tco_eur - coalesce(price_protection_credit_eur, 0)) / term_months)
  from silver.device_ledger where is_closed and term_months is not null group by 1""").fetchall():
    fam_kpi[(fam, "all")] = (int(n), float(p), float(c), float(tco_pm))


def basis(fam, term):
    """mean per closed device of the family and term per line, with the fallback to all terms below min_n"""
    key = (fam, term) if fam_n.get((fam, term), 0) >= min_n else (fam, "all")
    n = fam_n.get(key, 0)
    B = {lt: (fam_lines[key][lt] / n if n else 0.0) for lt in COST_LINES}
    fee, gross = fam_fee.get(key, (0.0, 0.0))
    F = fee / gross if gross else 0.0
    return key, n, B, F


fam_term = {}
for fam in FAM_LABEL:
    fam_term[fam] = {}
    for term in TERMS + ["all"]:
        key = (fam, term)
        n = fam_n.get(key, 0)
        k = fam_kpi.get(key)
        fee, gross = fam_fee.get(key, (0.0, 0.0))
        fam_term[fam][str(term)] = {"n": n, "p": fl(k[1]) if k else None, "c": fl(k[2]) if k else None, "tco_pm": fl(k[3]) if k else None,
                                    "B": {lt: fam_lines[key][lt] / n if n else 0.0 for lt in COST_LINES}, "F": fee / gross if gross else 0.0}

# ---- closed devices per key
keys = {}


def kk(slug, storage, term):
    return f"{slug}|{int(storage)}|{int(term)}"


def new_key():
    return {"closed": {"n": 0, "p": 0.0, "c": 0.0, "est": 0.0, "tco_pm": 0.0, "mb": 0, "rate": 0.0, "term_sum": 0, "ppc_n": 0, "ppc_sum": 0.0, "n_lines": 0, "lines": {}},
            "open": {"n": 0, "p": 0.0, "c": 0.0, "tco_pm": 0.0, "mb": 0, "rate": 0.0, "mr": 0, "term_sum": 0, "ppc_n": 0, "ppc_sum": 0.0, "n_lines": 0, "lines": {}, "exp": {}, "erc": 0.0, "fee_exp": 0.0, "fb": 0, "fee_open": 0,
                     "basis": {}, "fbt": {}},
            "spec": ""}


closed = con.execute("""select slug, storage_gb, variant_spec, term_months, count(*) n,
  sum(purchase_price - coalesce(price_protection_credit_eur, 0)) p, sum(tco_eur - purchase_price) c,
  sum(tco_eur - tco_transactional_eur) est, sum((tco_eur - coalesce(price_protection_credit_eur, 0)) / term_months) tco_pm,
  sum(months_billed) mb, sum(monthly_rate) rate, sum(term_months) term_sum,
  count(*) filter (price_protection_credit_eur > 0) ppc_n, sum(coalesce(price_protection_credit_eur, 0)) ppc_sum, sum(n_lines) n_lines
  from silver.device_ledger where is_closed and term_months is not null group by all""").df()
for _, r in closed.iterrows():
    k = keys.setdefault(kk(r["slug"], r["storage_gb"], r["term_months"]), new_key())
    k["spec"] = r["variant_spec"]
    k["closed"].update({"n": int(r["n"]), "p": fl(r["p"]), "c": fl(r["c"]), "est": fl(r["est"]), "tco_pm": fl(r["tco_pm"]), "mb": int(r["mb"] or 0), "rate": fl(r["rate"]),
                        "term_sum": int(r["term_sum"]), "ppc_n": int(r["ppc_n"]), "ppc_sum": fl(r["ppc_sum"])})
closed_lines = con.execute("""select d.slug, d.storage_gb, d.term_months, l.line_type, -sum(l.amount_eur) s, count(distinct l.serial) dev,
  sum(case when l.is_estimate then -l.amount_eur else 0 end) est, count(distinct case when l.is_estimate then l.serial end) est_dev, count(*) cnt
  from silver.ledger_lines l join silver.device_ledger d using(serial)
  where d.is_closed and d.term_months is not null and l.line_class = 'cost' group by all""").df()
# line_class = 'cost' and not amount_eur < 0: a booked cost line with amount zero (channel fee of an employee buyout at
# zero percent plus zero) is still a line the device had, so it counts in "QTY Geraete mit Zeile" and in the cost-line count
for _, r in closed_lines.iterrows():
    k = keys[kk(r["slug"], r["storage_gb"], r["term_months"])]["closed"]
    k["lines"][r["line_type"]] = [fl(r["s"]), int(r["dev"]), fl(r["est"]), int(r["est_dev"])]
    k["n_lines"] += int(r["cnt"])

# ---- open devices (with a contract): booked to date, plus the expectation per device
open_dev = con.execute("""select serial, slug, storage_gb, variant_spec, model_family, term_months, months_billed, months_remaining, monthly_rate,
  purchase_price, coalesce(price_protection_credit_eur, 0) ppc, tco_eur, estimate_rv_lease_end, resale_channel, expected_remaining_cost, n_lines
  from silver.device_ledger where not is_closed and term_months is not null""").df()
open_lines = defaultdict(dict)
open_cnt = defaultdict(int)  # cost lines per open serial, same definition as the closed count (line_class = 'cost')
for serial, lt, s, cnt in con.execute("""select l.serial, l.line_type, -sum(l.amount_eur), count(*)
  from silver.ledger_lines l join silver.device_ledger d using(serial)
  where not d.is_closed and d.term_months is not null and l.line_class = 'cost' group by all""").fetchall():
    open_lines[serial][lt] = float(s)
    open_cnt[serial] += int(cnt)
mdm_enrolled = {s for (s,) in con.execute("select distinct serial from bronze.wms_staging_log where mdm_enrolled").fetchall()}
for _, r in open_dev.iterrows():
    key = kk(r["slug"], r["storage_gb"], r["term_months"])
    k = keys.setdefault(key, new_key())
    k["spec"] = r["variant_spec"]
    o = k["open"]
    booked = open_lines.get(r["serial"], {})
    term = int(r["term_months"]); mr = int(r["months_remaining"] or 0)
    bkey, nb, B, F = basis(r["model_family"], term)
    exp = {}
    for lt in COST_LINES:
        if lt in ("repair", "replacement_logistics"):
            exp[lt] = B[lt] * mr / term
        elif lt in ("return_logistics", "wipe_grading", "refurbishment"):
            exp[lt] = 0.0 if lt in booked else B[lt]
        elif lt == "holding_cost":
            exp[lt] = max(0.0, B[lt] - booked.get(lt, 0.0))
        elif lt == "support":
            exp[lt] = support_rate * mr  # one allocation per remaining rented month, same rate the ledger books
        elif lt == "mdm_operations":
            exp[lt] = (mdm_rate * mr) if r["serial"] in mdm_enrolled else 0.0
        elif lt == "channel_fee":
            exp[lt] = 0.0 if lt in booked else F * float(clean(r["estimate_rv_lease_end"]) or 0.0)
        else:
            exp[lt] = 0.0
    p = float(r["purchase_price"]) - float(r["ppc"])
    c_sofar = float(r["tco_eur"]) - float(r["purchase_price"])
    exp_total = sum(exp.values())
    ch = r["resale_channel"] if isinstance(r["resale_channel"], str) and r["resale_channel"] in channel_fees else "marketplace"
    fee_exp = float(clean(r["estimate_rv_lease_end"]) or 0.0) * channel_fees[ch]["pct"] + channel_fees[ch]["fixed"]
    o["n"] += 1; o["p"] += p; o["c"] += c_sofar; o["tco_pm"] += (p + c_sofar + exp_total) / term
    o["mb"] += int(r["months_billed"] or 0); o["mr"] += mr; o["rate"] += float(r["monthly_rate"]); o["term_sum"] += term
    o["ppc_n"] += 1 if float(r["ppc"]) > 0 else 0; o["ppc_sum"] += float(r["ppc"]); o["n_lines"] += open_cnt.get(r["serial"], 0)
    o["erc"] += float(clean(r["expected_remaining_cost"]) or 0.0); o["fee_exp"] += fee_exp
    o["fb"] += 1 if bkey[1] == "all" else 0
    o["basis"][str(bkey[1])] = o["basis"].get(str(bkey[1]), 0) + 1  # which family-and-term group each open device actually used
    if bkey[1] == "all":
        o["fbt"][str(term)] = o["fbt"].get(str(term), 0) + 1  # the device's own term, when that term fell back to all terms
    o["fee_open"] += 1 if "channel_fee" not in booked else 0
    for lt, s in booked.items():
        cur = o["lines"].setdefault(lt, [0.0, 0])
        cur[0] += s; cur[1] += 1
    for lt, e in exp.items():
        if e:
            o["exp"][lt] = o["exp"].get(lt, 0.0) + e
# no rounding of the open aggregates here: the browser adds keys across storage and term first and rounds only when it prints

not_deployed = dict(con.execute("select slug, count(*) from silver.device_ledger where term_months is null group by 1").fetchall())
not_deployed_st = defaultdict(dict)  # devices without a contract per slug and storage, so the storage list of the fleet tile adds up
for slug, st, n in con.execute("select slug, storage_gb, count(*) from silver.device_ledger where term_months is null group by 1, 2").fetchall():
    not_deployed_st[slug][str(int(st))] = int(n)
purchase_span = {s: [str(a), str(b)] for s, a, b in con.execute("select slug, min(purchase_date), max(purchase_date) from silver.device_ledger group by 1").fetchall()}

# ---- catalogue
mrows = {r["slug"]: r for _, r in models.iterrows()}


def fam_key(slug, m):
    if slug in slug_family:
        return slug_family[slug]
    if m["oem"] == "Apple" and m["family"] == "Smartphone":
        return "iphone_like"
    return {"Smartphone": "android_like", "Tablet": "tablet_like", "Laptop": "laptop_like"}.get(m["family"], "android_like")


fleet_specs = defaultdict(dict)
for key, k in keys.items():
    slug, st, _ = key.split("|")
    fleet_specs[slug][int(st)] = k["spec"]
for slug, st, spec in con.execute("select distinct slug, storage_gb, variant_spec from silver.device_ledger where term_months is null").fetchall():
    fleet_specs[slug].setdefault(int(st), spec)  # a storage held only by devices without a contract still belongs to the model
model_list = []
for _, m in models.iterrows():
    slug = m["slug"]
    specs = {}
    for _, v in variants[variants["slug"] == slug].iterrows():
        if clean(v["storage_gb"]) is not None and int(v["storage_gb"]) not in specs:
            specs[int(v["storage_gb"])] = v["spec"]
    for st, sp in fleet_specs.get(slug, {}).items():
        specs.setdefault(st, sp)
    model_list.append({"slug": slug, "name": m["model_name"], "oem": m["oem"], "family": m["family"], "fam": fam_key(slug, m),
                       "in_fleet": slug in fleet_specs or slug in not_deployed,
                       "specs": [{"gb": st, "label": specs[st]} for st in sorted(specs)]})
model_list.sort(key=lambda d: (d["family"], d["oem"], d["name"]))
in_fleet = sum(1 for d in model_list if d["in_fleet"])
# counted, not typed: models with at least one variant carrying a launch RRP, and models whose launch date is a sales start
# (launch_date_kind = verfuegbarkeit), not only an announcement and not missing
n_rrp = int(variants[variants["rrp_eur_launch_de"].notna()]["slug"].nunique())
n_launch = int((models["launch_date_de"].notna() & (models["launch_date_kind"] == "verfuegbarkeit")).sum())
thinkpads = sorted(((sum(k["closed"]["n"] for key, k in keys.items() if key.split("|")[0] == s), s) for s in fleet_specs if "thinkpad" in s), reverse=True)
quick = ["iphone-16-pro-max", "iphone-15", "samsung-galaxy-s24"] + ([thinkpads[0][1]] if thinkpads else [])
quick = [s for s in quick if s in mrows]

# ---- deposited definition (texts as on the cycle tab, twelve rows) and the data channel per line
defs = [
    {"lt": "purchase_price", "name": "Einkaufspreis", "phase": "Anschaffung", "src": "Lieferantenrechnung (ERP), Stückposition je Seriennummer", "booked": "Rechnung", "est": "bis die Rechnung da ist: Bestellpreis, markiert", "owner": "Head of Procurement", "channel": "ERP Lieferantenrechnungen"},
    {"lt": "freight", "name": "Fracht vom Lieferanten", "phase": "Anschaffung", "src": "Lieferantenrechnung, Positionen der Bestellzeile, centgenau auf die gelieferten Seriennummern verteilt", "booked": "Rechnung", "est": "nein", "owner": "", "channel": "ERP Lieferantenrechnungen"},
    {"lt": "duty", "name": "Zoll", "phase": "Anschaffung", "src": "Lieferantenrechnung, Positionen der Bestellzeile, centgenau auf die gelieferten Seriennummern verteilt", "booked": "Rechnung", "est": "nein", "owner": "", "channel": "ERP Lieferantenrechnungen"},
    {"lt": "staging", "name": "Einrichtung vor Versand", "phase": "Bereitstellung", "src": "Einrichtungsprotokoll (Lager)", "booked": "Einrichtung", "est": "nein", "owner": "", "channel": "Lager Einrichtungsprotokoll"},
    {"lt": "outbound_shipping", "name": "Versand zum Kunden", "phase": "Bereitstellung", "src": "Versandprotokoll (Lager), Richtung Kunde", "booked": "Versand", "est": "nein", "owner": "", "channel": "Lager Versandprotokoll"},
    {"lt": "support", "name": "Nutzerbetreuung je Gerätemonat (Umlage, geschätzt)", "phase": "Service", "src": "je Mietrechnung des Geräts ein Satz je Gerätemonat: First-Level-Support, Störungsbearbeitung, Austauschkoordination; Teamkosten, keine Buchung je Seriennummer", "booked": "Rechnungsdatum der Mietrechnung", "est": "ja, immer", "owner": "Head of Service Operations", "channel": "Schätzung: Mietrechnungen mal Satz je Gerätemonat"},
    {"lt": "mdm_operations", "name": "Geräteverwaltung MDM je Gerätemonat (Umlage, geschätzt)", "phase": "Service", "src": "je Mietrechnung ein Satz je Gerätemonat, nur für Geräte, die das Einrichtungsprotokoll als MDM-registriert führt; die MDM-Lizenz selbst bleibt beim Kunden", "booked": "Rechnungsdatum der Mietrechnung", "est": "ja, immer", "owner": "Head of Service Operations", "channel": "Schätzung: Mietrechnungen mal Satz je Gerätemonat, Einrichtungsprotokoll (MDM-Flag)"},
    {"lt": "repair", "name": "Reparatur", "phase": "Service", "src": "Servicefall mit Lösung Reparatur und Kosten", "booked": "Schließen des Servicefalls", "est": "nein", "owner": "", "channel": "Servicedesk"},
    {"lt": "replacement_logistics", "name": "Austauschversand", "phase": "Service", "src": "Versandprotokoll, Richtung Austausch, gebucht auf das defekte Gerät", "booked": "Versand", "est": "nein", "owner": "", "channel": "Lager Versandprotokoll"},
    {"lt": "return_logistics", "name": "Rücksendung vom Kunden", "phase": "Rückgabe", "src": "Versandprotokoll, Richtung Rücksendung", "booked": "Versand", "est": "nein", "owner": "", "channel": "Lager Versandprotokoll"},
    {"lt": "wipe_grading", "name": "Datenlöschung und Zustandsprüfung", "phase": "Rückgabe", "src": "Rückläufer-Beleg", "booked": "Wareneingang der Rückgabe", "est": "nein", "owner": "", "channel": "Rücknahme"},
    {"lt": "refurbishment", "name": "Aufbereitung", "phase": "Wiederverkauf", "src": "Aufbereitungsauftrag", "booked": "Fertigstellung", "est": "nein", "owner": "", "channel": "Aufbereiter"},
    {"lt": "holding_cost", "name": "Lagertage (Lagerkosten je Tag, geschätzt)", "phase": "Kapital und Lager", "src": "Tage je Lagerphase (Eingang, Rückgabe, Verkauf) mal Lagerkosten je Tag", "booked": "Ende der Lagerphase", "est": "ja, immer", "owner": "CFO", "channel": "Schätzung: Tage mal Lagerkosten je Tag"},
    {"lt": "channel_fee", "name": "Kanalgebühren", "phase": "Wiederverkauf", "src": "Gutschrift des Verkaufsauftrags (Prozent plus Fixbetrag)", "booked": "Gutschrift des Verkaufs", "est": "bis die Gutschrift da ist: angenommener Satz, markiert", "owner": "Head of Recommerce", "channel": "Recommerce Gutschriften"},
]

data = {"today": TODAY, "vat": vat, "min_n": min_n, "holding_rate": holding_rate, "support_rate": support_rate, "mdm_rate": mdm_rate, "alloc_owner": alloc_owner,
        "n_mdm_enrolled": int(len(mdm_enrolled)), "channel_fees": channel_fees, "fam_label": FAM_LABEL,
        "models_total": int(len(models)), "models_in_fleet": in_fleet, "n_rrp": n_rrp, "n_launch": n_launch, "n_serials": int(n_serials), "n_ppc_fleet": int(n_ppc_fleet),
        "est_owner": est_owner, "models": model_list, "keys": keys, "not_deployed": not_deployed, "not_deployed_st": not_deployed_st, "purchase_span": purchase_span,
        "fam_term": fam_term, "defs": defs, "quick": quick, "terms": TERMS, "lines": COST_LINES}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/tco.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(de_roles(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":"))) + "\n")
print(OUT.name + ": geschrieben")
