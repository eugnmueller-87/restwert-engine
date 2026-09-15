/* Restwert Engine v3: Registerpruefung. Laeuft nach allen Motoren, vor app.js.
   Meldet in der Konsole, welcher Motor oder Helfer fehlt, und haelt die Liste in window.RE.missing. */
(function (w) {
  'use strict';
  var RE = w.RE;
  var TABS = ['report', 'device', 'market', 'forecast', 'tco', 'cycle', 'levers', 'term', 'lake'];
  var HELPERS = ['fmt', 'TABLE', 'H', 'ROW', 'C', 'N'];
  var missing = [];
  if (!RE) {
    console.error('RE: window.RE fehlt; engine/_helpers.js wurde nicht geladen');
    w.RE = { missing: TABS.slice() };
    return;
  }
  HELPERS.forEach(function (h) { if (RE[h] === undefined) { missing.push('Helfer ' + h); console.error('RE: Helfer fehlt: ' + h); } });
  TABS.forEach(function (t) {
    if (typeof RE[t] !== 'function') { missing.push(t); console.error('RE: Motor fehlt: ' + t + ' (engine/' + t + '.js)'); }
    else if (RE[t].version !== 3) console.warn('RE: Motor ' + t + ' ohne version = 3');
  });
  RE.missing = missing;
  RE.tabs = TABS.slice();
})(window);
