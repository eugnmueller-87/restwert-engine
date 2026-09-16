/* Restwert Engine v3, Motor Kreislauf (cycle). Vertrag: v3/CONTRACT.md, Abschnitt 7.5.
   Quelle aller Texte, Zahlen und Rechenwege: v3/src/cycle.js und v3/src/cycle.body.html (der heutige Reiter), eins zu eins.
   Reine Funktion window.RE.cycle(D, opts, P) -> V. Kein DOM, kein Zustand, kein Diagramm; opts und P werden nicht gebraucht.
   Jede Zahl im Sichtbereich kommt aus D und geht durch E.fmt. */
(function (w) {
  'use strict';
  var E = w.RE;

  /* Die acht Stationen des Kreislaufs, wie heute: Ordnungszeichen, Station, Kurztext */
  var STEPS = [
    ['0', 'Daten', 'Kommt alles an?'],
    ['1', 'Einkaufspreis', 'Was bezahlt, gegen welche UVP?'],
    ['2', 'Kosten bis Verkauf', 'Was kostet es bis zum Verkauf?'],
    ['3', 'Restwertprognose', 'Was wird es am Leasingende wert sein?'],
    ['4', 'Verkauf', 'Was hat es gebracht, gegen die Restwertprognose?'],
    ['5', 'Lifecycle-Marge', 'Was bleibt je Gerät?'],
    ['6', 'Stellschrauben', 'Wo ziehen wir an?'],
    ['7', 'Verträge', 'Was mit wem, bis wann?']
  ];

  /* Anzeigenamen: die Codenamen bleiben im Datensatz, im Sichtbereich steht Deutsch */
  var VAL = {
    employee_buyout: 'Mitarbeiterkauf', marketplace: 'Marktplatz', b2b_wholesale: 'Großhandel (Verkauf an Geschäftskunden)',
    as_is: 'Verkauf ohne Aufbereitung', manufacturer: 'Hersteller direkt', reseller: 'über Zwischenhändler'
  };
  var FIRST = { 'Nach Laufzeit': 'Laufzeit', 'Nach Verkaufskanal': 'Verkaufskanal', 'Nach Bezugsweg': 'Bezugsweg' };
  var KEY = { 'Nach Laufzeit': 'c-term', 'Nach Verkaufskanal': 'c-channel', 'Nach Bezugsweg': 'c-source' };
  var LN = {
    channel_choice: 'Kanalwahl', purchase_discount: 'Einkaufsrabatt', grade_and_repair: 'Zustand und Reparatur', aging: 'Liegetage',
    term_length: 'Laufzeit', price_protection: 'Preisschutz', manufacturer_mix: 'Herstellermix'
  };
  var LT = {
    freight: 'Fracht vom Lieferanten', duty: 'Zoll', staging: 'Einrichtung vor Versand', outbound_shipping: 'Versand zum Kunden',
    repair: 'Reparatur', replacement_logistics: 'Austauschversand', return_logistics: 'Rücksendung vom Kunden',
    wipe_grading: 'Datenlöschung und Zustandsprüfung', refurbishment: 'Aufbereitung',
    holding_cost: 'Lagertage (Lagerkosten je Tag, geschätzt)', channel_fee: 'Kanalgebühren',
    support: 'Nutzerbetreuung je Gerätemonat (Umlage, geschätzt)', mdm_operations: 'Geräteverwaltung MDM je Gerätemonat (Umlage, geschätzt)'
  };

  /* Hinterlegte Kostendefinition (docs/TCO_DEFINITION.md, docs/LEDGER.md), heute der erste Klappblock des Reiter:
     Kostenzeile, Phase, Herkunft, Gebucht bei, Geschätzt, Verantwortlich */
  var COSTDEF = [
    ['Einkaufspreis', 'Anschaffung', 'Lieferantenrechnung (ERP), Stückposition je Seriennummer', 'Rechnung', 'bis die Rechnung da ist: Bestellpreis, markiert', 'Einkaufsleitung'],
    ['Fracht vom Lieferanten, Zoll', 'Anschaffung', 'Lieferantenrechnung, Positionen der Bestellzeile, centgenau auf die gelieferten Seriennummern verteilt', 'Rechnung', 'nein', ''],
    ['Einrichtung vor Versand', 'Bereitstellung', 'Einrichtungsprotokoll (Lager)', 'Einrichtung', 'nein', ''],
    ['Versand zum Kunden', 'Bereitstellung', 'Versandprotokoll (Lager), Richtung Kunde', 'Versand', 'nein', ''],
    ['Nutzerbetreuung je Gerätemonat', 'Service', 'je Mietrechnung des Geräts ein Satz je Gerätemonat (First-Level-Support, Störungsbearbeitung, Austauschkoordination); Teamkosten, keine Buchung je Seriennummer', 'Rechnungsdatum der Mietrechnung', 'ja, immer (Umlage)', 'Leitung Service'],
    ['Geräteverwaltung MDM je Gerätemonat', 'Service', 'je Mietrechnung ein Satz je Gerätemonat, nur für Geräte, die das Einrichtungsprotokoll als MDM-registriert führt; die MDM-Lizenz selbst bleibt beim Kunden', 'Rechnungsdatum der Mietrechnung', 'ja, immer (Umlage)', 'Leitung Service'],
    ['Reparatur', 'Service', 'Servicefall mit Lösung Reparatur und Kosten', 'Schließen des Servicefalls', 'nein', ''],
    ['Austauschversand', 'Service', 'Versandprotokoll, Richtung Austausch, gebucht auf das defekte Gerät', 'Versand', 'nein', ''],
    ['Rücksendung vom Kunden', 'Rückgabe', 'Versandprotokoll, Richtung Rücksendung', 'Versand', 'nein', ''],
    ['Datenlöschung und Zustandsprüfung', 'Rückgabe', 'Rückläufer-Beleg', 'Wareneingang der Rückgabe', 'nein', ''],
    ['Aufbereitung', 'Wiederverkauf', 'Aufbereitungsauftrag', 'Fertigstellung', 'nein', ''],
    ['Lagertage', 'Kapital und Lager', 'Tage je Lagerphase (Eingang, Rückgabe, Verkauf) mal Lagerkosten je Tag', 'Ende der Lagerphase', 'ja, immer', 'CFO'],
    ['Kanalgebühren', 'Wiederverkauf', 'Gutschrift des Verkaufsauftrags (Prozent plus Fixbetrag)', 'Gutschrift des Verkaufs', 'bis die Gutschrift da ist: angenommener Satz, markiert', 'Leitung Recommerce']
  ];

  /* ---------- lokale Helfer ---------- */
  /* Prozent mit so vielen Nachkommastellen, wie der Wert im Datensatz hat (heute: rohe Zahl plus Prozentzeichen;
     E.fmt.pct1 zeigt bei 37 eine Nullstelle, die heute nicht da ist, deshalb lokal nachgebaut, CONTRACT 5) */
  function decimals(x) { var s = String(x), i = s.indexOf('.'); return i < 0 ? 0 : Math.min(2, s.length - i - 1); }
  function pctRaw(x) { return (x === null || x === undefined || isNaN(Number(x))) ? '' : E.fmt.num(x, decimals(x)) + ' %'; }
  function num(x) { var n = Number(x); return isNaN(n) ? 0 : n; }
  function owner(s) { return E.fmt.role(s); }
  function oderList(items) {
    if (items.length < 2) return items.join('');
    return items.slice(0, -1).join(', ') + ' oder ' + items[items.length - 1];
  }
  function uniq(list) { var seen = {}, out = []; list.forEach(function (x) { if (!seen[x]) { seen[x] = 1; out.push(x); } }); return out; }
  /* Die Huelle schreibt font-weight je Zelle (Standard 400) und ueberschreibt damit das Gewicht der Zeile:
     eine fette Zeile braucht deshalb fette Zellen. Lokal geloest, im Bericht gemeldet. */
  function row(cells, bold) {
    if (bold) cells = cells.map(function (x) { return Object.assign({}, x, { weight: 600 }); });
    return E.ROW(cells, { bold: !!bold });
  }

  w.RE.cycle = function (D, opts, P) {
    var f = E.fmt;
    var c = D.closed || {}, o = D.open || {}, ing = D.ingest || {};
    var fam = D.fam || [], famOem = D.fam_oem || [], others = D.others || [], levers = D.levers || [], tco = D.tco || [], tot = D.tco_total;
    /* Seriennummern und Modelle im Simulationshinweis. Die heutige Seite tippt beide Zahlen als festen Text
       (make_cycle_page.py, Zeile 86; kein Feld in cycle.json), und die getippte Seriennummernzahl widerspricht der
       Statuszeile der Hülle (lake.serials) sowie report.n_serials und tco.n_serials. Hier kommt jede Zahl aus D:
       Seriennummern aus D.n_serials, sobald der Datensatz es führt wie die Nachbartabs, sonst abgeschlossene plus
       offene Kreisläufe (dieselbe Zahl wie lake.serials); die Modellzahl aus D.n_models (Modelle mit Verkaufsstart
       und UVP), und ohne dieses Feld steht der Klammersatz ohne Zahl. Keine Zahl wird getippt. */
    var serials = D.n_serials !== undefined && D.n_serials !== null ? num(D.n_serials) : num(c.n) + num(o.n);
    var nModels = num(D.n_models);

    /* ---------- Kopf und Hinweise ---------- */
    var lead = 'Jedes Gerät von der Bestellung bis zum Zahlungseingang aus dem Verkauf, an einer Seriennummer: was wir bezahlt haben, was es uns bis zum Verkauf gekostet hat, was der Kunde gezahlt hat, was es beim Verkauf noch gebracht hat, und was davon vor Finanzierung, Gemeinkosten und Steuern bleibt; Nutzerbetreuung und Geräteverwaltung sind als Umlage je Gerätemonat drin. Alle Zahlen hier sind simuliert und zeigen die Rechnung, nicht ein Haus.';
    var banner = 'Simulierte Daten, echte Mechanik. ' + (serials > 0 ? f.qty(serials) + ' Seriennummern' : 'Seriennummern')
      + ', gezogen aus dem echten Katalog ' + (nModels > 0 ? '(' + f.qty(nModels) + ' Modelle mit echten Verkaufsstarts und UVPs)' : 'mit echten Verkaufsstarts und UVPs')
      + '; die Wertkurve der Simulation ist an den öffentlichen Preisbelegen des Reiter Realisierung geeicht. Rabatte, Schadensraten, Kanalmix, Gebühren und Verträge sind Annahmen mit verantwortlicher Rolle (Datei config/lake.yaml). Kein Wert ist eine Tatsache über ein reales Unternehmen.';

    /* ---------- Stationen ---------- */
    var steps = STEPS.map(function (s) { return { k: s[0], t: s[1], q: s[2] }; });

    /* ---------- Kacheln ---------- */
    var share = num(c.n) > 0 ? num(c.profit) / num(c.n) : null;
    var kpis = [
      { label: 'Abgeschlossene Kreisläufe', value: f.qty(c.n), lines: [
        'verkauft oder verschrottet',
        share === null ? '' : f.pct(share) + ' mit positiver Lifecycle-Marge',
        share === null ? '' : f.pct(1 - share) + ' mit negativer'
      ] },
      { label: 'Lifecycle-Marge je Gerät, abgeschlossen, im Mittel', value: f.eur(c.mean_eur), neg: num(c.mean_eur) < 0, lines: [
        'Mieterlös ' + f.eur(c.rent),
        'plus Restwert ' + f.eur(c.rv),
        'minus Einkaufspreis ' + f.eur(c.purchase),
        'minus Kosten bis Verkauf ' + f.eur(c.cost_to_sale),
        'alle Werte gerundet; eine Marge, kein Gewinn'
      ] },
      { label: 'Offene Kreisläufe', value: f.qty(o.n), lines: [
        'noch beim Kunden oder im Lager',
        'Lifecycle-Marge bei Verkauf heute: ' + f.eur(o.liq),
        'am Leasingende voraussichtlich: ' + f.eur(o.proj),
        'zwei Fragen, nie addieren'
      ] },
      { label: 'Datenqualität', value: pctRaw(D.chain_pct), lines: [
        'Seriennummern mit lückenloser Datumskette von Bestellung bis Zahlungseingang',
        f.qty(ing.unresolved || 0) + ' von ' + f.qty(ing.rows_read || 0) + ' Zeilen ohne Seriennummer'
      ] }
    ];
    var kpiDefs = [
      { k: 'Lifecycle-Marge je Gerät', v: 'Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf, im Mittel je abgeschlossenem Gerät, alle Werte auf ganze Euro gerundet'
        + (num(c.cred_n) > 0 ? '; dazu Preisschutz-Gutschriften des Herstellers bei ' + f.qty(c.cred_n) + ' Geräten, im Mittel ' + f.eur2(c.cred_mean) + ' je Gerät, die zur Marge zählen und in keiner der vier Zahlen stecken' : '')
        + '. Eine Marge, kein Gewinn: Nutzerbetreuung und Geräteverwaltung (MDM) stecken als Umlage je Gerätemonat in den Kosten bis Verkauf; es fehlen Finanzierungskosten, Gemeinkosten und Steuern' },
      { k: 'bei Verkauf heute', v: 'bisheriger Mieterlös plus Restwertprognose heute nach Kanalgebühren minus Einkaufspreis minus bisherige Kosten bis Verkauf, Summe über alle offenen Geräte' },
      { k: 'am Leasingende voraussichtlich', v: 'voller Mieterlös plus Restwertprognose am Leasingende nach Kanalgebühren minus Einkaufspreis minus bisherige und erwartete Kosten bis Verkauf; die zwei Zahlen beantworten zwei Fragen und werden nie addiert' }
    ];

    /* ---------- Legendensätze, die mehrere Tabellen teilen (Wortlaut der Haupttabelle) ---------- */
    var termGroup = others.filter(function (g) { return g.label === 'Nach Laufzeit'; })[0];
    var terms = termGroup ? (termGroup.rows || []).map(function (r) { return Number(r.cohort_value); }).filter(function (n) { return !isNaN(n); }).sort(function (a, b) { return a - b; }) : [];
    var termText = terms.length ? 'über den ganzen Kreislauf von ' + oderList(terms.map(f.qty)) + ' Monaten, nicht je Jahr' : 'über den ganzen Kreislauf, nicht je Jahr';
    var DEF = {
      qty: { k: 'Geräte', v: 'Geräte, deren Kreislauf abgeschlossen ist, also verkauft oder verschrottet' },
      purchase: { k: 'Einkaufspreis', v: 'was wir dem Lieferanten für das Gerät gezahlt haben' },
      cost: { k: 'Kosten bis Verkauf', v: 'alles, was das Gerät nach dem Einkauf bis zum Zahlungseingang aus dem Verkauf kostet: Fracht vom Lieferanten und Zoll, Einrichtung vor Versand, Versand zum Kunden, Nutzerbetreuung und Geräteverwaltung je Gerätemonat (Umlage), Reparatur, Austauschversand und Rücksendung vom Kunden, Lagertage, Datenlöschung und Zustandsprüfung, Aufbereitung, Kanalgebühren; Aufschlüsselung in der Tabelle unten, Mittelwert je Gerät' },
      rent: { k: 'Mieterlös', v: 'Miete, die der Kunde über die gesamte Laufzeit für dieses Gerät gezahlt hat' },
      rv: { k: 'Restwert', v: 'der Verkaufspreis, den das Gerät nach dem Leasing tatsächlich erzielt hat, vor Abzug der Kanalgebühren; verschrottet zählt ' + f.eur(0) + '. Die Vorhersage dieses Werts heißt Restwertprognose und steht in keiner Spalte dieser Tabelle' },
      margin: { k: 'Lifecycle-Marge je Gerät', v: 'Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf; die oberste Kennzahl des Werkzeugs. Eine Marge, kein Gewinn: Nutzerbetreuung und Geräteverwaltung (MDM, Mobile Device Management) stecken als Umlage je Gerätemonat drin (geschätzt, Verantwortlich Leitung Service); Finanzierungskosten, Gemeinkosten und Steuern hängen nicht an der Seriennummer und fehlen hier' },
      pct: { k: 'in % Einkaufspreis', v: 'Lifecycle-Marge je Gerät geteilt durch Einkaufspreis, beides als Mittelwert der Zeile, ' + termText }
    };

    /* ---------- Tabelle 1: Lifecycle-Marge je Familie und Hersteller ---------- */
    var famCells = function (r) {
      var neg = num(r.mean_eur) < 0;
      return [E.N(f.qty(r.n_closed)), E.N(f.eur(r.purchase)), E.N(f.eur(r.cost_to_sale)), E.N(f.eur(r.rent)), E.N(f.eur(r.rv)), E.N(f.eur(r.mean_eur), { neg: neg }), E.N(pctRaw(r.pct_purchase), { neg: neg })];
    };
    var famRows = [];
    fam.forEach(function (fr) {
      famRows.push(row([E.C(fr.family), E.C('alle Hersteller')].concat(famCells(fr)), true));
      famOem.filter(function (x) { return x.family === fr.family; }).forEach(function (x) {
        famRows.push(row([E.C(''), E.C(x.oem, { indent: 1 })].concat(famCells(x)), false));
      });
    });
    var famNames = fam.map(function (fr) { return fr.family; }).join(', ');
    var tFam = E.TABLE('c-fam', 'Lifecycle-Marge je Familie und Hersteller, abgeschlossene Kreisläufe, Mittelwerte je Gerät', [
      E.H('Familie'), E.H('Hersteller'), E.H('Geräte', 1), E.H('Einkaufspreis', 1), E.H('Kosten bis Verkauf', 1), E.H('Mieterlös', 1), E.H('Restwert', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('in % Einkaufspreis', 1)
    ], famRows, {
      n: num(c.n),
      note: 'Diese Tabelle zeigt für die abgeschlossenen Kreisläufe der Simulation, was ein Gerät vor Finanzierung, Gemeinkosten und Steuern gebracht hat: Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf, als Mittelwert je Gerät, erst je Familie, darunter je Hersteller. Nutzerbetreuung und Geräteverwaltung (MDM) sind seit dieser Version als Umlage je Gerätemonat in den Kosten bis Verkauf enthalten; nicht drin sind Finanzierungskosten, Gemeinkosten und Steuern, deshalb ist es eine Marge und kein Gewinn.',
      defs: [
        { k: 'Familie', v: 'die Geräteart (' + famNames + '), keine Marke; die fette Zeile rechnet über alle Geräte der Familie, die eingerückten Zeilen darunter je Hersteller' },
        { k: 'Hersteller', v: 'die Marke des Geräts; die fette Familienzeile (alle Hersteller) rechnet über die ganze Geräteart, die eingerückten Zeilen darunter je Hersteller' },
        DEF.qty, DEF.purchase, DEF.cost, DEF.rent, DEF.rv, DEF.margin, DEF.pct
      ]
    });

    /* ---------- Tabellen 2 bis 4: die anderen Gruppierungen, je in eigener Tabelle ---------- */
    var othersNote = 'Die drei Tabellen: Mittelwerte je Gerät über die abgeschlossenen Kreisläufe der jeweiligen Gruppe. Einkaufspreis und Lifecycle-Marge je Gerät wie in der Haupttabelle; Kosten bis Verkauf, Mieterlös und Restwert sind hier weggelassen, die Rechnung dahinter ist dieselbe.';
    var tOthers = others.map(function (g, i) {
      var isTerm = g.label === 'Nach Laufzeit';
      var first = FIRST[g.label] || 'Gruppe';
      var label = function (v) {
        var s = String(v === null || v === undefined ? '' : v);
        if (VAL[s]) return VAL[s];
        if (isTerm && /^\d+$/.test(s)) return f.qty(s) + ' Monate';
        return s;
      };
      var rows = (g.rows || []).map(function (r) {
        var neg = num(r.mean_eur) < 0;
        return row([E.C(label(r.cohort_value)), E.N(f.qty(r.n_closed)), E.N(f.eur(r.purchase)), E.N(f.eur(r.mean_eur), { neg: neg }), E.N(pctRaw(r.pct_purchase), { neg: neg })], false);
      });
      var names = (g.rows || []).map(function (r) { return label(r.cohort_value); }).join(', ');
      var firstDef;
      if (isTerm) firstDef = 'die vereinbarte Laufzeit des Mietvertrags in Monaten; jede Zeile rechnet über die abgeschlossenen Kreisläufe mit dieser Laufzeit';
      else if (g.label === 'Nach Verkaufskanal') firstDef = 'an wen verkauft wurde: ' + names + '; jede Zeile rechnet über die abgeschlossenen Kreisläufe dieses Kanals';
      else if (g.label === 'Nach Bezugsweg') firstDef = 'von wem gekauft wurde: ' + names + '; jede Zeile rechnet über die abgeschlossenen Kreisläufe dieses Bezugswegs';
      else firstDef = 'die Gruppe, über die die Zeile rechnet: ' + names;
      return E.TABLE(KEY[g.label] || ('c-other-' + i), g.label, [
        E.H(first), E.H('Geräte', 1), E.H('Einkaufspreis', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('in % Einkaufspreis', 1)
      ], rows, {
        defs: [{ k: first, v: firstDef }, DEF.qty, DEF.purchase, DEF.margin, DEF.pct],
        foot: i === others.length - 1 ? othersNote : ''
      });
    });

    /* ---------- Tabelle 5: Kosten bis Verkauf, aufgeschlüsselt ---------- */
    var tcoRows = tco.map(function (r) {
      return row([E.C(LT[r.line_type] || r.line_type), E.N(f.eur(r.eur_per_closed)), E.N(f.qty(r.n_devices)), E.N(f.eur(r.eur_per_affected)), E.N(f.eur(r.eur))], false);
    });
    if (tot) tcoRows.push(row([E.C('Kosten bis Verkauf, Summe'), E.N(f.eur(tot.eur_per_closed)), E.N(f.qty(tot.n_devices)), E.N(f.eur(tot.eur_per_closed)), E.N(f.eur(tot.eur))], true));
    var tTco = E.TABLE('c-tco', 'Kosten bis Verkauf, aufgeschlüsselt, Mittelwert je abgeschlossenem Gerät', [
      E.H('Kostenzeile'), E.H('Euro je Gerät, alle', 1), E.H('Geräte mit Kostenart', 1), E.H('Euro je betroffenes Gerät', 1), E.H('Summe', 1)
    ], tcoRows, {
      n: tco.length,
      defs: [
        { k: 'Kostenzeile', v: 'Kostenart aus dem Geräte-Hauptbuch; jede Buchung dort ist eine Rechnungsposition, ein Versandprotokoll, ein Servicefall oder ein Lagertag-Satz je Seriennummer' },
        { k: 'Euro je Gerät, alle', v: 'Summe der Zeile geteilt durch alle abgeschlossenen Geräte, auch die ohne diese Kostenart; diese Spalte addiert sich zu den Kosten bis Verkauf der Kachel oben (letzte Zeile)' },
        { k: 'Geräte mit Kostenart', v: 'wie viele der abgeschlossenen Geräte diese Kostenart überhaupt hatten; Reparatur zum Beispiel nur die reparierten' },
        { k: 'Euro je betroffenes Gerät', v: 'Summe der Zeile geteilt durch die Geräte, die sie hatten; bei Reparatur also die Kosten je repariertem Gerät' },
        { k: 'Summe', v: 'Euro dieser Kostenart über alle abgeschlossenen Kreisläufe' }
      ],
      foot: 'Jede Zeile stammt aus Buchungen mit Herkunft im Geräte-Hauptbuch. Drei Zeilen sind Schätzungen: Lagertage (Tage im Lager mal Lagerkosten je Tag, Verantwortlich CFO) sowie Nutzerbetreuung und Geräteverwaltung (Satz je Gerätemonat, Verantwortlich Leitung Service).'
    });

    /* ---------- Tabelle 6: hinterlegte Kostendefinition (heute ein Klappblock; die Hülle kennt Tabellen nur in tables) ---------- */
    var defRows = COSTDEF.map(function (r) {
      return row([E.C(r[0]), E.C(r[1]), E.C(r[2]), E.C(r[3]), E.C(r[4], { bold: r[4] === 'ja, immer' }), E.C(r[5])], false);
    });
    var phases = uniq(COSTDEF.map(function (r) { return r[1]; })).join(', ');
    var tDef = E.TABLE('c-costdef', 'Hinterlegte Kostendefinition: was in Einkaufspreis und Kosten bis Verkauf drin ist, was nicht, was noch fehlt', [
      E.H('Kostenzeile'), E.H('Phase'), E.H('Herkunft'), E.H('Gebucht bei'), E.H('Geschätzt'), E.H('Verantwortlich')
    ], defRows, {
      collapsible: true,
      note: 'Gemeint sind die Gesamtkosten des Leasinghauses (Total Cost of Ownership, TCO): jeder Euro, den das Leasinghaus für eine Seriennummer von der Bestellung bis zum Zahlungseingang aus dem Wiederverkauf ausgibt; oben erscheinen sie getrennt als Einkaufspreis und Kosten bis Verkauf, nie als eine Summe. Nicht gemeint sind die Kosten des Kunden (Softwarelizenzen, Nutzerhotline, Ausfallzeiten). Lifecycle-Marge je Gerät = Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf; die Preisschutz-Gutschrift des Herstellers ist eine eigene Erlöszeile im Hauptbuch und zählt zur Marge. Hinterlegt in docs/TCO_DEFINITION.md und docs/LEDGER.md im Repo.',
      defs: [
        { k: 'Kostenzeile', v: 'die Preis- oder Kostenzeile des Geräte-Hauptbuchs, die die Zeile beschreibt' },
        { k: 'Phase', v: 'der Abschnitt des Kreislaufs, in dem die Kosten anfallen: ' + phases },
        { k: 'Herkunft', v: 'der Beleg, aus dem die Buchung stammt' },
        { k: 'Gebucht bei', v: 'das Ereignis, mit dem die Buchung ins Geräte-Hauptbuch kommt' },
        { k: 'Geschätzt', v: 'ob die Zeile eine Schätzung ist: nein, nur bis der Beleg da ist, oder immer' },
        { k: 'Verantwortlich', v: 'die Rolle, die den geschätzten Wert setzt; leer, wenn die Zeile nie geschätzt wird' }
      ],
      foot: 'Nicht drin, mit Absicht: Softwarelizenzen und Sicherheitssoftware auf dem Gerät (Kosten des Kunden, auch die MDM-Lizenz), Produktivitätsausfälle und Schulung (Kunde), Abschreibungen (Managementsicht, kein Zahlungseingang und keine Zahlung), Kapitalkosten über die Lagerkosten hinaus. Drin als Umlage, weil Teamkosten und keine Buchung je Seriennummer: Nutzerbetreuung (First-Level-Support) und Geräteverwaltung (MDM-Betrieb) je Gerätemonat, ein Satz je Mietrechnung, Platzhalter ohne öffentliche Quelle, Verantwortlich Leitung Service; jede dieser Zeilen ist als geschätzt markiert.'
    });

    /* ---------- Klappblöcke: Hinweise ---------- */
    var top = levers.length ? levers[0] : null;
    var blocks = [
      { key: 'c-levers', title: 'Wo ziehen wir an', ordered: false,
        intro: 'Die sieben Stellschrauben haben einen eigenen Reiter: je Stellschraube die Frage, der Rechenweg, die Herleitung der Jahreszahl, ein vorgerechnetes Gerät, die verantwortliche Rolle und die Regel.',
        items: top ? [{ lead: 'Größte Stellschraube in der Simulation:', text: (LN[top.lever_name] || top.lever_name) + ' (Verantwortlich: ' + owner(top.threshold_owner) + '). Jahreszahl und Herleitung stehen auf dem Reiter Stellschrauben.' }] : [] },
      { key: 'c-tool', title: 'Wie das Werkzeug aufgebaut ist (v0.2)', ordered: false, items: [
        { lead: 'Vier Datenschichten:', text: 'Rohdateien je Quelle (unverändert); geprüfte Zeilen mit Seriennummer und Quellverweis, Doppelte und Ungeklärtes gezählt; das Geräte-Hauptbuch (jede Kosten- und Erlöszeile mit Herkunft); Kennzahlen, Gruppen und Stellschrauben.' },
        { lead: 'Quellen:', text: 'Bestellungen und Lieferantenrechnungen (ERP), Einrichtung vor Versand und Versand (Lager), Mietverträge und Mietrechnungen (Portal), Tickets (Service), Rückläufer mit Zustandsprüfung und Löschzertifikat, Aufbereitung, Verkaufsaufträge und Gutschriften je Kanal, Vertragsregister, indirekte Ausgaben.' },
        { lead: 'Prinzip:', text: 'das Modell berät, deterministischer Code entscheidet, ein benannter Mensch ist für jede Schwelle verantwortlich. Kosten sind Summen aus Zeilen, nie ein Prozentsatz auf eine Summe, wenn eine Transaktion existiert.' },
        { lead: 'Offene Kreisläufe', text: 'zeigen zwei getrennte Zahlen: bei Verkauf heute und am Leasingende voraussichtlich. Nie addiert.' }
      ] }
    ];

    return {
      kicker: 'Simulation, Stand ' + f.de(D.today),
      subject: 'Der Gerätekreislauf: was bleibt je Gerät?',
      intro: lead + ' ' + banner,
      steps: steps,
      kpis: kpis,
      kpiDefs: kpiDefs,
      tables: [tFam].concat(tOthers, [tTco, tDef]),
      blocks: blocks
    };
  };
  w.RE.cycle.version = 3;
})(window);
