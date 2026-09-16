/* Restwert Engine v3, Motor FAQ (Reiter "faq", Bereich Market Intelligence). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6.
   Reine Funktion window.RE.faq(D, opts, P): baut aus data/faq.json die Fragen und Antworten, die die Recherchen zu Preisbelegen,
   Studien, Methode und Grenzen gesammelt haben. Der Text der Antworten steht in web/tools/gen/faq.json (kuratiert, mit Quellen);
   jede Zahl aus den Daten des Werkzeugs steht dort NICHT als Text, sondern als Platzhalter {fact, fmt}, den der Generator
   (make_faq_data.py) aus outputs/ fuellt und dieser Motor ueber E.fmt schreibt. Fehlt ein Wert, meldet der Motor es in der
   Konsole und schreibt es sichtbar in die Antwort, statt still eine Zahl zu erfinden. Kein DOM, kein Zustand. */
(function (w) {
  'use strict';
  var E = w.RE, f = E.fmt;

  var TAG_GROUP = { Grundlagen: 'tag-neutral', Preisbelege: 'tag-accent', Studien: 'tag-accent', Methode: 'tag-neutral', Grenzen: 'tag-accent-2', Roadmap: 'tag-accent-2' };
  var FMT = {
    qty: function (v) { return f.qty(v); },
    pct: function (v) { return f.pct(v); },
    pct1: function (v) { return f.pct1(v); },
    num: function (v) { return f.num(v); },
    num1: function (v) { return f.num(v, 1); },
    eur: function (v) { return f.eur(v); },
    de: function (v) { return f.de(v); },
    text: function (v) { return String(v); }
  };

  function isStr(x) { return typeof x === 'string' && x.length > 0; }
  function shortNum(v) { /* so kurz wie die Datei: 30.2 -> 30,2; 0.0081 -> 0,0081; 16 -> 16 */
    var r = Math.round(v * 10000) / 10000, dec = (String(r).split('.')[1] || '').length;
    return f.num(r, dec);
  }

  w.RE.faq = function (D, opts, P) {
    if (!D || !Array.isArray(D.entries)) throw new Error('faq: D.entries fehlt');
    var facts = D.facts || {}, missing = [];
    var render = function (segs) {
      return (Array.isArray(segs) ? segs : []).map(function (s) {
        if (!s) return '';
        if (isStr(s.fact)) {
          var v = facts[s.fact];
          if (v === undefined || v === null) { missing.push(s.fact); console.error('faq: Platzhalter ohne Wert: ' + s.fact); return '[Zahl fehlt: ' + s.fact + ']'; }
          var fmt = FMT[s.fmt] || FMT.text;
          return fmt(v);
        }
        return isStr(s.text) ? s.text : '';
      }).join('');
    };

    var entries = D.entries.map(function (e) { return { id: e.id, gruppe: e.gruppe || 'Grundlagen', frage: e.frage, antwort: render(e.antwort), quellen: Array.isArray(e.quellen) ? e.quellen : [] }; });
    var groups = []; entries.forEach(function (e) { if (groups.indexOf(e.gruppe) < 0) groups.push(e.gruppe); });
    var urls = {}; entries.forEach(function (e) { e.quellen.forEach(function (q) { if (isStr(q.url)) urls[q.url] = true; }); });
    var nUrls = Object.keys(urls).length;

    /* ---------- Kopf ---------- */
    var kicker = 'Market Intelligence, Stand ' + f.de(D.today);
    var subject = 'Fragen und Antworten zu Preisbelegen, Studien und Methode';
    var intro = 'Was die Recherchen zu Marktdaten ergeben haben, als Fragen, wie sie beim Lesen der Kurven entstehen. Jede Antwort nennt ihre Quellen; jede Zahl aus den Daten des Werkzeugs kommt aus dem aktuellen Lauf und nicht aus dem Text. '
      + 'Die Reiter Realisierung, Serie gegen Serie und Studien zeigen die Daten, dieser Reiter erklärt sie.';

    /* ---------- Kennzahlen ---------- */
    var kpis = [
      { label: 'Fragen', value: f.qty(entries.length), tags: [], lines: groups.map(function (g) { return g + ': ' + f.qty(entries.filter(function (e) { return e.gruppe === g; }).length); }) },
      { label: 'Quellen mit Adresse', value: f.qty(nUrls), tags: [{ cls: 'tag-neutral', text: 'öffentlich' }], lines: ['jede Adresse steht unter der Antwort, die sie trägt', 'Zahlen ohne Adresse kommen aus dem aktuellen Lauf des Werkzeugs'] },
      { label: 'Stand der Recherchen', value: f.de(D.research_date || D.today), tags: [], lines: [isStr(D.research_note) ? D.research_note : 'Preisbelege und Studien, deutsche Aufbereiter und Ankaufportale'] }
    ];
    var kpiDefs = [
      { k: 'Fragen', v: 'Anzahl der Einträge, nach Gruppe: Grundlagen (Begriffe), Preisbelege (woher die Daten kommen), Studien (was andere messen), Methode (wie gerechnet wird), Grenzen (was die Daten nicht können), Roadmap (was noch nicht gebaut ist).' },
      { k: 'Quellen mit Adresse', v: 'verschiedene Adressen, die in den Antworten zitiert werden; eine Adresse kann mehrere Antworten tragen.' }
    ];

    /* ---------- Die Fragen als Klappbloecke, je Gruppe in der Reihenfolge der Daten ---------- */
    var blocks = entries.map(function (e) {
      var items = e.quellen.map(function (q) {
        return { lead: 'Quelle:', text: (isStr(q.titel) ? q.titel : q.url) + (isStr(q.datum) ? ', ' + q.datum : ''), href: q.url, linkText: 'öffnen' };
      });
      return { key: 'faq-' + e.id, title: e.frage, ordered: false, intro: e.antwort, items: items, tag: { cls: TAG_GROUP[e.gruppe] || 'tag-neutral', text: e.gruppe } };
    });

    var tables = [];
    if (D.fact_sources && Object.keys(D.fact_sources).length) {
      var used = {}; D.entries.forEach(function (e) { (e.antwort || []).forEach(function (s) { if (s && isStr(s.fact)) used[s.fact] = true; }); });
      var rows = Object.keys(used).sort().map(function (k) {
        var v = facts[k];
        return E.ROW([E.C(k, { nowrap: true }), E.N(v === undefined || v === null ? '' : (typeof v === 'number' ? shortNum(v) : String(v))), E.C(D.fact_sources[k] || 'Herkunft nicht hinterlegt')]);
      });
      tables.push(E.TABLE('faq-facts', 'Zahlen in den Antworten und ihre Herkunft', [E.H('Platzhalter'), E.H('Wert im aktuellen Lauf', 1), E.H('Herkunft (Datei und Spalte)')], rows, {
        collapsible: true, tags: [{ cls: 'tag-neutral', text: 'aus dem Lauf' }],
        note: 'Jede Zahl, die in einer Antwort steht und aus dem Werkzeug stammt, mit dem Wert dieses Laufs und der Datei, aus der der Generator sie liest. Ändert sich der Lauf, ändern sich die Antworten mit.',
        empty: 'Keine Zahl aus dem Lauf in den Antworten.',
        defs: [
          { k: 'Platzhalter', v: 'Schlüssel, unter dem die Antwort die Zahl anfordert ({fact: ...} in web/tools/gen/faq.json).' },
          { k: 'Wert im aktuellen Lauf', v: 'der Wert, den make_faq_data.py aus outputs/ und config/ gelesen hat; so viele Nachkommastellen, wie die Datei nennt, Anteile als Anteile.' },
          { k: 'Herkunft (Datei und Spalte)', v: 'Datei unter outputs/, data/ oder config/ und die Zeile und Spalte, aus der der Wert stammt.' }
        ]
      }));
    }

    var calcnote = missing.length ? 'Platzhalter ohne Wert in diesem Lauf: ' + missing.join(', ') + '. Der Generator (web/tools/gen/make_faq_data.py) kennt diese Schlüssel nicht.' : '';

    return {
      kicker: kicker, subject: subject, intro: intro,
      kpis: kpis, kpiDefs: kpiDefs, calcnote: calcnote,
      tables: tables,
      blocks: blocks
    };
  };
  w.RE.faq.version = 3;
})(window);
