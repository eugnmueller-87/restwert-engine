"""Kennzahlen: data/kpis.json fuer den Reiter "KPIs" (Bereich Analytics) der Restwert Engine, als schlanker Tracker.

Der Reiter zeigt je Kennzahl des KPI-Rahmens (web/tools/gen/kpi_rahmen.json, Fassung 4, kuratiert): Ziel, Ist,
Fortschritt, Bis, Beitraege, Status. Sonst nichts; Formel, Zaehlregel, Quelle, Eigner und Pruefer stehen im Rahmen,
auf den jede Zeile verlinkt (Feld url des Rahmens plus #<nr>). Umbau am 17.09.2026 auf Eugens Ansage: kein
Erklaertext, alles gerade runter, ein Zyklus (Quartal, Halbjahr, Jahr) und die Frage, wie viel Prozent des Wegs
geschafft sind.

Quellen: die 14 gold-KPIs (outputs/gold__kpi_values.csv), die 20 KPIs der eingefrorenen v0.1-Registry
(outputs/kpi_values.csv), Ziele und Mindeststichproben aus config/kpi_targets.yaml, Monatsreihen und Baseline
(Mittel der ersten drei Monate mit Daten) aus data/restwert.duckdb mit denselben Formeln wie die gold-KPIs, der
Zyklus aus config/performance_cycle.yaml, die Rollen aus config/owners.yaml.

Satz D, ESG (seit Fassung 4, 18.09.2026), sechs Kennzahlen D1 bis D6:
  D1  Zweites Leben: Abgaenge im rollierenden Zwoelfmonatsfenster aus silver.device_ledger, verkauft (heute ohne
      Zweitzyklus-Kanal, also nur verkauft) gegen verschrottet; Monatsreihe je Abgangsmonat (closed_date)
  D2  CO2 vermieden: D1-Zaehler je Familie mal Vermeidungsfaktor aus config/esg.yaml (kg CO2e je Geraet, mit Quelle),
      durch 1.000 in t CO2e; eine Familie ohne Faktor (Laptop, tag frage) bleibt n/a, die Summe traegt den Hinweis
      "ohne Laptops"; Ziel referenz ohne Zahl, also Status nicht_messbar mit sichtbarem Ist
  D3  Reparaturquote: bronze.sd_tickets, nur abgeschlossene (closed_at), resolution repair gegen replace
  D4  Nutzungsdauer: Monate von contract_start bis closed_date je Abgang, Mittel je Familie und gesamt
  D5, D6  nicht_im_werkzeug (Support-Ende je Modell im Katalog, Feed mit Bewertungsnachweisen fehlen)

Status je Kennzahl, deterministisch und nie geraten (unveraendert seit dem 17.09.2026, Version 3):
  erfuellt            Ist erreicht das Ziel
  gelb                Ist liegt auf der schlechteren Seite innerhalb GELB_BAND des Ziels (bei Zielen gegen die
                      Baseline: innerhalb GELB_BAND der geforderten Bewegung)
  verfehlt            darueber hinaus
  nicht_messbar       Nenner null, Mindeststichprobe unterschritten, oder der Zaehler der Kennzahl ist im Modell
                      nicht gebildet (das fehlende Feld steht dabei, kurz)
  nicht_im_werkzeug   die Daten existieren in der Engine nicht (die fehlende Quelle steht dabei, kurz)
Ein KPI mit status not_measurable in der Engine kommt mit value None an und wird nie als 0 gezeigt (records.py).

Fortschritt je Kennzahl (0 bis 1): der Anteil des Wegs von der Baseline zum Ziel; bei Zielen von 100 % oder 0 der
Anteil am Ziel; erfuellt heisst 1. Soll heute: die lineare Erwartung zwischen Zyklusstart und Bis, je Zyklus.
Bis: Zyklusstart plus Horizont des Rahmens (Feld horizont, aus dem Zieltext, sonst aus Echt ab), gerundet auf das
Ende des Zyklusabschnitts, in dem das Datum liegt.

Beitragsbuch je Kennzahl aus Ereignissen mit Akteur (Datum, Rolle, Massnahme, Wirkung, Beleg-ID, Stufe):
  B1  main.indirect_spend, bestaetigte oder unbestaetigte Preisreduktionen (saving_type hard_price_reduction),
      Rolle ueber die Kategorie; Stufe realisiert, wenn Controlling bestaetigt hat, sonst verhandelt
  C2  bronze.erp_supplier_invoices, Zeilen line_kind price_protection_credit, Rolle ueber die Geraetefamilie der
      Bestellzeile im Hauptbuch; Stufe realisiert (gutgeschrieben)
  C4  main.decision_log, Regel R01 (Reparatur je Geraet), Rolle ueber die Familie in inputs_json; Stufe identifiziert
  C10 main.decision_log, Regel R02 (Kanalwahl je Geraet), Rolle wie C4; Stufe identifiziert
  A2  silver.contracts, Vertraege mit den sieben vorhandenen Pflichtfeldern, Rolle ueber Kategorie oder OEM;
      Stufe identifiziert (das achte Feld fehlt im Register, siehe Status)
  D1, D2  bronze.rf_work_orders im Fenster mit Ergebnis sellable oder as_is (kein scrap), Rolle ueber die Familie
      der Seriennummer im Hauptbuch; Stufe realisiert (Auftrag abgeschlossen); Beleg work_order_id
  D3, D4  bronze.sd_tickets im Fenster, abgeschlossen mit resolution repair, Rolle ueber die Familie; Stufe realisiert;
      Beleg ticket_id. Nur Ereignisse mit Beleg-ID; ohne Beleg wird nichts gebucht.
Wirkung nur, wo ein Betrag in der Quelle steht; sonst leer. Keine Zahl je Person: nur Rollencodes und Team.

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
BOOK_ROWS = 12            # Beitragszeilen je Kennzahl in der Datei; der Rest wird gezaehlt
BELEG_CHARS = 12          # Kurzform der 32-stelligen decision_id in der Beleg-Spalte
PERIODS = [("q", "Quartal", 3), ("h", "Halbjahr", 6), ("j", "Jahr", 12)]
STUFE_REAL, STUFE_VERH, STUFE_IDENT = "realisiert", "verhandelt", "identifiziert"
# deutsche Beschriftung der Datenwerte, dieselbe wie auf den anderen Reitern (make_report_data.py, make_lake_data.py)
CH_DE = {"employee_buyout": "Mitarbeiterkauf", "marketplace": "Marktplatz", "b2b_wholesale": "Großhandel", "as_is": "Verkauf ohne Aufbereitung"}
CAT_DE = {"hardware": "Hardware", "logistics": "Logistik", "repair": "Reparatur", "refurbishment": "Aufbereitung", "software": "Software",
          "marketing": "Marketing", "facilities": "Gebäude und Betrieb", "consulting": "Beratung", "packaging": "Verpackung",
          "connectivity": "Mobilfunk und Konnektivität", "resale_channel": "Verkaufskanal", "financing": "Finanzierung",
          "security_software": "Sicherheitssoftware", "rental": "Miete"}
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


def de_date(v):
    return pd.Timestamp(v).strftime("%d.%m.%Y")


def dn(x, nd=0):
    """Zahl deutsch (Tausenderpunkt, Komma) fuer Texte, die der Generator selbst formuliert."""
    if x is None:
        return "n/a"
    s = f"{float(x):,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def clamp01(x):
    return None if x is None else max(0.0, min(1.0, float(x)))


# ---- der Rahmen (kuratiert)
rahmen = json.load(open(RAHMEN, encoding="utf-8"))
if EN_DASH in json.dumps(rahmen, ensure_ascii=False) or EM_DASH in json.dumps(rahmen, ensure_ascii=False):
    raise SystemExit("kpi_rahmen.json: Strich im Rahmen")
by_nr = {k["nr"]: k for k in rahmen["kpis"]}
if not str(rahmen.get("url", "")).startswith("https://"):
    raise SystemExit("kpi_rahmen.json: Feld url fehlt (Adresse des Rahmens fuer den Link Definition)")

# ---- Ziele, Mindeststichproben, Eigner
targets = yaml.safe_load(open(REPO / "config" / "kpi_targets.yaml", encoding="utf-8")) or {}
MIN_N = {str(k): int(v) for k, v in (targets.get("min_n") or {}).items()}
MIN_N_OWNER = own(targets.get("min_n_owner", ""))

# ---- der Zyklus des Hauses
cyc = yaml.safe_load(open(REPO / "config" / "performance_cycle.yaml", encoding="utf-8")) or {}
CYCLE_START = pd.Timestamp(str(cyc.get("start_date", ""))).normalize()
CYCLE_DEFAULT = str(cyc.get("cycle", "q"))
if CYCLE_DEFAULT not in {p[0] for p in PERIODS}:
    raise SystemExit("performance_cycle.yaml: cycle muss q, h oder j sein")
CYCLE_OWNER = own(cyc.get("owner", ""))
TODAY_TS = pd.Timestamp(TODAY).normalize()

# ---- die ESG-Faktoren (D2): kg CO2e je Geraet mit zweitem Leben, je Familie, mit Quelle; null heisst n/a, nie 0
esg = yaml.safe_load(open(REPO / "config" / "esg.yaml", encoding="utf-8")) or {}
ESG_OWNER = own(esg.get("owner", ""))
ESG_FACTORS = {}       # Familie -> {value, source, tag, note}
for fam, f in (esg.get("factors") or {}).items():
    f = f or {}
    if f.get("value") is not None and not str(f.get("source", "")).strip():
        raise SystemExit(f"esg.yaml: Faktor {fam} ohne Quelle")
    ESG_FACTORS[str(fam)] = {"value": fl(f.get("value")), "source": str(f.get("source", "")), "tag": str(f.get("tag", "")), "note": str(f.get("note", ""))}
FAM_DE = {"Smartphone": "Smartphones", "Tablet": "Tablets", "Laptop": "Laptops"}

# ---- die Rollen
owners = yaml.safe_load(open(REPO / "config" / "owners.yaml", encoding="utf-8")) or {}
ROLES = []              # [{code, label, scope}] in der Reihenfolge der Datei, Team zuletzt
CAT_ROLE, FAM_ROLE = {}, {}
for code, r in (owners.get("roles") or {}).items():
    ROLES.append({"code": str(code), "label": str(r.get("label", code)), "scope": str(r.get("scope", ""))})
    for c in r.get("categories") or []:
        CAT_ROLE[str(c)] = str(code)
    for fam in r.get("families") or []:
        FAM_ROLE[str(fam)] = str(code)
TEAM = str((owners.get("team") or {}).get("label", "Team"))
TEAM_CATS = {str(c) for c in ((owners.get("team") or {}).get("categories") or [])}
ROLES.append({"code": "TEAM", "label": TEAM, "scope": "ohne Zuordnung zu einer Rolle: " + ", ".join(sorted(CAT_DE.get(c, c) for c in TEAM_CATS))})
ROLE_LABEL = {r["code"]: r["label"] for r in ROLES}
ROLE_ORDER = {r["code"]: i for i, r in enumerate(ROLES)}
UNMAPPED = set()


def role_of_category(cat):
    c = str(cat or "")
    if c in CAT_ROLE:
        return CAT_ROLE[c]
    if c not in TEAM_CATS:
        UNMAPPED.add(c)
    return "TEAM"


def role_of_family(fam):
    f = str(fam or "")
    if f in FAM_ROLE:
        return FAM_ROLE[f]
    UNMAPPED.add(f)
    return "TEAM"


# ---- die Engine-KPIs
gold = pd.read_csv(REPO / "outputs" / "gold__kpi_values.csv")
v01 = pd.read_csv(REPO / "outputs" / "kpi_values.csv")
AS_OF = pd.Timestamp(gold["as_of"].iloc[0])
WIN_START = (AS_OF - pd.DateOffset(months=12) + pd.Timedelta(days=1)).normalize()
WINDOW = f"{de_date(WIN_START)} bis {de_date(AS_OF)}"


def kpi_row(frame, kpi_id, layer):
    r = frame[frame["kpi_id"] == kpi_id]
    if not len(r):
        raise SystemExit(f"{layer}: {kpi_id} fehlt in der Wertetabelle")
    r = r.iloc[0]
    status = str(r["status"])
    return {
        "id": kpi_id, "layer": layer, "value": fl(r["value"]) if status == "ok" else None, "raw_value": fl(r["value"]),
        "numerator": fl(r["numerator"], 2), "denominator": fl(r["denominator"], 2), "n": iv(r["n"]),
        "unit": str(r["unit"]), "status": status, "min_n": MIN_N.get(kpi_id),
    }


G = {k: kpi_row(gold, k, "gold") for k in gold["kpi_id"]}
V = {k: kpi_row(v01, k, "v0.1") for k in v01["kpi_id"]}

# ---- DuckDB: Monatsreihen, die Zaehlungen, die kein KPI schon liefert, und die Ereignisse mit Akteur
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


def progress_of(status, v, t, base, zw):
    """(anteil 0..1, art): 'weg' gleich Anteil des Wegs von der Baseline zum Ziel, 'anteil' gleich Anteil am Ziel."""
    if status == "erfuellt":
        return 1.0, "weg" if base else "anteil"
    if v is None or t is None:
        return None, ""
    richtung = zw.get("richtung")
    share_only = zw.get("art") == "absolut" and (t == 0 or (zw.get("einheit") == "ratio" and t >= 1.0))
    b = base.get("v") if base else None
    base_wrong_side = b is not None and ((richtung == "up" and b < t) or (richtung == "down" and b > t))
    if not share_only and base_wrong_side:
        return clamp01((v - b) / (t - b)), "weg"
    if t == 0:
        return 0.0, "anteil"
    if richtung == "up":
        return clamp01(v / t), "anteil"
    return (clamp01(t / v) if v else 1.0), "anteil"


def segment_end(date, months):
    """Ende des Zyklusabschnitts (3, 6 oder 12 Monate, kalendarisch), in dem date liegt, und sein Name."""
    y, m = date.year, date.month
    idx = (m - 1) // months
    last_month = (idx + 1) * months
    end = (pd.Timestamp(year=y, month=last_month, day=1) + pd.offsets.MonthEnd(0)).normalize()
    if months == 12:
        label = str(y)
    elif months == 6:
        label = f"H{idx + 1} {y}"
    else:
        label = f"Q{idx + 1} {y}"
    return end, label


def horizon_of(k):
    """Bis und Soll heute je Zyklus aus dem Horizont des Rahmens; ohne Monate (naechster Launch) nur der Text."""
    hz = k.get("horizont") or {}
    months = hz.get("monate")
    out = {"monate": months, "quelle": str(hz.get("quelle", "")), "datum": None, "bis": {}, "soll": {}}
    if months is None:
        for key, _label, _n in PERIODS:
            out["bis"][key] = {"label": str(k.get("echt_ab", "")), "ende": None}
            out["soll"][key] = None
        return out
    date = (CYCLE_START + pd.DateOffset(months=int(months))).normalize()
    out["datum"] = date.date().isoformat()
    for key, _label, n in PERIODS:
        end, label = segment_end(date, n)
        span = (end - CYCLE_START).days
        soll = clamp01((TODAY_TS - CYCLE_START).days / span) if span > 0 else None
        out["bis"][key] = {"label": label, "ende": end.date().isoformat()}
        out["soll"][key] = fl(soll)
    return out


rows = []
BOOK = {}   # nr -> [ereignisse]


def add(nr, modus, ist, serie=None, min_n=1, fehlt="", zweite=None):
    k = by_nr[nr]
    zw = dict(k["ziel_wert"])
    base = baseline_of(serie or [], min_n) if serie else None
    t, delta = resolve_target(zw, base)
    zw["berechnet"] = t
    status = None
    if modus == "keine":
        status = "nicht_im_werkzeug"
    elif modus == "luecke":
        status = "nicht_messbar"
    else:
        v = ist.get("v")
        if v is None:
            status = "nicht_messbar"
            fehlt = fehlt or ist.get("grund") or "kein Wert"
        elif t is None:
            status = "nicht_messbar"
            fehlt = fehlt or ("Baseline: keine drei Monate mit mindestens " + dn(min_n) + " Beobachtungen" if zw.get("art") in ("baseline_delta", "baseline_faktor") else "Ziel ohne Zahl: " + str(zw.get("referenz_kurz", "")))
        else:
            status = judge(v, t, zw["richtung"], delta, bool(zw.get("strikt")))
            parts = [status]
            if zweite is not None:
                z2 = zweite
                z2["status"] = judge(z2.get("v"), z2.get("ziel_v"), z2.get("richtung"), None, False) if z2.get("v") is not None else None
                if z2["status"] is not None:
                    parts.append(z2["status"])
            status = max(parts, key=lambda s: RANK[s])
    p, p_art = progress_of(status, ist.get("v"), t, base, zw) if status in RANK else (None, "")
    if p is not None and zweite is not None and zweite.get("v") is not None and zweite.get("status") is not None:
        # Doppelziel: der Fortschritt ist der kleinere der beiden Teilfortschritte, sonst stuende 100 % neben verfehlt
        p2, _ = progress_of(zweite["status"], zweite["v"], zweite["ziel_v"], None, {"art": "absolut", "richtung": zweite["richtung"], "einheit": zweite["einheit"]})
        if p2 is not None:
            p = min(p, p2)
    rows.append({
        "nr": nr, "satz": nr[0], "name": k["name"], "beispiel": bool(k["beispiel"]), "takt": k["takt"], "echt_ab": k["echt_ab"],
        "eigner": k["eigner"], "pruefer": k["pruefer"], "status": status, "fehlt": fehlt,
        "ist": {"v": ist.get("v"), "einheit": ist.get("einheit"), "n": ist.get("n"), "signed": ist.get("signed"),
                "hinweis": ist.get("hinweis") or "", "je_familie": ist.get("je_familie") or []},
        "zweite": zweite, "ziel_wert": zw, "baseline": base, "fortschritt": {"v": fl(p), "art": p_art},
        "horizont": horizon_of(k), "definition_url": rahmen["url"] + "#" + nr.lower(), "min_n": min_n,
    })


LEDGER = "silver.device_ledger"

# ---------------------------------------------------------------- Satz A: alle Rollen
# A1 Spend unter Management: Naeherung ueber KPI_IND_SPEND_UNDER_MGMT (v0.1), Monatsreihe aus indirect_spend
k = V["KPI_IND_SPEND_UNDER_MGMT"]
ser = monthly("""select date_trunc('month', invoice_date) m,
                        sum(case when has_po or has_contract then amount else 0 end) / nullif(sum(amount), 0) v, count(*) n
                 from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
add("A1", "naeherung", {"v": k["value"], "einheit": "ratio", "n": k["n"]}, serie=ser, min_n=1)

# A2 Vertragsregister vollstaendig: sieben von acht Feldern zaehlbar, das achte (Eigner im Team) fehlt im Register
SEVEN = """counterparty_name is not null and category is not null and spend_under_contract_eur is not null
           and end_date is not null and notice_deadline is not null and auto_renewal is not null and price_protection is not null"""
n_ctr = con.execute("select count(*) from silver.contracts").fetchone()[0]
add("A2", "luecke", {"v": None, "einheit": "ratio", "n": iv(n_ctr)}, fehlt="Feld Eigner im Team und Wertschwelle")

# A3, A4: nicht im Werkzeug
add("A3", "keine", {"v": None, "einheit": "ratio", "n": None}, fehlt="Prüfprotokoll je Lieferant mit Datum")
add("A4", "keine", {"v": None, "einheit": "count", "n": None}, fehlt="Speicher des Teams mit Abnahme")

# ---------------------------------------------------------------- Satz B: Indirekt
# B1 Hard Savings validiert: bestaetigte Preisreduktionen gegen den gesamten indirekten Spend, rollierend zwoelf Monate
HARD = "saving_confirmed_by_controlling and coalesce(saving_type, 'hard_price_reduction') = 'hard_price_reduction'"
hard, spend, n_inv = con.execute(f"""select sum(case when {HARD} then coalesce(saving, 0) else 0 end), sum(amount), count(*)
                                     from main.indirect_spend where invoice_date >= ? and invoice_date <= ?""", [WIN_START.date(), AS_OF.date()]).fetchone()
ser = monthly(f"""select date_trunc('month', invoice_date) m,
                         sum(case when {HARD} then coalesce(saving, 0) else 0 end) / nullif(sum(amount), 0) v, count(*) n
                  from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
add("B1", "naeherung", {"v": fl(hard / spend) if spend else None, "einheit": "ratio", "n": iv(n_inv), "grund": "Spend null"}, serie=ser, min_n=1)

# B3 Verlaengerungen mit Vorlauf: Kalender vorhanden, Verhandlungsakte nicht
add("B3", "luecke", {"v": None, "einheit": "ratio", "n": None}, fehlt="Datum der ersten Verhandlungshandlung")

# B4: nicht im Werkzeug
add("B4", "keine", {"v": None, "einheit": "ratio", "n": None}, fehlt="Vertragsversionen je Lieferant und VPI")

# B5 Stille Verlaengerungen: Kandidaten zaehlbar, Entscheidung nicht
add("B5", "luecke", {"v": None, "einheit": "count", "n": iv(n_ctr)}, fehlt="Feld Entscheidung mit Datum und Eigner")

# B6 Maverick-Quote: Naeherung ueber KPI_IND_MAVERICK_SHARE, Monatsreihe aus indirect_spend, Ziel halbiert gegen die Baseline
k = V["KPI_IND_MAVERICK_SHARE"]
ser = monthly("""select date_trunc('month', invoice_date) m,
                        sum(case when not has_po and not has_contract then amount else 0 end) / nullif(sum(amount), 0) v, count(*) n
                 from main.indirect_spend where invoice_date is not null group by 1 order by 1""")
add("B6", "naeherung", {"v": k["value"], "einheit": "ratio", "n": k["n"]}, serie=ser, min_n=1)

# B7 Scorecard: nur OTIF gerechnet, nur Hardware
add("B7", "luecke", {"v": None, "einheit": "ratio", "n": None}, fehlt="Scorecard und Gesprächsprotokoll je Lieferant")

# B8 Gewichtetes Zahlungsziel: aus silver.contracts, Monatsreihe ueber die je Monat laufenden Vertraege
ser = monthly("""with months as (select unnest(generate_series(date_trunc('month', (select min(start_date) from silver.contracts)), date_trunc('month', ?::date), interval 1 month))::date m)
                 select m, sum(c.payment_terms_days * c.spend_under_contract_eur) / nullif(sum(c.spend_under_contract_eur), 0) v, count(*) n
                 from months join silver.contracts c on c.start_date <= last_day(m) and (c.end_date is null or c.end_date >= m)
                 where c.payment_terms_days is not null and c.spend_under_contract_eur is not null group by 1 order by 1""", [AS_OF.date()])
latest = ser[-1] if ser else {"v": None, "n": 0, "m": ""}
add("B8", "direkt", {"v": latest["v"], "einheit": "days", "n": latest["n"], "grund": "kein Vertrag mit Zahlungsziel"}, serie=ser, min_n=1)

# ---------------------------------------------------------------- Satz C: Resale
# C1 Einstandspreis gegen UVP: KPI_PUR_LANDED_VS_RRP, Monatsreihe je Eingangsmonat, Ziel minus zwei Punkte gegen die Baseline
k = G["KPI_PUR_LANDED_VS_RRP"]
ser = monthly(f"""select date_trunc('month', received_at) m,
                         sum(landed_cost - coalesce(price_protection_credit_eur, 0)) / nullif(sum(rrp_net_eur), 0) v, count(*) n
                  from {LEDGER} where received_at is not null and landed_cost is not null and rrp_net_eur is not null group by 1 order by 1""")
add("C1", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "grund": "unter der Mindeststichprobe"}, serie=ser, min_n=k["min_n"] or 1)

# C2 Preisschutz: KPI_PUR_PRICE_PROTECTION_CAPTURE, in der Simulation unter der Mindeststichprobe
k = G["KPI_PUR_PRICE_PROTECTION_CAPTURE"]
ser = monthly(f"""select date_trunc('month', received_at) m,
                         sum(case when price_protection_status = 'claimed' then coalesce(price_protection_credit_eur, 0) else 0 end)
                           / nullif(sum(case when price_protection_status = 'claimed' then coalesce(price_protection_credit_eur, 0) when price_protection_status = 'missed' then coalesce(price_protection_claimable_eur, 0) else 0 end), 0) v,
                         count(*) n
                  from {LEDGER} where received_at is not null and price_protection_status in ('claimed', 'missed') group by 1 order by 1""")
grund_c2 = f"Mindeststichprobe: {dn(k['n'])} von {dn(k['min_n'])} Seriennummern" if k["status"] != "ok" else ""
add("C2", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "grund": grund_c2}, serie=ser, min_n=k["min_n"] or 1)

# C3 Zuteilung beim Launch: Bausteine vorhanden, keine Kennzahl, keine Baseline vor dem naechsten Launch
add("C3", "luecke", {"v": None, "einheit": "ratio", "n": None}, fehlt="Vorbestellfenster, geliefert gegen bestellt")

# C4 Lifecycle-Marge je Modell: KPI_RSLT_CLOSED_PER_DEVICE, Monatsreihe je Abschlussmonat
k = G["KPI_RSLT_CLOSED_PER_DEVICE"]
ser = monthly(f"""select date_trunc('month', closed_date) m, avg(lifecycle_result_eur) v, count(*) n from {LEDGER}
                  where is_closed and closed_date is not null and lifecycle_result_eur is not null group by 1 order by 1""")
add("C4", "direkt", {"v": k["value"], "einheit": "eur", "n": k["n"], "grund": "unter der Mindeststichprobe"}, serie=ser, min_n=k["min_n"] or 1)

# C5 Restwertprognose: KPI_TOP_RV_FORECAST_ERROR (v0.1) plus die kanalbereinigte Verzerrung der letzten drei vollen Monate
k = V["KPI_TOP_RV_FORECAST_ERROR"]
err = con.execute("""select month, model_family, mape, bias_channel_adjusted, n_with_forecast from main.rv_forecast_error_monthly order by month""").df()
err["month"] = pd.to_datetime(err["month"])
star = err[(err["model_family"] == "*") & err["mape"].notna()].sort_values("month")
floor = AS_OF.to_period("M").to_timestamp()
complete = star[star["month"] < floor]
tail3 = complete[complete["bias_channel_adjusted"].notna()].tail(3)
bias3 = fl(tail3["bias_channel_adjusted"].mean()) if len(tail3) else None
ser = [{"m": month_str(r.month), "v": fl(r.mape), "n": iv(r.n_with_forecast)} for r in star.itertuples(index=False)]
zw5 = by_nr["C5"]["ziel_wert"]["zweite"]
add("C5", "direkt", {"v": k["value"], "einheit": "ratio", "n": k["n"], "grund": "kein voller Monat mit Prognose"}, serie=ser, min_n=1,
    zweite={"label": zw5["label_kurz"], "v": abs(bias3) if bias3 is not None else None, "signed": bias3, "einheit": "ratio", "ziel_v": zw5["wert"], "richtung": zw5["richtung"]})

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
add("C6", "naeherung", {"v": abs(dev) if dev is not None else None, "signed": dev, "einheit": "ratio", "n": k["n"], "grund": "unter der Mindeststichprobe"}, serie=ser, min_n=k["min_n"] or 1,
    zweite={"label": zw6["label_kurz"], "v": verkauf, "einheit": "eur", "ziel_v": zw6["wert"], "richtung": zw6["richtung"]})

# C7 Erloes gegen die Marktkurve: Bausteine getrennt, Differenz nicht gebaut
add("C7", "luecke", {"v": None, "einheit": "ratio", "n": None}, fehlt="Rechnung eigener Erlös gegen Marktkurve")

# C8 Tage bis Erloes und Lagerdauer: KPI_RSL_DAYS_RETURN_TO_CASH; Buchwert ueber 180 Tage aus der v0.1-Tabelle device_pnl
k = G["KPI_RSL_DAYS_RETURN_TO_CASH"]
bv_old = con.execute("select sum(case when days_in_stock > 180 then book_value else 0 end) from main.device_pnl where lifecycle_status = 'in_stock'").fetchone()[0]
ser = monthly(f"""select date_trunc('month', credited_at) m, median(days_return_to_cash) v, count(*) n from {LEDGER}
                  where credited_at is not null and days_return_to_cash is not null group by 1 order by 1""")
zw8 = by_nr["C8"]["ziel_wert"]["zweite"]
add("C8", "direkt", {"v": k["value"], "einheit": "days", "n": k["n"], "grund": "unter der Mindeststichprobe"}, serie=ser, min_n=k["min_n"] or 1,
    zweite={"label": zw8["label_kurz"], "v": fl(bv_old, 2), "einheit": "eur", "ziel_v": zw8["wert"], "richtung": zw8["richtung"]})

# C9 Genauigkeit der Zustandspruefung: grade_inspected gegen grade_out aus dem Hauptbuch, Monatsreihe je Monat der Verkaufsfaehigkeit
n_g, n_eq = con.execute(f"select count(*), sum(case when grade_inspected = grade_out then 1 else 0 end) from {LEDGER} where grade_inspected is not null and grade_out is not null and sellable_date >= ? and sellable_date <= ?", [WIN_START.date(), AS_OF.date()]).fetchone()
ser = monthly(f"""select date_trunc('month', sellable_date) m, sum(case when grade_inspected = grade_out then 1 else 0 end) / nullif(count(*), 0) v, count(*) n from {LEDGER}
                  where grade_inspected is not null and grade_out is not null and sellable_date is not null group by 1 order by 1""")
add("C9", "naeherung", {"v": fl(n_eq / n_g) if n_g else None, "einheit": "ratio", "n": iv(n_g), "grund": "kein Rückläufer mit beiden Graden"}, serie=ser, min_n=1)

# C10 Kanalverlust: Regel nicht gerechnet
add("C10", "luecke", {"v": None, "einheit": "ratio", "n": None}, fehlt="Regel bester Kanal je Monat und Grade")

# ---------------------------------------------------------------- Satz D: ESG
# D1 Zweites Leben: Abgaenge im Fenster, verkauft gegen verschrottet (ohne Zweitzyklus-Kanal heisst zweites Leben heute verkauft)
ABGANG = f"from {LEDGER} where lifecycle_status in ('sold', 'scrapped') and closed_date is not null"
n_ab, n_zl = con.execute(f"select count(*), sum(case when lifecycle_status = 'sold' then 1 else 0 end) {ABGANG} and closed_date >= ? and closed_date <= ?", [WIN_START.date(), AS_OF.date()]).fetchone()
ser = monthly(f"""select date_trunc('month', closed_date) m, sum(case when lifecycle_status = 'sold' then 1 else 0 end) / nullif(count(*), 0) v, count(*) n
                  {ABGANG} group by 1 order by 1""")
add("D1", "direkt", {"v": fl(n_zl / n_ab) if n_ab else None, "einheit": "ratio", "n": iv(n_ab), "grund": "kein Abgang im Fenster"}, serie=ser, min_n=1)

# D2 CO2 vermieden: D1-Zaehler je Familie mal Faktor aus esg.yaml; Familie ohne Faktor n/a, die Summe ohne sie
zl_fam = dict(con.execute(f"select catalogue_family, count(*) {ABGANG} and lifecycle_status = 'sold' and closed_date >= ? and closed_date <= ? group by 1", [WIN_START.date(), AS_OF.date()]).fetchall())
je_fam, t_sum, ohne, n_d2 = [], 0.0, [], 0
for fam in sorted(set(ESG_FACTORS) | {str(k) for k in zl_fam}, key=lambda x: (x not in ESG_FACTORS, x)):
    n_f = iv(zl_fam.get(fam, 0))
    fac = ESG_FACTORS.get(fam, {})
    if fac.get("value") is None:
        je_fam.append({"familie": fam, "n": n_f, "faktor": None, "v": None, "tag": fac.get("tag", "frage"), "quelle": fac.get("source", "")})
        if n_f:
            ohne.append(FAM_DE.get(fam, fam))
        continue
    t_f = n_f * fac["value"] / 1000.0
    t_sum += t_f
    n_d2 += n_f
    je_fam.append({"familie": fam, "n": n_f, "faktor": fac["value"], "v": fl(t_f, 3), "tag": fac["tag"], "quelle": fac["source"]})
fac_sql = " ".join(f"when catalogue_family = '{f}' then {v['value']}" for f, v in ESG_FACTORS.items() if v.get("value") is not None)
ser = monthly(f"""select date_trunc('month', closed_date) m, sum(case {fac_sql} else 0 end) / 1000.0 v, count(*) n
                  {ABGANG} and lifecycle_status = 'sold' group by 1 order by 1""") if fac_sql else []
add("D2", "naeherung", {"v": fl(t_sum, 3) if n_d2 else None, "einheit": "t_co2e", "n": n_d2, "grund": "kein Gerät mit zweitem Leben und Faktor",
                        "hinweis": ("ohne " + " und ".join(ohne) + ": Faktor fehlt") if ohne else "", "je_familie": je_fam}, serie=ser, min_n=1)

# D3 Reparaturquote bei Defekt: abgeschlossene Tickets im Fenster, repair gegen replace
TICKETS = "from bronze.sd_tickets where closed_at is not null and resolution in ('repair', 'replace')"
n_t, n_rep = con.execute(f"select count(*), sum(case when resolution = 'repair' then 1 else 0 end) {TICKETS} and closed_at >= ? and closed_at < ?", [WIN_START.date(), (AS_OF + pd.Timedelta(days=1)).date()]).fetchone()
ser = monthly(f"""select date_trunc('month', closed_at) m, sum(case when resolution = 'repair' then 1 else 0 end) / nullif(count(*), 0) v, count(*) n
                  {TICKETS} group by 1 order by 1""")
add("D3", "direkt", {"v": fl(n_rep / n_t) if n_t else None, "einheit": "ratio", "n": iv(n_t), "grund": "kein abgeschlossener Defektfall im Fenster"}, serie=ser, min_n=1)

# D4 Nutzungsdauer je Geraet: Monate von der ersten Vermietung bis zum Abgang, Mittel je Familie und gesamt
MONATE = "datediff('day', contract_start, closed_date) / 30.4375"
n_d4, m_d4 = con.execute(f"select count(*), avg({MONATE}) {ABGANG} and contract_start is not null and closed_date >= ? and closed_date <= ?", [WIN_START.date(), AS_OF.date()]).fetchone()
je_fam4 = [{"familie": str(f), "n": iv(n), "v": fl(v, 1)} for f, n, v in con.execute(f"select catalogue_family, count(*), avg({MONATE}) {ABGANG} and contract_start is not null and closed_date >= ? and closed_date <= ? group by 1 order by 1", [WIN_START.date(), AS_OF.date()]).fetchall()]
ser = monthly(f"""select date_trunc('month', closed_date) m, avg({MONATE}) v, count(*) n {ABGANG} and contract_start is not null group by 1 order by 1""")
add("D4", "direkt", {"v": fl(m_d4, 2) if n_d4 else None, "einheit": "months", "n": iv(n_d4), "grund": "kein Abgang mit Vermietungsstart im Fenster", "je_familie": je_fam4}, serie=ser, min_n=1)

# D5, D6: nicht im Werkzeug
add("D5", "keine", {"v": None, "einheit": "ratio", "n": None}, fehlt="Support-Ende je Modell im Katalog")
add("D6", "keine", {"v": None, "einheit": "ratio", "n": None}, fehlt="Feed suppliers/reviews mit Datum und Ergebnis je Lieferant")


# ---------------------------------------------------------------- Beitragsbuch: Ereignisse mit Akteur
def book(nr, datum, rolle, massnahme, wirkung, beleg, stufe):
    BOOK.setdefault(nr, []).append({"datum": pd.Timestamp(datum).date().isoformat(), "rolle": rolle, "massnahme": massnahme,
                                    "wirkung": fl(wirkung, 2), "beleg": str(beleg), "stufe": stufe})


# B1: Preisreduktionen im Fenster der Kennzahl, Rolle ueber die Kategorie
for spend_id, d, cat, sav, ok in con.execute("""select spend_id, invoice_date, category, saving, saving_confirmed_by_controlling from main.indirect_spend
                                                 where coalesce(saving, 0) > 0 and coalesce(saving_type, 'hard_price_reduction') = 'hard_price_reduction'
                                                   and invoice_date >= ? and invoice_date <= ? order by invoice_date""", [WIN_START.date(), AS_OF.date()]).fetchall():
    book("B1", d, role_of_category(cat), "Preisreduktion, " + CAT_DE.get(str(cat), str(cat)), sav, spend_id, STUFE_REAL if ok else STUFE_VERH)

# C2: Preisschutz-Gutschriften im Fenster, Rolle ueber die Geraetefamilie der Bestellzeile
for inv, d, amt, fam in con.execute(f"""select i.invoice_number, i.invoice_date, i.amount_eur, l.catalogue_family
                                        from bronze.erp_supplier_invoices i join {LEDGER} l on l.po_number = i.po_number and l.po_line = i.po_line
                                        where i.line_kind = 'price_protection_credit' and i.invoice_date >= ? and i.invoice_date <= ? order by i.invoice_date""", [WIN_START.date(), AS_OF.date()]).fetchall():
    book("C2", d, role_of_family(fam), "Preisschutz gutgeschrieben, " + str(fam), amt, inv, STUFE_REAL)

# C4 und C10: Entscheidungen des Laufs zum Stichtag (R01 Reparatur, R02 Kanalwahl), Rolle ueber die Familie in inputs_json
dec = con.execute("""select decision_id, decided_at, rule_id, outcome, value_at_stake_eur, json_extract_string(inputs_json, '$.family') fam
                     from main.decision_log where rule_id in ('R01', 'R02') and as_of = ? and value_at_stake_eur is not null order by decided_at, decision_id""", [AS_OF.date()]).fetchall()
if len({str(d[0])[:BELEG_CHARS] for d in dec}) != len(dec):
    raise SystemExit("decision_log: die Kurzform der decision_id ist nicht eindeutig; BELEG_CHARS erhoehen")
for decision_id, decided_at, rule_id, outcome, eur, fam in dec:
    rolle = role_of_family(fam)
    if rule_id == "R01":
        book("C4", decided_at, rolle, "Reparatur nach Regel freigegeben" if outcome == "repair" else "Reparatur nach Regel abgelehnt", eur, str(decision_id)[:BELEG_CHARS], STUFE_IDENT)
    else:
        book("C10", decided_at, rolle, "Kanalwahl nach Regel, " + CH_DE.get(str(outcome), str(outcome)), eur, str(decision_id)[:BELEG_CHARS], STUFE_IDENT)

# A2: Vertraege mit den sieben vorhandenen Pflichtfeldern zum Stichtag; Hardware ueber die Katalogfamilien der OEMs
oem_fam = {}
for oem, fam in con.execute("select distinct oem, family from bronze.cat_models").fetchall():
    oem_fam.setdefault(str(oem), set()).add(str(fam))
for cid, cat, covers, d in con.execute(f"select contract_id, category, covers_oems, as_of from silver.contracts where {SEVEN} order by contract_id").fetchall():
    if str(cat) == "hardware":
        fams = set()
        for o in [x.strip() for x in str(covers or "").split(",") if x.strip()]:
            fams |= oem_fam.get(o, set())
        roles = {FAM_ROLE.get(f, "TEAM") for f in fams}
        rolle = roles.pop() if len(roles) == 1 else "TEAM"
    else:
        rolle = role_of_category(cat)
    book("A2", d, rolle, "Vertrag erfasst, sieben Felder, " + CAT_DE.get(str(cat), str(cat)), None, cid, STUFE_IDENT)

# D1 und D2: Aufbereitungsauftraege im Fenster mit zweitem Leben (sellable oder as_is, kein scrap), Rolle ueber die Familie
GRADE_DE = {"A": "Grade A", "B": "Grade B", "C": "Grade C", "D": "Grade D"}
for wo, d, fam, outcome, grade in con.execute(f"""select w.work_order_id, w.finished_at, l.catalogue_family, w.outcome, w.grade_out
                                                   from bronze.rf_work_orders w join {LEDGER} l on l.serial = w.serial
                                                   where w.outcome <> 'scrap' and w.work_order_id is not null and w.finished_at >= ? and w.finished_at < ? order by w.finished_at, w.work_order_id""",
                                               [WIN_START.date(), (AS_OF + pd.Timedelta(days=1)).date()]).fetchall():
    rolle = role_of_family(fam)
    text = "Aufbereitung, " + ("verkaufsfähig, " + GRADE_DE.get(str(grade), str(grade)) if outcome == "sellable" else CH_DE.get("as_is", "as_is")) + ", " + str(fam)
    book("D1", d, rolle, text, None, wo, STUFE_REAL)
    book("D2", d, rolle, text, None, wo, STUFE_REAL)

# D3 und D4: abgeschlossene Reparaturtickets im Fenster, Rolle ueber die Familie
DAMAGE_DE = {"battery": "Akku", "screen": "Display", "housing": "Gehäuse", "water": "Wasserschaden", "other": "sonstiger Schaden"}
for tid, d, fam, dmg in con.execute(f"""select t.ticket_id, t.closed_at, l.catalogue_family, t.damage_type
                                        from bronze.sd_tickets t join {LEDGER} l on l.serial = t.serial
                                        where t.resolution = 'repair' and t.closed_at is not null and t.ticket_id is not null and t.closed_at >= ? and t.closed_at < ? order by t.closed_at, t.ticket_id""",
                                    [WIN_START.date(), (AS_OF + pd.Timedelta(days=1)).date()]).fetchall():
    rolle = role_of_family(fam)
    text = "Reparatur statt Ersatz, " + DAMAGE_DE.get(str(dmg), str(dmg)) + ", " + str(fam)
    book("D3", d, rolle, text, None, tid, STUFE_REAL)
    book("D4", d, rolle, text, None, tid, STUFE_REAL)

con.close()

# ---- Buch an die Zeilen: Summen je Rolle, die juengsten Zeilen, der Rest gezaehlt
for r in rows:
    ev = sorted(BOOK.get(r["nr"], []), key=lambda e: (e["datum"], e["beleg"]), reverse=True)
    sums = {}
    for e in ev:
        s = sums.setdefault(e["rolle"], {"rolle": e["rolle"], "label": ROLE_LABEL.get(e["rolle"], e["rolle"]), "n": 0, "eur": None})
        s["n"] += 1
        if e["wirkung"] is not None:
            s["eur"] = fl((s["eur"] or 0.0) + e["wirkung"], 2)
    with_eur = [e["wirkung"] for e in ev if e["wirkung"] is not None]
    r["beitraege"] = {
        "n": len(ev), "eur": fl(sum(with_eur), 2) if with_eur else None,
        "summen": sorted(sums.values(), key=lambda s: ROLE_ORDER.get(s["rolle"], 99)),
        "liste": [dict(e, label=ROLE_LABEL.get(e["rolle"], e["rolle"])) for e in ev[:BOOK_ROWS]], "n_mehr": max(0, len(ev) - BOOK_ROWS),
    }

# ---- Reihenfolge und Zaehlung
ORDER = [k["nr"] for k in rahmen["kpis"]]
rows.sort(key=lambda r: ORDER.index(r["nr"]))
if [r["nr"] for r in rows] != ORDER:
    raise SystemExit("kpis.json: nicht jede Kennzahl des Rahmens hat eine Zeile")
counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("erfuellt", "gelb", "verfehlt", "nicht_messbar", "nicht_im_werkzeug")}
measured = [r["fortschritt"]["v"] for r in rows if r["fortschritt"]["v"] is not None]
SETS = [{"key": "A", "label": "Satz A, alle Rollen"}, {"key": "B", "label": "Satz B, Indirekt"}, {"key": "C", "label": "Satz C, Resale"}, {"key": "D", "label": "Satz D, ESG", "gruppe": "esg"}]

data = {
    "today": TODAY, "as_of": AS_OF.date().isoformat(), "window": WINDOW, "window_start": WIN_START.date().isoformat(),
    "fassung": rahmen["fassung"], "stand": rahmen["stand"], "quelle": rahmen["quelle"], "url": rahmen["url"], "tausch_hinweis": rahmen["tausch_hinweis"],
    "gelb_band": GELB_BAND, "baseline_months": BASELINE_MONTHS, "book_rows": BOOK_ROWS, "min_n_owner": MIN_N_OWNER,
    "cycle": {"start": CYCLE_START.date().isoformat(), "default": CYCLE_DEFAULT, "owner": CYCLE_OWNER, "periods": [{"key": p[0], "label": p[1], "months": p[2]} for p in PERIODS]},
    "owners": {"roles": ROLES, "unmapped": sorted(UNMAPPED)},
    "esg": {"owner": ESG_OWNER, "unit": str(esg.get("unit", "")), "factors": [dict(v, familie=k) for k, v in ESG_FACTORS.items()]},
    "counts": counts, "n_kpis": len(rows), "n_measured": len(measured),
    "fortschritt_mittel": fl(sum(measured) / len(measured)) if measured else None,
    "sets": SETS, "rows": rows,
}
text = de_roles(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
if EN_DASH in text or EM_DASH in text:
    raise SystemExit("kpis.json: Strich in den Daten")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(text + "\n", encoding="utf-8", newline="\n")
print("kpis.json:", len(rows), "Kennzahlen,", counts, "Stichtag", data["as_of"], "Beitraege", sum(len(v) for v in BOOK.values()),
      "ohne Rolle:", sorted(UNMAPPED) or "keine")
