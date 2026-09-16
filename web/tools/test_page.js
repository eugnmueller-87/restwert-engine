// Restwert Engine, Seite: Testlauf der Huelle in jsdom.
// Aufruf: node web/tools/test_page.js [tab] [--no-ui]   (vorher: python web/build.py)
// Baut web/dist/index.html (cdnjs-Skripte entfernt, React und ReactDOM aus node_modules, Plotly-Stub, localStorage leer),
// klickt jeden Reiter, klappt alles auf (jeder Knopf mit aria-expanded="false"), schreibt den sichtbaren Text nach
// web/out/<tab>.txt (Tabellenzeilen als "a | b | c") und sammelt Konsolenfehler.
// Danach laufen die Bedienungsschritte (Klick, dann Text pruefen): Reiter, Statuszeile, Protokoll, Kennzahlen, Hinweise,
// Tabellen, Klappbloecke, Diagramme, Auswahl und Aktionen je Bereich, Dialoge, Export, Lauf, Wiederherstellung aus
// localStorage in einem zweiten Fenster. Mit einem Tab als Argument laufen nur die Schritte dieses Bereichs plus die
// allgemeinen; --no-ui laesst sie ganz weg. Exit 1 bei Fehlern.
// Zum Schluss: Striche (U+2013, U+2014) und Hedge-Woerter im Code von web/dist/** (Daten ausgenommen), externe Adressen
// der Vorlage, Skelett-Tags, Tokens beider Themen, Phone-Breite.
'use strict';
const fs = require('fs'), path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const V3 = path.join(__dirname, '..');
const distArg = process.argv.find(a => a.startsWith('--dist='));   // --dist=dist-cockpit prueft die zweite Optik
const APP = path.join(V3, distArg ? distArg.slice(7) : 'dist'), OUT = path.join(V3, distArg ? 'out-' + distArg.slice(7) : 'out');
const NM = path.join(V3, 'node_modules');
// Reiter mit ihrem Bereich (Kopfzeile seit 16.09.2026: Bericht, Analytics, Market Intelligence, Daten; die Reiter eines Bereichs stehen in der zweiten Zeile)
const TABS = [['report', 'Bericht', 'Bericht'], ['device', 'Gerät', 'Analytics'], ['forecast', 'Prognosegüte', 'Analytics'], ['tco', 'TCO', 'Analytics'], ['cycle', 'Kreislauf', 'Analytics'], ['levers', 'Stellschrauben', 'Analytics'], ['term', 'Laufzeit', 'Analytics'],
  ['market', 'Realisierung', 'Market Intelligence'], ['series', 'Serie gegen Serie', 'Market Intelligence'], ['studies', 'Studien', 'Market Intelligence'], ['faq', 'FAQ', 'Market Intelligence'], ['lake', 'Daten', 'Daten']];
const groupOf = label => (TABS.find(t => t[1] === label) || [])[2] || label;
const ARGS = process.argv.slice(2);
const NO_UI = ARGS.includes('--no-ui');
const only = ARGS.find(a => !a.startsWith('--')) || null;
const STYLESHEET = (() => { try { const tpl = fs.readdirSync(APP).find(f => /template\.html$/.test(f)); const m = tpl && /<link rel="stylesheet" href="([a-z.-]+\.css)">/.exec(fs.readFileSync(path.join(APP, tpl), 'utf8')); return m ? m[1] : 'styles.css'; } catch (e) { return 'styles.css'; } })();
if (only && !TABS.some(t => t[0] === only)) { console.error('unbekannter Tab: ' + only + ' (erlaubt: ' + TABS.map(t => t[0]).join(', ') + ')'); process.exit(2); }
const labelOf = key => (TABS.find(t => t[0] === key) || [])[1] || '';
const on = key => !only || only === key;

const sleep = ms => new Promise(r => setTimeout(r, ms));
const errors = [];
let currentTab = '';
function err(msg) { errors.push((currentTab ? currentTab + ': ' : '') + msg); }

// ---- 1. Seite lesen: Links raus, Skript-Adressen einsammeln ----
const indexPath = path.join(APP, 'index.html');
if (!fs.existsSync(indexPath)) { console.error('web/dist/index.html fehlt; erst python web/build.py laufen lassen'); process.exit(2); }
let html = fs.readFileSync(indexPath, 'utf8');
html = html.replace(/<link[^>]*>\s*/g, '');
const localScripts = [];
html = html.replace(/<script src="([^"]+)"><\/script>\s*/g, (m, src) => { if (!/^https?:/i.test(src)) localScripts.push(src); return ''; });
const cssText = fs.existsSync(path.join(APP, STYLESHEET)) ? fs.readFileSync(path.join(APP, STYLESHEET), 'utf8') : '';

// ---- 2. Fenster: die Seite mit allen Skripten, Plotly-Stub, Blob-Pfad ohne Navigation ----
let w = null, plotly = null, blobs = null;
function makeWindow(seed) {
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => err('jsdom: ' + (e && e.message ? e.message : String(e)) + (e && e.detail && e.detail.stack ? '\n    ' + String(e.detail.stack).split('\n').slice(0, 3).join('\n    ') : '')));
  vc.on('error', (...a) => err('console.error: ' + a.map(x => (x && x.stack) ? String(x.stack).split('\n').slice(0, 2).join(' ') : String(x)).join(' ')));
  vc.on('warn', () => {}); vc.on('log', () => {}); vc.on('info', () => {}); vc.on('debug', () => {});
  const dom = new JSDOM('<!doctype html><html><head><title>Restwert Engine</title></head><body></body></html>', { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc, url: 'http://localhost/v3/app/' });
  const win = dom.window;
  win.scrollTo = () => {};
  const pl = { react: 0, newPlot: 0, resize: 0, last: null, downloads: 0 };
  win.Plotly = {
    react: (el, traces, layout, config) => { pl.react++; pl.last = { traces: traces, layout: layout, config: config }; },
    newPlot: (el, traces, layout, config) => { pl.newPlot++; pl.last = { traces: traces, layout: layout, config: config }; },
    Plots: { resize: () => { pl.resize++; } }
  };
  // Export CSV: der Blob entsteht, die Ablage wird gezaehlt statt navigiert (jsdom kennt keine Navigation)
  const bl = [];
  win.URL.createObjectURL = b => { bl.push(b); return 'blob:restwert-' + bl.length; };
  win.URL.revokeObjectURL = () => {};
  win.HTMLAnchorElement.prototype.click = function () { pl.downloads++; };
  try { win.localStorage.clear(); } catch (e) {}
  if (seed) for (const k of Object.keys(seed)) { try { win.localStorage.setItem(k, seed[k]); } catch (e) { err('localStorage seed ' + k + ': ' + e.message); } }
  if (cssText) { const st = win.document.createElement('style'); st.textContent = cssText; win.document.head.appendChild(st); }
  win.document.body.innerHTML = html;
  const run = (file, code) => { const s = win.document.createElement('script'); s.textContent = code + '\n//# sourceURL=' + file; win.document.body.appendChild(s); };
  run('react.production.min.js', fs.readFileSync(path.join(NM, 'react', 'umd', 'react.production.min.js'), 'utf8'));
  run('react-dom.production.min.js', fs.readFileSync(path.join(NM, 'react-dom', 'umd', 'react-dom.production.min.js'), 'utf8'));
  for (const src of localScripts) {
    const p = path.join(APP, src);
    if (!fs.existsSync(p)) { err('Skript fehlt: ' + src); continue; }
    run(src, fs.readFileSync(p, 'utf8'));
  }
  return { w: win, plotly: pl, blobs: bl };
}
function activate(win) { w = win.w; plotly = win.plotly; blobs = win.blobs; }
activate(makeWindow(null));

// ---- 3. Sichtbarer Text ----
const BLOCK = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'LI', 'TR', 'DT', 'DD', 'FIGCAPTION', 'SECTION', 'DIV', 'TABLE', 'THEAD', 'TBODY', 'UL', 'OL', 'DL', 'BUTTON', 'LABEL', 'OPTION', 'DETAILS', 'SUMMARY', 'FIGURE', 'HEADER', 'MAIN', 'NAV']);
const SKIP = new Set(['SCRIPT', 'STYLE', 'DATALIST', 'TEMPLATE', 'TITLE']);
const NL = String.fromCharCode(1);
function walk(el, out) {
  if (el.nodeType === 3) { const t = el.textContent.replace(/\s+/g, ' ').trim(); if (t) out.push(t); return; }
  if (el.nodeType !== 1) return;
  if (el.hidden || SKIP.has(el.tagName)) return;
  const st = el.getAttribute('style') || '';
  if (/display\s*:\s*none/.test(st)) return;
  if (el.tagName === 'TR') { out.push(NL + [...el.children].map(c => c.textContent.replace(/\s+/g, ' ').trim()).join(' | ') + NL); return; }
  if (BLOCK.has(el.tagName)) out.push(NL);
  for (const c of el.childNodes) walk(c, out);
  if (BLOCK.has(el.tagName)) out.push(NL);
}
function dump() {
  const root = w.document.getElementById('app'); const out = []; walk(root, out);
  return out.join(' ').replace(/\x01/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n').replace(/\n{2,}/g, '\n').trim();
}
async function waitFor(pred, ms, what) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { try { if (pred()) return true; } catch (e) {} await sleep(40); }
  err('Zeitüberschreitung: ' + what); return false;
}
async function expandAll() {
  for (let i = 0; i < 10; i++) {
    const btns = [...w.document.querySelectorAll('#app button[aria-expanded="false"]')];
    if (!btns.length) return;
    btns.forEach(b => b.click());
    await sleep(120);
  }
}
const rendered = () => w.document.querySelector('#app h2.subject') || w.document.querySelector('#app [role="alert"]');

// ---- 3b. Bedienung durchklicken ----
async function uiSmoke() {
  const $ = sel => w.document.querySelector(sel);
  const $$ = sel => [...w.document.querySelectorAll(sel)];
  // ein offener Dialog hat Vorrang: "Szenario speichern" gibt es als Bereichsknopf UND als Dialogknopf
  const btn = text => { const pools = [$$('#app [role="dialog"] button'), $$('#app button')]; for (const pool of pools) { const hit = pool.find(b => b.textContent.trim() === text) || pool.find(b => b.textContent.trim().startsWith(text)); if (hit) return hit; } return null; };
  const setValue = (el, v) => {
    const proto = el.tagName === 'SELECT' ? w.HTMLSelectElement.prototype : (el.tagName === 'TEXTAREA' ? w.HTMLTextAreaElement.prototype : w.HTMLInputElement.prototype);
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new w.Event('input', { bubbles: true })); el.dispatchEvent(new w.Event('change', { bubbles: true }));
  };
  const expect = (cond, what) => { if (!cond) err('erwartet: ' + what); return !!cond; };
  const click = (text, what) => { const b = btn(text); expect(b, 'Knopf "' + text + '"' + (what ? ' (' + what + ')' : '')); if (b) b.click(); return b; };
  const step = async (key, name, fn) => {
    if (key !== 'all' && !on(key)) return;
    currentTab = 'ui ' + name;
    try { await fn(); } catch (e) { err(String(e && e.stack ? e.stack.split('\n').slice(0, 2).join(' ') : e)); }
    await sleep(150);
  };
  const tab = async label => { const g = groupOf(label); if (g !== label && !btn(label)) { click(g, 'Bereich'); await sleep(200); } click(label); await sleep(250); };
  const dialog = () => $('#app [role="dialog"]');
  const dialogText = () => { const d = dialog(); return d ? d.textContent : ''; };
  const mainText = () => $('#app main').textContent;
  const logSection = () => $('#app section[aria-label="Protokoll"]');
  const logText = () => { const s = logSection(); return s ? s.textContent : ''; };
  const logRows = () => $$('#app section[aria-label="Protokoll"] tbody tr').length;
  const status = () => $('#app .status').textContent;
  const current = () => { const b = $$('#app nav button').find(x => x.getAttribute('aria-current') === 'page'); return b ? b.textContent.trim() : ''; };
  const store = (k, def) => { try { const v = JSON.parse(w.localStorage.getItem('restwert-' + k)); return v === null || v === undefined ? def : v; } catch (e) { return def; } };
  const segOpts = labelId => $$('#app [aria-labelledby="' + labelId + '"] .seg-opt');
  const segOn = labelId => (segOpts(labelId).find(x => x.classList.contains('seg-on')) || { textContent: '' }).textContent.trim();
  const fmt = w.RE.fmt;
  const data = name => JSON.parse(w.document.getElementById('data-' + name).textContent);
  const L = data('lake'), CFG = data('config').purchase_discount_pct, DEV = data('device');
  const pctLabel = share => fmt.pct1(share); // eine Schreibweise fuer den Einkaufsabschlag: Huelle, Realisierung und Laufzeit (CONTRACT 7.3)
  const rowsBtnIn = root => [...root.querySelectorAll('section button')].find(x => /Zeilen (zeigen|ausblenden)$/.test(x.textContent.trim()));

  await step('all', 'Reiter: geplante Bereiche ausgegraut', async () => {
    const planned = $$('#app nav[aria-label="Bereiche"] button').filter(b => b.disabled);
    expect(planned.map(b => b.textContent.trim()).join(', ') === 'Lager, Verträge', 'Lager und Verträge ausgegraut (ist: ' + planned.map(b => b.textContent.trim()).join(', ') + ')');
    planned.forEach(b => expect(b.title === 'Als Nächstes geplant', 'Hinweis "Als Nächstes geplant" auf ' + b.textContent.trim()));
    const before = current(); planned.forEach(b => b.click()); await sleep(200);
    expect(current() === before, 'Klick auf einen geplanten Bereich wechselt nicht (vorher ' + before + ', jetzt ' + current() + ')');
  });
  await step('all', 'Reiter: Wechsel, Betreff, Kicker, gemerkt', async () => {
    for (const [key, label] of TABS) {
      if (!on(key)) continue;
      await tab(label);
      expect(current() === label, 'Reiter ' + label + ' aktiv');
      expect(($('#app h2.subject') || { textContent: '' }).textContent.trim().length > 0, 'Betreff auf ' + label);
      expect(($('#app .kicker') || { textContent: '' }).textContent.trim().length > 0, 'Kicker auf ' + label);
      expect(w.localStorage.getItem('restwert-tab') === key, 'Reiter gemerkt: ' + key);
    }
  });
  await step('all', 'Statuszeile aus dem lake-Modell', async () => {
    const s = status();
    expect(s.includes('Letzter Lauf Stand ' + fmt.de(L.today)), 'Letzter Lauf zeigt den Stand der Daten (' + fmt.de(L.today) + ')');
    expect(s.includes('Dateien ' + fmt.qty(L.tot.files)), 'Dateien ' + fmt.qty(L.tot.files));
    expect(s.includes('Zeilen ' + fmt.qty(L.tot.read)), 'Zeilen ' + fmt.qty(L.tot.read));
    expect(s.includes('ungeklärt ' + fmt.qty(L.tot.unresolved)), 'ungeklärt ' + fmt.qty(L.tot.unresolved));
    expect(s.includes('Seriennummern ' + fmt.qty(L.serials)), 'Seriennummern ' + fmt.qty(L.serials));
    expect(s.includes('Prototyp: Läufe werden protokolliert, nicht gerechnet.'), 'Prototyp-Satz');
    expect(!s.includes('offen für den nächsten Lauf'), 'noch nichts offen für den nächsten Lauf');
  });
  await step('all', 'Protokoll auf und zu', async () => {
    const b = btn('Protokoll ('); if (!expect(b, 'Knopf Protokoll')) return;
    if (b.getAttribute('aria-expanded') === 'true') { b.click(); await sleep(150); }
    expect(!logSection(), 'Protokoll zu');
    click('Protokoll ('); await sleep(150);
    expect(logSection() && logText().includes('Protokoll dieser Installation') && logText().includes('Noch keine Einträge'), 'Protokoll offen und leer');
    click('Protokoll ('); await sleep(150); expect(!logSection(), 'Protokoll wieder zu');
    click('Protokoll ('); await sleep(150); expect(logSection(), 'Protokoll bleibt für die nächsten Schritte offen');
  });
  await step('all', 'Kennzahlen: Herleitung und Begriffe auf und zu', async () => {
    await tab(only ? labelOf(only) : 'Bericht');
    const kpiBtn = text => $$('#app section[aria-label="Ergebnis"] + div button').find(b => b.textContent.trim() === text);
    const liCount = () => $$('#app section[aria-label="Ergebnis"] li').length;
    const defs = () => $('#app section[aria-label="Ergebnis"] + div + dl.defs');
    const kpiN = $$('#app .kpi-value').length;
    expect(kpiN > 0, 'Kennzahlen vorhanden');
    if (kpiBtn('Weniger')) { kpiBtn('Weniger').click(); await sleep(150); }
    if (kpiBtn('Herleitung') || !only) {
      const closed = liCount();
      expect(closed === kpiN, 'eingeklappt eine Bullet je Kennzahl (' + closed + ' bei ' + kpiN + ' Kennzahlen)');
      const b1 = kpiBtn('Herleitung'); if (expect(b1, 'Knopf Herleitung')) { b1.click(); await sleep(150); }
      expect(liCount() > closed, 'Herleitung zeigt weitere Bullets (' + liCount() + ' nach ' + closed + ')');
      expect(kpiBtn('Weniger'), 'Knopf heißt jetzt Weniger');
      if (kpiBtn('Weniger')) { kpiBtn('Weniger').click(); await sleep(150); }
      expect(liCount() === closed, 'Herleitung wieder zu');
    }
    if (kpiBtn('Begriffe ausblenden')) { kpiBtn('Begriffe ausblenden').click(); await sleep(150); }
    if (kpiBtn('Begriffe') || !only) {
      expect(!defs(), 'Begriffe zu');
      const b2 = kpiBtn('Begriffe'); if (expect(b2, 'Knopf Begriffe unter den Kennzahlen')) { b2.click(); await sleep(150); }
      expect(defs() && defs().querySelectorAll('dt').length > 0, 'Begriffe zeigen mindestens einen Eintrag (seit 16.09.2026 ohne den Eintrag QTY: Stückzahlen heißen, was sie zählen)');
      expect(kpiBtn('Begriffe ausblenden'), 'Knopf heißt jetzt Begriffe ausblenden');
      if (kpiBtn('Begriffe ausblenden')) { kpiBtn('Begriffe ausblenden').click(); await sleep(150); }
      expect(!defs(), 'Begriffe wieder zu');
    }
  });
  await step('all', 'Hinweise auf und zu', async () => {
    const b = () => $$('#app main button').find(x => /^Hinweise/.test(x.textContent.trim()));
    if (!b()) { if (!only) err('Knopf Hinweise fehlt'); return; }
    const p = () => b().parentElement.querySelector('p');
    if (b().getAttribute('aria-expanded') === 'true') { b().click(); await sleep(150); }
    expect(!p(), 'Hinweise zu');
    b().click(); await sleep(150);
    expect(p() && p().textContent.trim().length > 20, 'Hinweise zeigen den Absatz');
    expect(b().textContent.trim() === 'Hinweise ausblenden', 'Knopf heißt jetzt Hinweise ausblenden');
    b().click(); await sleep(150); expect(!p(), 'Hinweise wieder zu');
  });
  await step('all', 'Tabellen: Zeilen zeigen und Begriffe', async () => {
    await tab(only ? labelOf(only) : 'Realisierung');
    let b = rowsBtnIn($('#app main'));
    if (!b) { if (!only) err('kein Knopf "Zeilen zeigen" auf Realisierung'); return; }
    if (/ausblenden/.test(b.textContent)) { b.click(); await sleep(200); }
    b = rowsBtnIn($('#app main')); const sec = b.closest('section');
    expect(sec.querySelectorAll('tbody tr').length === 0, 'eingeklappt keine Zeilen');
    const m = /^(\S+) Zeilen zeigen$/.exec(b.textContent.trim());
    expect(m, 'Knopf nennt die Zeilenzahl (' + b.textContent.trim() + ')');
    b.click(); await sleep(250);
    const n = sec.querySelectorAll('tbody tr').length;
    expect(n > 0 && m && fmt.qty(n) === m[1], 'Zeilen sichtbar: ' + n + ' bei angekündigt ' + (m && m[1]));
    b = rowsBtnIn(sec.parentElement) || rowsBtnIn($('#app main'));
    expect(b && b.textContent.trim() === 'Zeilen ausblenden', 'Knopf heißt jetzt Zeilen ausblenden');
    const ths = sec.querySelectorAll('thead th').length;
    const defsBtn = () => [...sec.querySelectorAll('button')].find(x => /^Begriffe/.test(x.textContent.trim()));
    if (expect(defsBtn(), 'Knopf Begriffe an der Tabelle')) {
      if (defsBtn().textContent.trim() === 'Begriffe ausblenden') { defsBtn().click(); await sleep(150); }
      expect(!sec.querySelector('dl.defs'), 'Tabellenbegriffe zu');
      defsBtn().click(); await sleep(150);
      const dts = sec.querySelectorAll('dl.defs dt').length;
      expect(dts > 0 && ths > 0, 'Begriffe zur Tabelle sichtbar (' + dts + ' Begriffe, ' + ths + ' Spalten)');
      defsBtn().click(); await sleep(150); expect(!sec.querySelector('dl.defs'), 'Tabellenbegriffe wieder zu');
    }
    if (b) { b.click(); await sleep(200); }
    expect(sec.querySelectorAll('tbody tr').length === 0, 'Zeilen wieder weg');
  });
  await step('all', 'Klappblöcke Methode und Grenzen', async () => {
    const tg = $$('#app .blk-toggle');
    if (!tg.length) { if (!only) err('keine Klappblöcke'); return; }
    const b = tg[0], wrap = b.parentElement;
    if (b.getAttribute('aria-expanded') === 'true') { b.click(); await sleep(150); }
    expect(wrap.querySelectorAll('li').length === 0, 'Block zu');
    b.click(); await sleep(150);
    expect(b.getAttribute('aria-expanded') === 'true' && wrap.querySelectorAll('li').length > 0, 'Block offen mit Punkten');
    b.click(); await sleep(150); expect(wrap.querySelectorAll('li').length === 0, 'Block wieder zu');
  });
  await step('all', 'Diagramme mit Traces', async () => {
    for (const [key, label] of [['device', 'Gerät'], ['market', 'Realisierung'], ['term', 'Laufzeit']]) {
      if (!on(key)) continue;
      await tab('Bericht'); // erst weg vom Zieltab: der aktive Reiter zeichnet zu Recht nicht neu
      const before = plotly.react; await tab(label); await sleep(300);
      expect($('#app figure .chart'), 'Diagrammfläche auf ' + label);
      if (!expect(plotly.react > before, 'Plotly.react auf ' + label)) continue;
      const c = plotly.last;
      expect(Array.isArray(c.traces) && c.traces.length > 0, 'Traces auf ' + label + ' (' + (c.traces || []).length + ')');
      expect(c.traces.every(t => Array.isArray(t.y) && t.y.length > 0), 'jede Trace mit y-Werten auf ' + label);
      // seit 16.09.2026: Legende unter der Zeichnung, ein Eintrag je Zeile; der untere Rand waechst mit den Eintraegen, die Hoehe mit dem Rand
      const nLeg = (c.traces || []).filter(x => x.showlegend !== false && x.name).length;
      expect(c.layout.margin && c.layout.margin.t === 24 && c.layout.margin.b === 44 + (nLeg ? 8 + nLeg * 20 : 0) + 8 && c.layout.height === 24 + 320 + c.layout.margin.b, 'Höhe und Ränder kommen von der Hülle auf ' + label + ' (' + nLeg + ' Legendeneinträge)');
      expect(c.layout.legend && c.layout.legend.orientation === 'v' && c.layout.legend.yanchor === 'top' && c.layout.legend.y < 0, 'Legende unterhalb der Zeichnung auf ' + label);
      expect(c.layout.paper_bgcolor === 'rgba(0,0,0,0)' && c.layout.plot_bgcolor === 'rgba(0,0,0,0)', 'durchsichtiger Hintergrund auf ' + label);
      expect(!/[\u2013\u2014]/.test(JSON.stringify([c.traces, c.layout])), 'kein Strich im Diagramm auf ' + label);
      expect(c.config && c.config.displayModeBar === false && c.config.responsive === true, 'ohne Werkzeugleiste, mitwachsend auf ' + label);
    }
    if (on('report')) { await tab('Bericht'); expect(!$('#app figure'), 'kein Diagramm auf Bericht'); }
  });

  await step('device', 'Gerät: Geräteart wählen', async () => { await tab('Gerät'); setValue($('#rw-fam'), 'Smartphone'); await sleep(250); expect(($('#app dl') || {}).textContent.includes('Smartphone'), 'Steckbrief zeigt Smartphone'); });
  await step('device', 'Gerät: Hersteller, Modell, Ausstattung', async () => {
    const oem = $('#rw-oem'); setValue(oem, oem.options[oem.options.length - 1].value); await sleep(250);
    expect($('#rw-oem').value === oem.options[oem.options.length - 1].value, 'Hersteller übernommen');
    const model = $('#rw-model'); setValue(model, model.options[model.options.length - 1].value); await sleep(250);
    expect($('#rw-model').value === model.options[model.options.length - 1].value, 'Modell übernommen');
    const spec = $('#rw-spec'); setValue(spec, spec.options[spec.options.length - 1].value); await sleep(250);
    expect($('#rw-spec').value === spec.options[spec.options.length - 1].value, 'Ausstattung übernommen');
    const dev = DEV.devices.find(x => x.id === $('#rw-spec').value);
    expect(dev && mainText().includes(dev.model), 'Betreff nennt das Modell ' + (dev ? dev.model : '?'));
    expect(w.localStorage.getItem('restwert-device') === $('#rw-spec').value, 'Gerät gemerkt');
  });
  await step('device', 'Gerät: Laufzeit und Kauf', async () => {
    const l = segOpts('rw-term-l').find(x => x.textContent.trim() === '36 Monate'); expect(l, 'Segment 36 Monate'); if (l) l.querySelector('input').click(); await sleep(250);
    expect(segOn('rw-term-l') === '36 Monate' && mainText().includes('36 Monate'), 'Laufzeit 36 Monate wirksam');
    const k = segOpts('rw-when-l').find(x => x.textContent.trim() === 'heute'); if (k) k.querySelector('input').click(); await sleep(250);
    expect(segOn('rw-when-l') === 'heute' && mainText().includes('Kauf heute'), 'Kauf heute wirksam');
  });
  await step('device', 'Gerät: Eingabe überschreiben und zurücksetzen', async () => {
    const buy = $('#rw-buy'); const before = buy.value; buy.focus(); await sleep(60); setValue(buy, '700'); await sleep(60); buy.blur(); await sleep(250);
    expect(btn('Vorbelegung wiederherstellen'), 'Knopf Vorbelegung wiederherstellen nach Eingabe (vorher ' + before + ')');
    expect($('#rw-buy').value === '700', 'Eingabe bleibt stehen (ist ' + $('#rw-buy').value + ')');
    click('Vorbelegung wiederherstellen'); await sleep(250);
    expect(!btn('Vorbelegung wiederherstellen'), 'Knopf verschwindet nach dem Zurücksetzen');
    expect($('#rw-buy').value === before, 'Vorbelegung wieder da');
  });
  await step('device', 'Gerät: Szenario speichern, laden, löschen', async () => {
    const before = logRows();
    click('Szenario speichern', 'Bereichsaktion'); await sleep(200); expect(dialog() && dialogText().includes('Szenario speichern'), 'Dialog Szenario offen');
    expect(dialogText().includes('Lifecycle-Marge'), 'Dialog fasst das Szenario zusammen');
    setValue($('#rw-s-name'), 'Probe'); await sleep(100); click('Szenario speichern', 'Dialog'); await sleep(300);
    expect(!dialog(), 'Dialog geschlossen'); expect(mainText().includes('Gespeicherte Szenarien') && mainText().includes('Probe'), 'Szenarientabelle mit "Probe"');
    expect(logRows() === before + 1 && logText().includes('Probe') && logText().includes('gespeichert'), 'Protokoll nennt das gespeicherte Szenario');
    click('Laden'); await sleep(250); click('Löschen'); await sleep(250); expect(!mainText().includes('Gespeicherte Szenarien'), 'Szenarientabelle weg nach Löschen');
    expect(logRows() === before + 2 && logText().includes('gelöscht'), 'Protokoll nennt das Löschen');
  });
  await step('device', 'Gerät: Preisbeleg erfassen', async () => {
    const before = logRows(), drawn = plotly.react;
    click('Preisbeleg erfassen'); await sleep(200); expect(dialog() && dialogText().includes('Preisbeleg erfassen'), 'Dialog Preisbeleg offen');
    expect($('#rw-a-dev').value.length > 0 && dialogText().includes('UVP'), 'Gerät vorbelegt mit UVP');
    click('Beleg speichern'); await sleep(200); expect(dialog() && dialogText().includes('nötig'), 'Fehlertext ohne Preis');
    setValue($('#rw-a-price'), '400'); await sleep(200); expect(dialogText().includes('Realisierung:'), 'Vorschau Realisierung');
    click('Beleg speichern'); await sleep(300); expect(!dialog(), 'Dialog geschlossen');
    expect(mainText().includes('manuell'), 'manueller Beleg in der Tabelle');
    expect(logRows() === before + 1 && logText().includes('erfasst'), 'Protokoll nennt den Beleg');
    expect(plotly.react > drawn, 'Diagramm mit dem Beleg neu gezeichnet');
    const pending = store('manual-anchors', []).length + Object.keys(store('thresholds', {})).length;
    expect(status().includes('offen für den nächsten Lauf ' + fmt.qty(pending)), 'Statuszeile zählt offene Änderungen (' + pending + ')');
  });

  await step('market', 'Realisierung: Familie und Diagramm', async () => {
    await tab('Realisierung');
    const l = segOpts('rw-mfam-l').find(x => x.textContent.trim() === 'Laptop'); expect(l, 'Segment Laptop'); if (l) l.querySelector('input').click(); await sleep(300);
    expect(segOn('rw-mfam-l') === 'Laptop' && mainText().includes('Preisbelege Laptop'), 'Tabelle der Familie Laptop');
    const drawn = plotly.react;
    const v = segOpts('rw-view-l').find(x => x.textContent.trim() === 'Einzelne Preisbelege'); expect(v, 'Segment Einzelne Preisbelege'); if (v) v.querySelector('input').click(); await sleep(300);
    expect(segOn('rw-view-l') === 'Einzelne Preisbelege' && plotly.react > drawn, 'Detail-Diagramm gezeichnet');
    expect(plotly.last && plotly.last.traces.some(t => t.mode === 'markers'), 'Detail-Diagramm mit Punkten');
    const k = segOpts('rw-view-l').find(x => x.textContent.trim() === 'Kurven je Hersteller'); if (k) k.querySelector('input').click(); await sleep(300);
    expect(segOn('rw-view-l') === 'Kurven je Hersteller', 'zurück auf Kurven je Hersteller');
  });
  await step('market', 'Realisierung: Preisbeleg erfassen', async () => {
    const fam = segOn('rw-mfam-l'); const dev = DEV.devices.find(x => x.family === fam && x.rrp > 0);
    if (!expect(dev, 'Gerät der Familie ' + fam + ' im Katalog')) return;
    const before = logRows();
    click('Preisbeleg erfassen'); await sleep(200); expect(dialog() && dialogText().includes('Preisbeleg erfassen'), 'Dialog Preisbeleg offen');
    // Das Geraet kommt aus den vier Auswahlfeldern des Katalogs (wie auf Geraet), vorbelegt mit der gewaehlten Familie
    expect($('#rw-a-fam') && $('#rw-a-fam').value === fam && dialogText().includes('UVP'), 'Geräteart im Dialog vorbelegt mit ' + fam);
    click('Beleg speichern'); await sleep(200); expect(dialog() && dialogText().includes('nötig'), 'Fehlertext ohne Preis');
    setValue($('#rw-a-fam'), dev.family); await sleep(150); setValue($('#rw-a-oem'), dev.oem); await sleep(150); setValue($('#rw-a-model'), dev.model); await sleep(150); setValue($('#rw-a-dev'), dev.id); await sleep(150);
    expect($('#rw-a-dev').value === dev.id && dialogText().includes('UVP ' + fmt.eur(dev.rrp)), 'Gerät über die vier Auswahlfelder gewählt, UVP ' + fmt.eur(dev.rrp));
    setValue($('#rw-a-price'), '333'); await sleep(150); expect(dialogText().includes(fmt.pct(333 / dev.rrp)), 'Vorschau ' + fmt.pct(333 / dev.rrp));
    click('Beleg speichern'); await sleep(300); expect(!dialog(), 'Dialog geschlossen');
    expect(logRows() === before + 1 && logText().includes(dev.label) && logText().includes('erfasst'), 'Protokoll nennt den Beleg');
    const b = rowsBtnIn($('#app main')); if (b && /zeigen$/.test(b.textContent)) { b.click(); await sleep(300); }
    expect(mainText().includes('manuell'), 'manueller Beleg in der Belegtabelle');
    const drawn = plotly.react;
    const v = segOpts('rw-view-l').find(x => x.textContent.trim() === 'Einzelne Preisbelege'); if (v) v.querySelector('input').click(); await sleep(300);
    expect(plotly.react > drawn && JSON.stringify(plotly.last.traces).includes('manuell'), 'Detail-Diagramm zählt den manuellen Beleg');
  });
  await step('market', 'Realisierung: Annahme Einkaufsabschlag aus der Konfiguration', async () => {
    const cur = store('assumptions', { disc: CFG.value }).disc;
    expect(Math.abs(cur - CFG.value) < 1e-9, 'Abschlag kommt aus data/config.json (' + CFG.value + ')');
    expect(btn('Annahme: Einkaufsabschlag ' + pctLabel(cur)), 'Knopf zeigt den Abschlag ' + pctLabel(cur));
    expect(mainText().includes(fmt.pct1(cur)), 'Motor nennt den Abschlag ' + fmt.pct1(cur));
    const before = logRows();
    click('Annahme: Einkaufsabschlag'); await sleep(200); expect(dialog() && dialogText().includes('Annahme ändern'), 'Dialog Annahme offen');
    expect(CFG.owner ? dialogText().includes(CFG.owner) : true, 'Dialog nennt den Verantwortlichen aus der Konfiguration');
    expect($('#rw-as-disc').value === String(Math.round(cur * 1000) / 10), 'Feld vorbelegt mit ' + String(Math.round(cur * 1000) / 10) + ' (ist "' + $('#rw-as-disc').value + '")');
    setValue($('#rw-as-disc'), '150'); click('Annahme speichern'); await sleep(200); expect(dialog() && dialogText().includes('Prozent'), 'Fehlertext bei unsinnigem Abschlag');
    setValue($('#rw-as-disc'), '12.5'); click('Annahme speichern'); await sleep(300); expect(!dialog(), 'Dialog geschlossen (Nachkommastelle erlaubt)');
    expect(btn('Annahme: Einkaufsabschlag 12,5 %'), 'Knopf zeigt 12,5 %');
    click('Annahme: Einkaufsabschlag'); await sleep(200); expect($('#rw-as-disc').value === '12.5', 'Feld hält den Wert mit Nachkommastelle (ist "' + $('#rw-as-disc').value + '")');
    setValue($('#rw-as-disc'), '15'); click('Annahme speichern'); await sleep(300); expect(!dialog(), 'Dialog geschlossen');
    expect(btn('Annahme: Einkaufsabschlag ' + pctLabel(0.15)), 'Knopf zeigt den neuen Abschlag'); expect(mainText().includes('15,0 %'), 'Motor rechnet mit dem neuen Abschlag');
    expect(logRows() === before + 2 && logText().includes('Einkaufsabschlag auf ' + pctLabel(0.15) + ' gesetzt'), 'Protokoll nennt die Annahme');
  });

  await step('tco', 'TCO: Auswahl und Segmente', async () => {
    await tab('TCO');
    const fam = $('#rw-tfam'); const other = [...fam.options].find(o => o.value !== fam.value);
    if (other) { setValue(fam, other.value); await sleep(300); expect($('#rw-tfam').value === other.value, 'Geräteart gewechselt auf ' + other.value); }
    const oem = $('#rw-toem'); const o2 = oem.options[oem.options.length - 1]; setValue(oem, o2.value); await sleep(300); expect($('#rw-toem').value === o2.value, 'Hersteller übernommen');
    const model = $('#rw-tmodel'); const m2 = model.options[model.options.length - 1]; setValue(model, m2.value); await sleep(300);
    expect($('#rw-tmodel').value === m2.value, 'Modell übernommen'); expect(mainText().includes(m2.textContent.trim()), 'Betreff nennt ' + m2.textContent.trim());
    let st = store('tco', null); expect(st && st.slug === m2.value, 'TCO gemerkt');
    const so = segOpts('rw-tst-l').filter(x => !x.classList.contains('seg-on')); expect(so.length > 0, 'weitere Ausstattung wählbar');
    if (so.length) { so[so.length - 1].querySelector('input').click(); await sleep(300); expect(segOn('rw-tst-l') === so[so.length - 1].textContent.trim(), 'Ausstattung gewechselt'); }
    const termOpt = segOpts('rw-tterm-l').filter(x => /^\d+ Monate/.test(x.textContent.trim()) && !x.classList.contains('seg-on')).pop(); expect(termOpt, 'Laufzeit mit Daten wählbar');
    const termVal = termOpt ? /^(\d+) Monate/.exec(termOpt.textContent.trim())[1] : '';
    if (termOpt) { termOpt.querySelector('input').click(); await sleep(300); expect(segOn('rw-tterm-l').startsWith(termVal + ' Monate'), 'Laufzeit ' + termVal + ' Monate gewählt'); expect(mainText().includes(termVal + ' Monate'), 'Text nennt die Laufzeit'); }
    st = store('tco', null); expect(st && st.term === termVal && st.storage !== 'all', 'Ausstattung und Laufzeit gemerkt (' + JSON.stringify(st) + ')');
  });

  await step('levers', 'Stellschrauben: Zeile wählen, Schwelle ändern', async () => {
    await tab('Stellschrauben');
    const rows = $$('#app main section:not([aria-label="Protokoll"]) table.table tbody tr, #app main .row-card[role="button"]'); expect(rows.length > 1, 'Übersicht mit Zeilen'); if (rows[1]) rows[1].click(); await sleep(300);
    expect(btn('Schwelle ändern: L01'), 'Dossier wechselt auf die zweite Zeile (L01)');
    const before = logRows();
    click('Schwelle ändern:'); await sleep(200); expect(dialog() && dialogText().includes('Schwelle ändern'), 'Dialog Schwelle offen');
    expect(dialogText().includes('Bisher') && dialogText().includes('Regel'), 'Dialog zeigt bisherige Schwelle und Regel');
    click('Schwelle speichern'); await sleep(200); expect(dialog() && dialogText().includes('nennen'), 'Fehlertext ohne Text');
    setValue($('#rw-t-text'), 'Probe-Schwelle'); setValue($('#rw-t-note'), 'Testlauf'); click('Schwelle speichern'); await sleep(300);
    expect(!dialog(), 'Dialog geschlossen'); expect(mainText().includes('geändert') && mainText().includes('Probe-Schwelle'), 'Dossier zeigt die geänderte Schwelle');
    expect(logRows() === before + 1 && logText().includes('Probe-Schwelle') && logText().includes('ab nächstem Lauf'), 'Protokoll nennt die Schwelle');
  });

  await step('lake', 'Daten: Datei einlesen, freigeben, entfernen', async () => {
    await tab(on('cycle') ? 'Kreislauf' : 'Daten');
    const before = logRows();
    click('Datei einlesen'); await sleep(200); expect(dialog() && dialogText().includes('Datei einlesen'), 'Dialog Datei offen');
    click('Probelauf ablegen'); await sleep(200); expect(dialog() && dialogText().includes('Datei wählen'), 'Fehlertext ohne Datei');
    const input = $('#rw-d-file'); const file = new w.File(['a;b;c\n1;2;3\n4;5;6\n'], 'probe.csv', { type: 'text/csv' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true }); input.dispatchEvent(new w.Event('change', { bubbles: true })); await sleep(400);
    expect(dialogText().includes(fmt.qty(2) + ' Datenzeilen') && dialogText().includes(fmt.qty(3) + ': a, b, c'), 'Datei gezählt: Zeilen und Spalten');
    click('Probelauf ablegen'); await sleep(400); expect(!dialog(), 'Dialog geschlossen');
    expect(current() === 'Daten', 'Sprung auf Daten');
    expect(mainText().includes('probe.csv') && mainText().includes('Probelauf'), 'Lieferung in der Tabelle als Probelauf');
    expect(logRows() === before + 1 && logText().includes('probe.csv'), 'Protokoll nennt die Lieferung');
    const files0 = L.tot.files;
    click('Freigeben'); await sleep(300); expect(mainText().includes('freigegeben'), 'Status freigegeben');
    expect(status().includes('Dateien ' + fmt.qty(files0 + 1)), 'Statuszeile zählt die freigegebene Datei mit (' + fmt.qty(files0 + 1) + ')');
    expect(logRows() === before + 2 && logText().includes('freigegeben für den nächsten Lauf'), 'Protokoll nennt die Freigabe');
    click('Entfernen'); await sleep(300); expect(mainText().includes('Noch keine Datei eingelesen.'), 'Leertext nach Entfernen');
    expect(status().includes('Dateien ' + fmt.qty(files0)), 'Statuszeile wieder bei ' + fmt.qty(files0));
    expect(logRows() === before + 3 && logText().includes('entfernt'), 'Protokoll nennt das Entfernen');
  });

  await step('all', 'Export CSV', async () => {
    const before = logRows(), nb = blobs.length, nd = plotly.downloads;
    click('Export CSV'); await sleep(250);
    expect(blobs.length === nb + 1, 'Blob erzeugt');
    const b = blobs[blobs.length - 1];
    expect(b && String(b.type).startsWith('text/csv') && b.size > 100, 'CSV mit Inhalt (' + (b ? b.size : 0) + ' Bytes)');
    expect(plotly.downloads === nd + 1, 'Ablage ausgelöst');
    expect(logRows() === before + 1 && logText().includes('als CSV exportiert'), 'Protokoll nennt den Export');
  });
  await step('all', 'Lauf starten', async () => {
    const before = logRows();
    click('Lauf starten'); await sleep(150); expect(btn('Lauf läuft') && btn('Lauf läuft').disabled, 'Knopf zeigt laufenden Lauf und ist gesperrt');
    await sleep(1700);
    expect(btn('Lauf starten') && !btn('Lauf starten').disabled, 'Knopf wieder frei');
    expect(logRows() === before + 1 && logText().includes('Lauf protokolliert'), 'Protokoll nennt den Lauf');
    const runs = store('runs', []); expect(runs.length >= 1 && status().includes('Letzter Lauf ' + runs[0].at), 'Statuszeile mit dem letzten Lauf');
  });
  await step('all', 'Dialog schließen: Abbrechen, Hintergrund, Escape', async () => {
    click('Datei einlesen'); await sleep(200); expect(dialog(), 'Dialog offen');
    click('Abbrechen'); await sleep(200); expect(!dialog(), 'Abbrechen schließt');
    click('Datei einlesen'); await sleep(200); $('#app .dialog-backdrop').click(); await sleep(200); expect(!dialog(), 'Hintergrundklick schließt');
    click('Datei einlesen'); await sleep(200); $('#rw-d-feed').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await sleep(200); expect(!dialog(), 'Escape schließt');
  });
  await step('all', 'Wiederherstellung aus localStorage', async () => {
    await tab(only ? labelOf(only) : 'TCO');
    const seed = {}; for (let i = 0; i < w.localStorage.length; i++) { const k = w.localStorage.key(i); seed[k] = w.localStorage.getItem(k); }
    const tabKey = seed['restwert-tab'], devId = seed['restwert-device'], tco = store('tco', null), log = store('log', []), runs = store('runs', []);
    const manual = store('manual-anchors', []), thresholds = store('thresholds', {}), asm = store('assumptions', null), deliveries = store('deliveries', []);
    const first = { w: w, plotly: plotly, blobs: blobs };
    activate(makeWindow(seed));
    await waitFor(rendered, 15000, 'zweites Fenster gerendert'); await sleep(300);
    expect(current() === labelOf(tabKey), 'Reiter wiederhergestellt: ' + labelOf(tabKey) + ' (ist ' + current() + ')');
    expect(btn('Protokoll (' + fmt.qty(log.length) + ')'), 'Protokoll zählt die gespeicherten Einträge (' + log.length + ')');
    if (runs.length) expect(status().includes('Letzter Lauf ' + runs[0].at), 'letzter Lauf wiederhergestellt');
    const pending = manual.length + Object.keys(thresholds).length + deliveries.filter(x => x.status === 'freigegeben').length;
    if (pending) expect(status().includes('offen für den nächsten Lauf ' + fmt.qty(pending)), 'offene Änderungen wiederhergestellt (' + pending + ')');
    if (tco && on('tco')) {
      if (current() !== 'TCO') await tab('TCO');
      expect($('#rw-tmodel').value === tco.slug, 'TCO-Modell wiederhergestellt (' + tco.slug + ')');
      expect(segOn('rw-tterm-l').startsWith(fmt.qty(tco.term) + ' Monate'), 'TCO-Laufzeit wiederhergestellt (' + segOn('rw-tterm-l') + ')');
      expect(segOn('rw-tst-l') !== 'alle Ausstattungen', 'TCO-Ausstattung wiederhergestellt');
    }
    if (devId && on('device')) { await tab('Gerät'); expect($('#rw-spec').value === devId, 'Gerät wiederhergestellt (' + devId + ')'); }
    if (asm && on('market')) { await tab('Realisierung'); expect(btn('Annahme: Einkaufsabschlag ' + pctLabel(asm.disc)), 'Annahme wiederhergestellt'); }
    if (manual.length && on('device')) { await tab('Gerät'); expect(mainText().includes('manuell'), 'manuelle Preisbelege wiederhergestellt'); }
    if (Object.keys(thresholds).length && on('levers')) {
      await tab('Stellschrauben'); const rows = $$('#app main section:not([aria-label="Protokoll"]) table.table tbody tr, #app main .row-card[role="button"]'); if (rows[1]) rows[1].click(); await sleep(300);
      expect(mainText().includes('geändert') && mainText().includes('Probe-Schwelle'), 'geänderte Schwelle wiederhergestellt');
    }
    click('Protokoll ('); await sleep(200); expect(logRows() === log.length, 'Protokolleinträge wiederhergestellt (' + logRows() + ' von ' + log.length + ')');
    activate(first);
  });
  currentTab = '';
}

// ---- 4. Lauf ----
(async () => {
  const summary = [];
  await waitFor(rendered, 15000, 'erstes Rendern');
  fs.mkdirSync(OUT, { recursive: true });
  for (const [key, label] of TABS) {
    if (only && key !== only) continue;
    currentTab = key;
    const before = plotly.react;
    const group = groupOf(label);
    const navBtn = text => [...w.document.querySelectorAll('#app nav button')].find(b => b.textContent.trim() === text);
    if (group !== label) { const g = navBtn(group); if (!g) { err('Bereich nicht gefunden: ' + group); continue; } g.click(); await sleep(150); }
    const btn = navBtn(label);
    if (!btn) { err('Reiter nicht gefunden: ' + label); continue; }
    btn.click();
    await waitFor(() => btn.getAttribute('aria-current') === 'page', 5000, 'Reiter aktiv');
    await waitFor(rendered, 15000, 'Inhalt gerendert');
    await sleep(150);
    await expandAll();
    await sleep(100);
    const alert = w.document.querySelector('#app [role="alert"]');
    if (alert) err('Hinweis auf der Seite: ' + alert.textContent.trim());
    // Vertrag, Abnahme Punkt 3: jede Zeile so viele Zellen wie Koepfe, jede Tabelle Begriffe mit einem Satz je Spalte
    for (const tbl of w.document.querySelectorAll('#app main table.table')) {
      const sec = tbl.closest('section'); if (!sec || sec.getAttribute('aria-label') === 'Protokoll') continue;
      const title = (sec.querySelector('h3') || { textContent: '?' }).textContent.trim();
      const th = tbl.querySelectorAll('thead th').length;
      tbl.querySelectorAll('tbody tr').forEach((tr, i) => { if (tr.children.length !== th) err('Tabelle "' + title + '": Zeile ' + (i + 1) + ' hat ' + tr.children.length + ' Zellen bei ' + th + ' Köpfen'); });
      const dts = sec.querySelectorAll('dl.defs dt').length;
      if (!dts) err('Tabelle "' + title + '": keine Begriffe (defs) zur Tabelle');
      else if (dts * 2 < th) err('Tabelle "' + title + '": ' + dts + ' Begriffe bei ' + th + ' Spalten (ein Begriff darf zwei Spalten fassen, nicht mehr)');
    }
    const text = dump();
    fs.writeFileSync(path.join(OUT, key + '.txt'), text + '\n', 'utf8');
    const tables = w.document.querySelectorAll('#app table.table').length;
    const kpis = w.document.querySelectorAll('#app .kpi-value').length;
    summary.push(key.padEnd(7) + ' ' + String(text.split('\n').length).padStart(5) + ' Zeilen Text, ' + String(kpis).padStart(2) + ' Kennzahlen, ' + String(tables).padStart(2) + ' Tabellen, Plotly.react ' + (plotly.react - before) + 'x');
  }
  currentTab = '';
  const uiErrorsBefore = errors.length;
  if (!NO_UI) await uiSmoke();
  const uiErrors = errors.length - uiErrorsBefore;

  // ---- 5. Striche und Hedge-Woerter im Code (Daten ausgenommen) ----
  currentTab = 'code';
  const HEDGE = /(^|[^\wäöüÄÖÜß])(vielleicht|eventuell|evtl\.?|möglicherweise|wahrscheinlich|vermutlich|womöglich|gegebenenfalls|ggf\.?|hoffentlich|könnten?|sollten?|versuchen|unter Umständen)(?![\wäöüÄÖÜß])/gi;
  const TPL_NAME = fs.readdirSync(APP).find(f => /template\.html$/.test(f)) || 'index.template.html';
  const files = [path.join(APP, 'app.js'), path.join(APP, STYLESHEET), path.join(APP, TPL_NAME), path.join(APP, 'skin-cockpit.js')];
  const engDir = path.join(APP, 'engine');
  if (fs.existsSync(engDir)) for (const f of fs.readdirSync(engDir)) if (f.endsWith('.js')) files.push(path.join(engDir, f));
  const stripped = fs.readFileSync(indexPath, 'utf8').replace(/<script type="application\/json"[^>]*>[\s\S]*?<\/script>/g, '');
  const texts = files.filter(f => fs.existsSync(f)).map(f => [path.relative(V3, f), fs.readFileSync(f, 'utf8')]);
  texts.push(['app/index.html (ohne Daten)', stripped]);
  for (const [name, text] of texts) {
    text.split('\n').forEach((line, i) => {
      // styles.css ist Eugens Design-System (Broadsheet) woertlich; Striche stehen dort nur in Kommentaren und sind kein sichtbarer Text
      if (/[\u2013\u2014]/.test(line) && !name.endsWith('styles.css')) err(name + ':' + (i + 1) + ': Strich: ' + line.trim().slice(0, 120));
      let m; HEDGE.lastIndex = 0;
      while ((m = HEDGE.exec(line))) err(name + ':' + (i + 1) + ': Hedge-Wort "' + m[2] + '": ' + line.trim().slice(0, 120));
    });
  }
  // Striche auch in den Daten: die Seite darf nirgends einen zeigen
  if (/[\u2013\u2014]/.test(fs.readFileSync(indexPath, 'utf8'))) err('app/index.html: Strich in den Daten');

  // ---- 6. Vorlage, Skelett, Themen, Phone-Breite ----
  currentTab = 'vorlage';
  const tpl = fs.existsSync(path.join(APP, TPL_NAME)) ? fs.readFileSync(path.join(APP, TPL_NAME), 'utf8') : '';
  (tpl.match(/https?:\/\/[^"'\s>]+/g) || []).forEach(u => {
    if (!/^https:\/\/(cdnjs\.cloudflare\.com|fonts\.googleapis\.com)(\/|$)/.test(u)) err('externe Adresse außerhalb von cdnjs und fonts.googleapis.com: ' + u);
  });
  (tpl.match(/<script src="https?:[^"]+"/g) || []).forEach(s => { if (!/cdnjs\.cloudflare\.com\/ajax\/libs\/[^/]+\/\d+\.\d+\.\d+\//.test(s)) err('Skript ohne feste Version oder nicht von cdnjs: ' + s); });
  if (!/^\s*<title>/.test(tpl)) err('Vorlage beginnt nicht mit <title>');
  const skel = stripped.match(/<!doctype[^>]*>|<html[^>]*>|<head[^>]*>|<body[^>]*>/gi);
  if (skel) err('Skelett-Tag in index.html: ' + skel.join(', '));
  currentTab = 'themen';
  const rootBlock = (cssText.match(/:root\s*\{([^}]*)\}/) || [])[1] || '';
  const darkMedia = (cssText.match(/@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}/) || [])[1] || '';
  const darkAttr = (cssText.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/) || [])[1] || '';
  const tokens = block => new Set((block.match(/--color-[\w-]+(?=\s*:)/g) || []));
  const light = tokens(rootBlock), dm = tokens(darkMedia), da = tokens(darkAttr);
  if (!light.size) err('keine Farbtokens in :root');
  // Broadsheet (Eugens Design-System) ist bewusst ein einziger Look, Zeitungspapier; ein dunkler Block ist keine Vorgabe (14.09.2026)
  for (const t of dm) if (!light.has(t)) err('Token nur im dunklen Block (Medienabfrage): ' + t);
  for (const t of da) if (!light.has(t)) err('Token nur im dunklen Block (data-theme): ' + t);
  for (const t of dm) if (!da.has(t)) err('Token im Medienblock, aber nicht unter data-theme: ' + t);
  for (const t of da) if (!dm.has(t)) err('Token unter data-theme, aber nicht im Medienblock: ' + t);
  const bodyBlock = (cssText.match(/(?:^|\n)body\s*\{([^}]*)\}/) || [])[1] || '';
  if (!/background\s*:\s*var\(--color-bg\)/.test(bodyBlock)) err('body ohne Hintergrund aus dem Token');
  if (!/color\s*:\s*var\(--color-text\)/.test(bodyBlock)) err('body ohne Textfarbe aus dem Token');
  currentTab = 'phone';
  (cssText.match(/min-width\s*:\s*[^;]+/g) || []).forEach(m => { const v = m.split(':')[1].trim(); if (!/^(0|100%|\d{1,2}px|min\(|0px)$/.test(v)) err('min-width in styles.css: ' + m); });
  if (!/\.tablewrap\s*\{[^}]*overflow-x\s*:\s*auto/.test(cssText)) err('.tablewrap ohne overflow-x: auto');
  currentTab = '';

  console.log(summary.join('\n'));
  console.log('Plotly: react ' + plotly.react + 'x, newPlot ' + plotly.newPlot + 'x, resize ' + plotly.resize + 'x');
  console.log('Bedienung: ' + (NO_UI ? 'übersprungen (--no-ui)' : (uiErrors ? uiErrors + ' Befunde' : 'alle Schritte ohne Befund')));
  if (errors.length) {
    console.log('\nFEHLER (' + errors.length + '):');
    errors.forEach(e => console.log('  ' + e));
    process.exit(1);
  }
  console.log('ok: ' + (only ? only : 'alle ' + TABS.length + ' Tabs') + ' ohne Fehler gerendert' + (NO_UI ? '' : ', Bedienung geprüft'));
  process.exit(0);
})().catch(e => { console.error('Testlauf abgebrochen:', e); process.exit(1); });
