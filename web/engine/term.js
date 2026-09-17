/* Restwert Engine v3, Motor Laufzeit (Reiter "term"). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6 und 7.7.
   Reine Funktion window.RE.term(D, opts, P): baut aus data/term.json das Ansichtsmodell V.
   Seit 17.09.2026 mit Auswahl (Eugens Frage: „Hier startet die Seite mit iPhone nach 12 Monaten, warum? Habe ich es
   ausgewählt?"): opts.slug wählt das Gerät (Modell aus dem Katalog; die Kurve ist die seiner Serie, Geräteart und
   Hersteller, weil nur dort eine Alterskurve mit Belegen vorliegt), opts.t1 die Laufzeit, opts.t2 die Vergleichslaufzeit;
   opts.select(patch) meldet einen Wechsel an die Hülle (Muster des Zyklus-Umschalters auf dem Reiter KPIs).
   Ohne Auswahl gilt, was der Generator aus den Daten bestimmt hat: die Serie mit den meisten Preisbelegen, deren
   jüngste Generation (spätester Verkaufsstart ohne Nachfolger), die Laufzeit mit den meisten abgeschlossenen
   Kreisläufen, als Vergleich die nächste längere Laufzeit (bei der längsten die nächste kürzere).
   Kennzeichen: eine Zahl in Klammern liegt außerhalb der Belegspanne der Kurve (verlängert); „nicht belegt" steht, wo
   es keine Kurve gibt oder eine unsichere Kurve verlängert werden müsste. Kein DOM, kein Zustand außer einem Index je
   Datenobjekt, keine getippte Zahl: jede Zahl kommt aus D und geht durch E.fmt. */
(function (w) {
  'use strict';
  var E = w.RE, CACHE = new WeakMap();

  /* Ersatzpalette (helle Tokenfarben aus styles.css), solange die Hülle noch keine Palette gelesen hat */
  var P0 = {
    font: '"Source Serif 4", serif', ink: '#201e1d', muted: '#7d7979', grid: '#d7d3d3', line: '#bab6b6',
    accent: '#0088b0', accent700: '#006786', accent2: '#d6006c', oem: {}
  };

  var TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' };
  var TAG_SIM = { cls: 'tag-neutral', text: 'simuliert' };
  var TAG_DER = { cls: 'tag-neutral', text: 'abgeleitet' };
  var FIT = { ok: 'belastbar', thin: 'unsicher, wenige Belege', no_fit: 'keine Kurve' };
  var RANK = { ok: 0, thin: 1, extrap: 2, none: 3 };

  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function uniq(list) { var seen = {}, out = []; list.forEach(function (x) { if (x !== undefined && x !== null && !seen[x]) { seen[x] = 1; out.push(x); } }); return out; }
  function byName(a, b) { return String(a).localeCompare(String(b), 'de'); }
  function opt(v) { return { value: v, label: v }; }
  function worst(a, b) { return RANK[a] >= RANK[b] ? a : b; }

  /* ---------- Index je Datenobjekt ---------- */
  function index(D) {
    var x = CACHE.get(D); if (x) return x;
    var list = D.series, bySlug = {}, byKey = {}, oemsByFam = {};
    list.forEach(function (s) {
      byKey[s.key] = s;
      (s.models || []).forEach(function (m) { bySlug[m.slug] = s; });
      oemsByFam[s.family] = (oemsByFam[s.family] || []).concat([s.oem]);
    });
    Object.keys(oemsByFam).forEach(function (k) { oemsByFam[k] = uniq(oemsByFam[k]).sort(byName); });
    x = { list: list, bySlug: bySlug, byKey: byKey, fams: uniq(list.map(function (s) { return s.family; })).sort(byName), oemsByFam: oemsByFam };
    CACHE.set(D, x); return x;
  }
  function nextTerm(terms, t) { var i = terms.indexOf(t); return i < terms.length - 1 ? terms[i + 1] : terms[i - 1]; }

  w.RE.term = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt, pal = P || P0;
    if (!D || !Array.isArray(D.series) || !D.series.length || !Array.isArray(D.terms) || D.terms.length < 2 || !D.default) {
      throw new Error('term.json unbrauchbar: series, terms oder default fehlen');
    }
    var X = index(D), terms = D.terms, factors = D.factors || {}, fam = Array.isArray(D.fam) ? D.fam : [];
    var select = typeof opts.select === 'function' ? opts.select : function () {};

    /* ---------- Auswahl: Serie und Modell, Laufzeit und Vergleichslaufzeit ---------- */
    var s = (opts.slug && X.bySlug[opts.slug]) || X.byKey[D.default.series] || X.list[0];
    var m = (opts.slug ? s.models.filter(function (z) { return z.slug === opts.slug; })[0] : null)
      || s.models.filter(function (z) { return z.slug === s.default_slug; })[0] || s.models[0];
    var t1 = terms.indexOf(Number(opts.t1)) >= 0 ? Number(opts.t1) : (terms.indexOf(D.default.term) >= 0 ? D.default.term : terms[0]);
    var t2 = terms.indexOf(Number(opts.t2)) >= 0 && Number(opts.t2) !== t1 ? Number(opts.t2) : nextTerm(terms, t1);
    var rows = s.rows, rowOf = function (t) { return rows.filter(function (r) { return r.t === t; })[0]; };
    var r1 = rowOf(t1), r2 = rowOf(t2);
    if (!r1 || !r2) throw new Error('term.json unbrauchbar: Zeile für ' + t1 + ' oder ' + t2 + ' Monate fehlt');
    var T1 = f.qty(t1), T2 = f.qty(t2), tS = Math.min(t1, t2), tL = Math.max(t1, t2), TS = f.qty(tS), TL = f.qty(tL);
    var termsAll = terms.map(function (t) { return f.qty(t); });
    var termsUnd = termsAll.slice(0, -1).join(', ') + ' und ' + termsAll[termsAll.length - 1];

    var pickers = {
      fams: X.fams.map(opt), oems: (X.oemsByFam[s.family] || []).map(opt),
      models: s.models.map(function (z) { return { value: z.slug, label: z.model }; })
    };
    /* die Serie mit den meisten Preisbelegen in der Geräteart (oder die des Herstellers), ihr Standardmodell */
    var firstOf = function (family, oem) {
      var hit = X.list.filter(function (z) { return (!family || z.family === family) && (!oem || z.oem === oem); });
      if (!hit.length) return null;
      hit = hit.slice().sort(function (a, b) { return (b.ask.n || 0) - (a.ask.n || 0) || byName(a.key, b.key); });
      return hit[0].default_slug;
    };
    var termOpts = terms.map(function (t) { return { label: f.qty(t) + ' Monate', value: t, checked: t === t1, select: function () { select({ t1: t }); } }; });
    var term2Opts = terms.filter(function (t) { return t !== t1; }).map(function (t) { return { label: f.qty(t) + ' Monate', value: t, checked: t === t2, select: function () { select({ t2: t }); } }; });

    /* ---------- Belege, Kurve, Kennzeichen ---------- */
    var ask = s.ask, bid = s.bid, fc = s.fam_curve;
    var noFit = !ask || ask.fit === 'no_fit' || !isNum(ask.slope), thin = !noFit && ask.fit === 'thin';
    var hasAge = ask && isNum(ask.age_min) && isNum(ask.age_max);
    var span = hasAge ? f.num(ask.age_min, 1) + ' bis ' + f.num(ask.age_max, 1) + ' Monate' : '';
    var belege = f.qty(ask ? ask.n : 0) + ' Preisbelege';
    var kurve = 'Kurve ' + s.key;
    var bezug = 'Marktplatz-Angebote laut ' + kurve + ' (' + belege + (span ? ', Alter ' + span : '') + ')';
    var under = hasAge ? f.qty(Math.floor(ask.age_min)) : '', over = hasAge ? f.qty(Math.ceil(ask.age_max)) : '';
    var stat = function (r) { if (noFit || !isNum(r.q)) return 'none'; if (r.extrap) return thin ? 'none' : 'extrap'; return thin ? 'thin' : 'ok'; };
    var statY = function (r) { if (noFit || !isNum(r.year_pm)) return 'none'; if (r.year_extrap) return thin ? 'none' : 'extrap'; return thin ? 'thin' : 'ok'; };
    var why = function (r) {
      if (noFit) return 'nicht belegt: keine Kurve für ' + s.key + ' (' + belege + (span ? ', Alter ' + span : '') + ')';
      if (r.extrap) return (thin ? 'nicht belegt: unsichere Kurve (' + belege + '), ' : 'in Klammern: Kurve verlängert, ')
        + (r.t < ask.age_min ? 'kein Beleg unter ' + under + ' Monaten' : 'kein Beleg über ' + over + ' Monaten');
      if (thin) return 'unsicher: nur ' + belege + ' (' + FIT.thin + ')';
      return '';
    };
    var mark = function (text, st) { return st === 'none' ? 'nicht belegt' : (st === 'extrap' ? '(' + text + ')' : text); };
    var pv = function (x, st, d) { return mark(d ? f.pct1(x) : f.pct(x), st); };
    var pb = function (x, d, extrap) { var t = d ? f.pct1(x) : f.pct(x); return t ? (extrap ? '(' + t + ')' : t) : ''; };   /* Tabellen: in Klammern außerhalb der Belegspanne */
    var n2 = function (x) { return f.num(x, 2); };
    var fmtFactorLoose = function (x) {   /* der Platzhalter-Faktor so kurz wie in config/lake.yaml: "1,5", "1", "0,85" */
      if (!isNum(x)) return '';
      if (Math.round(x) === x) return f.num(x, 0);
      if (Math.round(x * 10) === x * 10) return f.num(x, 1);
      return f.num(x, 2);
    };
    var yr = function (t) { return f.qty(Math.round(t / terms[0])); };   /* Jahresnummer der Laufzeit */
    var disc = f.pct1(D.disc);
    var stud = s.studies || {}, st1 = stud[String(t1)], st2 = stud[String(t2)];
    var jahre = Array.isArray(s.jahre) ? s.jahre : [];
    var studLine = function (st, t) { return st ? 'laut Studien USA und Großbritannien nach ' + f.qty(t) + ' Monaten: ' + f.pct(st.lo_q) + ' bis ' + f.pct(st.hi_q) + ' der UVP (Ankauf-Gebote, ' + f.qty(st.n) + ' Quellen)' : ''; };
    var own = (s.points || []).filter(function (p) { return p.slug === m.slug; });
    var ownLine = own.length ? f.qty(own.length) + ' Preisbelege für ' + m.model + ' selbst, im Diagramm hervorgehoben' : 'kein eigener Preisbeleg für ' + m.model + '; die Kurve gilt für die Serie ' + s.key;
    var simDisc = isNum(D.sim_discount_min) && isNum(D.sim_discount_max) && s.fam === 'iphone_like' ? f.pct(D.sim_discount_min) + ' bis ' + f.pct(D.sim_discount_max) + ', ' : '';
    var famBucket = 'Geräteart ' + s.fam_label + ' der Simulation';
    var hasCost = isNum(r1.cost_pm) && isNum(r2.cost_pm);
    var placeholder = isNum(factors[String(t1)]) && isNum(factors[String(t2)]) && factors[String(t2)] ? factors[String(t1)] / factors[String(t2)] : null;

    /* ---------- Kopf ---------- */
    var kicker = 'Analyse, öffentliche Preisbelege und Simulation, Stand ' + f.de(D.today);
    var subject = 'Laufzeit gegen Restwert: ' + m.model + ', ' + T1 + ' gegen ' + T2 + ' Monate';
    var intro = 'Gerät und Laufzeit oben wählen, daneben die Vergleichslaufzeit; Kacheln, Kurve und „Was man daraus macht" rechnen mit der Auswahl. '
      + 'Die Kurve ist die der Serie (Geräteart und Hersteller, hier ' + s.key + '), weil nur auf dieser Ebene eine Alterskurve mit Belegen vorliegt; '
      + 'das Modell wählt die Beschriftung und hebt seine eigenen Belege im Diagramm hervor. Vorbelegt ist die Serie mit den meisten Preisbelegen und ihre jüngste Generation (spätester Verkaufsstart ohne Nachfolger im Katalog). '
      + 'Öffentliche Preisbelege: ' + f.qty(ask ? ask.n : 0) + ' Marktplatz-Angebote' + (bid ? ' und ' + f.qty(bid.n) + ' Ankauf-Gebote' : '') + ' für ' + s.key + '; '
      + 'simuliert: ' + f.qty(s.sim_n) + ' abgeschlossene Kreisläufe der ' + famBucket + '.';

    /* ---------- Kennzahlen (die vier Kacheln) ---------- */
    var rvTile = function (r, t, st) {
      var stx = stat(r);
      return {
        label: m.model + ' nach ' + f.qty(t) + ' Monaten',
        value: pv(r.q, stx),
        lines: ['der UVP, ' + bezug, why(r), ownLine,
          isNum(r.qbid) && bid ? 'Ankauf-Gebot „bis zu“ laut Kurve: ' + f.pct(r.qbid) + ' der UVP (' + f.qty(bid.n) + ' Preisbelege' + (bid.fit === 'thin' ? ', ' + FIT.thin : '') + ')' : (bid && !noFit ? 'Ankauf-Kurve ohne Beleg bei ' + f.qty(t) + ' Monaten' : ''),
          studLine(st, t)],
        tags: [TAG_PUB]
      };
    };
    var sy1 = statY(r1), sy2 = statY(r2);
    var yearMax = rows.filter(function (r) { return statY(r) !== 'none'; }).sort(function (a, b) { return b.year_pm - a.year_pm; })[0];
    var stF = hasCost ? worst(stat(r1), stat(r2)) : 'none';
    var kpis = [
      rvTile(r1, t1, st1),
      rvTile(r2, t2, st2),
      {
        label: 'Wertverlust je Monat, Jahr ' + yr(t1) + ' gegen Jahr ' + yr(t2),
        value: pv(r1.year_pm, sy1, 1) + ' gegen ' + pv(r2.year_pm, sy2, 1),
        lines: ['der UVP, nur der Verlust des jeweiligen Jahres, beide nach der ' + kurve,
          sy1 === 'extrap' || sy2 === 'extrap' ? 'in Klammern: das Jahr beginnt oder endet außerhalb der Belege (' + span + ')' : (sy1 === 'none' || sy2 === 'none' ? why(sy1 === 'none' ? r1 : r2) : ''),
          yearMax ? 'das teuerste Jahr laut Kurve ist Jahr ' + yr(yearMax.t) + ' mit ' + pv(yearMax.year_pm, statY(yearMax), 1) + ' der UVP je Monat' : '',
          stud[String(terms[0])] ? 'Jahr ' + yr(terms[0]) + ' laut Studien: ' + f.pct1((D.buy_share - stud[String(terms[0])].hi_q) / terms[0]) + ' bis ' + f.pct1((D.buy_share - stud[String(terms[0])].lo_q) / terms[0]) + ' der UVP je Monat' : ''],
        tags: [TAG_PUB]
      },
      {
        label: 'Nötige Miete, ' + T1 + ' gegen ' + T2 + ' Monate',
        value: stF === 'none' ? 'nicht belegt' : 'Faktor ' + mark(n2(r1.factor[String(t2)]), stF),
        lines: [(stF === 'none' ? '' : mark(n2(r1.factor[String(t2)]), stF) + ' ') + 'nach der ' + kurve + ' und den Kosten bis Verkauf der ' + famBucket,
          hasCost ? (stF === 'none' ? why(stat(r1) === 'none' ? r1 : r2) : (stF === 'extrap' ? 'in Klammern: eine der beiden Laufzeiten liegt außerhalb der Belege (' + span + ')' : why(r1) || why(r2))) : 'nicht belegt: keine Kosten bis Verkauf für die ' + famBucket,
          st1 && isNum(st1.factor_lo[String(t2)]) ? n2(st1.factor_lo[String(t2)]) + ' bis ' + n2(st1.factor_hi[String(t2)]) + ' nach den Studien (Ankauf-Gebote USA und Großbritannien, nach ' + T1 + ' Monaten)' : '',
          isNum(placeholder) ? 'Platzhalter der Simulation: ' + n2(placeholder) + ' (term_rate_factor ' + fmtFactorLoose(factors[String(t1)]) + ' gegen ' + fmtFactorLoose(factors[String(t2)]) + ')' : ''],
        tags: [TAG_DER]
      }
    ];
    var kpiDefs = [
      { k: 'UVP', v: 'Unverbindliche Preisempfehlung des Herstellers beim deutschen Verkaufsstart; die Kurve rechnet einschließlich Mehrwertsteuer, Kosten und Einkaufspreis der Simulation ohne Mehrwertsteuer.' },
      { k: 'Faktor', v: 'nötige Miete je Monat der gewählten Laufzeit (' + T1 + ' Monate) geteilt durch die der Vergleichslaufzeit (' + T2 + ' Monate); so viel teurer oder günstiger je Monat muss der Vertrag sein.' },
      { k: 'in Klammern', v: 'die Kurve ist an dieser Stelle über ihre Belege hinaus verlängert (Belege der ' + kurve + (span ? ': ' + span : '') + '); eine Größenordnung, kein Beleg.' },
      { k: 'nicht belegt', v: 'für die Serie gibt es keine Kurve, oder eine unsichere Kurve (wenige Belege) müsste verlängert werden; dann steht keine Zahl.' }
    ];
    var calcnote = 'Kacheln: Prozent der UVP nach der ' + kurve + ' (Marktplatz-Angebote, Obergrenze, abgelesen für Zustandsstufe B), Kosten bis Verkauf aus der ' + famBucket + '. '
      + (hasAge ? 'Die Belege reichen von ' + span + 'n; außerhalb steht die Zahl in Klammern (verlängert), unsichere Kurven werden nicht verlängert. ' : '')
      + 'Der Faktor sagt, um wie viel teurer je Monat ein ' + T1 + '-Monats-Vertrag gegenüber einem ' + T2 + '-Monats-Vertrag sein muss, damit Wertverlust und Kosten bis Verkauf gedeckt sind'
      + (st1 ? '; die Studienwerte setzen ein Gebot gegen ein Angebot und liegen deshalb zu hoch.' : '.');

    /* ---------- Was man daraus macht ---------- */
    var fr = function (t) { var r = rowOf(t); var st = hasCost ? worst(stat(r), stat(r2)) : 'none'; return f.qty(t) + ': ' + (st === 'none' ? 'nicht belegt' : mark(n2(r.factor[String(t2)]), st)); };
    var lap = fam.filter(function (x) { return x.family === 'Laptop'; })[0], sm = fam.filter(function (x) { return x.family === 'Smartphone'; })[0];
    var tF = terms.filter(function (t) { return lap && sm && lap.extrap && sm.extrap && !lap.extrap[String(t)] && !sm.extrap[String(t)]; });
    var tFam = tF.indexOf(t1) >= 0 ? t1 : tF[tF.length - 1];
    var rS = rowOf(tS);
    var actions = [
      { lead: 'Die Miete je Laufzeit aus der Kurve ableiten, nicht flach setzen.',
        text: 'Ein ' + T1 + '-Monats-Vertrag mit ' + m.model + ' muss je Monat ' + (stF === 'none' ? 'einen Faktor der ' + T2 + '-Monats-Miete kosten, der für ' + s.key + ' nicht belegt ist' : 'den Faktor ' + mark(n2(r1.factor[String(t2)]), stF) + (st1 && isNum(st1.factor_hi[String(t2)]) ? ' (Kurve) bis ' + n2(st1.factor_hi[String(t2)]) + ' (Studien)' : '') + ' der ' + T2 + '-Monats-Miete kosten')
          + '; alle Laufzeiten gegen ' + T2 + ' Monate: ' + terms.map(fr).join(', ') + '. '
          + 'Wer alle Laufzeiten gleich bepreist, verschenkt bei kurzen Verträgen Geld und verliert bei langen den Kunden an den Wettbewerb. '
          + 'Das ist die Zahl, die die Stellschraube Laufzeit (Kennung L07 auf dem Reiter Stellschrauben) misst; mit dieser Preisregel wäre ihr Hebel null.' },
      { lead: (yearMax ? 'Das teure Jahr ist Jahr ' + yr(yearMax.t) + '.' : 'Das teure Jahr ist für diese Serie nicht belegt.'),
        text: 'Der Wertverlust je Monat liegt bei ' + s.key + ' in Jahr ' + yr(t1) + ' bei ' + pv(r1.year_pm, sy1, 1) + ' der UVP, in Jahr ' + yr(t2) + ' bei ' + pv(r2.year_pm, sy2, 1)
          + (stud[String(terms[0])] ? '; die Studien sagen für Jahr ' + yr(terms[0]) + ' ' + f.pct1((D.buy_share - stud[String(terms[0])].hi_q) / terms[0]) + ' bis ' + f.pct1((D.buy_share - stud[String(terms[0])].lo_q) / terms[0]) + ' (Ankauf-Gebote USA und Großbritannien)' : '') + '. '
          + (hasAge && ask.age_min > terms[0] ? 'Unter ' + under + ' Monaten zeigt die deutsche Kurve keinen Beleg; was dort steht, ist verlängert. ' : '')
          + 'Ein Gerät, das ' + TL + ' Monate läuft, überspringt mehr Generationen als eines mit ' + TS + ' Monaten, aber den größten Sprung zahlt jede Laufzeit. '
          + 'Reparatur und Lagertage stecken in den Kosten bis Verkauf und wachsen mit der Laufzeit; was nicht drin ist, sind Akkuzustand und Kundenzufriedenheit bei alten Geräten.' },
      { lead: 'Zweiter Kreislauf statt Verkauf nach ' + TS + ' Monaten.',
        text: 'Ein ' + m.model + ', das nach ' + TS + ' Monaten mit ' + pv(rS.q, stat(rS)) + ' der UVP zurückkommt' + (stud[String(tS)] ? ' (Studien: ' + f.pct(stud[String(tS)].lo_q) + ' bis ' + f.pct(stud[String(tS)].hi_q) + ')' : '')
          + ', ist als Gebrauchtgerät noch vermietbar; ein zweiter Vertrag über ' + TL + ' Monate zu einer niedrigeren Miete spart den zweiten Einkauf und die zweite Kanalgebühr. '
          + 'Die Simulation verkauft heute nach jedem Vertrag; das ist der Kandidat für die nächste Ausbaustufe (v0.3).' },
      { lead: 'Laufzeit nach Geräteart wählen.',
        text: (lap && sm && tFam ? 'Ein Laptop verliert bei ' + f.qty(tFam) + ' Monaten ' + f.pct1(lap.loss_pm[String(tFam)]) + ' der UVP je Monat, ein Smartphone ' + f.pct1(sm.loss_pm[String(tFam)]) + ' (Familienkurven über alle Hersteller). ' : '')
          + 'Lange Laufzeiten gehören zu Laptops und Tablets, bei Smartphones ist die Laufzeit mit den meisten Kreisläufen die Mitte; die kurze nur mit der hohen Miete aus der Kurve (erster Punkt) oder mit dem zweiten Kreislauf (dritter Punkt).' },
      { lead: 'Vorzeitige Rückgabe bepreisen.',
        text: 'In der Simulation verliert jeder vorzeitig beendete Vertrag Geld, egal welche Laufzeit (Tabelle unten, Spalte „Lifecycle-Marge, vorzeitig zurück“), '
          + 'weil die Miete aufhört, das Gerät aber schon den Wertverlust der ersten Monate getragen hat und die Kosten bis Verkauf voll anfallen. '
          + 'Der Vertrag braucht einen Ausgleich bei vorzeitiger Rückgabe, mindestens die entgangene Miete bis zum Punkt, ab dem das Gerät seine Kosten eingespielt hat (Reiter Gerät). '
          + 'Die Simulation kennt diesen Ausgleich noch nicht; er ist eine Zeile im Kundenvertrag und ein Kandidat für die nächste Ausbaustufe (v0.3).' },
      { lead: 'Verkaufszeitpunkt vor dem Nachfolger.',
        text: 'Die Kurve mittelt den Sprung beim Erscheinen der nächsten Generation; ein Vertrag, der einen Monat vor dem Nachfolger endet, gibt das Gerät in den teureren Markt zurück. '
          + (m.successor ? 'Für ' + m.model + ' nennt der Katalog als Nachfolger ' + m.successor + (m.successor_date ? ' ab ' + f.de(m.successor_date) : '') + '. ' : 'Für ' + m.model + ' ist im Katalog kein Nachfolger eingetragen (Verkaufsstart ' + f.de(m.launch) + ', heute ' + f.num(m.age) + ' Monate alt). ')
          + 'Das lässt sich mit den Verkaufsstartdaten des Katalogs planen, braucht aber erst einen Beleg mit Datum (offen).' }
    ];

    /* ---------- Diagramm: Serienkurve mit Belegen, Familienkurve grau, die beiden Laufzeiten markiert ---------- */
    var traces = [];
    var curveTrace = function (c, name, color, dash, width) {
      if (!c || !isNum(c.slope) || !isNum(c.intercept) || !isNum(c.age_min)) return;
      var xs = [], ys = [];
      for (var mo = Math.floor(c.age_min); mo <= Math.ceil(c.age_max); mo++) { xs.push(mo); ys.push(Math.min(1, Math.exp(c.intercept + c.slope * mo))); }
      traces.push({ type: 'scatter', mode: 'lines', name: name, x: xs, y: ys, line: { color: color, dash: dash, width: width }, hoverinfo: 'skip' });
    };
    if (fc) curveTrace(fc, 'Kurve ' + s.family + ', alle Hersteller, ' + f.qty(fc.n) + ' Preisbelege (Vergleich)', pal.muted, 'dash', 1.5);
    if (!noFit) curveTrace(ask, kurve + ', Marktplatz-Angebote, ' + belege + ' (Obergrenze)', pal.ink, 'solid', 2);
    if (bid) curveTrace(bid, kurve + ', Ankauf-Gebote „bis zu“, ' + f.qty(bid.n) + ' Preisbelege (Ankaufsseite, Höchstwerte)', pal.ink, 'dot', 2);
    var pts = s.points || [];
    var others = pts.filter(function (p) { return p.slug !== m.slug; });
    var marks = function (list, sym, color, size, name) {
      if (!list.length) return;
      traces.push({
        type: 'scatter', mode: 'markers', name: name,
        x: list.map(function (p) { return p.age; }), y: list.map(function (p) { return p.q; }),
        marker: { symbol: sym, size: size, color: color, opacity: 0.85 },
        text: list.map(function (p) { return p.model + ' ' + p.spec + '<br>Monate ' + f.num(p.age, 1) + ', Realisierung ' + f.pct(p.q); }),
        hovertemplate: '%{text}<extra></extra>'
      });
    };
    var askO = others.filter(function (p) { return p.kind === 'ask'; }), bidO = others.filter(function (p) { return p.kind === 'bid'; });
    marks(askO, 'diamond', pal.accent, 8, 'Marktplatz-Angebote Zustandsstufe B, ' + s.key + ', ' + f.qty(askO.length) + ' Preisbelege');
    marks(bidO, 'triangle-down', pal.accent2, 8, 'Ankauf-Gebote „bis zu“, ' + s.key + ', ' + f.qty(bidO.length) + ' Preisbelege');
    marks(own, 'diamond', pal.accent700, 13, 'Preisbelege ' + m.model + ', ' + f.qty(own.length));
    var ages = pts.map(function (p) { return p.age; }).concat([ask ? ask.age_max : null, bid ? bid.age_max : null, fc ? fc.age_max : null, tL]).filter(isNum);
    var xmax = Math.ceil(Math.max.apply(null, ages)) + 2;
    var ticks = [0, 0.2, 0.4, 0.6, 0.8, 1];
    var layout = {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: pal.font, color: pal.ink, size: 13 },
      xaxis: { title: { text: 'Monate seit deutschem Verkaufsstart des Modells' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: false, range: [0, xmax] },
      yaxis: { title: { text: 'Realisierung in % der UVP' }, tickvals: ticks, ticktext: ticks.map(function (t) { return f.pct(t); }), range: [0, 1.05], gridcolor: pal.grid, linecolor: pal.line, zeroline: false },
      legend: { orientation: 'h', y: 1.02, yanchor: 'bottom', x: 0, xanchor: 'left', font: { color: pal.ink } },
      hoverlabel: { font: { family: pal.font } },
      shapes: [
        { type: 'line', x0: t1, x1: t1, y0: 0, y1: 1, yref: 'paper', line: { color: pal.accent, dash: 'solid', width: 2 } },
        { type: 'line', x0: t2, x1: t2, y0: 0, y1: 1, yref: 'paper', line: { color: pal.muted, dash: 'dash', width: 1 } }
      ],
      annotations: [
        { x: t1, y: 0, yref: 'paper', text: 'gewählt: ' + T1 + ' Monate', showarrow: false, yanchor: 'bottom', xanchor: 'left', xshift: 4, font: { size: 11, color: pal.accent700 } },
        { x: t2, y: 0.06, yref: 'paper', text: 'Vergleich: ' + T2 + ' Monate', showarrow: false, yanchor: 'bottom', xanchor: 'left', xshift: 4, font: { size: 11, color: pal.muted } }
      ]
    };
    var chartNote = 'Die ' + kurve + ' mit den Belegen als Punkten und den beiden Laufzeiten als Linien (öffentlich). Durchgezogen: Marktplatz-Angebote' + (bid ? ', gepunktet: Ankauf-Gebote „bis zu“ (Höchstwerte vor der Zustandsprüfung, kein Boden)' : '')
      + ', jeweils nur über der Altersspanne ihrer Belege; grau gestrichelt: die Familienkurve ' + s.family + ' über alle Hersteller. '
      + 'Punkte: die Preisbelege der Serie (Raute Zustandsstufe B, Dreieck Ankauf-Gebot), große Raute: die Belege für ' + m.model + ' selbst. Senkrecht: ' + T1 + ' Monate (gewählt) und ' + T2 + ' Monate (Vergleich). '
      + (hasAge && ask.age_min > terms[0] ? 'Links von ' + under + ' Monaten gibt es keinen deutschen Beleg für ' + s.key + '.' : '');

    /* ---------- Tabelle 1: Was die Serie nach den vier Laufzeiten noch wert ist ---------- */
    var hasStudies = jahre.length > 0;
    var rvRows = rows.map(function (r) {
      var studies = jahre.filter(function (j) { return j.monate === r.t; }).map(function (j) { return { href: j.url, text: j.quelle, rest: j.datum + ': ' + j.modelle }; });
      var cells = [
        E.C(f.qty(r.t)),
        E.N(noFit ? 'nicht belegt' : pb(r.q, 0, r.extrap)),
        E.N(r.raw ? f.pct(r.raw.med) + ' (' + f.qty(r.raw.qty) + ' Preisbelege)' : 'kein Beleg'),
        E.N(isNum(r.qbid) ? pb(r.qbid, 0, r.extrap_bid) : ''),
        E.N(r.rawbid ? f.pct(r.rawbid.med) + ' (' + f.qty(r.rawbid.qty) + ' Preisbelege)' : 'kein Beleg')
      ];
      if (hasStudies) cells.push(E.C('', { links: studies, minW: studies.length ? 240 : 0 }));
      cells.push(E.N(noFit ? '' : pb(r.loss, 0, r.extrap)), E.N(noFit ? '' : pb(r.loss_pm, 1, r.extrap)), E.N(noFit ? '' : pb(r.year_pm, 1, r.year_extrap)));
      return E.ROW(cells, { bold: r.t === t1 || r.t === t2 });
    });
    var rvCols = [E.H('Monate'), E.H('Marktplatz-Angebot, Kurve', 1), E.H('Preisbelege, mittleres Angebot', 1), E.H('Ankauf-Gebot, Kurve', 1), E.H('Ankauf-Gebote, Belege', 1)];
    if (hasStudies) rvCols.push(E.H('Studien USA und Großbritannien'));
    rvCols.push(E.H('Wertverlust seit Kauf', 1), E.H('je Monat seit Kauf', 1), E.H('je Monat, dieses Jahr', 1));
    var rvDefs = [
      { k: 'Monate', v: 'Modellalter seit deutschem Verkaufsstart; ein Gerät, das am Verkaufsstart gekauft und ' + T1 + ' Monate vermietet wird, ist bei Rückgabe ' + T1 + ' Monate alt; fett die gewählte Laufzeit und die Vergleichslaufzeit.' },
      { k: 'Marktplatz-Angebot, Kurve', v: 'Realisierung in Prozent der UVP laut ' + kurve + ' vom Reiter Realisierung (alle Stufen, abgelesen für Zustandsstufe B); Obergrenze, weil die Marge des Aufbereiters darin steckt; in Klammern, wo die Kurve über die Belege hinaus verlängert ist; Verlässlichkeit der Kurve: ' + (FIT[ask ? ask.fit : 'no_fit'] || '') + '.' },
      { k: 'Preisbelege, mittleres Angebot', v: 'das mittlere Marktplatz-Angebot der deutschen Belege der Serie in Zustandsstufe B, deren Alter in dieses Jahr fällt (Jahr ' + yr(terms[0]) + ' = ' + f.qty(0) + ' bis ' + f.qty(terms[0] - 1) + ' Monate); die Spalte zählt sie.' },
      { k: 'Ankauf-Gebot, Kurve', v: 'die Ankaufsseite laut Kurve der Ankauf-Gebote der Serie; leer, wo die Kurve keine Belege hat. Achtung: die deutschen Ankauf-Belege sind Höchstwerte „bis zu“ vor der Zustandsprüfung (Bestzustand), also kein Boden; der tatsächliche Ankaufpreis liegt darunter.' },
      { k: 'Ankauf-Gebote, Belege', v: 'das mittlere Ankauf-Gebot „bis zu“ der deutschen Belege, deren Alter in dieses Jahr fällt; die Spalte zählt sie.' }
    ];
    if (hasStudies) rvDefs.push({ k: 'Studien USA und Großbritannien', v: 'Realisierung nach Ankauf-Gebot laut veröffentlichten Studien in den USA und in Großbritannien (' + f.pct(1) + ' minus dort genannter Verlust), mit Quelle; andere Länder, andere Preisart als die Marktplatz-Kurve, deshalb nur als Bandbreite dort, wo deutsche Belege fehlen; die Tabelle der Studien führt sie einzeln auf.' });
    rvDefs.push(
      { k: 'Wertverlust seit Kauf', v: 'Einkaufspreis minus Marktplatz-Angebot, in Prozent der UVP; der Einkaufspreis ist als UVP minus ' + disc + ' angenommen (Platzhalter mit verantwortlicher Rolle).' },
      { k: 'je Monat seit Kauf', v: 'Wertverlust seit Kauf geteilt durch alle Monate seit Kauf.' },
      { k: 'je Monat, dieses Jahr', v: 'nur der Wertverlust dieses einen Jahres (Kurvenwert am Jahresanfang minus am Jahresende), geteilt durch ' + f.qty(terms[0]) + '; zeigt, welches Jahr das teure ist; in Klammern, wenn Anfang oder Ende außerhalb der Belege liegt.' }
    );
    var tRv = E.TABLE('x-rv', 'Was ' + s.key + ' nach ' + termsUnd + ' Monaten noch wert ist', rvCols, rvRows, { n: 0, tags: [TAG_PUB], defs: rvDefs });

    /* ---------- Tabelle 2: die Studien einzeln (nur, wo die Serie welche hat) ---------- */
    var tStudies = null;
    if (hasStudies) {
      var stRows = jahre.map(function (j) {
        return E.ROW([E.C('', { href: j.url, linkText: j.quelle }), E.C(j.datum, { nowrap: true }), E.C(j.markt), E.N(f.qty(j.monate)), E.C(j.modelle, { minW: 240 })]);
      });
      tStudies = E.TABLE('x-studies', 'Studien USA und Großbritannien, Ankauf-Gebote, ' + s.key, [E.H('Quelle'), E.H('Datum'), E.H('Markt'), E.H('Monate', 1), E.H('Modelle')], stRows, {
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
    }

    /* ---------- Tabelle 3: Was jeder Vertragsmonat kosten muss ---------- */
    var needRows = rows.map(function (r) {
      var st = hasCost && isNum(r.need_pm) ? worst(stat(r), stat(r2)) : 'none';
      return E.ROW([
        E.C(f.qty(r.t) + ' Monate', { nowrap: true }),
        E.N(noFit ? 'nicht belegt' : pb(r.loss, 0, r.extrap)),
        E.N(noFit ? '' : pb(r.loss_pm, 1, r.extrap)),
        E.N(f.pct1(r.cost_pm)),
        E.N(noFit ? '' : pb(r.need_pm, 1, r.extrap)),
        E.N(st === 'none' ? (noFit ? '' : 'nicht belegt') : mark(n2(r.factor[String(t2)]), st)),
        E.N(n2(r.factor_sim))
      ], { bold: r.t === t1 || r.t === t2 });
    });
    var tNeed = E.TABLE('x-need', 'Was jeder Vertragsmonat kosten muss, damit nichts fehlt', [
      E.H('Laufzeit'), E.H('Wertverlust seit Kauf', 1), E.H('je Monat', 1), E.H('Kosten bis Verkauf, monatlich', 1), E.H('Nötige Miete je Monat', 1),
      E.H('Faktor gegen ' + T2 + ' Monate', 1), E.H('Faktor in der Simulation', 1)
    ], needRows, {
      n: 0,
      tags: [TAG_PUB, TAG_SIM, TAG_DER],
      note: 'Die Miete eines Vertrags muss zwei Dinge decken: den Wertverlust des Geräts über die Laufzeit (' + kurve + ', öffentlich) und die Kosten bis Verkauf (simuliert, je Laufzeit aus den abgeschlossenen Kreisläufen der ' + famBucket + '). '
        + 'Ein Teil davon fällt je Gerät an, egal wie lang der Vertrag war (Einrichtung, Versand, Rücksendung, Datenlöschung, Aufbereitung, Kanalgebühren), ein Teil wächst mit der Laufzeit (Reparatur, Lagertage). '
        + 'Beides geteilt durch die Monate ergibt die Miete je Monat, bei der die Lifecycle-Marge null ist. '
        + 'Alles in Prozent der UVP; Kosten und Einkaufspreis ohne Mehrwertsteuer gegen die UVP ohne Mehrwertsteuer, die Kurve auf beiden Seiten einschließlich Mehrwertsteuer.',
      foot: st1 ? T1 + ' Monate nach den Studien statt nach der Kurve: Realisierung ' + f.pct(st1.lo_q) + ' bis ' + f.pct(st1.hi_q) + ' (Ankauf-Gebote USA und Großbritannien), '
        + 'nötige Miete ' + f.pct1(st1.need_lo) + ' bis ' + f.pct1(st1.need_hi) + ' je Monat, Faktor ' + n2(st1.factor_lo[String(t2)]) + ' bis ' + n2(st1.factor_hi[String(t2)]) + ' gegen ' + T2 + ' Monate; '
        + 'die Spanne mischt ein Gebot im Zähler mit dem Angebot im Nenner und liegt deshalb zu hoch.' : '',
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit in Monaten; das Gerät ist bei Rückgabe so viele Monate alt; fett die gewählte Laufzeit und die Vergleichslaufzeit.' },
        { k: 'Wertverlust seit Kauf', v: 'Einkaufspreis minus Marktplatz-Angebot laut Kurve am Ende der Laufzeit, in Prozent der UVP; in Klammern außerhalb der Belegspanne.' },
        { k: 'je Monat', v: 'Wertverlust seit Kauf geteilt durch die Monate der Laufzeit.' },
        { k: 'Kosten bis Verkauf, monatlich', v: 'simuliert, dieselbe Zahl wie in der Tabelle der Simulation: im Mittel ' + f.eur(s.sim_cost) + ' je abgeschlossenem Kreislauf der ' + famBucket + ' bei ' + f.eur(s.sim_rrp_net) + ' UVP ohne Mehrwertsteuer, also ' + f.pct(s.cost_share) + ' der UVP; je Laufzeit der eigene Wert, geteilt durch die Monate.' },
        { k: 'Nötige Miete je Monat', v: '(Wertverlust seit Kauf plus Kosten bis Verkauf) geteilt durch die Monate; ohne Gewinn, ohne Finanzierung, Gemeinkosten, Steuern; Nutzerbetreuung und Geräteverwaltung stecken als Umlage in den Kosten bis Verkauf.' },
        { k: 'Faktor gegen ' + T2 + ' Monate', v: 'nötige Miete je Monat dieser Laufzeit geteilt durch die der Vergleichslaufzeit (' + T2 + ' Monate), abgeleitet aus Kurve und Kosten; in Klammern, wenn eine der beiden Laufzeiten außerhalb der Belege liegt; nicht belegt bei unsicherer oder fehlender Kurve.' },
        { k: 'Faktor in der Simulation', v: 'der Platzhalter (term_rate_factor in config/lake.yaml, bezogen auf die Laufzeit mit Faktor ' + fmtFactorLoose(1) + '), mit dem die Simulation heute die Monatsrate je Laufzeit skaliert; Verantwortlich: Leitung Customer Success.' }
      ]
    });

    /* ---------- Tabelle 4: Dasselbe je Geräteart ---------- */
    var famTerms = terms.filter(function (t) { return fam.some(function (x) { return x.extrap && !x.extrap[String(t)]; }); });
    var famRows = fam.map(function (x) {
      var cells = [E.C(x.family + (x.family === s.family ? ' (gewählt)' : '')), E.N(f.qty(x.n)), E.C(f.num(x.age_min, 1) + ' bis ' + f.num(x.age_max, 1), { nowrap: true })];
      famTerms.forEach(function (t) { cells.push(E.N(pb(x.q[String(t)], 0, x.extrap[String(t)]))); });
      famTerms.forEach(function (t) { cells.push(E.N(pb(x.loss_pm[String(t)], 1, x.extrap[String(t)]))); });
      return E.ROW(cells, { bold: x.family === s.family });
    });
    var famCols = [E.H('Geräteart'), E.H('Preisbelege', 1), E.H('Monate')];
    famTerms.forEach(function (t) { famCols.push(E.H('nach ' + f.qty(t) + ' Monaten', 1)); });
    famTerms.forEach(function (t) { famCols.push(E.H('je Monat bei ' + f.qty(t), 1)); });   /* hoechstens vier Woerter je Kopf (CONTRACT 4.1); Wertverlust sagt die Legende */
    var famTermList = famTerms.map(function (t) { return f.qty(t); }).join(', ');
    var tFamT = E.TABLE('x-fam', 'Dasselbe je Geräteart: wer verträgt lange Laufzeiten', famCols, famRows, {
      n: fam.reduce(function (a, x) { return a + (isNum(x.n) ? x.n : 0); }, 0),
      tags: [TAG_PUB],
      defs: [
        { k: 'Geräteart', v: 'Gerätefamilie; die Kurve des Reiter Realisierung läuft über alle Hersteller der Familie; fett die Geräteart der gewählten Serie.' },
        { k: 'Preisbelege', v: 'Stückzahl der Marktplatz-Angebote, aus denen die Familienkurve gerechnet ist.' },
        { k: 'Monate', v: 'Altersspanne dieser Belege in Monaten seit deutschem Verkaufsstart, jüngster bis ältester Beleg.' },
        { k: 'nach ' + famTermList + ' Monaten', v: 'Realisierung laut Familienkurve (Marktplatz-Angebote, abgelesen für Zustandsstufe B), in Klammern außerhalb der Belegspanne.' },
        { k: 'je Monat bei ' + famTermList, v: 'Wertverlust je Monat bei dieser Laufzeit: (Einkaufspreis minus Realisierung) geteilt durch die Monate, in Prozent der UVP; je kleiner, desto besser trägt die Geräteart eine lange Laufzeit.' }
      ]
    });

    /* ---------- Tabelle 5: Wie es in der Simulation heute aussieht (der Eimer der Serie) ---------- */
    var sim = Array.isArray(s.sim) ? s.sim : [];
    var simRows = sim.map(function (r) {
      return E.ROW([
        E.C(f.qty(r.term_months) + ' Monate', { nowrap: true }),
        E.N(f.qty(r.n)), E.N(f.qty(r.early || 0)), E.N(isNum(r.mb) ? f.num(r.mb, 1) : ''),
        E.N(f.eur(r.p)), E.N(f.eur2(r.rate)), E.N(f.eur(r.rent)), E.N(f.eur(r.rv)), E.N(f.eur(r.c)),
        E.N(f.eur(r.m), { neg: isNum(r.m) && r.m < 0 }),
        E.N(f.eur(r.m_full), { neg: isNum(r.m_full) && r.m_full < 0 }),
        E.N(f.eur(r.m_early), { neg: isNum(r.m_early) && r.m_early < 0 })
      ], { bold: r.term_months === t1 || r.term_months === t2 });
    });
    var simN = isNum(s.sim_n) ? s.sim_n : sim.reduce(function (a, r) { return a + (isNum(r.n) ? r.n : 0); }, 0);
    var simFew = sim.slice().sort(function (a, b) { return a.n - b.n; })[0];
    var tSim = E.TABLE('x-sim', 'Wie es in der Simulation heute aussieht: ' + famBucket, [
      E.H('Laufzeit'), E.H('abgeschlossen', 1), E.H('davon vorzeitig zurück', 1), E.H('Monate abgerechnet, im Mittel', 1), E.H('Einkaufspreis', 1), E.H('Miete je Monat', 1),
      E.H('Mieterlös', 1), E.H('Restwert', 1), E.H('Kosten bis Verkauf', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('Lifecycle-Marge, volle Laufzeit', 1), E.H('Lifecycle-Marge, vorzeitig zurück', 1)
    ], simRows, {
      n: simN,
      tags: [TAG_SIM],
      empty: 'Für die ' + famBucket + ' ist kein Kreislauf abgeschlossen.',
      defs: [
        { k: 'Laufzeit', v: 'geplante Vertragslaufzeit in Monaten; die Zeile mittelt alle abgeschlossenen Kreisläufe dieser Laufzeit in der ' + famBucket + ' (die Simulation kennt vier Gerätearten: iPhone, Android-Smartphone, Tablet, Laptop; feiner nicht).' },
        { k: 'abgeschlossen', v: 'Stückzahl der abgeschlossenen Kreisläufe je Laufzeit in der Simulation.' },
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
      foot: 'Mittelwerte je Gerät der abgeschlossenen Kreisläufe der ' + famBucket + '. Die Miete je Monat folgt aus dem Platzhalter-Faktor je Laufzeit; die Rangfolge der Laufzeiten ist deshalb eine Folge dieser Annahme, kein Marktbefund. '
        + (simFew ? f.qty(simFew.term_months) + ' Monate sind erst wenige abgeschlossen (' + f.qty(simFew.n) + ', davon ' + f.qty(simFew.early || 0) + ' vorzeitig beendet); die Zahl ist ein Urteil über vorzeitige Rückgaben, nicht über ' + f.qty(simFew.term_months) + ' Monate. ' : '')
        + (isNum(s.sim_buy_net) ? 'Der Einkaufspreis hier ist netto und liegt bei ' + f.pct(s.sim_buy_net) + ' der UVP ohne Mehrwertsteuer (Rabatt der Simulation, ' + simDisc + 'Platzhalter); ' : '')
        + 'die Marktrechnung oben nimmt einschließlich Mehrwertsteuer ' + f.pct(D.buy_share) + ' der UVP (Platzhalter des Einkaufs, ' + disc + ' Abschlag). '
        + 'Zwei Platzhalter für dieselbe Größe; welcher gilt, entscheidet die Einkaufsleitung.'
    });

    /* ---------- Tabelle 6: Was die Miete je Laufzeit ausmacht (nur die Miete geändert, Faktoren gegen die Vergleichslaufzeit) ---------- */
    var wiRows = [];
    sim.forEach(function (r) {
      var t = r.term_months, f0 = factors[String(t)], fB = factors[String(t2)], row = rowOf(t);
      if (!isNum(f0) || !f0 || !isNum(fB) || !fB || !row || !isNum(r.m) || !isNum(r.rate)) return;
      var st = hasCost && isNum(row.need_pm) ? worst(stat(row), stat(r2)) : 'none';
      if (st === 'none') return;
      var fd = row.factor[String(t2)], sj = stud[String(t)];
      var fLo = sj && isNum(sj.factor_lo[String(t2)]) ? Math.min(fd, sj.factor_lo[String(t2)]) : fd;
      var fHi = sj && isNum(sj.factor_hi[String(t2)]) ? Math.max(fd, sj.factor_hi[String(t2)]) : fd;
      var fp = f0 / fB;   /* der Platzhalter, ebenfalls gegen die Vergleichslaufzeit gerechnet */
      var rLo = r.rate * fLo / fp, rHi = r.rate * fHi / fp;
      var mLo = r.m + (rLo - r.rate) * t, mHi = r.m + (rHi - r.rate) * t;
      var same = fLo === fHi, br = function (x) { return st === 'extrap' ? '(' + x + ')' : x; };
      wiRows.push(E.ROW([
        E.C(f.qty(t) + ' Monate', { nowrap: true }),
        E.N(n2(fp)),
        E.N(f.eur2(r.rate)),
        E.N(f.eur(r.m), { neg: r.m < 0 }),
        E.N(br(same ? n2(fLo) : n2(fLo) + ' bis ' + n2(fHi))),
        E.N(br(same ? f.eur2(rLo) : f.eur2(rLo) + ' bis ' + f.eur2(rHi))),
        E.N(br(same ? f.eur(mLo) : f.eur(mLo) + ' bis ' + f.eur(mHi)), { neg: mHi < 0 })
      ], { bold: t === t1 }));
    });
    var tWhatIf = E.TABLE('x-whatif', 'Was die Miete je Laufzeit ausmacht', [
      E.H('Laufzeit'), E.H('Faktor, Platzhalter', 1), E.H('Miete je Monat', 1), E.H('Lifecycle-Marge je Gerät', 1),
      E.H('Faktor, abgeleitet', 1), E.H('Miete je Monat, abgeleitet', 1), E.H('Lifecycle-Marge, abgeleitet', 1)
    ], wiRows, {
      n: 0,
      tags: [TAG_SIM, TAG_DER],
      note: 'Nur die Miete geändert, alles andere wie in der Simulation der ' + famBucket + '; die Lifecycle-Marge je Gerät verschiebt sich um die Mietänderung mal die Monate der Laufzeit. Beide Faktoren gegen ' + T2 + ' Monate; Laufzeiten ohne belegte Kurve fehlen.',
      empty: 'Für ' + s.key + ' ist keine Laufzeit belegt; ohne Kurve keine abgeleitete Miete.',
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit in Monaten der Simulation; fett die gewählte Laufzeit.' },
        { k: 'Faktor, Platzhalter', v: 'der Platzhalter (term_rate_factor in config/lake.yaml), mit dem die Simulation heute die Monatsrate je Laufzeit skaliert, hier geteilt durch den Platzhalter der Vergleichslaufzeit (' + T2 + ' Monate).' },
        { k: 'Miete je Monat', v: 'Monatsrate der Simulation mit dem Platzhalter-Faktor, Mittel je Gerät.' },
        { k: 'Lifecycle-Marge je Gerät', v: 'Lifecycle-Marge je Gerät der Simulation mit dieser Miete, Mittel.' },
        { k: 'Faktor, abgeleitet', v: 'Faktor gegen ' + T2 + ' Monate aus Kurve und Kosten (Tabelle oben); wo Studien vorliegen, die Spanne von der Kurve bis zu den Studien; in Klammern außerhalb der Belegspanne.' },
        { k: 'Miete je Monat, abgeleitet', v: 'Miete je Monat mal abgeleiteter Faktor geteilt durch Platzhalter-Faktor.' },
        { k: 'Lifecycle-Marge, abgeleitet', v: 'Lifecycle-Marge je Gerät mit der abgeleiteten Miete, alles andere unverändert.' }
      ]
    });

    /* ---------- Grenzen, ausgesprochen ---------- */
    var blocks = [{
      key: 'x-limits', title: 'Grenzen, ausgesprochen', ordered: false, intro: '',
      items: [
        { lead: '', text: 'Die Kurve gilt für die Serie (' + s.key + '), nicht für das einzelne Modell: eine Alterskurve mit Belegen gibt es nur je Geräteart und Hersteller. Das Modell (' + m.model + ') wählt die Beschriftung und hebt seine eigenen Belege hervor; ' + ownLine + '.' },
        { lead: '', text: (hasAge ? 'Die Belege der ' + kurve + ' reichen von ' + span + 'n; außerhalb ist die Kurve verlängert und steht in Klammern, eine unsichere Kurve (wenige Belege) wird nicht verlängert, dort steht „nicht belegt“. ' : 'Für ' + s.key + ' gibt es keine Kurve; die Kacheln sagen „nicht belegt“. ')
          + (stud[String(terms[0])] ? 'Für ' + f.qty(terms[0]) + ' Monate sichern die Studien (USA, Großbritannien, Ankauf-Gebote) den Wert nach unten ab; deshalb steht dort eine Spanne.' : 'Studien mit Jahreswerten liegen nur für Apple Smartphones vor; für diese Serie gibt es keine.') },
        { lead: '', text: 'Marktplatz-Angebote sind die Obergrenze (Marge des Aufbereiters). Die deutschen Ankauf-Belege sind Höchstwerte „bis zu“ eines Ankäufers vor der Zustandsprüfung, also kein Boden; der tatsächliche Ankaufpreis liegt darunter. Was ein Leasinghaus erzielt, hängt vom Kanal ab (Reiter Stellschrauben, Kanalwahl).' },
        { lead: '', text: 'Die Kosten bis Verkauf sind simuliert und kennen nur vier Gerätearten (iPhone, Android-Smartphone, Tablet, Laptop); eine Serie wie ' + s.key + ' teilt sich den Eimer ' + s.fam_label + ' mit anderen Herstellern. Die Kurve ist öffentlich und seriengenau. Die abgeleiteten Faktoren mischen beides und sind deshalb eine Größenordnung, kein Preis.' },
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
      tables: [tRv].concat(tStudies ? [tStudies] : []).concat([tNeed, tFamT, tSim, tWhatIf]),
      blocks: blocks,
      /* Sonderfelder Laufzeit (seit 17.09.2026): Auswahl fuer die Huelle */
      sel: { slug: m.slug, family: s.family, oem: s.oem, model: m.model, series: s.key },
      pickers: pickers, firstOf: firstOf,
      terms: terms, t1: t1, t2: t2, termOpts: termOpts, term2Opts: term2Opts
    };
  };
  w.RE.term.version = 3;
})(window);
