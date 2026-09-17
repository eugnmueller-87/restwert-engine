"""Kennzahlen: data/kpis.json fuer den Reiter "KPIs" (Bereich Analytics) der Restwert Engine.

Der Reiter buendelt die 21 Kennzahlen des KPI-Rahmens (web/tools/gen/kpi_rahmen.json, Fassung 3, kuratiert) mit dem,
was die Engine schon liefert: die 14 gold-KPIs (outputs/gold__kpi_values.csv, outputs/gold__kpi_breakdown.csv,
restwert/gold/kpis.py) und die 20 KPIs der eingefrorenen v0.1-Registry (outputs/kpi_values.csv,
outputs/kpi_breakdown.csv), dazu Ziele, Mindeststichproben und Eigner aus config/kpi_targets.yaml. Monatsreihen
fuer den Trend und die Baseline (Mittel der ersten drei Monate mit Daten) rechnet dieser Generator aus denselben
Tabellen in data/restwert.duckdb, mit denselben Formeln wie die gold-KPIs, nur je Kalendermonat statt rollierend.

Status je Kennzahl, deterministisch und nie geraten:
  erfuellt            Ist erreicht das Ziel
  gelb                Ist liegt auf der schlechteren Seite innerhalb GELB_BAND des Ziels (bei Zielen gegen die
                      Baseline: innerhalb GELB_BAND der geforderten Bewegung)
  verfehlt            darueber hinaus
  nicht_messbar       Nenner null, Mindeststichprobe unterschritten, oder der Zaehler der Kennzahl ist im Modell
                      nicht gebildet (Grund steht dabei; eine Naeherung der Engine steht daneben, wenn es eine gibt)
  nicht_im_werkzeug   die Daten existieren in der Engine nicht (was das Haus liefern muss, steht dabei)
Ein KPI mit status not_measurable in der Engine kommt mit value None an und wird nie als 0 gezeigt (records.py).
Keine Zahl je Person: nur Kategorie, Marke, Kanal und Team.

Aufruf: python make_kpis_data.py <REPO> <OUT.json> <TODAY>
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
HERE = pathlib.Path(__file__).parent
RAHMEN = HERE / "kpi_rahmen.json"

GELB_BAND = 0.2           # Anteil des Ziels (oder der geforderten Bewegung), der noch als gelb gilt
BASELINE_MONTHS = 3       # Baseline gleich Mittel der ersten drei Monate mit Daten (ab Mindeststichprobe)
SPARK_MONTHS = 24         # die Verlaufslinie zeigt die letzten 24 Monate mit Wert ab Mindeststichprobe
TAG_SIM, TAG_DER = "simuliert", "abgeleitet"
# deutsche Beschriftung der Datenwerte, dieselbe wie auf den anderen Reitern (make_report_data.py, make_lake_data.py)
FAM_DE = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "tablet_like": "Tablet", "laptop_like": "Laptop"}
CH_DE = {"employee_buyout": "Mitarbeiterkauf", "marketplace": "Marktplatz", "b2b_wholesale": "Großhandel", "as_is": "Verkauf ohne Aufbereitung"}
CAT_DE = {"hardware": "Hardware", "logistics": "Logistik", "repair": "Reparatur", "refurbishment": "Aufbereitung", "software": "Software",
          "marketing": "Marketing", "facilities": "Gebäude und Betrieb", "consulting": "Beratung", "packaging": "Verpackung",
          "connectivity": "Mobilfunk und Konnektivität", "resale_channel": "Verkaufskanal", "financing": "Finanzierung",
          "security_software": "Sicherheitssoftware", "rental": "Miete"}
TYP_DE = {"hard_price_reduction": "Preisreduktion (Hard Saving)", "cost_avoidance": "Kostenvermeidung", "rebate": "Rückvergütung"}
LABEL = {}
LABEL.update(FAM_DE); LABEL.update(CH_DE); LABEL.update(CAT_DE); LABEL.update(TYP_DE)
EN_DASH, EM_DASH = chr(0x2013), chr(0x2014)   # die Seite darf keinen Strich zeigen (build.py lehnt ihn ab)


def fl(v, nd=6):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, nd)


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


def s_or_blank(v):
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def de_date(v):
    return pd.Timestamp(v).strftime("%d.%m.%Y")


def dn(x, nd=0):
    """Zahl deutsch (Tausenderpunkt, Komma) fuer Texte, die der Generator selbst formuliert."""
    if x is None:
        return "n/a"
    s = f"{float(x):,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# ---- der Rahmen (kuratiert)
rahmen = json.load(open(RAHMEN, encoding="utf-8"))
if EN_DASH in json.dumps(rahmen, ensure_ascii=False) or EM_DASH in json.dumps(rahmen, ensure_ascii=False):
    raise SystemExit("kpi_rahmen.json: Strich im Rahmen")
by_nr = {k["nr"]: k for k in rahmen["kpis"]}

# ---- Ziele, Mindeststichproben, Eigner
targets = yaml.safe_load(open(REPO / "config" / "kpi_targets.yaml", encoding="utf-8")) or {}
MIN_N = {str(k): int(v) for k, v in (targets.get("min_n") or {}).items()}
MIN_N_OWNER = own(targets.get("min_n_owner", ""))
TARGETS_OWNER = own(targets.get("targets_owner", ""))
T01 = targets.get("targets") or {}

# ---- die Engine-KPIs
gold = pd.read_csv(REPO / "outputs" / "gold__kpi_values.csv")
gold_bd = pd.read_csv(REPO / "outputs" / "gold__kpi_breakdown.csv")
v01 = pd.read_csv(REPO / "outputs" / "kpi_values.csv")
v01_bd = pd.read_csv(REPO / "outputs" / "kpi_breakdown.csv")
AS_OF = pd.Timestamp(gold["as_of"].iloc[0])
WIN_START = (AS_OF - pd.DateOffset(months=12) + pd.Timedelta(days=1)).normalize()
WINDOW = f"{de_date(WIN_START)} bis {de_date(AS_OF)}"
STICHTAG = "Stichtag " + de_date(AS_OF)


def kpi_row(frame, kpi_id, layer):
    r = frame[frame["kpi_id"] == kpi_id]
    if not len(r):
        raise SystemExit(f"{layer}: {kpi_id} fehlt in der Wertetabelle")
    r = r.iloc[0]
    status = str(r["status"])
    return {
        "id": kpi_id, "layer": layer, "name": str(r["name"]), "page": str(r.get("page", r.get("area", ""))),
        "value": fl(r["value"]) if status == "ok" else None, "raw_value": fl(r["value"]),
        "numerator": fl(r["numerator"], 2), "denominator": fl(r["denominator"], 2), "n": iv(r["n"]),
        "unit": str(r["unit"]), "status": status, "note": s_or_blank(r["note"]),
        "target": fl(r["target"]) if "target" in r and not pd.isna(r["target"]) else None,
        "direction": str(r["direction"]), "formula": s_or_blank(r["formula_text"]), "tables": s_or_blank(r["source_tables"]),
        "owner": own(r["owner"]) if "owner" in r else "", "min_n": MIN_N.get(kpi_id),
    }


def breakdown(frame, kpi_id, dim, nd=4, col="value"):
    b = frame[(frame["kpi_id"] == kpi_id) & (frame["dimension"] == dim)]
    return [{"k": LABEL.get(str(r.dimension_value), str(r.dimension_value)), "v": fl(getattr(r, col), nd), "n": iv(r.n)} for r in b.itertuples(index=False)]


G = {k: kpi_row(gold, k, "gold") for k in gold["kpi_id"]}
V = {k: kpi_row(v01, k, "v0.1") for k in v01["kpi_id"]}

# ---- DuckDB: Monatsreihen und die Zaehlungen, die kein KPI schon liefert
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)


def monthly(sql, params=()):
    """[{m, v, n}] je Kalendermonat, aufsteigend; v None bei Nenner null."""
    rows = con.execute(sql, params).fetchall()
    out = []
    for m, v, n in rows:
        if m is None:
            continue
        out.append({"m": month_str(m), "v": fl(v), "n": iv(n)})
    return out


def baseline_of(series, min_n):
    """Mittel der ersten BASELINE_MONTHS Monate mit Wert und n >= min_n; None, wenn es keinen gibt."""
    ok = [s for s in series if s["v"] is not None and s["n"] >= max(1, min_n)]
    first = ok[:BASELINE_MONTHS]
    if not first:
        return None
    return {"v": fl(sum(s["v"] for s in first) / len(first)), "monate": [s["m"] for s in first], "n": sum(s["n"] for s in first)}


def judge(v, t, richtung, delta=None, strikt=False):
    """erfuellt, gelb, verfehlt gegen ein numerisches Ziel; None bei fehlendem Wert oder Ziel."""
    if v is None or t is None:
        return None
    if richtung == "up":
        ok = v > t if strikt else v >= t
    else:
        ok = v < t if strikt else v <= t
    if ok:
        return "erfuellt"
    tol = abs(delta) * GELB_BAND if delta is not None else abs(t) * GELB_BAND
    if richtung == "up":
        return "gelb" if v >= t - tol else "verfehlt"
    return "gelb" if v <= t + tol else "verfehlt"


RANK = {"erfuellt": 0, "gelb": 1, "verfehlt": 2}


def resolve_target(zw, baseline):
    """Das numerische Ziel: absolut, oder gegen die Baseline; (ziel, delta) mit delta der geforderten Bewegung."""
    art = zw.get("art")
    if art == "absolut":
        return zw.get("wert"), None
    if baseline is None or baseline.get("v") is None:
        return None, None
    b = baseline["v"]
    if art == "baseline_delta":
        t = b + zw["wert"]
        if zw.get("einheit") == "ratio":
            t = max(0.0, min(1.0, t))
        return fl(t), zw["wert"]
    if art == "baseline_faktor":
        t = b * zw["wert"]
        return fl(t), fl(t - b)
    return None, None


def used(kpi_ids, tabellen, spalten, fenster, zaehlung):
    return {"kpi_ids": kpi_ids, "tabellen": tabellen, "spalten": spalten, "fenster": fenster, "zaehlung": zaehlung}


rows = []


def add(nr, modus, ist, serie=None, min_n=1, benutzt=None, naeherung="", liefern="", zweite=None, grund="", tag=TAG_SIM, extra=None, bezug="", ersatz=""):
    k = by_nr[nr]
    spark = [s for s in (serie or []) if s["v"] is not None and s["n"] >= max(1, min_n)][-SPARK_MONTHS:]
    zw = dict(k["ziel_wert"])
    base = baseline_of(serie or [], min_n) if zw.get("art") in ("baseline_delta", "baseline_faktor") else None
    t, delta = resolve_target(zw, base)
    zw["berechnet"] = t
    status = None
    reason = grund
    if modus == "keine":
        status = "nicht_im_werkzeug"
    elif modus == "luecke":
        status = "nicht_messbar"
    else:
        v = ist.get("v")
        if v is None:
            status = "nicht_messbar"
            reason = reason or ist.get("grund", "kein Wert")
        elif t is None:
            status = "nicht_messbar"
            reason = reason or ("Baseline fehlt: keine drei Monate mit mindestens " + str(min_n) + " Beobachtungen" if zw.get("art") in ("baseline_delta", "baseline_faktor") else "Ziel ohne Zahl im Werkzeug (" + str(zw.get("referenz", "")) + ")")
        else:
            status = judge(v, t, zw["richtung"], delta, bool(zw.get("strikt")))
            parts = [status]
            if zweite is not None:
                z2 = zweite
                z2["status"] = judge(z2.get("v"), z2.get("ziel_v"), z2.get("richtung"), None, False) if z2.get("v") is not None else None
                if z2["status"] is not None:
                    parts.append(z2["status"])
            status = max(parts, key=lambda s: RANK[s])
    rows.append({
        "nr": nr, "satz": nr[0], "name": k["name"], "rollen": k["rollen"], "ziel": k["ziel"], "beispiel": bool(k["beispiel"]),
        "takt": k["takt"], "echt_ab": k["echt_ab"], "eigner": k["eigner"], "pruefer": k["pruefer"],
        "formel": k["formel"], "quelle": k["quelle"], "zaehlregel": k["zaehlregel"], "engine": k["engine"],
        "modus": modus, "status": status, "grund": reason, "ist": ist, "zweite": zweite, "ziel_wert": zw, "baseline": base,
        "serie": serie or [], "spark": spark, "min_n": min_n, "bezug": bezug, "benutzt": benutzt or used([], "", "", "", ""), "naeherung": naeherung, "ersatz": ersatz, "liefern": liefern,
        "tag": tag, "extra": extra or {},
    })


LEDGER = "silver.device_ledger"

# ---------------------------------------------------------------- Satz A: alle Rollen
# A1 Spend unter Management: Naeherung ueber KPI_IND_SPEND_UNDER_MGMT (v0.1), Monatsreihe aus indirect_spend
k = V["KPI_IND_SPEND_UNDER_MGMT"]
ser = monthly("""select date_trunc('month', invoice_date) m,
                        sum(case when has_po or has_contract then amount else 0 end) / nullif(sum(amount), 0) v, count(*) n
                 from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
add("A1", "naeherung", {"v": k["value"], "einheit": "ratio", "n": k["n"], "text": "rollierend zwölf Monate, alle indirekten Rechnungen"},
    serie=ser, min_n=1,
    benutzt=used(["KPI_IND_SPEND_UNDER_MGMT"], "main.indirect_spend", "amount, has_po, has_contract, invoice_date", WINDOW,
                 "Summe der Rechnungsbeträge mit Bestellung oder Vertrag durch Summe aller indirekten Rechnungsbeträge; Monatsreihe je Rechnungsmonat aus derselben Tabelle"),
    naeherung="Die Einkaufsbeteiligung vor der Unterschrift und der bereinigte Nenner (ohne Löhne, Steuern, Zinsen, Konzernverrechnung) fehlen im Modell; gezählt wird jeder Rechnungsbetrag mit Bestellung oder Vertrag gegen alle indirekten Rechnungen.",
    extra={"kategorien": breakdown(v01_bd, "KPI_IND_SPEND_UNDER_MGMT", "category")},
    bezug="des indirekten Spends")

# A2 Vertragsregister vollstaendig: sieben von acht Feldern zaehlbar, das achte (Eigner im Team) fehlt im Register
n_ctr, n_seven = con.execute("""select count(*), sum(case when counterparty_name is not null and category is not null and spend_under_contract_eur is not null
                                        and end_date is not null and notice_deadline is not null and auto_renewal is not null and price_protection is not null then 1 else 0 end)
                                from silver.contracts""").fetchone()
add("A2", "luecke", {"v": None, "einheit": "ratio", "n": iv(n_ctr), "text": ""},
    benutzt=used([], "silver.contracts", "counterparty_name, category, spend_under_contract_eur, end_date, notice_deadline, auto_renewal, price_protection", STICHTAG,
                 "Verträge mit allen sieben vorhandenen Feldern gefüllt durch alle Verträge im Register; eine Wertschwelle kennt das Register nicht"),
    grund="Das achte Pflichtfeld Eigner im Team fehlt im Register; nach der Zählregel ist damit kein Vertrag vollständig, und ohne Wertschwelle fehlt der Nenner.",
    ersatz=f"sieben von acht Feldern gefüllt: {dn(n_seven)} von {dn(n_ctr)} Verträgen",
    liefern="ein Feld Eigner im Team je Vertrag im Register und die Wertschwelle, die Controlling einheitlich setzt",
    extra={"n_seven": iv(n_seven), "n_ctr": iv(n_ctr)})

# A3, A4: nicht im Werkzeug
add("A3", "keine", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    liefern="ein Prüfprotokoll je Lieferant mit Datum (Sanktionslisten, Eigentümer, Sorgfaltspflichten) und das Kreditorenbuch auf Ebene der Muttergesellschaft für den Nenner")
add("A4", "keine", {"v": None, "einheit": "count", "n": None, "text": ""},
    liefern="den gemeinsamen Speicher des Teams mit Datum und Verweis je Verbesserung und die Abnahme im Quartalsgespräch")

# ---------------------------------------------------------------- Satz B: Indirekt
# B1 Hard Savings validiert: bestaetigte Preisreduktionen gegen den gesamten indirekten Spend, rollierend zwoelf Monate
hard, spend, n_inv, n_hard = con.execute("""select sum(case when saving_confirmed_by_controlling and coalesce(saving_type, 'hard_price_reduction') = 'hard_price_reduction' then coalesce(saving, 0) else 0 end),
                                                   sum(amount), count(*),
                                                   sum(case when saving_confirmed_by_controlling and coalesce(saving_type, 'hard_price_reduction') = 'hard_price_reduction' and coalesce(saving, 0) > 0 then 1 else 0 end)
                                            from main.indirect_spend where invoice_date >= ? and invoice_date <= ?""", [WIN_START.date(), AS_OF.date()]).fetchone()
ser = monthly("""select date_trunc('month', invoice_date) m,
                        sum(case when saving_confirmed_by_controlling and coalesce(saving_type, 'hard_price_reduction') = 'hard_price_reduction' then coalesce(saving, 0) else 0 end) / nullif(sum(amount), 0) v,
                        count(*) n
                 from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
kk = V["KPI_IND_SAVINGS_CONFIRMED"]
plan_year = AS_OF.year
plan_eur = (targets.get("savings_plan_eur") or {}).get(plan_year, (targets.get("savings_plan_eur") or {}).get(str(plan_year)))
add("B1", "naeherung", {"v": fl(hard / spend) if spend else None, "einheit": "ratio", "n": iv(n_inv), "text": f"bestätigte Preisreduktionen {dn(hard, 2)} € auf {dn(spend, 2)} € indirekten Spend, {dn(n_hard)} Fälle, rollierend zwölf Monate", "grund": "Spend null"},
    serie=ser, min_n=1, tag=TAG_DER,
    benutzt=used(["KPI_IND_SAVINGS_CONFIRMED"], "main.indirect_spend", "saving, saving_type, saving_confirmed_by_controlling, amount, invoice_date", WINDOW,
                 "Summe der von Controlling bestätigten Preisreduktionen (saving_type hard_price_reduction) durch Summe aller indirekten Rechnungsbeträge; die Engine-Kennzahl selbst misst gegen den Jahresplan aus config/kpi_targets.yaml"),
    naeherung="Der Nenner ist der gesamte indirekte Spend, nicht der adressierbare der Kategorie; die schriftliche Baseline je Fall und die vier Stufen identifiziert, verhandelt, validiert, realisiert kennt das Modell nicht, nur bestätigt oder unbestätigt.",
    extra={"plan_ratio": kk["value"], "plan_year": plan_year, "plan_eur": fl(plan_eur, 2), "plan_confirmed": kk["numerator"], "plan_owner": own(targets.get("savings_plan_owner", "")), "hard": fl(hard, 2), "spend": fl(spend, 2), "n_hard": iv(n_hard), "typen": breakdown(v01_bd, "KPI_IND_SAVINGS_CONFIRMED", "saving_type", 2, col="numerator")},
    bezug="des indirekten Spends")

# B3 Verlaengerungen mit Vorlauf: Kalender vorhanden, Verhandlungsakte nicht
n_cal, n_act, n_next = con.execute("""select count(*), sum(case when action_required then 1 else 0 end),
                                             sum(case when days_to_notice_deadline between 0 and 90 then 1 else 0 end)
                                      from gold.renewal_calendar_v2""").fetchone()
add("B3", "luecke", {"v": None, "einheit": "ratio", "n": iv(n_cal), "text": ""},
    benutzt=used([], "gold.renewal_calendar_v2", "notice_deadline, days_to_notice_deadline, auto_renewal, action_required", STICHTAG,
                 "Kündigungstermine je Vertrag sind berechnet; das Datum der ersten Verhandlungshandlung gibt es nicht"),
    grund="Der Zähler fehlt: keine Verhandlungsakte, kein Datum einer ersten dokumentierten Verhandlungshandlung im Modell.",
    ersatz=f"Kündigungskalender: {dn(n_cal)} Verträge, {dn(n_act)} mit Handlungsbedarf, {dn(n_next)} mit Kündigungstermin in den nächsten 90 Tagen",
    liefern="eine Verhandlungsakte je Verlängerung mit dem Datum der ersten dokumentierten Verhandlungshandlung (Schreiben mit Ziel oder Angebot an den Lieferanten)",
    extra={"n_cal": iv(n_cal), "n_act": iv(n_act), "n_next": iv(n_next)})

# B4: nicht im Werkzeug
add("B4", "keine", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    liefern="das Vertragsregister mit alter und neuer Vertragsversion je Lieferant (Preis je Einheit) und den VPI vom Statistischen Bundesamt")

# B5 Stille Verlaengerungen: Kandidaten zaehlbar, Entscheidung nicht
n_auto, n_passed = con.execute("""select sum(case when auto_renewal then 1 else 0 end),
                                         sum(case when auto_renewal and notice_deadline < ? and (end_date is null or end_date >= ?) then 1 else 0 end)
                                  from silver.contracts""", [AS_OF.date(), AS_OF.date()]).fetchone()
add("B5", "luecke", {"v": None, "einheit": "count", "n": iv(n_ctr), "text": ""},
    benutzt=used([], "silver.contracts", "auto_renewal, notice_deadline, end_date", STICHTAG,
                 "Verträge mit automatischer Verlängerung und verstrichenem Kündigungstermin sind Kandidaten; ob eine Entscheidung protokolliert ist, steht in keinem Feld"),
    grund="Das Feld Entscheidung mit Datum und Eigner fehlt im Register; Kandidaten sind zählbar, still oder entschieden nicht.",
    ersatz=f"{dn(n_auto)} Verträge mit automatischer Verlängerung, davon {dn(n_passed)} mit verstrichenem Kündigungstermin bei laufendem Vertrag",
    liefern="ein Feld Entscheidung (verlängern, kündigen, neu verhandeln) mit Datum und Eigner in der versionierten Änderungshistorie des Registers",
    extra={"n_auto": iv(n_auto), "n_passed": iv(n_passed)})

# B6 Maverick-Quote: Naeherung ueber KPI_IND_MAVERICK_SHARE, Monatsreihe aus indirect_spend, Ziel halbiert gegen die Baseline
k = V["KPI_IND_MAVERICK_SHARE"]
ser = monthly("""select date_trunc('month', invoice_date) m,
                        sum(case when not has_po and not has_contract then amount else 0 end) / nullif(sum(amount), 0) v, count(*) n
                 from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
add("B6", "naeherung", {"v": k["value"], "einheit": "ratio", "n": k["n"], "text": "rollierend zwölf Monate, alle indirekten Rechnungen"},
    serie=ser, min_n=1,
    benutzt=used(["KPI_IND_MAVERICK_SHARE"], "main.indirect_spend", "amount, has_po, has_contract, invoice_date", WINDOW,
                 "Summe der Rechnungsbeträge ohne Bestellung und ohne Vertrag durch Summe aller indirekten Rechnungsbeträge; Monatsreihe je Rechnungsmonat"),
    naeherung="Keine Wertschwelle, keine rollierende 90-Tage-Bündelung je Lieferant, keine Ausnahme für Abrufe aus einem registrierten Rahmenvertrag; gezählt wird jede Rechnung ohne Bestellung und ohne Vertrag.",
    extra={"kategorien": breakdown(v01_bd, "KPI_IND_MAVERICK_SHARE", "category")},
    bezug="des indirekten Spends")

# B7 Scorecard: nur OTIF gerechnet, nur Hardware
k = V["KPI_PROC_SUPPLIER_OTIF"]
add("B7", "luecke", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    benutzt=used(["KPI_PROC_SUPPLIER_OTIF"], "main.purchase_orders", "delivered_date, promised_date, qty_delivered, qty_ordered", WINDOW,
                 "nur die Dimension Lieferleistung: Lieferungen pünktlich und vollständig durch gelieferte Aufträge, Hardware-Einkauf (v0.1)"),
    grund="Scorecard und Gesprächsprotokoll fehlen im Modell; von den vier Dimensionen ist nur die Lieferleistung (OTIF) gerechnet, und nur für Hardware.",
    ersatz=(f"Lieferleistung Hardware (OTIF): {dn(k['value'] * 100, 1)} % der Lieferungen pünktlich und vollständig ({dn(k['numerator'])} von {dn(k['denominator'])} Lieferungen, {WINDOW})") if k["value"] is not None else "OTIF nicht messbar",
    liefern="eine Scorecard je strategischem Lieferanten mit Reklamationen je 100 Aufträge, Preisverhalten (B4) und Risiko (A3) sowie ein Gesprächsprotokoll mit Datum je Quartal",
    extra={"otif": k["value"], "otif_n": k["n"]})

# B8 Gewichtetes Zahlungsziel: aus silver.contracts, Monatsreihe ueber die je Monat laufenden Vertraege
ser = monthly("""with months as (select unnest(generate_series(date_trunc('month', (select min(start_date) from silver.contracts)), date_trunc('month', ?::date), interval 1 month))::date m)
                 select m, sum(c.payment_terms_days * c.spend_under_contract_eur) / nullif(sum(c.spend_under_contract_eur), 0) v, count(*) n
                 from months join silver.contracts c on c.start_date <= last_day(m) and (c.end_date is null or c.end_date >= m)
                 where c.payment_terms_days is not null and c.spend_under_contract_eur is not null group by 1 order by 1""", [AS_OF.date()])
w_actual, w_sum, n_pay = con.execute("""select sum(payment_terms_days * spend_actual_12m_eur) / nullif(sum(spend_actual_12m_eur), 0), sum(spend_actual_12m_eur), count(*)
                                         from silver.contracts where status = 'active' and payment_terms_days is not null""").fetchone()
latest = ser[-1] if ser else {"v": None, "n": 0, "m": ""}
add("B8", "direkt", {"v": latest["v"], "einheit": "days", "n": latest["n"], "monat": latest["m"], "text": "laufende Verträge des Monats, gewichtet mit dem Vertragswert je Jahr", "grund": "kein Vertrag mit Zahlungsziel"},
    serie=ser, min_n=1, tag=TAG_DER,
    benutzt=used([], "silver.contracts", "payment_terms_days, spend_under_contract_eur, spend_actual_12m_eur, start_date, end_date", "je Kalendermonat, laufende Verträge",
                 "Summe(Zahlungsziel in Tagen mal Vertragswert je Jahr) durch Summe(Vertragswert je Jahr) über die im Monat laufenden Verträge; daneben zum Stichtag die Gewichtung mit dem tatsächlichen Spend der letzten zwölf Monate"),
    naeherung="Nur Spend mit registriertem Vertrag; indirekter Spend ohne Vertrag trägt kein Zahlungsziel-Feld. Der Skontoverlust und der Kapitalkostensatz von Treasury sind nicht im Modell.",
    extra={"w_actual": fl(w_actual, 2), "w_sum": fl(w_sum, 2), "n_pay": iv(n_pay)},
    bezug="Tage Zahlungsziel")

# ---------------------------------------------------------------- Satz C: Resale
# C1 Einstandspreis gegen UVP: KPI_PUR_LANDED_VS_RRP, Monatsreihe je Eingangsmonat, Ziel minus zwei Punkte gegen die Baseline
k = G["KPI_PUR_LANDED_VS_RRP"]
ser = monthly(f"""select date_trunc('month', received_at) m,
                         sum(landed_cost - coalesce(price_protection_credit_eur, 0)) / nullif(sum(rrp_net_eur), 0) v, count(*) n
                  from {LEDGER} where received_at is not null and landed_cost is not null and rrp_net_eur is not null group by 1 order by 1""")
add("C1", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "text": "rollierend zwölf Monate ab Wareneingang", "grund": k["note"]},
    serie=ser, min_n=k["min_n"] or 1,
    benutzt=used(["KPI_PUR_LANDED_VS_RRP", "KPI_PUR_DISCOUNT_VS_RRP"], k["tables"], "landed_cost, price_protection_credit_eur, rrp_net_eur, received_at", WINDOW,
                 k["formula"] + "; Monatsreihe je Eingangsmonat aus derselben Tabelle; je Marke aus gold.kpi_breakdown"),
    naeherung="landed_cost gleich Einkaufspreis plus Fracht plus Zoll; die Einbuchung (staging) zählt in der Engine als eigene Kostenzeile, nicht im Einstandspreis.",
    extra={"marken": breakdown(gold_bd, "KPI_PUR_LANDED_VS_RRP", "oem"), "rabatt": G["KPI_PUR_DISCOUNT_VS_RRP"]["value"], "rabatt_n": G["KPI_PUR_DISCOUNT_VS_RRP"]["n"]},
    bezug="der UVP netto")

# C2 Preisschutz: KPI_PUR_PRICE_PROTECTION_CAPTURE, in der Simulation unter der Mindeststichprobe
k = G["KPI_PUR_PRICE_PROTECTION_CAPTURE"]
ser = monthly(f"""select date_trunc('month', received_at) m,
                         sum(case when price_protection_status = 'claimed' then coalesce(price_protection_credit_eur, 0) else 0 end)
                           / nullif(sum(case when price_protection_status = 'claimed' then coalesce(price_protection_credit_eur, 0) when price_protection_status = 'missed' then coalesce(price_protection_claimable_eur, 0) else 0 end), 0) v,
                         count(*) n
                  from {LEDGER} where received_at is not null and price_protection_status in ('claimed', 'missed') group by 1 order by 1""")
grund_c2 = ""
if k["status"] != "ok":
    grund_c2 = f"Mindeststichprobe unterschritten: {dn(k['n'])} Seriennummern mit Anspruch unter min_n gleich {dn(k['min_n'])} (Eigner {MIN_N_OWNER}); zur Kenntnis: gutgeschrieben {dn(k['numerator'], 2)} € von {dn(k['denominator'], 2)} € entstandener Ansprüche"
add("C2", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "text": "rollierend zwölf Monate ab Wareneingang", "grund": grund_c2},
    serie=ser, min_n=k["min_n"] or 1,
    benutzt=used(["KPI_PUR_PRICE_PROTECTION_CAPTURE"], k["tables"], "price_protection_status, price_protection_credit_eur, price_protection_claimable_eur, received_at", WINDOW, k["formula"]),
    naeherung="Die zweite Zahl aus C2 (Anteil des Lagerwerts mit Klausel) gibt es nicht separat; je Seriennummer steht nur der Status claimed, missed oder open.",
    extra={"raw": k["raw_value"], "numerator": k["numerator"], "denominator": k["denominator"]},
    bezug="der entstandenen Ansprüche")

# C3 Zuteilung beim Launch: Bausteine vorhanden, keine Kennzahl, keine Baseline vor dem naechsten Launch
n_po, n_gr = con.execute("select (select count(*) from bronze.erp_po_lines), (select count(*) from bronze.erp_goods_receipts)").fetchone()
add("C3", "luecke", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    benutzt=used([], "bronze.erp_po_lines, bronze.erp_goods_receipts, data/catalogue/models.csv", "qty_ordered, Empfangsdatum je Seriennummer, launch_date_de", "",
                 "Bestellmengen, Wareneingänge und Verkaufsstart liegen vor; ein Vorbestellfenster und die Rechnung geliefert durch bestellt in 30 Tagen nach Launch gibt es nicht"),
    grund="Keine Kennzahl im Werkzeug und keine Simulationszahl; die Baseline entsteht beim nächsten echten Launch.",
    ersatz=f"Bausteine im Modell: {dn(n_po)} Bestellzeilen, {dn(n_gr)} Wareneingänge je Seriennummer",
    liefern="bestellt gegen geliefert in 30 Tagen je Variante beim letzten und beim nächsten Launch, mit dem Vorbestellfenster",
    extra={"n_po": iv(n_po), "n_gr": iv(n_gr)})

# C4 Lifecycle-Marge je Modell: KPI_RSLT_CLOSED_PER_DEVICE, Paare Modell mal Laufzeit aus dem Hauptbuch, Monatsreihe je Abschlussmonat
k = G["KPI_RSLT_CLOSED_PER_DEVICE"]
min_c4 = k["min_n"] or 1
pairs = con.execute(f"""select slug, term_months, count(*) n, avg(lifecycle_result_eur) v from {LEDGER}
                        where is_closed and closed_date >= ? and closed_date <= ? and lifecycle_result_eur is not null group by 1, 2""", [WIN_START.date(), AS_OF.date()]).fetchall()
n_pairs = len(pairs)
n_pairs_pos = sum(1 for p in pairs if p[3] is not None and float(p[3]) > 0)
big = [p for p in pairs if p[2] >= min_c4]
big_pos = sum(1 for p in big if p[3] is not None and float(p[3]) > 0)
n_loss = con.execute(f"select count(*) from {LEDGER} where is_closed and closed_date >= ? and closed_date <= ? and lifecycle_result_eur < 0", [WIN_START.date(), AS_OF.date()]).fetchone()[0]
ser = monthly(f"""select date_trunc('month', closed_date) m, avg(lifecycle_result_eur) v, count(*) n from {LEDGER}
                  where is_closed and closed_date is not null and lifecycle_result_eur is not null group by 1 order by 1""")
add("C4", "direkt", {"v": k["value"], "einheit": "eur", "n": k["n"], "text": f"rollierend zwölf Monate; {dn(n_loss)} von {dn(k['n'])} Geräten mit Verlust", "grund": k["note"]},
    serie=ser, min_n=min_c4,
    benutzt=used(["KPI_RSLT_CLOSED_PER_DEVICE", "KPI_TCO_PER_CLOSED_DEVICE"], k["tables"], "lifecycle_result_eur, is_closed, closed_date, slug, term_months", WINDOW,
                 k["formula"] + f"; je Modell und Laufzeit: {dn(n_pairs_pos)} von {dn(n_pairs)} Paaren mit Ergebnis über null, davon Paare ab {dn(min_c4)} Geräten: {dn(big_pos)} von {dn(len(big))}"),
    naeherung="",
    extra={"n_pairs": n_pairs, "n_pairs_pos": n_pairs_pos, "n_big": len(big), "n_big_pos": big_pos, "n_loss": iv(n_loss),
           "laufzeiten": breakdown(gold_bd, "KPI_RSLT_CLOSED_PER_DEVICE", "term_months", 2), "familien": breakdown(gold_bd, "KPI_RSLT_CLOSED_PER_DEVICE", "catalogue_family", 2),
           "tco": G["KPI_TCO_PER_CLOSED_DEVICE"]["value"], "tco_n": G["KPI_TCO_PER_CLOSED_DEVICE"]["n"]},
    bezug="je abgeschlossenem Gerät")

# C5 Restwertprognose: KPI_TOP_RV_FORECAST_ERROR (v0.1) plus die kanalbereinigte Verzerrung der letzten drei vollen Monate
k = V["KPI_TOP_RV_FORECAST_ERROR"]
err = con.execute("""select month, model_family, mape, bias, mape_channel_adjusted, bias_channel_adjusted, n_with_forecast from main.rv_forecast_error_monthly order by month""").df()
err["month"] = pd.to_datetime(err["month"])
star = err[(err["model_family"] == "*") & err["mape"].notna()].sort_values("month")
floor = AS_OF.to_period("M").to_timestamp()
complete = star[star["month"] < floor]
tail3 = complete[complete["bias_channel_adjusted"].notna()].tail(3)
bias3 = fl(tail3["bias_channel_adjusted"].mean()) if len(tail3) else None
last_month = month_str(complete.iloc[-1]["month"]) if len(complete) else ""
fam_last = err[(err["month"] == (complete.iloc[-1]["month"] if len(complete) else None)) & (err["model_family"] != "*")]
fam_rows = [{"k": FAM_DE.get(str(r.model_family), str(r.model_family)), "mape": fl(r.mape), "bias": fl(r.bias), "n": iv(r.n_with_forecast)} for r in fam_last.itertuples(index=False)]
ser = [{"m": month_str(r.month), "v": fl(r.mape), "n": iv(r.n_with_forecast)} for r in star.itertuples(index=False)]
zw5 = by_nr["C5"]["ziel_wert"]["zweite"]
add("C5", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "monat": last_month, "text": "Geschäftssicht, alle Gerätearten, letzter voller Monat", "grund": k["note"]},
    serie=ser, min_n=1,
    zweite={"label": zw5["label"], "v": abs(bias3) if bias3 is not None else None, "signed": bias3, "einheit": "ratio", "n": iv(tail3["n_with_forecast"].sum()) if len(tail3) else 0,
            "ziel_v": zw5["wert"], "richtung": zw5["richtung"], "monate": [month_str(x) for x in tail3["month"]]},
    benutzt=used(["KPI_TOP_RV_FORECAST_ERROR"], "main.rv_forecast_error_monthly", "mape, bias, bias_channel_adjusted, n_with_forecast, model_family", "letzter voller Monat vor dem Stichtag; Verzerrung über die letzten drei vollen Monate",
                 "Fehler gleich mittlerer absoluter Fehler in Prozent des erzielten Preises (MAPE) der Prognose bei Rückgabe; Verzerrung gleich Mittel der kanalbereinigten Verzerrung der drei Monate; je Geräteart aus derselben Tabelle"),
    naeherung="",
    extra={"familien": fam_rows, "ziel_engine": k["target"], "ziel_engine_owner": TARGETS_OWNER},
    bezug="des erzielten Preises")

# C6 Restwert realisiert gegen Annahme: Abweichung aus KPI_RSL_REALISED_VS_RECORD, Veraeusserungsergebnis aus dem Bericht (Monatsregel)
k = G["KPI_RSL_REALISED_VS_RECORD"]
dev = fl(1.0 - k["value"]) if k["value"] is not None else None
rep_path = REPO / "web" / "data" / "report.json"
z2 = z3 = None
if rep_path.exists():
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    z2 = (rep.get("pnl") or {}).get("z2")
    z3 = (rep.get("pnl") or {}).get("z3")
verkauf = fl(z2["sum"] + z3["sum"], 2) if isinstance(z2, dict) and isinstance(z3, dict) else None
ser = monthly(f"""select date_trunc('month', sale_date) m, 1 - sum(resale_gross) / nullif(sum(estimate_rv_of_record), 0) v, count(*) n from {LEDGER}
                  where sale_date is not null and resale_gross is not null and estimate_rv_of_record > 0 and coalesce(resale_channel, '') <> 'as_is' group by 1 order by 1""")
zw6 = by_nr["C6"]["ziel_wert"]["zweite"]
add("C6", "naeherung", {"v": abs(dev) if dev is not None else None, "signed": dev, "einheit": "ratio", "n": k["n"], "text": "Betrag der Abweichung (Annahme minus Erlös) durch Annahme, rollierend zwölf Monate", "grund": k["note"]},
    serie=ser, min_n=k["min_n"] or 1, tag=TAG_DER,
    zweite={"label": zw6["label"], "v": verkauf, "einheit": "eur", "n": iv(z3["n"]) if isinstance(z3, dict) else 0, "ziel_v": zw6["wert"], "richtung": zw6["richtung"],
            "erloes": fl(z2["sum"], 2) if isinstance(z2, dict) else None, "buchwert": fl(abs(z3["sum"]), 2) if isinstance(z3, dict) else None},
    benutzt=used(["KPI_RSL_REALISED_VS_RECORD"], k["tables"] + "; web/data/report.json (Zeilen Restwert und Restbuchwert der Abgänge)", "resale_gross, estimate_rv_of_record, sale_date, resale_channel", WINDOW,
                 k["formula"] + "; Abweichung gleich eins minus dieses Verhältnis; Veräußerungsergebnis gleich Restwert minus Restbuchwert der Abgänge nach der Monatsregel des Reiters Bericht"),
    naeherung="Die Annahme ist die Prognose bei Rückgabe, nicht die Annahme aus Rate und Verbriefung; der Erlös ist brutto vor Kanalgebühr; der Restbuchwert folgt der Monatsregel des Berichts, nicht der Anlagenbuchhaltung.",
    extra={"ratio": k["value"], "kanaele": breakdown(gold_bd, "KPI_RSL_REALISED_VS_RECORD", "resale_channel")},
    bezug="der Annahme")

# C7 Erloes gegen die Marktkurve: Bausteine getrennt, Differenz nicht gebaut
n_anchors = len(pd.read_csv(REPO / "outputs" / "market_anchors.csv"))
n_curves = len(pd.read_csv(REPO / "outputs" / "market_curves.csv"))
add("C7", "luecke", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    benutzt=used([], "outputs/market_anchors.csv, outputs/market_curves.csv, silver.device_ledger", "Preisbelege je Modellreihe und Alter; resale_gross, rrp_net_eur, resale_channel", "",
                 "öffentliche Kurve und eigene Realisierung liegen getrennt vor; die Differenz eigen minus Marktkurve minus Kanalgebühr bildet keine Tabelle"),
    grund="Die Rechnung ist nicht gebaut: keine Tabelle stellt die eigene Realisierung der Marktplatzkurve derselben Modellreihe bei gleichem Alter minus Kanalgebühr gegenüber.",
    ersatz=f"Bausteine im Modell: {dn(n_anchors)} öffentliche Preisbelege, {dn(n_curves)} Kurven (Reiter Realisierung), eigene Verkäufe im Hauptbuch",
    liefern="",
    extra={"n_anchors": n_anchors, "n_curves": n_curves})

# C8 Tage bis Erloes und Lagerdauer: KPI_RSL_DAYS_RETURN_TO_CASH; Buchwert ueber 180 Tage aus der v0.1-Tabelle device_pnl
k = G["KPI_RSL_DAYS_RETURN_TO_CASH"]
bv_old, bv_all, n_stock, n_old = con.execute("""select sum(case when days_in_stock > 180 then book_value else 0 end), sum(book_value), count(*), sum(case when days_in_stock > 180 then 1 else 0 end)
                                                 from main.device_pnl where lifecycle_status = 'in_stock'""").fetchone()
ser = monthly(f"""select date_trunc('month', credited_at) m, median(days_return_to_cash) v, count(*) n from {LEDGER}
                  where credited_at is not null and days_return_to_cash is not null group by 1 order by 1""")
zw8 = by_nr["C8"]["ziel_wert"]["zweite"]
add("C8", "direkt", {"v": k["value"], "einheit": "days", "n": k["n"], "text": "Median über die gutgeschriebenen Verkäufe, rollierend zwölf Monate", "grund": k["note"]},
    serie=ser, min_n=k["min_n"] or 1,
    zweite={"label": zw8["label"], "v": fl(bv_old, 2), "einheit": "eur", "n": iv(n_old), "ziel_v": zw8["wert"], "richtung": zw8["richtung"], "buchwert_lager": fl(bv_all, 2), "n_lager": iv(n_stock)},
    benutzt=used(["KPI_RSL_DAYS_RETURN_TO_CASH", "KPI_INV_AGING_180"], k["tables"] + "; main.device_pnl (v0.1)", "days_return_to_cash, credited_at; book_value, days_in_stock, lifecycle_status", WINDOW,
                 k["formula"] + "; Buchwert der Geräte im Lager mit mehr als 180 Tagen Lagerdauer aus device_pnl zum Stichtag"),
    naeherung="Der Buchwert folgt der linearen Monatsregel der v0.1-Tabelle, nicht der Anlagenbuchhaltung; die Kapitalbindung je Tag ist nicht gerechnet, weil kein Kapitalkostensatz in der Konfiguration steht.",
    extra={"kanaele": breakdown(gold_bd, "KPI_RSL_DAYS_RETURN_TO_CASH", "resale_channel", 1), "aging_share": V["KPI_INV_AGING_180"]["value"], "aging_n": V["KPI_INV_AGING_180"]["n"]},
    bezug="Tage von Rückgabe bis Zahlungseingang")

# C9 Genauigkeit der Zustandspruefung: grade_inspected gegen grade_out aus dem Hauptbuch, Monatsreihe je Monat der Verkaufsfaehigkeit
n_g, n_eq = con.execute(f"select count(*), sum(case when grade_inspected = grade_out then 1 else 0 end) from {LEDGER} where grade_inspected is not null and grade_out is not null and sellable_date >= ? and sellable_date <= ?", [WIN_START.date(), AS_OF.date()]).fetchone()
ser = monthly(f"""select date_trunc('month', sellable_date) m, sum(case when grade_inspected = grade_out then 1 else 0 end) / nullif(count(*), 0) v, count(*) n from {LEDGER}
                  where grade_inspected is not null and grade_out is not null and sellable_date is not null group by 1 order by 1""")
conf = con.execute(f"select grade_inspected, grade_out, count(*) from {LEDGER} where grade_inspected is not null and grade_out is not null and sellable_date >= ? and sellable_date <= ? group by 1, 2 order by 1, 2", [WIN_START.date(), AS_OF.date()]).fetchall()
kk = V["KPI_REC_GRADING_ACCURACY"]
add("C9", "naeherung", {"v": fl(n_eq / n_g) if n_g else None, "einheit": "ratio", "n": iv(n_g), "text": "Rückläufer mit Grade nach Prüfung gleich Grade beim Abgang aus der Aufbereitung, rollierend zwölf Monate", "grund": "kein Rückläufer mit beiden Graden"},
    serie=ser, min_n=1, tag=TAG_DER,
    benutzt=used(["KPI_REC_GRADING_ACCURACY"], LEDGER, "grade_inspected, grade_out, sellable_date", WINDOW,
                 "Anzahl Rückläufer mit grade_inspected gleich grade_out durch alle Rückläufer mit beiden Graden; die Engine-Kennzahl KPI_REC_GRADING_ACCURACY misst eine andere Paarung (vom Kunden deklariert gegen geprüft)"),
    naeherung="Ob ein Grade-Wechsel nach Reparatur nach der Grading-Regel beschlossen war, kennt das Modell nicht; jeder Wechsel zählt als Abweichung. Die Stichprobe von Controlling gibt es nicht.",
    extra={"paare": [{"ein": str(a), "aus": str(b), "n": iv(c)} for a, b, c in conf], "deklariert": kk["value"], "deklariert_num": kk["numerator"], "deklariert_den": kk["denominator"]},
    bezug="der Rückläufer")

# C10 Kanalverlust: Regel nicht gerechnet; die Stellschraube Kanalwahl steht daneben
lev = con.execute("select lever_id, lever_name, eur_fleet_per_year, n_attributed from gold.levers_summary where lever_name = 'channel_choice'").fetchone()
add("C10", "luecke", {"v": None, "einheit": "ratio", "n": None, "text": ""},
    benutzt=used(["KPI_REC_MARGIN_MIX_CHANNEL"], "gold.resale_by_channel_grade, gold.levers_summary, silver.device_ledger", "channel, grade_at_sale, sum_net; eur_fleet_per_year (L03 channel_choice); resale_net, resale_channel, slug, grade_out, sale_date", "",
                 "Netto je Kanal und Grade liegt vor; die Regel bester Kanal je Monat für dasselbe Modell und Grade minus gewählter Kanal, geteilt durch den Gesamterlös, ist nicht gerechnet"),
    grund="Die Rechnung ist nicht gebaut: der beste Kanal je Monat, Modell und Grade wird nicht aus den eigenen Verkäufen bestimmt; die Stellschraube Kanalwahl rechnet gegen die Prognose bei Rückgabe.",
    ersatz=(f"Stellschraube Kanalwahl ({lev[0]}): {dn(lev[2])} € je Jahr über {dn(lev[3])} Geräte, gegen die Prognose bei Rückgabe" if lev else ""),
    liefern="",
    extra={"lever": {"id": lev[0], "eur": fl(lev[2], 2), "n": iv(lev[3])} if lev else None})

con.close()

# ---- Reihenfolge und Zaehlung
ORDER = [k["nr"] for k in rahmen["kpis"]]
rows.sort(key=lambda r: ORDER.index(r["nr"]))
if [r["nr"] for r in rows] != ORDER:
    raise SystemExit("kpis.json: nicht jede Kennzahl des Rahmens hat eine Zeile")
counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("erfuellt", "gelb", "verfehlt", "nicht_messbar", "nicht_im_werkzeug")}
SETS = [{"key": "A", "label": "Satz A, alle Rollen"}, {"key": "B", "label": "Satz B, Indirekt"}, {"key": "C", "label": "Satz C, Resale"}]

data = {
    "today": TODAY, "as_of": AS_OF.date().isoformat(), "window": WINDOW,
    "fassung": rahmen["fassung"], "stand": rahmen["stand"], "quelle": rahmen["quelle"], "tausch_hinweis": rahmen["tausch_hinweis"],
    "gelb_band": GELB_BAND, "baseline_months": BASELINE_MONTHS, "spark_months": SPARK_MONTHS, "min_n_owner": MIN_N_OWNER, "targets_owner": TARGETS_OWNER,
    "counts": counts, "n_kpis": len(rows), "sets": SETS, "rows": rows,
    "engine_layers": {"gold": {"n": int(len(gold)), "pages": sorted(set(gold["page"].astype(str)))}, "v01": {"n": int(len(v01)), "areas": sorted(set(v01["area"].astype(str)))}},
}
text = de_roles(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
if EN_DASH in text or EM_DASH in text:
    raise SystemExit("kpis.json: Strich in den Daten")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(text + "\n", encoding="utf-8", newline="\n")
print("kpis.json:", len(rows), "Kennzahlen,", counts, "Stichtag", data["as_of"])
