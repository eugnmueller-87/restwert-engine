/* Restwert Engine v3: die Huelle. Port von v3/design.dc.html (Claude Design) nach React 18 UMD, ohne Build.
   Motoren: window.RE.<tab>(D, opts, P) nach v3/CONTRACT.md. Daten inline (#data-<tab>), sonst fetch('data/<tab>.json').
   Korrekturen gegen das Design (BRIEF Punkt 2): Bericht zuerst und als eigener Tab; kein Suchfeld auf Geraet;
   Einkaufsabschlag aus data/config.json; kein Halbgeviert- oder Geviertstrich.
   Leiste seit 16.09.2026 (Eugens Ansage): vier Bereiche in der Kopfzeile (Bericht, Analytics, Market Intelligence, Daten),
   darunter die Reiter des gewaehlten Bereichs. Ein Reiter kann die Daten eines anderen lesen (data: 'market'). */
(function (w) {
  'use strict';
  var React = w.React, ReactDOM = w.ReactDOM, h = React.createElement;
  /* Eine zweite Optik ("Skin") darf das Zeichnen uebernehmen, nie das Rechnen: window.RE_SKIN (geladen vor app.js) liefert
     render(o) und zeichnet damit die ganze Seite aus den Ansichtswerten o (renderVals); die einzelnen Haken renderLog,
     renderSectionBar, renderDeviceSettings, renderKpis, renderTables, renderDossier, renderBlocks, renderDialog greifen nur
     fuer einen Skin OHNE eigenes render(). init() reicht die Bausteine (Tag, Cell, Table, ...) an den Skin.
     Seit 16.09.2026 (Eugen: weg von der Zeitungsoptik, hin zum Werkzeug). */
  var SKIN = w.RE_SKIN || null;

  var GROUPS = [
    { key: 'report', label: 'Bericht' }, { key: 'analytics', label: 'Analytics' }, { key: 'intel', label: 'Market Intelligence' }, { key: 'lake', label: 'Daten' }
  ];
  var TABS = [
    { key: 'report', label: 'Bericht', group: 'report' },
    { key: 'device', label: 'Gerät', group: 'analytics' }, { key: 'forecast', label: 'Prognosegüte', group: 'analytics' }, { key: 'tco', label: 'TCO', group: 'analytics' },
    { key: 'cycle', label: 'Kreislauf', group: 'analytics' }, { key: 'levers', label: 'Stellschrauben', group: 'analytics' }, { key: 'term', label: 'Laufzeit', group: 'analytics' },
    { key: 'kpis', label: 'KPIs', group: 'analytics' },
    { key: 'market', label: 'Realisierung', group: 'intel' }, { key: 'series', label: 'Serie gegen Serie', group: 'intel', data: 'market' },
    { key: 'studies', label: 'Studien', group: 'intel', data: 'market' }, { key: 'faq', label: 'FAQ', group: 'intel' },
    { key: 'lake', label: 'Daten', group: 'lake' }
  ];
  var PLANNED = ['Lager', 'Verträge'];
  var OVER_LABEL = { buy: 'Einkaufspreis', rate: 'Miete', cost: 'Kosten' };
  var DEFAULT_TERMS = [12, 24, 36, 48];
  var MUTED = 'var(--ink-70)';
  var NOTE_STYLE = { margin: '4px 0 0', maxWidth: '110ch', fontSize: 12.5, lineHeight: 1.5, color: MUTED };

  function tabOf(key) { for (var i = 0; i < TABS.length; i++) if (TABS[i].key === key) return TABS[i]; return TABS[0]; }
  function dataOf(key) { return tabOf(key).data || tabOf(key).key; }
  function tabsIn(group) { return TABS.filter(function (t) { return t.group === group; }); }
  function readInline(id) {
    var el = document.getElementById(id); if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { console.error('inline', id, e); return null; }
  }
  /* Der Einkaufsabschlag hat eine Schreibweise auf der ganzen Seite: E.fmt.pct1, wie CONTRACT 7.3 sie dem Motor vorgibt */
  function pctLabel(fmt, share) { return fmt.pct1(share); }
  function whenText(when) { return when === 'launch' ? 'am Verkaufsstart' : 'heute'; }
  /* Fokussierbare Elemente eines Dialogs, in Dokumentreihenfolge */
  function focusables(root) {
    return Array.prototype.slice.call(root.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'));
  }
  /* ---------- Bausteine ---------- */
  function Tag(g, extra) { return h('span', { className: 'tag ' + g.cls, style: Object.assign({ fontSize: 10, padding: '1px 7px' }, extra || {}) }, g.text); }
  function Ghost(label, onClick, expanded, extra) {
    var p = { type: 'button', className: 'btn btn-ghost', onClick: onClick, style: Object.assign({ whiteSpace: 'nowrap', fontSize: 12.5, padding: '2px 4px' }, extra || {}) };
    if (expanded !== undefined) p['aria-expanded'] = !!expanded;
    return h('button', p, label);
  }
  function Defs(list) {
    return h('dl', { className: 'defs' }, list.map(function (d, i) {
      return h(React.Fragment, { key: i }, h('dt', null, d.k), h('dd', null, d.v));
    }));
  }
  function Field(label, id, control, style) {
    return h('div', { className: 'field', style: style }, h('label', { htmlFor: id }, label), control);
  }
  function Select(id, options, onChange) {
    var sel = '';
    options.forEach(function (o) { if (o.selected) sel = String(o.value); });
    return h('select', { className: 'input', id: id, value: sel, onChange: onChange, style: { minHeight: 32, fontSize: 13 } },
      options.map(function (o) { return h('option', { key: String(o.value), value: String(o.value) }, o.label); }));
  }
  function Seg(labelId, label, name, options, style) {
    return h('div', { className: 'field', style: style },
      h('label', { id: labelId }, label),
      h('div', { className: 'seg', role: 'radiogroup', 'aria-labelledby': labelId },
        options.map(function (o, i) {
          return h('label', { key: i, className: 'seg-opt' + (o.checked ? ' seg-on' : '') },
            h('input', { type: 'radio', name: name, checked: !!o.checked, onChange: o.select }), o.label);
        })));
  }

  /* Verlaufslinie in einer Zelle (E.N mit spark, seit 17.09.2026, Reiter KPIs): Strich in der Textfarbe, ohne Stylesheet-Bedarf */
  function CellSpark(v) {
    var W = 72, H = 20, Pd = 2, lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), span = hi - lo || 1;
    var pts = v.map(function (y, i) { return [Pd + (W - 2 * Pd) * i / (v.length - 1), Pd + (H - 2 * Pd) * (1 - (y - lo) / span)]; });
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    var last = pts[pts.length - 1];
    return h('svg', { className: 'spark', width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': true, style: { verticalAlign: 'middle', marginLeft: 6, overflow: 'visible' } },
      h('path', { d: d, fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinejoin: 'round', strokeLinecap: 'round' }),
      h('circle', { cx: last[0], cy: last[1], r: 2, fill: 'currentColor' }));
  }
  function Cell(c, i, pad, padLeft) {
    var st = { textAlign: c.align, color: c.color, fontWeight: c.weight, whiteSpace: c.wrap, minWidth: c.minW, verticalAlign: 'top', padding: pad };
    if (c.indent) st.paddingLeft = padLeft + c.indent * 16;
    return h('td', { key: i, style: st },
      c.text,
      c.href ? h(React.Fragment, null, c.text ? ' ' : null, h('a', { href: c.href, target: '_blank', rel: 'noopener', style: { color: 'var(--color-accent-700)' } }, c.linkText)) : null,
      c.tag ? h(React.Fragment, null, ' ', h('span', { className: 'tag ' + c.tag, style: { fontSize: 10, padding: '1px 7px' } }, c.tagText)) : null,
      c.hasBar ? h('span', { className: 'cell-bar', style: { width: c.bar } }) : null,
      c.hasSpark ? CellSpark(c.spark) : null,
      /* Leerzeichen vor der Unterzeile: im Textfluss (Screenreader, Kopieren) sonst "99,7 % BeispielBaseline" in einem Wort */
      c.sub ? h(React.Fragment, null, ' ', h('div', { style: { fontSize: 11.5, fontWeight: 400, lineHeight: 1.4, color: 'var(--ink-65)' } }, c.sub)) : null,
      c.hasLinks ? c.links.map(function (l, j) {
        return h('div', { key: j, style: { fontSize: 12, lineHeight: 1.4 } }, h('a', { href: l.href, target: '_blank', rel: 'noopener', style: { color: 'var(--color-accent-700)' } }, l.text), ' ', l.rest);
      }) : null,
      c.hasActions ? h('span', { style: { display: 'inline-flex', gap: 2, whiteSpace: 'nowrap' } }, c.actions.map(function (a, j) {
        return h('button', { key: j, type: 'button', className: 'btn btn-ghost', onClick: a.onClick, style: { whiteSpace: 'nowrap', fontSize: 12, padding: '1px 6px' } }, a.label);
      })) : null);
  }
  function Table(t, tableFont, pad, padLeft, extraStyle) {
    return h('div', { className: 'tablewrap' },
      h('table', { className: 'table', style: Object.assign({ fontSize: tableFont }, extraStyle || {}) },
        h('thead', null, h('tr', null, t.cols.map(function (c, i) {
          return h('th', { key: i, style: { textAlign: c.align, verticalAlign: 'bottom', lineHeight: 1.3, padding: pad, fontSize: 10.5 } }, c.text);
        }))),
        h('tbody', null, t.rows.map(function (r, i) {
          var rp = { key: i, onClick: r.onClick, style: { fontWeight: r.weight, background: r.bg, cursor: r.cursor } };
          if (r.onClick) {
            /* klickbare Zeile auch ohne Maus: Tab, dann Enter oder Leertaste */
            rp.tabIndex = 0; rp.role = 'button'; rp['aria-pressed'] = r.weight === 600;
            rp.onKeyDown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.onClick(); } };
          }
          return h('tr', rp, r.cells.map(function (c, j) { return Cell(c, j, pad, padLeft); }));
        }))));
  }

  /* ---------- Die Huelle ---------- */
  class App extends React.Component {
    constructor(props) {
      super(props);
      var cfg = readInline('data-config');
      if (!cfg || !cfg.purchase_discount_pct) console.error('config: data-config fehlt, der Einkaufsabschlag hat keinen Wert');
      this.cfg = (cfg && cfg.purchase_discount_pct) || { value: 0, owner: '', note: '', min: null, max: null };
      this.state = {
        tab: 'report', data: {}, loading: {}, failed: {}, open: {}, defs: {}, blocks: {}, kpiDefs: {}, kpiDetails: {}, introOpen: {},
        market: { family: 'Smartphone', detail: false }, dev: { id: null, term: 24, when: 'launch', over: {} }, editing: {}, draft: {},
        tco: { slug: null, storage: 'all', term: 'all' }, lever: null,
        kpi: { period: null, open: null },   /* Reiter KPIs: Zyklus (null heisst Standard aus den Daten) und die aufgeklappte Kennzahl */
        scenarios: [], manual: [], deliveries: [], thresholds: {}, assumptions: { disc: this.cfg.value }, log: [], runs: [], running: false,
        logOpen: false, dlg: null, form: {}
      };
      this.chartRef = React.createRef();
      this._loaded = {};
      this._tables = [];
    }
    remember() { return this.props.rememberSelection !== false; }
    readStore(k, def) { try { var v = JSON.parse(localStorage.getItem('restwert-' + k)); return v === null || v === undefined ? def : v; } catch (e) { return def; } }
    saveStore(k, v) { try { localStorage.setItem('restwert-' + k, JSON.stringify(v)); } catch (e) {} }
    persist(k, v) { if (!this.remember()) return; try { localStorage.setItem(k, v); } catch (e) {} }
    stamp(d) { d = d || new Date(); var p = function (n) { return String(n).padStart(2, '0'); }; return p(d.getDate()) + '.' + p(d.getMonth() + 1) + '.' + d.getFullYear() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()); }
    isoToday() { var d = new Date(), p = function (n) { return String(n).padStart(2, '0'); }; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); }

    componentDidMount() {
      var self = this, tab = this.state.tab;
      var patch = {
        scenarios: this.readStore('scenarios', []), manual: this.readStore('manual-anchors', []), deliveries: this.readStore('deliveries', []),
        thresholds: this.readStore('thresholds', {}), assumptions: this.readStore('assumptions', { disc: this.cfg.value }),
        log: this.readStore('log', []), runs: this.readStore('runs', [])
      };
      if (this.remember()) {
        try {
          var t = localStorage.getItem('restwert-tab'); if (t && TABS.some(function (x) { return x.key === t; })) { tab = t; patch.tab = t; }
          /* ein Link darf einen Reiter nennen (#tab=levers); er schlaegt den gemerkten Reiter, aendert aber nichts am Merken */
          var hs = /[#&]tab=([a-z]+)/.exec(String(w.location && w.location.hash || ''));
          if (hs && TABS.some(function (x) { return x.key === hs[1]; })) { tab = hs[1]; patch.tab = hs[1]; }
          var d = localStorage.getItem('restwert-device'); if (d) patch.dev = Object.assign({}, this.state.dev, { id: d });
          var tc = JSON.parse(localStorage.getItem('restwert-tco') || 'null'); if (tc && tc.slug) patch.tco = { slug: tc.slug, storage: tc.storage || 'all', term: tc.term || 'all' };
        } catch (e) {}
      }
      this.setState(patch);
      this.load(tab); this.load('lake'); this.load('device');
      /* Plotly misst Text beim Zeichnen; kommt die Schrift aus dem Netz erst danach, sind Legende und Achsentitel zu eng
         geschnitten. Deshalb ein zweites Zeichnen, sobald die Schriften da sind. */
      /* Plotly misst Text beim ersten Zeichnen und merkt sich die Masse; misst es mit der Ersatzschrift, bevor die Schrift aus
         dem Netz da ist, bleiben Legende und Achsentitel fuer immer zu eng geschnitten. Deshalb wird das Diagramm erst
         gezeichnet, wenn die Schrift geladen ist, spaetestens nach zwei Sekunden. */
      this._fontsReady = !(document.fonts && document.fonts.load);
      if (!this._fontsReady) {
        var famRaw = '', fam = '';
        try { famRaw = getComputedStyle(document.documentElement).getPropertyValue('--font-chart').trim(); } catch (e) {}
        fam = (famRaw || '"Source Serif 4", serif').split(',')[0].replace(/"/g, '').trim();
        var go = function () { if (self._fontsReady) return; self._fontsReady = true; self.draw(); };
        try { document.fonts.load('13px ' + fam).then(go, go); } catch (e) { go(); }
        this._ft = setTimeout(go, 2000);
      }
      var tries = 0;
      var tick = function () { if (self.readPalette()) self.forceUpdate(); else if (tries++ < 50) self._pt = setTimeout(tick, 120); };
      tick();
      /* Themenwechsel zur Laufzeit: neue Palette lesen und das Diagramm neu faerben */
      this._onTheme = function () { self.forceUpdate(); };
      try { this._mq = typeof w.matchMedia === 'function' ? w.matchMedia('(prefers-color-scheme: dark)') : null; if (this._mq && this._mq.addEventListener) this._mq.addEventListener('change', this._onTheme); } catch (e) { this._mq = null; }
      try { if (typeof w.MutationObserver === 'function') { this._mo = new w.MutationObserver(this._onTheme); this._mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] }); } } catch (e) { this._mo = null; }
    }
    componentWillUnmount() {
      clearTimeout(this._pt); clearTimeout(this._dt); clearTimeout(this._rt); clearTimeout(this._ft);
      try { if (this._mq && this._mq.removeEventListener) this._mq.removeEventListener('change', this._onTheme); } catch (e) {}
      try { if (this._mo) this._mo.disconnect(); } catch (e) {}
      if (this._onDocKey) { document.removeEventListener('keydown', this._onDocKey); this._onDocKey = null; }
    }
    componentDidUpdate(prevProps, prevState) {
      this.draw();
      if (prevState && !prevState.dlg && this.state.dlg) this.dlgOpened();
      else if (prevState && prevState.dlg && !this.state.dlg) this.dlgClosed();
    }
    /* Dialog: Fokus hinein, Escape und Tab auf Dokumentebene (auch wenn der Fokus noch auf dem Knopf dahinter sitzt),
       beim Schliessen zurueck auf den Knopf, der den Dialog geoeffnet hat */
    dlgOpened() {
      var self = this;
      this._onDocKey = function (e) {
        if (e.key === 'Escape') { e.preventDefault(); self.closeDlg(); return; }
        if (e.key !== 'Tab') return;
        var dlg = document.querySelector('#app .dialog'); if (!dlg) return;
        var f = focusables(dlg); if (!f.length) return;
        var first = f[0], last = f[f.length - 1], a = document.activeElement;
        if (!dlg.contains(a)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); return; }
        if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
      };
      document.addEventListener('keydown', this._onDocKey);
      var dlg = document.querySelector('#app .dialog');
      if (dlg && !dlg.contains(document.activeElement)) { var f = focusables(dlg); if (f.length) f[0].focus(); }
    }
    dlgClosed() {
      if (this._onDocKey) { document.removeEventListener('keydown', this._onDocKey); this._onDocKey = null; }
      var op = this._opener; this._opener = null;
      if (op && typeof op.focus === 'function' && document.contains(op)) op.focus();
    }

    load(tabKey) {
      var self = this, name = dataOf(tabKey);
      if (this._loaded[name]) return; this._loaded[name] = true;
      var j = readInline('data-' + name);
      if (j) { this.setState(function (s) { var data = Object.assign({}, s.data); data[name] = j; return { data: data }; }); return; }
      if (typeof w.fetch !== 'function') { this.setState(function (s) { var f = Object.assign({}, s.failed); f[name] = true; return { failed: f }; }); return; }
      this.setState(function (s) { var l = Object.assign({}, s.loading); l[name] = true; return { loading: l }; });
      w.fetch('data/' + name + '.json').then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
        .then(function (j2) { self.setState(function (s) { var data = Object.assign({}, s.data), l = Object.assign({}, s.loading); data[name] = j2; l[name] = false; return { data: data, loading: l }; }); })
        .catch(function (e) { console.error('load', name, e); self.setState(function (s) { var l = Object.assign({}, s.loading), f = Object.assign({}, s.failed); l[name] = false; f[name] = true; return { loading: l, failed: f }; }); });
    }
    readPalette() {
      var cs = getComputedStyle(document.documentElement); var v = function (n) { return cs.getPropertyValue(n).trim(); };
      if (!v('--color-accent')) return null;
      return {
        font: v('--font-chart') || '"Source Serif 4", serif', ink: v('--color-text'), muted: v('--color-neutral-600'), grid: v('--color-neutral-300'), line: v('--color-neutral-400'),
        accent: v('--color-accent'), accent700: v('--color-accent-700'), accent2: v('--color-accent-2'),
        oem: { Apple: v('--color-accent-700'), Samsung: v('--color-accent-2-600'), Google: v('--color-neutral-800'), Motorola: v('--color-accent-400'), Fairphone: v('--color-accent-2-400'), 'HMD Global (Nokia)': v('--color-neutral-500'), Nokia: v('--color-neutral-500'), Lenovo: v('--color-accent-2-800'), Dell: v('--color-neutral-600'), HP: v('--color-accent-900'), Microsoft: v('--color-accent-2-900') }
      };
    }
    draw() {
      var self = this, el = this.chartRef.current, spec = this._chart; if (!el || !spec) return;
      if (!this._fontsReady) return;   /* die Schrift des Diagramms ist noch nicht da; componentDidMount zeichnet nach */
      if (!w.Plotly) { clearTimeout(this._dt); this._dt = setTimeout(function () { self.draw(); }, 250); return; }
      if (this._drawn === this._chartKey && el.dataset.drawn) return;
      /* Die Legende steht unter der Zeichnung, ein Eintrag je Zeile: lange Namen werden nicht abgeschnitten (seit 16.09.2026,
         Befund der Durchsicht; vorher lag sie waagerecht oben und Plotly schnitt Namen ab). Der untere Rand waechst mit der
         Zahl der Eintraege; die Farbe der Legende bleibt die des Motors (CONTRACT 2). */
      var nLeg = (spec.traces || []).filter(function (t) { return t.showlegend !== false && t.name; }).length;
      var PLOT = 320, TOP = 24, XAXIS = 44, ROW = 20, B = XAXIS + (nLeg ? 8 + nLeg * ROW : 0) + 8, H = TOP + PLOT + B;
      el.style.height = H + 'px';
      var legend = Object.assign({}, spec.layout && spec.layout.legend, { orientation: 'v', x: 0, xanchor: 'left', y: -(XAXIS + 8) / PLOT, yanchor: 'top', traceorder: 'normal' });
      try {
        w.Plotly.react(el, spec.traces, Object.assign({}, spec.layout, { height: H, margin: { l: 52, r: 12, t: TOP, b: B }, legend: legend }), { displayModeBar: false, responsive: true });
      } catch (e) { console.error('chart', e); }
      this._drawn = this._chartKey; el.dataset.drawn = '1';
    }

    setTab(key) { this._lastIn = this._lastIn || {}; this._lastIn[tabOf(key).group] = key; this.setState({ tab: key }); this.persist('restwert-tab', key); this.load(key); try { w.scrollTo(0, 0); } catch (e) {} }
    setGroup(g) { var list = tabsIn(g); if (!list.length) return; var cur = this.state.tab, last = (this._lastIn && this._lastIn[g]) || (tabOf(cur).group === g ? cur : null); this.setTab(last && list.some(function (t) { return t.key === last; }) ? last : list[0].key); }
    selectDevice(id) { if (!id) return; this.setState(function (s) { return { dev: Object.assign({}, s.dev, { id: id, over: {} }), editing: {} }; }); this.persist('restwert-device', id); }
    setDev(patch) { this.setState(function (s) { return { dev: Object.assign({}, s.dev, patch, { over: {} }), editing: {} }; }); }
    setTco(patch) { var self = this; this.setState(function (s) { var tco = Object.assign({}, s.tco, patch); self.persist('restwert-tco', JSON.stringify(tco)); return { tco: tco }; }); }
    toggle(bucket, key, def) { this.setState(function (s) { var b = Object.assign({}, s[bucket]); b[key] = !(b[key] !== undefined ? b[key] : def); var o = {}; o[bucket] = b; return o; }); }
    addLog(kind, text, who, status, cls) { var self = this; this.setState(function (s) { var log = [{ at: self.stamp(), kind: kind, text: text, who: who || 'Sie', status: status, cls: cls || 'tag-neutral' }].concat(s.log).slice(0, 200); self.saveStore('log', log); return { log: log }; }); }
    put(k, v) { var self = this; this.setState(function () { var st = {}; st[k] = v; self.saveStore(k === 'manual' ? 'manual-anchors' : k, v); return st; }); }
    setForm(patch) { this.setState(function (s) { return { form: Object.assign({}, s.form, patch, { error: '' }) }; }); }
    openDlg(dlg, form) { this._opener = document.activeElement; this.setState({ dlg: dlg, form: form || {} }); }
    closeDlg() { this.setState({ dlg: null, form: {} }); }
    fail(msg) { this.setState(function (s) { return { form: Object.assign({}, s.form, { error: msg }) }; }); }

    startRun() {
      var self = this, fmt = w.RE.fmt;
      if (this.state.running) return; this.setState({ running: true });
      this._rt = setTimeout(function () {
        var s = self.state, rel = s.deliveries.filter(function (x) { return x.status === 'freigegeben'; }).length, th = Object.keys(s.thresholds).length;
        var run = { at: self.stamp(), anchors: s.manual.length, deliveries: rel, thresholds: th, disc: s.assumptions.disc };
        self.put('runs', [run].concat(s.runs).slice(0, 50)); self.setState({ running: false });
        self.addLog('Lauf', 'Lauf protokolliert: ' + fmt.qty(s.manual.length) + ' manuelle Preisbelege, ' + fmt.qty(rel) + ' freigegebene Lieferungen, ' + fmt.qty(th) + ' geänderte Schwellen, Einkaufsabschlag ' + pctLabel(fmt, s.assumptions.disc) + ' einbezogen', 'Sie', 'protokolliert', 'tag-accent');
      }, 1400);
    }
    exportCsv() {
      var T = this._tables || []; if (!T.length) return;
      var esc = function (v) { var s = String(v === null || v === undefined ? '' : v).replace(/"/g, '""'); return /[;"\n]/.test(s) ? '"' + s + '"' : s; };
      var cellText = function (c) { return [c.text, c.linkText, c.tagText, c.sub].filter(Boolean).join(' '); };
      var lines = [];
      T.forEach(function (t) { lines.push(esc(t.title)); lines.push(t.cols.map(function (c) { return esc(c.text); }).join(';')); t.rows.forEach(function (r) { lines.push(r.cells.map(function (c) { return esc(cellText(c)); }).join(';')); }); lines.push(''); });
      if (!(w.URL && typeof w.URL.createObjectURL === 'function')) { this.addLog('Export', 'Export in dieser Umgebung nicht möglich', 'Sie', 'abgebrochen', 'tag-accent-2'); return; }
      try {
        var blob = new Blob([String.fromCharCode(0xFEFF) + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
        var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'restwert-' + this.state.tab + '-' + this.isoToday() + '.csv';
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      } catch (e) { console.error('export', e); }
      this.addLog('Export', w.RE.fmt.qty(T.length) + ' Tabellen des Reiters ' + tabOf(this.state.tab).label + ' als CSV exportiert', 'Sie', 'exportiert', 'tag-neutral');
    }

    /* ---------- Ansichtswerte, eins zu eins aus renderVals() des Designs ---------- */
    renderVals() {
      var self = this, S = this.state, RE = w.RE, D = S.data[dataOf(S.tab)], P = this.readPalette() || undefined;
      var density = this.props.density || 'compact', defsDefault = (this.props.definitions || 'collapsed') === 'open';
      var cellPad = density === 'compact' ? '5px 8px' : '9px 10px', padLeft = density === 'compact' ? 8 : 10, tableFont = density === 'compact' ? 13 : 14;
      var fmt = RE.fmt;
      /* Kopfzeile: die Bereiche; darunter die Reiter des gewaehlten Bereichs, wenn er mehr als einen hat. Der aktive Reiter
         traegt aria-current="page"; ein Bereich mit nur einem Reiter traegt es selbst, ein Bereich mit mehreren "location". */
      var curGroup = tabOf(S.tab).group, subList = tabsIn(curGroup);
      var groups = GROUPS.map(function (g) {
        var list = tabsIn(g.key), isCur = g.key === curGroup;
        return { key: g.key, label: g.label, current: isCur ? (list.length > 1 ? 'location' : 'page') : undefined, onClick: function () { self.setGroup(g.key); },
          /* alle Reiter des Bereichs, fuer eine Seitenleiste (Cockpit); die zweite Kopfzeile der Broadsheet-Optik nimmt nur out.tabs */
          tabs: list.map(function (t) { return { key: t.key, label: t.label, current: t.key === S.tab ? 'page' : undefined, onClick: function () { self.setTab(t.key); } }; }) };
      });
      var tabs = subList.length > 1 ? subList.map(function (t) { return { key: t.key, label: t.label, current: t.key === S.tab ? 'page' : undefined, onClick: function () { self.setTab(t.key); } }; }) : [];
      var motor = typeof RE[S.tab] === 'function' ? RE[S.tab] : null;
      var V = null, error = '';
      try {
        if (D && motor) {
          var opts = {};
          if (S.tab === 'market') opts = Object.assign({}, S.market, { extra: S.manual, disc: S.assumptions.disc });
          else if (S.tab === 'device') opts = Object.assign({}, S.dev, { extra: S.manual });
          else if (S.tab === 'tco') opts = Object.assign({}, S.tco);
          else if (S.tab === 'lake') opts = {
            deliveries: S.deliveries,
            release: function (id) { var del = S.deliveries.map(function (x) { return x.id === id ? Object.assign({}, x, { status: 'freigegeben' }) : x; }); self.put('deliveries', del); var x = S.deliveries.find(function (y) { return y.id === id; }); if (x) self.addLog('Lieferung', x.feed + ': ' + x.fileName + ' freigegeben für den nächsten Lauf (' + fmt.qty(x.rows) + ' Zeilen)', 'Sie', 'freigegeben', 'tag-accent'); },
            remove: function (id) { var x = S.deliveries.find(function (y) { return y.id === id; }); self.put('deliveries', S.deliveries.filter(function (y) { return y.id !== id; })); if (x) self.addLog('Lieferung', x.feed + ': ' + x.fileName + ' entfernt', 'Sie', 'entfernt', 'tag-neutral'); }
          };
          else if (S.tab === 'levers') opts = { lever: S.lever, thresholds: S.thresholds, select: function (id) { self.setState({ lever: id }); } };
          else if (S.tab === 'kpis') opts = {
            period: S.kpi.period, open: S.kpi.open,
            setPeriod: function (p) { self.setState(function (s) { return { kpi: Object.assign({}, s.kpi, { period: p }) }; }); },
            toggle: function (nr) { self.setState(function (s) { return { kpi: Object.assign({}, s.kpi, { open: s.kpi.open === nr ? null : nr }) }; }); }
          };
          V = motor(D, opts, P);
          if (!V || typeof V !== 'object') { V = null; error = 'Der Motor für diesen Reiter hat kein Ansichtsmodell geliefert.'; }
        }
      } catch (e) { console.error('compute', e); V = null; error = 'Der Motor für diesen Reiter hat einen Fehler gemeldet: ' + (e && e.message ? e.message : String(e)); }
      if (!motor) error = 'Motor für diesen Reiter fehlt (engine/' + S.tab + '.js).';
      if (S.failed[dataOf(S.tab)]) error = 'Daten für diesen Reiter konnten nicht geladen werden.';

      var L = S.data.lake, lastRun = S.runs[0];
      var status = [];
      status.push({ k: 'Letzter Lauf', v: lastRun ? lastRun.at : (L ? 'Stand ' + fmt.de(L.today) : '…') });
      if (L) {
        status.push({ k: 'Dateien', v: fmt.qty(L.tot.files + S.deliveries.filter(function (x) { return x.status === 'freigegeben'; }).length) });
        status.push({ k: 'Zeilen', v: fmt.qty(L.tot.read) }); status.push({ k: 'ungeklärt', v: fmt.qty(L.tot.unresolved) }); status.push({ k: 'Seriennummern', v: fmt.qty(L.serials) });
      }
      var pending = S.manual.length + S.deliveries.filter(function (x) { return x.status === 'freigegeben'; }).length + Object.keys(S.thresholds).length;
      if (pending) status.push({ k: 'offen für den nächsten Lauf', v: fmt.qty(pending) });
      var planned = PLANNED.map(function (l) { return { label: l, title: 'Als Nächstes geplant' }; });

      var out = {
        groups: groups, tabs: tabs, hasTabs: tabs.length > 0, planned: planned, pending: pending, status: status, cellPad: cellPad, padLeft: padLeft, tableFont: tableFont,
        isDevice: S.tab === 'device', isMarket: S.tab === 'market', isTco: S.tab === 'tco', isKpis: S.tab === 'kpis', periodOpts: [],
        loading: !V && !error, ready: !!V, error: error,
        kicker: '', subject: '', intro: '', introOpen: false, introLabel: 'Hinweise', toggleIntro: function () { self.toggle('introOpen', S.tab, false); },
        facts: [], hasFacts: false, hasKpis: false, kpis: [], hasMoreKpi: false, kpiDetailsLabel: 'Herleitung', kpiDetailsOpen: false, toggleKpiDetails: function () {},
        hasKpiDefs: false, kpiDefs: [], kpiDefsOpen: false, kpiDefsLabel: 'Begriffe', toggleKpiDefs: function () {},
        hasChart: false, chartNote: '', tables: [], hasBlocks: false, blocks: [], hasSteps: false, steps: [], hasActions: false, actions: [], calcnote: '', hasDossier: false, dossier: null,
        aFams: [], aOems: [], aModels: [], aSpecs: [],
        devFams: [], devOems: [], devModels: [], devSpecs: [], termOpts: [], whenOpts: [], famOpts: [], viewOpts: [], tcoFams: [], tcoOems: [], tcoModels: [], storageOpts: [], tcoTermOpts: [],
        hasOverride: false, buyValue: '', rateValue: '', costValue: '', buyBorder: 'var(--color-divider)', rateBorder: 'var(--color-divider)', costBorder: 'var(--color-divider)',
        sectionActions: [], hasSectionActions: false,
        logOpen: S.logOpen, toggleLog: function () { self.setState(function (s) { return { logOpen: !s.logOpen }; }); }, protokollLabel: 'Protokoll (' + fmt.qty(S.log.length) + ')', log: S.log, hasLog: S.log.length > 0, noLog: S.log.length === 0,
        exportCsv: function () { self.exportCsv(); }, startRun: function () { self.startRun(); }, running: S.running, runLabel: S.running ? 'Lauf läuft …' : 'Lauf starten',
        openDelivery: function () { self.openDlg('delivery', { feed: (L && L.feeds[0]) ? L.feeds[0].name : '' }); },
        dlgOpen: !!S.dlg, dlgAnchor: S.dlg === 'anchor', dlgDelivery: S.dlg === 'delivery', dlgThreshold: S.dlg === 'threshold', dlgAssumption: S.dlg === 'assumption', dlgScenario: S.dlg === 'scenario',
        dlgTitle: '', dlgKicker: '', dlgHint: '', dlgSubmit: 'Speichern', form: S.form, closeDlg: function () { self.closeDlg(); }, onBackdrop: function () { self.closeDlg(); }, stop: function (e) { e.stopPropagation(); }, onDlgKey: function (e) { if (e.key === 'Escape') { e.stopPropagation(); self.closeDlg(); } }, submitDlg: function () {}, feedOpts: [],
        discMin: this.cfg.min, discMax: this.cfg.max
      };

      /* Dialoge (unabhaengig vom Bereich) */
      var f = S.form; var setF = function (k) { return function (e) { var p = {}; p[k] = e.target.value; self.setForm(p); }; };
      if (S.dlg === 'anchor') {
        /* Das Geraet kommt aus den vier Auswahlfeldern des Katalogs (Geraeteart, Hersteller, Modell, Ausstattung), wie auf
           dem Tab Geraet (BRIEF, Korrektur a): kein Freitext, kein Abgleich einer getippten Bezeichnung. Der Motor
           Geraet liefert Auswahl und Geraet fuer f.devId (CONTRACT 7.2: pickers, firstOf, d). */
        var dev = S.data.device, DV = null;
        if (dev) { try { DV = RE.device(dev, { id: f.devId || null }, P); } catch (e) { console.error('anchor', e); } }
        var d0 = DV ? DV.d : null;
        var markA = function (list, cur) { return (list || []).map(function (o) { return Object.assign({}, o, { selected: String(o.value) === String(cur) }); }); };
        var price = parseFloat(String(f.price || '').replace(',', '.'));
        Object.assign(out, {
          dlgTitle: 'Preisbeleg erfassen', dlgKicker: 'öffentlicher Preis mit Quelle', dlgHint: 'Eine Zeile je Preis: Gerät, Zustand, Preis, Datum, Adresse der Seite. Der Beleg zählt sofort in Diagramm und Tabellen; die Kurven werden erst beim nächsten Lauf neu gerechnet.', dlgSubmit: 'Beleg speichern',
          setKind: setF('kind'), setGrade: setF('grade'), setPrice: setF('price'), setDate: setF('date'), setCondition: setF('condition'), setUrl: setF('url'),
          aFams: d0 ? markA(DV.pickers.fams, d0.family) : [], aOems: d0 ? markA(DV.pickers.oems, d0.oem) : [], aModels: d0 ? markA(DV.pickers.models, d0.model) : [], aSpecs: d0 ? markA(DV.pickers.specs, d0.id) : [],
          onAFamily: function (e) { var x = DV ? DV.firstOf(e.target.value, null, null) : null; if (x) self.setForm({ devId: x.id }); },
          onAOem: function (e) { var x = DV && d0 ? DV.firstOf(d0.family, e.target.value, null) : null; if (x) self.setForm({ devId: x.id }); },
          onAModel: function (e) { var x = DV && d0 ? DV.firstOf(d0.family, d0.oem, e.target.value) : null; if (x) self.setForm({ devId: x.id }); },
          onASpec: function (e) { self.setForm({ devId: e.target.value }); },
          form: Object.assign({}, f, {
            kind: f.kind || 'refurbished-marktplatz', grade: f.grade || 'B', date: f.date || this.isoToday(),
            devInfo: d0 ? (d0.oem + ', ' + d0.family + ' · UVP ' + fmt.eur(d0.rrp) + ' · Verkaufsstart ' + fmt.de(d0.launch)) : 'Der Gerätekatalog ist noch nicht geladen.',
            preview: d0 && price > 0 ? (fmt.eur(price) + ' geteilt durch UVP ' + fmt.eur(d0.rrp) + ' = ' + fmt.pct(price / d0.rrp)) : ''
          }),
          submitDlg: function () {
            if (!d0 || !(price > 0)) { self.fail('Gerät aus dem Katalog und ein Preis über null sind nötig.'); return; }
            var date = f.date || self.isoToday(); var age = Math.round((new Date(date) - new Date(d0.launch)) / (365.25 / 12 * 86400000) * 10) / 10; var tradein = f.kind === 'ankauf-trade-in';
            var a = { id: 'm' + Date.now(), slug: d0.slug, model_name: d0.model, oem: d0.oem, family: d0.family, spec_used: d0.spec, condition: f.condition || (tradein ? 'Ankauf „bis zu“' : ''), grade: tradein ? 'TRADEIN' : (f.grade || 'B'), age_months: age, rrp_eur_launch_de: d0.rrp, price_eur: price, realisation: Math.round(price / d0.rrp * 10000) / 10000, source_kind: tradein ? 'ankauf-trade-in' : 'refurbished-marktplatz', source_url: f.url || '', date_seen: date, manual: true };
            self.put('manual', S.manual.concat([a]));
            self.addLog('Preisbeleg', d0.label + ': ' + fmt.eur(price) + ' (' + (a.grade === 'TRADEIN' ? 'Ankauf-Gebot' : 'Stufe ' + a.grade) + '), ' + fmt.pct(a.realisation) + ' der UVP, ' + fmt.de(date), 'Sie', 'erfasst', 'tag-accent-2');
            self.closeDlg();
          }
        });
      }
      if (S.dlg === 'delivery') {
        var feeds = L ? L.feeds : [];
        Object.assign(out, {
          dlgTitle: 'Datei einlesen', dlgKicker: 'Probelauf ohne Spuren', dlgHint: 'Die Datei wird gelesen und gezählt, nichts wird geschrieben. Freigegeben wird sie im Bereich Daten; erst der nächste Lauf prüft Schlüssel und übernimmt Zeilen.', dlgSubmit: 'Probelauf ablegen',
          feedOpts: feeds.map(function (x) { return { value: x.name, label: x.name + ' · ' + x.system, selected: x.name === f.feed }; }), setFeed: setF('feed'),
          onFile: function (e) {
            var file = e.target.files && e.target.files[0]; if (!file) return;
            var read = typeof file.text === 'function' ? file.text() : new Promise(function (res, rej) { var r = new FileReader(); r.onload = function () { res(String(r.result || '')); }; r.onerror = function () { rej(r.error); }; r.readAsText(file); });
            read.then(function (t) {
              var lines = t.split(/\r?\n/).filter(function (l) { return l.trim(); }); var header = lines[0] || ''; var sep = header.indexOf(';') >= 0 ? ';' : (header.indexOf('\t') >= 0 ? '\t' : ',');
              var cols = header.split(sep).map(function (s) { return s.trim().replace(/^"|"$/g, ''); }).filter(Boolean);
              self.setForm({ fileName: file.name, rows: Math.max(0, lines.length - 1), cols: cols, size: file.size });
            });
          },
          form: Object.assign({}, f, { rowsText: f.rows !== undefined ? fmt.qty(f.rows) + ' Datenzeilen (ohne Kopfzeile)' : '', colsText: f.cols ? fmt.qty(f.cols.length) + ': ' + f.cols.join(', ') : '' }),
          submitDlg: function () {
            if (!f.fileName) { self.fail('Bitte eine Datei wählen.'); return; }
            var x = { id: 'd' + Date.now(), feed: f.feed || (feeds[0] ? feeds[0].name : ''), fileName: f.fileName, rows: f.rows || 0, cols: f.cols || [], at: self.stamp(), status: 'Probelauf' };
            self.put('deliveries', S.deliveries.concat([x]));
            self.addLog('Lieferung', x.feed + ': ' + x.fileName + ' im Probelauf gelesen, ' + fmt.qty(x.rows) + ' Zeilen, ' + fmt.qty(x.cols.length) + ' Spalten', 'Sie', 'Probelauf', 'tag-accent-2');
            self.closeDlg(); if (S.tab !== 'lake') self.setTab('lake');
          }
        });
      }
      if (S.dlg === 'threshold') {
        Object.assign(out, {
          dlgTitle: 'Schwelle ändern', dlgKicker: f.lever, dlgHint: 'Die Schwelle ist der Wert, ab dem die Regel reagiert. Die Änderung gilt ab dem nächsten Lauf und wird mit Rolle und Begründung protokolliert.', dlgSubmit: 'Schwelle speichern',
          setText: setF('text'), setNote: setF('note'), setBy: setF('by'),
          submitDlg: function () {
            if (!String(f.text || '').trim()) { self.fail('Bitte die neue Schwelle nennen.'); return; }
            var th = Object.assign({}, S.thresholds); th[f.leverId] = { text: f.text.trim(), note: (f.note || '').trim(), by: (f.by || '').trim() || 'ohne Rolle', at: self.stamp() };
            self.put('thresholds', th);
            self.addLog('Schwelle', f.lever + ': „' + f.text.trim() + '“ (vorher: ' + f.current + ')', (f.by || '').trim() || 'ohne Rolle', 'ab nächstem Lauf', 'tag-accent-2');
            self.closeDlg();
          }
        });
      }
      if (S.dlg === 'assumption') {
        var cfg = this.cfg, hasMin = typeof cfg.min === 'number', hasMax = typeof cfg.max === 'number';
        Object.assign(out, {
          dlgTitle: 'Annahme ändern', dlgKicker: 'Einkaufsabschlag',
          dlgHint: 'Der Einkaufspreis eines Leasinghauses ist nicht öffentlich; der Abschlag auf die UVP ist eine Annahme mit verantwortlicher Rolle (config/assumptions.yaml' + (cfg.owner ? ', Verantwortlich ' + cfg.owner : '') + '). Er rechnet die Spalte „gegen Einkaufspreis“ um.',
          dlgSubmit: 'Annahme speichern', setDisc: setF('disc'), setBy: setF('by'),
          submitDlg: function () {
            var v = parseFloat(String(f.disc || '').replace(',', '.'));
            var lo = hasMin ? cfg.min * 100 : 0, hi = hasMax ? cfg.max * 100 : 100;
            var ok = isFinite(v) && v >= lo && (hasMax ? v <= hi : v < hi);
            if (!ok) {
              self.fail(hasMin || hasMax
                ? 'Bitte einen Abschlag zwischen ' + fmt.num(lo, 0) + ' und ' + fmt.num(hi, 0) + ' Prozent eingeben.'
                : 'Bitte den Abschlag als Anteil der UVP in Prozent eingeben.');
              return;
            }
            self.put('assumptions', Object.assign({}, S.assumptions, { disc: v / 100 }));
            self.addLog('Annahme', 'Einkaufsabschlag auf ' + pctLabel(fmt, v / 100) + ' gesetzt (vorher ' + pctLabel(fmt, S.assumptions.disc) + ')', (f.by || '').trim() || 'ohne Rolle', 'wirksam', 'tag-accent');
            self.closeDlg();
          }
        });
      }
      if (S.dlg === 'scenario') {
        Object.assign(out, {
          dlgTitle: 'Szenario speichern', dlgKicker: 'Gerät, Laufzeit, Eingaben', dlgHint: '', dlgSubmit: 'Szenario speichern', setName: setF('name'),
          submitDlg: function () {
            var name = String(f.name || '').trim() || f.defaultName;
            var sc = { id: 's' + Date.now(), name: name, at: self.stamp(), dev: { id: f.devId, term: f.term, when: f.when, over: f.over || {} } };
            self.put('scenarios', S.scenarios.concat([sc])); self.addLog('Szenario', '„' + name + '“ gespeichert', 'Sie', 'gespeichert', 'tag-accent'); self.closeDlg();
          }
        });
      }

      if (!V) { this._chart = null; this._tables = []; return out; }

      /* Allgemeine Felder des Ansichtsmodells */
      out.kicker = V.kicker || tabOf(S.tab).label; out.subject = V.subject || ''; out.intro = V.intro || '';
      out.introOpen = !!S.introOpen[S.tab]; out.introLabel = out.introOpen ? 'Hinweise ausblenden' : 'Hinweise';
      if (Array.isArray(V.facts) && V.facts.length) { out.facts = V.facts; out.hasFacts = true; }
      out.calcnote = V.calcnote || '';
      out.tablesLast = !!V.tablesLast;   /* Tabellen hinter den Klappbloecken (FAQ: erst die Fragen, dann die Herkunft der Zahlen) */
      var detail = !!S.kpiDetails[S.tab];
      out.kpis = (V.kpis || []).map(function (k) { var lines = (k.lines || []).filter(Boolean); return Object.assign({}, k, { tags: k.tags || [], color: k.neg ? 'var(--color-accent-2-700)' : 'var(--color-text)', lines: (detail ? lines : lines.slice(0, 1)).map(function (v) { return { v: v }; }) }); });
      out.hasKpis = out.kpis.length > 0;
      out.hasMoreKpi = (V.kpis || []).some(function (k) { return (k.lines || []).filter(Boolean).length > 1; });
      out.kpiDetailsOpen = detail; out.kpiDetailsLabel = detail ? 'Weniger' : 'Herleitung'; out.toggleKpiDetails = function () { self.toggle('kpiDetails', S.tab, false); };
      if (V.kpiDefs && V.kpiDefs.length) {
        var kOpen = S.kpiDefs[S.tab] !== undefined ? S.kpiDefs[S.tab] : defsDefault;
        Object.assign(out, { hasKpiDefs: true, kpiDefs: V.kpiDefs, kpiDefsOpen: kOpen, kpiDefsLabel: kOpen ? 'Begriffe ausblenden' : 'Begriffe', toggleKpiDefs: function () { self.toggle('kpiDefs', S.tab, defsDefault); } });
      }
      var shape = function (t) {
        var dOpen = S.defs[t.key] !== undefined ? S.defs[t.key] : defsDefault; var rOpen = !t.collapsible || !!S.open[t.key];
        return Object.assign({}, t, {
          tags: t.tags || [], count: t.n ? fmt.qty(t.n) + ' Zeilen' : '', defsOpen: dOpen && t.hasDefs, defsLabel: dOpen ? 'Begriffe ausblenden' : 'Begriffe',
          toggleDefs: function () { self.toggle('defs', t.key, defsDefault); }, rowsOpen: rOpen, rowsLabel: rOpen ? 'Zeilen ausblenden' : fmt.qty(t.rows.length) + ' Zeilen zeigen',
          toggleRows: function () { self.toggle('open', t.key, false); }, showEmpty: !t.hasRows && !!t.empty
        });
      };
      var tables = (V.tables || []);
      out.blocks = (V.blocks || []).map(function (b) { var open = !!S.blocks[b.key]; return Object.assign({}, b, { open: open, caret: open ? '▾' : '▸', toggle: function () { self.toggle('blocks', b.key, false); }, intro: b.intro || '', items: b.items || [], ordered: !!b.ordered, unordered: !b.ordered, hasTag: !!b.tag, tagCls: b.tag ? b.tag.cls : '', tagText: b.tag ? b.tag.text : '' }); });
      out.hasBlocks = out.blocks.length > 0;
      if (V.chart) { out.hasChart = true; out.chartNote = V.chartNote || ''; this._chart = V.chart; this._chartKey = S.tab + '|' + JSON.stringify([S.market, S.dev, S.tco, S.lever, S.manual.length, S.assumptions.disc, P ? P.accent + P.ink : '']); } else this._chart = null;
      if (V.steps && V.steps.length) { out.steps = V.steps; out.hasSteps = true; }
      if (V.actions && V.actions.length) { out.actions = V.actions; out.hasActions = true; }

      /* Geraet */
      if (S.tab === 'device') {
        var d = V.d, mark = function (list, cur) { return (list || []).map(function (o) { return Object.assign({}, o, { selected: String(o.value) === String(cur) }); }); };
        out.devFams = mark(V.pickers.fams, d.family); out.devOems = mark(V.pickers.oems, d.oem); out.devModels = mark(V.pickers.models, d.model); out.devSpecs = mark(V.pickers.specs, d.id);
        out.onDevFamily = function (e) { var x = V.firstOf(e.target.value, null, null); if (x) self.selectDevice(x.id); };
        out.onDevOem = function (e) { var x = V.firstOf(d.family, e.target.value, null); if (x) self.selectDevice(x.id); };
        out.onDevModel = function (e) { var x = V.firstOf(d.family, d.oem, e.target.value); if (x) self.selectDevice(x.id); };
        out.onDevSpec = function (e) { self.selectDevice(e.target.value); };
        var terms = Array.isArray(V.terms) && V.terms.length ? V.terms : DEFAULT_TERMS;
        out.termOpts = terms.map(function (t) { return { label: fmt.qty(t) + ' Monate', checked: t === V.term, select: function () { self.setDev({ term: t }); } }; });
        out.whenOpts = [['launch', 'am Verkaufsstart'], ['today', 'heute']].map(function (x) { return { label: x[1], checked: x[0] === V.when, select: function () { self.setDev({ when: x[0] }); } }; });
        var field = function (k, digits) {
          var base = V.inputs[k], baseText = digits ? Number(base).toFixed(digits) : String(Math.round(Number(base)));
          var val = S.editing[k] ? (S.draft[k] !== undefined ? S.draft[k] : '') : baseText; var K = k[0].toUpperCase() + k.slice(1);
          out[k + 'Value'] = val; out[k + 'Border'] = V.overridden[k] ? 'var(--color-accent)' : 'var(--color-divider)';
          out['onFocus' + K] = function () { self.setState(function (s) { var ed = Object.assign({}, s.editing), dr = Object.assign({}, s.draft); ed[k] = true; dr[k] = baseText; return { editing: ed, draft: dr }; }); };
          out['onDraft' + K] = function (e) { var v = e.target.value; self.setState(function (s) { var dr = Object.assign({}, s.draft); dr[k] = v; return { draft: dr }; }); };
          out['onBlur' + K] = function (e) { var v = parseFloat(e.target.value); self.setState(function (s) { var over = Object.assign({}, s.dev.over), ed = Object.assign({}, s.editing); if (!isNaN(v) && v !== base) over[k] = v; ed[k] = false; return { dev: Object.assign({}, s.dev, { over: over }), editing: ed }; }); };
        };
        field('buy', 0); field('rate', 2); field('cost', 0);
        out.onKeyCommit = function (e) { if (e.key === 'Enter') e.target.blur(); };
        out.hasOverride = Object.keys(S.dev.over).length > 0; out.resetOverrides = function () { self.setState(function (s) { return { dev: Object.assign({}, s.dev, { over: {} }), editing: {} }; }); };
        var ov = S.dev.over, ovText = Object.keys(ov).length ? ', Eingaben: ' + Object.keys(ov).map(function (k) { return OVER_LABEL[k] + ' ' + fmt.eur2(ov[k]); }).join(', ') : '';
        out.sectionActions = [
          { label: 'Szenario speichern', onClick: function () { self.openDlg('scenario', { name: '', defaultName: d.model + ', ' + fmt.qty(V.term) + ' Monate', summary: d.label + ', ' + fmt.qty(V.term) + ' Monate, Kauf ' + whenText(V.when) + ovText + '. Lifecycle-Marge je Gerät ' + (V.summary.margin === null ? 'keine Prognose' : fmt.eur(V.summary.margin)) + ', Kosten eingespielt ab Monat ' + V.summary.be + '.', devId: d.id, term: V.term, when: V.when, over: ov }); } },
          { label: 'Preisbeleg erfassen', onClick: function () { self.openDlg('anchor', { devId: d.id, grade: 'B', kind: 'refurbished-marktplatz', date: self.isoToday() }); } }
        ];
        if (S.scenarios.length) {
          var rows = S.scenarios.map(function (sc) {
            var s2 = null; try { s2 = RE.device(D, Object.assign({}, sc.dev, { extra: S.manual }), P).summary; } catch (e) { console.error('scenario', e); }
            var dd = V.byId[sc.dev.id];
            var ctx = (dd ? dd.label : sc.dev.id) + ' · ' + fmt.qty(sc.dev.term) + ' Monate · Kauf ' + whenText(sc.dev.when); var ovKeys = Object.keys(sc.dev.over || {});
            return RE.ROW([
              RE.C(sc.name, { bold: true, minW: 240, sub: (sc.name === ctx ? '' : ctx + ' · ') + 'gespeichert ' + sc.at + (ovKeys.length ? ' · Eingaben: ' + ovKeys.map(function (k) { return OVER_LABEL[k]; }).join(', ') : '') }),
              RE.N(s2 ? fmt.eur(s2.buy) : ''), RE.N(s2 ? fmt.eur2(s2.rate) : ''), RE.N(s2 ? fmt.eur(s2.cost) : ''), RE.N(s2 ? fmt.eur(s2.rv) : ''),
              RE.N(s2 ? (s2.margin === null ? 'keine Prognose' : fmt.eur(s2.margin)) : '', { neg: !!(s2 && s2.margin !== null && s2.margin < 0) }), RE.N(s2 ? String(s2.be) : ''),
              RE.C('', { actions: [
                { label: 'Laden', onClick: function () { self.setState({ dev: Object.assign({}, sc.dev), editing: {} }); self.persist('restwert-device', sc.dev.id); } },
                { label: 'Löschen', onClick: function () { self.put('scenarios', S.scenarios.filter(function (x) { return x.id !== sc.id; })); self.addLog('Szenario', '„' + sc.name + '“ gelöscht', 'Sie', 'gelöscht', 'tag-neutral'); } }
              ] })
            ]);
          });
          tables = [RE.TABLE('d-scen', 'Gespeicherte Szenarien', [RE.H('Szenario'), RE.H('Einkaufspreis', 1), RE.H('Miete je Monat', 1), RE.H('Kosten bis Verkauf', 1), RE.H('Restwertprognose am Ende', 1), RE.H('Lifecycle-Marge je Gerät', 1), RE.H('Kosten eingespielt ab Monat', 1), RE.H('')], rows, { note: 'Jede Zeile rechnet mit den heutigen Daten und den gespeicherten Eingaben; Laden stellt Gerät, Laufzeit, Kauf und Eingaben wieder her.' })].concat(tables);
        }
      }
      /* Realisierung */
      if (S.tab === 'market') {
        out.famOpts = (V.fams || []).map(function (fam) { return { label: fam, checked: fam === V.current, select: function () { self.setState(function (s) { return { market: Object.assign({}, s.market, { family: fam }) }; }); } }; });
        out.viewOpts = [[false, 'Kurven je Hersteller'], [true, 'Einzelne Preisbelege']].map(function (o) { return { label: o[1], checked: o[0] === !!V.detail, select: function () { self.setState(function (s) { return { market: Object.assign({}, s.market, { detail: o[0] }) }; }); } }; });
        out.sectionActions = [
          { label: 'Preisbeleg erfassen', onClick: function () {
            /* vorbelegt mit dem ersten Geraet der gewaehlten Familie; ohne Katalog bleibt es beim Standardgeraet des Motors */
            var first = null; try { var x = S.data.device ? RE.device(S.data.device, { id: null }, P).firstOf(V.current, null, null) : null; first = x ? x.id : null; } catch (e) { console.error('anchor', e); }
            self.openDlg('anchor', { devId: first, grade: 'B', kind: 'refurbished-marktplatz', date: self.isoToday() });
          } },
          { label: 'Annahme: Einkaufsabschlag ' + pctLabel(fmt, S.assumptions.disc), onClick: function () { self.openDlg('assumption', { disc: String(Math.round(S.assumptions.disc * 1000) / 10), by: '' }); } }
        ];
      }
      /* TCO */
      if (S.tab === 'tco') {
        var m = V.m, markT = function (list, cur) { return (list || []).map(function (o) { return Object.assign({}, o, { selected: String(o.value) === String(cur) }); }); };
        out.tcoFams = markT(V.pickers.fams, m.family); out.tcoOems = markT(V.pickers.oems, m.oem); out.tcoModels = markT(V.pickers.models, m.slug);
        out.onTcoFamily = function (e) { var x = V.firstOf(e.target.value, null); if (x) self.setTco({ slug: x.slug, storage: 'all', term: 'all' }); };
        out.onTcoOem = function (e) { var x = V.firstOf(m.family, e.target.value); if (x) self.setTco({ slug: x.slug, storage: 'all', term: 'all' }); };
        out.onTcoModel = function (e) { self.setTco({ slug: e.target.value, storage: 'all', term: 'all' }); };
        out.storageOpts = (V.storageOpts || []).map(function (o) { return Object.assign({}, o, { select: function () { self.setTco({ storage: o.value }); } }); });
        out.tcoTermOpts = (V.termOpts || []).map(function (o) { return Object.assign({}, o, { select: function () { self.setTco({ term: o.value }); } }); });
      }
      /* Stellschrauben */
      if (S.tab === 'levers') {
        var ds = V.dossier; tables = V.overview ? [V.overview] : [];
        if (ds) {
          out.hasDossier = true;
          out.dossier = Object.assign({}, ds, { sumColor: ds.sumNeg ? 'var(--color-accent-2-700)' : 'var(--color-text)', tun: (ds.tun || []).map(function (v) { return { v: v }; }), meta: ds.meta || [], kv: ds.kv || [], noExample: !ds.hasExample });
          out.sectionActions = [{ label: 'Schwelle ändern: ' + ds.id, onClick: function () { self.openDlg('threshold', { leverId: ds.id, lever: ds.id + ' ' + ds.name, current: ds.threshold, rule: ds.meta && ds.meta[2] ? ds.meta[2].v : '', text: '', note: '', by: ds.owner }); } }];
        }
      }
      /* KPIs: der Zyklus als Pillen-Umschalter, die Optionen kommen aus dem Motor (Beschriftung, gewaehlt, Wechsel) */
      if (S.tab === 'kpis') out.periodOpts = V.periodOpts || [];
      out.tables = tables.map(shape); this._tables = out.tables; out.hasSectionActions = out.sectionActions.length > 0;
      return out;
    }

    /* ---------- Zeichnen ---------- */
    renderHeader(o) {
      if (SKIN && SKIN.renderHeader) return SKIN.renderHeader.call(this, o);
      return h('header', { className: 'app-header' },
        h('div', { className: 'rule-top' }),
        h('div', { className: 'gutter', style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px 18px', paddingBlock: 5 } },
          h('div', { className: 'brand' }, 'Restwert Engine'),
          h('nav', { 'aria-label': 'Bereiche', style: { display: 'flex', flexWrap: 'wrap', gap: '0 16px' } },
            o.groups.map(function (g) { return h('button', { key: g.key, type: 'button', className: 'nav-tab', onClick: g.onClick, 'aria-current': g.current }, g.label); }),
            PLANNED.map(function (l) { return h('button', { key: l, type: 'button', className: 'nav-tab', disabled: true, title: 'Als Nächstes geplant' }, l); })),
          h('div', { style: { marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4 } },
            h('button', { type: 'button', className: 'btn btn-ghost', onClick: o.toggleLog, 'aria-expanded': o.logOpen, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 8px' } }, o.protokollLabel),
            h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.exportCsv, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 10px' } }, 'Export CSV'),
            h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.openDelivery, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 10px' } }, 'Datei einlesen'),
            h('button', { type: 'button', className: 'btn btn-primary', onClick: o.startRun, disabled: o.running, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 12px' } }, o.runLabel))),
        h('div', { style: { borderTop: '1px solid var(--color-text)' } }),
        o.hasTabs ? h('nav', { 'aria-label': 'Reiter des Bereichs', className: 'nav-sub gutter' },
          o.tabs.map(function (t) { return h('button', { key: t.key, type: 'button', className: 'nav-tab', onClick: t.onClick, 'aria-current': t.current }, t.label); })) : null,
        h('div', { className: 'status gutter' },
          o.status.map(function (s) { return h('span', { key: s.k, style: { whiteSpace: 'nowrap' } }, s.k + ' ', h('b', null, s.v)); }),
          h('span', { style: { marginLeft: 'auto', fontStyle: 'italic' } }, 'Prototyp: Läufe werden protokolliert, nicht gerechnet.')));
    }
    renderLog(o) {
      if (SKIN && SKIN.renderLog) return SKIN.renderLog.call(this, o);
      if (!o.logOpen) return null;
      return h('section', { 'aria-label': 'Protokoll', style: { margin: '14px 0 0' } },
        h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' } }, h('h3', { style: { fontSize: 15, margin: 0 } }, 'Protokoll dieser Installation'), h('span', { style: { fontSize: 12, color: MUTED } }, 'manuell erfasste Daten, Lieferungen, Schwellen, Läufe · lokal gespeichert')),
        o.hasLog ? h('div', { className: 'tablewrap' }, h('table', { className: 'table', style: { fontSize: 13 } },
          h('thead', null, h('tr', null, ['Zeit', 'Art', 'Was', 'Wer', 'Status'].map(function (k) { return h('th', { key: k, style: { padding: '5px 8px' } }, k); }))),
          h('tbody', null, o.log.map(function (e, i) {
            return h('tr', { key: i }, h('td', { style: { padding: '5px 8px', whiteSpace: 'nowrap' } }, e.at), h('td', { style: { padding: '5px 8px', whiteSpace: 'nowrap' } }, e.kind), h('td', { style: { padding: '5px 8px' } }, e.text), h('td', { style: { padding: '5px 8px', whiteSpace: 'nowrap' } }, e.who), h('td', { style: { padding: '5px 8px' } }, h('span', { className: 'tag ' + e.cls }, e.status)));
          })))) : null,
        o.noLog ? h('p', { style: { margin: '8px 0 0', fontSize: 13, fontStyle: 'italic', color: MUTED } }, 'Noch keine Einträge. Preisbelege erfassen, Dateien einlesen, Schwellen ändern oder Szenarien speichern; jeder Schritt landet hier.') : null);
    }
    renderSectionBar(o) {
      if (SKIN && SKIN.renderSectionBar) return SKIN.renderSectionBar.call(this, o);
      var parts = [];
      if (o.isDevice) {
        parts.push(Field('Geräteart', 'rw-fam', Select('rw-fam', o.devFams, o.onDevFamily), { flex: '0 1 130px' }));
        parts.push(Field('Hersteller', 'rw-oem', Select('rw-oem', o.devOems, o.onDevOem), { flex: '0 1 150px' }));
        parts.push(Field('Modell', 'rw-model', Select('rw-model', o.devModels, o.onDevModel), { flex: '1 1 200px', maxWidth: 300 }));
        parts.push(Field('Ausstattung', 'rw-spec', Select('rw-spec', o.devSpecs, o.onDevSpec), { flex: '1 1 180px', maxWidth: 280 }));
      }
      if (o.isMarket) {
        parts.push(Seg('rw-mfam-l', 'Gerätefamilie', 'rw-mfam', o.famOpts));
        parts.push(Seg('rw-view-l', 'Diagramm', 'rw-view', o.viewOpts));
      }
      if (o.isTco) {
        parts.push(Field('Geräteart', 'rw-tfam', Select('rw-tfam', o.tcoFams, o.onTcoFamily), { flex: '0 1 130px' }));
        parts.push(Field('Hersteller', 'rw-toem', Select('rw-toem', o.tcoOems, o.onTcoOem), { flex: '0 1 150px' }));
        parts.push(Field('Modell', 'rw-tmodel', Select('rw-tmodel', o.tcoModels, o.onTcoModel), { flex: '1 1 220px', maxWidth: 320 }));
        parts.push(Seg('rw-tst-l', 'Ausstattung', 'rw-tst', o.storageOpts));
        parts.push(Seg('rw-tterm-l', 'Laufzeit', 'rw-tterm', o.tcoTermOpts));
      }
      if (o.isKpis && o.periodOpts.length) parts.push(Seg('rw-kpi-period-l', 'Zyklus', 'rw-kpi-period', o.periodOpts));
      var actions = o.hasSectionActions ? h('div', { style: { marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', gap: 4, alignSelf: 'flex-end' } },
        o.sectionActions.map(function (a, i) { return h('button', { key: i, type: 'button', className: 'btn btn-secondary', onClick: a.onClick, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 10px' } }, a.label); })) : null;
      if (!parts.length && !actions) return null;
      return h('section', { 'aria-label': 'Auswahl und Aktionen', style: { display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '12px 18px', padding: '16px 0 0' } },
        parts.map(function (p, i) { return h(React.Fragment, { key: i }, p); }), actions);
    }
    renderDeviceSettings(o) {
      if (SKIN && SKIN.renderDeviceSettings) return SKIN.renderDeviceSettings.call(this, o);
      if (!o.isDevice || !o.ready) return null;
      var numField = function (label, id, step, value, onFocus, onChange, onBlur, border) {
        return Field(label, id, h('input', { className: 'input', id: id, type: 'number', step: step, value: value, onFocus: onFocus, onChange: onChange, onBlur: onBlur, onKeyDown: o.onKeyCommit, style: { minHeight: 32, fontSize: 13, textAlign: 'right', borderColor: border } }), { width: 140 });
      };
      return h('section', { 'aria-label': 'Rechnung einstellen', style: { display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '12px 18px', padding: '12px 0 0' } },
        Seg('rw-term-l', 'Laufzeit', 'rw-term', o.termOpts),
        Seg('rw-when-l', 'Kauf', 'rw-when', o.whenOpts),
        numField('Einkaufspreis ohne Mehrwertsteuer, €', 'rw-buy', '1', o.buyValue, o.onFocusBuy, o.onDraftBuy, o.onBlurBuy, o.buyBorder),
        numField('Miete je Monat ohne Mehrwertsteuer, €', 'rw-rate', '0.01', o.rateValue, o.onFocusRate, o.onDraftRate, o.onBlurRate, o.rateBorder),
        numField('Kosten bis Verkauf, €', 'rw-cost', '1', o.costValue, o.onFocusCost, o.onDraftCost, o.onBlurCost, o.costBorder),
        o.hasOverride ? h('button', { type: 'button', className: 'btn btn-ghost', onClick: o.resetOverrides, style: { whiteSpace: 'nowrap', fontSize: 13, padding: '6px 8px', alignSelf: 'flex-end' } }, 'Vorbelegung wiederherstellen') : null);
    }
    renderKpis(o) {
      if (SKIN && SKIN.renderKpis) return SKIN.renderKpis.call(this, o);
      if (!o.hasKpis) return null;
      return h(React.Fragment, null,
        h('section', { 'aria-label': 'Ergebnis', style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(190px, 100%), 1fr))', gap: '18px 28px', margin: '22px 0 0', minWidth: 0 } },
          o.kpis.map(function (k, i) {
            return h('div', { key: i, style: { display: 'grid', gap: 5, alignContent: 'start' } },
              h('div', { className: 'kpi-label' }, h('span', null, k.label), k.tags.map(function (g, j) { return h('span', { key: j, className: 'tag ' + g.cls, style: { textTransform: 'none', letterSpacing: '0.02em', fontSize: 10, padding: '1px 7px' } }, g.text); })),
              h('div', { className: 'kpi-value', style: { color: k.color } }, k.value),
              h('ul', { style: { margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 2, fontSize: 12, lineHeight: 1.45, color: MUTED } }, k.lines.map(function (l, j) { return h('li', { key: j }, l.v); })));
          })),
        h('div', { style: { margin: '8px 0 0', display: 'flex', gap: 2, flexWrap: 'wrap' } },
          o.hasMoreKpi ? Ghost(o.kpiDetailsLabel, o.toggleKpiDetails, o.kpiDetailsOpen) : null,
          o.hasKpiDefs ? Ghost(o.kpiDefsLabel, o.toggleKpiDefs, o.kpiDefsOpen) : null),
        o.hasKpiDefs && o.kpiDefsOpen ? Defs(o.kpiDefs) : null);
    }
    renderTables(o) {
      if (SKIN && SKIN.renderTables) return SKIN.renderTables.call(this, o);
      var self = this;
      return o.tables.map(function (t) {
        return h('section', { key: t.key, style: { margin: '30px 0 0' } },
          h('div', { style: { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 10px' } },
            h('h3', { style: { fontSize: 15, margin: 0, letterSpacing: '-0.005em' } }, t.title),
            t.tags.map(function (g, j) { return h('span', { key: j, className: 'tag ' + g.cls, style: { fontSize: 10, padding: '1px 7px' } }, g.text); }),
            t.count ? h('span', { style: { fontSize: 12, color: MUTED } }, t.count) : null,
            h('span', { style: { marginLeft: 'auto', display: 'inline-flex', gap: 2 } },
              t.collapsible ? Ghost(t.rowsLabel, t.toggleRows, t.rowsOpen, { padding: '2px 6px' }) : null,
              t.hasDefs ? Ghost(t.defsLabel, t.toggleDefs, t.defsOpen, { padding: '2px 6px' }) : null)),
          t.note ? h('p', { style: NOTE_STYLE }, t.note) : null,
          t.defsOpen ? Defs(t.defs) : null,
          t.rowsOpen ? h(React.Fragment, null,
            t.hasRows ? Table(t, o.tableFont, o.cellPad, o.padLeft) : null,
            t.showEmpty ? h('p', { style: { margin: '8px 0 0', fontStyle: 'italic', fontSize: 13, color: MUTED } }, t.empty) : null) : null,
          t.footLead ? h(React.Fragment, null,
            h('p', { style: { margin: '8px 0 2px', fontSize: 12.5, fontWeight: 600 } }, t.footLead),
            h('ul', { style: { margin: 0, paddingLeft: 18, maxWidth: '110ch', fontSize: 12.5, lineHeight: 1.5, color: MUTED } }, t.footLines.map(function (l, j) { return h('li', { key: j }, l.v); }))) : null,
          t.foot ? h('p', { style: { margin: '6px 0 0', maxWidth: '110ch', fontSize: 12.5, lineHeight: 1.5, color: MUTED } }, t.foot) : null);
      });
    }
    renderDossier(o) {
      if (SKIN && SKIN.renderDossier) return SKIN.renderDossier.call(this, o);
      if (!o.hasDossier) return null;
      var d = o.dossier, H4 = function (t) { return h('h4', { style: { fontSize: 13, margin: '0 0 4px' } }, t); };
      var kvLabel = { fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: MUTED };
      return h('section', { 'aria-label': 'Stellschraube im Detail', style: { margin: '34px 0 0', display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 18, minWidth: 0 } },
        h('div', { style: { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px 24px', minWidth: 0 } },
          h('div', null, h('div', { style: kvLabel }, 'Stellschraube ' + d.id + ' · gewählt'),
            h('h3', { style: { fontSize: 18, margin: '2px 0 0', letterSpacing: '-0.01em', maxWidth: '50ch' } }, d.name + ': ', h('span', { style: { fontWeight: 400 } }, d.handle))),
          h('div', { className: 'kpi-value', style: { fontSize: 24, color: d.sumColor } }, d.sum)),
        h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: '16px 40px', maxWidth: 1300, minWidth: 0 } },
          h('div', null, H4('Prüffrage'), h('p', { style: { margin: 0, fontSize: 13.5, lineHeight: 1.5 } }, d.frage), h('p', { style: { margin: '6px 0 0', fontSize: 12.5, lineHeight: 1.5, color: MUTED } }, d.bedeutung)),
          h('div', null, H4('Was man tun kann'), h('ul', { style: { margin: 0, paddingLeft: 18, display: 'grid', gap: 3, fontSize: 13, lineHeight: 1.5 } }, d.tun.map(function (x, i) { return h('li', { key: i }, x.v); }))),
          h('div', null, H4('Wer dreht daran'),
            h('dl', { style: { margin: 0, display: 'grid', gap: 6, fontSize: 13, lineHeight: 1.5 } }, d.meta.map(function (x, i) {
              return h('div', { key: i }, h('dt', { style: kvLabel }, x.k, ' ', x.changed ? h('span', { className: 'tag tag-accent-2', style: { fontSize: 10, padding: '1px 7px', textTransform: 'none', letterSpacing: '0.02em' } }, 'geändert') : null),
                h('dd', { style: { margin: '1px 0 0' } }, x.v), x.note ? h('dd', { style: { margin: '1px 0 0', fontSize: 12, color: MUTED } }, x.note) : null);
            })))),
        h('div', null, h('h4', { style: { fontSize: 13, margin: '0 0 6px' } }, 'Herleitung, zwölf Monate'),
          h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(150px, 100%), 1fr))', gap: '10px 24px', maxWidth: 800, minWidth: 0 } },
            d.kv.map(function (x, i) { return h('div', { key: i }, h('div', { style: kvLabel }, x.k), h('div', { className: 'kpi-value', style: { fontSize: 18, lineHeight: 1.1, marginTop: 2 } }, x.v), h('div', { style: { fontSize: 12, lineHeight: 1.4, marginTop: 1, color: MUTED } }, x.s)); })),
          d.eventNote ? h('p', { style: { margin: '8px 0 0', fontSize: 12.5, lineHeight: 1.5, maxWidth: '110ch', color: MUTED } }, d.eventNote) : null),
        d.hasChannel && d.channel ? h('div', null, h('h4', { style: { fontSize: 13, margin: '0 0 6px' } }, d.channel.title),
          Table({ cols: d.channel.cols, rows: d.channel.rows }, o.tableFont, o.cellPad, o.padLeft, { maxWidth: 860 }),
          d.channel.foot ? h('p', { style: { margin: '6px 0 0', fontSize: 12.5, lineHeight: 1.5, maxWidth: '110ch', color: MUTED } }, d.channel.foot) : null) : null,
        h('div', { style: { maxWidth: 760 } }, h('h4', { style: { fontSize: 13, margin: '0 0 6px' } }, 'Ein Gerät, vorgerechnet'),
          d.hasExample && d.example ? h(React.Fragment, null,
            h('p', { style: { margin: 0, fontSize: 13, lineHeight: 1.5 } }, h('strong', null, d.example.head), ' · ' + d.example.delta),
            h('dl', { style: { margin: '6px 0 0', display: 'grid', gridTemplateColumns: '1fr max-content', gap: '3px 16px', fontSize: 13 } }, (d.example.rows || []).map(function (r, i) { return h(React.Fragment, { key: i }, h('dt', { style: { color: MUTED } }, r.k), h('dd', { style: { margin: 0, textAlign: 'right' } }, r.v)); })),
            d.example.formula ? h('p', { style: { margin: '6px 0 0', fontSize: 12.5, lineHeight: 1.5, color: MUTED } }, d.example.formula) : null,
            d.example.note ? h('p', { style: { margin: '4px 0 0', fontSize: 12, lineHeight: 1.5, color: MUTED } }, d.example.note) : null) : null,
          d.noExample ? h('p', { style: { margin: 0, fontStyle: 'italic', fontSize: 13, color: MUTED } }, 'Im Fenster kein Gerät mit einem Hebel größer null.') : null));
    }
    renderBlocks(o) {
      if (SKIN && SKIN.renderBlocks) return SKIN.renderBlocks.call(this, o);
      if (!o.hasBlocks) return null;
      var list = function (b, tag) { return h(tag, { style: { margin: '6px 0 0 15px', paddingLeft: 20, maxWidth: '100ch', display: 'grid', gap: 4, fontSize: 13, lineHeight: 1.5 } }, b.items.map(function (it, i) { return h('li', { key: i }, it.lead ? h('strong', null, it.lead) : null, it.lead ? ' ' : null, it.text, it.href ? h(React.Fragment, null, ' ', h('a', { href: it.href, target: '_blank', rel: 'noopener', style: { color: 'var(--color-accent-700)' } }, it.linkText || 'Quelle')) : null); })); };
      return h('section', { 'aria-label': 'Methode und Grenzen', style: { margin: '36px 0 0', display: 'grid', gap: 8 } },
        o.blocks.map(function (b) {
          return h('div', { key: b.key },
            h('button', { type: 'button', className: 'blk-toggle', onClick: b.toggle, 'aria-expanded': b.open },
              h('span', { style: { display: 'inline-block', width: 9, color: 'var(--color-accent-700)', fontSize: 12 } }, b.caret), b.title,
              b.hasTag ? h('span', { className: 'tag ' + b.tagCls, style: { fontWeight: 400, fontSize: 10, padding: '1px 7px' } }, b.tagText) : null),
            b.open ? h(React.Fragment, null,
              b.intro ? h('p', { style: { margin: '6px 0 0 15px', maxWidth: '100ch', fontSize: 13, lineHeight: 1.5, color: MUTED } }, b.intro) : null,
              b.ordered ? list(b, 'ol') : list(b, 'ul')) : null);
        }));
    }
    renderDialog(o) {
      if (SKIN && SKIN.renderDialog) return SKIN.renderDialog.call(this, o);
      if (!o.dlgOpen) return null;
      var f = o.form, body = null;
      var input = function (id, props) { return h('input', Object.assign({ className: 'input', id: id }, props)); };
      var lbl = function (id, text) { return h('label', { htmlFor: id }, text); };
      if (o.dlgAnchor) {
        body = h('div', { style: { display: 'grid', gap: 12 } },
          h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 } },
            Field('Geräteart', 'rw-a-fam', Select('rw-a-fam', o.aFams, o.onAFamily)),
            Field('Hersteller', 'rw-a-oem', Select('rw-a-oem', o.aOems, o.onAOem)),
            Field('Modell', 'rw-a-model', Select('rw-a-model', o.aModels, o.onAModel)),
            Field('Ausstattung', 'rw-a-dev', Select('rw-a-dev', o.aSpecs, o.onASpec))),
          f.devInfo ? h('div', { style: { fontSize: 12, marginTop: -6, color: MUTED } }, f.devInfo) : null,
          h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 } },
            h('div', { className: 'field' }, lbl('rw-a-kind', 'Preisart'), h('select', { className: 'input', id: 'rw-a-kind', value: f.kind, onChange: o.setKind }, h('option', { value: 'refurbished-marktplatz' }, 'Marktplatz-Angebot'), h('option', { value: 'ankauf-trade-in' }, 'Ankauf-Gebot „bis zu“'))),
            h('div', { className: 'field' }, lbl('rw-a-grade', 'Zustandsstufe'), h('select', { className: 'input', id: 'rw-a-grade', value: f.grade, onChange: o.setGrade }, h('option', { value: 'A' }, 'A wie neu'), h('option', { value: 'B' }, 'B sehr gut'), h('option', { value: 'C' }, 'C gut'), h('option', { value: 'D' }, 'D akzeptabel'), h('option', { value: 'UNKNOWN' }, 'unbekannt'))),
            h('div', { className: 'field' }, lbl('rw-a-price', 'Preis einschließlich Mehrwertsteuer, €'), input('rw-a-price', { type: 'number', step: '0.01', min: '0', value: f.price || '', onChange: o.setPrice, style: { textAlign: 'right' } })),
            h('div', { className: 'field' }, lbl('rw-a-date', 'Datum des Belegs'), input('rw-a-date', { type: 'date', value: f.date || '', onChange: o.setDate }))),
          h('div', { className: 'field' }, lbl('rw-a-cond', 'Zustand laut Verkäufer'), input('rw-a-cond', { value: f.condition || '', onChange: o.setCondition, placeholder: 'z. B. Sehr gut, Premium, Wie neu' })),
          h('div', { className: 'field' }, lbl('rw-a-url', 'Quelle (Adresse der Seite)'), input('rw-a-url', { type: 'url', value: f.url || '', onChange: o.setUrl, placeholder: 'https://…' })),
          f.preview ? h('p', { style: { margin: 0, fontSize: 13 } }, h('span', { style: { fontWeight: 600 } }, 'Realisierung:'), ' ' + f.preview) : null);
      }
      if (o.dlgDelivery) {
        body = h('div', { style: { display: 'grid', gap: 12 } },
          h('div', { className: 'field' }, lbl('rw-d-feed', 'Kanal'), Select('rw-d-feed', o.feedOpts, o.setFeed)),
          h('div', { className: 'field' }, lbl('rw-d-file', 'Datei (CSV oder Textexport des Quellsystems)'), input('rw-d-file', { type: 'file', accept: '.csv,.txt,.tsv', onChange: o.onFile, style: { padding: '6px 10px' } })),
          f.fileName ? h('dl', { style: { margin: 0, display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 14px', fontSize: 13 } },
            h('dt', { style: { color: MUTED } }, 'Datei'), h('dd', { style: { margin: 0 } }, f.fileName), h('dt', { style: { color: MUTED } }, 'Zeilen'), h('dd', { style: { margin: 0 } }, f.rowsText), h('dt', { style: { color: MUTED } }, 'Spalten'), h('dd', { style: { margin: 0 } }, f.colsText)) : null);
      }
      if (o.dlgThreshold) {
        body = h('div', { style: { display: 'grid', gap: 12 } },
          h('dl', { style: { margin: 0, display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 14px', fontSize: 13 } }, h('dt', { style: { color: MUTED } }, 'Bisher'), h('dd', { style: { margin: 0 } }, f.current), h('dt', { style: { color: MUTED } }, 'Regel'), h('dd', { style: { margin: 0 } }, f.rule)),
          h('div', { className: 'field' }, lbl('rw-t-text', 'Neue Schwelle'), input('rw-t-text', { value: f.text || '', onChange: o.setText, placeholder: 'Wortlaut der neuen Schwelle', autoFocus: true })),
          h('div', { className: 'field' }, lbl('rw-t-note', 'Begründung'), h('textarea', { className: 'input', id: 'rw-t-note', rows: 2, value: f.note || '', onChange: o.setNote, placeholder: 'Warum die Schwelle geändert wird' })),
          h('div', { className: 'field' }, lbl('rw-t-by', 'Geändert durch (Rolle)'), input('rw-t-by', { value: f.by || '', onChange: o.setBy })));
      }
      if (o.dlgAssumption) {
        var np = { type: 'number', step: '0.5', value: f.disc || '', onChange: o.setDisc, style: { textAlign: 'right' }, autoFocus: true };
        if (typeof o.discMin === 'number') np.min = String(o.discMin * 100);
        if (typeof o.discMax === 'number') np.max = String(o.discMax * 100);
        body = h('div', { style: { display: 'grid', gap: 12 } },
          h('div', { className: 'field', style: { maxWidth: 200 } }, lbl('rw-as-disc', 'Einkaufsabschlag auf die UVP, %'), input('rw-as-disc', np)),
          h('div', { className: 'field' }, lbl('rw-as-by', 'Geändert durch (Rolle)'), input('rw-as-by', { value: f.by || '', onChange: o.setBy })));
      }
      if (o.dlgScenario) {
        body = h('div', { style: { display: 'grid', gap: 12 } },
          h('div', { className: 'field' }, lbl('rw-s-name', 'Name des Szenarios'), input('rw-s-name', { value: f.name || '', onChange: o.setName, placeholder: f.defaultName || '', autoFocus: true })),
          h('p', { style: { margin: 0, fontSize: 13, color: MUTED } }, f.summary));
      }
      return h('div', { className: 'dialog-backdrop', style: { zIndex: 20 }, onClick: o.onBackdrop },
        h('div', { className: 'dialog', role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'rw-dlg-title', style: { width: 'min(560px, 100%)', maxHeight: '92vh', overflow: 'auto' }, onClick: o.stop, onKeyDown: o.onDlgKey },
          h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' } }, h('div', { className: 'dialog-title', id: 'rw-dlg-title' }, o.dlgTitle), o.dlgKicker ? h('span', { style: { fontSize: 12, color: MUTED } }, o.dlgKicker) : null),
          o.dlgHint ? h('p', { className: 'dialog-body', style: { margin: 0, fontSize: 13 } }, o.dlgHint) : null,
          body,
          f.error ? h('p', { style: { margin: 0, fontSize: 13, color: 'var(--color-accent-2-700)' } }, f.error) : null,
          h('div', { className: 'dialog-actions' }, h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.closeDlg }, 'Abbrechen'), h('button', { type: 'button', className: 'btn btn-primary', onClick: o.submitDlg }, o.dlgSubmit))));
    }
    render() {
      var o = this.renderVals();
      if (SKIN && SKIN.render) return SKIN.render.call(this, o);
      var record = null;
      if (o.ready) {
        record = h(React.Fragment, null,
          h('div', { style: { margin: '22px 0 0', display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '4px 14px' } },
            h('span', { className: 'kicker' }, o.kicker), h('h2', { className: 'subject' }, o.subject)),
          o.hasFacts ? h('dl', { style: { display: 'flex', flexWrap: 'wrap', gap: '4px 22px', margin: '8px 0 0', fontSize: 12.5, color: MUTED } },
            o.facts.map(function (x, i) { return h('div', { key: i, style: { display: 'flex', gap: 6, alignItems: 'baseline', margin: 0, flexWrap: 'wrap' } }, h('dt', { style: { letterSpacing: '0.06em', textTransform: 'uppercase', fontSize: 10.5 } }, x.k), h('dd', { style: { margin: 0, color: 'var(--color-text)' } }, x.v, x.href ? h(React.Fragment, null, ' ', h('a', { href: x.href, target: '_blank', rel: 'noopener', style: { color: 'var(--color-accent-700)', fontSize: 11.5 } }, 'Quelle')) : null)); })) : null,
          o.intro ? h('div', { style: { margin: '6px 0 0' } }, Ghost(o.introLabel, o.toggleIntro, o.introOpen), o.introOpen ? h('p', { style: { margin: '4px 0 0', maxWidth: '100ch', fontSize: 13, lineHeight: 1.5, color: MUTED } }, o.intro) : null) : null,
          o.hasSteps ? h('ol', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px 22px', listStyle: 'none', padding: 0, margin: '18px 0 0' } },
            o.steps.map(function (s, i) { return h('li', { key: i, style: { flex: '0 1 auto', display: 'flex', gap: 6, alignItems: 'baseline' } }, h('span', { style: { fontSize: 11, color: 'var(--color-accent-700)' } }, s.k), h('span', { style: { fontWeight: 600, fontSize: 13 } }, s.t), h('span', { style: { fontSize: 12, color: MUTED } }, s.q)); })) : null,
          this.renderKpis(o),
          o.calcnote ? h('p', { style: { margin: '14px 0 0', maxWidth: '110ch', fontSize: 12.5, lineHeight: 1.5, color: MUTED } }, o.calcnote) : null,
          o.hasActions ? h('section', { style: { margin: '28px 0 0' } }, h('h3', { style: { fontSize: 15, margin: '0 0 8px' } }, 'Was man daraus macht'),
            h('ol', { style: { margin: 0, paddingLeft: 20, maxWidth: '100ch', display: 'grid', gap: 6, fontSize: 13, lineHeight: 1.5 } }, o.actions.map(function (a, i) { return h('li', { key: i }, h('strong', null, a.lead), ' ', a.text); }))) : null,
          o.hasChart ? h('figure', null, h('div', { ref: this.chartRef, className: 'chart' }), o.chartNote ? h('figcaption', { style: { fontSize: 12, lineHeight: 1.5, maxWidth: '110ch', color: MUTED, marginTop: 2 } }, o.chartNote) : null) : null,
          o.tablesLast ? null : this.renderTables(o),
          this.renderDossier(o),
          this.renderBlocks(o),
          o.tablesLast ? this.renderTables(o) : null);
      }
      return h('div', { className: 'app' },
        this.renderHeader(o),
        h('main', { className: 'main gutter' },
          this.renderLog(o),
          this.renderSectionBar(o),
          this.renderDeviceSettings(o),
          o.loading ? h('p', { className: 'loading', style: { margin: '28px 0 0', fontSize: 13, color: MUTED } }, 'Daten werden geladen …') : null,
          o.error ? h('p', { role: 'alert', style: { margin: '28px 0 0', fontSize: 13, color: 'var(--color-accent-2-700)' } }, o.error) : null,
          record),
        this.renderDialog(o));
    }
  }

  var root = document.getElementById('app');
  if (!root) { console.error('app: <div id="app"> fehlt'); return; }
  if (SKIN && typeof SKIN.init === 'function') SKIN.init({ React: React, h: h, Tag: Tag, Ghost: Ghost, Defs: Defs, Field: Field, Select: Select, Seg: Seg, Cell: Cell, Table: Table, MUTED: MUTED, NOTE_STYLE: NOTE_STYLE, PLANNED: PLANNED, GROUPS: GROUPS, TABS: TABS, tabOf: tabOf, tabsIn: tabsIn, dataOf: dataOf });
  ReactDOM.createRoot(root).render(h(App, { density: 'compact', definitions: 'collapsed', rememberSelection: true }));
})(window);
