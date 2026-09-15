# -*- coding: utf-8 -*-
"""Bericht: Momentaufnahme fuer die Geschaeftsfuehrung aus CFO-Sicht, zwoelf Monate bis zum Stichtag.
Jede Zahl kommt aus der DuckDB (read_only) oder aus config/*.yaml; gerundet wird erst im Browser.
Usage: python make_report_page.py <REPO> <OUT> <TODAY>  ->  <OUT>/index.html"""
import json, sys, pathlib, math, calendar
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
import duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]

# every element id the browser script addresses; the combined page prefixes them with "report-"
IDS = ["data", "today", "h1", "banner", "tiles", "tilenote", "pnl-h2", "pnl", "pnlnote", "kpi-h2", "kpi", "kpinote",
       "rental-h2", "rental-head", "rental-q", "rental-top", "rental-note", "supplier-head", "supplier-top", "supplier-note",
       "impact-h2", "impact", "impactnote", "status-h2", "status", "statusnote"]

FAM_DE = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "tablet_like": "Tablet", "laptop_like": "Laptop"}
CAT_DE = {"hardware": "Hardware", "logistics": "Logistik", "repair": "Reparatur", "refurbishment": "Aufbereitung", "software": "Software",
          "marketing": "Marketing", "facilities": "Gebäude und Betrieb", "consulting": "Beratung", "packaging": "Verpackung",
          "connectivity": "Mobilfunk und Konnektivität", "resale_channel": "Verkaufskanal", "financing": "Finanzierung",
          "security_software": "Sicherheitssoftware", "rental": "Miete"}
ROLE_ONLY_SUFFIX = " (role-only)"  # restwert/lake/common.py
ROLE_DE = {"Logistics partner": "Logistikpartner", "Refurbishment and repair partner": "Aufbereitungs- und Reparaturpartner",
           "Software vendor": "Softwareanbieter", "Marketing agency": "Marketingagentur", "Facilities provider": "Gebäudedienstleister",
           "Consulting firm": "Beratungsfirma", "Packaging supplier": "Verpackungslieferant", "IT reseller A": "IT-Zwischenhändler A",
           "IT reseller B": "IT-Zwischenhändler B", "Carrier partner": "Mobilfunkpartner", "Marketplace channel A": "Marktplatz A",
           # two role names the register carries beyond the brief's list (found in main.contracts_register)
           "Rugged-device OEM": "Hersteller robuster Geräte", "Mobile threat defense partner": "Partner für mobile Bedrohungsabwehr"}
# the seven Stellschrauben of the glossary, keyed by gold.levers_summary.lever_name
LEVER_DE = {"purchase_discount": "Einkaufsrabatt", "price_protection": "Preisschutz", "channel_choice": "Kanalwahl",
            "grade_and_repair": "Zustand und Reparatur", "aging": "Liegetage", "term_length": "Laufzeit", "manufacturer_mix": "Herstellermix"}
RULE_DE = {"aging_write_down": "Wertberichtigung bei zu langer Lagerdauer"}  # restwert/decisions/registry.py rule names
# indirect categories that the device ledger books per device as well (config/lake.yaml indirect_categories against ledger line_type)
DOUBLE_CATS = ("repair", "refurbishment")
KPI_ROWS = [  # (group, kpi_id, German name); ten of the fourteen gold KPIs, in page order
    ("Daten", "KPI_DATA_CHAIN_COMPLETE", "Datumskette vollständig"),
    ("Einkauf", "KPI_PUR_DISCOUNT_VS_RRP", "Einkaufsrabatt gegen UVP ohne Mehrwertsteuer"),
    ("Einkauf", "KPI_PUR_PRICE_PROTECTION_CAPTURE", "Preisschutz-Gutschriften erfasst"),
    ("Kosten", "KPI_TCO_PER_CLOSED_DEVICE", "TCO je abgeschlossenem Gerät (Tab TCO)"),
    ("Kosten", "KPI_TCO_ESTIMATE_SHARE", "Anteil geschätzter Kostenzeilen am TCO"),
    ("Recommerce", "KPI_RSL_REALISED_VS_RECORD", "Restwert gegen Restwertprognose bei Rückgabe"),
    ("Recommerce", "KPI_RSL_DAYS_RETURN_TO_CASH", "Tage von Rückgabe bis Zahlungseingang, mittleres Gerät"),
    ("Ergebnis", "KPI_RSLT_CLOSED_PER_DEVICE", "Lifecycle-Marge je Gerät, abgeschlossene Kreisläufe"),
    ("Ergebnis", "KPI_LEV_ADDITIVE_EUR_PA", "Geld, das noch liegt, je Jahr (Tab Stellschrauben)"),
    ("Verträge", "KPI_CTR_COVERAGE_BY_OEM", "Hardware-Einkauf unter laufendem Vertrag"),
]
WARN = []


def warn(msg):
    WARN.append(msg); print("WARNING", msg)


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def fl(v):
    """float as is: rounding happens only in the browser at display time, never in the payload"""
    v = clean(v)
    return None if v is None else float(v)


def iv(v):
    v = clean(v)
    return None if v is None else int(v)


def dec(v):
    v = clean(v)
    return Decimal(0) if v is None else Decimal(str(v))


OWNER_DE = {"Head of Procurement": "Einkaufsleitung", "Head of Recommerce": "Leitung Recommerce", "Head of Service Operations": "Leitung Service",
            "Head of Customer Success": "Leitung Customer Success", "Head of Indirect Procurement": "Leitung Indirekter Einkauf", "Data owner": "Dateneigner",
            "CFO": "CFO", "Category Manager Hardware": "Category Manager Hardware"}


def own(s):
    if s is None:
        return None
    t = str(s).replace(" (name)", "")
    return OWNER_DE.get(t, t)


def role_de(name):
    if name is None:
        return ""
    name = str(name)
    if not name.endswith(ROLE_ONLY_SUFFIX):
        return name  # a manufacturer with its public name stays as it is
    prefix = name[: -len(ROLE_ONLY_SUFFIX)]
    if prefix not in ROLE_DE:
        warn(f"unbekannte Rolle im Vertragsregister, englisch gelassen: {prefix}")
    return ROLE_DE.get(prefix, prefix) + " (nur Rolle)"


def cat_de(code):
    code = str(code)
    if code not in CAT_DE:
        warn(f"unbekannte Vertragskategorie, Codewert gelassen: {code}")
    return CAT_DE.get(code, code)


def lever_de(name):
    name = str(name)
    if name not in LEVER_DE:
        warn(f"unbekannte Stellschraube in gold.levers_summary, Codewert gelassen: {name}")
    return LEVER_DE.get(name, name)


def rule_de(name):
    if name is None:
        warn("Regel der Wertberichtigung ohne Namen in main.decision_queue")
        return "ohne Namen"
    name = str(name)
    if name not in RULE_DE:
        warn(f"unbekannte Regel im Wertberichtigungsbuch, Codewert gelassen: {name}")
    return RULE_DE.get(name, name)


def add_months(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    y += d.year; m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_between(a, b):
    """month boundaries crossed, the same count as DuckDB datediff('month', a, b)"""
    return (b.year - a.year) * 12 + (b.month - a.month)


# ---- Stichtag und Fenster
lake = yaml.safe_load(open(REPO / "config" / "lake.yaml", encoding="utf-8"))
AS_OF = lake["as_of"]
AS_OF = AS_OF if isinstance(AS_OF, date) else date.fromisoformat(str(AS_OF))
targets_cfg = yaml.safe_load(open(REPO / "config" / "kpi_targets.yaml", encoding="utf-8"))
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
AS = str(AS_OF)
kpi_as_of = con.execute("select max(as_of) from gold.kpi_values").fetchone()[0]
if kpi_as_of != AS_OF:
    warn(f"gold.kpi_values steht auf {kpi_as_of}, config/lake.yaml as_of auf {AS_OF}")
WS = con.execute("select (?::date - interval 12 month)::date", [AS]).fetchone()[0]       # window start (exclusive)
WS2 = con.execute("select (?::date - interval 12 month)::date", [str(WS)]).fetchone()[0]  # prior window start (exclusive)
FW_END = con.execute("select (?::date + interval 12 month)::date", [AS]).fetchone()[0]   # forward window end (inclusive)
W = [str(WS), AS]  # event_date > WS and event_date <= AS
PW = [str(WS2), str(WS)]  # prior period: event_date > WS2 and event_date <= WS
F = [AS, str(FW_END)]  # end_date > AS and end_date <= FW_END
n_serials = iv(con.execute("select count(*) from silver.device_ledger").fetchone()[0])

# Waechter: die Abschreibung laeuft je Seriennummer ab Einkaufspreis; ein zweiter Vertrag je Geraet (Wiedervermietung) muesste ab
# Restbuchwert bei Vertragsbeginn abschreiben, und das ist nicht gebaut.
n_rc, n_rc_serials = con.execute("select count(*), count(distinct serial) from main.rental_contracts").fetchone()
if int(n_rc) != int(n_rc_serials):
    raise SystemExit(f"ABBRUCH: Folgeverträge: {n_rc} Verträge auf {n_rc_serials} Seriennummern; Abschreibung ab Restbuchwert bei Vertragsbeginn ist nicht gebaut")

# ---- eine Monatsregel fuer Abschreibung, Restbuchwert der Abgaenge und Restbuchwert der Flotte:
# Laufzeit lt = Monatsgrenzen zwischen Vertragsbeginn und vertraglichem Ende (bei Ersatzvertraegen kuerzer als term_months, weil das
# Ersatzgeraet bis zum Ende des ersetzten Vertrags laeuft), mindestens 1; je Vertragsmonat k (Monatsbeginn = start_date + k Monate)
# wird (Einkaufspreis minus Restwertprognose) / lt gebucht, wenn der Monatsbeginn vor dem wirksamen Ende und nicht nach dem Stichtag liegt.
RV_RECORD = """select serial, forecast_rv from main.rv_forecast_of_record where not is_missing
  qualify row_number() over (partition by serial order by run_as_of desc) = 1"""
DEP_CTE = f"""rv as ({RV_RECORD}),
c as (
  select r.serial, r.start_date, greatest(datediff('month', r.start_date, r.end_date), 1) lt, coalesce(r.actual_end_date, r.end_date) eff_end,
    (r.replaces_contract_id is not null) as is_repl, d.purchase_price,
    case when f.forecast_rv is not null then 'record' when d.estimate_rv_lease_end is not null then 'lease_end' else 'none' end rv_src,
    coalesce(f.forecast_rv, d.estimate_rv_lease_end, 0) rv_end
  from main.rental_contracts r join silver.device_ledger d using(serial) left join rv f using(serial)),
m as (select c.*, unnest(generate_series(0, c.lt - 1)) k from c),
mm as (select *, (start_date + to_months(k))::date month_start, greatest(purchase_price - rv_end, 0) / lt dep_month from m),
acc as (select serial, coalesce(sum(dep_month) filter (month_start < eff_end and month_start <= ?::date), 0) acc_dep,
               count(*) filter (month_start < eff_end and month_start <= ?::date) n_booked from mm group by serial)"""
ACC_P = [AS, AS]

# ---- Gewinn und Verlust, Zeile fuer Zeile (Erloese positiv, Aufwand negativ, genau wie sie in die Summen eingehen)
z1_sum, z1_n, z1_serials = con.execute("""select coalesce(sum(amount_eur), 0), count(*), count(distinct serial) from silver.ledger_lines
  where line_type = 'rental_revenue' and event_date > ? and event_date <= ?""", W).fetchone()
n_active, rate_active = con.execute("select count(*), coalesce(sum(monthly_rate), 0) from main.rental_contracts where status = 'active'").fetchone()
z2_sum, z2_n = con.execute("""select coalesce(sum(amount_eur), 0), count(*) from silver.ledger_lines
  where line_type = 'resale_gross' and event_date > ? and event_date <= ?""", W).fetchone()
fees = con.execute("""select coalesce(-sum(amount_eur), 0) from silver.ledger_lines
  where line_type = 'channel_fee' and event_date > ? and event_date <= ?""", W).fetchone()[0]
n_sold_dev = con.execute("select count(*) from silver.device_ledger where sale_date > ? and sale_date <= ?", W).fetchone()[0]
if int(n_sold_dev) != int(z2_n):
    warn(f"QTY resale_gross-Zeilen im Fenster {z2_n} ungleich QTY Geräte mit sale_date im Fenster {n_sold_dev}")
n_scrapped = con.execute("""select count(*) from silver.device_ledger
  where lifecycle_status = 'scrapped' and closed_date > ? and closed_date <= ?""", W).fetchone()[0]

z3_n, z3_bv = con.execute(f"""
with {DEP_CTE},
ab as (
  select d.serial, d.purchase_price, coalesce(d.sale_date, d.closed_date) off_date
  from silver.device_ledger d
  where (d.sale_date > ? and d.sale_date <= ?) or (d.lifecycle_status = 'scrapped' and d.closed_date > ? and d.closed_date <= ?)),
bv as (
  select ab.*, coalesce(a.acc_dep, 0) acc_dep,
    coalesce((select sum(w.amount) from main.write_down_ledger w where w.serial = ab.serial and w.as_of <= ab.off_date), 0) wd
  from ab left join acc a using(serial))
select count(*), coalesce(sum(greatest(purchase_price - acc_dep - wd, 0)), 0) from bv""", ACC_P + W + W).fetchone()
z4_sum, z4_n = con.execute("""select coalesce(sum(amount_eur), 0), count(*) from silver.ledger_lines
  where line_type = 'price_protection_credit' and event_date > ? and event_date <= ?""", W).fetchone()
z5_sum, z5_n, z5_types, z5_est, z5_est_hold, z5_est_alloc, z5_est_fee = con.execute("""select coalesce(sum(amount_eur), 0), count(*), count(distinct line_type),
  coalesce(-sum(case when is_estimate then amount_eur else 0 end), 0),
  coalesce(-sum(case when is_estimate and line_type = 'holding_cost' then amount_eur else 0 end), 0),
  coalesce(-sum(case when is_estimate and line_type in ('support', 'mdm_operations') then amount_eur else 0 end), 0),
  coalesce(-sum(case when is_estimate and line_type not in ('holding_cost', 'support', 'mdm_operations') then amount_eur else 0 end), 0)
  from silver.ledger_lines where line_class = 'cost' and line_type <> 'purchase_price' and event_date > ? and event_date <= ?""", W).fetchone()
z5_dbl = {t: fl(s) for t, s in con.execute("""select line_type, coalesce(-sum(amount_eur), 0) from silver.ledger_lines
  where line_class = 'cost' and line_type in (?, ?) and event_date > ? and event_date <= ? group by 1""", list(DOUBLE_CATS) + W).fetchall()}
# Z6 ohne die beiden Kategorien, die je Geraet schon in Z5 stehen (Reparatur, Aufbereitung); sonst zaehlt die EBITDA-Naeherung
# sie doppelt. Die ausgeschlossene Summe wird beziffert und steht in der Zeile und in der Legende (Entscheidung 14.09.2026).
z6_sum, z6_n, z6_cat = con.execute("""select coalesce(-sum(amount), 0), count(*), count(distinct category) from main.indirect_spend
  where category not in (?, ?) and invoice_date > ? and invoice_date <= ?""", list(DOUBLE_CATS) + W).fetchone()
z6_cats = [cat_de(c) for (c,) in con.execute("select distinct category from main.indirect_spend where category not in (?, ?) and invoice_date > ? and invoice_date <= ? order by 1", list(DOUBLE_CATS) + W).fetchall()]
z6_dbl = {c: fl(s) for c, s in con.execute("""select category, coalesce(sum(amount), 0) from main.indirect_spend
  where category in (?, ?) and invoice_date > ? and invoice_date <= ? group by 1""", list(DOUBLE_CATS) + W).fetchall()}
z6_dbl_n, z6_dbl_sum = con.execute("""select count(*), coalesce(sum(amount), 0) from main.indirect_spend
  where category in (?, ?) and invoice_date > ? and invoice_date <= ?""", list(DOUBLE_CATS) + W).fetchone()
z7_sum, z7_n, z7_months, z7_rec, z7_lease, z7_none, z7_repl = con.execute(f"""
with {DEP_CTE}
select coalesce(-sum(dep_month), 0), count(distinct serial), count(*),
  count(distinct case when rv_src = 'record' then serial end), count(distinct case when rv_src = 'lease_end' then serial end), count(distinct case when rv_src = 'none' then serial end),
  count(distinct case when is_repl then serial end)
from mm where month_start > ? and month_start <= ? and month_start < eff_end""", ACC_P + W).fetchone()
z8_sum, z8_n, z8_owner = con.execute("""select coalesce(-sum(amount), 0), count(*), min(threshold_owner) from main.write_down_ledger
  where as_of > ? and as_of <= ?""", W).fetchone()
z8_rules = con.execute("""select distinct w.rule_id, q.rule_name from main.write_down_ledger w
  left join (select distinct rule_id, rule_name from main.decision_queue) q using(rule_id) where w.as_of > ? and w.as_of <= ? order by 1""", W).fetchall()
z8_rules_text = ", ".join(f"{rid} ({rule_de(name)})" for rid, name in z8_rules)
n_pp, sum_pp = con.execute("""select count(*), coalesce(-sum(amount_eur), 0) from silver.ledger_lines
  where line_type = 'purchase_price' and event_date > ? and event_date <= ?""", W).fetchone()

# z3_bv is a positive book value and gets its minus here; z5, z6, z7, z8 already come negative out of their queries
Z1, Z2, Z3, Z4, Z5, Z6, Z7, Z8 = dec(z1_sum), dec(z2_sum), -dec(z3_bv), dec(z4_sum), dec(z5_sum), dec(z6_sum), dec(z7_sum), dec(z8_sum)
S0 = Z1 + Z2 + Z3 + Z4 + Z5      # Geraetegeschaeft vor indirekten Ausgaben
S1 = S0 + Z6                     # EBITDA-Naeherung
S2 = S1 + Z7 + Z8                # EBIT-Naeherung
# Nachrechnung auf zwei anderen Wegen: das Hauptbuch in einer Abfrage (alle Zeilen ausser Einkaufspreis) minus indirekte Ausgaben,
# und die Abschreibung je Vertragsmonat in Python statt in SQL. Weicht eine Summe um einen Cent ab, bricht der Generator ab.
ledger_all, indirect_all = con.execute("""select
  (select coalesce(sum(amount_eur), 0) from silver.ledger_lines where line_type <> 'purchase_price' and event_date > ? and event_date <= ?),
  (select coalesce(sum(amount), 0) from main.indirect_spend where category not in (?, ?) and invoice_date > ? and invoice_date <= ?)""", W + list(DOUBLE_CATS) + W).fetchone()
S0_check = dec(ledger_all) + Z3
if abs(S0_check - S0) > Decimal("0.005"):
    raise SystemExit(f"ABBRUCH: Zwischensumme Gerätegeschäft stimmt nicht auf den Cent: Zeilensumme {S0} gegen Nachrechnung {S0_check}")
S1_check = S0_check - dec(indirect_all)
if abs(S1_check - S1) > Decimal("0.005"):
    raise SystemExit(f"ABBRUCH: EBITDA-Näherung stimmt nicht auf den Cent: Zeilensumme {S1} gegen Nachrechnung {S1_check}")
dep_py = Decimal(0); acc_py = {}
for serial, start, end, eff_end, pp, rv_end in con.execute(f"""with rv as ({RV_RECORD})
  select r.serial, r.start_date, r.end_date, coalesce(r.actual_end_date, r.end_date), d.purchase_price, coalesce(f.forecast_rv, d.estimate_rv_lease_end, 0)
  from main.rental_contracts r join silver.device_ledger d using(serial) left join rv f using(serial)""").fetchall():
    lt = max(months_between(start, end), 1)
    per_month = max(float(pp) - float(rv_end), 0.0) / lt
    acc = 0.0
    for k in range(lt):
        ms = add_months(start, k)
        if ms < eff_end and ms <= AS_OF:
            acc += per_month
            if ms > WS:
                dep_py += Decimal(str(per_month))
    acc_py[serial] = acc
if abs(-dep_py - Z7) > Decimal("0.5"):  # Python sums per contract-month in floats; SQL in doubles; half a Euro covers float noise on 33k terms
    raise SystemExit(f"ABBRUCH: planmäßige Abschreibung weicht ab: SQL {Z7} gegen Python {-dep_py}")
# jede Seriennummer: aufgelaufene Abschreibung der SQL-Monatsregel gleich der Python-Schleife, sonst Abbruch (Z3 und Flotte haengen daran)
acc_sql = dict(con.execute(f"with {DEP_CTE} select serial, acc_dep from acc", ACC_P).fetchall())
bad = [s for s in set(acc_sql) | set(acc_py) if abs(float(acc_sql.get(s, 0.0)) - acc_py.get(s, 0.0)) > 0.01]
if bad:
    raise SystemExit(f"ABBRUCH: aufgelaufene Abschreibung weicht bei QTY {len(bad)} Seriennummern ab, zum Beispiel {bad[:3]}")
S2_check = S1_check + Z7 + Z8
if abs(S2_check - S2) > Decimal("0.005"):
    raise SystemExit(f"ABBRUCH: EBIT-Näherung stimmt nicht auf den Cent: {S2} gegen {S2_check}")
print(f"G&V nachgerechnet: Gerätegeschäft {S0}, EBITDA {S1} (Nachrechnung {S1_check}), EBIT {S2}, Abschreibung SQL {Z7} gegen Python {-dep_py}, acc je Serial geprüft QTY {len(acc_sql)}")

pnl = {"z1": {"sum": fl(Z1), "n": iv(z1_n), "n_serials": iv(z1_serials)},
       "z2": {"sum": fl(Z2), "n": iv(z2_n)},
       "z3": {"sum": fl(Z3), "n": iv(z3_n)},
       "z4": {"sum": fl(Z4), "n": iv(z4_n)},
       "z5": {"sum": fl(Z5), "n": iv(z5_n), "n_types": iv(z5_types), "est": fl(z5_est), "est_hold": fl(z5_est_hold), "est_alloc": fl(z5_est_alloc), "est_fee": fl(z5_est_fee),
              "dbl": {c: z5_dbl.get(c, 0.0) for c in DOUBLE_CATS}},
       "z6": {"sum": fl(Z6), "n": iv(z6_n), "n_cat": iv(z6_cat), "cats": z6_cats, "rows_per_month": iv(lake.get("indirect_rows_per_month")),
              "dbl": {c: z6_dbl.get(c, 0.0) for c in DOUBLE_CATS}, "dbl_n": iv(z6_dbl_n), "dbl_sum": fl(z6_dbl_sum),
              "dbl_names": [cat_de(c) for c in DOUBLE_CATS]},
       "z7": {"sum": fl(Z7), "n": iv(z7_n), "n_months": iv(z7_months), "n_rec": iv(z7_rec), "n_lease": iv(z7_lease), "n_none": iv(z7_none), "n_repl": iv(z7_repl)},
       "z8": {"sum": fl(Z8), "n": iv(z8_n), "owner": own(z8_owner) or own(targets_cfg.get("min_n_owner")), "rules": z8_rules_text},
       "s0": fl(S0), "s1": fl(S1), "s2": fl(S2), "n_pp": iv(n_pp), "sum_pp": fl(sum_pp),
       "indirect_owner": own(lake.get("design_parameter_owners", {}).get("indirect_categories")),
       "holding_owner": own(con.execute("select min(assumption_owner) from silver.ledger_lines where line_type = 'holding_cost' and is_estimate").fetchone()[0])}

# ---- Kennzahlen
kv = {r[0]: r for r in con.execute("select kpi_id, value, unit, status, target, owner, n, name from gold.kpi_values where as_of = ?", [AS]).fetchall()}
kpi_rows = []
for group, kid, name in KPI_ROWS:
    r = kv.get(kid)
    if r is None:
        warn(f"Kennzahl fehlt in gold.kpi_values: {kid}"); continue
    kpi_rows.append({"group": group, "id": kid, "name": name, "value": fl(r[1]), "unit": r[2], "status": r[3], "target": fl(r[4]), "owner": own(r[5]), "n": iv(r[6])})
cfg_targets = targets_cfg.get("targets") or {}
kpi = {"rows": kpi_rows, "n_shown": len(kpi_rows), "n_targets": sum(1 for r in kpi_rows if r["target"] is not None),
       "n_gold": len(kv), "n_cfg_targets": len(cfg_targets), "n_cfg_targets_gold": sum(1 for k in cfg_targets if k in kv),
       "targets_owner": own(targets_cfg.get("targets_owner")), "min_n_owner": own(targets_cfg.get("min_n_owner")),
       "min_n": {k: iv(v) for k, v in (targets_cfg.get("min_n") or {}).items()}}


def kpi_val(kid):
    r = kv.get(kid)
    return None if r is None else fl(r[1])


def kpi_owner(kid):
    r = kv.get(kid)
    return None if r is None else own(r[5])


# ---- Mietvertraege, die in den naechsten zwoelf Monaten enden
ending = con.execute("""select r.contract_id, r.customer_id, r.monthly_rate, r.end_date, d.estimate_rv_lease_end, year(r.end_date), quarter(r.end_date)
  from main.rental_contracts r left join silver.device_ledger d using(serial)
  where r.status = 'active' and r.end_date > ? and r.end_date <= ? order by r.end_date, r.contract_id""", F).fetchall()
n_end = len(ending); sum_rate = sum(dec(e[2]) for e in ending); sum_rv = sum(dec(e[4]) for e in ending); n_norv = sum(1 for e in ending if e[4] is None)
quarters = {}
for e in ending:
    q = quarters.setdefault((e[5], e[6]), {"y": int(e[5]), "q": int(e[6]), "n": 0, "rate": Decimal(0), "rv": Decimal(0)})
    q["n"] += 1; q["rate"] += dec(e[2]); q["rv"] += dec(e[4])
quarters = [dict(v, rate=fl(v["rate"]), rv=fl(v["rv"])) for k, v in sorted(quarters.items())]
cust = {}
for e in ending:
    c = cust.setdefault(e[1], {"customer": e[1], "n": 0, "rate": Decimal(0), "last_end": None})
    c["n"] += 1; c["rate"] += dec(e[2]); c["last_end"] = max(c["last_end"] or e[3], e[3])
cust_sorted = sorted(cust.values(), key=lambda c: (-c["rate"], c["customer"]))
top5 = cust_sorted[:5]; rest_c = cust_sorted[5:]
rental = {"n_end": n_end, "sum_rate": fl(sum_rate), "sum_rv": fl(sum_rv), "n_norv": n_norv, "n_active": iv(n_active), "rate_active": fl(rate_active),
          "quarters": quarters, "n_quarters": len(quarters),
          "top": [{"customer": c["customer"], "n": c["n"], "rate": fl(c["rate"]), "last_end": str(c["last_end"])} for c in top5],
          "rest": {"n_customers": len(rest_c), "n": sum(c["n"] for c in rest_c), "rate": fl(sum((c["rate"] for c in rest_c), Decimal(0)))}}

# ---- Lieferanten- und Partnervertraege, laufend zum Stichtag (Tabelle: Top 5 nach Jahreswert, Bauauftrag)
sup = con.execute("""select contract_id, counterparty, category, annual_value, end_date, notice_deadline, notice_days, auto_renewal, price_protection
  from main.contracts_register where contract_type = 'supplier' and status = 'active' order by annual_value desc, contract_id""").fetchall()
cal = dict(con.execute("select contract_id, notice_deadline from main.renewal_calendar where contract_type = 'supplier'").fetchall())
for s in sup:
    if s[0] in cal and cal[s[0]] != s[5]:
        warn(f"Kündigungstermin weicht ab: Register {s[5]} gegen renewal_calendar {cal[s[0]]} bei {s[0]}")
sup_end = sorted([s for s in sup if s[4] is not None and s[4] > AS_OF and s[4] <= FW_END], key=lambda s: (s[5] or s[4], s[0]))
sup_show = sup_end[:5]                       # die Tabelle zeigt die auslaufenden Vertraege nach Kuendigungstermin, hoechstens fuenf
end_out = sup_end[5:]                        # auslaufende Vertraege jenseits der fuenf Zeilen, nur als Summe
sup_later = [x for x in sup if x not in sup_end]   # laufende Vertraege, die spaeter enden
supplier = {"n_sup": len(sup), "sum_av": fl(sum((dec(s[3]) for s in sup), Decimal(0))),
            "n_sup_end": len(sup_end), "av_end": fl(sum((dec(s[3]) for s in sup_end), Decimal(0))),
            "n_notice_past": sum(1 for s in sup_end if s[5] is not None and s[5] < AS_OF), "n_auto": sum(1 for s in sup_end if bool(s[7])),
            "top": [{"cp": role_de(s[1]), "cat": cat_de(s[2]), "av": fl(s[3]), "end": str(s[4]) if s[4] else None, "notice": str(s[5]) if s[5] else None,
                     "auto": bool(s[7]), "pp": bool(s[8])} for s in sup_show],
            "rest": {"n": len(sup_later), "av": fl(sum((dec(s[3]) for s in sup_later), Decimal(0)))},
            "end_out": {"n": len(end_out), "n_in": len(sup_show), "av": fl(sum((dec(s[3]) for s in end_out), Decimal(0))),
                        "first_end": str(min(s[4] for s in end_out)) if end_out else None, "last_end": str(max(s[4] for s in end_out)) if end_out else None,
                        "first_notice": str(min(s[5] for s in end_out if s[5] is not None)) if any(s[5] is not None for s in end_out) else None}}

# ---- Wirkung des Teams: jede Zahl fuer das Fenster und ein zweites Mal fuer die zwoelf Monate davor


def impact_window(win):
    n1, eur1, ratio1 = con.execute("""select count(*), coalesce(sum(discount_vs_rrp_eur), 0),
      1 - sum(purchase_price - coalesce(price_protection_credit_eur, 0)) / nullif(sum(rrp_net_eur), 0) from silver.device_ledger
      where received_at > ? and received_at <= ? and rrp_net_eur is not null and purchase_price is not null""", win).fetchone()
    sum2, n2 = con.execute("""select coalesce(sum(amount_eur), 0), count(*) from silver.ledger_lines
      where line_type = 'price_protection_credit' and event_date > ? and event_date <= ?""", win).fetchone()
    n3, gross3, rec3 = con.execute("""select count(*), coalesce(sum(resale_gross), 0), coalesce(sum(estimate_rv_of_record), 0) from silver.device_ledger
      where sale_date > ? and sale_date <= ? and resale_channel <> 'as_is' and estimate_rv_of_record is not null""", win).fetchone()
    med4, n4 = con.execute("""select median(days_return_to_cash), count(*) from silver.device_ledger
      where credited_at > ? and credited_at <= ? and days_return_to_cash is not null""", win).fetchone()
    sum5, conf5, hard5, confhard5, n5 = con.execute("""select coalesce(sum(saving), 0), coalesce(sum(case when saving_confirmed_by_controlling then saving else 0 end), 0),
      coalesce(sum(case when saving_type = 'hard_price_reduction' then saving else 0 end), 0),
      coalesce(sum(case when saving_confirmed_by_controlling and saving_type = 'hard_price_reduction' then saving else 0 end), 0), count(*) filter (saving > 0)
      from main.indirect_spend where invoice_date > ? and invoice_date <= ?""", win).fetchone()
    return {"w1": {"n": iv(n1), "eur": fl(eur1), "ratio": fl(ratio1)},
            "w2": {"sum": fl(sum2), "n": iv(n2)},
            "w3": {"n": iv(n3), "delta": fl(dec(gross3) - dec(rec3)), "ratio": (fl(dec(gross3) / dec(rec3)) if dec(rec3) != 0 else None)},
            "w4": {"days": fl(med4), "n": iv(n4)},
            "w5": {"sum": fl(sum5), "confirmed": fl(conf5), "hard": fl(hard5), "confirmed_hard": fl(confhard5), "n": iv(n5)}}


cur, prev = impact_window(W), impact_window(PW)
# Nachrechnung gegen die Kennzahlen: Einkaufsrabatt, Restwert gegen Restwertprognose bei Rueckgabe, Tage bis Zahlungseingang
for kid, mine, label in (("KPI_PUR_DISCOUNT_VS_RRP", cur["w1"]["ratio"], "Einkaufsrabatt gegen UVP"),
                         ("KPI_RSL_REALISED_VS_RECORD", cur["w3"]["ratio"], "Restwert gegen Restwertprognose bei Rückgabe"),
                         ("KPI_RSL_DAYS_RETURN_TO_CASH", cur["w4"]["days"], "Median Tage Rückgabe bis Zahlungseingang")):
    if kpi_val(kid) is not None and mine is not None and abs(mine - kpi_val(kid)) > 1e-9:
        warn(f"{label}: Nachrechnung {mine} gegen Kennzahl {kpi_val(kid)}")
if abs(dec(cur["w2"]["sum"]) - Z4) > Decimal("0.005"):
    warn(f"Preisschutz-Gutschriften: Wirkung {cur['w2']['sum']} gegen G&V {Z4}")
ppc = kv.get("KPI_PUR_PRICE_PROTECTION_CAPTURE")
plan = (targets_cfg.get("savings_plan_eur") or {}).get(AS_OF.year)
impact = {"prev_start1": str(WS2 + timedelta(days=1)), "prev_end": str(WS), "plan_year": AS_OF.year, "plan": fl(plan),
          "w1": dict(cur["w1"], ratio=kpi_val("KPI_PUR_DISCOUNT_VS_RRP"), owner=kpi_owner("KPI_PUR_DISCOUNT_VS_RRP"), prev=prev["w1"]),
          "w2": dict(cur["w2"], status=ppc[3] if ppc else None, kpi_n=iv(ppc[6]) if ppc else None,
                     min_n=kpi["min_n"].get("KPI_PUR_PRICE_PROTECTION_CAPTURE"), owner=kpi_owner("KPI_PUR_PRICE_PROTECTION_CAPTURE"), prev=prev["w2"]),
          "w3": dict(cur["w3"], ratio=kpi_val("KPI_RSL_REALISED_VS_RECORD"), owner=kpi_owner("KPI_RSL_REALISED_VS_RECORD"), prev=prev["w3"]),
          "w4": dict(cur["w4"], days=kpi_val("KPI_RSL_DAYS_RETURN_TO_CASH"), owner=kpi_owner("KPI_RSL_DAYS_RETURN_TO_CASH"), prev=prev["w4"]),
          "w5": dict(cur["w5"], owner=own(targets_cfg.get("savings_plan_owner")), prev=prev["w5"])}

# ---- Stand der Dinge
b1 = con.execute("""select
  count(*) filter (lifecycle_status in ('rented', 'awaiting_return')), count(*) filter (lifecycle_status = 'awaiting_return'),
  count(*) filter (lifecycle_status in ('wip', 'in_stock') and return_date is not null), count(*) filter (lifecycle_status = 'in_stock' and return_date is not null),
  count(*) filter (term_months is null and not is_closed) from silver.device_ledger""").fetchone()
# Restbuchwert der Flotte zum Stichtag, dieselbe Monatsregel wie Z7 und Z3; Restwertprognose zum Leasingende beim Kunden, heute im Lager
fleet_rows = con.execute(f"""
with {DEP_CTE},
o as (
  select d.serial, d.purchase_price, coalesce(a.acc_dep, 0) acc_dep,
    coalesce((select sum(w.amount) from main.write_down_ledger w where w.serial = d.serial and w.as_of <= ?::date), 0) wd,
    case when d.lifecycle_status in ('rented', 'awaiting_return') then d.estimate_rv_lease_end else d.estimate_rv_today end rv,
    case when d.lifecycle_status in ('rented', 'awaiting_return') then 'customer' when d.lifecycle_status in ('wip', 'in_stock') then 'stock'
         when d.term_months is null then 'spare' else 'other' end grp
  from silver.device_ledger d left join acc a using(serial) where not d.is_closed)
select grp, count(*), coalesce(sum(greatest(purchase_price - acc_dep - wd, 0)), 0), coalesce(sum(rv), 0), count(rv),
  coalesce(sum(greatest(purchase_price - acc_dep - wd, 0)) filter (rv is not null), 0) from o group by grp""", ACC_P + [AS]).fetchall()
fleet = {"n": 0, "bv": Decimal(0), "n_none": 0, "bv_none": Decimal(0)}
for grp, n, bv, rv, n_rv, bv_rv in fleet_rows:
    if grp == "other" and n:
        warn(f"QTY {n} offene Geräte ohne Zuordnung (weder beim Kunden, im Lager noch Ersatz) im Restbuchwert der Flotte")
    fleet[grp] = {"n": iv(n), "bv": fl(bv), "rv": fl(rv), "n_rv": iv(n_rv), "bv_rv": fl(bv_rv)}
    fleet["n"] += int(n); fleet["bv"] += dec(bv); fleet["n_none"] += int(n) - int(n_rv); fleet["bv_none"] += dec(bv) - dec(bv_rv)
for grp in ("customer", "stock", "spare"):
    fleet.setdefault(grp, {"n": 0, "bv": 0.0, "rv": 0.0, "n_rv": 0, "bv_rv": 0.0})
fleet["bv"] = fl(fleet["bv"]); fleet["bv_none"] = fl(fleet["bv_none"])
n_open_check = con.execute("select count(*) from silver.device_ledger where not is_closed").fetchone()[0]
if int(n_open_check) != fleet["n"]:
    raise SystemExit(f"ABBRUCH: offene Geräte {n_open_check} gegen Flottengruppen {fleet['n']}")
dq = con.execute("""select count(*), count(*) filter (priority = 1), count(*) filter (due_date < ?), coalesce(sum(value_at_stake_eur), 0),
  coalesce(sum(value_at_stake_eur) filter (priority = 1), 0), count(*) filter (priority = 1 and due_date < ?) from main.decision_queue""", [AS, AS]).fetchone()
month_first = date(AS_OF.year, AS_OF.month, 1)
err = con.execute("""select model_family, month, n_sales, mape, bias from main.rv_forecast_error_monthly
  where month < ? and mape is not null qualify row_number() over (partition by model_family order by month desc) <= 3 order by model_family, month""", [str(month_first)]).fetchall()
fams = defaultdict(list)
for fam, month, n, mape, bias in err:
    fams[fam].append((month, int(n or 0), float(mape), float(bias) if bias is not None else None))
order = list(FAM_DE) + ["*"]
for fam in fams:
    if fam not in order:
        warn(f"unbekannte Geräteart in rv_forecast_error_monthly: {fam}")
quality = []
for fam in order + [f for f in fams if f not in order]:
    rows = fams.get(fam)
    if not rows:
        continue
    n_tot = sum(r[1] for r in rows)
    mape_w = (sum(r[2] * r[1] for r in rows) / n_tot) if n_tot else (sum(r[2] for r in rows) / len(rows))
    bias_rows = [r for r in rows if r[3] is not None]
    n_b = sum(r[1] for r in bias_rows)
    bias_w = ((sum(r[3] * r[1] for r in bias_rows) / n_b) if n_b else None) if bias_rows else None
    quality.append({"fam": "alle Gerätearten" if fam == "*" else FAM_DE.get(fam, fam), "n": n_tot, "n_months": len(rows), "mape": mape_w, "bias": bias_w,
                    "first": str(min(r[0] for r in rows)), "last": str(max(r[0] for r in rows))})
# Geld, das noch liegt: die addierbaren Stellschrauben aus gold.levers_summary, nicht getippt
lev = con.execute("select lever_name, additive, eur_fleet_per_year from gold.levers_summary order by rank").fetchall()
lev_add = [lever_de(n) for n, a, e in lev if a]; lev_non = [lever_de(n) for n, a, e in lev if not a]
lev_sum_add = sum((dec(e) for n, a, e in lev if a), Decimal(0))
if kpi_val("KPI_LEV_ADDITIVE_EUR_PA") is not None and abs(float(lev_sum_add) - kpi_val("KPI_LEV_ADDITIVE_EUR_PA")) > 0.005:
    warn(f"Hebel additiv: gold.levers_summary {lev_sum_add} gegen Kennzahl {kpi_val('KPI_LEV_ADDITIVE_EUR_PA')}")
status = {"n_rented": iv(b1[0]), "n_await": iv(b1[1]), "n_stock": iv(b1[2]), "n_sellable": iv(b1[3]), "n_spare": iv(b1[4]),
          "n_sold": iv(z2_n), "n_scrapped": iv(n_scrapped), "fleet": fleet,
          "chain": kpi_val("KPI_DATA_CHAIN_COMPLETE"), "chain_owner": kpi_owner("KPI_DATA_CHAIN_COMPLETE"),
          "n_q": iv(dq[0]), "n_p1": iv(dq[1]), "n_due": iv(dq[2]), "stake": fl(dq[3]), "stake_p1": fl(dq[4]), "n_p1_due": iv(dq[5]),
          "quality": quality, "q_first": min((q["first"] for q in quality), default=None), "q_last": max((q["last"] for q in quality), default=None), "q_months": max((q["n_months"] for q in quality), default=0),
          "lev": kpi_val("KPI_LEV_ADDITIVE_EUR_PA"), "lev_add": lev_add, "n_add": len(lev_add), "n_non": len(lev_non)}

data = {"today": TODAY, "as_of": AS, "win_start1": str(WS + timedelta(days=1)), "fw_start1": str(AS_OF + timedelta(days=1)), "fw_end": str(FW_END),
        "n_serials": n_serials, "tiles": {"fees": fl(fees), "ratio_record": kpi_val("KPI_RSL_REALISED_VS_RECORD"), "n_scrapped": iv(n_scrapped)},
        "pnl": pnl, "kpi": kpi, "rental": rental, "supplier": supplier, "impact": impact, "status": status, "warnings": list(WARN)}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/report.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben")
