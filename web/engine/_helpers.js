/* Restwert Engine v3: Helfer fuer die Motoren. Klassisches Skript, laedt vor jedem Motor.
   Definiert window.RE = { fmt, TABLE, H, ROW, C, N }. Vertrag: v3/CONTRACT.md, Abschnitte 4 und 5. */
(function (w) {
  'use strict';
  var RE = w.RE || (w.RE = {});

  function str(t) { return t === null || t === undefined ? '' : String(t); }

  /* ---------- Zahlen, deutsch ---------- */
  var hasIntl = typeof Intl !== 'undefined' && typeof Intl.NumberFormat === 'function';
  var cache = {};
  function nf(d) {
    var k = 'd' + d;
    if (!cache[k]) cache[k] = hasIntl
      ? new Intl.NumberFormat('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: true })
      : null;
    return cache[k];
  }
  function bad(x) { return x === null || x === undefined || typeof x === 'boolean' || x === '' || isNaN(Number(x)); }
  function group(intStr) { return intStr.replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function plain(x, d) {
    /* Ersatz ohne Intl: kaufmaennisch runden, Tausenderpunkt, Komma */
    var n = Number(x), neg = n < 0, s = Math.abs(n).toFixed(d);
    if (Number(s) === 0) neg = false;
    var parts = s.split('.');
    return (neg ? '-' : '') + group(parts[0]) + (d > 0 ? ',' + parts[1] : '');
  }
  function num(x, d) {
    if (bad(x)) return '';
    d = d === undefined ? 1 : d;
    var n = Number(x);
    if (Math.abs(n) < Math.pow(10, -(d + 1))) n = 0; /* -0 und Rundungsstaub werden 0 */
    var f = nf(d);
    if (f) { var s = f.format(n); return s.charAt(0) === '−' ? '-' + s.slice(1) : s; }
    return plain(n, d);
  }
  function de(iso) {
    if (iso === null || iso === undefined || iso === '') return '';
    var p = String(iso).split('-');
    if (p.length === 3) return p[2] + '.' + p[1] + '.' + p[0];
    if (p.length === 2) return p[1] + '.' + p[0];
    return String(iso);
  }
  /* Rollen: die Konfiguration fuehrt englische Platzhalter ("Head of Recommerce (name)"), die Seite spricht deutsch;
     eine Schreibweise je Rolle auf allen Reitern (Befund der Durchsicht vom 16.09.2026) */
  var ROLE_DE = [['Head of Service Operations', 'Leitung Service'], ['Head of Recommerce', 'Leitung Recommerce'], ['Head of Indirect Procurement', 'Leitung Indirekter Einkauf'],
    ['Head of Procurement', 'Einkaufsleitung'], ['Head of Customer Success', 'Leitung Customer Success'], ['Data owner', 'Dateneigner']];
  function role(s) {
    var t = str(s).replace(/ \(name\)/g, '');
    ROLE_DE.forEach(function (r) { t = t.split(r[0]).join(r[1]); });
    return t;
  }
  var fmt = {
    de: de,
    role: role,
    qty: function (n) { return bad(n) ? '' : num(Math.round(Number(n)), 0); },
    eur: function (x) { return bad(x) ? '' : num(Math.round(Number(x)), 0) + ' €'; },
    eur2: function (x) { return bad(x) ? '' : num(x, 2) + ' €'; },
    pct: function (x) { return bad(x) ? '' : num(100 * Number(x), 0) + ' %'; },
    pct1: function (x) { return bad(x) ? '' : num(100 * Number(x), 1) + ' %'; },
    num: num
  };

  /* ---------- Tabellen ---------- */
  function cell(text, o, numeric) {
    o = o || {};
    var align = o.center ? 'center' : (o.right || numeric) ? 'right' : 'left';
    var links = Array.isArray(o.links) ? o.links.map(function (l) { return { href: str(l.href), text: str(l.text), rest: str(l.rest) }; }) : [];
    var actions = Array.isArray(o.actions) ? o.actions.map(function (a) { return { label: str(a.label), onClick: typeof a.onClick === 'function' ? a.onClick : function () {} }; }) : [];
    var bar = o.bar ? Math.max(0, Math.round(Number(o.bar))) : 0;
    /* seit 18.09.2026 (Reiter KPIs, Eigner je Kennzahl): Bedienelemente in einer Zelle (Auswahl oder Textfeld) und eine
       Pille, die nur gezeichnet wird; beides bleibt aus dem Export CSV heraus (die Huelle exportiert text, linkText, tagText, sub) */
    var controls = Array.isArray(o.controls) ? o.controls.map(function (k) {
      return {
        kind: k.kind === 'text' ? 'text' : 'select', id: str(k.id), label: str(k.label), value: str(k.value), placeholder: str(k.placeholder),
        options: Array.isArray(k.options) ? k.options.map(function (x) { return { value: str(x.value), label: str(x.label) }; }) : [],
        onChange: typeof k.onChange === 'function' ? k.onChange : function () {}
      };
    }) : [];
    return {
      text: str(text),
      align: align,
      color: o.color ? String(o.color) : (o.neg ? 'var(--color-accent-2-700)' : 'inherit'),
      neg: !!o.neg,
      weight: o.bold ? 600 : 400,
      wrap: (o.nowrap || numeric) ? 'nowrap' : 'normal',
      minW: o.minW ? Math.round(Number(o.minW)) + 'px' : '0',
      href: str(o.href),
      linkText: o.href ? (o.linkText ? str(o.linkText) : 'Quelle') : '',
      tag: o.tag ? str(o.tag) : '',
      tagText: o.tag ? str(o.tagText) : '',
      hasBar: bar > 0,
      bar: bar,
      sub: str(o.sub),
      hasLinks: links.length > 0,
      links: links,
      hasActions: actions.length > 0,
      actions: actions,
      indent: o.indent ? Math.max(0, Math.round(Number(o.indent))) : 0,
      /* seit 16.09.2026 (Cockpit-Optik): eine kleine Verlaufslinie neben dem Wert, Zahlen in Reihenfolge der Zeit */
      spark: Array.isArray(o.spark) ? o.spark.map(Number).filter(function (x) { return !isNaN(x); }) : [],
      hasSpark: Array.isArray(o.spark) && o.spark.length > 1,
      pill: o.pill ? str(o.pill) : '',
      pillCls: o.pill ? str(o.pillCls || 'tag-outline') : '',
      hasControls: controls.length > 0,
      controls: controls
    };
  }
  function C(text, o) { return cell(text, o, false); }
  function N(text, o) { return cell(text, o, true); }
  function H(text, right) { return { text: str(text), align: right ? 'right' : 'left' }; }
  function ROW(cells, o) {
    o = o || {};
    var bold = !!(o.bold || o.sum);
    return {
      cells: Array.isArray(cells) ? cells : [],
      weight: bold ? 600 : 400,
      bg: o.bg ? String(o.bg) : 'transparent',
      cursor: typeof o.onClick === 'function' ? 'pointer' : 'default',
      onClick: typeof o.onClick === 'function' ? o.onClick : undefined
    };
  }
  function TABLE(key, title, cols, rows, o) {
    o = o || {};
    cols = Array.isArray(cols) ? cols : [];
    rows = Array.isArray(rows) ? rows : [];
    var defs = Array.isArray(o.defs) ? o.defs.map(function (d) { return { k: str(d.k), v: str(d.v) }; }) : [];
    var tags = Array.isArray(o.tags) ? o.tags.map(function (t) { return { cls: str(t.cls), text: str(t.text) }; }) : [];
    var n = o.n === undefined ? rows.length : (bad(o.n) ? 0 : Number(o.n));
    return {
      key: str(key),
      title: str(title),
      cols: cols,
      rows: rows,
      n: n,
      note: str(o.note),
      defs: defs,
      hasDefs: defs.length > 0,
      collapsible: !!o.collapsible,
      hasRows: rows.length > 0,
      empty: str(o.empty),
      foot: str(o.foot),
      footLead: str(o.footLead),
      footLines: (Array.isArray(o.footLines) ? o.footLines : []).map(function (v) { return { v: str(v) }; }),
      tags: tags,
      /* seit 16.09.2026 (Cockpit-Optik): cards = eine Karte je Zeile statt Tabellenzeilen (erste Zelle Titel, zweite Text,
         Zahlenzellen rechts als Kennwerte, Markenzelle als Pille); die Broadsheet-Huelle ignoriert das Feld */
      cards: !!o.cards,
      cardHero: typeof o.cardHero === 'number' ? o.cardHero : null   /* Spaltenindex des Werts, der auf der Karte gross steht */
    };
  }

  RE.fmt = fmt; RE.TABLE = TABLE; RE.H = H; RE.ROW = ROW; RE.C = C; RE.N = N;
  RE.helpersVersion = 3;
})(window);
