/* Restwert Engine v3: Motor Bericht. Klassisches Skript, definiert window.RE.report(D, opts, P).
   Port von v3/src/report.js und v3/src/report.body.html: dieselben Saetze, dieselben Zahlen, dieselben Tabellen und
   Legenden, nur als Ansichtsmodell nach v3/CONTRACT.md (Abschnitte 2, 4, 5 und 7.1). Kein Diagramm, keine Sonderfelder,
   keine Optionen. Jede Zahl kommt aus D und geht durch E.fmt. */
(function (w) {
  'use strict';
  var E = w.RE;

  var SIM = { cls: 'tag-neutral', text: 'simuliert' };
  var DER = { cls: 'tag-neutral', text: 'abgeleitet' };

  function nn(x) { return x === null || x === undefined || isNaN(x); }
  function sgn(s) { return s.charAt(0) === '-' ? s : '+' + s; }
  /* Absatz zu Listenpunkt: lead = die Woerter bis zum ersten Doppelpunkt, sonst leer (Vertrag 7.1) */
  function item(text) {
    var i = text.indexOf(': ');
    return i > 0 ? { lead: text.slice(0, i + 1), text: text.slice(i + 2) } : { lead: '', text: text };
  }
  function def(k, v) { return { lead: k + ':', text: v }; }

  w.RE.report = function (D, opts, P) {
    var f = E.fmt, de = f.de, qty = f.qty, eur = f.eur, pct1 = f.pct1;
    /* Monat und Jahr aus einem Tagesdatum: 'JJJJ-MM-TT' wird 'MM.JJJJ' (heute mj) */
    var mj = function (iso) { return iso ? de(String(iso).slice(0, 7)) : ''; };
    /* Veraenderung gegen die Vorperiode: Prozentpunkte bei Anteilen, Euro bei Summen, Tage bei Tagen */
    var dpp = function (a, b) { return (nn(a) || nn(b)) ? '' : sgn(f.num((a - b) * 100, 1)) + ' Punkte'; };
    var deur = function (a, b) { return (nn(a) || nn(b)) ? '' : sgn(eur(a - b)); };
    var ddays = function (a, b) { if (nn(a) || nn(b)) return ''; var d = Math.round(a - b); return sgn(qty(d)) + (Math.abs(d) === 1 ? ' Tag' : ' Tage'); };
    /* Die Summe der auf Euro gerundeten Glieder kann die gerundete Summe um einen Euro verfehlen: dann sagt es die Zeile */
    var rn = function (parts, total) { var s = 0; parts.forEach(function (p) { s += Math.round(p); }); return s !== Math.round(total) ? '; Glieder auf Euro gerundet' : ''; };

    var P_ = D.pnl, R = D.rental, S = D.supplier, I = D.impact, ST = D.status, K = D.kpi, FL = ST.fleet;
    var S0 = P_.z1.sum + P_.z2.sum + P_.z3.sum + P_.z4.sum + P_.z5.sum, S1 = S0 + P_.z6.sum, S2 = S1 + P_.z7.sum + P_.z8.sum, Z3a = P_.z2.sum + P_.z3.sum;

    /* ---------- Kopf ---------- */
    var kicker = 'Momentaufnahme für die Geschäftsführung, Stand ' + de(D.today) + ' (Stichtag der Daten ' + de(D.as_of) + ')';
    var subject = 'Wo das Gerätegeschäft steht: zwölf Monate bis ' + de(D.as_of);
    var lead = 'Mieterlös, Restwert, EBITDA und EBIT als Näherung, die Kennzahlen, die Verträge, die in den nächsten zwölf Monaten enden, und was das Team bewegt hat; jede Zahl kommt aus dem Geräte-Hauptbuch, dem Vertragsregister oder den Konfigurationsdateien, nichts ist getippt.';
    var banner = 'Simulierte Daten, echte Mechanik: ' + qty(D.n_serials) + ' Seriennummern aus dem echten Katalog, jede Buchung erfunden, kein Wert ist eine Tatsache über ein reales Unternehmen; mit den Daten eines Hauses zeigt dieselbe Seite dessen Zahlen.';

    /* ---------- Gewinn und Verlust: der Motor addiert nur; Erloese kommen positiv, Aufwand negativ aus den Daten ---------- */
    var dblZ6 = P_.z6.dbl_names[0] + ' ' + eur(P_.z6.dbl.repair) + ' und ' + P_.z6.dbl_names[1] + ' ' + eur(P_.z6.dbl.refurbishment);
    var dblZ5 = eur(P_.z5.dbl.repair) + ' und ' + eur(P_.z5.dbl.refurbishment);
    var pnl = [
      { id: 'Z1', name: 'Mieterlös', val: P_.z1.sum, qty: P_.z1.n, basis: 'Mietrechnungen', s0: true },
      { id: 'Z2', name: 'Restwert', val: P_.z2.sum, qty: P_.z2.n, basis: 'erzielter Verkaufspreis nach Verkaufsdatum, vor Kanalgebühren', s0: true },
      { id: 'Z3', name: 'Restbuchwert der Abgänge', val: P_.z3.sum, qty: P_.z3.n, basis: 'Einkaufspreis minus aufgelaufene Abschreibung nach der Monatsregel der Zeile Planmäßige Abschreibung, minus frühere Wertberichtigungen; abgeleitet', s0: true },
      { id: 'Z3a', name: 'Veräußerungsergebnis', tag: DER, val: Z3a, qty: null, basis: 'Restwert minus Restbuchwert; verschrottet zählt mit 0 €' + rn([P_.z2.sum, P_.z3.sum], Z3a) },
      { id: 'Z4', name: 'Preisschutz-Gutschriften', val: P_.z4.sum, qty: P_.z4.n, basis: 'Gutschriften des Herstellers; als Ertrag der Periode gezeigt, in der Bilanz eine Minderung der Anschaffungskosten, hier vereinfacht', s0: true },
      { id: 'Z5', name: 'Kosten bis Verkauf', val: P_.z5.sum, qty: P_.z5.n, basis: qty(P_.z5.n_types) + ' Kostenarten, Fracht bis Kanalgebühren; davon geschätzt ' + eur(P_.z5.est) + ': Lagertage ' + eur(P_.z5.est_hold) + ', Umlage Nutzerbetreuung und Geräteverwaltung je Gerätemonat ' + eur(P_.z5.est_alloc || 0) + ', Kanalgebühren geschätzt bis zum Zahlungseingang ' + eur(P_.z5.est_fee), s0: true },
      { id: 'S0', name: 'Zwischensumme Gerätegeschäft, vor indirekten Ausgaben', sum: 's0', basis: 'Mieterlös plus Veräußerungsergebnis plus Preisschutz-Gutschriften minus Kosten bis Verkauf' + rn([P_.z1.sum, P_.z2.sum, P_.z3.sum, P_.z4.sum, P_.z5.sum], S0) },
      { id: 'Z6', name: 'Indirekte Ausgaben', val: P_.z6.sum, qty: P_.z6.n, basis: qty(P_.z6.n_cat) + ' Kategorien (' + P_.z6.cats.join(', ') + '), Platzhalterband des Generators (' + qty(P_.z6.rows_per_month) + ' Rechnungen je Monat, config/lake.yaml); nicht enthalten: ' + dblZ6 + ' (' + qty(P_.z6.dbl_n) + ' Rechnungen), weil beides je Gerät (' + dblZ5 + ') schon in Kosten bis Verkauf steht', s1: true },
      { id: 'S1', name: 'EBITDA-Näherung', sum: 's1', basis: 'Zwischensumme Gerätegeschäft minus indirekte Ausgaben; vor Abschreibung, Wertberichtigung, Zinsen, Steuern' + rn([S0, P_.z6.sum], S1) },
      { id: 'Z7', name: 'Planmäßige Abschreibung', val: P_.z7.sum, qty: P_.z7.n, basis: 'linear je Vertragsmonat; Restwertprognose bei Rückgabe für ' + qty(P_.z7.n_rec) + ' Geräte, zum Leasingende für ' + qty(P_.z7.n_lease) + ' Geräte, ohne Prognose ' + qty(P_.z7.n_none) + '; ' + qty(P_.z7.n_repl) + ' Ersatzverträge mit Laufzeit bis zum Ende des ersetzten Vertrags', s2: true },
      { id: 'Z8', name: 'Wertberichtigungen', val: P_.z8.sum, qty: P_.z8.n, basis: (P_.z8.rules ? 'Regel ' + P_.z8.rules : 'keine im Fenster') + ', Verantwortlich ' + P_.z8.owner, s2: true },
      { id: 'S2', name: 'EBIT-Näherung', sum: 's2', basis: 'vor Zinsen und Steuern' + rn([S1, P_.z7.sum, P_.z8.sum], S2) }
    ];
    var chkS0 = 0; pnl.forEach(function (r) { if (r.s0) chkS0 += r.val; });
    var chkS1 = chkS0; pnl.forEach(function (r) { if (r.s1) chkS1 += r.val; });
    var chkS2 = chkS1; pnl.forEach(function (r) { if (r.s2) chkS2 += r.val; });
    if (Math.abs(chkS0 - S0) > 0.005 || Math.abs(chkS1 - S1) > 0.005 || Math.abs(chkS2 - S2) > 0.005 || Math.abs(S0 - P_.s0) > 0.005 || Math.abs(S1 - P_.s1) > 0.005 || Math.abs(S2 - P_.s2) > 0.005) throw new Error('Zeilensumme der G&V weicht ab');
    var pnlRows = pnl.map(function (r) {
      var v = r.sum === 's0' ? S0 : (r.sum === 's1' ? S1 : (r.sum === 's2' ? S2 : r.val));
      return E.ROW([
        E.C(r.name, r.tag ? { tag: r.tag.cls, tagText: r.tag.text } : {}),
        E.N(eur(v), { neg: v < 0 }),
        E.N(r.qty === null || r.qty === undefined ? '' : qty(r.qty)),
        E.C(r.basis, { minW: 260 })
      ], { bold: !!r.sum });
    });
    var pnlFoot = 'Anlage-Sicht: die Geräte gelten hier als Anlagevermögen und werden linear auf die Restwertprognose abgeschrieben; ob das Haus sie als Anlage oder als Vorrat führt, ist offen und entscheidet die Bilanzierung, Verantwortlich CFO. Nicht enthalten: Finanzierungskosten, Steuern und das Personal des Teams (steht nicht im Geräte-Hauptbuch); Fracht, Zoll und Einrichtung sind hier Periodenaufwand, in einer Bilanz wären sie Anschaffungsnebenkosten; die Einkaufspreise der ' + qty(P_.n_pp) + ' im Fenster gekauften Geräte (' + eur(P_.sum_pp) + ') sind aktiviert und laufen über die Abschreibung, nicht über diese Tabelle.';
    var pnlDefs = [
      /* die beiden ersten Spalten haben heute keinen Legendensatz; der Vertrag verlangt einen je Spalte */
      { k: 'Zeile', v: 'Zeile der Gewinn- und Verlustrechnung; Zwischensummen stehen fett.' },
      { k: 'Euro zwölf Monate', v: 'Summe der Zeile in den zwölf Monaten bis zum Stichtag, in Euro; Erlöse positiv, Aufwand negativ.' },
      { k: 'Anzahl', v: 'Rechnungszeilen, Geräte oder Rechnungen, wie die Spalte Grundlage sagt.' },
      { k: 'Grundlage', v: 'woher die Zahl kommt und ob sie gebucht oder abgeleitet ist.' },
      { k: 'Mieterlös', v: 'was die Kunden in den zwölf Monaten für die Geräte gezahlt haben, ohne Mehrwertsteuer, nach Rechnungsdatum.' },
      { k: 'Restwert', v: 'was verkaufte Geräte gebracht haben, vor Abzug der Kanalgebühren; die Gebühren stehen in Kosten bis Verkauf.' },
      { k: 'Restbuchwert der Abgänge', v: 'der Wert, mit dem die verkauften und verschrotteten Geräte noch in den Büchern standen: Einkaufspreis minus planmäßige Abschreibung bis zum Vertragsende, bei vorzeitiger Rückgabe bis zur Rückgabe, nach derselben Monatsregel wie die Zeile Planmäßige Abschreibung, minus frühere Wertberichtigungen.' },
      { k: 'Veräußerungsergebnis', v: 'Restwert minus Restbuchwert beim Abgang; negativ heißt unter Buchwert verkauft.' },
      { k: 'Preisschutz-Gutschriften', v: 'Gutschriften des Herstellers, weil er den Listenpreis kurz nach unserem Kauf gesenkt hat; hier als Ertrag der Periode, in einer Bilanz eine Minderung der Anschaffungskosten.' },
      { k: 'Zwischensumme Gerätegeschäft', v: 'was das Gerätegeschäft vor den indirekten Ausgaben verdient: Mieterlös plus Veräußerungsergebnis plus Preisschutz-Gutschriften minus Kosten bis Verkauf.' },
      { k: 'Kosten bis Verkauf', v: 'Fracht, Zoll, Einrichtung, Versand, Nutzerbetreuung und Geräteverwaltung je Gerätemonat (Umlage), Reparatur, Austauschversand, Rücksendung, Datenlöschung und Zustandsprüfung, Aufbereitung, Lagertage, Kanalgebühren; alles als Aufwand der Periode; Lagertage und die zwei Umlagen sind Schätzungen' },
      { k: 'Indirekte Ausgaben', v: 'Rechnungen außerhalb des einzelnen Geräts; in der Simulation ein Platzhalterband (config/lake.yaml, Verantwortlich Leitung Indirekter Einkauf), auf echten Daten die Kreditorenbuchhaltung. Reparatur und Aufbereitung stehen in der Simulation auch als Rechnungen hier, sind aber je Gerät schon in Kosten bis Verkauf gebucht; deshalb zählen sie in dieser Zeile nicht (Betrag in der Zeile). Auf echten Daten gilt dieselbe Regel: jede Kostenart genau einmal, je Gerät oder als Rechnung.' },
      { k: 'EBITDA-Näherung', v: 'Ergebnis vor Abschreibung, Wertberichtigung, Zinsen und Steuern; eine Näherung, weil Personal und Finanzierung fehlen und die indirekten Ausgaben in der Simulation ein Platzhalterband sind.' },
      { k: 'Planmäßige Abschreibung', v: 'der Teil des Einkaufspreises, der in diesen zwölf Monaten verbraucht wurde: Einkaufspreis minus Restwertprognose zum Vertragsende, gleichmäßig auf die Vertragsmonate zwischen Vertragsbeginn und vertraglichem Ende verteilt; gebucht wird jeder Vertragsmonat, der vor dem wirksamen Ende beginnt.' },
      { k: 'Wertberichtigungen', v: 'außerplanmäßige Abschreibung nach der Regel, die die Zeile nennt; die Regel entscheidet, eine verantwortliche Rolle setzt die Schwelle.' },
      { k: 'EBIT-Näherung', v: 'Ergebnis vor Zinsen und Steuern.' }
    ];
    var tPnl = E.TABLE('r-pnl', 'Gewinn und Verlust, zwölf Monate bis ' + de(D.as_of),
      [E.H('Zeile'), E.H('Euro zwölf Monate', 1), E.H('Anzahl', 1), E.H('Grundlage')], pnlRows,
      { n: 0, foot: pnlFoot, defs: pnlDefs, tags: [SIM, DER] });

    /* ---------- Kacheln ---------- */
    var z7 = P_.z7.sum, z8 = P_.z8.sum;
    var kpis = [
      { label: 'Mieterlös, zwölf Monate', tags: [SIM], value: eur(P_.z1.sum), neg: P_.z1.sum < 0, lines: [
        qty(P_.z1.n) + ' Mietrechnungen (Monatszeilen) für ' + qty(P_.z1.n_serials) + ' Geräte',
        de(D.win_start1) + ' bis ' + de(D.as_of) + ', Rechnungsdatum',
        'nicht enthalten: künftige Monate der ' + qty(R.n_active) + ' laufenden Verträge'] },
      { label: 'Restwert, zwölf Monate', tags: [SIM], value: eur(P_.z2.sum), neg: P_.z2.sum < 0, lines: [
        qty(P_.z2.n) + ' verkaufte Geräte, vor Abzug der Kanalgebühren (' + eur(D.tiles.fees) + ')',
        'gegen Restwertprognose bei Rückgabe ' + pct1(D.tiles.ratio_record) + ' (' + qty(I.w3.n) + ' Verkäufe ohne Ist-Zustand)',
        'dazu ' + qty(D.tiles.n_scrapped) + ' verschrottete Geräte mit 0 €'] },
      { label: 'EBITDA-Näherung, zwölf Monate', tags: [SIM, DER], value: eur(S1), neg: S1 < 0, lines: [
        'Gerätegeschäft ' + eur(S0) + ' minus indirekte Ausgaben ' + eur(Math.abs(P_.z6.sum)) + '; die indirekten Ausgaben sind in der Simulation ein Platzhalterband (' + qty(P_.z6.rows_per_month) + ' Rechnungen je Monat, config/lake.yaml), keine Tatsache; Reparatur und Aufbereitung (' + eur(P_.z6.dbl_sum) + ') zählen hier nicht, sie stehen je Gerät schon in Kosten bis Verkauf',
        'vor Abschreibung, Wertberichtigung, Zinsen und Steuern; Personal des Teams fehlt, es steht nicht im Geräte-Hauptbuch',
        'Anlage-Sicht als Näherung, Verantwortlich CFO'] },
      { label: 'EBIT-Näherung, zwölf Monate', tags: [SIM, DER], value: eur(S2), neg: S2 < 0, lines: [
        'EBITDA-Näherung minus planmäßige Abschreibung ' + eur(Math.abs(z7)) + ' minus Wertberichtigungen ' + eur(Math.abs(z8)),
        'Abschreibung linear je Vertragsmonat vom Einkaufspreis auf die Restwertprognose zum Vertragsende',
        'ob das Gerät beim Haus Anlage oder Vorrat ist, ist eine offene Frage an das Haus; hier Anlage'] }
    ];
    var kpiDefs = [
      { k: 'Näherung', v: 'EBITDA und EBIT sind aus dem Geräte-Hauptbuch abgeleitet, nicht aus einer Buchhaltung; die Tabelle darunter zeigt jede Zeile und ihre Herkunft.' },
      { k: 'simuliert, abgeleitet', v: 'simuliert: Buchung des Generators; abgeleitet: aus Buchungen gerechnet, keine eigene Buchung.' }
    ];

    /* ---------- Kennzahlen: die Spalte Ziel erscheint erst, wenn ein Ziel gesetzt ist; ein nicht messbarer Wert steht nur in der Legende ---------- */
    var fmtv = function (v, u) { if (v === null || v === undefined) return ''; if (u === 'ratio') return pct1(v); if (u === 'EUR') return eur(v); if (u === 'days') return qty(v) + ' Tage'; return f.num(v, 1); };
    var stDe = function (s) { return s === 'ok' ? 'ok' : (s === 'not_measurable' ? 'nicht messbar' : s); };
    var showT = K.n_targets > 0;
    var kpiCols = [E.H('Kennzahl'), E.H('Wert', 1)].concat(showT ? [E.H('Ziel', 1)] : []).concat([E.H('Status'), E.H('Verantwortlich')]);
    var kpiRows = [], lastG = null, nm = [];
    K.rows.forEach(function (r) {
      if (r.group !== lastG) {
        var g = [E.C(r.group, { bold: true })];
        while (g.length < kpiCols.length) g.push(E.C(''));
        kpiRows.push(E.ROW(g, { bold: true }));
        lastG = r.group;
      }
      var nmeas = r.status === 'not_measurable', mn = K.min_n[r.id];
      var wert = nmeas ? 'nicht messbar (' + qty(r.n) + ' von mindestens ' + qty(mn) + ' Beobachtungen)' : fmtv(r.value, r.unit);
      if (nmeas) nm.push(r.name + ' ' + fmtv(r.value, r.unit) + ' bei ' + qty(r.n) + (r.n === 1 ? ' Beobachtung' : ' Beobachtungen'));
      var cells = [E.C(r.name), nmeas ? E.C(wert, { right: true }) : E.N(wert)];
      if (showT) cells.push(E.N(fmtv(r.target, r.unit)));
      cells.push(E.C(stDe(r.status)), E.C(f.role(r.owner)));
      kpiRows.push(E.ROW(cells));
    });
    var tcoRow = K.rows.filter(function (r) { return r.id === 'KPI_TCO_PER_CLOSED_DEVICE'; })[0];
    var zielDd = showT
      ? 'für ' + qty(K.n_targets) + ' der ' + qty(K.n_shown) + ' Kennzahlen gesetzt (config/kpi_targets.yaml, Verantwortlich ' + f.role(K.targets_owner) + '); leer heißt kein Ziel gesetzt.'
      : 'für keine der ' + qty(K.n_shown) + ' gezeigten Kennzahlen gesetzt: config/kpi_targets.yaml führt ' + qty(K.n_cfg_targets) + ' Ziele, davon ' + qty(K.n_cfg_targets_gold) + ' für eine der ' + qty(K.n_gold) + ' Kennzahlen der Kennzahlentabelle (gold.kpi_values), die anderen für alte Kennungen; Verantwortlich ' + f.role(K.targets_owner) + '; die Spalte Ziel erscheint, sobald ein Ziel gesetzt ist.';
    var kpiTableDefs = [
      { k: 'Kennzahl', v: 'Name der Kennzahl; die zwölf Monate bis zum Stichtag, wo die Kennzahl ein Fenster hat (Einkauf, Kosten, Recommerce, Lifecycle-Marge, Verträge), zum Stichtag nur bei Daten; Geld, das noch liegt, ist ein Betrag je Jahr aus der Hebelrechnung (Reiter Stellschrauben).' },
      { k: 'Wert', v: 'in Prozent, Euro oder Tagen; mittleres Gerät heißt: die Hälfte der Geräte liegt darüber, die Hälfte darunter.' },
      { k: 'Ziel', v: zielDd },
      { k: 'Status', v: 'ok: gemessen; nicht messbar: unter der Mindeststückzahl (config/kpi_targets.yaml, Verantwortlich ' + f.role(K.min_n_owner) + ') oder ohne Nenner' + (nm.length ? '; der gespeicherte Wert steht nur hier zur Orientierung: ' + nm.join('; ') : '') + '.' },
      { k: 'Verantwortlich', v: 'Rolle, die die Kennzahl und ihr Ziel verantwortet.' }
    ];
    if (tcoRow) kpiTableDefs.push({ k: 'TCO je Gerät', v: 'Einkaufspreis plus alle Kosten bis Verkauf (Fracht und Zoll eingeschlossen), Mittel je Gerät über die ' + qty(tcoRow.n) + ' in den zwölf Monaten abgeschlossenen Kreisläufe; der Einkaufspreis ist darin enthalten, anders als in der Zeile Kosten bis Verkauf oben; Einzelheiten auf dem Reiter TCO.' });
    kpiTableDefs.push({ k: 'Anteil geschätzter Kostenzeilen', v: 'bezogen auf diese Summe mit Einkaufspreis; in der Tabelle Gewinn und Verlust sind ' + pct1(P_.z5.est / Math.abs(P_.z5.sum)) + ' der Kosten bis Verkauf geschätzt, weil dort der Einkaufspreis fehlt.' });
    var tKpi = E.TABLE('r-kpi', 'Kennzahlen, Stand ' + de(D.as_of), kpiCols, kpiRows, { n: K.n_shown, defs: kpiTableDefs, tags: [SIM] });

    /* ---------- Mietvertraege, die enden ---------- */
    var rentalTitle = 'Mietverträge, die bis ' + de(D.fw_end) + ' enden';
    var rentalHead = qty(R.n_end) + ' Verträge enden zwischen ' + de(D.fw_start1) + ' und ' + de(D.fw_end) + ': Mieterlös je Monat ' + eur(R.sum_rate) + ' fällt weg, Restwertprognose der Rückläufer zusammen ' + eur(R.sum_rv) + ' (' + qty(R.n_norv) + ' Geräte ohne Prognose); heute laufen ' + qty(R.n_active) + ' Verträge mit ' + eur(R.rate_active) + ' je Monat.';
    var qn = 0, qr = 0, qv = 0;
    var qRows = R.quarters.map(function (q) {
      qn += q.n; qr += q.rate; qv += q.rv;
      return E.ROW([E.C(q.q + '. Quartal ' + q.y), E.N(qty(q.n)), E.N(eur(q.rate)), E.N(eur(q.rv))]);
    });
    qRows.push(E.ROW([
      E.C('zwölf Monate zusammen, ' + qty(R.n_quarters) + ' Quartale' + (rn(R.quarters.map(function (q) { return q.rate; }), qr) || rn(R.quarters.map(function (q) { return q.rv; }), qv))),
      E.N(qty(qn)), E.N(eur(qr)), E.N(eur(qv))
    ], { bold: true }));
    var defQuartal = { k: 'Quartal', v: 'Kalenderquartal, in das das vertragliche Enddatum fällt.' };
    var defQtyEnd = { k: 'endende Verträge', v: 'laufende Mietverträge mit Enddatum im Quartal; vorzeitige Rückgaben stehen nicht darin.' };
    var defRateText = 'Summe der Monatsmieten dieser Verträge, ohne Mehrwertsteuer; fällt ab dem Enddatum weg, wenn nicht verlängert.';
    var defRv = { k: 'Restwertprognose der Rückläufer', v: 'Summe der Restwertprognose zum Leasingende der Geräte hinter diesen Verträgen, Stand Stichtag; keine Buchung.' };
    var tRentalQ = E.TABLE('r-rental-q', rentalTitle,
      [E.H('Quartal'), E.H('endende Verträge', 1), E.H('wegfallender Mieterlös je Monat', 1), E.H('Restwertprognose der Rückläufer', 1)], qRows,
      { n: R.n_quarters, note: rentalHead, defs: [defQuartal, defQtyEnd, { k: 'wegfallender Mieterlös je Monat', v: defRateText }, defRv], tags: [SIM] });

    var cRows = R.top.map(function (c) { return E.ROW([E.C(c.customer), E.N(qty(c.n)), E.N(eur(c.rate)), E.C(de(c.last_end), { nowrap: true })]); });
    if (R.rest.n_customers > 0) {
      cRows.push(E.ROW([
        E.C('übrige ' + qty(R.rest.n_customers) + ' Kunden' + rn(R.top.map(function (c) { return c.rate; }).concat([R.rest.rate]), R.sum_rate)),
        E.N(qty(R.rest.n)), E.N(eur(R.rest.rate)), E.C('')
      ]));
    }
    var tRentalTop = E.TABLE('r-rental-top', rentalTitle + ', je Kunde',
      [E.H('Kunde'), E.H('endende Verträge', 1), E.H('Mieterlös je Monat', 1), E.H('letztes Vertragsende')], cRows,
      { n: R.top.length, defs: [
        { k: 'Kunde', v: 'Kundenkennung aus dem Vertrag, in der Simulation ein Platzhalter.' },
        defQtyEnd,
        { k: 'Mieterlös je Monat', v: defRateText },
        { k: 'letztes Vertragsende', v: 'Enddatum des zuletzt endenden Vertrags dieses Kunden im Fenster.' }
      ], tags: [SIM] });

    /* ---------- Lieferanten- und Partnervertraege: Tabelle nach Kuendigungstermin, der Kopfsatz sagt, wann die auslaufenden enden ---------- */
    var EO = S.end_out;
    var sHead = qty(S.n_sup) + ' laufende Verträge, Jahreswert zusammen ' + eur(S.sum_av) + '; davon enden bis ' + de(D.fw_end) + ' ' + qty(S.n_sup_end) + ' mit Jahreswert ' + eur(S.av_end) + ', Kündigungstermin bei ' + qty(S.n_notice_past) + ' davon schon verstrichen, ' + qty(S.n_auto) + ' davon verlängern sich automatisch';
    if (S.n_sup_end > 0) sHead += '. Die Tabelle zeigt ' + (EO.n > 0 ? 'die ' + qty(EO.n_in) + ' mit dem nächsten Kündigungstermin; die ' + qty(EO.n) + ' anderen (Jahreswert zusammen ' + eur(EO.av) + ') enden zwischen ' + de(EO.first_end) + ' und ' + de(EO.last_end) : 'sie, nach Kündigungstermin sortiert');
    else sHead += '. Kein Vertrag endet in diesem Fenster';
    sHead += '.';
    var sRows = S.top.map(function (s) {
      return E.ROW([E.C(s.cp), E.C(s.cat), E.N(eur(s.av)), E.C(de(s.end), { nowrap: true }), E.C(de(s.notice), { nowrap: true }), E.C(s.auto ? 'ja' : 'nein'), E.C(s.pp ? 'ja' : 'nein')]);
    });
    if (S.rest.n > 0) {
      sRows.push(E.ROW([
        E.C('übrige ' + qty(S.rest.n) + ' laufende Verträge, Ende nach ' + de(D.fw_end) + rn(S.top.map(function (s) { return s.av; }).concat([EO.av, S.rest.av]), S.sum_av)),
        E.C(''), E.N(eur(S.rest.av)), E.C(''), E.C(''), E.C(''), E.C('')
      ]));
    }
    var tSupplier = E.TABLE('r-supplier', 'Lieferanten- und Partnerverträge, laufend zum Stichtag',
      [E.H('Gegenpartei'), E.H('Kategorie'), E.H('Jahreswert', 1), E.H('Vertragsende'), E.H('Kündigungstermin'), E.H('verlängert sich'), E.H('Preisschutz')], sRows,
      { n: S.top.length, note: sHead, defs: [
        { k: 'Gegenpartei', v: 'Hersteller mit echtem Namen oder Partner nur als Rolle (nur Rolle), keine Tatsache über ein reales Unternehmen.' },
        { k: 'Kategorie', v: 'was der Vertrag abdeckt.' },
        { k: 'Jahreswert', v: 'vertraglich erwartete Ausgaben je Jahr, ohne Mehrwertsteuer; die Tabelle zeigt die Verträge, die in den nächsten zwölf Monaten enden, nach Kündigungstermin, höchstens fünf; alle anderen stehen nur als Summe.' },
        { k: 'Vertragsende', v: 'vertragliches Enddatum.' },
        { k: 'Kündigungstermin', v: 'letzter Tag, an dem gekündigt werden kann (Enddatum minus Kündigungsfrist); liegt er vor dem Stichtag, ist die Frist verstrichen, und die Spalte verlängert sich sagt, was dann gilt.' },
        { k: 'verlängert sich', v: 'ja: ohne Kündigung verlängert sich der Vertrag automatisch; nein: er endet am Vertragsende.' },
        { k: 'Preisschutz', v: 'ob der Vertrag eine Preisschutz-Gutschrift bei Preissenkung des Herstellers zusichert.' }
      ], tags: [SIM] });

    /* ---------- Wirkung des Teams, Fenster gegen die zwoelf Monate davor ---------- */
    var w2st = I.w2.status === 'not_measurable' ? 'ist unter der Mindeststückzahl ' + qty(I.w2.min_n) + ' nicht messbar' : 'ist ' + stDe(I.w2.status);
    var IR = [
      ['Einkaufsrabatt gegen UVP ohne Mehrwertsteuer', 'simuliert', I.w1.eur, pct1(I.w1.ratio) + ', ' + eur(I.w1.eur), pct1(I.w1.prev.ratio) + ', ' + eur(I.w1.prev.eur), dpp(I.w1.ratio, I.w1.prev.ratio) + ', ' + deur(I.w1.eur, I.w1.prev.eur),
        'UVP ohne Mehrwertsteuer minus gezahlter Preis nach Preisschutz-Gutschrift, ' + qty(I.w1.n) + ' im Fenster gelieferte Geräte (Vorperiode ' + qty(I.w1.prev.n) + ')', I.w1.owner],
      ['Preisschutz-Gutschriften', 'simuliert', I.w2.sum, eur(I.w2.sum), eur(I.w2.prev.sum), deur(I.w2.sum, I.w2.prev.sum),
        qty(I.w2.n) + (I.w2.n === 1 ? ' Gutschrift' : ' Gutschriften') + ' nach Gutschriftdatum (Vorperiode ' + qty(I.w2.prev.n) + '); die Kennzahl Preisschutz-Gutschriften erfasst zählt ' + qty(I.w2.kpi_n) + (I.w2.kpi_n === 1 ? ' Gerät' : ' Geräte') + ' mit Anspruch (gutgeschrieben oder verpasst) unter den im Fenster gelieferten und ' + w2st, I.w2.owner],
      ['Restwert gegen Restwertprognose bei Rückgabe', 'simuliert, abgeleitet', I.w3.delta, pct1(I.w3.ratio) + ', ' + eur(I.w3.delta), pct1(I.w3.prev.ratio) + ', ' + eur(I.w3.prev.delta), dpp(I.w3.ratio, I.w3.prev.ratio) + ', ' + deur(I.w3.delta, I.w3.prev.delta),
        'Restwert minus Restwertprognose bei Rückgabe, ' + qty(I.w3.n) + ' Verkäufe ohne Ist-Zustand (Vorperiode ' + qty(I.w3.prev.n) + '); negativ heißt unter der Prognose', I.w3.owner],
      ['Tage von Rückgabe bis Zahlungseingang, mittleres Gerät', 'simuliert', I.w4.days, qty(I.w4.days) + ' Tage', qty(I.w4.prev.days) + ' Tage', ddays(I.w4.days, I.w4.prev.days),
        'Rückgabe bis Zahlungseingang aus dem Verkauf, ' + qty(I.w4.n) + ' Verkäufe (Vorperiode ' + qty(I.w4.prev.n) + '); die Hälfte schneller, die Hälfte langsamer', I.w4.owner],
      ['Indirekte Einsparungen, vom Controlling bestätigt', 'simuliert', I.w5.confirmed, eur(I.w5.confirmed), eur(I.w5.prev.confirmed), deur(I.w5.confirmed, I.w5.prev.confirmed),
        'gemeldet ' + eur(I.w5.sum) + ', davon vom Controlling bestätigt ' + eur(I.w5.confirmed) + ', davon harte Preissenkung ' + eur(I.w5.confirmed_hard) + ', die gegen den Plan zählt' + (nn(I.plan) ? '' : ' (Plan ' + I.plan_year + ': ' + eur(I.plan) + ' je Kalenderjahr, config/kpi_targets.yaml)') + '; ' + qty(I.w5.n) + ' Rechnungen mit Einsparung (Vorperiode ' + qty(I.w5.prev.n) + ')', I.w5.owner]
    ];
    var iRows = IR.map(function (r) {
      return E.ROW([E.C(r[0], { tag: 'tag-neutral', tagText: r[1] }), E.N(r[3], { neg: r[2] < 0 }), E.N(r[4]), E.N(r[5]), E.C(r[6], { minW: 260 }), E.C(r[7])]);
    });
    var tImpact = E.TABLE('r-impact', 'Wirkung des Teams, zwölf Monate bis ' + de(D.as_of) + ' gegen die zwölf Monate davor',
      [E.H('Wirkung'), E.H('Wert', 1), E.H('Vorperiode', 1), E.H('Veränderung', 1), E.H('Rechenweg'), E.H('Verantwortlich')], iRows,
      { foot: 'In der Simulation sind das die Zahlen des Generators; mit echten Daten sind es die Zahlen des Teams.', defs: [
        { k: 'Wirkung', v: 'was das Team beeinflusst hat, in denselben Wörtern wie die Kennzahlen oben; die Zeilen nicht addieren, Zeile 2 steckt in Zeile 1 schon: die Gutschrift mindert den gezahlten Preis.' },
        { k: 'Wert', v: 'Anteil und Euro, oder Tage, für die zwölf Monate bis ' + de(D.as_of) + '.' },
        { k: 'Vorperiode', v: 'dieselbe Rechnung für die zwölf Monate davor, ' + de(I.prev_start1) + ' bis ' + de(I.prev_end) + '.' },
        { k: 'Veränderung', v: 'Wert minus Vorperiode: Prozentpunkte bei Anteilen, Euro bei Summen, Tage bei Tagen; ein Vergleich, keine Ursache.' },
        { k: 'Rechenweg', v: 'ein Halbsatz, woraus die Zahl entsteht.' },
        { k: 'Verantwortlich', v: 'Rolle, die die Kennzahl verantwortet.' }
      ] });

    /* ---------- Stand der Dinge ---------- */
    var qparts = ST.quality.map(function (q) { return q.fam + ' ' + pct1(q.mape) + ', Bias ' + pct1(q.bias) + ' (' + qty(q.n) + ' Verkäufe' + (q.n_months !== ST.q_months ? ', ' + qty(q.n_months) + ' Monate' : '') + ')'; });
    var fleetTxt = 'Restbuchwert der Flotte zum Stichtag ' + eur(FL.bv) + ' für ' + qty(FL.n) + ' offene Geräte, abgeleitet: Einkaufspreis minus Abschreibung bis ' + de(D.as_of) + ' nach der Monatsregel der Zeile Planmäßige Abschreibung, minus Wertberichtigungen. Beim Kunden ' + qty(FL.customer.n) + ' mit ' + eur(FL.customer.bv) + ', davon planmäßig bis zum Vertragsende noch abzuschreiben ' + eur(FL.customer.bv_rv - FL.customer.rv) + ' auf die Restwertprognose zum Leasingende ' + eur(FL.customer.rv) + '; im Lager ' + qty(FL.stock.n) + ' mit ' + eur(FL.stock.bv) + ' gegen Restwertprognose heute ' + eur(FL.stock.rv) + ', Differenz ' + eur(FL.stock.rv - FL.stock.bv_rv) + ', negativ heißt die Bücher stehen über der Prognose; Ersatzgeräte ohne Vertrag ' + qty(FL.spare.n) + ' zum Einkaufspreis (ohne Vertrag keine planmäßige Abschreibung) ' + eur(FL.spare.bv) + ' gegen Restwertprognose heute ' + eur(FL.spare.rv) + ', Differenz ' + eur(FL.spare.rv - FL.spare.bv_rv) + '.'
      + (FL.n_none > 0 ? ' ' + qty(FL.n_none) + ' Geräte ohne Prognose (Restbuchwert ' + eur(FL.bv_none) + ') stehen in keiner Differenz.' : '');
    var B = [
      'Beim Kunden ' + qty(ST.n_rented) + ' Geräte, davon ' + qty(ST.n_await) + ' mit beendetem Vertrag und ausstehender Rückgabe; im Lager nach Rückgabe ' + qty(ST.n_stock) + ' Geräte, davon ' + qty(ST.n_sellable) + ' verkaufsfähig; Ersatzgeräte ohne Vertrag ' + qty(ST.n_spare) + '; verkauft im Fenster ' + qty(ST.n_sold) + ' Geräte, verschrottet ' + qty(ST.n_scrapped) + '.',
      fleetTxt,
      'Datumskette vollständig bei ' + pct1(ST.chain) + ' der Seriennummern (jede Station von Bestellung bis Zahlungseingang aus dem Verkauf hat ein Datum), Verantwortlich ' + ST.chain_owner + '.',
      qty(ST.n_p1) + ' Entscheidungen mit Priorität 1, ' + eur(ST.stake_p1) + ' im Spiel, davon ' + qty(ST.n_p1_due) + ' überfällig; insgesamt warten ' + qty(ST.n_q) + ' Entscheidungen mit ' + eur(ST.stake) + ' im Spiel, ' + qty(ST.n_due) + ' davon überfällig.',
      'Prognosegüte der letzten ' + qty(ST.q_months) + ' Monate (' + mj(ST.q_first) + ' bis ' + mj(ST.q_last) + '), je Geräteart Prognosefehler (MAPE) und Verzerrung (Bias), mit den Verkäufen je Monat gewichtet (Verkäufe ohne Ist-Zustand), abgeleitet: ' + qparts.join('; ') + '.',
      eur(ST.lev) + ' je Jahr liegen an den ' + qty(ST.n_add) + ' addierbaren Stellschrauben ' + ST.lev_add.join(' und ') + '; die ' + qty(ST.n_non) + ' anderen stehen je Stellschraube auf dem Reiter Stellschrauben und dürfen nicht addiert werden.'
    ];
    var blocks = [
      { key: 'r-status', title: 'Stand der Dinge zum ' + de(D.as_of), items: B.map(item), ordered: false },
      { key: 'r-status-defs', title: 'Begriffe zum Stand der Dinge', ordered: false, items: [
        def('Restbuchwert der Flotte', 'Wert, mit dem die offenen Geräte zum Stichtag in den Büchern stehen; die Differenz zur Restwertprognose ist bei Geräten beim Kunden die noch geplante Abschreibung, bei Geräten im Lager und Ersatzgeräten das Wertberichtigungsrisiko.'),
        def('MAPE', 'mittlerer Fehler der Restwertprognose in Prozent des erzielten Restwerts, ohne Vorzeichen.'),
        def('Bias', 'mittlere Abweichung mit Vorzeichen: positiv heißt Prognose zu hoch, negativ zu niedrig (restwert/forecast/backtest.py); gerechnet gegen die Restwertprognose für den Marktplatz, ein Verkauf an Mitarbeiter oder Großhändler unter Marktplatzpreis zählt hier als Prognose zu hoch (Geschäftssicht, restwert/forecast/error_series.py; die Modellsicht mit Kanalfaktor steht auf dem Reiter Prognosegüte).'),
        def('Entscheidungen', 'Zeilen der Entscheidungswarteschlange: Priorität 1 handeln, 2 prüfen, 3 Hinweis; im Spiel ist die Summe der Beträge, die die Regeln je Entscheidung nennen.'),
        def('Ersatzgeräte', 'gekaufte Geräte ohne Mietvertrag, im Lager für Austausch.')
      ] }
    ];

    return {
      kicker: kicker,
      subject: subject,
      intro: lead + ' ' + banner,
      kpis: kpis,
      kpiDefs: kpiDefs,
      tables: [tPnl, tKpi, tRentalQ, tRentalTop, tSupplier, tImpact],
      blocks: blocks
    };
  };
  w.RE.report.version = 3;
})(window);
