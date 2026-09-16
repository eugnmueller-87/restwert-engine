/* Restwert Engine v3: Motor TCO. Klassisches Skript, definiert window.RE.tco(D, opts, P) nach v3/CONTRACT.md 7.4.
   Quelle aller Texte, Zahlen, Rechenwege, Tabellen und Legenden: v3/src/tco.js und v3/src/tco.body.html (der heutige Reiter).
   Der Motor fasst kein DOM an, haelt keinen Zustand und tippt keine Zahl: jede Zahl kommt aus D oder opts und geht durch E.fmt.
   Lokal nachgebaut, weil es sie in E.fmt bewusst nicht gibt: money (unter hundert Euro zwei Stellen, sonst ganze Euro), wie heute. */
(function (w) {
  'use strict';
  var E = w.RE, CACHE = new WeakMap();

  function uniq(arr) { var seen = {}; return arr.filter(function (x) { if (seen[x]) return false; seen[x] = true; return true; }); }

  /* Vorarbeit je Datensatz, einmal je D (Vertrag 1: WeakMap mit D als Schluessel) */
  function index(D) {
    var x = CACHE.get(D); if (x) return x;
    var bySlug = {}; D.models.forEach(function (m) { bySlug[m.slug] = m; });
    var pool = D.models.filter(function (m) { return m.in_fleet; });
    var keysBySlug = {};
    Object.keys(D.keys).forEach(function (k) { var s = k.split('|')[0]; (keysBySlug[s] = keysBySlug[s] || []).push(k); });
    var NAME = {}, DEF = {}; D.defs.forEach(function (r) { NAME[r.lt] = r.name; DEF[r.lt] = r; });
    var LINES = D.lines, COST = LINES.filter(function (l) { return l !== 'purchase_price'; });
    x = { bySlug: bySlug, pool: pool, keysBySlug: keysBySlug, NAME: NAME, DEF: DEF, LINES: LINES, COST: COST };
    CACHE.set(D, x); return x;
  }

  /* Summenbehaelter und Addition, eins zu eins aus dem heutigen Skript */
  function empty() {
    return {
      closed: { n: 0, p: 0, c: 0, est: 0, tco_pm: 0, mb: 0, rate: 0, term_sum: 0, ppc_n: 0, ppc_sum: 0, n_lines: 0, lines: {} },
      open: { n: 0, p: 0, c: 0, tco_pm: 0, mb: 0, rate: 0, mr: 0, term_sum: 0, ppc_n: 0, ppc_sum: 0, n_lines: 0, lines: {}, exp: {}, erc: 0, fee_exp: 0, fb: 0, fee_open: 0, basis: {}, fbt: {} }
    };
  }
  function addInto(a, b) {
    ['n', 'p', 'c', 'est', 'tco_pm', 'mb', 'rate', 'term_sum', 'ppc_n', 'ppc_sum', 'n_lines', 'mr', 'erc', 'fee_exp', 'fb', 'fee_open'].forEach(function (fld) { if (b[fld] !== undefined) a[fld] = (a[fld] || 0) + b[fld]; });
    Object.keys(b.lines || {}).forEach(function (lt) { var v = b.lines[lt]; var cur = a.lines[lt] || [0, 0, 0, 0]; for (var i = 0; i < v.length; i++) cur[i] = (cur[i] || 0) + v[i]; a.lines[lt] = cur; });
    Object.keys(b.exp || {}).forEach(function (lt) { a.exp[lt] = (a.exp[lt] || 0) + b.exp[lt]; });
    ['basis', 'fbt'].forEach(function (fld) { if (!b[fld]) return; if (!a[fld]) a[fld] = {}; Object.keys(b[fld]).forEach(function (t) { a[fld][t] = (a[fld][t] || 0) + b[fld][t]; }); });
  }
  function openTotals(o) { var e = 0; Object.keys(o.exp).forEach(function (lt) { e += o.exp[lt]; }); return { sofar: o.p + o.c, exp: e, total: o.p + o.c + e }; }

  /* Grundlage der Erwartung je Kostenzeile, Wortlaut wie heute */
  var GB = {
    purchase_price: 'gebucht, keine Erwartung', freight: 'gebucht, keine Erwartung', duty: 'gebucht, keine Erwartung', staging: 'gebucht, keine Erwartung', outbound_shipping: 'gebucht, keine Erwartung',
    repair: 'Mittel der Geräteart und Laufzeit mal Anteil der noch offenen Monate', replacement_logistics: 'Mittel der Geräteart und Laufzeit mal Anteil der noch offenen Monate',
    return_logistics: 'Mittel der Geräteart und Laufzeit, wenn noch nicht gebucht', wipe_grading: 'Mittel der Geräteart und Laufzeit, wenn noch nicht gebucht', refurbishment: 'Mittel der Geräteart und Laufzeit, wenn noch nicht gebucht',
    holding_cost: 'Mittel der Geräteart und Laufzeit minus bis heute gebucht, mindestens null',
    support: 'Satz je Gerätemonat mal noch offene Monate der Laufzeit', mdm_operations: 'Satz je Gerätemonat mal noch offene Monate, nur bei MDM-Registrierung',
    channel_fee: 'Gebührenanteil am Restwert der Geräteart und Laufzeit mal Restwertprognose am Leasingende dieses Geräts'
  };
  var TAGS = {
    sim: [{ cls: 'tag-neutral', text: 'simuliert' }],
    der: [{ cls: 'tag-neutral', text: 'abgeleitet' }],
    both: [{ cls: 'tag-neutral', text: 'simuliert' }, { cls: 'tag-neutral', text: 'abgeleitet' }]
  };

  w.RE.tco = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt, X = index(D);
    var qty = function (n) { return f.qty(n || 0); };
    var money = function (x) { if (x === null || x === undefined || isNaN(x)) return ''; return Math.abs(x) < 100 ? f.eur2(x) : f.eur(x); };
    var eur2 = function (x) { return f.eur2(x); };
    var num1 = function (x) { return f.num(x, 1); };
    var de = f.de;
    var owners = D.est_owner || {};
    var ownH = f.role(owners.holding_cost || 'CFO'), ownC = f.role(owners.channel_fee || 'Leitung Recommerce');

    /* Modell: opts.slug, sonst das heutige Standardmodell (D.quick[0]), sonst das erste in der Flotte */
    var m = null;
    if (opts.slug && X.bySlug[opts.slug] && X.bySlug[opts.slug].in_fleet) m = X.bySlug[opts.slug];
    if (!m) { var q0 = (D.quick || [])[0]; m = (q0 && X.bySlug[q0] && X.bySlug[q0].in_fleet) ? X.bySlug[q0] : (X.pool[0] || D.models[0]); }

    /* Auswahlfelder: nur Modelle, die die Simulation gekauft hat, in heutiger Reihenfolge */
    var pool = X.pool;
    var optOf = function (v) { return { value: v, label: v }; };
    var pickers = {
      fams: uniq(pool.map(function (x) { return x.family; })).map(optOf),
      oems: uniq(pool.filter(function (x) { return x.family === m.family; }).map(function (x) { return x.oem; })).map(optOf),
      models: pool.filter(function (x) { return x.family === m.family && x.oem === m.oem; }).map(function (x) { return { value: x.slug, label: x.name }; })
    };
    var firstOf = function (fm, oem) { return pool.filter(function (x) { return (!fm || x.family === fm) && (!oem || x.oem === oem); })[0] || null; };

    /* Ausstattung und Laufzeit: nur Optionen mit Geraeten; eine Auswahl ohne Geraete faellt auf "alle" zurueck, wie heute */
    var storage = (opts.storage === undefined || opts.storage === null || opts.storage === '') ? 'all' : String(opts.storage);
    var term = (opts.term === undefined || opts.term === null || opts.term === '') ? 'all' : String(opts.term);
    var slugKeys = X.keysBySlug[m.slug] || [];
    function countFor(st, t) {
      var n = 0;
      slugKeys.forEach(function (k) { var p = k.split('|'); if (st !== 'all' && p[1] !== String(st)) return; if (t !== 'all' && p[2] !== String(t)) return; var v = D.keys[k]; n += v.closed.n + v.open.n; });
      return n;
    }
    var specs = m.specs.filter(function (s) { return countFor(s.gb, 'all') > 0; });
    if (storage !== 'all' && !specs.some(function (s) { return String(s.gb) === storage; })) storage = 'all';
    var storageOpts = [{ value: 'all', label: 'alle Ausstattungen', checked: storage === 'all' }].concat(specs.map(function (s) {
      return { value: String(s.gb), label: s.label + ' (' + qty(countFor(s.gb, 'all')) + ' Geräte)', checked: storage === String(s.gb) };
    }));
    var terms = D.terms.filter(function (t) { return countFor(storage, t) > 0; });
    if (term !== 'all' && !terms.some(function (t) { return String(t) === term; })) term = 'all';
    var termOpts = [{ value: 'all', label: 'alle Laufzeiten', checked: term === 'all' }].concat(terms.map(function (t) {
      return { value: String(t), label: qty(t) + ' Monate (' + qty(countFor(storage, t)) + ' Geräte)', checked: term === String(t) };
    }));

    /* Summen der Auswahl und des ganzen Modells */
    var keys = slugKeys.filter(function (k) { var p = k.split('|'); return (storage === 'all' || p[1] === storage) && (term === 'all' || p[2] === term); });
    function agg(list) { var A = empty(); list.forEach(function (k) { addInto(A.closed, D.keys[k].closed); addInto(A.open, D.keys[k].open); }); return A; }
    var famRow = function (fm, t) { return (D.fam_term[fm] || {})[String(t)] || { n: 0, B: {}, F: 0 }; };
    function grundlage(cc, oo) {
      if (cc.n >= D.min_n) return { kind: 'closed', text: 'voll, ' + qty(cc.n) + ' abgeschlossen' };
      if (cc.n > 0) return { kind: 'closed', text: 'voll, unsicher (' + qty(cc.n) + ' abgeschlossen)' };
      if (oo.n > 0) return { kind: 'open', text: 'bis heute plus Erwartung, ' + qty(oo.n) + ' laufend' };
      return { kind: 'none', text: 'keine Geräte' };
    }

    var fam = m.fam, famL = D.fam_label[fam] || fam;
    var A = agg(keys), c = A.closed, o = A.open;
    var AA = agg(slugKeys); var nd = D.not_deployed[m.slug] || 0; var nAll = AA.closed.n + AA.open.n + nd;
    var termMeanC = c.n ? c.term_sum / c.n : null, termMeanO = o.n ? o.term_sum / o.n : null;
    var termTextO = term === 'all' ? (o.n ? num1(termMeanO) + ' Monaten (Mittel der Laufzeiten)' : '') : qty(term) + ' Monaten';
    var basisFallback = (term !== 'all' && famRow(fam, term).n < D.min_n);
    var nBasis = term === 'all' ? famRow(fam, 'all').n : (basisFallback ? famRow(fam, 'all').n : famRow(fam, term).n);
    /* Der Grundlagentext nennt die Gruppen, auf denen die laufenden Geraete der Auswahl tatsaechlich ruhen (o.basis), nie eine Gruppe ohne Geraet */
    var basisText;
    if (term === 'all') {
      var B = o.basis || {}, FT = o.fbt || {};
      var parts = D.terms.filter(function (t) { return B[String(t)]; }).map(function (t) { return qty(t) + ' Monate (' + qty(famRow(fam, t).n) + ' abgeschlossene Geräte, Grundlage für ' + qty(B[String(t)]) + ' laufende)'; });
      var fbTerms = D.terms.filter(function (t) { return FT[String(t)]; }).map(function (t) { return qty(t) + ' Monate (nur ' + qty(famRow(fam, t).n) + ' abgeschlossene, unter ' + qty(D.min_n) + ')'; });
      basisText = 'je Gerät bei seiner Laufzeit: ' + (parts.length ? parts.join(', ') : 'keine Laufzeitgruppe')
        + (B.all ? (parts.length ? '; ' : '') + 'bei ' + qty(B.all) + ' laufenden Geräten über alle Laufzeiten (' + qty(famRow(fam, 'all').n) + ' abgeschlossene Geräte), weil ihre Laufzeit zu wenige abgeschlossene hat: ' + fbTerms.join(', ') : '');
    } else {
      basisText = basisFallback
        ? 'über alle Laufzeiten (unter ' + qty(famRow(fam, term).n) + ' bei ' + qty(term) + ' Monaten, unter ' + qty(D.min_n) + '), ' + qty(nBasis) + ' abgeschlossene Geräte'
        : 'bei ' + qty(term) + ' Monaten, ' + qty(nBasis) + ' abgeschlossene Geräte';
    }
    var OT = openTotals(o);

    /* ---------- Kennzahlen: die fuenf Kacheln ---------- */
    var kpis = [];
    var kpi = function (label, value, lines, tag) { kpis.push({ label: label, value: value, lines: lines, tags: TAGS[tag] }); };
    if (nAll === 0) {
      kpi('TCO je Gerät, abgeschlossene Kreisläufe', 'keine Geräte', [
        'dieses Modell wurde in der Simulation nicht gekauft',
        'die Geräteart ' + famL + ' steht als Näherung in der Tabelle Geräteart und Laufzeit unten',
        'ohne Geräte kennt der Reiter weder Einkaufspreis noch UVP dieses Modells; die UVP steht auf dem Reiter Gerät'], 'sim');
    } else if (c.n === 0) {
      kpi('TCO je Gerät, abgeschlossene Kreisläufe', 'keine abgeschlossenen', [
        (AA.closed.n ? 'in dieser Auswahl gibt es keinen abgeschlossenen Kreislauf' : 'dieses Modell hat in der Simulation noch keinen abgeschlossenen Kreislauf') + (o.n ? '; Erwartung siehe rechts' : '; kein Gerät mit Vertrag in dieser Auswahl'),
        'keine abgeschlossenen Geräte (verkauft oder verschrottet)',
        qty(o.n) + ' laufende Geräte in dieser Auswahl'], 'sim');
    } else {
      var li = [
        'Einkaufspreis ' + money(c.p / c.n) + ' plus Kosten bis Verkauf ' + money(c.c / c.n) + ', im Mittel je Gerät',
        qty(c.n) + ' abgeschlossene Geräte (verkauft oder verschrottet)',
        'davon geschätzt ' + money(c.est / c.n) + ' je Gerät: Lagertage, Kanalgebühren ohne Gutschrift',
        'TCO je Vertragsmonat ' + eur2(c.tco_pm / c.n) + ', bei ' + num1(termMeanC) + ' Monaten Laufzeit im Mittel'];
      if (c.n < D.min_n) li.push('unsicher: ' + qty(c.n) + ' liegt unter ' + qty(D.min_n) + ' abgeschlossenen Geräten');
      kpi('TCO je Gerät, abgeschlossene Kreisläufe', money((c.p + c.c) / c.n), li, 'sim');
    }
    if (o.n === 0) {
      kpi('TCO je Gerät, laufende Kreisläufe, bis heute plus Erwartung', nAll === 0 ? 'keine Geräte' : 'keine laufenden', [
        nAll === 0 ? 'kein Gerät dieses Modells in der Simulation' : (c.n ? 'in dieser Auswahl läuft kein Kreislauf mehr; alles steht links' : 'kein Gerät mit Vertrag in dieser Auswahl'),
        'Erwartung, keine Buchung: was noch fehlt, steht in der Tabelle unten'], 'both');
    } else {
      kpi('TCO je Gerät, laufende Kreisläufe, bis heute plus Erwartung', money(OT.total / o.n), [
        'bis heute ' + money(OT.sofar / o.n) + ': Einkaufspreis ' + money(o.p / o.n) + ' plus bisherige Kosten bis Verkauf ' + money(o.c / o.n),
        'Erwartung für den Rest ' + money(OT.exp / o.n) + ', aus dem Mittel der Geräteart ' + famL + ' ' + basisText,
        qty(o.n) + ' laufende Geräte, im Mittel ' + num1(o.mb / o.n) + ' von ' + termTextO + ' abgerechnet',
        'Erwartung, keine Buchung: was noch fehlt, steht in der Tabelle unten'], 'both');
    }
    /* Kachel 3: TCO je Vertragsmonat gegen Miete je Monat */
    var useClosed = c.n >= D.min_n; var basePm = null, baseTxt = '', rateAll = (c.rate + o.rate) / ((c.n + o.n) || 1);
    if (useClosed) { basePm = c.tco_pm / c.n; baseTxt = 'abgeschlossene Kreisläufe, voll, ' + qty(c.n) + ' Geräte'; }
    else if (o.n > 0) { basePm = o.tco_pm / o.n; baseTxt = 'laufende Kreisläufe, voraussichtlich (bis heute plus Erwartung), ' + qty(o.n) + ' Geräte' + (c.n ? '; die ' + qty(c.n) + ' abgeschlossenen liegen unter ' + qty(D.min_n) : ''); }
    else if (c.n > 0) { basePm = c.tco_pm / c.n; baseTxt = 'abgeschlossene Kreisläufe, voll, aber unsicher: ' + qty(c.n) + ' unter ' + qty(D.min_n); }
    if (basePm === null) {
      kpi('TCO je Vertragsmonat gegen Miete je Monat', 'keine Geräte', ['kein Gerät mit Vertrag in dieser Auswahl', 'ohne Restwert gerechnet: der Restwert steht auf dem Reiter Gerät'], 'der');
    } else {
      kpi('TCO je Vertragsmonat gegen Miete je Monat', eur2(basePm), [
        'Grundlage: ' + baseTxt,
        'Miete je Monat ' + eur2(rateAll) + ', Mittel der Verträge dieser Auswahl (' + qty(c.n + o.n) + ' Verträge)',
        'Miete minus TCO je Vertragsmonat: ' + eur2(rateAll - basePm) + ' je Monat',
        'ohne Restwert gerechnet: der Restwert steht auf dem Reiter Gerät'], 'der');
    }
    /* Kachel 4: Anteil geschaetzter Zeilen */
    if (c.n === 0) {
      kpi('Anteil geschätzter Zeilen', nAll === 0 ? 'keine Geräte' : 'keine abgeschlossenen', [
        'ohne abgeschlossene Geräte kein Anteil; bei laufenden Geräten sind Lagertage gebucht und Belegzeilen fehlen noch',
        'Lagertage: immer geschätzt, Tage mal Lagerkosten je Tag (' + eur2(D.holding_rate) + ' je Tag, Verantwortlich ' + ownH + ')',
        qty(o.n_lines) + ' Kostenzeilen bei den laufenden Geräten dieser Auswahl (Einkaufspreis bis Kanalgebühren, ohne Mietrechnungen)'], 'sim');
    } else {
      var feeL = c.lines.channel_fee || [0, 0, 0, 0], ppL = c.lines.purchase_price || [0, 0, 0, 0];
      kpi('Anteil geschätzter Zeilen', f.pct1(c.est / (c.p + c.c)), [
        'des TCO der ' + qty(c.n) + ' abgeschlossenen Geräte dieser Auswahl stammt aus geschätzten Kostenzeilen',
        'Lagertage: immer geschätzt, Tage mal Lagerkosten je Tag (' + eur2(D.holding_rate) + ' je Tag, Verantwortlich ' + ownH + ')',
        'Kanalgebühren: geschätzt, bis die Gutschrift des Verkaufs da ist, bei ' + qty(feeL[3]) + ' Geräten (Verantwortlich ' + ownC + ')',
        'Einkaufspreis: geschätzt bis zur Stückrechnung, in der Simulation bei ' + qty(ppL[3]) + ' Geräten',
        qty(c.n_lines) + ' Kostenzeilen bei den abgeschlossenen Geräten dieser Auswahl (Einkaufspreis bis Kanalgebühren, ohne die Erlöszeilen Mieterlös und Restwert)'], 'sim');
    }
    /* Kachel 5: dieses Modell in der Flotte */
    var ndSt = D.not_deployed_st[m.slug] || {};
    var stList = m.specs.map(function (s) { var k = ndSt[String(s.gb)] || 0; return s.label + ' (' + qty(countFor(s.gb, 'all') + k) + ' Geräte' + (k ? ', davon ' + qty(k) + ' ohne Vertrag' : '') + ')'; }).join(', ');
    var tList = D.terms.map(function (t) { return qty(t) + ' Monate (' + qty(countFor('all', t)) + ' Geräte)'; }).join(', ');
    var span = D.purchase_span[m.slug] || [];
    kpi('Dieses Modell in der simulierten Flotte', qty(nAll) + ' Geräte', nAll === 0
      ? ['nicht in der Simulation gekauft', 'Ausstattungen im Katalog: ' + (m.specs.map(function (s) { return s.label; }).join(', ') || 'keine mit UVP')]
      : ['davon abgeschlossen ' + qty(AA.closed.n) + ', laufend mit Vertrag ' + qty(AA.open.n) + ', ohne Vertrag ' + qty(nd) + ' (Ersatzgeräte im Lager, ohne Laufzeit, ohne TCO je Vertragsmonat)',
        'Ausstattungen: ' + stList, 'Laufzeiten: ' + tList, 'gekauft ' + de(span[0]) + ' bis ' + de(span[1])], 'sim');

    var nppc = c.ppc_n + o.ppc_n;
    var kpiDefs = [
      { k: 'TCO', v: 'Einkaufspreis plus Kosten bis Verkauf, je Seriennummer, hier im Mittel je Gerät; die einzige Stelle im Werkzeug, an der beide Teile zu einer Zahl addiert werden, und jede Kachel nennt die zwei Teile. Gemeint sind die Kosten des Leasinghauses, nicht die Kosten des Kunden. Nutzerbetreuung und Geräteverwaltung je Gerätemonat sind als Umlage drin (geschätzt, ein Satz je Mietrechnung), siehe Kostendefinition unten.' },
      { k: 'Einkaufspreis', v: 'Rechnungspreis des Lieferanten minus Preisschutz-Gutschrift des Herstellers, falls eine kam (in dieser Auswahl bei ' + qty(nppc) + ' Geräten).' },
      { k: 'Erwartung', v: 'eine Zahl aus dem Mittel abgeschlossener Geräte der Geräteart ' + famL + ', keine Buchung; jede Zeile der Tabelle laufender Kreisläufe nennt ihre Grundlage.' }
    ];

    /* ---------- Tabelle 1: abgeschlossene Kreislaeufe je Kostenzeile ---------- */
    var closedNote = c.n
      ? (qty(c.n) + ' abgeschlossene Geräte der Auswahl. Jede Zeile ist die Summe ihrer Buchungen geteilt durch alle abgeschlossenen Geräte, auch die ohne diese Kostenart.' + (c.n < D.min_n ? ' Unsicher: ' + qty(c.n) + ' liegt unter ' + qty(D.min_n) + '.' : ''))
      : 'keine abgeschlossenen Kreisläufe für diese Auswahl; die Geräteart steht in der Tabelle Geräteart und Laufzeit unten.';
    var estText = function (lt, L) {
      var l = L[lt] || [0, 0, 0, 0];
      if (lt === 'holding_cost') return 'ja, immer (' + ownH + ')';
      if (lt === 'support' || lt === 'mdm_operations') return 'ja, immer, Umlage (' + f.role(D.alloc_owner || 'Leitung Service') + ')';
      if (lt === 'channel_fee') return 'bis Gutschrift, ' + qty(l[3]) + ' Geräte (' + ownC + ')';
      if (lt === 'purchase_price') return 'bis Stückrechnung, ' + qty(l[3]) + ' Geräte (Einkaufsleitung); minus Preisschutz-Gutschrift bei ' + qty(c.ppc_n) + ' Geräten';
      return 'nein';
    };
    var sumCost = 0;
    var closedRows = X.LINES.map(function (lt) {
      var l = c.lines[lt] || [0, 0, 0, 0]; var s = lt === 'purchase_price' ? c.p : l[0]; if (lt !== 'purchase_price') sumCost += s;
      return E.ROW([E.C(X.NAME[lt]), E.N(c.n ? money(s / c.n) : ''), E.N(c.n ? qty(l[1]) : ''), E.N(l[1] ? money(s / l[1]) : ''), E.C(estText(lt, c.lines)), E.C(X.DEF[lt].channel)]);
    });
    closedRows.push(E.ROW([E.C('Kosten bis Verkauf, Summe'), E.N(c.n ? money(sumCost / c.n) : ''), E.N(c.n ? qty(c.n) : ''), E.N(c.n ? money(sumCost / c.n) : ''), E.C('Summe der Zeilen außer Einkaufspreis'), E.C('')], { bold: true }));
    closedRows.push(E.ROW([E.C('TCO = Einkaufspreis plus Kosten bis Verkauf'), E.N(c.n ? money((c.p + sumCost) / c.n) : ''), E.N(c.n ? qty(c.n) : ''), E.N(c.n ? money((c.p + sumCost) / c.n) : ''), E.C(c.n ? 'davon geschätzt ' + money(c.est / c.n) + ' je Gerät' : ''), E.C('')], { bold: true }));
    var tClosed = E.TABLE('t-closed', 'Abgeschlossene Kreisläufe: TCO je Kostenzeile, Mittel je Gerät',
      [E.H('Kostenzeile'), E.H('je Gerät im Mittel', 1), E.H('Geräte mit Zeile', 1), E.H('je betroffenes Gerät', 1), E.H('geschätzt'), E.H('Datenkanal im Einsatz')], closedRows, {
        note: closedNote, tags: TAGS.sim,
        defs: [
          { k: 'Kostenzeile', v: 'Kostenart aus dem Geräte-Hauptbuch, benannt wie auf dem Reiter Kreislauf; die zwei fetten Zeilen sind Summen der Zeilen darüber.' },
          { k: 'je Gerät im Mittel', v: 'Summe der Zeile über die abgeschlossenen Geräte der Auswahl geteilt durch alle abgeschlossenen Geräte, auch die ohne diese Kostenart, in Euro ohne Mehrwertsteuer.' },
          { k: 'Geräte mit Zeile', v: 'wie viele der abgeschlossenen Geräte diese Kostenart überhaupt hatten, Reparatur zum Beispiel nur die reparierten; eine gebuchte Zeile zählt auch, wenn ihr Betrag null ist, etwa die Kanalgebühr beim Mitarbeiterkauf ohne Gebühr.' },
          { k: 'je betroffenes Gerät', v: 'Summe der Zeile geteilt durch Geräte mit Zeile.' },
          { k: 'geschätzt', v: 'ob die Zeile eine Buchung mit Beleg ist oder eine Schätzung mit verantwortlicher Rolle; Lagertage sind immer eine Schätzung.' },
          { k: 'Datenkanal im Einsatz', v: 'aus welcher Quelle des Bereichs Daten die Zeile im echten Betrieb kommt, wenn der Datensee des Hauses die Simulation ersetzt.' }
        ]
      });

    /* ---------- Tabelle 2: laufende Kreislaeufe ---------- */
    var openNote = o.n
      ? (qty(o.n) + ' laufende Geräte der Auswahl (beim Kunden, in Rückgabe, in Aufbereitung oder verkaufsfähig im Lager), im Mittel ' + num1(o.mb / o.n) + ' von ' + termTextO + ' abgerechnet. Erwartung = Mittel der abgeschlossenen Geräte der Geräteart ' + famL + ' ' + basisText + (term === 'all' ? '; bei „alle Laufzeiten“ je Gerät mit seiner Laufzeit gerechnet, dann gemittelt' : '') + '.')
      : (nAll === 0 ? 'kein Gerät dieses Modells in der Simulation.' : 'keine laufenden Kreisläufe in dieser Auswahl.');
    var sSofar = 0, sExp = 0;
    var openRows = X.LINES.map(function (lt) {
      var l = o.lines[lt] || [0, 0]; var s = lt === 'purchase_price' ? o.p : l[0]; var e = o.exp[lt] || 0; if (lt !== 'purchase_price') { sSofar += s; sExp += e; }
      return E.ROW([E.C(X.NAME[lt]), E.N(o.n ? money(s / o.n) : ''), E.N(o.n ? money(e / o.n) : ''), E.N(o.n ? money((s + e) / o.n) : ''), E.C(GB[lt] + (lt === 'channel_fee' && o.n ? ' (' + qty(o.fee_open) + ' ohne Gutschrift)' : ''))]);
    });
    openRows.push(E.ROW([E.C('Kosten bis Verkauf, Summe'), E.N(o.n ? money(sSofar / o.n) : ''), E.N(o.n ? money(sExp / o.n) : ''), E.N(o.n ? money((sSofar + sExp) / o.n) : ''), E.C('Summe der Zeilen außer Einkaufspreis')], { bold: true }));
    openRows.push(E.ROW([E.C('TCO = Einkaufspreis plus Kosten bis Verkauf'), E.N(o.n ? money((o.p + sSofar) / o.n) : ''), E.N(o.n ? money(sExp / o.n) : ''), E.N(o.n ? money((o.p + sSofar + sExp) / o.n) : ''), E.C('voraussichtlich; Einkaufspreis plus bis heute plus Erwartung')], { bold: true }));
    /* Pruefsatz: Erwartung dieses Reiter gegen die Erwartung des Geraete-Hauptbuchs. Er vergleicht die Tabelle der
       laufenden Kreislaeufe und steht deshalb als foot unter ihr, nicht als calcnote unter den Kennzahlen. */
    var openFoot = '';
    if (o.n) {
      var ledger = (o.erc + o.fee_exp) / o.n, tab = sExp / o.n, diff = tab - ledger;
      openFoot = 'Zum Vergleich erwartet das Geräte-Hauptbuch selbst für diese Geräte im Mittel ' + money(o.erc / o.n) + ' Restkosten ohne Kanalgebühren plus ' + money(o.fee_exp / o.n) + ' Kanalgebühren (Regel in docs/LEDGER.md, Geräteart ohne Laufzeit, Mindeststückzahl ' + qty(D.min_n) + ', Kanal des Geräts oder Marktplatz); die Erwartung dieses Reiter (' + money(tab) + ') liegt ' + money(Math.abs(diff)) + ' ' + (diff >= 0 ? 'darüber' : 'darunter') + '. Eine Abweichung ist ein Befund über zwei Regeln, kein Fehler einer Zahl.';
    }
    var tOpen = E.TABLE('t-open', 'Laufende Kreisläufe: bis heute, Erwartung, voraussichtlich',
      [E.H('Kostenzeile'), E.H('bis heute', 1), E.H('Erwartung', 1), E.H('bis heute plus Erwartung', 1), E.H('Grundlage der Erwartung')], openRows, {
        note: openNote, foot: openFoot, tags: [{ cls: 'tag-neutral', text: 'simuliert' }, { cls: 'tag-neutral', text: 'Erwartung abgeleitet' }],
        defs: [
          { k: 'Kostenzeile', v: 'Kostenart aus dem Geräte-Hauptbuch, benannt wie auf dem Reiter Kreislauf; die zwei fetten Zeilen sind Summen der Zeilen darüber.' },
          { k: 'bis heute', v: 'Summe der bis zum Stichtag gebuchten Zeilen dieser Kostenart geteilt durch alle laufenden Geräte der Auswahl, Euro ohne Mehrwertsteuer.' },
          { k: 'Erwartung', v: 'was für den Rest des Kreislaufs noch erwartet wird, aus dem Mittel der abgeschlossenen Geräte derselben Geräteart und Laufzeit; eine Erwartung, keine Buchung, und nie eine Zahl aus einer Buchung.' },
          { k: 'bis heute plus Erwartung', v: 'die Summe der beiden Spalten links; bei einem Verkauf heute entfielen die erwarteten Mietmonate, nicht die Kosten, deshalb kein zweiter Wert.' },
          { k: 'Grundlage der Erwartung', v: 'welche Regel je Zeile die Erwartung liefert; fehlen der Geräteart und Laufzeit weniger als ' + qty(D.min_n) + ' abgeschlossene Geräte, gilt die Geräteart über alle Laufzeiten, und die Notiz über der Tabelle sagt es.' }
        ]
      });

    /* ---------- Tabelle 3: je Laufzeit und Ausstattung, alle Geraete des Modells ---------- */
    var groups = {}; slugKeys.forEach(function (k) { var p = k.split('|'); (groups[p[2]] = groups[p[2]] || {})[p[1]] = k; });
    var termRows = [], rowsT = 0, detailRows = 0;
    var pushRow = function (label1, label2, Ag, bold) {
      var cc = Ag.closed, oo = Ag.open; var g = grundlage(cc, oo); var n = cc.n + oo.n; var p = null, cst = null, tco = null, pm = null;
      if (g.kind === 'closed') { p = cc.p / cc.n; cst = cc.c / cc.n; tco = p + cst; pm = cc.tco_pm / cc.n; }
      else if (g.kind === 'open') { var t = openTotals(oo); p = oo.p / oo.n; cst = (oo.c + t.exp) / oo.n; tco = p + cst; pm = oo.tco_pm / oo.n; }
      var rate = n ? (cc.rate + oo.rate) / n : null;
      termRows.push(E.ROW([E.C(label1, { nowrap: true }), E.C(label2), E.N(money(p)), E.N(money(cst)), E.N(money(tco)), E.N(eur2(pm)), E.N(eur2(rate)), E.C(g.text)], { bold: !!bold }));
      rowsT++; if (!bold) detailRows++;
    };
    D.terms.forEach(function (t) {
      var g = groups[String(t)]; if (!g) return;
      var sts = Object.keys(g).map(Number).sort(function (a, b) { return a - b; });
      sts.forEach(function (st) { var lab = (m.specs.filter(function (s) { return s.gb === st; })[0] || {}).label || D.keys[g[st]].spec; pushRow(qty(t) + ' Monate', lab, agg([g[st]]), false); });
      pushRow(qty(t) + ' Monate', 'alle Ausstattungen', agg(sts.map(function (st) { return g[st]; })), true);
    });
    if (nd) { termRows.push(E.ROW([E.C('ohne Vertrag', { nowrap: true }), E.C('alle Ausstattungen'), E.N(''), E.N(''), E.N(''), E.N(''), E.N(''), E.C(qty(nd) + ' Geräte ohne Vertrag, keine Laufzeit')])); detailRows++; }
    var termsNote = rowsT
      ? 'Alle Geräte dieses Modells, alle Ausstattungen und Laufzeiten, unabhängig von der Auswahl oben; die fette Zeile je Laufzeit rechnet über alle Ausstattungen. Einkaufspreis bis TCO je Vertragsmonat rechnen nur mit der Grundlage, die die letzte Spalte nennt, nie gemischt; Miete je Monat ist das Mittel aller Verträge der Zeile, abgeschlossene und laufende.'
      : 'kein Gerät dieses Modells mit Vertrag in der Simulation.';
    var tTerms = E.TABLE('t-terms', 'Je Laufzeit und Ausstattung',
      [E.H('Laufzeit'), E.H('Ausstattung'), E.H('Einkaufspreis', 1), E.H('Kosten bis Verkauf', 1), E.H('TCO', 1), E.H('TCO je Vertragsmonat', 1), E.H('Miete je Monat', 1), E.H('Grundlage')], termRows, {
        note: termsNote, tags: TAGS.sim,
        defs: [
          { k: 'Laufzeit', v: 'vereinbarte Vertragslaufzeit in Monaten, nicht die abgerechneten Monate.' },
          { k: 'Ausstattung', v: 'Speicher, für den Einkaufspreis und UVP gelten.' },
          { k: 'Einkaufspreis', v: 'Rechnungspreis minus Preisschutz-Gutschrift, Mittel je Gerät der Grundlage.' },
          { k: 'Kosten bis Verkauf', v: 'Fracht bis Kanalgebühren, Mittel je Gerät der Grundlage; bei laufenden Geräten bis heute plus Erwartung.' },
          { k: 'TCO', v: 'Einkaufspreis plus Kosten bis Verkauf, die zwei Spalten links addiert.' },
          { k: 'TCO je Vertragsmonat', v: 'TCO geteilt durch die Laufzeit in Monaten, je Gerät gerechnet und dann gemittelt; ein vorzeitig zurückgegebenes Gerät wird trotzdem durch die vereinbarte Laufzeit geteilt, seine abgerechneten Monate liegen darunter.' },
          { k: 'Miete je Monat', v: 'Mittel der Monatsmiete der Verträge dieser Zeile, zum Vergleich mit der Spalte links.' },
          { k: 'Grundlage', v: 'ob die Zahlen aus vollen Kreisläufen stammen oder aus bis heute plus Erwartung, und aus wie vielen Geräten; alle Geräte dieses Modells je Laufzeit und Ausstattung zählt die Kachel „Dieses Modell in der simulierten Flotte“ und die Auswahl oben, die ganze Flotte je Laufzeit der Reiter Kreislauf.' }
        ]
      });

    /* ---------- Tabelle 4: Geraeteart und Laufzeit ---------- */
    var famRows = [];
    Object.keys(D.fam_label).forEach(function (fm) {
      D.terms.concat(['all']).forEach(function (t) {
        var r = famRow(fm, t);
        famRows.push(E.ROW([
          E.C(D.fam_label[fm]), E.C(t === 'all' ? 'alle Laufzeiten' : qty(t) + ' Monate', { nowrap: true }),
          E.N(qty(r.n) + (t !== 'all' && r.n > 0 && r.n < D.min_n ? ' (unter ' + qty(D.min_n) + ')' : '')),
          E.N(money(r.p)), E.N(money(r.c)), E.N(r.p !== null && r.p !== undefined ? money(r.p + r.c) : ''), E.N(eur2(r.tco_pm))
        ], { bold: fm === fam }));
      });
    });
    var tFam = E.TABLE('t-family', 'Geräteart und Laufzeit: Vergleich und Grundlage der Erwartung',
      [E.H('Geräteart'), E.H('Laufzeit'), E.H('abgeschlossene Geräte', 1), E.H('Einkaufspreis', 1), E.H('Kosten bis Verkauf', 1), E.H('TCO', 1), E.H('TCO je Vertragsmonat', 1)], famRows, {
        note: 'Geräteart ' + famL + ' des gewählten Modells fett; die anderen Gerätearten zum Vergleich. Ein Modell ohne Geräte in der Simulation hat nur diese Zeilen als Näherung, und die Kacheln sagen das.',
        tags: TAGS.sim,
        defs: [
          { k: 'Geräteart', v: 'die Gruppe, aus der die Erwartung für laufende Geräte stammt; nicht der Hersteller.' },
          { k: 'Laufzeit', v: 'vereinbarte Laufzeit in Monaten.' },
          { k: 'abgeschlossene Geräte', v: 'abgeschlossene Geräte dieser Geräteart und Laufzeit, die Grundlage der Mittelwerte; unter ' + qty(D.min_n) + ' gilt die Zeile „alle Laufzeiten“ als Grundlage.' },
          { k: 'Einkaufspreis', v: 'Rechnungspreis minus Preisschutz-Gutschrift, Mittel je abgeschlossenem Gerät.' },
          { k: 'Kosten bis Verkauf', v: 'Fracht bis Kanalgebühren, Mittel je abgeschlossenem Gerät.' },
          { k: 'TCO', v: 'Einkaufspreis plus Kosten bis Verkauf, Mittel je abgeschlossenem Gerät.' },
          { k: 'TCO je Vertragsmonat', v: 'TCO geteilt durch die Laufzeit in Monaten, je Gerät gerechnet und dann gemittelt.' }
        ]
      });

    /* ---------- Tabelle 5: hinterlegte Kostendefinition (heute ein Klappelement, deshalb eingeklappt) ---------- */
    var defRows = D.defs.map(function (r) {
      return E.ROW([E.C(r.name), E.C(r.phase, { nowrap: true }), E.C(r.src, { minW: 240 }), E.C(r.booked), E.C(r.est, { bold: r.lt === 'holding_cost' }), E.C(f.role(r.owner), { nowrap: true })]);
    });
    var tDefs = E.TABLE('t-defs', 'Hinterlegte Kostendefinition: was im TCO drin ist, was nicht, was noch fehlt',
      [E.H('Kostenzeile'), E.H('Phase'), E.H('Herkunft'), E.H('Gebucht bei'), E.H('Geschätzt?'), E.H('Verantwortlich')], defRows, {
        note: 'Gemeint sind die Gesamtkosten des Leasinghauses je Seriennummer von der Bestellung bis zum Zahlungseingang aus dem Verkauf: Einkaufspreis plus Kosten bis Verkauf. Nicht gemeint sind Kosten des Kunden. Hinterlegt in docs/TCO_DEFINITION.md und docs/LEDGER.md.',
        tags: TAGS.sim, collapsible: true,
        defs: [
          { k: 'Kostenzeile', v: 'Kostenart des Geräte-Hauptbuchs, benannt wie auf dem Reiter Kreislauf; die ' + qty(D.defs.length) + ' Zeilen sind die Kostendefinition in docs/TCO_DEFINITION.md, Abschnitt 3, in derselben Reihenfolge.' },
          { k: 'Phase', v: 'Abschnitt des Kreislaufs, in dem die Zeile entsteht: Anschaffung, Bereitstellung, Service, Rückgabe, Wiederverkauf oder Kapital und Lager.' },
          { k: 'Herkunft', v: 'aus welchem Beleg oder Quellsystem die Zeile stammt und wie ein Sammelbetrag auf die Seriennummer verteilt wird.' },
          { k: 'Gebucht bei', v: 'welches Ereignis die Zeile auslöst, kein Datum: Rechnung, Einrichtung, Versand, Schließen des Servicefalls, Wareneingang, Fertigstellung, Ende der Lagerphase, Gutschrift des Verkaufs.' },
          { k: 'Geschätzt?', v: 'ob die Zeile eine Buchung mit Beleg ist oder eine markierte Schätzung, und bis wann die Schätzung gilt; Lagertage sind immer eine Schätzung, deshalb fett.' },
          { k: 'Verantwortlich', v: 'die Rolle, die den Schätzwert festlegt und ändert; leer, wenn die Zeile nie geschätzt wird.' }
        ]
      });

    /* ---------- Klappbloecke: die drei Hinweislisten unter der Kostendefinition ---------- */
    var item = function (text) { return { lead: '', text: text }; };
    var blocks = [
      { key: 't-notin', title: 'Nicht drin, mit Absicht', ordered: false, items: [
        item('Softwarelizenzen und Sicherheitssoftware (Kunde)'),
        item('Produktivitätsausfälle und Schulung (Kunde)'),
        item('Abschreibungen (Managementsicht, kein Zahlungseingang und keine Zahlung)'),
        item('Kapitalkosten über die Lagerkosten je Tag hinaus'),
        item('Mieterlös und Restwert (Erlösseite, Reiter Kreislauf und Reiter Gerät)')] },
      { key: 't-missing', title: 'Noch nicht drin, obwohl Kosten des Leasinghauses', ordered: false, items: [
        item('Kapitalkosten über die Lagerkosten je Tag hinaus, und die Verwertung verschrotteter Geräte als eigene Zeile. Nutzerbetreuung und Geräteverwaltung je Gerätemonat sind seit dieser Version drin: als Umlage, ein Satz je Mietrechnung (Nutzerbetreuung ' + f.eur2(D.support_rate) + ', Geräteverwaltung ' + f.eur2(D.mdm_rate) + ' je Gerätemonat, Platzhalter ohne öffentliche Quelle, Verantwortlich ' + (D.alloc_owner || 'Leitung Service') + '), Geräteverwaltung nur für die ' + f.qty(D.n_mdm_enrolled) + ' Geräte mit MDM-Registrierung im Einrichtungsprotokoll.')] },
      { key: 't-ppc', title: 'Preisschutz-Gutschrift', ordered: false, items: [
        item('mindert auf diesem Reiter den Einkaufspreis (Begriffe); docs/TCO_DEFINITION.md Abschnitt 2 führt sie auf der Erlösseite, deshalb weicht der TCO hier bei ' + qty(D.n_ppc_fleet) + ' von ' + qty(D.n_serials) + ' Geräten der Simulation um die Gutschrift von der Summe des Hauptbuchs (Codename tco_eur) ab.')] }
    ];

    /* ---------- Kopf und Hinweise ---------- */
    var lead = 'Modell wählen, dann steht je Kostenzeile, was das Gerät bis zum Zahlungseingang aus dem Verkauf gekostet hat: bei abgeschlossenen Kreisläufen voll, bei laufenden bis heute plus Erwartung für den Rest.';
    var banner = 'Simulierte Daten, echte Mechanik. Katalog (' + qty(D.models_total) + ' Modelle, davon ' + qty(D.n_rrp) + ' mit belegter UVP und ' + qty(D.n_launch) + ' mit belegtem Verkaufsstart) und Preisbelege sind öffentlich; jede Buchung im Geräte-Hauptbuch ist simuliert (' + qty(D.n_serials) + ' Seriennummern). Lagertage sind immer eine Schätzung (Lagerkosten je Tag, Verantwortlich ' + ownH + '), Kanalgebühren bis zur Gutschrift des Verkaufs (Verantwortlich ' + ownC + '). Kein Wert ist eine Tatsache über ein reales Unternehmen. Der Datensee des Hauses ersetzt später die Simulation, Spalte für Spalte, siehe Bereich Daten.';
    var count = 'Auswahl: nur die ' + qty(D.models_in_fleet) + ' Modelle, die die Simulation gekauft hat (von ' + qty(D.models_total) + ' im Katalog); ohne Geräte gibt es keine Gesamtkosten.';

    return {
      kicker: 'TCO (Einkaufspreis plus Kosten bis Verkauf) je Gerät, Stand ' + de(D.today),
      subject: 'Was kostet uns ein Gerät insgesamt: Einkaufspreis plus Kosten bis Verkauf',
      intro: lead + ' ' + banner + ' ' + count,
      m: m, pickers: pickers, firstOf: firstOf, storageOpts: storageOpts, termOpts: termOpts,
      kpis: kpis,
      kpiDefs: kpiDefs,
      calcnote: '',
      tables: [tClosed, tOpen, tTerms, tFam, tDefs],
      blocks: blocks
    };
  };
  w.RE.tco.version = 3;
})(window);
