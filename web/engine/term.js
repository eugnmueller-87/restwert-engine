/* Restwert Engine v3, Motor Laufzeit (Reiter "term"). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6 und 7.7.
   Reine Funktion window.RE.term(D, opts, P): baut aus v3/data/term.json das Ansichtsmodell V.
   Texte, Zahlen, Rechenwege, Tabellen und Legenden eins zu eins aus dem heutigen Skript (v3/src/term.js,
   v3/src/term.body.html); jede Zahl kommt aus D und geht durch E.fmt. Kein DOM, kein Zustand, kein Nachladen.
   Zahlen, die heute im Text getippt standen, werden hier hergeleitet: die vier Laufzeiten aus D.rows[].t,
   "unter 18 Monaten" als abgerundetes Mindestalter der Apple-Marktplatz-Belege, "unter 36 Monaten" als erste
   Laufzeit mit einem Wert der Ankauf-Kurve, die Jahresnummern als Laufzeit geteilt durch die kürzeste Laufzeit,
   der Einkaufsabschlag aus D.disc. */
(function (w) {
  'use strict';
  var E = w.RE;

  /* Ersatzpalette (helle Tokenfarben aus styles.css), solange die Hülle noch keine Palette gelesen hat */
  var P0 = {
    font: '"Source Serif 4", serif', ink: '#201e1d', muted: '#7d7979', grid: '#d7d3d3', line: '#bab6b6',
    accent: '#0088b0', accent700: '#006786', accent2: '#d6006c', oem: {}
  };

  var TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' };
  var TAG_SIM = { cls: 'tag-neutral', text: 'simuliert' };
  var TAG_DER = { cls: 'tag-neutral', text: 'abgeleitet' };

  function isNum(x) { return typeof x === 'number' && isFinite(x); }

  w.RE.term = function (D, opts, P) {
    var f = E.fmt, pal = P || P0;
    if (!D || !Array.isArray(D.rows) || D.rows.length < 4 || !D.apple_ask || !D.apple_bid || !D.alt12) {
      throw new Error('term.json unbrauchbar: rows, apple_ask, apple_bid oder alt12 fehlen');
    }
    var rows = D.rows, ask = D.apple_ask, bid = D.apple_bid, alt = D.alt12;
    var jahre = Array.isArray(D.jahre) ? D.jahre : [];
    var fam = Array.isArray(D.fam) ? D.fam : [];
    var sim = Array.isArray(D.sim) ? D.sim : [];
    var points = Array.isArray(D.points) ? D.points : [];
    var factors = D.factors || {};

    /* Die vier Laufzeiten, positionsgleich mit dem heutigen Skript (r12 = rows[0], r36 = rows[2]) */
    var r12 = rows[0], r24 = rows[1], r36 = rows[2], r48 = rows[3];
    var T12 = f.qty(r12.t), T24 = f.qty(r24.t), T36 = f.qty(r36.t), T48 = f.qty(r48.t);
    var termsAll = rows.map(function (r) { return f.qty(r.t); });
    var termsUnd = termsAll.slice(0, -1).join(', ') + ' und ' + termsAll[termsAll.length - 1];
    var termsKomma = termsAll.join(', ');

    /* Hergeleitete Zahlen, die heute getippt im Text standen */
    var under = f.qty(Math.floor(ask.age_min));                       /* jüngster deutscher Marktplatz-Beleg, abgerundet */
    var bidRow = rows.filter(function (r) { return isNum(r.qbid); })[0];
    var bidFrom = bidRow ? f.qty(bidRow.t) : '';                        /* erste Laufzeit mit Wert der Ankauf-Kurve */
    var yr = function (r) { return f.qty(Math.round(r.t / r12.t)); };  /* Jahresnummer der Laufzeit */
    var disc = f.pct1(D.disc);                                         /* Einkaufsabschlag, Platzhalter; Schreibweise wie Huelle und Realisierung (CONTRACT 7.3) */
    /* Spanne des Apple-Rabatts der Simulation (config/lake.yaml, discount_by_oem.Apple; build.py schreibt sie nach term.json).
       Fehlt das Feld, steht der Klammersatz ohne Spanne: keine Zahl wird getippt. */
    var simDisc = isNum(D.sim_discount_min) && isNum(D.sim_discount_max) ? f.pct(D.sim_discount_min) + ' bis ' + f.pct(D.sim_discount_max) + ', ' : '';
    var studyLo = (D.buy_share - alt.hi_q) / r12.t;                    /* Wertverlust je Monat im ersten Jahr laut Studien */
    var studyHi = (D.buy_share - alt.lo_q) / r12.t;

    /* Lokale Formate, die E.fmt bewusst nicht hat (Vertrag Abschnitt 5) */
    var n2 = function (x) { return f.num(x, 2); };
    /* pb: Prozent, in Klammern außerhalb der Belegspanne (heute pctb) */
    var pb = function (x, d, extrap) { var s = d ? f.pct1(x) : f.pct(x); return s ? (extrap ? '(' + s + ')' : s) : ''; };
    /* fmtFactorLoose: der Platzhalter-Faktor so kurz wie heute (String(f0): "1,5", "1", "0,85") */
    var fmtFactorLoose = function (x) {
      if (!isNum(x)) return '';
      if (Math.round(x) === x) return f.num(x, 0);
      if (Math.round(x * 10) === x * 10) return f.num(x, 1);
      return f.num(x, 2);
    };
    var findFam = function (name) { return fam.filter(function (x) { return x.family === name; })[0]; };

    /* ---------- Kopf ---------- */
    var kicker = 'Analyse, öffentliche Preisbelege und Simulation, Stand ' + f.de(D.today);
    var subject = 'Laufzeit gegen Restwert: was ein Jahr mehr kostet';
    var intro = 'Die Frage: Ein längerer Vertrag bringt länger Miete, aber das Gerät kommt mit weniger Wert zurück, weil es Generationen überspringt. '
      + 'Wie viel weniger ist ein drei Jahre altes iPhone wert als ein ein Jahr altes, was heißt das je Vertragsmonat, und woran lässt sich drehen? '
      + 'Beispiel Apple Smartphone, weil dafür die meisten Preisbelege vorliegen; die Familienkurven stehen darunter. '
      + 'Öffentliche Preisbelege: ' + f.qty(ask.n) + ' Marktplatz-Angebote und ' + f.qty(bid.n) + ' Ankauf-Gebote für Apple Smartphones; '
      + 'simuliert: ' + f.qty(D.sim_n) + ' abgeschlossene iPhone-Kreisläufe.';

    /* ---------- Kennzahlen (die vier Kacheln) ---------- */
    var kpis = [
      {
        label: 'iPhone nach ' + T12 + ' Monaten',
        value: f.pct(alt.lo_q) + ' bis ' + f.pct(alt.hi_q),
        lines: ['der UVP, Ankauf-Gebote laut Studien USA und Großbritannien',
          'deutsche Marktplatz-Kurve, verlängert: ' + f.pct(r12.q),
          'kein deutscher Beleg unter ' + under + ' Monaten'],
        tags: [TAG_PUB]
      },
      {
        label: 'iPhone nach ' + T36 + ' Monaten',
        value: f.pct(r36.q),
        lines: ['der UVP, Marktplatz-Angebot laut deutscher Kurve',
          f.qty(ask.n) + ' Preisbelege',
          isNum(r36.qbid) ? 'Ankauf-Gebot „bis zu“: ' + f.pct(r36.qbid) : ''],
        tags: [TAG_PUB]
      },
      {
        label: 'Wertverlust je Monat, Jahr ' + yr(r12) + ' gegen Jahr ' + yr(r36),
        value: f.pct1(r12.year_pm) + ' gegen ' + f.pct1(r36.year_pm),
        lines: ['der UVP, beide nach der deutschen Kurve',
          'Jahr ' + yr(r12) + ' laut Studien: ' + f.pct1(studyLo) + ' bis ' + f.pct1(studyHi),
          'das erste Jahr ist das teure'],
        tags: [TAG_PUB]
      },
      {
        label: 'Nötige Miete, ' + T12 + ' gegen ' + T24 + ' Monate',
        value: 'Faktor ' + n2(r12.factor_derived) + ' bis ' + n2(alt.factor_hi),
        lines: [n2(r12.factor_derived) + ' nach der deutschen Kurve',
          n2(alt.factor_lo) + ' bis ' + n2(alt.factor_hi) + ' nach den Studien',
          'Platzhalter der Simulation: ' + n2(factors[String(r12.t)])],
        tags: [TAG_DER]
      }
    ];
    var kpiDefs = [
      { k: 'UVP', v: 'Unverbindliche Preisempfehlung des Herstellers beim deutschen Verkaufsstart; die Kurve rechnet einschließlich Mehrwertsteuer, Kosten und Einkaufspreis der Simulation ohne Mehrwertsteuer.' },
      { k: 'Faktor', v: 'nötige Miete je Monat einer Laufzeit geteilt durch die der ' + T24 + '-Monats-Laufzeit; so viel teurer oder günstiger je Monat muss der Vertrag sein.' }
    ];
    var calcnote = 'Kacheln: Prozent der UVP. Die deutsche Kurve sind Marktplatz-Angebote (Obergrenze), unter ' + under + ' Monaten über die Belege hinaus verlängert; '
      + 'die Studien sind Ankauf-Gebote aus den USA und Großbritannien, eine andere Preisart. '
      + 'Der Faktor sagt, um wie viel teurer je Monat ein ' + T12 + '-Monats-Vertrag gegenüber einem ' + T24 + '-Monats-Vertrag sein muss, damit Wertverlust und Kosten bis Verkauf gedeckt sind; '
      + 'die Studienwerte setzen ein Gebot gegen ein Angebot und liegen deshalb zu hoch.';

    /* ---------- Was man daraus macht (die sechs Handlungen) ---------- */
    var lap = findFam('Laptop'), sm = findFam('Smartphone');
    var actions = [
      { lead: 'Die Miete je Laufzeit aus der Kurve ableiten, nicht flach setzen.',
        text: 'Ein ' + T12 + '-Monats-Vertrag muss je Monat den Faktor ' + n2(r12.factor_derived) + ' (Kurve) bis ' + n2(alt.factor_hi) + ' (Studien) der ' + T24 + '-Monats-Miete kosten, '
          + 'ein ' + T36 + '-Monats-Vertrag nur ' + n2(r36.factor_derived) + ', ein ' + T48 + '-Monats-Vertrag ' + n2(r48.factor_derived) + '. '
          + 'Wer alle Laufzeiten gleich bepreist, verschenkt bei kurzen Verträgen Geld und verliert bei langen den Kunden an den Wettbewerb. '
          + 'Das ist die Zahl, die die Stellschraube Laufzeit (Kennung L07 auf dem Reiter Stellschrauben) misst; mit dieser Preisregel wäre ihr Hebel null.' },
      { lead: 'Das erste Jahr ist das teure, nicht das dritte.',
        text: 'Der Wertverlust je Monat liegt in Jahr ' + yr(r12) + ' bei ' + f.pct1(studyLo) + ' bis ' + f.pct1(studyHi) + ' der UVP (Studien; die verlängerte deutsche Kurve sagt ' + f.pct1(r12.year_pm) + '), '
          + 'in Jahr ' + yr(r36) + ' bei ' + f.pct1(r36.year_pm) + ', in Jahr ' + yr(r48) + ' bei ' + f.pct1(r48.year_pm) + '. '
          + 'Dass der erste Verlust der größte ist, tragen die Studien; die deutsche Kurve zeigt unter ' + under + ' Monaten keinen Beleg. '
          + 'Ein Gerät, das drei Jahre läuft, überspringt zwar zwei Generationen, aber den größten Sprung macht es im ersten Jahr, den zahlt jede Laufzeit. '
          + 'Lange Verträge sind deshalb je Monat die günstigen. Reparatur und Lagertage stecken in den Kosten bis Verkauf und wachsen mit der Laufzeit; '
          + 'was nicht drin ist, sind Akkuzustand und Kundenzufriedenheit bei alten Geräten.' },
      { lead: 'Zweiter Kreislauf statt Verkauf nach ' + T12 + ' Monaten.',
        text: 'Ein Gerät, das nach einem Jahr mit ' + f.pct(alt.lo_q) + ' bis ' + f.pct(r12.q) + ' der UVP zurückkommt, ist als Gebrauchtgerät noch vermietbar '
          + '(die Spanne reicht vom Ankauf-Gebot der Studien bis zur verlängerten Marktplatz-Kurve); ein zweiter Vertrag über ' + T24 + ' Monate zu einer niedrigeren Miete spart den zweiten Einkauf und die zweite Kanalgebühr. '
          + 'Die Simulation verkauft heute nach jedem Vertrag; das ist der Kandidat für die nächste Ausbaustufe (v0.3).' },
      { lead: 'Laufzeit nach Geräteart wählen.',
        text: (lap && sm ? 'Ein Laptop verliert bei ' + T36 + ' Monaten ' + f.pct1(lap.loss_pm[r36.t]) + ' der UVP je Monat, ein Smartphone ' + f.pct1(sm.loss_pm[r36.t]) + '. ' : '')
          + 'Lange Laufzeiten gehören zu Laptops und Tablets, bei Smartphones ist ' + T24 + ' die Mitte; ' + T12 + ' Monate nur mit der hohen Miete aus der Kurve (erster Punkt) oder mit dem zweiten Kreislauf (dritter Punkt).' },
      { lead: 'Vorzeitige Rückgabe bepreisen.',
        text: 'In der Simulation verliert jeder vorzeitig beendete Vertrag Geld, egal welche Laufzeit (Tabelle unten, Spalte „Lifecycle-Marge, vorzeitig zurück“), '
          + 'weil die Miete aufhört, das Gerät aber schon den Wertverlust der ersten Monate getragen hat und die Kosten bis Verkauf voll anfallen. '
          + 'Der Vertrag braucht einen Ausgleich bei vorzeitiger Rückgabe, mindestens die entgangene Miete bis zum Punkt, ab dem das Gerät seine Kosten eingespielt hat (Reiter Gerät). '
          + 'Die Simulation kennt diesen Ausgleich noch nicht; er ist eine Zeile im Kundenvertrag und ein Kandidat für die nächste Ausbaustufe (v0.3).' },
      { lead: 'Verkaufszeitpunkt vor dem Nachfolger.',
        text: 'Die Kurve mittelt den Sprung beim Erscheinen der nächsten Generation; ein Vertrag, der einen Monat vor dem Nachfolger endet, gibt das Gerät in den teureren Markt zurück. '
          + 'Das lässt sich mit den Verkaufsstartdaten des Katalogs planen, braucht aber erst einen Beleg mit Datum (offen).' }
    ];

    /* ---------- Diagramm: die Kurve, mit den vier Laufzeiten markiert ---------- */
    var traces = [];
    var curve = function (c, name, dash) {
      if (!c || !isNum(c.slope) || !isNum(c.intercept)) return;
      var xs = [], ys = [];
      for (var m = Math.floor(c.age_min); m <= Math.ceil(c.age_max); m++) { xs.push(m); ys.push(Math.min(1, Math.exp(c.intercept + c.slope * m))); }
      traces.push({ type: 'scatter', mode: 'lines', name: name, x: xs, y: ys, line: { color: pal.ink, dash: dash, width: 2 }, hoverinfo: 'skip' });
    };
    curve(ask, 'Kurve Marktplatz-Angebote, ' + f.qty(ask.n) + ' Preisbelege (Obergrenze)', 'solid');
    curve(bid, 'Kurve Ankauf-Gebote „bis zu“, ' + f.qty(bid.n) + ' Preisbelege (Ankaufsseite, Höchstwerte)', 'dot');
    var askPts = points.filter(function (p) { return p.kind === 'ask'; }), bidPts = points.filter(function (p) { return p.kind === 'bid'; });
    var marks = function (pts, sym, color, name) {
      if (!pts.length) return;
      traces.push({
        type: 'scatter', mode: 'markers', name: name,
        x: pts.map(function (p) { return p.age; }), y: pts.map(function (p) { return p.q; }),
        marker: { symbol: sym, size: 9, color: color, opacity: 0.8 },
        text: pts.map(function (p) { return p.model + ' ' + p.spec + '<br>Monate ' + f.num(p.age, 1) + ', Realisierung ' + f.pct(p.q); }),
        hovertemplate: '%{text}<extra></extra>'
      });
    };
    marks(askPts, 'diamond', pal.accent, 'Marktplatz-Angebote Zustandsstufe B, ' + f.qty(askPts.length) + ' Preisbelege');
    marks(bidPts, 'triangle-down', pal.accent2, 'Ankauf-Gebote „bis zu“, ' + f.qty(bidPts.length) + ' Preisbelege');
    var ages = points.map(function (p) { return p.age; }).concat([ask.age_max, bid.age_max]).filter(isNum);
    var xmax = Math.ceil(Math.max.apply(null, ages.length ? ages : [r48.t])) + 2;
    var ticks = [0, 0.2, 0.4, 0.6, 0.8, 1];
    var layout = {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: pal.font, color: pal.ink, size: 13 },
      xaxis: { title: { text: 'Monate seit deutschem Verkaufsstart des Modells' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: false, range: [0, xmax] },
      yaxis: { title: { text: 'Realisierung in % der UVP' }, tickvals: ticks, ticktext: ticks.map(function (t) { return f.pct(t); }), range: [0, 1.05], gridcolor: pal.grid, linecolor: pal.line, zeroline: false },
      legend: { orientation: 'h', y: 1.02, yanchor: 'bottom', x: 0, xanchor: 'left', font: { color: pal.ink } },
      hoverlabel: { font: { family: pal.font } },
      shapes: rows.map(function (r) { return { type: 'line', x0: r.t, x1: r.t, y0: 0, y1: 1, yref: 'paper', line: { color: pal.line, dash: 'dash', width: 1 } }; }),
      annotations: rows.map(function (r) { return { x: r.t, y: 0, yref: 'paper', text: f.qty(r.t) + ' Monate', showarrow: false, yanchor: 'bottom', xanchor: 'left', xshift: 4, font: { size: 11, color: pal.muted } }; })
    };
    var chartNote = 'Die Kurve, mit den vier Laufzeiten markiert (öffentlich). Durchgezogen: Kurve der Marktplatz-Angebote (Apple Smartphones), gepunktet: Kurve der Ankauf-Gebote „bis zu“ (Höchstwerte vor der Zustandsprüfung, kein Boden), '
      + 'jeweils nur über der Altersspanne ihrer Belege. Punkte: die Preisbelege selbst (Raute Zustandsstufe B, Dreieck Ankauf-Gebot). Senkrechte Linien: ' + termsKomma + ' Monate. '
      + 'Links von ' + under + ' Monaten gibt es keinen deutschen Beleg; dort helfen nur die Studien.';

    /* ---------- Tabelle 1: Was ein iPhone nach 12, 24, 36 und 48 Monaten noch wert ist ---------- */
    var rvRows = rows.map(function (r, i) {
      var prev = rows[Math.max(0, i - 1)];
      var studies = jahre.filter(function (j) { return j.monate === r.t; }).map(function (j) { return { href: j.url, text: j.quelle, rest: j.datum + ': ' + j.modelle }; });
      return E.ROW([
        E.C(f.qty(r.t)),
        E.N(pb(r.q, 0, r.extrap)),
        E.N(r.raw ? f.pct(r.raw.med) + ' (' + f.qty(r.raw.qty) + ' Preisbelege)' : 'kein Beleg'),
        E.N(isNum(r.qbid) ? pb(r.qbid, 0, r.extrap_bid) : ''),
        E.N(r.rawbid ? f.pct(r.rawbid.med) + ' (' + f.qty(r.rawbid.qty) + ' Preisbelege)' : 'kein Beleg'),
        E.C('', { links: studies, minW: studies.length ? 240 : 0 }),
        E.N(pb(r.loss, 0, r.extrap)),
        E.N(pb(r.loss_pm, 1, r.extrap)),
        E.N(pb(r.year_pm, 1, r.extrap || prev.extrap))
      ]);
    });
    var tRv = E.TABLE('x-rv', 'Was ein iPhone nach ' + termsUnd + ' Monaten noch wert ist', [
      E.H('Monate'), E.H('Marktplatz-Angebot, Kurve', 1), E.H('Preisbelege, mittleres Angebot', 1), E.H('Ankauf-Gebot, Kurve', 1), E.H('Ankauf-Gebote, Belege', 1),
      E.H('Studien USA und Großbritannien'), E.H('Wertverlust seit Kauf', 1), E.H('je Monat seit Kauf', 1), E.H('je Monat, dieses Jahr', 1)
    ], rvRows, {
      n: 0,
      tags: [TAG_PUB],
      defs: [
        { k: 'Monate', v: 'Modellalter seit deutschem Verkaufsstart; ein Gerät, das am Verkaufsstart gekauft und ' + T24 + ' Monate vermietet wird, ist bei Rückgabe ' + T24 + ' Monate alt.' },
        { k: 'Marktplatz-Angebot, Kurve', v: 'Realisierung in Prozent der UVP laut Kurve des Reiter Realisierung (Apple Smartphones, alle Stufen, abgelesen für Zustandsstufe B); Obergrenze, weil die Marge des Aufbereiters darin steckt; in Klammern, wo die Kurve über die Belege hinaus verlängert ist.' },
        { k: 'Preisbelege, mittleres Angebot', v: 'das mittlere Marktplatz-Angebot der deutschen Belege in Zustandsstufe B, deren Alter in dieses Jahr fällt (Jahr ' + yr(r12) + ' = ' + f.qty(0) + ' bis ' + f.qty(r12.t - 1) + ' Monate); die Spalte Belege zählt sie.' },
        { k: 'Ankauf-Gebot, Kurve', v: 'die Ankaufsseite laut Kurve der Apple Ankauf-Gebote; leer, wo die Kurve keine Belege hat' + (bidFrom ? ' (unter ' + bidFrom + ' Monaten)' : '') + '. Achtung: die deutschen Ankauf-Belege sind Höchstwerte „bis zu“ vor der Zustandsprüfung (Bestzustand), also kein Boden; der tatsächliche Ankaufpreis liegt darunter.' },
        { k: 'Ankauf-Gebote, Belege', v: 'das mittlere Ankauf-Gebot „bis zu“ der deutschen Belege, deren Alter in dieses Jahr fällt; die Spalte Belege zählt sie.' },
        { k: 'Studien USA und Großbritannien', v: 'Realisierung nach Ankauf-Gebot laut veröffentlichten Studien in den USA und in Großbritannien (' + f.pct(1) + ' minus dort genannter Verlust), mit Quelle; andere Länder, andere Preisart als die Marktplatz-Kurve, deshalb nur als Bandbreite dort, wo deutsche Belege fehlen; die Tabelle der Studien führt sie einzeln auf.' },
        { k: 'Wertverlust seit Kauf', v: 'Einkaufspreis minus Marktplatz-Angebot, in Prozent der UVP; der Einkaufspreis ist als UVP minus ' + disc + ' angenommen (Platzhalter mit verantwortlicher Rolle).' },
        { k: 'je Monat seit Kauf', v: 'Wertverlust seit Kauf geteilt durch alle Monate seit Kauf.' },
        { k: 'je Monat, dieses Jahr', v: 'nur der Wertverlust dieses einen Jahres (Kurvenwert am Jahresanfang minus am Jahresende), geteilt durch ' + T12 + '; zeigt, welches Jahr das teure ist.' }
      ]
    });

    /* ---------- Tabelle 2: die Studien einzeln ---------- */
    var stRows = jahre.map(function (j) {
      return E.ROW([
        E.C('', { href: j.url, linkText: j.quelle }),
        E.C(j.datum, { nowrap: true }),
        E.C(j.markt),
        E.N(f.qty(j.monate)),
        E.C(j.modelle, { minW: 240 })
      ]);
    });
    var tStudies = E.TABLE('x-studies', 'Studien USA und Großbritannien, Ankauf-Gebote', [
      E.H('Quelle'), E.H('Datum'), E.H('Markt'), E.H('Monate', 1), E.H('Modelle')
    ], stRows, {
      tags: [TAG_PUB],
      note: 'Realisierung nach Ankauf-Gebot laut veröffentlichten Studien (' + f.pct(1) + ' minus dort genannter Verlust); andere Länder, andere Preisart als die Marktplatz-Kurve, deshalb nur als Bandbreite dort, wo deutsche Belege fehlen.',
      empty: 'Keine Studie hinterlegt.',
      defs: [
        { k: 'Quelle', v: 'Herausgeber der Studie, mit Link auf die Seite.' },
        { k: 'Datum', v: 'Datum der Veröffentlichung oder des Abrufs, wie in der Quelle genannt.' },
        { k: 'Markt', v: 'Land, Preisart und Bezugsgröße der Studie.' },
        { k: 'Monate', v: 'Modellalter in Monaten, für das die Studie den Wert nennt; die Zeile der Tabelle darüber mit derselben Zahl.' },
        { k: 'Modelle', v: 'die dort genannten Modelle mit ihrer Realisierung in Prozent der UVP.' }
      ]
    });

    /* ---------- Tabelle 3: Was jeder Vertragsmonat kosten muss ---------- */
    var needRows = rows.map(function (r) {
      return E.ROW([
        E.C(f.qty(r.t) + ' Monate', { nowrap: true }),
        E.N(pb(r.loss, 0, r.extrap)),
        E.N(pb(r.loss_pm, 1, r.extrap)),
        E.N(f.pct1(r.cost_pm)),
        E.N(pb(r.need_pm, 1, r.extrap)),
        E.N(r.extrap ? '(' + n2(r.factor_derived) + ')' : n2(r.factor_derived)),
        E.N(n2(r.factor_sim))
      ]);
    });
    var tNeed = E.TABLE('x-need', 'Was jeder Vertragsmonat kosten muss, damit nichts fehlt', [
      E.H('Laufzeit'), E.H('Wertverlust seit Kauf', 1), E.H('je Monat', 1), E.H('Kosten bis Verkauf, monatlich', 1), E.H('Nötige Miete je Monat', 1),
      E.H('Faktor gegen ' + T24 + ' Monate', 1), E.H('Faktor in der Simulation', 1)
    ], needRows, {
      n: 0,
      tags: [TAG_PUB, TAG_SIM, TAG_DER],
      note: 'Die Miete eines Vertrags muss zwei Dinge decken: den Wertverlust des Geräts über die Laufzeit (öffentliche Kurve) und die Kosten bis Verkauf (simuliert, je Laufzeit aus den abgeschlossenen iPhone-Kreisläufen). '
        + 'Ein Teil davon fällt je Gerät an, egal wie lang der Vertrag war (Einrichtung, Versand, Rücksendung, Datenlöschung, Aufbereitung, Kanalgebühren), ein Teil wächst mit der Laufzeit (Reparatur, Lagertage). '
        + 'Beides geteilt durch die Monate ergibt die Miete je Monat, bei der die Lifecycle-Marge null ist. '
        + 'Alles in Prozent der UVP; Kosten und Einkaufspreis ohne Mehrwertsteuer gegen die UVP ohne Mehrwertsteuer, die Kurve auf beiden Seiten einschließlich Mehrwertsteuer.',
      foot: T12 + ' Monate nach den Studien statt nach der verlängerten Kurve: Realisierung ' + f.pct(alt.lo_q) + ' bis ' + f.pct(alt.hi_q) + ' (Ankauf-Gebote USA und Großbritannien), '
        + 'nötige Miete ' + f.pct1(alt.need_lo) + ' bis ' + f.pct1(alt.need_hi) + ' je Monat, Faktor ' + n2(alt.factor_lo) + ' bis ' + n2(alt.factor_hi) + ' gegen ' + T24 + ' Monate; '
        + 'die Spanne mischt ein Gebot im Zähler mit dem Angebot im Nenner und liegt deshalb zu hoch.',
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit in Monaten; das Gerät ist bei Rückgabe so viele Monate alt.' },
        { k: 'Wertverlust seit Kauf', v: 'Einkaufspreis minus Marktplatz-Angebot laut Kurve am Ende der Laufzeit, in Prozent der UVP; in Klammern außerhalb der Belegspanne.' },
        { k: 'je Monat', v: 'Wertverlust seit Kauf geteilt durch die Monate der Laufzeit.' },
        { k: 'Kosten bis Verkauf, monatlich', v: 'simuliert, dieselbe Zahl wie in der Tabelle der Simulation: im Mittel ' + f.eur(D.sim_cost) + ' je abgeschlossenem iPhone-Kreislauf bei ' + f.eur(D.sim_rrp_net) + ' UVP ohne Mehrwertsteuer, also ' + f.pct(D.cost_share) + ' der UVP; je Laufzeit der eigene Wert, geteilt durch die Monate.' },
        { k: 'Nötige Miete je Monat', v: '(Wertverlust seit Kauf plus Kosten bis Verkauf) geteilt durch die Monate; ohne Gewinn, ohne Finanzierung, Gemeinkosten, Steuern; Nutzerbetreuung und Geräteverwaltung stecken als Umlage in den Kosten bis Verkauf.' },
        { k: 'Faktor gegen ' + T24 + ' Monate', v: 'nötige Miete je Monat dieser Laufzeit geteilt durch die der ' + T24 + '-Monats-Laufzeit, abgeleitet aus Kurve und Kosten; so viel teurer oder günstiger je Monat muss der Vertrag sein.' },
        { k: 'Faktor in der Simulation', v: 'der Platzhalter (term_rate_factor in config/lake.yaml), mit dem die Simulation heute die Monatsrate je Laufzeit skaliert; Verantwortlich: Leitung Customer Success.' }
      ]
    });

    /* ---------- Tabelle 4: Dasselbe je Geräteart ---------- */
    var famTerms = rows.map(function (r) { return r.t; }).filter(function (t) { return fam.some(function (x) { return x.extrap && !x.extrap[String(t)]; }); });
    var famRows = fam.map(function (x) {
      var cells = [E.C(x.family), E.N(f.qty(x.n)), E.C(f.num(x.age_min, 1) + ' bis ' + f.num(x.age_max, 1), { nowrap: true })];
      famTerms.forEach(function (t) { cells.push(E.N(pb(x.q[String(t)], 0, x.extrap[String(t)]))); });
      famTerms.forEach(function (t) { cells.push(E.N(pb(x.loss_pm[String(t)], 1, x.extrap[String(t)]))); });
      return E.ROW(cells);
    });
    var famCols = [E.H('Geräteart'), E.H('Preisbelege', 1), E.H('Monate')];
    famTerms.forEach(function (t) { famCols.push(E.H('nach ' + f.qty(t) + ' Monaten', 1)); });
    famTerms.forEach(function (t) { famCols.push(E.H('je Monat bei ' + f.qty(t), 1)); });   /* hoechstens vier Woerter je Kopf (CONTRACT 4.1); Wertverlust sagt die Legende */
    var famTermList = famTerms.map(function (t) { return f.qty(t); }).join(', ');
    var tFam = E.TABLE('x-fam', 'Dasselbe je Geräteart: wer verträgt lange Laufzeiten', famCols, famRows, {
      n: fam.reduce(function (s, x) { return s + (isNum(x.n) ? x.n : 0); }, 0),
      tags: [TAG_PUB],
      defs: [
        { k: 'Geräteart', v: 'Gerätefamilie; die Kurve des Reiter Realisierung läuft über alle Hersteller der Familie.' },
        { k: 'Preisbelege', v: 'Stückzahl der Marktplatz-Angebote, aus denen die Familienkurve gerechnet ist.' },
        { k: 'Monate', v: 'Altersspanne dieser Belege in Monaten seit deutschem Verkaufsstart, jüngster bis ältester Beleg.' },
        { k: 'nach ' + famTermList + ' Monaten', v: 'Realisierung laut Familienkurve (Marktplatz-Angebote, abgelesen für Zustandsstufe B), in Klammern außerhalb der Belegspanne.' },
        { k: 'je Monat bei ' + famTermList, v: 'Wertverlust je Monat bei dieser Laufzeit: (Einkaufspreis minus Realisierung) geteilt durch die Monate, in Prozent der UVP; je kleiner, desto besser trägt die Geräteart eine lange Laufzeit.' }
      ]
    });

    /* ---------- Tabelle 5: Wie es in der Simulation heute aussieht ---------- */
    var simRows = sim.map(function (r) {
      return E.ROW([
        E.C(f.qty(r.term_months) + ' Monate', { nowrap: true }),
        E.N(f.qty(r.n)),
        E.N(f.qty(r.early || 0)),
        E.N(isNum(r.mb) ? f.num(r.mb, 1) : ''),
        E.N(f.eur(r.p)),
        E.N(f.eur2(r.rate)),
        E.N(f.eur(r.rent)),
        E.N(f.eur(r.rv)),
        E.N(f.eur(r.c)),
        E.N(f.eur(r.m), { neg: isNum(r.m) && r.m < 0 }),
        E.N(f.eur(r.m_full), { neg: isNum(r.m_full) && r.m_full < 0 }),
        E.N(f.eur(r.m_early), { neg: isNum(r.m_early) && r.m_early < 0 })
      ]);
    });
    var simN = isNum(D.sim_n) ? D.sim_n : sim.reduce(function (s, r) { return s + (isNum(r.n) ? r.n : 0); }, 0);
    var sim48 = sim.filter(function (r) { return r.term_months === r48.t; })[0];
    var tSim = E.TABLE('x-sim', 'Wie es in der Simulation heute aussieht', [
      E.H('Laufzeit'), E.H('abgeschlossen', 1), E.H('davon vorzeitig zurück', 1), E.H('Monate abgerechnet, im Mittel', 1), E.H('Einkaufspreis', 1), E.H('Miete je Monat', 1),
      E.H('Mieterlös', 1), E.H('Restwert', 1), E.H('Kosten bis Verkauf', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('Lifecycle-Marge, volle Laufzeit', 1), E.H('Lifecycle-Marge, vorzeitig zurück', 1)
    ], simRows, {
      n: simN,
      tags: [TAG_SIM],
      defs: [
        { k: 'Laufzeit', v: 'geplante Vertragslaufzeit in Monaten; die Zeile mittelt alle abgeschlossenen iPhone-Kreisläufe dieser Laufzeit.' },
        { k: 'abgeschlossen', v: 'Stückzahl der abgeschlossenen iPhone-Kreisläufe je Laufzeit in der Simulation.' },
        { k: 'davon vorzeitig zurück', v: 'Verträge, die vor dem geplanten Ende endeten; das Gerät kam zurück, die Miete hörte auf, der Vertrag sah keinen Ausgleich vor (Annahme der Simulation).' },
        { k: 'Monate abgerechnet', v: 'tatsächlich berechnete Vertragsmonate im Mittel, gegen die Laufzeit in der ersten Spalte.' },
        { k: 'Einkaufspreis', v: 'Rechnungspreis des Lieferanten je Gerät, ohne Mehrwertsteuer, Mittel je Gerät.' },
        { k: 'Miete je Monat', v: 'Monatsrate der Simulation, Mittel je Gerät; folgt aus dem Platzhalter-Faktor je Laufzeit.' },
        { k: 'Mieterlös', v: 'Miete über die abgerechneten Monate, Mittel je Gerät.' },
        { k: 'Restwert', v: 'Verkaufspreis nach dem Leasing, vor Abzug der Kanalgebühren und ohne Mehrwertsteuer, Mittel je Gerät.' },
        { k: 'Kosten bis Verkauf', v: 'alles vom Einkauf bis zum Zahlungseingang aus dem Verkauf, Mittel je Gerät.' },
        { k: 'Lifecycle-Marge je Gerät', v: 'Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf, Mittel je Gerät.' },
        { k: 'Lifecycle-Marge, volle Laufzeit', v: 'Lifecycle-Marge je Gerät nur der Verträge, die bis zum Ende liefen.' },
        { k: 'Lifecycle-Marge, vorzeitig zurück', v: 'Lifecycle-Marge je Gerät nur der Verträge, die vorzeitig endeten.' }
      ],
      footLead: 'Was bis zum Gewinn fehlt',
      footLines: ['Finanzierungskosten, Gemeinkosten und Steuern', 'Gewinnaufschlag; Nutzerbetreuung und Geräteverwaltung (MDM) sind als Umlage schon in den Kosten bis Verkauf'],
      foot: 'Mittelwerte je Gerät der abgeschlossenen iPhone-Kreisläufe der Simulation. Die Miete je Monat folgt aus dem Platzhalter-Faktor je Laufzeit; die Rangfolge der Laufzeiten ist deshalb eine Folge dieser Annahme, kein Marktbefund. '
        + (sim48 ? T48 + ' Monate sind erst wenige abgeschlossen (' + f.qty(sim48.n) + ', davon ' + f.qty(sim48.early || 0) + ' vorzeitig beendet); die Zahl ist ein Urteil über vorzeitige Rückgaben, nicht über ' + T48 + ' Monate. ' : '')
        + 'Der Einkaufspreis hier ist netto und liegt bei ' + f.pct(D.sim_buy_net) + ' der UVP ohne Mehrwertsteuer (Apple-Rabatt der Simulation, ' + simDisc + 'Platzhalter); '
        + 'die Marktrechnung oben nimmt einschließlich Mehrwertsteuer ' + f.pct(D.buy_share) + ' der UVP (Platzhalter des Einkaufs, ' + disc + ' Abschlag). '
        + 'Zwei Platzhalter für dieselbe Größe; welcher gilt, entscheidet der Einkaufsleitung.'
    });

    /* ---------- Tabelle 6: Was die Miete je Laufzeit ausmacht (nur die Miete geändert) ---------- */
    var wiRows = [];
    sim.forEach(function (r) {
      var t = r.term_months, f0 = factors[String(t)], row = rows.filter(function (x) { return x.t === t; })[0];
      if (!isNum(f0) || !f0 || !row || !isNum(r.m) || !isNum(r.rate)) return;
      var fLo = (t === r12.t) ? Math.min(row.factor_derived, alt.factor_lo) : row.factor_derived;
      var fHi = (t === r12.t) ? alt.factor_hi : row.factor_derived;
      var rLo = r.rate * fLo / f0, rHi = r.rate * fHi / f0;
      var mLo = r.m + (rLo - r.rate) * t, mHi = r.m + (rHi - r.rate) * t;
      var same = fLo === fHi;
      wiRows.push(E.ROW([
        E.C(f.qty(t) + ' Monate', { nowrap: true }),
        E.N(fmtFactorLoose(f0)),
        E.N(f.eur2(r.rate)),
        E.N(f.eur(r.m), { neg: r.m < 0 }),
        E.N(same ? n2(fLo) : n2(fLo) + ' bis ' + n2(fHi)),
        E.N(same ? f.eur2(rLo) : f.eur2(rLo) + ' bis ' + f.eur2(rHi)),
        E.N(same ? f.eur(mLo) : f.eur(mLo) + ' bis ' + f.eur(mHi), { neg: mHi < 0 })
      ]));
    });
    var tWhatIf = E.TABLE('x-whatif', 'Was die Miete je Laufzeit ausmacht', [
      E.H('Laufzeit'), E.H('Faktor, Platzhalter', 1), E.H('Miete je Monat', 1), E.H('Lifecycle-Marge je Gerät', 1),
      E.H('Faktor, abgeleitet', 1), E.H('Miete je Monat, abgeleitet', 1), E.H('Lifecycle-Marge, abgeleitet', 1)
    ], wiRows, {
      n: 0,
      tags: [TAG_SIM, TAG_DER],
      note: 'Nur die Miete geändert, alles andere wie in der Simulation; die Lifecycle-Marge je Gerät verschiebt sich um die Mietänderung mal die Monate der Laufzeit.',
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit in Monaten der Simulation.' },
        { k: 'Faktor, Platzhalter', v: 'der Platzhalter (term_rate_factor in config/lake.yaml), mit dem die Simulation heute die Monatsrate je Laufzeit skaliert.' },
        { k: 'Miete je Monat', v: 'Monatsrate der Simulation mit dem Platzhalter-Faktor, Mittel je Gerät.' },
        { k: 'Lifecycle-Marge je Gerät', v: 'Lifecycle-Marge je Gerät der Simulation mit dieser Miete, Mittel.' },
        { k: 'Faktor, abgeleitet', v: 'Faktor gegen ' + T24 + ' Monate aus Kurve und Kosten (Tabelle oben); bei ' + T12 + ' Monaten die Spanne von der Kurve bis zu den Studien.' },
        { k: 'Miete je Monat, abgeleitet', v: 'Miete je Monat mal abgeleiteter Faktor geteilt durch Platzhalter-Faktor.' },
        { k: 'Lifecycle-Marge, abgeleitet', v: 'Lifecycle-Marge je Gerät mit der abgeleiteten Miete, alles andere unverändert.' }
      ]
    });

    /* ---------- Grenzen, ausgesprochen ---------- */
    var blocks = [{
      key: 'x-limits', title: 'Grenzen, ausgesprochen', ordered: false, intro: '',
      items: [
        { lead: '', text: 'Unter ' + under + ' Monaten Modellalter gibt es keinen deutschen Preisbeleg; der Wert nach ' + T12 + ' Monaten ist eine Verlängerung der Kurve und wird durch die Studien (USA, Großbritannien, Ankauf-Gebote) nach unten abgesichert. Deshalb steht für ' + T12 + ' Monate eine Spanne.' },
        { lead: '', text: 'Marktplatz-Angebote sind die Obergrenze (Marge des Aufbereiters). Die deutschen Ankauf-Belege sind Höchstwerte „bis zu“ eines Ankäufers vor der Zustandsprüfung, also kein Boden; der tatsächliche Ankaufpreis liegt darunter. Was ein Leasinghaus erzielt, hängt vom Kanal ab (Reiter Stellschrauben, Kanalwahl).' },
        { lead: '', text: 'Die Kosten bis Verkauf sind simuliert; die Kurve ist öffentlich. Die abgeleiteten Faktoren mischen beides und sind deshalb eine Größenordnung, kein Preis. Die Studienwerte für Jahr ' + yr(r12) + ' sind Ankauf-Gebote aus den USA und Großbritannien; wo sie gegen die deutsche Marktplatz-Kurve gerechnet werden, steht das dabei.' },
        { lead: '', text: 'Der Sprung beim Erscheinen des Nachfolgers steckt in der Kurve nur gemittelt; wer einen Monat vor dem Nachfolger verkauft, liegt darüber, wer danach verkauft, darunter. Dafür fehlt ein Beleg mit Datum.' }
      ]
    }];

    return {
      kicker: kicker,
      subject: subject,
      intro: intro,
      kpis: kpis,
      kpiDefs: kpiDefs,
      calcnote: calcnote,
      actions: actions,
      chart: { traces: traces, layout: layout },
      chartNote: chartNote,
      tables: [tRv, tStudies, tNeed, tFam, tSim, tWhatIf],
      blocks: blocks
    };
  };
  w.RE.term.version = 3;
})(window);
