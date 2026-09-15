# -*- coding: utf-8 -*-
"""Stellschrauben page, uniform cards, every number from the same 12-month window, German labels only."""
import json, sys, pathlib, math
import duckdb, pandas as pd, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
as_of = con.execute("select max(as_of) from gold.levers_summary").fetchone()[0]
win_start = con.execute("select max(as_of) - interval 12 month from gold.levers_summary").fetchone()[0]
THR = yaml.safe_load(open(REPO / "config" / "thresholds.yaml", encoding="utf-8"))
THR = THR.get("thresholds", THR)
FAM_DE = {"iphone_like": "iPhone", "android_like": "Android-Smartphone", "laptop_like": "Laptop", "tablet_like": "Tablet"}
CH = {"employee_buyout": "Mitarbeiterkauf", "marketplace": "Marktplatz", "b2b_wholesale": "Großhandel (Verkauf an Geschäftskunden)", "as_is": "Verkauf ohne Aufbereitung"}
ROUTE_DE = {"reseller": "Zwischenhändler", "oem_direct": "Hersteller direkt", "manufacturer": "Hersteller direkt"}
def de_group(g):
    parts = [ROUTE_DE.get(x.strip(), x.strip()) for x in str(g).split("/")]
    return " / ".join(parts)
OWNER_DE = {"Head of Procurement (name)": "Einkaufsleitung", "Head of Recommerce (name)": "Leitung Recommerce", "Head of Service Operations (name)": "Leitung Service", "CFO (name)": "CFO", "Category Manager Hardware (name)": "Category Manager Hardware", "Head of Customer Success (name)": "Leitung Customer Success"}

TEXT = {
    "L01": ("Einkaufsrabatt", "Haben wir jedes Gerät so günstig eingekauft wie die besten 25 % unserer eigenen Einkäufe desselben Herstellers?",
            "Für jedes Gerät wird der gezahlte Preis mit dem Preis verglichen, den die besten 25 % der eigenen Einkäufe desselben Herstellers, Bezugswegs und Halbjahres erreicht haben. Die Differenz ist der Hebel; wer besser eingekauft hat, zählt 0."),
    "L02": ("Preisschutz", "Hat der Hersteller kurz nach unserem Kauf den Listenpreis gesenkt, und haben wir die Gutschrift dafür beantragt, die uns der Vertrag zusichert?",
            "Preisschutz ist die Gutschrift des Herstellers, wenn er den Listenpreis innerhalb einer vertraglichen Frist nach unserem Kauf senkt. Senkt ein Hersteller den Preis innerhalb des Preisschutzfensters, steht für den Bestand eine Gutschrift zu. Der Hebel ist die Gutschrift, die nicht beantragt wurde. Offene Fenster zählen noch nicht."),
    "L03": ("Kanalwahl", "Haben wir jedes Gerät über den Kanal verkauft, der nach Abzügen am meisten bringt?",
            "Für jedes verkaufte Gerät wird jeder zulässige Kanal mit der Restwertprognose bewertet, die bei Rückgabe galt: mal Kanalfaktor, minus Gebühren, minus Lagerkosten bis das Geld da ist. Der Hebel ist der Erlös des besten Kanals minus der des gewählten; 0, wenn der beste gewählt wurde. Ob der Kanal am Ende mehr oder weniger als die Restwertprognose brachte, ist Prognosegenauigkeit und zählt hier nicht."),
    "L04": ("Zustand und Reparatur", "Stimmte der vom Kunden gemeldete Zustand, und hat sich die Reparatur gelohnt?",
            "Zwei Teile: der Wert laut Wertkurve der Simulation bei der gemeldeten Zustandsstufe minus dem bei der geprüften, beides in Prozent des Einkaufspreises, mal Einkaufspreis; und jede Reparatur oberhalb der zulässigen Quote des Restwerts mit dem Betrag darüber."),
    "L05": ("Liegetage", "Wie lange liegen zurückgegebene Geräte, bis sie verkauft sind, und was kostet jeder Tag über dem Soll?",
            "Gemessen wird je Seriennummer die Kette Rückgabe, Datenlöschung und Zustandsprüfung, Aufbereitung fertig (ab da verkaufsfähig), Verkauf, Zahlungseingang, jede Station mit Datum aus dem Geräte-Hauptbuch. Liegetage sind die Tage von verkaufsfähig bis verkauft; das Soll je Geräteart ist eine Annahme (Leitung Recommerce). Tage über dem Soll mal Lagerkosten je Tag, plus der Wertverlust laut Wertkurve der Simulation zwischen erwartetem und tatsächlichem Verkaufsmonat. Geräte im Lager werden zum Stichtag gemessen. Die Lagerkosten je Tag sind ein Platzhalter, der Hebel also eine Schätzung."),
    "L06": ("Herstellermix", "Erzielt ein Hersteller beim Wiederverkauf weniger als die eigene Flotte bei gleicher Familie, gleichem Alter und Zustand?",
            "Die Realisierung dieses Geräts beim Verkauf, bezogen auf die UVP ohne Mehrwertsteuer, gegen die mittlere Realisierung der eigenen Verkäufe derselben Familie im selben Altersfenster und Zustand (die Hälfte der Geräte liegt darüber, die Hälfte darunter). Negativ, wenn der Hersteller besser war als die Flotte."),
    "L07": ("Laufzeit", "Welche Vertragslaufzeit bringt je Vertragsmonat die meiste Lifecycle-Marge, und was kosten uns die Verträge mit einer schlechteren Laufzeit?",
            "Für jedes abgeschlossene Gerät wird die Lifecycle-Marge durch die Vertragsmonate geteilt; das ist die Marge je Vertragsmonat. Innerhalb derselben Geräteart und desselben Einkaufshalbjahres wird je Laufzeit (12, 24, 36, 48 Monate) der mittlere Wert gebildet. Der Hebel eines Geräts ist die Marge je Monat der besten anderen Laufzeit minus die eigene, mal die eigenen Vertragsmonate; 0, wenn die eigene Laufzeit die beste ist. Das ist ein Vergleich von Vertragspolitiken, kein Fehler am einzelnen Gerät; deshalb zählt der Hebel einmal je Gruppe, nicht je Gerät. In der Simulation folgt die Rangfolge der Laufzeiten aus den Monatssätzen je Laufzeit in config/lake.yaml, die Platzhalter sind."),
}

EVENT = {"L01": "Kauf", "L02": "Kauf", "L03": "Verkauf", "L04": "Abschluss des Kreislaufs (Verkauf oder Verschrottung)", "L05": "Verkauf; für Geräte, die am Stichtag noch im Lager liegen, der Stichtag", "L06": "Verkauf", "L07": "Abschluss des Kreislaufs (Verkauf oder Verschrottung)"}

HANDLE = {
    "L01": "Rabattboden je Hersteller bei jeder Bestellung durchsetzen",
    "L02": "Jede Preissenkung des Herstellers im Schutzfenster als Gutschrift beantragen",
    "L03": "Verkaufskanal je Gerät nach dem Erlös nach Abzügen wählen",
    "L04": "Rückgabezustand prüfen und nur reparieren, wenn es sich rechnet",
    "L05": "Bestand nach Liegetagen steuern: Soll je Geräteart, Preisstaffel, Kanalwechsel",
    "L06": "Herstelleranteil im Einkauf nach erzielter Realisierung steuern",
    "L07": "Miete je Laufzeit aus der Wertkurve bepreisen, nicht flach",
}
TUN = {
    "L01": ["Rabattboden je Hersteller aus den besten 25 % der eigenen Einkäufe setzen und bei jeder Bestellung prüfen (Regel R07 schlägt an, wenn ein Bestellpreis darunter liegt).",
            "Bestellungen bündeln, wo der Boden verfehlt wurde; die Referenz gilt je Hersteller, Bezugsweg und Halbjahr, sie zeigt also, welche Bestellungen zu teuer waren.",
            "Hersteller direkt gegen Zwischenhändler je Modell vergleichen; ein Zwischenhändler mit Aufschlag muss ihn durch Lieferzeit oder Service verdienen."],
    "L02": ["Preissenkungen der Hersteller im Schutzfenster automatisch erkennen: Katalog-UVP gegen Bestellpreis, Frist aus dem Vertrag, Gutschrift beantragen (Regel R05).",
            "Erinnerung {reminder} Tage bevor ein Preisschutzfenster schließt (Schwelle price_protection_reminder_days, Verantwortlich: Category Manager Hardware); Anträge unter {min_claim} lohnen den Aufwand nicht (Schwelle price_protection_min_claim_eur).",
            "Preisschutzfrist und Mindestbetrag in jedem Herstellervertrag verhandeln; ohne Klausel gibt es nichts zu holen."],
    "L03": ["Vor jedem Verkauf jeden zulässigen Kanal mit der Restwertprognose bewerten und den mit dem höchsten Erlös nach Abzügen nehmen; Regel R02 lässt einen langsameren Kanal nur zu, wenn er den schnellsten um die Schwelle schlägt.",
            "Mitarbeiterkauf vor der Rückgabe anbieten: keine Kanalgebühr, kürzeste Zeit bis zum Zahlungseingang.",
            "Großhandel für Mengen und ältere Generationen, Marktplatz für junge Geräte in Zustandsstufe A und B.",
            "Kanalfaktoren und Gebühren je Quartal aus den eigenen Verkäufen nachziehen, nicht aus Annahmen."],
    "L04": ["Gemeldeten und geprüften Zustand je Rückgabe vergleichen und Abweichungen dem Kunden nach Vertrag berechnen.",
            "Reparieren nur bis zur zulässigen Quote des Restwerts (Regel R01); darüber ohne Aufbereitung verkaufen.",
            "Schadensquote je Kunde und Geräteart zurückspielen: Schutzhüllen, Versicherung, Kaution."],
    "L05": ["Soll-Liegetage je Geräteart setzen und den Bestand darüber jede Woche sehen; Regel R03 schreibt verkaufsfähigen Bestand ab {aging_90} und ab {aging_180} Tagen ab (Schwellen aging_days_90 und aging_days_180, Verantwortlich: CFO), das Ziel ist, dass sie nie greift.",
            "Preis nach Liegetagen staffeln und nach dem Soll den Kanal wechseln, wenn ein anderer Kanal nach Abzügen mehr bringt oder sicher abnimmt (Mengenabnahme im Großhandel); in der Simulation ist der Großhandel nicht schneller als der Marktplatz (Tabelle oben), das Argument ist die sichere Abnahme, nicht die Zeit.",
            "Verkauf vor der Rückgabe anbahnen: das Vertragsende ist bekannt, Mitarbeiterkauf und Großhandelsabruf lassen sich davor vereinbaren.",
            "Rückgabe bis verkaufsfähig kurz halten: Datenlöschung, Prüfung und Aufbereitung als eine Kette messen; Zustandsstufe D ohne Aufbereitung verkaufen.",
            "Verkaufsfenster vor dem Nachfolger nutzen; die Verkaufsstarttermine stehen im Katalog.",
            "Tage bis Zahlungseingang je Kanal mitzählen: verkauft ist nicht bezahlt."],
    "L06": ["Herstelleranteil im Einkauf nach der erzielten Realisierung steuern, nicht nach dem Listenpreis.",
            "Hersteller mit dauerhaft schlechterer Realisierung teurer bepreisen oder kürzer vermieten.",
            "Hinweis ADV03 meldet, wenn ein Hersteller unter seiner Familie liegt; die Entscheidung bleibt beim Category Manager."],
    "L07": ["Miete je Laufzeit aus der Wertkurve und den Kosten je Kreislauf ableiten (Tab Laufzeit), nicht flach über alle Laufzeiten.",
            "Laufzeit je Geräteart empfehlen: lange für Laptops und Tablets, 24 Monate für Smartphones, 12 nur mit hoher Miete oder zweitem Kreislauf.",
            "Hinweis ADV04 an den CFO, wenn eine Laufzeit je Gerät um die Schwelle besser abschließt als eine andere."],
}

def eur(x, d=2):
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{x:,.{d}f} €").replace(",", "X").replace(".", ",").replace("X", ".")
def pct(x, d=1):
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else (f"{100 * x:.{d}f} %").replace(".", ",")

def example_rows(lev, cj, delta, fam, oem):
    """German label/value rows for the worked example; only the numbers the formula uses."""
    g = cj.get
    if lev == "L01":
        return [("UVP ohne Mehrwertsteuer (Einkauf rechnet netto)", eur(g("rrp_net_eur"))), ("Gezahlter Preis (Rechnungspreis minus Preisschutz-Gutschrift)", eur(g("purchase_price"))),
                ("Gezahlter Rabatt gegen UVP", pct(g("discount_vs_rrp_pct"))),
                (f"Referenzrabatt: der Rabatt, den das beste Viertel der eigenen Einkäufe dieses Herstellers mindestens erreicht hat (Gruppe {de_group(g('group'))}, QTY {g('n')} Vergleichskäufe)", pct(g("reference_discount_pct_p75"))),
                ("Referenzpreis (UVP minus Referenzrabatt)", eur(g("reference_purchase_price_capped"))),
                ("Hebel = gezahlter Preis minus Referenzpreis", eur(delta))], "Hebel = (Referenzrabatt minus gezahlter Rabatt) mal UVP ohne Mehrwertsteuer, nie unter 0"
    if lev == "L02":
        st = {"missed": "verpasst", "claimed": "beantragt", "n/a": "nicht anwendbar", "open": "offen"}.get(g("price_protection_status"), g("price_protection_status"))
        return [("Preisschutzfenster laut Vertrag", f"{g('price_protection_days')} Tage"), ("Status", st),
                ("Gutschrift, die zustand", eur(g("price_protection_claimable_eur"))), ("Gutschrift, die erhalten wurde", eur(g("price_protection_credit_eur"))),
                ("Hebel = zustehend minus erhalten", eur(delta))], "Hebel = zustehende Gutschrift, wenn das Fenster ohne Antrag geschlossen wurde"
    if lev == "L03":
        nets = g("net_by_channel") or {}; fac = g("channel_factors") or {}; d2c = g("days_to_cash_by_channel") or {}
        rows = [("Restwertprognose bei Rückgabe (Zustandsstufe " + str(g("grade_at_sale")) + ")", eur(g("forecast_rv_of_record")))]
        for k in sorted(nets, key=lambda k: -nets[k]):
            tag = " (gewählt)" if k == g("actual_channel") else ""
            tag += " (bester)" if k == g("best_channel") else ""
            rows.append((f"Erlös nach Abzügen über {CH.get(k, k)}{tag}: Kanalfaktor {str(round(fac.get(k, 0), 2)).replace('.', ',')} (Annahme, Datei config/lake.yaml), {int(d2c.get(k, 0))} Tage bis Zahlungseingang", eur(nets[k])))
        rows.append(("Tatsächlich erzielter Restwert (vor Abzug der Kanalgebühren; die Differenz zur Restwertprognose ist Prognosegenauigkeit, nicht Kanalwahl)", eur(g("resale_gross"))))
        rows.append(("Hebel = Erlös des besten Kanals minus Erlös des gewählten Kanals", eur(delta)))
        return rows, "Erlös nach Abzügen je Kanal = Restwertprognose mal Kanalfaktor minus Gebühren minus Lagerkosten je Tag mal Tage bis Zahlungseingang"
    if lev == "L04":
        gp = g("grade_part_eur"); rp = None if gp is None or delta is None else delta - gp
        return [("Gemeldete Zustandsstufe / geprüfte Zustandsstufe", f"{g('grade_declared')} / {g('grade_inspected')}"),
                ("Wert laut Wertkurve der Simulation bei gemeldeter Zustandsstufe, in Prozent des Einkaufspreises", pct(g("grid_ratio_declared"))), ("Wert laut Wertkurve der Simulation bei geprüfter Zustandsstufe, in Prozent des Einkaufspreises", pct(g("grid_ratio_inspected"))),
                ("Einkaufspreis", eur(g("purchase_price"))), ("Alter bei Rückgabe", f"{g('months_since_launch_at_return')} Monate"),
                ("Teil Zustand = Einkaufspreis mal Differenz der beiden Anteile", eur(gp)),
                ("Teil Reparatur = Reparaturkosten über der zulässigen Quote (" + pct(g("repair_max_share_of_rv"), 0) + " des Restwerts)", eur(rp)),
                ("Hebel = Teil Zustand plus Teil Reparatur", eur(delta))], "Hebel = Einkaufspreis mal (Anteil gemeldet minus Anteil geprüft) plus Reparaturkosten über der Quote"
    if lev == "L05":
        hp = g("holding_part_eur"); vp = None if hp is None or delta is None else delta - hp
        return [("Liegetage: Tage von verkaufsfähig bis verkauft", f"{g('days_sellable_to_sold')}"), ("Erwartete Liegetage (Annahme, Verantwortlich: Leitung Recommerce)", f"{g('expected_return_to_sale_days')}"),
                ("Tage darüber", f"{g('excess_days')}"), ("Lagerkosten je Tag (Platzhalter, Verantwortlich: CFO)", eur(g("holding_cost_per_day"))),
                ("Teil Lagerkosten = Tage darüber mal Lagerkosten je Tag", eur(hp)),
                ("Teil Wertverlust laut Wertkurve der Simulation (Wert im erwarteten Verkaufsmonat " + pct(g("grid_ratio_expected"), 0) + " gegen tatsächlichen " + pct(g("grid_ratio_actual"), 0) + ", in Prozent des Einkaufspreises)", eur(vp)),
                ("Hebel = Teil Lagerkosten plus Teil Wertverlust", eur(delta))], "Hebel = Tage darüber mal Lagerkosten je Tag plus Einkaufspreis mal Wertverlust laut Wertkurve der Simulation"
    if lev == "L06":
        return [("Restwert (Verkaufspreis vor Abzug der Kanalgebühren)", eur(g("resale_gross"))), ("UVP ohne Mehrwertsteuer", eur(g("rrp_net_eur"))),
                ("Realisierung dieses Geräts, bezogen auf die UVP ohne Mehrwertsteuer", pct(g("realised_ratio"))),
                (f"Referenz: mittlere Realisierung der eigenen Verkäufe, {g('catalogue_family')}, Alter {g('age_bucket')} Monate, Zustandsstufe {g('grade_at_sale')}, QTY {g('n')} Vergleichsgeräte aus der eigenen Flotte", pct(g("median_ratio_family"))),
                ("Hebel = (Referenz minus eigene Realisierung) mal UVP ohne Mehrwertsteuer", eur(delta))], "Hebel = (mittlere Realisierung der Flotte minus eigene Realisierung) mal UVP ohne Mehrwertsteuer; negativ, wenn der Hersteller besser war"
    if lev == "L07":
        return [("Eigene Laufzeit / beste andere Laufzeit der Gruppe", f"{g('this_term')} / {g('other_term')} Monate"),
                (f"Lifecycle-Marge je Vertragsmonat, eigene Laufzeit, mittleres Gerät der Gruppe (QTY {g('n_this_term')} Geräte)", eur(g("median_result_per_month_this_term"))),
                (f"Lifecycle-Marge je Vertragsmonat, beste andere Laufzeit, mittleres Gerät der Gruppe (QTY {g('n_other_term')} Geräte)", eur(g("median_result_per_month_other_term"))),
                ("Gruppe: Geräteart / Einkaufshalbjahr", f"{FAM_DE.get(g('model_family'), g('model_family'))} / {g('purchase_half_year')}"),
                ("Hebel = Differenz je Vertragsmonat mal eigene Vertragsmonate", eur(delta))], "Hebel = (Lifecycle-Marge je Vertragsmonat der besten anderen Laufzeit minus der eigenen) mal eigene Vertragsmonate, nie unter 0, einmal je Gruppe"
    return [(k, str(v)) for k, v in cj.items() if not isinstance(v, (dict, list))][:8], cj.get("formula", "")

def threshold_text(lev, oem, fam):
    key = {"L01": "purchase_discount_floor_pct", "L02": "price_protection_min_claim_eur", "L03": "channel_min_net_uplift_eur", "L04": "repair_max_share_of_rv", "L05": "aging_days_90", "L06": "oem_realisation_gap_pct", "L07": "term_result_gap_alert_eur"}[lev]
    t = THR.get(key, {}); owner = OWNER_DE.get(t.get("owner"), t.get("owner"))
    if lev == "L01":
        v = (t.get("values") or {}); ex = v.get(oem); rest = ", ".join(f"{k} {pct(x, 0)}" for k, x in v.items())
        return f"Rabattboden je Hersteller (Regel R07 schlägt an, wenn ein Bestellpreis darunter liegt): {oem} {pct(ex, 0)}; alle: {rest}", owner, "R07"
    if lev == "L04":
        v = (t.get("values") or {}); ex = v.get(fam)
        return f"Zulässige Reparaturquote am Restwert (Regel R01): {FAM_DE.get(fam, fam)} {pct(ex, 0)}; alle: " + ", ".join(f"{FAM_DE.get(k, k)} {pct(x, 0)}" for k, x in v.items()), owner, "R01"
    if lev == "L02":
        return f"Mindestbetrag für einen Preisschutz-Antrag (Regel R05): {eur(t.get('value'), 0)}", owner, "R05"
    if lev == "L03":
        return f"Ein langsamerer Kanal muss den schnellsten nach Abzügen um mindestens {eur(t.get('value'), 0)} schlagen (Regel R02)", owner, "R02"
    if lev == "L05":
        return f"Erste Alterungsstufe (Regel R03): {t.get('value')} Liegetage; die erwarteten Liegetage je Familie setzt die Leitung Recommerce", owner, "R03"
    if lev == "L06":
        return f"Hinweis ADV03 meldet, wenn ein Hersteller im Mittel mehr als {round(100 * float(t.get('value', 0)))} Prozentpunkte unter der mittleren Realisierung seiner Familie liegt", owner, "ADV03"
    if lev == "L07":
        return f"Hinweis ADV04 meldet, wenn eine Laufzeit je Gerät mindestens {eur(t.get('value'), 0)} besser abschließt als eine andere Laufzeit derselben Gruppe", owner, "ADV04"
    return "", owner, ""

channel_days = con.execute("""select resale_channel, count(*) n, round(avg(datediff('day', return_date, sellable_date))) d_sellable,
    round(avg(datediff('day', sellable_date, sale_date))) d_sold, round(avg(days_return_to_cash)) d_cash
    from silver.device_ledger where is_closed and resale_channel is not null and return_date is not null and sellable_date is not null group by 1 order by d_sold""").df()
channel_days = [{"kanal": CH.get(r["resale_channel"], r["resale_channel"]), "n": int(r["n"]), "d_sellable": float(r["d_sellable"]), "d_sold": float(r["d_sold"]), "d_cash": float(r["d_cash"])} for _, r in channel_days.iterrows()]
stock_now = con.execute("select count(*) n, round(avg(days_in_stock_to_date)) d, sum(case when days_in_stock_to_date>90 then 1 else 0 end) over90 from silver.device_ledger where not is_closed and sellable_date is not null and sale_date is null").df().iloc[0].to_dict()
cards = []
summary = con.execute("select * from gold.levers_summary order by rank").df()
for _, s in summary.iterrows():
    lev = s["lever_id"]
    win = con.execute("""select sum(case when is_attributed then 1 else 0 end) n_attr, sum(case when is_attributed and delta_eur>0 then 1 else 0 end) n_pos,
        round(sum(case when is_attributed then delta_eur else 0 end),2) sum_win
        from gold.levers_per_device where lever_id=? and event_date > ?""", [lev, win_start]).df().iloc[0].to_dict()
    alltime = con.execute("select sum(case when is_attributed then 1 else 0 end) n_attr, round(sum(case when is_attributed then delta_eur else 0 end),2) sum_all, count(*) n_rows from gold.levers_per_device where lever_id=?", [lev]).df().iloc[0].to_dict()
    ex = con.execute("""select p.serial, p.delta_eur, p.event_date, p.counterfactual_json, d.launch_date, d.model_name
        from gold.levers_per_device p join silver.device_ledger d using (serial)
        where p.lever_id=? and p.is_attributed and p.delta_eur>0 and p.event_date > ? order by p.delta_eur desc""", [lev, win_start]).df()
    example = None
    if not ex.empty:
        # worked example: the newest model generation (launch date) with at least 3 devices carrying a lever in the
        # window; among them the device whose lever is nearest the median of all positive levers in the window
        median = float(ex["delta_eur"].median())
        gens = ex.groupby("launch_date").size()
        newest = max(g for g, n in gens.items() if n >= 3) if (gens >= 3).any() else ex["launch_date"].max()
        pool = ex[ex["launch_date"] == newest].copy()
        pool["_dist"] = (pool["delta_eur"] - median).abs()
        m = pool.sort_values(["_dist", "serial"]).iloc[0]
        cj = json.loads(m["counterfactual_json"]) if m["counterfactual_json"] else {}
        meta = con.execute("select oem, model_family, model_name, is_closed from silver.device_ledger where serial=?", [m["serial"]]).df()
        oem = meta.iloc[0]["oem"] if not meta.empty else cj.get("oem", "")
        fam = meta.iloc[0]["model_family"] if not meta.empty else cj.get("model_family", "")
        rows, formula = example_rows(lev, cj, float(m["delta_eur"]), fam, oem)
        ex_event = EVENT[lev] if lev != "L05" else ("Verkauf" if (not meta.empty and bool(meta.iloc[0]["is_closed"])) else "Stichtag, Gerät noch im Lager")
        example = {"serial": m["serial"], "model": meta.iloc[0]["model_name"] if not meta.empty else "", "date": str(m["event_date"])[:10], "event": ex_event, "delta": eur(float(m["delta_eur"])), "rows": rows, "formula": formula, "oem": oem, "fam": fam}
    thr_text, owner, rule = threshold_text(lev, example["oem"] if example else "", example["fam"] if example else "")
    name, frage, bedeutung = TEXT[lev]
    n_attr = int(win["n_attr"] or 0); sum_win = float(win["sum_win"] or 0.0)
    cards.append({
        "lever_id": lev, "name": name, "frage": frage, "bedeutung": bedeutung,
        "n_attr": n_attr, "n_pos": int(win["n_pos"] or 0), "sum_win": sum_win,
        "per_device": (sum_win / n_attr) if n_attr else None,
        "per_cohort": bool(s["lever_basis"] and "once per cohort" in str(s["lever_basis"])),
        "all_n": int(alltime["n_attr"] or 0), "all_sum": float(alltime["sum_all"] or 0.0), "all_rows": int(alltime["n_rows"] or 0),
        "owner": owner, "rule": rule, "threshold": thr_text, "example": example, "event": EVENT[lev], "handle": HANDLE[lev],
        "tun": [t.format(aging_90=THR.get("aging_days_90", {}).get("value"), aging_180=THR.get("aging_days_180", {}).get("value"), reminder=THR.get("price_protection_reminder_days", {}).get("value"), min_claim=eur(THR.get("price_protection_min_claim_eur", {}).get("value"), 0)) for t in TUN[lev]],
        "channel_days": channel_days if lev == "L05" else None, "stock_now": {k: (float(v) if v is not None else None) for k, v in stock_now.items()} if lev == "L05" else None,
    })

# consistency guard: the card's window sum must equal the summary's eur_fleet_per_year for additive-per-device levers
for c, (_, s) in zip(cards, summary.iterrows()):
    if not c["per_cohort"] and abs(c["sum_win"] - float(s["eur_fleet_per_year"])) > 1.0:
        print("WARNING window sum differs from summary", c["lever_id"], c["sum_win"], float(s["eur_fleet_per_year"]))
    if c["per_cohort"]:
        c["sum_win"] = float(s["eur_fleet_per_year"]); c["per_device"] = float(s["eur_per_device"])

data = {"today": TODAY, "as_of": str(as_of)[:10], "win_start": str(win_start)[:10], "cards": cards}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/levers.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben")
