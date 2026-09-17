/* Restwert Engine v3, Motor Kennzahlen (kpis), Bereich Analytics. Neu am 17.09.2026.
   Reine Funktion: window.RE.kpis(D, opts, P) baut das Ansichtsmodell aus data/kpis.json (erzeugt von make_kpis_data.py
   aus dem KPI-Rahmen kpi_rahmen.json, den gold-KPIs, der v0.1-Registry, kpi_targets.yaml und den Monatsreihen aus
   data/restwert.duckdb). Frage des Reiters: Erfüllen wir unsere Kennzahlen, und welche können wir überhaupt schon messen?
   Kein DOM, kein Zustand, keine getippte Zahl: jede Zahl kommt aus D und geht durch E.fmt. Der Status je Kennzahl steht
   fertig in D (deterministisch im Generator); der Motor zeigt ihn, er urteilt nicht. */
(function (w) {
  'use strict';
  var E = w.RE;

  var TAG_SIM = { cls: 'tag-neutral', text: 'simuliert' };
  var TAG_DER = { cls: 'tag-neutral', text: 'abgeleitet' };
  var STATUS = {
    erfuellt: { label: 'erfüllt', cls: 'tag-accent', neg: false },
    gelb: { label: 'im Korridor', cls: 'tag-accent-2', neg: false },
    verfehlt: { label: 'verfehlt', cls: 'tag-red', neg: true },
    nicht_messbar: { label: 'nicht messbar', cls: 'tag-neutral', neg: false },
    nicht_im_werkzeug: { label: 'nicht im Werkzeug', cls: 'tag-outline', neg: false }
  };
  var MODUS = { direkt: 'direkt', naeherung: 'Näherung', luecke: 'Lücke', keine: '' };

  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function has(x) { return x !== null && x !== undefined && x !== ''; }

  w.RE.kpis = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt;
    if (!D || !Array.isArray(D.rows) || !D.counts || !Array.isArray(D.sets)) throw new Error('kpis.json unbrauchbar: rows, counts oder sets fehlen');
    var rows = D.rows, C = D.counts, nAll = D.n_kpis || rows.length;
    var band = isNum(D.gelb_band) ? f.pct(D.gelb_band) : '';
    var baseMonths = f.qty(D.baseline_months);

    /* ---------- Formate ---------- */
    var money = function (x) { return !isNum(x) ? '' : (Math.abs(x) < 100 ? f.eur2(x) : f.eur(x)); };
    var val = function (v, unit, bezug) {
      if (!isNum(v)) return 'n/a';
      if (unit === 'ratio') return f.pct1(v) + (bezug ? ' ' + bezug : '');
      if (unit === 'days') return f.num(v, 1) + ' ' + (bezug || 'Tage');
      if (unit === 'eur') return money(v) + (bezug ? ' ' + bezug : '');
      return f.qty(v) + (bezug ? ' ' + bezug : '');
    };
    var valShort = function (v, unit) {
      if (!isNum(v)) return 'n/a';
      if (unit === 'ratio') return f.pct1(v);
      if (unit === 'days') return f.num(v, 1) + ' Tage';
      if (unit === 'eur') return money(v);
      return f.qty(v);
    };
    var signed = function (v) { return !isNum(v) ? '' : (v >= 0 ? 'plus ' : 'minus ') + f.pct1(Math.abs(v)); };
    var points = function (v) { return f.num(Math.abs(v) * 100, 0) + ' Punkte'; };
    var zielText = function (r) {
      var z = r.ziel_wert || {}, u = z.einheit, t = z.berechnet;
      if (z.art === 'absolut') {
        if (z.richtung === 'up') return (z.strikt ? 'über ' : 'mindestens ') + valShort(z.wert, u);
        return 'höchstens ' + valShort(z.wert, u);
      }
      if (z.art === 'baseline_delta') {
        var move = u === 'ratio' ? points(z.wert) : valShort(Math.abs(z.wert), u);
        return 'Baseline ' + (z.wert >= 0 ? 'plus ' : 'minus ') + move + (isNum(t) ? ', also ' + valShort(t, u) : ', Baseline fehlt');
      }
      if (z.art === 'baseline_faktor') return 'Baseline mal ' + f.num(z.wert, 1) + (isNum(t) ? ', also ' + valShort(t, u) : ', Baseline fehlt');
      if (z.art === 'referenz') return 'höchstens ' + (z.referenz || '');
      return '';
    };
    var baseText = function (r) {
      var b = r.baseline; if (!b || !isNum(b.v)) return '';
      var u = (r.ziel_wert || {}).einheit;
      return 'Baseline ' + valShort(b.v, u) + ', Mittel der Monate ' + (b.monate || []).map(f.de).join(', ') + ' (' + f.qty(b.n) + ' Beobachtungen)';
    };
    var istCell = function (r) {
      var I = r.ist || {}, st = STATUS[r.status] || STATUS.nicht_messbar;
      var text = isNum(I.v) && r.status !== 'nicht_im_werkzeug' ? val(I.v, I.einheit, r.bezug) : 'n/a';
      var sub = [];
      if (isNum(I.v) && I.text) sub.push((I.monat ? 'Monat ' + f.de(I.monat) + ', ' : '') + I.text + (isNum(I.n) ? ', ' + f.qty(I.n) + ' Beobachtungen' : ''));
      if (r.zweite && has(r.zweite.label)) sub.push(r.zweite.label + ': ' + (isNum(r.zweite.v) ? valShort(r.zweite.v, r.zweite.einheit) : 'n/a'));
      if (!isNum(I.v) && r.ersatz) sub.push('heute im Werkzeug: ' + r.ersatz);
      /* E.C mit right statt E.N: die Zahl steht rechts, die Zeile darunter darf umbrechen, sonst zieht sie die Tabelle in die Breite */
      return E.C(text, { right: true, neg: st.neg, bold: isNum(I.v), sub: sub.join('; '), minW: 200 });
    };
    var trendCell = function (r) {
      var sp = Array.isArray(r.spark) ? r.spark : [];
      if (!sp.length) return E.C('');
      var u = (r.ziel_wert || {}).einheit || (r.ist || {}).einheit;
      var vals = sp.map(function (s) { return s.v; });
      return E.C(f.qty(sp.length) + ' Monate', { right: true, nowrap: true, minW: 150, spark: vals, sub: f.de(sp[0].m) + ' ' + valShort(sp[0].v, u) + ' bis ' + f.de(sp[sp.length - 1].m) + ' ' + valShort(sp[sp.length - 1].v, u) });
    };
    var statusCell = function (r) {
      var st = STATUS[r.status] || STATUS.nicht_messbar;
      var sub = r.status === 'erfuellt' ? '' : (r.grund || '');
      if (r.status === 'gelb' && !sub) sub = 'auf der schlechteren Seite innerhalb ' + band + ' des Ziels';
      return E.C('', { tag: st.cls, tagText: st.label, neg: st.neg, sub: sub, minW: 120 });
    };
    var nrList = function (status) { return rows.filter(function (r) { return r.status === status; }).map(function (r) { return r.nr; }).join(', ') || 'keine'; };

    /* ---------- Kopf ---------- */
    var kicker = 'Kennzahlen, ' + (D.fassung || '') + ', Stand ' + f.de(D.stand) + ' (Daten Stand ' + f.de(D.today) + ', Stichtag ' + f.de(D.as_of) + ')';
    var subject = 'Erfüllen wir unsere Kennzahlen, und welche können wir überhaupt schon messen?';
    var intro = 'Der KPI-Rahmen (' + (D.fassung || '') + ', Stand ' + f.de(D.stand) + ') nennt ' + f.qty(nAll) + ' Kennzahlen in drei Sätzen: A für alle Rollen, B für Indirekt, C für Resale. '
      + 'Dieser Reiter rechnet nichts neu, er hält jede Kennzahl gegen das, was die Engine schon liefert: die gold-KPIs, die eingefrorene v0.1-Registry und die Monatsreihen aus denselben Tabellen. '
      + (D.tausch_hinweis || '') + ' Die Baseline ist das Mittel der ersten ' + baseMonths + ' Monate mit Daten. '
      + 'Ein Status wird nie geraten: erfüllt, im Korridor (auf der schlechteren Seite innerhalb ' + band + ' des Ziels), verfehlt, nicht messbar (Nenner null, Mindeststichprobe unterschritten oder Zähler im Modell nicht gebildet, mit Grund) oder nicht im Werkzeug (die Daten gibt es in der Engine nicht). '
      + 'Simulierte Flotte: die Zahlen zeigen die Mechanik, nicht das Haus. Keine Zahl je Person, nur Kategorie, Marke, Kanal und Team.';

    /* ---------- Kacheln ---------- */
    var von = ' von ' + f.qty(nAll) + ' Kennzahlen';
    var kpis = [
      { label: 'erfüllt', value: f.qty(C.erfuellt), neg: false, tags: [TAG_SIM], lines: [f.qty(C.erfuellt) + von + ': ' + nrList('erfuellt'), 'Ist erreicht das Ziel; bei Zielen mit Kennzeichen Beispiel gegen das Beispiel, bis der Tausch gegen die Baseline kommt.'] },
      { label: 'im Korridor', value: f.qty(C.gelb), neg: false, tags: [TAG_SIM], lines: [f.qty(C.gelb) + von + ': ' + nrList('gelb'), 'auf der schlechteren Seite innerhalb ' + band + ' des Ziels; bei Zielen gegen die Baseline innerhalb ' + band + ' der geforderten Bewegung.'] },
      { label: 'verfehlt', value: f.qty(C.verfehlt), neg: C.verfehlt > 0, tags: [TAG_SIM], lines: [f.qty(C.verfehlt) + von + ': ' + nrList('verfehlt'), 'darüber hinaus; bei zwei Teilzahlen zählt die schlechtere.'] },
      { label: 'nicht messbar', value: f.qty(C.nicht_messbar), neg: false, tags: [TAG_SIM], lines: [f.qty(C.nicht_messbar) + von + ': ' + nrList('nicht_messbar'), 'Nenner null, Mindeststichprobe unterschritten (Eigner ' + f.role(D.min_n_owner || '') + ') oder der Zähler ist im Modell nicht gebildet; der Grund steht in der Tabelle, daneben, was das Werkzeug heute an dessen Stelle zählt.'] },
      { label: 'nicht im Werkzeug', value: f.qty(C.nicht_im_werkzeug), neg: false, tags: [], lines: [f.qty(C.nicht_im_werkzeug) + von + ': ' + nrList('nicht_im_werkzeug'), 'die Daten existieren in der Engine nicht; was das Haus liefern muss, steht im Block unten.'] }
    ];
    var kpiDefs = [
      { k: 'Baseline', v: 'das Mittel der ersten ' + baseMonths + ' Monate mit Daten ab Mindeststichprobe; im Rahmen der Baseline-Monat des Hauses, in der Simulation die ersten Monate der Flotte. Ziele mit Bezug auf die Baseline werden daraus gerechnet.' },
      { k: 'Echt ab', v: 'ab wann die Kennzahl im Haus mit echten Daten messbar ist, laut Rahmen; bis dahin zeigt die Simulation die Mechanik.' },
      { k: 'Beispiel', v: 'ein Zielwert ohne Baseline des Hauses, als Beispiel gesetzt; ' + (D.tausch_hinweis || '') },
      { k: 'nicht messbar', v: 'Nenner null, Mindeststichprobe unterschritten oder der Zähler der Kennzahl ist im Modell nicht gebildet; der Wert ist dann n/a, nie 0.' },
      { k: 'nicht im Werkzeug', v: 'die Daten der Kennzahl existieren in der Engine nicht; der Block „Was das Haus liefern muss“ nennt die Quelle.' },
      { k: 'Näherung', v: 'die Engine misst dieselbe Größe, aber nicht exakt nach der Zählregel des Rahmens; die Abweichung steht in der Herleitung.' },
      { k: 'Fassung', v: (D.fassung || '') + ' des KPI-Rahmens, Stand ' + f.de(D.stand) + '; die Zeilen der Tabellen sind der Rahmen, die Zahlen sind der Lauf vom ' + f.de(D.as_of) + '.' }
    ];
    var calcnote = 'Status je Kennzahl: erfüllt, wenn Ist das Ziel erreicht; im Korridor, wenn Ist auf der schlechteren Seite innerhalb ' + band + ' des Ziels liegt (bei Zielen gegen die Baseline innerhalb ' + band + ' der geforderten Bewegung); sonst verfehlt. '
      + 'Ist ist der Engine-Wert des Laufs (rollierend zwölf Monate, ' + D.window + '), die Verlaufslinie die Monatsscheiben derselben Formel, zuletzt ' + f.qty(D.spark_months) + ' Monate ab Mindeststichprobe.';

    /* ---------- Tabellen: eine je Satz ---------- */
    var cols = [E.H('Nr'), E.H('Kennzahl'), E.H('Ziel'), E.H('Ist (Simulation)', 1), E.H('Status'), E.H('Trend', 1), E.H('Takt'), E.H('Echt ab'), E.H('Eigner'), E.H('Prüfer')];
    var defs = [
      { k: 'Nr, Kennzahl', v: 'Nummer und Name laut Rahmen; die Marke Näherung heißt: die Engine misst dieselbe Größe, aber nicht exakt nach der Zählregel.' },
      { k: 'Ziel', v: 'das Ziel als Zahl (bei Zielen gegen die Baseline aus der Baseline gerechnet), darunter der Wortlaut des Rahmens; Beispiel markiert Zielwerte ohne Baseline des Hauses.' },
      { k: 'Ist (Simulation)', v: 'der Wert der Engine im Lauf; n/a, wenn nicht messbar oder nicht im Werkzeug; darunter Fenster, Beobachtungen, zweite Teilzahl oder, was das Werkzeug heute an dessen Stelle zählt.' },
      { k: 'Status, Trend', v: 'der Status nach der Regel oben, mit Grund; der Trend als Verlaufslinie der Monatswerte (Anzahl Monate, erster und letzter Wert).' },
      { k: 'Takt, Echt ab', v: 'Berichtstakt und Beginn der echten Messung laut Rahmen.' },
      { k: 'Eigner, Prüfer', v: 'wer die Zahl verantwortet und wer sie prüft, als Rolle, nie als Person.' }
    ];
    var tables = D.sets.map(function (s) {
      var list = rows.filter(function (r) { return r.satz === s.key; });
      var trs = list.map(function (r) {
        var st = STATUS[r.status] || STATUS.nicht_messbar;
        var isN = r.modus === 'naeherung';
        return E.ROW([
          E.C(r.nr, { bold: true, nowrap: true }),
          E.C(r.name, { bold: true, minW: 160, tag: isN ? 'tag-neutral' : '', tagText: isN ? MODUS.naeherung : '' }),
          E.C(zielText(r), { minW: 170, tag: r.beispiel ? 'tag-outline' : '', tagText: r.beispiel ? 'Beispiel' : '', sub: r.ziel }),
          istCell(r),
          statusCell(r),
          trendCell(r),
          E.C(r.takt, { nowrap: true }), E.C(r.echt_ab, { nowrap: true }), E.C(f.role(r.eigner)), E.C(f.role(r.pruefer))
        ], { bold: false });
      });
      var nStatus = ['erfuellt', 'gelb', 'verfehlt', 'nicht_messbar', 'nicht_im_werkzeug'].map(function (k) { var n = list.filter(function (r) { return r.status === k; }).length; return n ? f.qty(n) + ' ' + STATUS[k].label : ''; }).filter(Boolean).join(', ');
      return E.TABLE('k-' + s.key, s.label, cols, trs, {
        n: trs.length, defs: defs, tags: [TAG_SIM, TAG_DER],
        note: f.qty(trs.length) + ' Kennzahlen: ' + nStatus + '. Stichtag der Daten ' + f.de(D.as_of) + '; rot, wenn verfehlt. Prozentwerte tragen ihren Bezug in der Zelle.'
      });
    });

    /* ---------- Herleitung je Kennzahl, ein Klappblock je Satz ---------- */
    var bdText = function (list, unit, nd) {
      return (list || []).filter(function (x) { return isNum(x.v); }).map(function (x) { return x.k + ' ' + (unit === 'ratio' ? f.pct1(x.v) : unit === 'eur' ? money(x.v) : unit === 'days' ? f.num(x.v, 1) + ' Tage' : f.num(x.v, nd || 1)) + ' (' + f.qty(x.n) + ')'; }).join(', ');
    };
    var derivation = function (r) {
      var B = r.benutzt || {}, I = r.ist || {}, X = r.extra || {}, u = (r.ziel_wert || {}).einheit, parts = [];
      parts.push('Formel laut Rahmen: ' + r.formel + '.');
      parts.push('Quelle laut Rahmen: ' + r.quelle + '.');
      if (r.status === 'nicht_im_werkzeug') {
        parts.push('Engine: ' + (r.engine && r.engine.note ? r.engine.note : 'keine Daten.'));
      } else {
        parts.push('Die Engine benutzt: ' + ((B.kpi_ids || []).length ? (B.kpi_ids || []).join(', ') + '; ' : '') + 'Tabellen ' + (B.tabellen || '') + '; Spalten ' + (B.spalten || '') + (B.fenster ? '; Fenster ' + B.fenster : '') + '.');
        parts.push('Zählung: ' + (B.zaehlung || '') + '.');
        if (isNum(I.v)) parts.push('Ist ' + val(I.v, I.einheit, r.bezug) + ' (' + (I.monat ? 'Monat ' + f.de(I.monat) + ', ' : '') + (I.text || '') + ')' + (isNum(I.n) ? ', ' + f.qty(I.n) + ' Beobachtungen' : '') + '.');
        if (r.zweite && isNum(r.zweite.v)) {
          var z = r.zweite, zs = z.status ? STATUS[z.status] : null;
          parts.push(z.label + ': ' + valShort(z.v, z.einheit) + (isNum(z.signed) ? ' (mit Vorzeichen ' + signed(z.signed) + ')' : '') + (isNum(z.erloes) && isNum(z.buchwert) ? ' (Erlös ' + money(z.erloes) + ' gegen Restbuchwert ' + money(z.buchwert) + ', ' + f.qty(z.n) + ' Abgänge)' : '') + (isNum(z.buchwert_lager) ? ' (' + f.qty(z.n) + ' von ' + f.qty(z.n_lager) + ' Geräten im Lager, Buchwert im Lager ' + money(z.buchwert_lager) + ')' : '') + (Array.isArray(z.monate) && z.monate.length ? ' (Monate ' + z.monate.map(f.de).join(', ') + ')' : '') + ', Ziel ' + (z.richtung === 'up' ? 'mindestens ' : 'höchstens ') + valShort(z.ziel_v, z.einheit) + (zs ? ', ' + zs.label : '') + '.');
        }
        var bt = baseText(r); if (bt) parts.push(bt + '; Ziel daraus ' + zielText(r) + '.');
        if (isNum(I.signed)) parts.push('Abweichung mit Vorzeichen ' + signed(I.signed) + ' (plus heißt: Erlös unter der Annahme).');
        if (X.marken && X.marken.length) parts.push('Je Marke: ' + bdText(X.marken, 'ratio') + '; in Klammern die Geräte.');
        if (isNum(X.rabatt)) parts.push('Rabatt gegen UVP netto (KPI_PUR_DISCOUNT_VS_RRP) ' + f.pct1(X.rabatt) + ' der UVP über ' + f.qty(X.rabatt_n) + ' Geräte.');
        if (X.familien && X.familien.length && X.familien[0].mape !== undefined) parts.push('Je Geräteart im Monat: ' + X.familien.map(function (x) { return x.k + ' Fehler ' + (isNum(x.mape) ? f.pct1(x.mape) : 'n/a') + ', Verzerrung ' + (isNum(x.bias) ? signed(x.bias) : 'n/a') + ' (' + f.qty(x.n) + ')'; }).join('; ') + '.');
        if (X.familien && X.familien.length && X.familien[0].mape === undefined) parts.push('Je Geräteart: ' + bdText(X.familien, 'eur') + '.');
        if (X.laufzeiten && X.laufzeiten.length) parts.push('Je Laufzeit in Monaten: ' + bdText(X.laufzeiten, 'eur') + '.');
        if (isNum(X.tco)) parts.push('Kosten je abgeschlossenem Gerät (KPI_TCO_PER_CLOSED_DEVICE) ' + money(X.tco) + ' über ' + f.qty(X.tco_n) + ' Geräte.');
        if (X.kanaele && X.kanaele.length) parts.push('Je Kanal: ' + bdText(X.kanaele, u === 'days' ? 'days' : 'ratio') + '.');
        if (X.kategorien && X.kategorien.length) parts.push('Je Kategorie: ' + bdText(X.kategorien, 'ratio') + '; in Klammern die Rechnungen.');
        if (X.typen && X.typen.length) parts.push('Bestätigte Beträge des Jahres je Art (in Klammern die Fälle): ' + bdText(X.typen, 'eur') + '.');
        if (isNum(X.plan_ratio)) parts.push('Die Engine-Kennzahl KPI_IND_SAVINGS_CONFIRMED misst gegen den Jahresplan ' + String(X.plan_year) + ': bestätigte Preisreduktionen ' + money(X.plan_confirmed) + ' von Plan ' + money(X.plan_eur) + ', also ' + f.pct1(X.plan_ratio) + ' des Plans (Platzhalter, Verantwortlich ' + f.role(X.plan_owner || '') + ').');
        if (isNum(X.deklariert)) parts.push('Zum Vergleich KPI_REC_GRADING_ACCURACY (deklariert gegen geprüft): ' + f.pct1(X.deklariert) + ' der Rückläufer wie deklariert (' + f.qty(X.deklariert_num) + ' von ' + f.qty(X.deklariert_den) + ').');
        if (X.paare && X.paare.length) parts.push('Paare Grade geprüft gegen Abgang: ' + X.paare.map(function (p) { return p.ein + ' zu ' + p.aus + ' ' + f.qty(p.n); }).join(', ') + '.');
        if (isNum(X.aging_share)) parts.push('Stückzahl-Anteil des gealterten Bestands (KPI_INV_AGING_180): ' + f.pct1(X.aging_share) + ' der ' + f.qty(X.aging_n) + ' Geräte im Lager.');
        if (isNum(X.w_actual)) parts.push('Zum Stichtag gewichtet mit dem tatsächlichen Spend der letzten zwölf Monate (' + money(X.w_sum) + '): ' + f.num(X.w_actual, 1) + ' Tage über ' + f.qty(X.n_pay) + ' aktive Verträge.');
        if (isNum(X.ziel_engine)) parts.push('Ziel der Engine in config/kpi_targets.yaml: ' + f.pct(X.ziel_engine) + ', Platzhalter, Verantwortlich ' + f.role(X.ziel_engine_owner || '') + '.');
        if (r.ersatz) parts.push('Heute im Werkzeug: ' + r.ersatz + (/[.!?]$/.test(r.ersatz) ? '' : '.'));
        if (r.naeherung) parts.push('Näherung: ' + r.naeherung + (/[.!?]$/.test(r.naeherung) ? '' : '.'));
        if (r.grund && r.status !== 'erfuellt') parts.push('Status ' + (STATUS[r.status] || {}).label + ': ' + r.grund + (/[.!?]$/.test(r.grund) ? '' : '.'));
      }
      parts.push('Zählregel laut Rahmen: ' + r.zaehlregel);
      if (r.engine && r.engine.source) parts.push('Fundstelle im Repo: ' + r.engine.source + '.');
      if (r.engine && r.engine.note && r.status !== 'nicht_im_werkzeug') parts.push('Zuordnung: ' + r.engine.note);
      if (r.beispiel) parts.push('Ziel ist Beispiel: ' + (D.tausch_hinweis || ''));
      return parts.join(' ');
    };
    var blocks = D.sets.map(function (s) {
      var list = rows.filter(function (r) { return r.satz === s.key; });
      return { key: 'k-her-' + s.key, title: 'Herleitung ' + s.label, ordered: false, intro: 'Je Kennzahl: Formel und Quelle laut Rahmen, was die Engine tatsächlich benutzt hat, die Zählung, der Status mit Grund, die Zählregel.',
        items: list.map(function (r) { return { lead: r.nr + ' ' + r.name + '.', text: derivation(r) }; }) };
    });

    /* ---------- Was das Haus liefern muss ---------- */
    var supply = rows.filter(function (r) { return has(r.liefern); });
    blocks.push({ key: 'k-liefern', title: 'Was das Haus liefern muss', ordered: false,
      intro: f.qty(supply.length) + ' Kennzahlen brauchen eine Quelle, die die Engine nicht hat; je Kennzahl die fehlende Quelle.',
      items: supply.map(function (r) { return { lead: r.nr + ' ' + r.name + ' (' + (STATUS[r.status] || {}).label + ').', text: 'Fehlt: ' + r.liefern + '.' + (r.ersatz ? ' Heute im Werkzeug: ' + r.ersatz + (/[.!?]$/.test(r.ersatz) ? '' : '.') : '') }; }) });

    /* ---------- Methode und Grenzen ---------- */
    blocks.push({ key: 'k-method', title: 'Methode, ausgeschrieben', ordered: true, intro: 'So entsteht jede Zeile.', items: [
      { lead: 'Der Rahmen ist die Zeile.', text: 'Nummer, Name, Ziel, Takt, Echt ab, Eigner, Prüfer, Formel, Quelle und Zählregel stammen aus dem KPI-Rahmen (' + (D.fassung || '') + ', Stand ' + f.de(D.stand) + '); nichts davon wird hier umformuliert.' },
      { lead: 'Die Engine ist die Zahl.', text: 'Ist kommt aus den gold-KPIs (' + f.qty((D.engine_layers || {}).gold ? D.engine_layers.gold.n : 0) + ', Seiten ' + (((D.engine_layers || {}).gold || {}).pages || []).join(', ') + ') oder der v0.1-Registry (' + f.qty((D.engine_layers || {}).v01 ? D.engine_layers.v01.n : 0) + ', Bereiche ' + (((D.engine_layers || {}).v01 || {}).areas || []).join(', ') + '), je mit Mindeststichprobe aus config/kpi_targets.yaml; wo die Engine keine fertige Kennzahl hat, zählt der Generator aus derselben Tabelle und markiert das als abgeleitet.' },
      { lead: 'Zuordnung vor dem Status.', text: 'Jede Kennzahl trägt eine Zuordnung zur Engine (direkt, Näherung, Lücke, keine); nur direkt und Näherung bekommen einen Status gegen das Ziel, Lücke heißt nicht messbar mit Grund, keine heißt nicht im Werkzeug.' },
      { lead: 'Ziel und Korridor.', text: 'Absolute Ziele stehen im Rahmen; Ziele gegen die Baseline werden aus dem Mittel der ersten ' + baseMonths + ' Monate mit Daten gerechnet. Im Korridor heißt: auf der schlechteren Seite innerhalb ' + band + ' des Ziels, bei Baseline-Zielen innerhalb ' + band + ' der geforderten Bewegung. Bei zwei Teilzahlen zählt die schlechtere.' },
      { lead: 'Trend.', text: 'Die Verlaufslinie zeigt die Monatsscheiben derselben Formel (zuletzt ' + f.qty(D.spark_months) + ' Monate ab Mindeststichprobe); der Ist-Wert ist rollierend über zwölf Monate, deshalb weicht der letzte Punkt der Linie vom Ist ab.' }
    ] });
    blocks.push({ key: 'k-limits', title: 'Grenzen, ausgesprochen', ordered: false, intro: '', items: [
      { lead: '', text: 'Simulierte Flotte: die Baseline liegt in den ersten Monaten der synthetischen Historie, nicht in dem Monat, den der Rahmen für das Haus nennt; Ziele gegen die Baseline sind hier Mechanik, keine Aussage über das Haus.' },
      { lead: '', text: 'Näherungen messen dieselbe Größe, aber nicht nach der Zählregel des Rahmens (Einkaufsbeteiligung, Wertschwelle, Rahmenverträge, Reparatur nach Regel); die Abweichung steht in jeder Herleitung.' },
      { lead: '', text: 'Ziele mit Kennzeichen Beispiel sind Platzhalter des Rahmens; ' + (D.tausch_hinweis || '') },
      { lead: '', text: 'Keine Zahl je Person: Eigner und Prüfer sind Rollen, Aufteilungen gibt es nur je Kategorie, Marke, Kanal und Team, nach der Regel des Rahmens zur Mitbestimmung.' },
      { lead: '', text: 'Ein Status aus einer Näherung ist ein Status der Näherung; ob das Haus die Kennzahl erfüllt, sagt erst die echte Messung ab dem Datum in Echt ab.' }
    ] });

    return {
      kicker: kicker, subject: subject, intro: intro,
      kpis: kpis, kpiDefs: kpiDefs, calcnote: calcnote,
      tables: tables, blocks: blocks
    };
  };
  w.RE.kpis.version = 3;
})(window);
