/* Restwert Engine, Optik "Cockpit" (seit 16.09.2026): die Zeichenschicht der Huelle als Werkzeug-Oberflaeche nach dem
   Prinzip von Eugens zwei Vorlagen (Seitenleiste mit Bereichen und Reitern, weisse Karten, Status als Punkt und Pille,
   grosse Zahlen, Verlaufslinien). Laedt VOR app.js und stellt window.RE_SKIN bereit. Cockpit definiert render(o) und
   zeichnet damit die ganze Seite aus den Ansichtswerten o, die app.js in renderVals() baut; die einzelnen renderXxx-Haken
   der Huelle werden dann nicht mehr aufgerufen. Hier wird nur gezeichnet: kein Zustand ausser dem Auf und Zu der
   Seitenleiste am Telefon, keine Zahl, kein Text, der nicht aus den Ansichtswerten kommt (Beschriftungen der Bedienung
   wie Menue, Export CSV ausgenommen).
   DOM-Vertrag mit dem Pruefstand (tools/test_page.js): nav[aria-label="Bereiche"] mit Reiter-Knoepfen (aria-current="page"),
   .status, main, h2.subject, .kicker, section[aria-label="Ergebnis"] + div + dl.defs, figure .chart, section mit h3 je
   Tabelle, .blk-toggle mit aria-expanded, section[aria-label="Protokoll"], [role="dialog"], die Feld-Ids rw-*. */
(function (w) {
  'use strict';
  var S = null;               /* Bausteine der Huelle, kommen ueber init() */
  var h = null, React = null;
  var sideOpen = false;       /* Seitenleiste am Telefon */

  function num(x) { return typeof x === 'number' && isFinite(x); }

  /* Verlaufslinie: 72 x 26, Zahlen in Zeitfolge, letzter Punkt markiert */
  function Spark(values, neg) {
    var v = (values || []).filter(num); if (v.length < 2) return null;
    var W = 72, H = 26, P = 2, lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), span = hi - lo || 1;
    var pts = v.map(function (y, i) { return [P + (W - 2 * P) * i / (v.length - 1), P + (H - 2 * P) * (1 - (y - lo) / span)]; });
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    var area = d + ' L' + pts[pts.length - 1][0].toFixed(1) + ' ' + (H - P) + ' L' + pts[0][0].toFixed(1) + ' ' + (H - P) + ' Z';
    var last = pts[pts.length - 1];
    return h('svg', { className: 'spark' + (neg ? ' neg' : ''), width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': true },
      h('path', { className: 'area', d: area }), h('path', { d: d }), h('circle', { className: 'dot', cx: last[0], cy: last[1], r: 2.2 }));
  }

  function Pill(g, key) { return h('span', { key: key, className: 'tag ' + g.cls }, g.text); }

  /* ---------- Seitenleiste ---------- */
  function renderSide(app, o) {
    var close = function (fn) { return function () { sideOpen = false; fn(); }; };
    var items = o.groups.map(function (g) {
      if (g.tabs.length === 1) {
        var t = g.tabs[0];
        return h('div', { key: g.key, className: 'ck-group' },
          h('button', { type: 'button', className: 'ck-item', 'aria-current': t.current, onClick: close(t.onClick) },
            t.label, t.key === 'lake' && o.pending ? h('span', { className: 'badge', 'data-badge': String(o.pending), 'aria-label': String(o.pending) + ' offen für den nächsten Lauf' }) : null));
      }
      return h('div', { key: g.key, className: 'ck-group' },
        h('button', { type: 'button', className: 'ck-group-h', 'aria-current': g.current, onClick: close(g.onClick) }, g.label),
        g.tabs.map(function (t) { return h('button', { key: t.key, type: 'button', className: 'ck-item', 'aria-current': t.current, onClick: close(t.onClick) }, t.label); }));
    });
    items.push(h('div', { key: 'planned', className: 'ck-group' },
      h('div', { className: 'ck-group-h', style: { cursor: 'default' } }, 'Geplant'),
      o.planned.map(function (p) { return h('button', { key: p.label, type: 'button', className: 'ck-item', disabled: true, title: p.title }, p.label); })));
    return h('aside', { className: 'ck-side' + (sideOpen ? ' open' : '') },
      h('div', { className: 'ck-brand' },
        h('b', null, 'Restwert Engine'),
        h('button', { type: 'button', className: 'ck-menu', 'aria-expanded': sideOpen, onClick: function () { sideOpen = !sideOpen; app.forceUpdate(); } }, sideOpen ? 'Schließen' : 'Menü')),
      h('nav', { 'aria-label': 'Bereiche', className: 'ck-nav' }, items));
  }

  /* ---------- Kopfzeile und Statuszeile ---------- */
  function renderTop(app, o) {
    var g = o.groups.filter(function (x) { return x.current; })[0], t = g ? g.tabs.filter(function (x) { return x.current; })[0] : null;
    var single = !g || g.tabs.length === 1;
    return h('header', { className: 'ck-top' },
      h('div', null,
        single ? null : h('div', { className: 'ck-crumb' }, g.label),
        h('div', { className: 'ck-title' }, t ? t.label : '')),
      h('div', { className: 'ck-actions' },
        h('button', { type: 'button', className: 'btn btn-ghost', onClick: o.toggleLog, 'aria-expanded': o.logOpen }, o.protokollLabel),
        h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.exportCsv }, 'Export CSV'),
        h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.openDelivery }, 'Datei einlesen'),
        h('button', { type: 'button', className: 'btn btn-primary', onClick: o.startRun, disabled: o.running }, o.runLabel)));
  }
  function renderStatus(o) {
    return h('div', { className: 'status ck-status' },
      o.status.map(function (s) { return h('span', { key: s.k, className: 'ck-chip' }, s.k + ' ', h('b', null, s.v)); }),
      h('span', { className: 'proto' }, 'Prototyp: Läufe werden protokolliert, nicht gerechnet.'));
  }

  /* ---------- Bausteine der Inhaltsflaeche ---------- */
  function Card(children, extra) { return h('section', Object.assign({ className: 'card' }, extra || {}), children); }

  function renderLog(o) {
    if (!o.logOpen) return null;
    return h('section', { 'aria-label': 'Protokoll', className: 'card' },
      h('div', { className: 'card-h' }, h('h3', null, 'Protokoll dieser Installation'), h('span', { className: 'log-meta' }, 'manuell erfasste Daten, Lieferungen, Schwellen, Läufe · lokal gespeichert')),
      o.hasLog ? h('div', { className: 'tablewrap' }, h('table', { className: 'table' },
        h('thead', null, h('tr', null, ['Zeit', 'Art', 'Was', 'Wer', 'Status'].map(function (k) { return h('th', { key: k }, k); }))),
        h('tbody', null, o.log.map(function (e, i) {
          return h('tr', { key: i }, h('td', { style: { whiteSpace: 'nowrap' } }, e.at), h('td', { style: { whiteSpace: 'nowrap' } }, e.kind), h('td', null, e.text), h('td', { style: { whiteSpace: 'nowrap' } }, e.who), h('td', null, h('span', { className: 'tag ' + e.cls }, e.status)));
        })))) : null,
      o.noLog ? h('p', { className: 'note', style: { fontStyle: 'italic' } }, 'Noch keine Einträge. Preisbelege erfassen, Dateien einlesen, Schwellen ändern oder Szenarien speichern; jeder Schritt landet hier.') : null);
  }

  function renderSectionBar(o) {
    var parts = [];
    if (o.isDevice) {
      parts.push(S.Field('Geräteart', 'rw-fam', S.Select('rw-fam', o.devFams, o.onDevFamily), { flex: '0 1 150px' }));
      parts.push(S.Field('Hersteller', 'rw-oem', S.Select('rw-oem', o.devOems, o.onDevOem), { flex: '0 1 160px' }));
      parts.push(S.Field('Modell', 'rw-model', S.Select('rw-model', o.devModels, o.onDevModel), { flex: '1 1 220px', maxWidth: 320 }));
      parts.push(S.Field('Ausstattung', 'rw-spec', S.Select('rw-spec', o.devSpecs, o.onDevSpec), { flex: '1 1 200px', maxWidth: 300 }));
    }
    if (o.isMarket) {
      parts.push(S.Seg('rw-mfam-l', 'Gerätefamilie', 'rw-mfam', o.famOpts));
      parts.push(S.Seg('rw-view-l', 'Diagramm', 'rw-view', o.viewOpts));
    }
    if (o.isTco) {
      parts.push(S.Field('Geräteart', 'rw-tfam', S.Select('rw-tfam', o.tcoFams, o.onTcoFamily), { flex: '0 1 150px' }));
      parts.push(S.Field('Hersteller', 'rw-toem', S.Select('rw-toem', o.tcoOems, o.onTcoOem), { flex: '0 1 160px' }));
      parts.push(S.Field('Modell', 'rw-tmodel', S.Select('rw-tmodel', o.tcoModels, o.onTcoModel), { flex: '1 1 240px', maxWidth: 340 }));
      parts.push(S.Seg('rw-tst-l', 'Ausstattung', 'rw-tst', o.storageOpts));
      parts.push(S.Seg('rw-tterm-l', 'Laufzeit', 'rw-tterm', o.tcoTermOpts));
    }
    var actions = o.hasSectionActions ? h('div', { className: 'actions' },
      o.sectionActions.map(function (a, i) { return h('button', { key: i, type: 'button', className: 'btn btn-secondary', onClick: a.onClick }, a.label); })) : null;
    if (!parts.length && !actions) return null;
    if (!parts.length) return h('section', { 'aria-label': 'Auswahl und Aktionen', className: 'toolbar', style: { justifyContent: 'flex-end' } }, actions);
    return h('section', { 'aria-label': 'Auswahl und Aktionen', className: 'card toolbar' },
      parts.map(function (p, i) { return h(React.Fragment, { key: i }, p); }), actions);
  }

  function renderDeviceSettings(o) {
    if (!o.isDevice || !o.ready) return null;
    var numField = function (label, id, step, value, onFocus, onChange, onBlur, border) {
      return S.Field(label, id, h('input', { className: 'input', id: id, type: 'number', step: step, value: value, onFocus: onFocus, onChange: onChange, onBlur: onBlur, onKeyDown: o.onKeyCommit, style: { textAlign: 'right', borderColor: border === 'var(--color-accent)' ? 'var(--c-green)' : undefined } }), { width: 150 });
    };
    return h('section', { 'aria-label': 'Rechnung einstellen', className: 'card toolbar' },
      S.Seg('rw-term-l', 'Laufzeit', 'rw-term', o.termOpts),
      S.Seg('rw-when-l', 'Kauf', 'rw-when', o.whenOpts),
      numField('Einkaufspreis ohne Mehrwertsteuer, €', 'rw-buy', '1', o.buyValue, o.onFocusBuy, o.onDraftBuy, o.onBlurBuy, o.buyBorder),
      numField('Miete je Monat ohne Mehrwertsteuer, €', 'rw-rate', '0.01', o.rateValue, o.onFocusRate, o.onDraftRate, o.onBlurRate, o.rateBorder),
      numField('Kosten bis Verkauf, €', 'rw-cost', '1', o.costValue, o.onFocusCost, o.onDraftCost, o.onBlurCost, o.costBorder),
      o.hasOverride ? h('button', { type: 'button', className: 'btn btn-ghost', onClick: o.resetOverrides, style: { alignSelf: 'flex-end' } }, 'Vorbelegung wiederherstellen') : null);
  }

  function renderKpis(o) {
    if (!o.hasKpis) return null;
    return h(React.Fragment, null,
      h('section', { 'aria-label': 'Ergebnis', className: 'kpis' },
        o.kpis.map(function (k, i) {
          var neg = !!k.neg;
          return h('div', { key: i, className: 'kpi' },
            h('div', { className: 'kpi-label' }, h('span', null, k.label), k.tags.map(function (g, j) { return Pill(g, j); })),
            h('div', { className: 'kpi-row' }, h('div', { className: 'kpi-value' + (neg ? ' neg' : '') }, k.value), Spark(k.spark, neg)),
            h('ul', { className: 'kpi-lines' }, k.lines.map(function (l, j) { return h('li', { key: j }, l.v); })));
        })),
      h('div', { style: { display: 'flex', gap: 2, flexWrap: 'wrap' } },
        o.hasMoreKpi ? S.Ghost(o.kpiDetailsLabel, o.toggleKpiDetails, o.kpiDetailsOpen) : null,
        o.hasKpiDefs ? S.Ghost(o.kpiDefsLabel, o.toggleKpiDefs, o.kpiDefsOpen) : null),
      o.hasKpiDefs && o.kpiDefsOpen ? S.Defs(o.kpiDefs) : null);
  }

  /* eine Karte je Zeile: erste Zelle Titel, zweite Text, Zahlen rechts als Kennwerte, Marken als Pillen, Links als Links */
  function RowCards(t, o) {
    return h('div', { className: 'rows' }, t.rows.map(function (r, i) {
      var cells = r.cells, cols = t.cols, title = cells[0] || {}, text = cells[1] || {}, metrics = [], pills = [], links = [], rest = [];
      cells.forEach(function (c, j) {
        if (j < 2) return;
        var head = cols[j] ? cols[j].text : '';
        if (c.tag) pills.push(h('span', { key: 'p' + j, className: 'tag ' + c.tag }, c.tagText || c.text));
        if (c.href) links.push(h('a', { key: 'l' + j, href: c.href, target: '_blank', rel: 'noopener' }, c.linkText || 'Quelle'));
        if (c.align === 'right' && c.text) metrics.push(h('div', { key: 'm' + j, className: 'metric' + (t.cardHero === j ? ' hero' : '') }, h('span', { className: 'k' }, head), h('span', { className: 'v' + (c.neg ? ' neg' : '') }, c.text)));
        else if (!c.tag && !c.href && c.text) rest.push(h('span', { key: 'r' + j }, head ? head + ': ' + c.text : c.text));
      });
      var props = { key: i, className: 'row-card' + (r.weight === 600 ? ' on' : ''), onClick: r.onClick };
      if (r.onClick) { props.tabIndex = 0; props.role = 'button'; props['aria-pressed'] = r.weight === 600; props.onKeyDown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.onClick(); } }; }
      return h('div', props,
        h('span', { className: 'row-num' }, String(i + 1)),
        h('div', null,
          h('div', { className: 'row-title' }, title.text, title.tag ? h(React.Fragment, null, ' ', h('span', { className: 'tag ' + title.tag }, title.tagText)) : null),
          title.sub ? h('div', { className: 'row-sub' }, title.sub) : null,
          text.text ? h('div', { className: 'row-text' }, text.text) : null,
          rest.length ? h('div', { className: 'row-sub' }, rest.map(function (x, j) { return h(React.Fragment, { key: j }, j ? ' · ' : null, x); })) : null,
          pills.length || links.length ? h('div', { className: 'row-sub', style: { display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 } }, pills, links) : null),
        metrics.length ? h('div', { className: 'row-metrics' }, metrics) : null);
    }));
  }

  function renderTables(o) {
    return o.tables.map(function (t) {
      return h('section', { key: t.key, className: 'card' },
        h('div', { className: 'card-h' },
          h('h3', null, t.title),
          t.tags.map(function (g, j) { return Pill(g, j); }),
          t.count ? h('span', { className: 'count' }, t.count) : null,
          h('span', { className: 'right' },
            t.collapsible ? S.Ghost(t.rowsLabel, t.toggleRows, t.rowsOpen) : null,
            t.hasDefs ? S.Ghost(t.defsLabel, t.toggleDefs, t.defsOpen) : null)),
        t.note ? h('p', { className: 'note' }, t.note) : null,
        t.defsOpen ? S.Defs(t.defs) : null,
        t.rowsOpen ? h(React.Fragment, null,
          t.hasRows ? (t.cards ? RowCards(t, o) : S.Table(t, o.tableFont, o.cellPad, o.padLeft)) : null,
          t.showEmpty ? h('p', { className: 'note', style: { fontStyle: 'italic' } }, t.empty) : null) : null,
        t.footLead ? h(React.Fragment, null,
          h('p', { className: 'note', style: { fontWeight: 600, color: 'var(--c-text)' } }, t.footLead),
          h('ul', { className: 'note', style: { paddingLeft: 18 } }, t.footLines.map(function (l, j) { return h('li', { key: j }, l.v); }))) : null,
        t.foot ? h('p', { className: 'note' }, t.foot) : null);
    });
  }

  function renderDossier(o) {
    if (!o.hasDossier) return null;
    var d = o.dossier, H4 = function (t) { return h('h4', { style: { fontSize: 13, margin: '0 0 4px' } }, t); };
    return h('section', { 'aria-label': 'Stellschraube im Detail', className: 'card', style: { display: 'grid', gap: 18 } },
      h('div', { style: { display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px 24px' } },
        h('div', null, h('div', { className: 'kv-label' }, 'Stellschraube ' + d.id + ' · gewählt'),
          h('h3', { style: { fontSize: 18, marginTop: 2 } }, d.name + ': ', h('span', { style: { fontWeight: 400 } }, d.handle))),
        h('div', { className: 'kpi-value' + (d.sumNeg ? ' neg' : ''), style: { fontSize: 24 } }, d.sum)),
      h('div', { className: 'dossier-grid' },
        h('div', null, H4('Prüffrage'), h('p', { style: { margin: 0, fontSize: 13.5 } }, d.frage), h('p', { className: 'note' }, d.bedeutung)),
        h('div', null, H4('Was man tun kann'), h('ul', { style: { margin: 0, paddingLeft: 18, display: 'grid', gap: 3, fontSize: 13 } }, d.tun.map(function (x, i) { return h('li', { key: i }, x.v); }))),
        h('div', null, H4('Wer dreht daran'),
          h('dl', { style: { margin: 0, display: 'grid', gap: 6, fontSize: 13 } }, d.meta.map(function (x, i) {
            return h('div', { key: i }, h('dt', { className: 'kv-label' }, x.k, ' ', x.changed ? h('span', { className: 'tag tag-accent-2' }, 'geändert') : null),
              h('dd', { style: { margin: '1px 0 0' } }, x.v), x.note ? h('dd', { className: 'note', style: { margin: '1px 0 0' } }, x.note) : null);
          })))),
      h('div', null, H4('Herleitung, zwölf Monate'),
        h('div', { className: 'dossier-kv' },
          d.kv.map(function (x, i) { return h('div', { key: i }, h('div', { className: 'kv-label' }, x.k), h('div', { className: 'kpi-value', style: { fontSize: 18, marginTop: 2 } }, x.v), h('div', { className: 'note', style: { margin: '1px 0 0' } }, x.s)); })),
        d.eventNote ? h('p', { className: 'note' }, d.eventNote) : null),
      d.hasChannel && d.channel ? h('div', null, H4(d.channel.title),
        S.Table({ cols: d.channel.cols, rows: d.channel.rows }, o.tableFont, o.cellPad, o.padLeft, { maxWidth: 860 }),
        d.channel.foot ? h('p', { className: 'note' }, d.channel.foot) : null) : null,
      h('div', { style: { maxWidth: 760 } }, H4('Ein Gerät, vorgerechnet'),
        d.hasExample && d.example ? h(React.Fragment, null,
          h('p', { style: { margin: 0, fontSize: 13 } }, h('strong', null, d.example.head), ' · ' + d.example.delta),
          h('dl', { style: { margin: '6px 0 0', display: 'grid', gridTemplateColumns: '1fr max-content', gap: '3px 16px', fontSize: 13 } }, (d.example.rows || []).map(function (r, i) { return h(React.Fragment, { key: i }, h('dt', { style: { color: 'var(--c-text-2)' } }, r.k), h('dd', { style: { margin: 0, textAlign: 'right' } }, r.v)); })),
          d.example.formula ? h('p', { className: 'note' }, d.example.formula) : null,
          d.example.note ? h('p', { className: 'note' }, d.example.note) : null) : null,
        d.noExample ? h('p', { className: 'note', style: { fontStyle: 'italic' } }, 'Im Fenster kein Gerät mit einem Hebel größer null.') : null));
  }

  function renderBlocks(o) {
    if (!o.hasBlocks) return null;
    var list = function (b, tag) {
      return h(tag, null, b.items.map(function (it, i) {
        return h('li', { key: i }, it.lead ? h('strong', null, it.lead) : null, it.lead ? ' ' : null, it.text,
          it.href ? h(React.Fragment, null, ' ', h('a', { href: it.href, target: '_blank', rel: 'noopener' }, it.linkText || 'Quelle')) : null);
      }));
    };
    return h('section', { 'aria-label': 'Methode und Grenzen', className: 'acc' },
      o.blocks.map(function (b) {
        return h('div', { key: b.key, className: 'acc-item' },
          h('button', { type: 'button', className: 'blk-toggle', onClick: b.toggle, 'aria-expanded': b.open },
            h('span', { className: 'caret' }, b.caret), b.title,
            b.hasTag ? h('span', { className: 'tag ' + b.tagCls }, b.tagText) : null),
          b.open ? h('div', { className: 'acc-body' },
            b.intro ? h('p', null, b.intro) : null,
            b.items.length ? (b.ordered ? list(b, 'ol') : list(b, 'ul')) : null) : null);
      }));
  }

  function renderDialog(o) {
    if (!o.dlgOpen) return null;
    var f = o.form, body = null;
    var input = function (id, props) { return h('input', Object.assign({ className: 'input', id: id }, props)); };
    var lbl = function (id, text) { return h('label', { htmlFor: id }, text); };
    var grid = function (min) { return { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(' + min + 'px, 1fr))', gap: 12 }; };
    if (o.dlgAnchor) {
      body = h('div', { style: { display: 'grid', gap: 12 } },
        h('div', { style: grid(150) },
          S.Field('Geräteart', 'rw-a-fam', S.Select('rw-a-fam', o.aFams, o.onAFamily)),
          S.Field('Hersteller', 'rw-a-oem', S.Select('rw-a-oem', o.aOems, o.onAOem)),
          S.Field('Modell', 'rw-a-model', S.Select('rw-a-model', o.aModels, o.onAModel)),
          S.Field('Ausstattung', 'rw-a-dev', S.Select('rw-a-dev', o.aSpecs, o.onASpec))),
        f.devInfo ? h('div', { className: 'note', style: { marginTop: -6 } }, f.devInfo) : null,
        h('div', { style: grid(160) },
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
        h('div', { className: 'field' }, lbl('rw-d-feed', 'Kanal'), S.Select('rw-d-feed', o.feedOpts, o.setFeed)),
        h('div', { className: 'field' }, lbl('rw-d-file', 'Datei (CSV oder Textexport des Quellsystems)'), input('rw-d-file', { type: 'file', accept: '.csv,.txt,.tsv', onChange: o.onFile })),
        f.fileName ? h('dl', { style: { margin: 0, display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 14px', fontSize: 13 } },
          h('dt', { className: 'note' }, 'Datei'), h('dd', { style: { margin: 0 } }, f.fileName), h('dt', { className: 'note' }, 'Zeilen'), h('dd', { style: { margin: 0 } }, f.rowsText), h('dt', { className: 'note' }, 'Spalten'), h('dd', { style: { margin: 0 } }, f.colsText)) : null);
    }
    if (o.dlgThreshold) {
      body = h('div', { style: { display: 'grid', gap: 12 } },
        h('dl', { style: { margin: 0, display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 14px', fontSize: 13 } }, h('dt', { className: 'note' }, 'Bisher'), h('dd', { style: { margin: 0 } }, f.current), h('dt', { className: 'note' }, 'Regel'), h('dd', { style: { margin: 0 } }, f.rule)),
        h('div', { className: 'field' }, lbl('rw-t-text', 'Neue Schwelle'), input('rw-t-text', { value: f.text || '', onChange: o.setText, placeholder: 'Wortlaut der neuen Schwelle', autoFocus: true })),
        h('div', { className: 'field' }, lbl('rw-t-note', 'Begründung'), h('textarea', { className: 'input', id: 'rw-t-note', rows: 2, value: f.note || '', onChange: o.setNote, placeholder: 'Warum die Schwelle geändert wird' })),
        h('div', { className: 'field' }, lbl('rw-t-by', 'Geändert durch (Rolle)'), input('rw-t-by', { value: f.by || '', onChange: o.setBy })));
    }
    if (o.dlgAssumption) {
      var np = { type: 'number', step: '0.5', value: f.disc || '', onChange: o.setDisc, style: { textAlign: 'right' }, autoFocus: true };
      if (typeof o.discMin === 'number') np.min = String(o.discMin * 100);
      if (typeof o.discMax === 'number') np.max = String(o.discMax * 100);
      body = h('div', { style: { display: 'grid', gap: 12 } },
        h('div', { className: 'field', style: { maxWidth: 220 } }, lbl('rw-as-disc', 'Einkaufsabschlag auf die UVP, %'), input('rw-as-disc', np)),
        h('div', { className: 'field' }, lbl('rw-as-by', 'Geändert durch (Rolle)'), input('rw-as-by', { value: f.by || '', onChange: o.setBy })));
    }
    if (o.dlgScenario) {
      body = h('div', { style: { display: 'grid', gap: 12 } },
        h('div', { className: 'field' }, lbl('rw-s-name', 'Name des Szenarios'), input('rw-s-name', { value: f.name || '', onChange: o.setName, placeholder: f.defaultName || '', autoFocus: true })),
        h('p', { className: 'note' }, f.summary));
    }
    return h('div', { className: 'dialog-backdrop', style: { zIndex: 20 }, onClick: o.onBackdrop },
      h('div', { className: 'dialog', role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'rw-dlg-title', style: { maxHeight: '92vh', overflow: 'auto' }, onClick: o.stop, onKeyDown: o.onDlgKey },
        h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' } }, h('div', { className: 'dialog-title', id: 'rw-dlg-title' }, o.dlgTitle), o.dlgKicker ? h('span', { className: 'note', style: { margin: 0 } }, o.dlgKicker) : null),
        o.dlgHint ? h('p', { className: 'dialog-body', style: { margin: 0 } }, o.dlgHint) : null,
        body,
        f.error ? h('p', { style: { margin: 0, fontSize: 13, color: 'var(--c-red)' } }, f.error) : null,
        h('div', { className: 'dialog-actions' }, h('button', { type: 'button', className: 'btn btn-secondary', onClick: o.closeDlg }, 'Abbrechen'), h('button', { type: 'button', className: 'btn btn-primary', onClick: o.submitDlg }, o.dlgSubmit))));
  }

  /* ---------- die Seite ---------- */
  function render(o) {
    var app = this;
    var record = null;
    if (o.ready) {
      record = h(React.Fragment, null,
        h('div', { style: { display: 'grid', gap: 4 } },
          h('span', { className: 'kicker' }, o.kicker), h('h2', { className: 'subject' }, o.subject),
          o.hasFacts ? h('dl', { style: { display: 'flex', flexWrap: 'wrap', gap: '4px 22px', margin: '4px 0 0', fontSize: 12.5, color: 'var(--c-text-2)' } },
            o.facts.map(function (x, i) { return h('div', { key: i, style: { display: 'flex', gap: 6, alignItems: 'baseline', margin: 0, flexWrap: 'wrap' } }, h('dt', { className: 'kv-label' }, x.k), h('dd', { style: { margin: 0, color: 'var(--c-text)' } }, x.v, x.href ? h(React.Fragment, null, ' ', h('a', { href: x.href, target: '_blank', rel: 'noopener', style: { fontSize: 11.5 } }, 'Quelle')) : null)); })) : null,
          o.intro ? h('div', null, S.Ghost(o.introLabel, o.toggleIntro, o.introOpen), o.introOpen ? h('p', { className: 'note', style: { maxWidth: '100ch', fontSize: 13 } }, o.intro) : null) : null),
        o.hasSteps ? h('ol', { className: 'card', style: { display: 'flex', flexWrap: 'wrap', gap: '8px 22px', listStyle: 'none', margin: 0 } },
          o.steps.map(function (s, i) { return h('li', { key: i, style: { display: 'flex', gap: 6, alignItems: 'baseline' } }, h('span', { style: { fontSize: 11, color: 'var(--c-green-3)' } }, s.k), h('span', { style: { fontWeight: 600, fontSize: 13 } }, s.t), h('span', { className: 'note', style: { margin: 0, fontSize: 12 } }, s.q)); })) : null,
        renderKpis(o),
        o.calcnote ? h('p', { className: 'note', style: { margin: 0 } }, o.calcnote) : null,
        o.hasActions ? h('section', { className: 'card' }, h('div', { className: 'card-h' }, h('h3', null, 'Was man daraus macht')),
          h('ol', { style: { margin: '8px 0 0', paddingLeft: 20, maxWidth: '100ch', display: 'grid', gap: 6, fontSize: 13 } }, o.actions.map(function (a, i) { return h('li', { key: i }, h('strong', null, a.lead), ' ', a.text); }))) : null,
        o.hasChart ? h('figure', { className: 'card' }, h('div', { ref: app.chartRef, className: 'chart' }), o.chartNote ? h('figcaption', null, o.chartNote) : null) : null,
        o.tablesLast ? null : renderTables(o),
        renderDossier(o),
        renderBlocks(o),
        o.tablesLast ? renderTables(o) : null);
    }
    return h('div', { className: 'app ck' },
      renderSide(app, o),
      h('div', { className: 'ck-main' },
        renderTop(app, o),
        renderStatus(o),
        h('main', { className: 'main ck-content' },
          renderLog(o),
          renderSectionBar(o),
          renderDeviceSettings(o),
          o.loading ? h('p', { className: 'loading', style: { margin: 0, fontSize: 13 } }, 'Daten werden geladen …') : null,
          o.error ? h('p', { role: 'alert', style: { margin: 0, fontSize: 13, color: 'var(--c-red)' } }, o.error) : null,
          record)),
      renderDialog(o));
  }

  w.RE_SKIN = {
    name: 'cockpit',
    init: function (shell) { S = shell; h = shell.h; React = shell.React; },
    render: render,
    renderKpis: renderKpis, renderTables: renderTables, renderDossier: renderDossier, renderBlocks: renderBlocks,
    renderDialog: renderDialog, renderLog: renderLog, renderSectionBar: renderSectionBar, renderDeviceSettings: renderDeviceSettings
  };
})(window);
