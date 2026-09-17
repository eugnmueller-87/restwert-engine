/* Restwert Engine v3, Motor Kennzahlen (kpis), Bereich Analytics. Neu am 17.09.2026, umgebaut am selben Tag zum
   schlanken Tracker (Eugens Ansage: kein Erklärtext, alles gerade runter, wo stehen wir, wer hat was beigetragen).
   Reine Funktion: window.RE.kpis(D, opts, P) baut das Ansichtsmodell aus data/kpis.json (erzeugt von make_kpis_data.py
   aus dem KPI-Rahmen kpi_rahmen.json, den gold-KPIs, der v0.1-Registry, kpi_targets.yaml, performance_cycle.yaml,
   owners.yaml und den Monatsreihen aus data/restwert.duckdb).
   Je Kennzahl eine Zeile: Nr, Kennzahl, Ziel, Ist, Fortschritt (Balken plus Prozent, darunter Soll heute), Bis,
   Beiträge je Rolle, Status. Keine Formel, keine Zählregel, kein Erklärabsatz im Reiter (Eugen, 17.09.2026): das steht
   im Rahmen, den jede Zeile über den Link Definition mit Anker erreicht. Eine Zeile klappt auf Klick ihre Beitragszeilen auf und trägt den Link Definition auf den
   Rahmen. Der Zyklus (Quartal, Halbjahr, Jahr) kommt über opts.period, der Wechsel über opts.setPeriod, die offene
   Zeile über opts.open und opts.toggle.
   Kein DOM, kein Zustand, keine getippte Zahl: jede Zahl kommt aus D und geht durch E.fmt. Der Status je Kennzahl steht
   fertig in D (deterministisch im Generator); der Motor zeigt ihn, er urteilt nicht. */
(function (w) {
  'use strict';
  var E = w.RE;

  var STATUS = {
    erfuellt: { label: 'erfüllt', cls: 'tag-accent', neg: false },
    gelb: { label: 'im Korridor', cls: 'tag-accent-2', neg: false },
    verfehlt: { label: 'verfehlt', cls: 'tag-red', neg: true },
    nicht_messbar: { label: 'nicht messbar', cls: 'tag-neutral', neg: false },
    nicht_im_werkzeug: { label: 'nicht im Werkzeug', cls: 'tag-outline', neg: false }
  };
  var STUFE = { realisiert: 'tag-accent', verhandelt: 'tag-accent-2', identifiziert: 'tag-neutral' };
  var BAR_W = 80;   /* Breite des vollen Fortschrittsbalkens in Pixeln (E.C mit bar, gezeichnet von der Hülle) */

  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function has(x) { return x !== null && x !== undefined && x !== ''; }

  w.RE.kpis = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt;
    if (!D || !Array.isArray(D.rows) || !D.counts || !Array.isArray(D.sets) || !D.cycle) throw new Error('kpis.json unbrauchbar: rows, counts, sets oder cycle fehlen');
    var rows = D.rows, C = D.counts, cyc = D.cycle, periods = Array.isArray(cyc.periods) ? cyc.periods : [];
    var period = periods.some(function (p) { return p.key === opts.period; }) ? opts.period : cyc['default'];
    var periodLabel = (periods.filter(function (p) { return p.key === period; })[0] || {}).label || '';
    var open = has(opts.open) ? String(opts.open) : '';
    var toggle = typeof opts.toggle === 'function' ? opts.toggle : null;
    var setPeriod = typeof opts.setPeriod === 'function' ? opts.setPeriod : null;
    var band = isNum(D.gelb_band) ? f.pct(D.gelb_band) : '';

    /* ---------- Formate ---------- */
    var money = function (x) { return !isNum(x) ? '' : (Math.abs(x) < 100 ? f.eur2(x) : f.eur(x)); };
    var valShort = function (v, unit) {
      if (!isNum(v)) return 'n/a';
      if (unit === 'ratio') return f.pct1(v);
      if (unit === 'days') return f.num(v, 1) + ' Tage';
      if (unit === 'eur') return money(v);
      return f.qty(v);
    };
    var count = function (n, one, many) { return f.qty(n) + ' ' + (n === 1 ? one : many); };
    /* Ziel: eine Zahl mit Einheit; bei Zielen gegen die Baseline nur die gerechnete Zahl. Die Regel dahinter (Baseline plus,
       mal, minus) steht im Rahmen hinter dem Link Definition, nie hier (Eugens Ansage 17.09.2026: keine Formel im Reiter). */
    var zielCell = function (r) {
      var z = r.ziel_wert || {}, u = z.einheit, t = z.berechnet, text = '', sub = '';
      var word = z.richtung === 'up' ? (z.strikt ? 'über ' : 'mindestens ') : 'höchstens ';
      if (z.art === 'absolut') text = word + valShort(z.wert, u) + (u === 'count' && has(z.einheit_wort) ? ' ' + z.einheit_wort : '');
      else if (z.art === 'baseline_delta' || z.art === 'baseline_faktor') {
        if (isNum(t)) text = word + valShort(t, u); else { text = 'n/a'; sub = 'Baseline fehlt'; }
      } else if (z.art === 'referenz') text = word + (z.referenz_kurz || z.referenz || '');
      if (z.zweite && has(z.zweite.label_kurz)) sub = (sub ? sub + '; ' : '') + z.zweite.label_kurz + ' ' + (z.zweite.richtung === 'up' ? 'mindestens ' : 'höchstens ') + valShort(z.zweite.wert, z.zweite.einheit);
      return E.C(text, { minW: 120, tag: r.beispiel ? 'tag-outline' : '', tagText: r.beispiel ? 'Beispiel' : '', sub: sub });
    };
    var istCell = function (r) {
      var I = r.ist || {}, st = STATUS[r.status] || STATUS.nicht_messbar, measurable = r.status in STATUS && r.status !== 'nicht_messbar' && r.status !== 'nicht_im_werkzeug';
      var text = measurable && isNum(I.v) ? valShort(I.v, I.einheit) : 'n/a';
      var sub = measurable && r.zweite && has(r.zweite.label) ? r.zweite.label + ' ' + valShort(r.zweite.v, r.zweite.einheit) : '';
      /* kein nowrap: die Unterzeile (zweite Teilzahl) darf umbrechen, sonst zieht sie die Tabelle über die Kartenbreite */
      return E.C(text, { right: true, bold: measurable && isNum(I.v), neg: st.neg, sub: sub, minW: 90 });
    };
    var progressCell = function (r) {
      if (r.status === 'nicht_im_werkzeug') return E.C('Quelle fehlt: ' + (r.fehlt || ''), { minW: 120 });
      if (r.status === 'nicht_messbar') return E.C(r.fehlt || '', { minW: 120 });
      var p = (r.fortschritt || {}).v, soll = ((r.horizont || {}).soll || {})[period];
      if (!isNum(p)) return E.C('n/a');
      return E.C(f.pct(p), { bold: true, nowrap: true, bar: Math.round(p * BAR_W), sub: isNum(soll) ? 'Soll heute ' + f.pct(soll) : '' });
    };
    var bisCell = function (r) {
      var b = ((r.horizont || {}).bis || {})[period] || {};
      /* Quartal, Halbjahr, Jahr bleiben auf einer Zeile; ein Text wie "nächster Launch" darf umbrechen */
      return E.C(b.label || '', { nowrap: String(b.label || '').length < 10 });
    };
    var beitragCell = function (r) {
      var B = r.beitraege || {}, S = Array.isArray(B.summen) ? B.summen : [];
      if (!S.length) return E.C('keine gebucht', { color: 'var(--ink-65)', minW: 120 });
      var text = S.map(function (s) { return s.label + ': ' + count(s.n, 'Maßnahme', 'Maßnahmen') + (isNum(s.eur) ? ', ' + f.eur(s.eur) : ''); }).join('; ');
      return E.C(text, { minW: 200 });
    };
    var statusCell = function (r) {
      var st = STATUS[r.status] || STATUS.nicht_messbar;
      return E.C('', { tag: st.cls, tagText: st.label, neg: st.neg, nowrap: true });
    };
    /* die Beitragszeilen einer aufgeklappten Kennzahl: gleiche Spaltenzahl wie die Tabelle, eingerückt, mit Kopfzeile */
    var bookRows = function (r) {
      var B = r.beitraege || {}, list = Array.isArray(B.liste) ? B.liste : [], out = [];
      var blank = function () { return E.C(''); };
      out.push(E.ROW([blank(), E.C('Datum', { bold: true, nowrap: true, indent: 1 }), E.C('Rolle', { bold: true }), E.C('Maßnahme', { bold: true }), E.C('Wirkung', { bold: true, right: true }),
        E.C('Beleg-ID', { bold: true }), E.C('Stufe', { bold: true }), E.C('', { href: r.definition_url, linkText: 'Definition' })], { bg: 'var(--ink-04)' }));
      if (!list.length) out.push(E.ROW([blank(), E.C('keine Beiträge gebucht: kein Ereignis mit Akteur in einer Quelle', { indent: 1 }), blank(), blank(), blank(), blank(), blank(), blank()]));
      list.forEach(function (e) {
        out.push(E.ROW([blank(), E.C(f.de(e.datum), { nowrap: true, indent: 1 }), E.C(e.label || e.rolle, { nowrap: true }), E.C(e.massnahme), E.N(isNum(e.wirkung) ? money(e.wirkung) : '', { neg: isNum(e.wirkung) && e.wirkung < 0 }),
          E.C(e.beleg, { nowrap: true }), E.C('', { tag: STUFE[e.stufe] || 'tag-neutral', tagText: e.stufe }), blank()]));
      });
      if (B.n_mehr > 0) out.push(E.ROW([blank(), E.C('und ' + count(B.n_mehr, 'weitere Zeile', 'weitere Zeilen') + ' in der Quelle', { indent: 1 }), blank(), blank(), blank(), blank(), blank(), blank()]));
      return out;
    };

    /* ---------- Kopf ---------- */
    var kicker = 'KPI-Tracker, ' + (D.fassung || '') + ' des Rahmens, Stand ' + f.de(D.stand);
    var subject = 'Wo stehen wir gegen die Ziele, und wer hat was beigetragen?';
    var cycleFacts = [
      { k: 'Zyklus', v: periodLabel + ', Start ' + f.de(cyc.start) + (cyc.owner ? ', gesetzt von ' + f.role(cyc.owner) : '') },
      { k: 'Stichtag', v: f.de(D.as_of) + ', Daten Stand ' + f.de(D.today) }
    ];
    var periodOpts = periods.map(function (p) {
      return { label: p.label, value: p.key, checked: p.key === period, select: function () { if (setPeriod) setPeriod(p.key); } };
    });

    /* ---------- Kacheln ---------- */
    var nMeasured = f.qty(D.n_measured), nAll = f.qty(D.n_kpis || rows.length);
    var kpis = [
      { label: 'Weg zum Ziel im Mittel', value: isNum(D.fortschritt_mittel) ? f.pct(D.fortschritt_mittel) : 'n/a', neg: false, tags: [],
        lines: [isNum(D.fortschritt_mittel) ? 'über ' + nMeasured + ' messbare Kennzahlen von ' + nAll : 'keine messbare Kennzahl'] },
      { label: 'erfüllt · im Korridor · verfehlt', value: f.qty(C.erfuellt) + ' · ' + f.qty(C.gelb) + ' · ' + f.qty(C.verfehlt), neg: C.verfehlt > 0, tags: [],
        lines: [nMeasured + ' Kennzahlen mit Status; im Korridor heißt innerhalb ' + band + ' des Ziels'] },
      { label: 'nicht messbar · nicht im Werkzeug', value: f.qty(C.nicht_messbar) + ' · ' + f.qty(C.nicht_im_werkzeug), neg: false, tags: [],
        lines: [f.qty(C.nicht_messbar + C.nicht_im_werkzeug) + ' Kennzahlen ohne Ist; was fehlt, steht in der Spalte Fortschritt'] }
    ];
    /* kein calcnote: die Rechenregeln (Fortschritt, Soll heute, Herkunft der Beiträge) stehen im Rahmen hinter dem Link
       Definition jeder Zeile; ein fest gerenderter Absatz unter den Kacheln wäre genau der Erklärtext, den der Reiter nicht trägt */

    /* ---------- Tabellen: eine je Satz ---------- */
    var cols = [E.H('Nr'), E.H('Kennzahl'), E.H('Ziel'), E.H('Ist', 1), E.H('Fortschritt'), E.H('Bis'), E.H('Beiträge'), E.H('Status')];
    var defs = [
      { k: 'Nr, Kennzahl', v: 'Nummer und Name laut Rahmen; die Zeile klappt auf Klick ihre Beiträge und den Link Definition auf.' },
      { k: 'Ziel', v: 'eine Zahl mit Einheit; bei Zielen gegen die Baseline die gerechnete Zahl; Beispiel markiert Zielwerte ohne Baseline des Hauses; die Regel dahinter steht in der Definition.' },
      { k: 'Ist', v: 'der Wert der Engine im Lauf, rollierend zwölf Monate; n/a, wenn nicht messbar oder nicht im Werkzeug; darunter die zweite Teilzahl eines Doppelziels.' },
      { k: 'Fortschritt', v: 'Balken und Prozent als Anteil des Wegs von der Baseline zum Ziel, darunter Soll heute; bei nicht messbar das fehlende Feld, bei nicht im Werkzeug die fehlende Quelle.' },
      { k: 'Bis', v: 'Zyklusstart plus Horizont des Rahmens, gerundet auf das Ende des Zyklusabschnitts (' + periodLabel + '), in dem das Datum liegt.' },
      { k: 'Beiträge', v: 'je Rolle die gebuchten Maßnahmen und ihre Wirkung in Euro; leer heißt keine gebucht. Stufe realisiert bei Bestätigung oder Gutschrift, verhandelt bei unbestätigter Preisreduktion, sonst identifiziert.' },
      { k: 'Beleg-ID', v: 'die Kennung in der Quelle; bei Entscheidungen die ersten Zeichen der decision_id.' },
      { k: 'Status', v: 'erfüllt, im Korridor (innerhalb ' + band + ' des Ziels auf der schlechteren Seite), verfehlt, nicht messbar, nicht im Werkzeug; bei zwei Teilzahlen zählt die schlechtere.' }
    ];
    var tables = D.sets.map(function (s) {
      var list = rows.filter(function (r) { return r.satz === s.key; });
      var trs = [];
      list.forEach(function (r) {
        var isOpen = open === String(r.nr);
        var ro = { bold: isOpen };
        if (isOpen) ro.bg = 'var(--ink-04)';
        if (toggle) ro.onClick = function () { toggle(r.nr); };
        trs.push(E.ROW([
          E.C(r.nr, { bold: true, nowrap: true }),
          E.C(r.name, { bold: true, minW: 130 }),
          zielCell(r), istCell(r), progressCell(r), bisCell(r), beitragCell(r), statusCell(r)
        ], ro));
        if (isOpen) trs = trs.concat(bookRows(r));
      });
      var nStatus = ['erfuellt', 'gelb', 'verfehlt', 'nicht_messbar', 'nicht_im_werkzeug'].map(function (k) { var n = list.filter(function (r) { return r.status === k; }).length; return n ? f.qty(n) + ' ' + STATUS[k].label : ''; }).filter(Boolean).join(', ');
      return E.TABLE('k-' + s.key, s.label, cols, trs, {
        n: list.length, defs: defs,
        note: f.qty(list.length) + ' Kennzahlen: ' + nStatus + '. Rot, wenn verfehlt; Zeile anklicken für die Beiträge und die Definition.'
      });
    });

    return {
      kicker: kicker, subject: subject, intro: '', facts: cycleFacts,
      kpis: kpis, calcnote: '',
      tables: tables, blocks: [],
      periodOpts: periodOpts, period: period
    };
  };
  w.RE.kpis.version = 5;
})(window);
