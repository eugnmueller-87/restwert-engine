# -*- coding: utf-8 -*-
"""Daten: which channels feed the tool, what came in, what was rejected, how the chain of dates looks; every number from the lake."""
import json, sys, pathlib, math
import duckdb, yaml
sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); TODAY = sys.argv[3]
con = duckdb.connect(str(REPO / "data" / "restwert.duckdb"), read_only=True)
lake = yaml.safe_load(open(REPO / "config" / "lake.yaml", encoding="utf-8"))

def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v

FEEDS = {  # feed key: (German name, delivering system in German, content in one line, needed for the cycle, public reference)
    "catalogue/models": ("Katalog: Modelle", "öffentlicher Katalog (Repo)", "Modell, Hersteller, Familie, Verkaufsstart, Nachfolger, je Zeile mit Quelle", True, True),
    "catalogue/variants": ("Katalog: Ausstattungen und UVP", "öffentlicher Katalog (Repo)", "Speicherstufen und Konfigurationen mit UVP beim Verkaufsstart, je Zeile mit Quelle", True, True),
    "market/curves": ("Marktkurven", "Tab Realisierung (öffentliche Preisbelege)", "Kurven je Familie und Hersteller aus den öffentlichen Preisbelegen; nur Vergleich, keine Entscheidung", False, True),
    "contracts/register": ("Vertragsregister", "Vertragsverwaltung", "Hersteller- und Händlerverträge: Laufzeit, Preisschutzfrist, Boni, Kündigung", False, False),
    "erp/purchase_orders": ("Bestellungen", "ERP, Einkauf", "Bestellkopf je Lieferant mit Vertragsbezug", True, False),
    "erp/po_lines": ("Bestellpositionen", "ERP, Einkauf", "Modell, Ausstattung, Menge, Preis je Position", True, False),
    "erp/goods_receipts": ("Wareneingänge", "ERP und Lager", "Seriennummer je gelieferter Position mit Eingangsdatum", True, False),
    "erp/supplier_invoices": ("Lieferantenrechnungen", "ERP, Kreditoren", "Rechnungspositionen je Bestellposition: Gerätepreis, Fracht, Zoll", True, False),
    "erp/price_changes": ("Preisänderungen der Hersteller", "ERP, Preislisten", "Listenpreisänderungen je Modell mit Datum; Grundlage des Preisschutzes", False, False),
    "wms/staging_log": ("Einrichtungsprotokoll", "Lager", "Einrichtung je Seriennummer vor dem Versand", True, False),
    "wms/shipments": ("Versandprotokoll", "Lager", "Jeder Versand je Seriennummer: zum Kunden, Austausch, Rücksendung", True, False),
    "portal/rental_contracts": ("Mietverträge", "Kundenportal", "Vertrag je Seriennummer: Kunde, Laufzeit, Monatsmiete, Beginn, Ende", True, False),
    "portal/rental_invoices": ("Mietrechnungen", "Kundenportal, Abrechnung", "Monatliche Mietrechnung je Vertrag", True, False),
    "servicedesk/tickets": ("Servicefälle", "Servicedesk", "Störungen und Reparaturen je Seriennummer mit Lösung und Kosten", False, False),
    "returns/receipts": ("Rückläufer", "Rücknahme", "Rückgabe je Seriennummer mit Zustandsprüfung und Löschzertifikat", True, False),
    "refurb/work_orders": ("Aufbereitungsaufträge", "Aufbereitungspartner", "Auftrag je Seriennummer mit Kosten, Zustand danach und Ergebnis", True, False),
    "recommerce/orders": ("Verkaufsaufträge", "Recommerce, je Kanal", "Verkauf je Seriennummer: Kanal, Preis, Datum", True, False),
    "recommerce/credit_notes": ("Gutschriften der Verkaufskanäle", "Kanalabrechnung", "Zahlungseingang und Gebühren je Verkauf", True, False),
    "finance/indirect_spend": ("Indirekte Ausgaben", "Kreditoren", "Ausgaben ohne Seriennummer (Dienstleister, Software); nicht im Gerätekreislauf", False, False),
}
SYS_DE = {"erp": "ERP", "wms": "Lager", "portal": "Kundenportal", "servicedesk": "Servicedesk", "returns": "Rücknahme", "refurb": "Aufbereiter", "recommerce": "Recommerce", "finance": "Finanzen", "contracts": "Vertragsverwaltung", "catalogue": "Katalog (öffentlich)", "market": "Marktkurven (öffentlich)"}
REASON_DE = {"unknown_po_line": "Rechnungsposition ohne bekannte Bestellposition", "unknown_serial": "Versandzeile ohne bekannte Seriennummer", "duplicate_conflict": "Doppelte Zeile mit anderem Inhalt (erste Lieferung gilt)", "missing_required": "Pflichtfeld leer", "bad_type": "Wert im falschen Format", "bad_enum": "Wert außerhalb der erlaubten Liste", "negative_amount": "Negativer Betrag", "unknown_po": "Position ohne bekannte Bestellung", "unknown_contract": "Vertrag unbekannt"}
STEP_DE = [("ordered_at", "Bestellung"), ("received_at", "Wareneingang"), ("staged_at", "Einrichtung"), ("shipped_at", "Versand zum Kunden"), ("returned_at", "Rückgabe"), ("wiped_at", "Datenlöschung"), ("graded_at", "Zustandsprüfung"), ("sellable_at", "verkaufsfähig"), ("sold_at", "Verkauf"), ("credited_at", "Zahlungseingang")]
STATUS_DE = {"deployed": "beim Kunden", "rented": "beim Kunden, vermietet", "awaiting_return": "Rückgabe erwartet", "in_stock": "im Lager, verkaufsfähig", "wip": "zurück, in Prüfung oder Aufbereitung", "sold": "verkauft", "scrapped": "verschrottet", "not_deployed": "Ersatzgerät, nie vermietet", "returned": "zurück, in Prüfung"}

OHNE = {'catalogue/models': 'Modell, Verkaufsstart und Alter je Gerät', 'catalogue/variants': 'UVP je Gerät, damit Rabatt und Realisierung', 'market/curves': 'nur der Vergleich mit den öffentlichen Preisbelegen', 'contracts/register': 'nur Tab Verträge und die Preisschutzfrist je Hersteller', 'erp/purchase_orders': 'Bestelldatum und Lieferant je Gerät', 'erp/po_lines': 'Bestellpreis je Gerät (Einkaufspreis bis zur Rechnung)', 'erp/goods_receipts': 'Seriennummer und Wareneingang: ohne sie gibt es das Gerät im Werkzeug nicht', 'erp/supplier_invoices': 'Einkaufspreis, Fracht und Zoll je Gerät', 'erp/price_changes': 'nur die Stellschraube Preisschutz', 'wms/staging_log': 'Einrichtung vor Versand (Kosten und Datum)', 'wms/shipments': 'Versand zum Kunden, Austausch, Rücksendung (Kosten und Daten)', 'portal/rental_contracts': 'Laufzeit und Miete je Gerät', 'portal/rental_invoices': 'Mieterlös je Gerät', 'servicedesk/tickets': 'nur die Reparaturkosten in der Stellschraube Zustand und Reparatur', 'returns/receipts': 'Rückgabe, Zustandsstufe, Datenlöschung je Gerät', 'refurb/work_orders': 'Aufbereitung und Datum verkaufsfähig', 'recommerce/orders': 'Restwert (Verkaufspreis) und Verkaufskanal je Gerät', 'recommerce/credit_notes': 'Zahlungseingang und Kanalgebühren je Gerät', 'finance/indirect_spend': 'nur die indirekten Ausgaben, die nicht im Gerätekreislauf stehen'}
ing = con.execute("select * from gold.ingest_summary").df()
feeds = []
for _, r in ing.iterrows():
    meta = FEEDS.get(r["feed"], (r["feed"], r["delivering_system"], "", True, False))
    feeds.append({"key": r["feed"], "name": meta[0], "system": meta[1], "content": meta[2], "needed": meta[3], "public": meta[4], "ohne": OHNE.get(r["feed"], ""), "source": SYS_DE.get(r["source_system"], r["source_system"]),
                  "files": int(r["n_files"]), "last": str(r["last_delivered_on"])[:10], "read": int(r["rows_read"]), "new": int(r["rows_new"]), "dup_same": int(r["duplicates_identical"]), "dup_conflict": int(r["duplicates_conflict"]), "unresolved": int(r["n_unresolved"]), "bronze": int(r["bronze_rows"])})
order = list(FEEDS.keys())
feeds.sort(key=lambda f: order.index(f["key"]) if f["key"] in order else 99)
tot = {"feeds": len(feeds), "files": sum(f["files"] for f in feeds), "read": sum(f["read"] for f in feeds), "new": sum(f["new"] for f in feeds), "dup_same": sum(f["dup_same"] for f in feeds), "dup_conflict": sum(f["dup_conflict"] for f in feeds), "unresolved": sum(f["unresolved"] for f in feeds),
       "systems": len(set(f["source"] for f in feeds if not f["public"])), "needed": sum(1 for f in feeds if f["needed"]), "public": sum(1 for f in feeds if f["public"])}
unres = [{"feed": FEEDS.get(next((k for k in FEEDS if k.endswith("/" + r["feed"])), ""), (r["feed"],))[0], "reason": REASON_DE.get(r["reason_code"], r["reason_code"]), "n": int(r["n"])} for _, r in con.execute("select feed, reason_code, count(*) n from bronze.unresolved group by 1, 2 order by 3 desc").df().iterrows()]
chain = con.execute("select step, sum(case when is_expected then n_serials else 0 end) exp, sum(case when is_expected then n_present else 0 end) pres from gold.chain_quality group by 1").df()
chain_map = {r["step"]: (int(r["exp"]), int(r["pres"])) for _, r in chain.iterrows()}
steps = [{"step": de, "expected": chain_map.get(k, (0, 0))[0], "present": chain_map.get(k, (0, 0))[1]} for k, de in STEP_DE]
status = [{"status": STATUS_DE.get(r["lifecycle_status"], r["lifecycle_status"]), "n": int(r["n"]), "complete": int(r["c"])} for _, r in con.execute("select lifecycle_status, count(*) n, sum(case when chain_complete then 1 else 0 end) c from silver.device_ledger group by 1 order by 2 desc").df().iterrows()]
serials = con.execute("select count(*) n, sum(case when chain_complete then 1 else 0 end) c from silver.device_ledger").df().iloc[0]
STEP_SOURCE = {  # step: (channel on the Daten tab, the row and field the date is read from)
    "ordered_at": ("Bestellungen (ERP)", "Bestelldatum des Bestellkopfs, über den Wareneingang der Seriennummer gefunden"),
    "received_at": ("Wareneingänge (ERP und Lager)", "Eingangsdatum der Zeile mit dieser Seriennummer"),
    "staged_at": ("Einrichtungsprotokoll (Lager)", "Datum der ersten Einrichtung dieser Seriennummer"),
    "shipped_at": ("Versandprotokoll (Lager)", "Datum des ersten Versands zum Kunden (oder als Austauschgerät)"),
    "returned_at": ("Rückläufer (Rücknahme)", "Rückgabedatum des letzten Rückläufer-Belegs"),
    "wiped_at": ("Rückläufer (Rücknahme)", "Datum des Löschzertifikats auf demselben Beleg"),
    "graded_at": ("Rückläufer (Rücknahme)", "Datum der Zustandsprüfung auf demselben Beleg"),
    "sellable_at": ("Aufbereitungsaufträge (Aufbereiter)", "Fertigstellung des letzten Auftrags, dessen Ergebnis nicht Verschrottung ist"),
    "sold_at": ("Verkaufsaufträge (Recommerce)", "Verkaufsdatum des Auftrags mit dieser Seriennummer"),
    "credited_at": ("Gutschriften der Verkaufskanäle (Kanalabrechnung)", "Datum der Gutschrift zu diesem Verkauf"),
}
for st in steps:
    key = [k for k, de in STEP_DE if de == st["step"]][0]
    st["channel"], st["field"] = STEP_SOURCE[key]
days_to_cash = {k: int(v) for k, v in (lake.get("days_to_cash") or {}).items()}
CH_DE = {"employee_buyout": "Mitarbeiterkauf", "marketplace": "Marktplatz", "b2b_wholesale": "Großhandel", "as_is": "Verkauf ohne Aufbereitung"}
miss = con.execute("""select t.serial, d.model_name, d.lifecycle_status, d.resale_channel, t.missing_steps,
  t.ordered_at, t.received_at, t.staged_at, t.shipped_at, t.returned_at, t.wiped_at, t.graded_at, t.sellable_at, t.sold_at, t.credited_at,
  r.wipe_certificate_id
  from silver.serial_timeline t join silver.device_ledger d using (serial)
  left join (select serial, max(wipe_certificate_id) wipe_certificate_id from bronze.ret_receipts group by 1) r using (serial)
  where not t.chain_complete order by t.serial""").df()
unresolved_ship = int(con.execute("select count(*) from bronze.unresolved where reason_code='unknown_serial' and feed='shipments'").fetchone()[0])
from datetime import date as _date
as_of_d = _date.fromisoformat(TODAY)
order_keys = [k for k, _ in STEP_DE]
missing = {}
for _, r in miss.iterrows():
    for key in str(r["missing_steps"]).split(","):
        key = key.strip()
        if key not in order_keys:
            continue
        idx = order_keys.index(key)
        prev = None
        for k in reversed(order_keys[:idx]):
            v = r[k]
            if v is not None and str(v) != "NaT":
                prev = (dict(STEP_DE)[k], str(v)[:10]); break
        days = (as_of_d - _date.fromisoformat(prev[1])).days if prev else None
        ch = CH_DE.get(r["resale_channel"], r["resale_channel"] or "")
        if key == "credited_at":
            usual = days_to_cash.get(r["resale_channel"] or "", None)
            hint = "Gutschrift des Kanals noch nicht geliefert; Verkauf über " + ch + " vor " + str(days) + " Tagen" + ((", üblich " + str(usual) + " Tage bis Zahlungseingang (Annahme)" + (", überfällig" if days is not None and usual is not None and days > usual else "")) if usual else "")
        elif key == "wiped_at":
            hint = "Rückläufer-Beleg ohne Löschzertifikat (Feld leer); die Rücknahme muss das Zertifikat nachliefern" if r["wipe_certificate_id"] is None or str(r["wipe_certificate_id"]) == "nan" else "Löschzertifikat vorhanden, aber ohne Datum"
        elif key == "shipped_at":
            hint = "kein Versandprotokoll mit dieser Seriennummer; im Kanal Versandprotokoll liegen " + str(unresolved_ship) + " ungeklärte Zeilen mit unbekannter Seriennummer, vermutlich gehört eine davon hierher (zu prüfen)"
        else:
            hint = "Zeile im Kanal " + STEP_SOURCE[key][0] + " fehlt"
        missing.setdefault(key, []).append({"serial": r["serial"], "model": r["model_name"], "status": STATUS_DE.get(r["lifecycle_status"], r["lifecycle_status"]), "prev": prev[0] if prev else "", "prev_date": prev[1] if prev else "", "days": days, "channel": STEP_SOURCE[key][0], "hint": hint})
missing_de = {dict(STEP_DE)[k]: v for k, v in missing.items()}
data = {"today": TODAY, "as_of": str(ing["as_of"].iloc[0])[:10], "feeds": feeds, "tot": tot, "unresolved": unres, "steps": steps, "status": status,
        "serials": int(serials["n"]), "chain_ok": int(serials["c"]), "missing": missing_de, "days_to_cash": days_to_cash, "cadence": lake.get("delivery_cadence", ""), "history_start": str(lake.get("history_start")), "purchase_end": str(lake.get("purchase_end"))}

# ---- write the data of this tab (the old single page that followed here was retired with Version 3; the shell
# in web/app.js and the motor in web/engine/lake.js render this JSON)
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(data, ensure_ascii=False, default=clean, separators=(",", ":")) + "\n")
print(OUT.name + ": geschrieben")
