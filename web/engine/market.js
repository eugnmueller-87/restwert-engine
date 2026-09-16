/* Restwert Engine v3, Motor Realisierung (Reiter "market"). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6 und 7.3.
   Reine Funktion window.RE.market(D, opts, P): baut aus v3/data/market.json das Ansichtsmodell V.
   Texte, Zahlen, Rechenwege, Tabellen und Legenden eins zu eins aus dem heutigen Reiter (v3/src/market.js,
   v3/src/market.body.html); jede Zahl kommt aus D oder opts und geht durch E.fmt. Kein DOM, kein Zustand ausser
   einem Index je Datenobjekt (WeakMap), kein Nachladen.
   Der Einkaufsabschlag kommt aus opts.disc (Konfiguration, im Dialog aenderbar), nie als Zahl aus diesem Skript.
   Manuell erfasste Preisbelege (opts.extra) zaehlen in der Belegtabelle und im Detail-Diagramm mit, nie in den Kurven.
   Seit 16.09.2026 (Bereich Market Intelligence): die Tabellen Serie gegen Serie, Gegenprobe und Studien stehen in den Motoren
   series.js und studies.js; hier bleiben Kacheln, Diagramm, Kurve je Hersteller und alle Preisbelege. */
(function (w) {
  'use strict';
  var E = w.RE, f = E.fmt, CACHE = new WeakMap();

  /* Ersatzpalette (helle Tokenfarben aus app/styles.css), solange die Huelle die CSS-Tokens noch nicht gelesen hat */
  var P0 = {
    font: '"Source Serif 4", serif', ink: '#201e1d', muted: '#7d7979', grid: '#d7d3d3', line: '#bab6b6',
    accent: '#0088b0', accent700: '#006786', accent2: '#d6006c',
    oem: { Apple: '#006786', Samsung: '#d82071', Google: '#444141', Motorola: '#62c5ee', Fairphone: '#ff90b1', 'HMD Global (Nokia)': '#9b9797', Nokia: '#9b9797', Lenovo: '#790e3d', Dell: '#7d7979', HP: '#0a303e', Microsoft: '#4b1528' }
  };

  /* Wortlisten wie heute */
  var GR = { A: 'A wie neu', B: 'B sehr gut', C: 'C gut', D: 'D akzeptabel', TRADEIN: 'keine Stufe', UNKNOWN: 'unbekannt' };
  var SYM = { A: 'circle', B: 'diamond', C: 'square', D: 'x', TRADEIN: 'triangle-down', UNKNOWN: 'circle-open' };
  var FIT = { ok: 'belastbar', thin: 'unsicher' };                 /* alles andere: keine Kurve */
  var FIT_TAG = { ok: 'tag-accent', thin: 'tag-accent-2' };        /* alles andere: tag-neutral */
  var KIND = { 'ankauf-trade-in': 'Ankauf-Gebot' };                 /* alles andere: Marktplatz-Angebot */
  var POP = [['marketplace', 'solid', 'Marktplatz-Angebote, abgelesen für Stufe B', ' (Obergrenze)'], ['tradein', 'dot', 'Ankauf-Gebote', ' (Höchstwerte bis zu)']];
  var TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' };
  var TAG_MAN = 'tag-accent-2', TAG_MAN_TEXT = 'manuell';
  var ZAHLWORT = ['null', 'ein', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwölf'];
  var DEFAULT_FAMILY = 'Smartphone';

  /* Feste Groessen der Ansicht, wie heute; keine Behauptungen ueber die Daten */
  var H24 = 24, H36 = 36;            /* Horizonte der Kacheln und Spalten; Schluessel q_24 und q_36 in D.curves */
  var GRADE_READ = 'B';              /* Stufe, fuer die die Kurven abgelesen werden und aus der die Herstellerlinien entstehen */
  var BUCKET = 6;                    /* Halbjahr: Breite der Altersklassen der Herstellerlinien, in Monaten */
  var MIN_OEM = 4;                   /* Mindestzahl Stufe-B-Belege je Hersteller fuer eine Linie */
  var MIN_BUCKET = 2;                /* Mindestzahl Belege je Halbjahr fuer einen Punkt der Linie */
  var XMAX = 62;                     /* rechter Rand der Monatsachse */
  var CURVE_PAD = 3;                 /* Monate, um die eine Kurve ueber ihre Belegspanne hinaus gezeichnet wird */
  var CURVE_CAP = 1.02;              /* Kurvenwerte darueber fallen weg; gezeichnet wird hoechstens 1 */
  var MARK_MIN = 6, MARK_STEP = 2, MARK_MAX = 16;   /* Punktgroesse je Belegzahl im Halbjahr */
  var DETAIL_SIZE = 8, DETAIL_OPACITY = 0.75;       /* Punkte im Detailmodus */
  var YTICKS = [0, 0.2, 0.4, 0.6, 0.8, 1];
  /* Schwellen der Verlaesslichkeit, wie der Generator der Kurven sie setzt (fit_quality in D.curves) */
  var FIT_OK_N = 12, FIT_THIN_N = 6, FIT_MIN_SPAN = 6;
  var EX_OFFSET = 6;                 /* Beispiel in den Grenzen: so viele Monate nach Verkaufsstart gekauft */

  /* ---------- kleine Helfer ---------- */
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function loose(x) { /* Zahl so kurz wie noetig: 0.88 -> '0,88', 0.875 -> '0,875' */
    if (!isNum(x)) return '';
    var r = Math.round(x * 10000) / 10000, dec = (String(r).split('.')[1] || '').length;
    return f.num(r, dec);
  }
  function median(list) {
    var v = list.slice().sort(function (a, b) { return a - b; }), n = v.length;
    return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
  }
  function pctBr(v, m, c) { /* in Klammern, wenn das Alter ausserhalb der Belegspanne liegt */
    var t = f.pct(v); if (!t) return '';
    return (m < c.age_min || m > c.age_max) ? '(' + t + ')' : t;
  }
  function normExtra(a) { /* manuell erfasster Preisbeleg, Felder wie D.anchors[i]; Ersatznamen werden toleriert */
    return {
      slug: a.slug, model_name: a.model_name || a.model || '', oem: a.oem || '', family: a.family || '',
      spec_used: a.spec_used || a.spec || '', condition: a.condition || '', grade: a.grade || 'UNKNOWN',
      age_months: isNum(a.age_months) ? a.age_months : a.age, rrp_eur_launch_de: isNum(a.rrp_eur_launch_de) ? a.rrp_eur_launch_de : a.rrp,
      price_eur: isNum(a.price_eur) ? a.price_eur : a.price, realisation: isNum(a.realisation) ? a.realisation : a.q,
      source_kind: a.source_kind || a.kind || 'refurbished-marktplatz', source_url: a.source_url || a.url || '', date_seen: a.date_seen || a.date || '', manual: true
    };
  }
  function sortKey(r) { return r.oem + r.model_name + r.age_months; }

  /* ---------- Index je Datenobjekt ---------- */
  function index(D) {
    var x = CACHE.get(D); if (x) return x;
    var fams = []; D.anchors.forEach(function (r) { if (fams.indexOf(r.family) < 0) fams.push(r.family); }); fams.sort();
    var famCurve = {}, oemCurves = {}, byFam = {};
    D.curves.forEach(function (c) {
      if (c.group_kind === 'family') famCurve[c.group + '|' + c.population] = c;
      if (c.group_kind === 'family_oem' && c.population === 'marketplace') {
        var fam = c.group.split(' / ')[0];
        (oemCurves[fam] = oemCurves[fam] || []).push({ c: c, oem: c.group.split(' / ')[1] });
      }
    });
    fams.forEach(function (fam) { byFam[fam] = D.anchors.filter(function (r) { return r.family === fam; }); });
    x = { fams: fams, famCurve: famCurve, oemCurves: oemCurves, byFam: byFam };
    CACHE.set(D, x); return x;
  }

  /* ---------- der Motor ---------- */
  w.RE.market = function (D, opts, P) {
    opts = opts || {};
    if (!D || !Array.isArray(D.anchors) || !D.anchors.length || !Array.isArray(D.curves)) throw new Error('market: D.anchors oder D.curves fehlen');
    var X = index(D), pal = P || P0, fams = X.fams;
    var current = fams.indexOf(opts.family) >= 0 ? opts.family : (fams.indexOf(DEFAULT_FAMILY) >= 0 ? DEFAULT_FAMILY : fams[0]);
    var detail = !!opts.detail;
    var disc = isNum(opts.disc) && opts.disc >= 0 && opts.disc < 1 ? opts.disc : null;
    var famCurve = function (fam, pop) { return X.famCurve[fam + '|' + pop] || null; };
    var q24 = 'q_' + H24, q36 = 'q_' + H36;
    var oemColor = function (o) { return (pal.oem && pal.oem[o]) || pal.muted; };

    /* Preisbelege der Familie: aus den Daten, dazu die manuell erfassten */
    var rowsFam = X.byFam[current] || [];
    var extra = (Array.isArray(opts.extra) ? opts.extra : []).filter(function (a) { return a && a.family === current; }).map(normExtra);
    var allRows = rowsFam.concat(extra);

    /* ---------- Kopf ---------- */
    var ex = D.example || {};
    var kicker = 'Öffentliche Preisbelege, Stand ' + f.de(D.today);
    var subject = 'Welches Gerät hält welchen Anteil seines Preises';
    var intro = 'Realisierung = Gebrauchtpreis geteilt durch die unverbindliche Preisempfehlung (UVP) des Herstellers beim deutschen Verkaufsstart, beide einschließlich Mehrwertsteuer. '
      + 'Beispiel: ' + ex.model + ', UVP ' + f.eur(ex.rrp) + ', Marktplatz-Angebot ' + f.eur(ex.price) + ' in Zustandsstufe B (sehr gut) am ' + f.de(ex.date) + ' = ' + f.num(ex.pct, 0) + ' %. '
      + 'Jeder Punkt ist ein öffentlicher Preisbeleg mit Adresse und Datum; keine Zahl stammt aus den Büchern eines Unternehmens. '
      + 'Woher die Belege kommen, was fehlt und warum, steht im Reiter FAQ; der Vergleich der Modellreihen im Reiter Serie gegen Serie, die veröffentlichten Studien im Reiter Studien.';

    /* ---------- Kennzahlen: die drei Kacheln, alle Familien ---------- */
    var kpis = fams.map(function (fam) {
      var c = famCurve(fam, 'marketplace'), t = famCurve(fam, 'tradein'), lines = [];
      if (c && isNum(c[q24])) {
        lines.push('nach ' + f.qty(H24) + ' Monaten ' + f.pct(c[q24]) + (isNum(c.age_min) && c.age_min > H24 ? ' (Kurve verlängert, jüngster Beleg ' + f.num(c.age_min) + ' Monate)' : ''));
        lines.push('verliert je Monat etwa ' + f.pct1(c.monthly_depreciation_pct) + ' des aktuellen Werts');
        lines.push('Kurve durch alle ' + f.qty(c.n) + ' Marktplatz-Angebote der Familie, alle Hersteller, jeder Beleg zählt gleich' + (c.fit_quality !== 'ok' ? '; Kurve unsicher' : ''));
        if (isNum(c.age_max) && c.age_max < H36) lines.push('nach ' + f.qty(H36) + ' Monaten: Kurve verlängert, ältester Beleg ' + f.num(c.age_max) + ' Monate');
      } else {
        lines.push(c ? f.qty(c.n) + ' Marktplatz-Angebote, keine Kurve' : 'keine Preisbelege');
      }
      if (t && isNum(t[q36])) lines.push('Ankauf-Gebote „bis zu“ nach ' + f.qty(H36) + ' Monaten: ' + f.pct(t[q36]) + ' (' + f.qty(t.n) + ' Ankauf-Gebote)');
      return { label: fam + ': Realisierung nach ' + f.qty(H36) + ' Monaten', value: c && isNum(c[q36]) ? f.pct(c[q36]) : 'keine Kurve', lines: lines, tags: [TAG_PUB] };
    });
    var kpiDefs = [
      { k: 'Kacheln', v: 'Realisierung in Prozent der UVP, an der Kurve der Familie abgelesen. Die Kurve ist kein Mittelwert der Geräte und kein Mittel der Hersteller: sie ist die Linie durch alle Preisbelege der Familie (alle Hersteller, alle Stufen A bis D, jeder Beleg zählt gleich, deshalb wiegt ein Hersteller mit vielen Belegen mehr), abgelesen für Zustandsstufe B (sehr gut); das ist die Obergrenze, weil die Marge des Aufbereiters darin steckt. Je Hersteller steht die eigene Kurve in der Tabelle unten. Ankauf-Gebote sind Höchstwerte „bis zu“ eines Ankäufers vor der Zustandsprüfung, kein Boden; der tatsächliche Ankaufpreis liegt darunter.' }
    ];

    /* ---------- Diagramm: Kurven je Hersteller oder einzelne Preisbelege, dazu die Kurven der Familie ---------- */
    var traces = [];
    var byOem = {}; (detail ? allRows : rowsFam).forEach(function (r) { (byOem[r.oem] = byOem[r.oem] || []).push(r); });
    Object.keys(byOem).sort().forEach(function (o) {
      if (detail) {
        var g = byOem[o];
        traces.push({
          type: 'scatter', mode: 'markers', name: o,
          x: g.map(function (r) { return r.age_months; }), y: g.map(function (r) { return r.realisation; }),
          marker: { color: oemColor(o), size: DETAIL_SIZE, opacity: DETAIL_OPACITY, symbol: g.map(function (r) { return SYM[r.grade] || 'circle'; }),
            line: { color: pal.ink, width: g.map(function (r) { return r.manual ? 2 : 0; }) } },
          text: g.map(function (r) {
            return r.model_name + ' ' + (r.spec_used || '') + '<br>' + r.condition + ', ' + f.eur(r.price_eur) + ' gegen UVP ' + f.eur(r.rrp_eur_launch_de)
              + '<br>Monate ' + f.num(r.age_months) + ', Realisierung ' + f.pct(r.realisation) + (r.manual ? '<br>manuell erfasst' : '');
          }),
          hovertemplate: '%{text}<extra></extra>'
        });
        return;
      }
      /* eine Linie je Hersteller: mittleres Marktplatz-Angebot der Stufe B je Halbjahr Modellalter, nur Belege der Daten */
      var b = byOem[o].filter(function (r) { return r.grade === GRADE_READ && !r.manual; });
      if (b.length < MIN_OEM) return;
      var buckets = {};
      b.forEach(function (r) { var k = Math.floor(r.age_months / BUCKET) * BUCKET + BUCKET / 2; (buckets[k] = buckets[k] || []).push(r.realisation); });
      var xs = [], ys = [], ns = [];
      Object.keys(buckets).map(Number).sort(function (a, c) { return a - c; }).forEach(function (k) {
        if (buckets[k].length < MIN_BUCKET) return;
        xs.push(k); ys.push(median(buckets[k])); ns.push(buckets[k].length);
      });
      if (!xs.length) return;
      traces.push({
        type: 'scatter', mode: 'lines+markers', name: o + ', Zustandsstufe ' + GRADE_READ + ', ' + f.qty(b.length) + ' Preisbelege', x: xs, y: ys,
        marker: { color: oemColor(o), size: ns.map(function (n) { return Math.min(MARK_MAX, MARK_MIN + MARK_STEP * n); }) }, line: { color: oemColor(o), width: 2 },
        text: xs.map(function (x, i) { return o + '<br>Monate etwa ' + f.qty(x) + '<br>mittleres Marktplatz-Angebot ' + f.pct(ys[i]) + '<br>' + f.qty(ns[i]) + ' Preisbelege im Halbjahr'; }),
        hovertemplate: '%{text}<extra></extra>'
      });
    });
    POP.forEach(function (p) {
      var c = famCurve(current, p[0]);
      if (!c || !isNum(c.slope_per_month) || !isNum(c.intercept)) return;
      var lo = Math.max(0, Math.floor(c.age_min) - CURVE_PAD), hi = Math.min(XMAX, Math.ceil(c.age_max) + CURVE_PAD), xs = [], ys = [];
      for (var m = lo; m <= hi; m++) { var v = Math.exp(c.intercept + c.slope_per_month * m); if (v <= CURVE_CAP) { xs.push(m); ys.push(Math.min(v, 1)); } }
      if (!xs.length) return;
      traces.push({
        type: 'scatter', mode: 'lines', name: 'Kurve ' + current + ', ' + p[2] + ', ' + f.qty(c.n) + ' Preisbelege' + p[3] + (c.fit_quality !== 'ok' ? ', unsicher' : ''),
        x: xs, y: ys, line: { color: pal.ink, dash: p[1], width: 2 }, hoverinfo: 'skip'
      });
    });
    var chart = {
      traces: traces,
      layout: {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { family: pal.font, color: pal.ink, size: 13 },
        xaxis: { title: { text: 'Monate seit deutschem Verkaufsstart des Modells' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: false, range: [0, XMAX] },
        yaxis: { title: { text: 'Realisierung in % der UVP' }, tickvals: YTICKS, ticktext: YTICKS.map(function (t) { return f.pct(t); }), range: [0, 1.05], gridcolor: pal.grid, linecolor: pal.line, zeroline: false },
        legend: { orientation: 'h', y: 1.02, yanchor: 'bottom', x: 0, xanchor: 'left', font: { color: pal.ink } },
        hoverlabel: { font: { family: pal.font } },
        shapes: [H24, H36].map(function (h) { return { type: 'line', x0: h, x1: h, y0: 0, y1: 1, yref: 'paper', line: { color: pal.line, dash: 'dash', width: 1 } }; }),
        annotations: [H24, H36].map(function (h) { return { x: h, y: 0, yref: 'paper', text: f.qty(h) + ' Monate', showarrow: false, yanchor: 'bottom', xanchor: 'left', xshift: 4, font: { size: 11, color: pal.muted } }; })
      }
    };
    var chartNote = 'Realisierung nach Modellalter, ' + current + '. Farbige Linien: je Hersteller das mittlere Marktplatz-Angebot in Zustandsstufe B je Halbjahr Modellalter (die Hälfte der Belege liegt darüber, die Hälfte darunter; je größer der Punkt, desto mehr Belege). '
      + 'Schwarz durchgezogen: die Kurve der Familie durch alle Marktplatz-Angebote der Stufen A bis D, abgelesen für Stufe B; die Obergrenze für den Erlös, weil die Marge des Aufbereiters darin steckt; sie endet, wo die Belege enden. '
      + 'Schwarz gepunktet: die Kurve der Ankauf-Gebote „bis zu“, also Höchstwerte vor der Zustandsprüfung, kein Boden. '
      + '„Einzelne Preisbelege“ blendet jeden Preisbeleg ein: Kreis A wie neu, Raute B sehr gut, Quadrat C gut, Kreuz D akzeptabel, offener Kreis Zustand vom Verkäufer nicht eindeutig, Dreieck Ankauf-Gebot'
      + (extra.length ? '; ein manuell erfasster Beleg trägt einen dunklen Rand' : '') + '.';

    /* ---------- Tabelle 1: Kurve je Hersteller ---------- */
    var discDef = disc === null
      ? 'Realisierung nach ' + f.qty(H36) + ' Monaten, bezogen auf einen angenommenen Einkaufspreis statt auf die UVP; die Hülle hat keinen Einkaufsabschlag übergeben, deshalb bleibt die Spalte leer'
      : 'Realisierung nach ' + f.qty(H36) + ' Monaten, bezogen auf einen angenommenen Einkaufspreis von UVP minus ' + f.pct1(disc) + ' statt auf die UVP, also geteilt durch ' + loose(1 - disc) + '; der Abschlag ist ein Platzhalter mit verantwortlicher Rolle (Datei config/assumptions.yaml), keine Tatsache über ein reales Unternehmen';
    var oemRows = (X.oemCurves[current] || []).map(function (e) {
      var c = e.c, vs = (disc !== null && isNum(c[q36])) ? c[q36] / (1 - disc) : null;
      return E.ROW([
        E.C(e.oem),
        E.N(f.qty(c.n)),
        E.C(f.num(c.age_min) + ' bis ' + f.num(c.age_max), { nowrap: true }),
        E.N(f.pct1(c.monthly_depreciation_pct)),
        E.N(pctBr(c[q24], H24, c)),
        E.N(pctBr(c[q36], H36, c)),
        E.N(pctBr(vs, H36, c)),
        E.C('', { tag: FIT_TAG[c.fit_quality] || 'tag-neutral', tagText: FIT[c.fit_quality] || 'keine Kurve' })
      ]);
    });
    var tOem = E.TABLE('m-oem', 'Kurve je Hersteller', [
      E.H('Hersteller'), E.H('Belege', 1), E.H('Monate'), E.H('Verlust je Monat', 1), E.H('nach ' + f.qty(H24) + ' Monaten', 1), E.H('nach ' + f.qty(H36) + ' Monaten', 1),
      E.H('gegen Einkaufspreis', 1), E.H('Verlässlichkeit')
    ], oemRows, {
      tags: [TAG_PUB],
      note: 'Eine Zeile je Hersteller der gewählten Familie. Grundlage sind alle Marktplatz-Angebote der Stufen A bis D, abgelesen für Zustandsstufe B (sehr gut), also die Obergrenze; Ankauf-Gebote stehen hier nicht. Ein Hersteller mischt hier Einsteiger, Flaggschiffe und faltbare Geräte; der Vergleich Serie gegen Serie steht im Reiter gleichen Namens.',
      empty: 'Für diese Familie liegt keine Kurve je Hersteller vor.',
      defs: [
        { k: 'Hersteller', v: 'Hersteller des Modells; eine Zeile je Hersteller der gewählten Familie.' },
        { k: 'Belege', v: 'Marktplatz-Angebote aller Stufen A bis D, die in die Kurve eingehen; die Stufen A, C und D gehen mit einem festen Abstand zur Stufe B ein, der zusammen mit der Linie geschätzt wird, abgelesen wird die Linie für Stufe B' },
        { k: 'Monate', v: 'Spanne des Modellalters, das diese Belege abdecken, in Monaten seit deutschem Verkaufsstart' },
        { k: 'Verlust je Monat', v: 'Prozent seines jeweils aktuellen Werts, die ein Gerät laut Kurve jeden Monat verliert; keine Prozentpunkte der UVP' },
        { k: 'nach ' + f.qty(H24) + ', nach ' + f.qty(H36) + ' Monaten', v: 'Realisierung in Prozent der UVP, an der Kurve abgelesen; steht der Wert in Klammern, liegt das Alter außerhalb der Spanne in Monate, die Linie ist dann über die Belege hinaus verlängert' },
        { k: 'gegen Einkaufspreis', v: discDef },
        { k: 'Verlässlichkeit', v: 'belastbar = mindestens ' + f.qty(FIT_OK_N) + ' Belege über mindestens ' + f.qty(FIT_MIN_SPAN) + ' Monate Altersspanne und der Wert fällt; unsicher = ' + f.qty(FIT_THIN_N) + ' bis ' + f.qty(FIT_OK_N - 1) + ' Belege, oder der Wert steigt mit dem Alter, was unplausibel ist; keine Kurve = unter ' + f.qty(FIT_THIN_N) + ' Belege oder unter ' + f.qty(FIT_MIN_SPAN) + ' Monate Spanne, dann bleiben die Spalten leer' }
      ]
    });

    /* ---------- Tabelle 2: alle Preisbelege der Familie, manuell erfasste zaehlen mit ---------- */
    var sorted = allRows.slice().sort(function (a, b) { return sortKey(a).localeCompare(sortKey(b)); });
    var ancRows = sorted.map(function (r) {
      return E.ROW([
        E.C(r.model_name), E.C(r.spec_used || ''), E.C(r.condition), E.C(GR[r.grade] || r.grade), E.N(f.num(r.age_months)), E.N(f.eur(r.rrp_eur_launch_de)),
        E.C(f.de(r.date_seen), { nowrap: true }), E.N(f.eur(r.price_eur)), E.N(f.pct(r.realisation)),
        E.C(KIND[r.source_kind] || 'Marktplatz-Angebot', { tag: r.manual ? TAG_MAN : '', tagText: r.manual ? TAG_MAN_TEXT : '' }),
        r.source_url ? E.C('', { href: r.source_url }) : E.C('keine Adresse')
      ]);
    });
    var tAnc = E.TABLE('m-anchors', 'Alle Preisbelege ' + current + ' (eine Zeile je öffentlichem Preis, mit Quelle)', [
      E.H('Modell'), E.H('Ausstattung'), E.H('Zustand laut Verkäufer'), E.H('Zustandsstufe'), E.H('Monate', 1), E.H('UVP', 1), E.H('Datum'), E.H('Preis', 1), E.H('Realisierung', 1), E.H('Preisart'), E.H('Quelle')
    ], ancRows, {
      tags: [TAG_PUB], collapsible: true,
      note: extra.length ? f.qty(extra.length) + ' manuell erfasste Preisbelege zählen hier und im Detail-Diagramm mit, nicht in den Kurven.' : '',
      empty: 'Für diese Familie liegt kein Preisbeleg vor.',
      defs: [
        { k: 'Modell', v: 'Modell, wie der Hersteller es nennt.' },
        { k: 'Ausstattung', v: 'Speicher und Farbe, wie die Quelle sie nennt.' },
        { k: 'Zustand laut Verkäufer', v: 'die Zustandsangabe, wie sie auf der Seite des Verkäufers oder Ankäufers steht' },
        { k: 'Zustandsstufe', v: 'diese Angabe übersetzt auf A wie neu, B sehr gut, C gut, D akzeptabel; Ankauf-Gebote haben keine Stufe' },
        { k: 'Monate', v: 'Modellalter am Datum des Belegs, in Monaten seit deutschem Verkaufsstart' },
        { k: 'UVP', v: 'unverbindliche Preisempfehlung des Herstellers beim deutschen Verkaufsstart, einschließlich Mehrwertsteuer' },
        { k: 'Datum, Preis', v: 'Preis am Datum des Belegs, einschließlich Mehrwertsteuer' },
        { k: 'Realisierung', v: 'Preis geteilt durch UVP' },
        { k: 'Preisart', v: 'Marktplatz-Angebot (Preis, den ein Aufbereiter verlangt) oder Ankauf-Gebot (Höchstwert „bis zu“, den ein Ankäufer vor der Zustandsprüfung nennt); manuell erfasste Belege tragen die Marke manuell' },
        { k: 'Quelle', v: 'Adresse der Seite, von der der Beleg stammt' }
      ]
    });

    /* ---------- Klappbloecke: Methode und Grenzen ---------- */
    var blocks = [
      {
        key: 'm-method', title: 'Wie gerechnet wird', ordered: true, intro: '',
        items: [
          { lead: 'UVP beim Verkaufsstart', text: 'je Modell und Ausstattung aus der deutschen Pressemitteilung des Herstellers oder der Fachpresse, mit Adresse und Datum je Zeile.' },
          { lead: 'Gebrauchtpreis heute', text: 'je Modell, Ausstattung und Zustand von öffentlichen Gebrauchtmarktplätzen (Marktplatz-Angebote) und Ankaufseiten (Ankauf-Gebote als Höchstwert „bis zu“ vor der Zustandsprüfung), eine Zeile je Preisbeleg.' },
          { lead: 'Realisierung', text: '= Gebrauchtpreis / UVP derselben Speicherstufe; beide einschließlich Mehrwertsteuer, Deutschland. Die Zustandsangaben der Verkäufer werden auf die Zustandsstufen A bis D übersetzt; Ankauf-Gebote bilden eine eigene Gruppe.' },
          { lead: 'Kurve', text: 'je Familie und je Hersteller: durch die Preisbelege wird eine Linie gelegt, bei der das Gerät jeden Monat denselben Prozentsatz seines aktuellen Werts verliert; alle Marktplatz-Angebote gehen ein; die Stufen A, C und D bekommen einen festen Abstand zur Stufe B, der zusammen mit der Linie geschätzt wird, und abgelesen wird die Linie für Stufe B. Unter ' + f.qty(FIT_THIN_N) + ' Belegen oder unter ' + f.qty(FIT_MIN_SPAN) + ' Monaten Altersspanne gibt es keine Kurve; bei ' + f.qty(FIT_THIN_N) + ' bis ' + f.qty(FIT_OK_N - 1) + ' Belegen oder wenn die Linie steigt, gilt sie als unsicher.' },
          { lead: 'Gegen den Einkaufspreis:', text: disc === null
            ? 'der Einkaufspreis eines Leasinghauses ist nicht öffentlich; die Hülle hat keinen Einkaufsabschlag übergeben, deshalb bleibt die Spalte gegen Einkaufspreis leer.'
            : 'der Einkaufspreis eines Leasinghauses ist nicht öffentlich; ein benannter Platzhalter (' + f.pct1(disc) + ' unter UVP) rechnet um: Realisierung gegen UVP geteilt durch ' + loose(1 - disc) + '.' },
          { lead: 'Mietpreis:', text: 'nicht öffentlich und hier nicht verwendet; der Reiter Kreislauf rechnet mit simulierten Mieten, nie mit der Miete eines Leasinghauses.' }
        ]
      },
      {
        key: 'm-limits', title: 'Grenzen, ausgesprochen', ordered: false, intro: '',
        items: [
          { lead: '', text: 'Marktplatz-Angebote enthalten die Marge des Aufbereiters; wer an Großhändler verkauft, erzielt weniger. Ankauf-Gebote sind hier Höchstwerte „bis zu“ vor der Zustandsprüfung, kein Boden; der tatsächliche Ankaufpreis liegt darunter. Was ein Leasinghaus erzielt, kennt es nur aus seinen eigenen Verkäufen.' },
          { lead: '', text: 'Monate zählen das Modellalter seit deutschem Verkaufsstart, nicht das Alter des einzelnen Geräts: ein ' + ZAHLWORT[EX_OFFSET] + ' Monate nach Verkaufsstart gekauftes Gerät ist nach ' + f.qty(H24) + ' Monaten Miete ' + f.qty(H24 + EX_OFFSET) + ' Monate alt auf dieser Kurve.' },
          { lead: '', text: 'Ein Tag, ein Schnappschuss. Das Rechenmodell des Werkzeugs lernt aus realisierten Verkäufen über die Zeit; diese Seite ist die öffentliche Plausibilitätsprüfung für sein Niveau, kein Ersatz.' },
          { lead: '', text: 'Erstellt mit KI-Unterstützung (Recherche durch nur-lesende Web-Agenten, Stichproben adversarisch geprüft); jede Zahl trägt ihre Quelle, geprüft wurde eine Stichprobe, nicht jede Zeile.' }
        ]
      }
    ];

    return {
      kicker: kicker,
      subject: subject,
      intro: intro,
      kpis: kpis,
      kpiDefs: kpiDefs,
      chart: chart,
      chartNote: chartNote,
      tables: [tOem, tAnc],
      blocks: blocks,
      /* Sonderfelder Realisierung (Vertrag 7.3) */
      fams: fams, current: current, detail: detail
    };
  };
  w.RE.market.version = 3;
})(window);
