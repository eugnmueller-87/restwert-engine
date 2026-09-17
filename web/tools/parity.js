// Restwert Engine, Seite: Paritaet der Zahlen je Tab gegen die abgelegte Referenz.
// Aufruf: node web/tools/parity.js [tab]   (vorher: node web/tools/test_page.js)
// Vergleicht web/ref/<tab>.txt (sichtbarer Text der abgenommenen Version) mit web/out/<tab>.txt (aktueller Bau):
// jede Zahl der Referenz (Ziffernfolge mit Punkt, Komma, %, Euro) muss in v3 vorkommen, als ganze Zahl (nicht als
// Teil einer laengeren Ziffernfolge). Fehlende Zahlen werden gelistet. Exit 1, wenn eine fehlt.
'use strict';
const fs = require('fs'), path = require('path');
const V3 = path.join(__dirname, '..');
const TABS = ['report', 'device', 'forecast', 'tco', 'cycle', 'levers', 'term', 'kpis', 'market', 'series', 'studies', 'faq', 'lake'];
const distArg = process.argv.find(a => a.startsWith('--dist='));   // --dist=dist-cockpit: die Ausgabe der zweiten Optik (out-dist-cockpit/)
const OUT_DIR = distArg ? 'out-' + distArg.slice(7) : 'out';
const only = process.argv.slice(2).find(a => !a.startsWith('--')) || null;
if (only && !TABS.includes(only)) { console.error('unbekannter Tab: ' + only); process.exit(2); }

const NUM = /\d[\d.,]*(?:\s?[%€])?/g;
function norm(s) { return s.replace(/ /g, ' ').replace(/[ \t]+/g, ' '); }
function tokens(text) {
  const out = []; let m; const re = new RegExp(NUM.source, 'g');
  while ((m = re.exec(text))) { const v = m[0].replace(/[.,]+$/, '').trim(); if (v) out.push(v); }
  return out;
}
function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
const cache = new Map();
function present(tok, text) {
  let re = cache.get(tok);
  if (!re) { re = new RegExp('(?<![\\d.,])' + esc(tok) + '(?![\\d.,]*\\d)'); cache.set(tok, re); }
  return re.test(text);
}

let failed = false;
const lines = [];
for (const tab of TABS) {
  if (only && tab !== only) continue;
  const refPath = path.join(V3, 'ref', tab + '.txt'), outPath = path.join(V3, OUT_DIR, tab + '.txt');
  if (!fs.existsSync(refPath)) { lines.push(tab + ': Referenz fehlt (' + path.relative(V3, refPath) + ')'); failed = true; continue; }
  if (!fs.existsSync(outPath)) { lines.push(tab + ': Ausgabe fehlt (' + path.relative(V3, outPath) + '); erst node v3/test_v3.js ' + tab); failed = true; continue; }
  const ref = norm(fs.readFileSync(refPath, 'utf8')), out = norm(fs.readFileSync(outPath, 'utf8'));
  const toks = tokens(ref);
  const missing = new Map();
  let viaComma = 0;
  for (const t of toks) {
    if (present(t, out)) continue;
    // Dezimalpunkt in der Referenz (Formatfehler der heutigen Seite, etwa "5.7 %"): die deutsche Schreibweise zaehlt als Treffer
    const m = /^(\d+)\.(\d{1,2})(\s?[%€])?$/.exec(t);
    if (m && present(m[1] + ',' + m[2] + (m[3] || ''), out)) { viaComma++; continue; }
    // ganze Zahl ohne Tausenderpunkt in der Referenz (etwa "1000"): die gruppierte Schreibweise zaehlt als Treffer
    const g = /^(\d{4,})(\s?[%€])?$/.exec(t);
    if (g && present(g[1].replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (g[2] || ''), out)) { viaComma++; continue; }
    missing.set(t, (missing.get(t) || 0) + 1);
  }
  const nMiss = [...missing.values()].reduce((a, b) => a + b, 0);
  const pct = toks.length ? Math.round((1 - nMiss / toks.length) * 1000) / 10 : 100;
  lines.push(tab.padEnd(7) + ' Zahlen in der Referenz ' + String(toks.length).padStart(5) + ', davon in v3 ' + String(toks.length - nMiss).padStart(5) + ' (' + String(pct).replace('.', ',') + ' %), fehlend ' + nMiss + ' (' + missing.size + ' verschiedene)' + (viaComma ? '; ' + viaComma + ' weitere nur in deutscher Schreibweise gefunden (Referenz mit Dezimalpunkt oder ohne Tausenderpunkt)' : ''));
  if (missing.size) {
    failed = true;
    const list = [...missing.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'de'));
    const shown = list.slice(0, 60).map(([t, n]) => t + (n > 1 ? ' (' + n + 'x)' : ''));
    lines.push('    fehlt: ' + shown.join(' | ') + (list.length > 60 ? ' | ... und ' + (list.length - 60) + ' weitere' : ''));
  }
}
console.log(lines.join('\n'));
// Der volle Lauf schreibt sein Ergebnis mit Zeitstempel nach out/_parity.log (ueberschreibend), damit keine Kopie ohne
// Datum liegen bleibt; ein Lauf fuer einen Tab schreibt nichts, das Log bleibt der letzte volle Stand.
if (!only) {
  const d = new Date(), p2 = n => String(n).padStart(2, '0');
  const head = 'Paritaet, Stand ' + p2(d.getDate()) + '.' + p2(d.getMonth() + 1) + '.' + d.getFullYear() + ' ' + p2(d.getHours()) + ':' + p2(d.getMinutes()) + (failed ? ' (mit Fehlbetraegen)' : ' (ohne Fehlbetrag)');
  fs.writeFileSync(path.join(V3, OUT_DIR, '_parity.log'), head + '\n' + lines.join('\n') + '\n', 'utf8');
}
process.exit(failed ? 1 : 0);
