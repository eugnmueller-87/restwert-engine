/* Restwert Engine v3: Motor fuer den Tab Geraet. Klassisches Skript, definiert window.RE.device.
   Vertrag: v3/CONTRACT.md (Abschnitte 1 bis 8, Sonderfelder 7.2). Quelle aller Texte, Zahlen und Rechenwege:
   v3/src/device.js und v3/src/device.body.html (der heutige Tab), Daten aus v3/data/device.json.
   Der Motor fasst kein DOM an, haelt keinen Zustand ausser einem Index je Datenobjekt (WeakMap) und schreibt
   keine Zahl als Text, die nicht aus D oder opts kommt; jede Zahl geht durch E.fmt. */
(function (w) {
  'use strict';
  var E = w.RE, f = E.fmt, CACHE = new WeakMap();

  /* Ersatzpalette, solange die Huelle die CSS-Tokens noch nicht gelesen hat (helle Werte aus app/styles.css) */
  var P0 = { font: '"Source Serif 4", serif', ink: '#201e1d', muted: '#7d7979', grid: '#d7d3d3', line: '#bab6b6', accent: '#0088b0', accent700: '#006786', accent2: '#d6006c', oem: {} };

  /* Wortlisten wie heute */
  var GR = { A: 'A wie neu', B: 'B sehr gut', C: 'C gut', D: 'D akzeptabel', TRADEIN: 'keine Stufe', UNKNOWN: 'unbekannt' };
  var FIT = { ok: 'belastbar, aus den Verkäufen dieses Modells', thin: 'unsicher, wenige Verkäufe dieses Modells', family: 'aus der Geräteart, weil dieses Modell zu wenige eigene Verkäufe hat', no_fit: 'keine Kurve' };
  var KIND = { 'ankauf-trade-in': 'Ankauf-Gebot „bis zu“', 'refurbished-marktplatz': 'Marktplatz-Angebot' };
  var TAG_SIM = { cls: 'tag-neutral', text: 'simuliert' }, TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' },
      TAG_DER = { cls: 'tag-neutral', text: 'abgeleitet' }, TAG_PRE = { cls: 'tag-neutral', text: 'Vorbelegung simuliert' };

  /* Feste Groessen der Ansicht: Zeilen und Horizonte wie heute, keine Behauptungen ueber die Daten */
  var HORIZON = 36;                                 /* Kachel "Restwertprognose nach 36 Monaten" */
  var AGES = [6, 12, 18, 24, 30, 36, 42, 48, 60];   /* Zeilen der Prognosetabelle, Modellalter in Monaten */
  var TERMS0 = [12, 24, 36, 48];                     /* Ersatz, falls D.factors keine Laufzeiten nennt */
  var DEFAULT_TERM = 24, DEFAULT_LABEL = 'iphone 15, 128 gb';
  var DTICK = 6;                                    /* Gitter der Monatsachse wie heute */

  /* ---------- kleine Helfer ---------- */
  function num(x) { return typeof x === 'number' && isFinite(x); }
  function plain(x) { /* Faktor wie heute geschrieben: 1 -> '1', 1.5 -> '1,5', 0.85 -> '0,85' */
    if (!num(x)) return '';
    var dec = (String(x).split('.')[1] || '').length;
    return f.num(x, Math.min(dec, 4));
  }
  function uniq(list) { var seen = {}, out = []; list.forEach(function (x) { if (x !== undefined && x !== null && !seen[x]) { seen[x] = 1; out.push(x); } }); return out; }
  function byName(a, b) { return String(a).localeCompare(String(b), 'de'); }
  function storageOf(z) { return num(z.storage) ? z.storage : null; }
  function sortSpecs(list) { /* nach Speicher aufsteigend, ohne Speicher zuletzt, sonst Reihenfolge der Daten */
    return list.map(function (z, i) { return { z: z, i: i }; }).sort(function (a, b) {
      var sa = storageOf(a.z), sb = storageOf(b.z);
      if (sa === null && sb === null) return a.i - b.i;
      if (sa === null) return 1;
      if (sb === null) return -1;
      return sa - sb || a.i - b.i;
    }).map(function (p) { return p.z; });
  }
  function opt(v) { return { value: v, label: v }; }
  function famLabel(D, d) { return (D.fam_label || {})[d.fam] || d.family; }

  /* ---------- Index je Datenobjekt ---------- */
  function index(D) {
    var x = CACHE.get(D); if (x) return x;
    var devs = D.devices;
    var byId = {}, byLabel = {};
    devs.forEach(function (z) { byId[z.id] = z; byLabel[String(z.label).toLowerCase()] = z; });
    var terms = Object.keys(D.factors || {}).map(Number).filter(function (n) { return num(n) && n > 0; }).sort(function (a, b) { return a - b; });
    if (!terms.length) terms = TERMS0.slice();
    var fams = uniq(devs.map(function (z) { return z.family; })).sort(byName);
    x = { devs: devs, byId: byId, byLabel: byLabel, terms: terms, fams: fams, def: byLabel[DEFAULT_LABEL] || devs[0] };
    CACHE.set(D, x); return x;
  }

  /* ---------- Rechenwege wie heute ---------- */
  function gridRatio(D, d, months) {
    var g = (D.grid || {})[d.slug]; if (!g || !g.r || !g.r.length) return null;
    var m = Math.max(0, Math.min(g.r.length - 1, Math.round(months)));
    return { r: g.r[m], lo: g.lo[m], hi: g.hi[m] };
  }
  function curveQ(D, d, months) {
    var C = D.curves || {}, oem = C[d.family + ' / ' + d.oem];
    var c = (oem && num(oem.slope)) ? oem : C[d.family];
    if (!c || !num(c.slope)) return null;
    var q = Math.min(1, Math.exp(c.intercept + c.slope * months));
    return { q: q, extrap: months < c.age_min || months > c.age_max, c: c, name: c === oem ? 'Kurve ' + d.oem + ' (' + d.family + ')' : 'Kurve ' + d.family + ', alle Hersteller' };
  }
  function defaults(D, d, term) {
    var fc = (D.fam_cost || {})[d.fam] || {};
    var ft = fc[String(term)] || fc[String(DEFAULT_TERM)] || {};
    var rrpNet = d.rrp / (1 + (num(D.vat) ? D.vat : 0));
    var vf = num(d.storage) ? (D.var_fleet || {})[d.slug + '|' + Math.round(d.storage) + '|' + term] : null;
    var sf = ((D.fleet || {})[d.slug] || []).filter(function (r) { return r.term_months === term; })[0];
    var src = {}, buy, rate, cost;
    if (vf && vf.p) { buy = vf.p; src.buy = 'QTY ' + f.qty(vf.n) + ' Geräte genau dieser Ausstattung und Laufzeit'; }
    else if (sf && sf.p_all && sf.rrp_net) { buy = sf.p_all * (rrpNet / sf.rrp_net); src.buy = 'QTY ' + f.qty(sf.n) + ' Geräte dieses Modells (andere Speicher), auf die UVP dieser Ausstattung umgerechnet'; }
    else if (num(ft.buy)) { buy = rrpNet * ft.buy; src.buy = 'UVP ohne Mehrwertsteuer mal ' + f.pct(ft.buy) + ' (Kaufanteil der Geräteart in der Simulation)'; }
    else { buy = rrpNet; src.buy = 'UVP ohne Mehrwertsteuer (kein Kaufanteil der Geräteart in der Simulation)'; }
    if (vf && vf.rate) { rate = vf.rate; src.rate = 'QTY ' + f.qty(vf.n) + ' Geräte dieser Ausstattung'; }
    else {
      var rp = (D.rate_pct || {})[d.fam], fac = (D.factors || {})[String(term)];
      rate = buy * (num(rp) ? rp : 0) * (num(fac) ? fac : 1);
      src.rate = 'Einkaufspreis mal ' + f.pct1(rp) + ' mal Faktor ' + plain(num(fac) ? fac : 1) + ' (Platzhalter der Simulation)';
    }
    if (vf && vf.c && vf.closed > 0) { cost = vf.c; src.cost = 'QTY ' + f.qty(vf.closed) + ' abgeschlossene Geräte dieser Ausstattung'; }
    else if (sf && sf.c && sf.closed > 0) { cost = sf.c; src.cost = 'QTY ' + f.qty(sf.closed) + ' abgeschlossene Geräte dieses Modells'; }
    else { cost = num(ft.c) ? ft.c : 0; src.cost = 'Mittelwert der Geräteart ' + famLabel(D, d) + ' bei ' + f.qty(term) + ' Monaten, QTY ' + f.qty(num(ft.n) ? ft.n : 0) + ' (Platzhalter der Simulation)'; }
    return { buy: buy, rate: rate, cost: cost, rrpNet: rrpNet, src: src };
  }
  function inputsFor(D, d, term, over) {
    var df = defaults(D, d, term);
    return { buy: num(over.buy) ? over.buy : df.buy, rate: num(over.rate) ? over.rate : df.rate, cost: num(over.cost) ? over.cost : df.cost, df: df };
  }
  function series(D, d, t, inp, a0) {
    var out = [];
    for (var m = 0; m <= t; m++) {
      var g = gridRatio(D, d, a0 + m), rv = g ? g.r * inp.buy : null;
      out.push({ m: m, rent: inp.rate * m, rv: rv, margin: rv === null ? null : inp.rate * m + rv - inp.buy - inp.cost });
    }
    return out;
  }
  function breakEven(s) {
    if (!s.length || s[0].margin === null) return { state: 'none' };
    for (var i = 0; i < s.length; i++) {
      if (s[i].margin >= 0) {
        var stays = true;
        for (var j = i; j < s.length; j++) { if (s[j].margin < 0) { stays = false; break; } }
        return { state: 'month', m: s[i].m, stays: stays };
      }
    }
    return { state: 'never' };
  }
  function beText(b) { return b.state === 'none' ? 'keine Prognose' : (b.state === 'never' ? 'nie' : f.qty(b.m) + (b.stays ? '' : ' (fällt später wieder unter null)')); }
  function beValue(b) { return b.state === 'month' && b.stays ? b.m : beText(b); }

  /* ---------- der Motor ---------- */
  w.RE.device = function (D, opts, P) {
    opts = opts || {};
    if (!D || !Array.isArray(D.devices) || !D.devices.length) throw new Error('device: D.devices fehlt oder ist leer');
    var X = index(D), pal = P || P0, devs = X.devs, terms = X.terms;
    var d = (opts.id !== null && opts.id !== undefined && X.byId[opts.id]) || X.def;
    var term = terms.indexOf(Number(opts.term)) >= 0 ? Number(opts.term) : (terms.indexOf(DEFAULT_TERM) >= 0 ? DEFAULT_TERM : terms[0]);
    var when = opts.when === 'today' ? 'today' : 'launch';
    var over = {};
    ['buy', 'rate', 'cost'].forEach(function (k) {
      if (!opts.over || opts.over[k] === undefined || opts.over[k] === null || opts.over[k] === '') return;
      var v = Number(opts.over[k]); if (isFinite(v)) over[k] = v;
    });

    /* Auswahlfelder: nur, was es im Katalog gibt */
    var oems = uniq(devs.filter(function (z) { return z.family === d.family; }).map(function (z) { return z.oem; })).sort(byName);
    var models = uniq(devs.filter(function (z) { return z.family === d.family && z.oem === d.oem; }).map(function (z) { return z.model; }));
    var specs = sortSpecs(devs.filter(function (z) { return z.family === d.family && z.oem === d.oem && z.model === d.model; }));
    var pickers = { fams: X.fams.map(opt), oems: oems.map(opt), models: models.map(opt), specs: specs.map(function (z) { return { value: z.id, label: z.spec }; }) };
    var firstOf = function (family, oem, model) {
      var hit = devs.filter(function (z) { return (!family || z.family === family) && (!oem || z.oem === oem) && (!model || z.model === model); });
      if (model) hit = sortSpecs(hit);
      return hit[0] || null;
    };

    /* Rechnung */
    var a0 = when === 'today' ? d.age : 0;
    var inp = inputsFor(D, d, term, over);
    var s = series(D, d, term, inp, a0), end = s[s.length - 1], be = breakEven(s);
    var perTerm = terms.map(function (t) {
      var i = inputsFor(D, d, t, over), st = series(D, d, t, i, a0), e = st[st.length - 1];
      return { t: t, rate: i.rate, rent: i.rate * t, rv: e.rv, cost: i.cost, margin: e.margin, be: breakEven(st) };
    });
    var g36 = gridRatio(D, d, a0 + HORIZON), q36 = curveQ(D, d, a0 + HORIZON), then36 = Math.round(a0 + HORIZON);
    var announced = d.launch_kind === 'ankuendigung';
    var whenText = when === 'launch' ? 'am Verkaufsstart' : 'heute';

    /* Kennzahlen: die vier Kacheln von heute */
    var kpis = [
      { label: d.label, value: f.eur(d.rrp), tags: [TAG_PUB],
        lines: ['UVP beim Verkaufsstart, mit Mehrwertsteuer', 'Verkaufsstart ' + f.de(d.launch) + (announced ? ' (Ankündigung)' : ''), 'heute ' + f.num(d.age) + ' Monate alt'] },
      { label: 'Restwertprognose nach ' + f.qty(HORIZON) + ' Monaten' + (when === 'today' ? ' ab heute' : ''), value: g36 ? f.pct(g36.r) : 'keine Prognose', tags: [TAG_SIM],
        lines: ['des Einkaufspreises, Zustandsstufe B, Marktplatz; Modellalter dann ' + f.qty(then36) + ' Monate',
                g36 ? 'das sind ' + f.eur(g36.r * inp.buy) + ' beim Einkaufspreis ' + f.eur(inp.buy) : 'Modell nicht in der Simulation',
                q36 ? q36.name + ': ' + (q36.extrap ? '(' + f.pct(q36.q) + ')' : f.pct(q36.q)) + ' der UVP' : 'keine öffentliche Kurve'] },
      { label: 'Lifecycle-Marge, ' + f.qty(term) + ' Monate', value: end.margin === null ? 'keine Prognose' : f.eur(end.margin), neg: end.margin !== null && end.margin < 0, tags: [TAG_DER],
        lines: end.margin === null ? ['keine Restwertprognose für dieses Modell'] : [
          'Mieterlös ' + f.eur(inp.rate * term) + ' plus Restwertprognose ' + f.eur(end.rv),
          'minus Einkaufspreis ' + f.eur(inp.buy) + ' minus Kosten bis Verkauf ' + f.eur(inp.cost),
          'Kauf ' + whenText] },
      { label: 'Kosten eingespielt ab Monat', value: beText(be), tags: [TAG_DER],
        lines: be.state === 'none' ? ['das Werkzeug hat keine Restwertprognose für dieses Modell']
          : (be.state === 'never' ? ['innerhalb von ' + f.qty(term) + ' Monaten nicht', 'ein Verkauf vor dem Ende wäre ein Verlust']
          : ['ab diesem Monat ist die Lifecycle-Marge in dieser Rechnung positiv' + (be.stays ? ' und bleibt es bis zum Ende' : ', fällt aber später wieder unter null'), 'davor wäre ein Verkauf ein Verlust']) }
    ];

    /* Steckbrief */
    var facts = [
      { k: 'Hersteller, Geräteart', v: d.oem + ', ' + d.family + (d.series ? ', Reihe ' + d.series : '') },
      { k: 'Verkaufsstart Deutschland', v: f.de(d.launch) + (announced ? ' (Ankündigung, Verfügbarkeit nicht belegt)' : ''), href: d.launch_url || '' },
      { k: 'UVP beim Verkaufsstart', v: f.eur(d.rrp) + ' mit ' + f.pct(D.vat) + ' Mehrwertsteuer, ' + f.eur(inp.df.rrpNet) + ' ohne' + (d.rrp_date ? ', Stand ' + f.de(d.rrp_date) : ''), href: d.rrp_url || '' },
      { k: 'Nachfolger', v: d.successor ? d.successor + (d.successor_date ? ', ab ' + f.de(d.successor_date) : '') : 'keiner eingetragen' },
      { k: 'Modellalter heute', v: f.num(d.age) + ' Monate seit Verkaufsstart' }
    ];

    /* Rechenhinweis: heutiger Absatz "Monat für Monat" plus die Herkunft der Vorbelegung */
    var calcnote = 'Monat für Monat: die aufgelaufene Miete steigt, die Restwertprognose fällt, die Kosten bis Verkauf fallen einmal an. Lifecycle-Marge im Monat m = Miete mal m plus Restwertprognose im Monat m minus Einkaufspreis minus Kosten bis Verkauf. Der erste Monat, in dem sie null erreicht, ist der Punkt, ab dem das Gerät seine Kosten eingespielt hat; wer es früher zurückbekommt und verkauft, macht Verlust.'
      + ' Vorbelegung für ' + f.qty(term) + ' Monate: Einkaufspreis aus ' + inp.df.src.buy + '; Miete aus ' + inp.df.src.rate + '; Kosten bis Verkauf aus ' + inp.df.src.cost + '.'
      + (when === 'today' ? ' Kauf heute: der Einkaufspreis ist der damalige Preis der Flotte, für ein ' + f.num(d.age) + ' Monate altes Modell heute überschreiben.' : '')
      + ' Alle drei Felder lassen sich überschreiben; Kauf am Verkaufsstart ist der Idealfall, die Simulation kauft später.';

    /* Diagramm: aufgelaufene Miete, Restwertprognose, Lifecycle-Marge Monat fuer Monat */
    var xs = s.map(function (p) { return p.m; });
    var line = function (name, short, ys, color, width) {
      return { type: 'scatter', mode: 'lines', name: name, x: xs, y: ys, line: { color: color, width: width },
        text: ys.map(function (v) { return v === null ? '' : f.eur(v); }), hovertemplate: 'Monat %{x}: %{text}<extra>' + short + '</extra>' };
    };
    var chart = {
      traces: [
        line('aufgelaufene Miete', 'aufgelaufene Miete', s.map(function (p) { return p.rent; }), pal.accent, 2),
        line('Restwertprognose', 'Restwertprognose', s.map(function (p) { return p.rv; }), pal.accent2, 2),
        line('Lifecycle-Marge (aufgelaufene Miete plus Restwertprognose minus Einkaufspreis minus Kosten bis Verkauf)', 'Lifecycle-Marge', s.map(function (p) { return p.margin; }), pal.ink, 3)
      ],
      layout: {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { family: pal.font, color: pal.ink },
        xaxis: { title: { text: 'Monate seit Vertragsbeginn' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: false, dtick: DTICK },
        yaxis: { title: { text: 'Euro' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: true, zerolinecolor: pal.muted },
        legend: { orientation: 'h', y: 1.02, yanchor: 'bottom', x: 0, xanchor: 'left' },
        shapes: be.state !== 'month' ? [] : [{ type: 'line', x0: be.m, x1: be.m, y0: 0, y1: 1, yref: 'paper', line: { color: pal.accent, dash: 'dash', width: 1 } }],
        annotations: be.state !== 'month' ? [] : [{ x: be.m, y: 0, yref: 'paper', text: 'Kosten eingespielt: Monat ' + f.qty(be.m), showarrow: false, yanchor: 'bottom', xanchor: 'left', xshift: 4, font: { size: 11, color: pal.accent700 } }]
      }
    };

    /* Tabelle 1: Rechner je Laufzeit */
    var termRows = perTerm.map(function (r) {
      return E.ROW([
        E.C(f.qty(r.t) + ' Monate'), E.N(f.eur2(r.rate)), E.N(f.eur(r.rent)), E.N(f.eur(r.rv)), E.N(f.eur(r.cost)),
        E.N(r.margin === null ? 'keine Prognose' : f.eur(r.margin), { neg: r.margin !== null && r.margin < 0 }), E.N(beText(r.be))
      ], { bold: r.t === term });
    });
    var tTerms = E.TABLE('d-terms', 'Ab wann rechnet sich das Gerät', [
      E.H('Laufzeit'), E.H('Miete je Monat', 1), E.H('Mieterlös', 1), E.H('Restwertprognose am Ende', 1), E.H('Kosten bis Verkauf', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('Kosten eingespielt ab Monat', 1)
    ], termRows, {
      n: 0, tags: [TAG_DER, TAG_PRE],
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit in Monaten; die fette Zeile ist die oben gewählte Laufzeit.' },
        { k: 'Miete je Monat', v: 'aus den Geräten dieser Ausstattung in der Simulation, sonst Einkaufspreis mal Monatssatz der Geräteart mal Faktor je Laufzeit (Platzhalter der Simulation); überschreibbar' },
        { k: 'Mieterlös', v: 'Miete je Monat mal Laufzeit, ohne Mehrwertsteuer.' },
        { k: 'Restwertprognose am Ende', v: 'Wert laut Restwertprognose des Werkzeugs für dieses Modell (Zustandsstufe B, Marktplatz) beim Modellalter am Vertragsende, in Euro auf den Einkaufspreis gerechnet' },
        { k: 'Kosten bis Verkauf', v: 'aus den abgeschlossenen Geräten dieser Ausstattung, sonst Mittelwert der Geräteart und Laufzeit (Platzhalter der Simulation); einmal je Kreislauf; überschreibbar' },
        { k: 'Lifecycle-Marge je Gerät', v: 'Mieterlös plus Restwertprognose am Ende minus Einkaufspreis minus Kosten bis Verkauf.' },
        { k: 'Kosten eingespielt ab Monat', v: 'der erste Monat, in dem Miete plus Restwertprognose Einkaufspreis und Kosten decken und die Marge danach bis zum Vertragsende nicht wieder unter null fällt; „nie“ heißt: nicht innerhalb dieser Laufzeit; „keine Prognose“ heißt: das Werkzeug hat für dieses Modell keine Restwertprognose' }
      ]
    });

    /* Tabelle 2: Restwertprognose des Werkzeugs */
    var g = (D.grid || {})[d.slug], qn = curveQ(D, d, DEFAULT_TERM);
    var prognote = (g
      ? 'Modellalter in Monaten seit Verkaufsstart. Die Prognose stammt aus dem Rechenmodell des Werkzeugs, gelernt aus den simulierten Verkäufen; Verlässlichkeit: ' + (FIT[g.fit] || g.fit) + '. Euro-Werte beim Einkaufspreis ' + f.eur(inp.buy) + '.'
      : 'Dieses Modell ist nicht in der Simulation; das Werkzeug hat keine eigene Prognose dafür.')
      + (qn ? ' Rechte Spalte: ' + qn.name + ' vom Tab Realisierung, QTY ' + f.qty(qn.c.n) + ' Preisbelege, Belege von ' + f.num(qn.c.age_min) + ' bis ' + f.num(qn.c.age_max) + ' Monaten.' : ' Für diese Familie gibt es keine öffentliche Kurve.');
    var progRows = AGES.map(function (m) {
      var r = g ? gridRatio(D, d, m) : null, q = curveQ(D, d, m);
      return E.ROW([
        E.C(f.qty(m) + ' Monate'), E.N(r ? f.pct(r.r) : ''), E.N(r ? f.pct(r.lo) + ' bis ' + f.pct(r.hi) : ''), E.N(r ? f.eur(r.r * inp.buy) : ''),
        E.N(q ? (q.extrap ? '(' + f.pct(q.q) + ')' : f.pct(q.q)) : '')
      ]);
    });
    var tProg = E.TABLE('d-prog', 'Restwertprognose des Werkzeugs für dieses Modell', [
      E.H('Modellalter'), E.H('Anteil des Einkaufspreises', 1), E.H('Spanne', 1), E.H('Euro beim Einkaufspreis oben', 1), E.H('Öffentliche Kurve, % UVP', 1)
    ], progRows, {
      n: 0, note: prognote, tags: [TAG_SIM],
      defs: [
        { k: 'Modellalter', v: 'Modellalter in Monaten seit Verkaufsstart.' },
        { k: 'Anteil des Einkaufspreises', v: 'die Restwertprognose des Werkzeugs (Zustandsstufe B, Marktplatz) in Prozent des Einkaufspreises, aus den Verkäufen der Simulation gelernt' },
        { k: 'Spanne', v: 'untere und obere Grenze der Prognose' },
        { k: 'Euro beim Einkaufspreis oben', v: 'Anteil des Einkaufspreises mal dem Einkaufspreis aus dem Feld oben, in Euro.' },
        { k: 'Öffentliche Kurve', v: 'zum Vergleich die Marktplatz-Kurve vom Tab Realisierung, in Prozent der UVP: die des Herstellers in dieser Familie, wenn es eine belastbare gibt, sonst die der Familie über alle Hersteller (die Notiz darüber sagt, welche); in Klammern außerhalb der Belegspanne. Andere Basis als die Spalte links (UVP statt Einkaufspreis)' }
      ]
    });

    /* Tabelle 3: oeffentliche Preisbelege, manuell erfasste aus opts.extra zaehlen mit */
    var anchors = ((D.anchors || {})[d.slug] || []).map(function (a) {
      return { spec: a.spec, grade: a.grade, condition: a.condition, age: a.age, price: a.price, q: a.q, kind: a.kind, url: a.url, date: a.date, manual: false };
    });
    (Array.isArray(opts.extra) ? opts.extra : []).forEach(function (a) {
      if (!a || a.slug !== d.slug) return;
      anchors.push({ spec: a.spec_used || a.spec || '', grade: a.grade, condition: a.condition || '', age: a.age_months !== undefined ? a.age_months : a.age, price: a.price_eur !== undefined ? a.price_eur : a.price,
        q: a.realisation !== undefined ? a.realisation : a.q, kind: a.source_kind || a.kind, url: a.source_url || a.url || '', date: a.date_seen || a.date, manual: true });
    });
    anchors.sort(function (a, b) { return byName(a.spec, b.spec) || (a.age - b.age); });
    var nManual = anchors.filter(function (a) { return a.manual; }).length;
    var ancRows = anchors.map(function (a) {
      return E.ROW([
        E.C(a.spec), E.C(GR[a.grade] || a.grade), E.C(a.condition), E.N(f.num(a.age)), E.N(f.eur(a.price)), E.N(f.pct(a.q)),
        E.C(KIND[a.kind] || (a.kind === 'ankauf-trade-in' ? KIND['ankauf-trade-in'] : KIND['refurbished-marktplatz']), { tag: a.manual ? 'tag-accent-2' : '', tagText: a.manual ? 'manuell' : '' }),
        E.C(f.de(a.date), { nowrap: true }), a.url ? E.C('', { href: a.url }) : E.C('keine Adresse')
      ]);
    });
    var tAnc = E.TABLE('d-anchors', 'Öffentliche Preisbelege für dieses Modell', [
      E.H('Ausstattung'), E.H('Zustandsstufe'), E.H('Zustand laut Verkäufer'), E.H('Monate', 1), E.H('Preis', 1), E.H('Realisierung', 1), E.H('Preisart'), E.H('Datum'), E.H('Quelle')
    ], ancRows, {
      tags: [TAG_PUB], collapsible: ancRows.length > 20,
      note: ancRows.length ? 'QTY ' + f.qty(ancRows.length) + ' Preisbelege für dieses Modell, alle Ausstattungen' + (nManual ? ', davon QTY ' + f.qty(nManual) + ' manuell erfasst' : '') + '; Realisierung = Preis geteilt durch die UVP derselben Ausstattung.' : '',
      empty: 'Für dieses Modell liegt kein öffentlicher Preisbeleg vor; die Kurve der Familie und des Herstellers vom Tab Realisierung gilt als Näherung.',
      foot: 'Ankauf-Gebot „bis zu“ ist der Höchstwert, den ein Ankäufer vor der Zustandsprüfung nennt, kein garantierter Betrag; der tatsächliche Ankaufpreis liegt darunter.',
      defs: [
        { k: 'Ausstattung', v: 'Speicher und Farbe des Geräts, wie die Quelle sie nennt.' },
        { k: 'Zustandsstufe', v: 'A wie neu, B sehr gut, C gut, D akzeptabel; Ankauf-Gebote haben keine Stufe, eine Angabe, die keiner Stufe zuzuordnen ist, heißt unbekannt.' },
        { k: 'Zustand laut Verkäufer', v: 'der Wortlaut der Quelle zum Zustand.' },
        { k: 'Monate', v: 'Modellalter in Monaten seit Verkaufsstart am Tag des Belegs.' },
        { k: 'Preis', v: 'Preis am Tag des Belegs, einschließlich Mehrwertsteuer.' },
        { k: 'Realisierung', v: 'Preis geteilt durch die UVP derselben Ausstattung, beide einschließlich Mehrwertsteuer.' },
        { k: 'Preisart', v: 'Marktplatz-Angebot eines Aufbereiters oder Ankauf-Gebot „bis zu“ eines Ankäufers; manuell erfasste Belege tragen die Marke manuell.' },
        { k: 'Datum', v: 'Tag, an dem der Preis gesehen wurde.' },
        { k: 'Quelle', v: 'Adresse der Seite mit dem Preis.' }
      ]
    });

    /* Tabelle 4: dieses Modell in der simulierten Flotte */
    var fl = (D.fleet || {})[d.slug] || [], nd = (D.not_deployed || {})[d.slug] || 0;
    var nFleet = fl.reduce(function (a, r) { return a + (num(r.n) ? r.n : 0); }, 0);
    var fleetRows = fl.map(function (r) {
      return E.ROW([
        E.C(f.qty(r.term_months) + ' Monate'), E.N(f.qty(r.n)), E.N(f.qty(r.closed)), E.N(r.closed ? f.qty(r.early) : ''), E.N(f.eur(r.p)), E.N(f.eur2(r.rate)),
        E.N(f.eur(r.rent)), E.N(f.eur(r.rv)), E.N(f.eur(r.c)), E.N(f.eur(r.m), { neg: num(r.m) && r.m < 0 }), E.N(r.closed ? f.pct(r.pos / r.closed) : '')
      ]);
    });
    var tFleet = E.TABLE('d-fleet', 'Dieses Modell in der simulierten Flotte', [
      E.H('Laufzeit'), E.H('QTY Geräte', 1), E.H('davon abgeschlossen', 1), E.H('davon vorzeitig zurück', 1), E.H('Einkaufspreis', 1), E.H('Miete je Monat', 1), E.H('Mieterlös', 1),
      E.H('Restwert', 1), E.H('Kosten bis Verkauf', 1), E.H('Lifecycle-Marge je Gerät', 1), E.H('davon mit positiver Lifecycle-Marge', 1)
    ], fleetRows, {
      n: nFleet, tags: [TAG_SIM],
      note: fleetRows.length ? 'QTY ' + f.qty(nFleet) + ' Geräte dieses Modells mit Mietvertrag in der Simulation, alle Ausstattungen' + (nd ? '; dazu QTY ' + f.qty(nd) + ' ohne Vertrag (Ersatzgeräte im Lager)' : '') + '.' : '',
      empty: 'Dieses Modell wurde in der Simulation nicht gekauft.',
      foot: 'Alle Spalten außer QTY sind Mittelwerte über die abgeschlossenen Kreisläufe dieser Laufzeit, deshalb addieren sich Mieterlös plus Restwert minus Einkaufspreis minus Kosten zur Marge. Warum die Flotte unter dem Rechner oben liegen kann: die Simulation kauft nicht am Verkaufsstart, sondern später; sie verkauft in der Zustandsstufe und über den Kanal, die das Gerät am Ende hatte, der Rechner nimmt Stufe B und Marktplatz; und ein vorzeitig zurückgegebener Vertrag hat weniger Mieterlös (Spalte vorzeitig). Ein Modell ohne Zeile wurde in der Simulation nicht gekauft.',
      defs: [
        { k: 'Laufzeit', v: 'Vertragslaufzeit der Geräte in dieser Zeile, in Monaten.' },
        { k: 'QTY Geräte', v: 'QTY (Quantity): Stückzahl der Geräte dieses Modells mit Mietvertrag dieser Laufzeit, alle Ausstattungen.' },
        { k: 'davon abgeschlossen', v: 'davon Geräte, deren Kreislauf abgeschlossen ist; nur sie gehen in die Mittelwerte der Zeile ein.' },
        { k: 'davon vorzeitig zurück', v: 'davon Geräte, die vor dem Vertragsende zurückkamen; leer, solange kein Kreislauf abgeschlossen ist.' },
        { k: 'Einkaufspreis', v: 'Einkaufspreis je Gerät im Mittel der abgeschlossenen Kreisläufe.' },
        { k: 'Miete je Monat', v: 'Miete je Monat im Mittel der abgeschlossenen Kreisläufe.' },
        { k: 'Mieterlös', v: 'Mieterlös je Gerät im Mittel; ein vorzeitig zurückgegebener Vertrag hat weniger.' },
        { k: 'Restwert', v: 'Erlös aus dem Verkauf je Gerät im Mittel, in der Zustandsstufe und über den Kanal, die das Gerät am Ende hatte.' },
        { k: 'Kosten bis Verkauf', v: 'Kosten bis Verkauf je Gerät im Mittel, einmal je Kreislauf.' },
        { k: 'Lifecycle-Marge je Gerät', v: 'Mieterlös plus Restwert minus Einkaufspreis minus Kosten bis Verkauf, je Gerät im Mittel.' },
        { k: 'davon mit positiver Lifecycle-Marge', v: 'Anteil der abgeschlossenen Kreisläufe, deren Lifecycle-Marge über null liegt.' }
      ]
    });

    return {
      kicker: 'Gerät nachschlagen, Stand ' + f.de(D.today),
      subject: 'Ein Gerät, alle Zahlen: was es verliert, was es bringt, ab wann es sich rechnet',
      intro: 'Gerät wählen (Modell und Speicher). Dann: Steckbrief mit UVP und Quelle, die öffentlichen Preisbelege für genau dieses Modell, die Restwertprognose des Werkzeugs, die Geräte dieses Modells in der simulierten Flotte, und ein Rechner je Laufzeit, der sagt, ab welchem Monat das Gerät seine Kosten eingespielt hat.'
        + ' QTY ' + f.qty(devs.length) + ' Geräte (Modell und Speicher) mit UVP im Katalog.',
      facts: facts,
      kpis: kpis,
      kpiDefs: [{ k: 'QTY (Quantity)', v: 'Stückzahl; das Wort dahinter sagt, was gezählt wird: Geräte, Preisbelege.' }],
      calcnote: calcnote,
      chart: chart,
      chartNote: '',
      tables: [tTerms, tProg, tAnc, tFleet],
      blocks: [],
      /* Sonderfelder Geraet (Vertrag 7.2) */
      d: d, byId: X.byId, byLabel: X.byLabel, pickers: pickers, firstOf: firstOf,
      terms: terms, term: term, when: when,
      inputs: { buy: inp.buy, rate: inp.rate, cost: inp.cost },
      overridden: { buy: num(over.buy), rate: num(over.rate), cost: num(over.cost) },
      summary: { buy: inp.buy, rate: inp.rate, cost: inp.cost, rv: end.rv, margin: end.margin, be: beValue(be) }
    };
  };
  w.RE.device.version = 3;
})(window);
