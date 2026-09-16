/* Restwert Engine v3, Motor Prognoseguete (forecast). Neu in Version 4 (16.09.2026), kein Port einer alten Seite.
   Reine Funktion: window.RE.forecast(D, opts, P) baut das Ansichtsmodell aus data/forecast.json (erzeugt von
   make_forecast_data.py aus rv_forecast_error_monthly, backtest_result, rv_forecast_of_record, forecast_runs,
   advisories, kpi_values, thresholds.yaml, kpi_targets.yaml). Kein DOM, kein Zustand, keine getippte Zahl:
   jede Zahl kommt aus D und geht durch E.fmt. Diagramm nur mit Farben aus P (Ersatz P0, solange P fehlt). */
(function (w) {
  'use strict';
  var E = w.RE;

  /* Ersatzpalette (helle Tokenfarben aus styles.css), solange die Huelle noch keine Palette gelesen hat */
  var P0 = {
    font: '"Source Serif 4", serif', ink: '#201e1d', muted: '#7d7979', grid: '#d7d3d3', line: '#bab6b6',
    accent: '#0088b0', accent700: '#006786', accent2: '#d6006c', oem: {}
  };
  var TAG_SIM = { cls: 'tag-neutral', text: 'simuliert' };
  var TAG_DER = { cls: 'tag-neutral', text: 'abgeleitet' };

  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function has(x) { return x !== null && x !== undefined; }

  w.RE.forecast = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt, pal = P || P0;
    if (!D || !Array.isArray(D.series) || !D.fam_label) throw new Error('forecast.json unbrauchbar: series oder fam_label fehlen');
    var L = D.latest || null, A = D.adv02 || {}, R = D.record || {}, fams = D.fam_label;
    var famName = function (k) { return fams[k] || k; };
    var minRows = isNum(D.min_rows) ? D.min_rows : 0;
    /* Vorzeichen fuer Verzerrung: plus heisst Prognose zu hoch */
    var sPct = function (x) { return !isNum(x) ? '' : (x >= 0 ? '+' + f.pct1(x) : f.pct1(x)); };
    var pctOr = function (x, alt) { return isNum(x) ? f.pct1(x) : alt; };
    var thin = 'unter ' + f.qty(minRows);
    var monthDe = function (m) { return f.de(m); };
    var months3 = (A.months || []).map(monthDe).join(', ');
    var star = D.series.filter(function (r) { return r.fam === '*'; });
    var starMetric = star.filter(function (r) { return isNum(r.mape); });

    /* ---------- Kopf ---------- */
    var kicker = 'Prognosegüte, Stand ' + f.de(D.today) + ' (Stichtag der Daten ' + f.de(D.as_of) + ')';
    var subject = 'Wie gut die Restwertprognose trifft: die Prognose, die bei der Rückgabe galt, gegen den Preis, den das Gerät dann erzielt hat';
    var intro = 'Jedes verkaufte Gerät wird gegen die Restwertprognose gemessen, die am Tag seiner Rückgabe in Kraft war (der Lauf strikt vor dem Rückgabedatum, Zustandsstufe wie bei der Rückgabe geprüft, Verkaufstag wie erwartet, Kanal Marktplatz). '
      + 'Nachträglich wird nichts verbessert: eine spätere Prognose zählt nicht. Verkäufe ohne Aufbereitung (As-Is) bleiben außen vor, weil dort keine Prognose gilt. '
      + 'Ein Monat bekommt erst ab ' + f.qty(minRows) + ' verkauften Geräten mit Prognose Kennzahlen; darunter stehen nur die Stückzahlen. '
      + 'Simulierte Daten, echte Mechanik: die Flotte ist synthetisch, die Preise folgen einer kalibrierten Kurve, also misst die Güte hier, wie gut das Modell eine bekannte Kurve zurückgewinnt, nicht den Markt. Die Rechnung ist dieselbe wie auf echten Daten.';

    /* ---------- Kennzahlen ---------- */
    var kpis = [];
    if (L) {
      var overTarget = isNum(L.mape) && isNum(L.target) && L.mape > L.target;
      /* Verlaufslinie (Cockpit-Optik): der Fehler der Gesamtreihe je Monat, nur Monate mit Kennzahl, in Zeitfolge */
      var sparkMape = star.filter(function (r) { return isNum(r.mape); }).map(function (r) { return r.mape; });
      kpis.push({
        label: 'Prognosefehler, letzter voller Monat', value: pctOr(L.mape, thin), neg: overTarget, spark: sparkMape,
        tags: [{ cls: 'tag-neutral', text: 'Geschäftssicht' }, TAG_SIM],
        lines: [
          'Monat ' + monthDe(L.month) + ': ' + f.qty(L.n_fc) + ' verkaufte Geräte mit Prognose, ' + f.qty(L.n_as_is) + ' Verkäufe ohne Aufbereitung ausgeschlossen',
          isNum(L.target) ? 'Ziel höchstens ' + f.pct(L.target) + ', Platzhalter, Verantwortlich ' + f.role(L.target_owner || 'CFO') + '; kleiner ist besser' + (overTarget ? '; Ziel verfehlt' : '; Ziel gehalten') : '',
          'mittlerer Fehler je Gerät ' + f.eur(L.mae) + ', gewichtet über die Summen (WAPE) ' + pctOr(L.wape, thin),
          'Summe der Prognosen ' + f.eur(L.sum_fc) + ' gegen Summe erzielt ' + f.eur(L.sum_real) + ' (' + sPct(L.real) + ' Realisierung gegen Prognose)'
        ]
      });
      kpis.push({
        label: 'Verzerrung, letzter voller Monat', value: isNum(L.bias) ? sPct(L.bias) : thin, neg: isNum(L.bias) && L.bias > 0,
        tags: [{ cls: 'tag-neutral', text: 'Geschäftssicht' }, TAG_SIM],
        lines: [
          'plus heißt: Prognose über dem erzielten Preis, also Restwert im Kollateral überschätzt; minus heißt: darunter',
          'die Geschäftssicht misst jeden Verkaufskanal gegen die Marktplatz-Prognose; Mitarbeiterkauf und Großhandel liegen unter dem Marktplatz und zählen hier als Fehler',
          'nur Marktplatz, ' + f.qty(L.n_mkt) + ' Verkäufe: Fehler ' + pctOr(L.mape_mkt, thin) + ', Verzerrung ' + (isNum(L.bias_mkt) ? sPct(L.bias_mkt) : thin)
        ]
      });
      kpis.push({
        label: 'Modellsicht, kanalbereinigt', value: pctOr(L.mape_ch, thin), neg: false,
        tags: [{ cls: 'tag-neutral', text: 'Modellsicht' }, TAG_DER],
        lines: [
          'dieselbe Prognose mal dem Kanalfaktor des tatsächlich genutzten Kanals; ein Kanalabschlag ist kein Modellfehler',
          'Verzerrung kanalbereinigt ' + (isNum(L.bias_ch) ? sPct(L.bias_ch) : thin) + ' gegen ' + sPct(L.bias) + ' in der Geschäftssicht: die Differenz ist der Kanalmix',
          'das ist die Zahl, die der Hinweis zur Nachkalibrierung prüft'
        ]
      });
    }
    var fired = isNum(A.fired) && A.fired > 0;
    kpis.push({
      label: 'Nachkalibrierung', value: fired ? 'prüfen' : 'nicht nötig', neg: fired,
      tags: [{ cls: fired ? 'tag-accent-2' : 'tag-accent', text: 'Hinweis ADV02' }],
      lines: [
        'Verzerrung der letzten drei vollen Monate (' + months3 + '), kanalbereinigt ' + (isNum(A.mean_bias_ch) ? sPct(A.mean_bias_ch) : thin) + ' gegen die Schwelle von plus oder minus ' + (isNum(A.threshold) ? f.pct(A.threshold) : '') ,
        f.qty(A.n_fc) + ' Verkäufe mit Prognose in den drei Monaten; Geschäftssicht derselben Monate ' + (isNum(A.mean_bias) ? sPct(A.mean_bias) : thin),
        'Schwelle forecast_recalibration_bias_pct, Platzhalter, Verantwortlich ' + f.role(A.owner || 'Head of Recommerce') + '; der Hinweis ändert nichts, er bittet einen Menschen um Prüfung' + (fired ? '; ' + f.qty(A.fired) + ' Hinweis im Lauf' : '; kein Hinweis im Lauf')
      ]
    });
    var kpiDefs = [
      { k: 'Prognosefehler', v: 'mittlerer absoluter Fehler in Prozent des erzielten Preises (MAPE), je Verkauf gerechnet und gemittelt; kleiner ist besser.' },
      { k: 'Verzerrung', v: 'mittlere Abweichung mit Vorzeichen (Prognose minus erzielt, in Prozent des erzielten Preises); plus heißt Prognose zu hoch.' },
      { k: 'Gewichtet (WAPE)', v: 'Summe der absoluten Fehler geteilt durch die Summe der erzielten Preise; teure Geräte wiegen mehr.' },
      { k: 'Geschäftssicht', v: 'jeder Verkaufskanal gegen die Marktplatz-Prognose; enthält den Kanalmix und ist die Zahl, die die Geschäftsführung sieht.' },
      { k: 'Modellsicht', v: 'die Prognose mal dem Kanalfaktor des genutzten Kanals; misst nur das Modell. Der Hinweis ADV02 prüft diese Sicht.' },
      { k: 'Prognose bei Rückgabe', v: 'der Prognoselauf, der strikt vor dem Rückgabedatum in Kraft war, mit der geprüften Zustandsstufe, dem erwarteten Verkaufstag und dem Kanal Marktplatz; sie wird nie nachträglich ersetzt.' }
    ];
    var calcnote = 'Rechnung je Verkauf: Fehler = (Prognose bei Rückgabe minus erzielter Bruttopreis) geteilt durch den erzielten Preis. Der Monat ist der Verkaufsmonat. Kennzahlen erst ab ' + f.qty(minRows) + ' Verkäufen mit Prognose im Monat.';

    /* ---------- Diagramm: Fehler und Verzerrung je Monat, alle Geraetearten ---------- */
    var xs = starMetric.map(function (r) { return r.month; });
    var traces = [
      { type: 'scatter', mode: 'lines+markers', name: 'Prognosefehler, Geschäftssicht', x: xs, y: starMetric.map(function (r) { return r.mape; }),
        line: { color: pal.ink, width: 2 }, marker: { size: 5, color: pal.ink },
        text: starMetric.map(function (r) { return monthDe(r.month) + ': Fehler ' + f.pct1(r.mape) + ', ' + f.qty(r.n_fc) + ' Verkäufe'; }), hovertemplate: '%{text}<extra></extra>' },
      { type: 'scatter', mode: 'lines', name: 'Prognosefehler, Modellsicht (kanalbereinigt)', x: xs, y: starMetric.map(function (r) { return r.mape_ch; }),
        line: { color: pal.accent, width: 2, dash: 'dot' },
        text: starMetric.map(function (r) { return monthDe(r.month) + ': Modellsicht ' + f.pct1(r.mape_ch); }), hovertemplate: '%{text}<extra></extra>' },
      { type: 'bar', name: 'Verzerrung, Geschäftssicht (plus: Prognose zu hoch)', x: xs, y: starMetric.map(function (r) { return r.bias; }),
        marker: { color: pal.accent2, opacity: 0.45 },
        text: starMetric.map(function (r) { return monthDe(r.month) + ': Verzerrung ' + sPct(r.bias) + ', kanalbereinigt ' + sPct(r.bias_ch); }), hovertemplate: '%{text}<extra></extra>' }
    ];
    if (L && isNum(L.target) && xs.length) {
      traces.push({ type: 'scatter', mode: 'lines', name: 'Ziel höchstens ' + f.pct(L.target) + ' (Platzhalter)', x: [xs[0], xs[xs.length - 1]], y: [L.target, L.target],
        line: { color: pal.muted, width: 1, dash: 'dash' }, hoverinfo: 'skip' });
    }
    var ticks = [-0.1, 0, 0.1, 0.2, 0.3];
    var layout = {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: pal.font, color: pal.ink, size: 13 },
      xaxis: { title: { text: 'Verkaufsmonat' }, gridcolor: pal.grid, linecolor: pal.line, zeroline: false, type: 'category', tickangle: -45, nticks: 12 },
      yaxis: { title: { text: 'in % des erzielten Preises' }, tickvals: ticks, ticktext: ticks.map(function (t) { return (t < 0 ? '-' : '') + f.pct(Math.abs(t)); }), range: [-0.12, 0.32], gridcolor: pal.grid, linecolor: pal.line, zeroline: true, zerolinecolor: pal.line },
      legend: { orientation: 'h', y: 1.02, yanchor: 'bottom', x: 0, xanchor: 'left', font: { color: pal.ink } },
      hoverlabel: { font: { family: pal.font } },
      bargap: 0.5
    };
    var chartNote = 'Alle Gerätearten zusammen, nur Monate mit mindestens ' + f.qty(minRows) + ' Verkäufen mit Prognose (' + f.qty(starMetric.length) + ' Monate, ' + (D.first_metric_month ? monthDe(D.first_metric_month) : '') + ' bis ' + (D.last_metric_month ? monthDe(D.last_metric_month) : '') + '). '
      + 'Linie: Prognosefehler in der Geschäftssicht, gepunktet die Modellsicht; Balken: Verzerrung mit Vorzeichen; gestrichelt das Ziel. Liegt die gepunktete Linie unter der durchgezogenen, ist der Abstand der Kanalmix, kein Modellfehler.';

    /* ---------- Tabelle 1: je Monat, alle Geraetearten ---------- */
    var mRows = star.filter(function (r) { return r.n_sales > 0 || r.n_fc > 0; }).map(function (r) {
      var ok = isNum(r.mape);
      return E.ROW([
        E.C(monthDe(r.month), { bold: ok, nowrap: true }),
        E.N(f.qty(r.n_sales)), E.N(f.qty(r.n_fc)), E.N(f.qty(r.n_as_is)),
        E.N(ok ? f.pct1(r.mape) : thin, { neg: ok && L && isNum(L.target) && r.mape > L.target }),
        E.N(ok ? sPct(r.bias) : '', { neg: ok && r.bias > 0 }),
        E.N(ok ? f.pct1(r.wape) : ''), E.N(ok ? f.eur(r.mae) : ''),
        E.N(ok ? f.pct1(r.mape_ch) : ''), E.N(ok ? sPct(r.bias_ch) : ''),
        E.N(f.qty(r.n_mkt)), E.N(isNum(r.mape_mkt) ? f.pct1(r.mape_mkt) : (r.n_mkt > 0 ? thin : ''))
      ]);
    });
    var tMonthly = E.TABLE('f-monthly', 'Je Verkaufsmonat, alle Gerätearten', [
      E.H('Monat'), E.H('Verkäufe', 1), E.H('mit Prognose', 1), E.H('ohne Aufbereitung raus', 1), E.H('Fehler', 1), E.H('Verzerrung', 1), E.H('gewichtet', 1),
      E.H('Fehler je Gerät', 1), E.H('Fehler Modellsicht', 1), E.H('Verzerrung Modellsicht', 1), E.H('Marktplatz', 1), E.H('Fehler Marktplatz', 1)
    ], mRows, {
      n: mRows.length, collapsible: mRows.length > 20,
      note: 'Fett die Monate mit Kennzahl (ab ' + f.qty(minRows) + ' Verkäufen mit Prognose). Der laufende Monat ' + (D.as_of ? monthDe(String(D.as_of).slice(0, 7)) : '') + ' ist unvollständig und zählt nicht für die Kennzahl oben.',
      defs: [
        { k: 'Monat', v: 'Verkaufsmonat des Geräts (Datum des Verkaufsauftrags).' },
        { k: 'Verkäufe', v: 'verkaufte Geräte des Monats ohne Verkäufe ohne Aufbereitung (As-Is).' },
        { k: 'mit Prognose', v: 'davon Geräte, für die bei der Rückgabe ein Prognoselauf in Kraft war.' },
        { k: 'ohne Aufbereitung raus', v: 'Verkäufe ohne Aufbereitung (As-Is), ausgeschlossen, weil dort keine Prognose gilt.' },
        { k: 'Fehler', v: 'mittlerer absoluter Fehler in Prozent des erzielten Preises (Geschäftssicht); rot, wenn über dem Ziel.' },
        { k: 'Verzerrung', v: 'mittlere Abweichung mit Vorzeichen; plus heißt Prognose zu hoch.' },
        { k: 'gewichtet', v: 'Summe der absoluten Fehler durch Summe der erzielten Preise (WAPE).' },
        { k: 'Fehler je Gerät', v: 'mittlerer absoluter Fehler in Euro je verkauftem Gerät.' },
        { k: 'Fehler Modellsicht, Verzerrung Modellsicht', v: 'dieselben Kennzahlen mit der Prognose am tatsächlich genutzten Kanal (kanalbereinigt).' },
        { k: 'Marktplatz, Fehler Marktplatz', v: 'nur Verkäufe über den Marktplatz, wo Geschäfts- und Modellsicht zusammenfallen; Kennzahl ab ' + f.qty(minRows) + '.' }
      ],
      tags: [TAG_SIM]
    });

    /* ---------- Tabelle 2: je Geraeteart ---------- */
    var fRows = (D.fam_summary || []).map(function (s) {
      var ok = isNum(s.mape);
      return E.ROW([
        E.C(famName(s.fam), { bold: s.fam === '*' }),
        E.N(f.qty(s.months)), E.N(f.qty(s.n_fc)), E.N(f.qty(s.n_as_is)),
        E.N(ok ? f.pct1(s.mape) : thin, { neg: ok && L && isNum(L.target) && s.mape > L.target }),
        E.N(ok ? sPct(s.bias) : '', { neg: ok && s.bias > 0 }),
        E.N(ok ? f.pct1(s.wape) : ''), E.N(ok ? f.eur(s.mae) : ''),
        E.N(ok ? f.pct1(s.mape_ch) : ''), E.N(ok ? sPct(s.bias_ch) : ''),
        E.C(ok ? monthDe(s.first) + ' bis ' + monthDe(s.last) : '', { nowrap: true })
      ], { bold: s.fam === '*' });
    });
    var tFam = E.TABLE('f-fam', 'Je Geräteart, Mittel über die Monate mit Kennzahl', [
      E.H('Geräteart'), E.H('Monate mit Kennzahl', 1), E.H('mit Prognose', 1), E.H('ohne Aufbereitung raus', 1), E.H('Fehler', 1), E.H('Verzerrung', 1),
      E.H('gewichtet', 1), E.H('Fehler je Gerät', 1), E.H('Fehler Modellsicht', 1), E.H('Verzerrung Modellsicht', 1), E.H('Zeitraum')
    ], fRows, {
      n: fRows.length,
      note: 'Ein Mittel über Monatswerte, jeder Monat zählt gleich; die Zeile „alle Gerätearten“ ist das Mittel der Gesamtreihe, nicht die Summe der Zeilen darunter.',
      defs: [
        { k: 'Geräteart', v: 'die Modellfamilie des Prognosemodells: iPhone, Android-Smartphone, Tablet, Laptop; fett die Reihe über alle.' },
        { k: 'Monate mit Kennzahl', v: 'Monate mit mindestens ' + f.qty(minRows) + ' Verkäufen mit Prognose in dieser Geräteart.' },
        { k: 'mit Prognose', v: 'verkaufte Geräte mit Prognose über alle Monate der Reihe.' },
        { k: 'ohne Aufbereitung raus', v: 'Verkäufe ohne Aufbereitung der Geräteart, ausgeschlossen.' },
        { k: 'Fehler, Verzerrung, gewichtet, Fehler je Gerät', v: 'wie in der Monatstabelle, als Mittel der Monatswerte; rot über dem Ziel.' },
        { k: 'Fehler Modellsicht, Verzerrung Modellsicht', v: 'kanalbereinigt, Mittel der Monatswerte.' },
        { k: 'Zeitraum', v: 'erster und letzter Monat mit Kennzahl.' }
      ],
      tags: [TAG_SIM, TAG_DER]
    });

    /* ---------- Tabelle 3: Rueckblick-Test ---------- */
    var bt = D.backtest || [];
    var bRows = bt.map(function (b) {
      return E.ROW([
        E.C(famName(b.fam), { bold: b.fam === '*' }),
        E.N(f.qty(b.n_train)), E.N(f.qty(b.n_test)),
        E.N(f.pct1(b.mape), { neg: L && isNum(L.target) && isNum(b.mape) && b.mape > L.target }), E.N(sPct(b.bias), { neg: isNum(b.bias) && b.bias > 0 }),
        E.N(f.pct1(b.wape)), E.N(f.eur(b.mae)), E.N(f.num(b.rmse_log, 3)),
        E.C(f.de(b.train_max), { nowrap: true }), E.C(f.de(b.test_min), { nowrap: true })
      ], { bold: b.fam === '*' });
    });
    var cutoff = bt.length ? bt[0].cutoff : '';
    var tBack = E.TABLE('f-backtest', 'Rückblick-Test' + (cutoff ? ', Stichtag ' + f.de(cutoff) : ''), [
      E.H('Geräteart'), E.H('Training', 1), E.H('Test', 1), E.H('Fehler', 1), E.H('Verzerrung', 1), E.H('gewichtet', 1), E.H('Fehler je Gerät', 1),
      E.H('Streuung log', 1), E.H('letzter Trainingsverkauf'), E.H('erster Testverkauf')
    ], bRows, {
      n: bRows.length,
      note: 'Das Modell wird nur mit Verkäufen bis zum Stichtag angepasst und dann an den Verkäufen danach gemessen, mit dem tatsächlichen Kanal, der tatsächlichen Zustandsstufe und dem tatsächlichen Verkaufstag. Deshalb fällt der Fehler hier kleiner aus als in der Monatsreihe, die die Prognose bei Rückgabe misst.',
      defs: [
        { k: 'Geräteart', v: 'Modellfamilie; fett alle zusammen.' },
        { k: 'Training', v: 'Verkäufe bis zum Stichtag, mit denen das Modell angepasst wurde.' },
        { k: 'Test', v: 'Verkäufe nach dem Stichtag, an denen gemessen wurde.' },
        { k: 'Fehler, Verzerrung, gewichtet, Fehler je Gerät', v: 'wie oben, über die Testverkäufe.' },
        { k: 'Streuung log', v: 'Wurzel des mittleren quadratischen Fehlers im Logarithmus des Preisverhältnisses; die Streuung, die das Modell für seine Bandbreite ansetzt.' },
        { k: 'letzter Trainingsverkauf, erster Testverkauf', v: 'Datumsgrenze zwischen Anpassung und Messung.' }
      ],
      empty: 'Kein Rückblick-Test im Lauf.',
      tags: [TAG_SIM]
    });

    /* ---------- Tabelle 4: welche Prognose bei Rueckgabe galt ---------- */
    var reasons = Object.keys(R.reasons || {});
    var reasonDe = { 'no run before return_date': 'kein Prognoselauf vor dem Rückgabedatum', 'device without launch_date or purchase_price': 'Gerät ohne Verkaufsstart oder Einkaufspreis' };
    var grades = R.grades || {};
    var rRows = [
      E.ROW([E.C('Rückgaben mit Rückgabedatum', { bold: true }), E.N(f.qty(R.n_returns)), E.C(f.de(R.first_return) + ' bis ' + f.de(R.last_return))]),
      E.ROW([E.C('davon mit Prognose bei Rückgabe'), E.N(f.qty(R.n_with)), E.C(R.n_returns ? f.pct1(R.n_with / R.n_returns) + ' der Rückgaben, ' + f.qty(R.n_runs_used) + ' Läufe genutzt' : '')])
    ].concat(reasons.map(function (k) {
      return E.ROW([E.C('ohne Prognose: ' + (reasonDe[k] || k)), E.N(f.qty(R.reasons[k]), { neg: true }), E.C('zählt in keiner Kennzahl; die ersten Rückgaben liegen vor dem ersten Lauf vom ' + f.de(D.first_run))]);
    })).concat(Object.keys(grades).map(function (g) {
      return E.ROW([E.C('Zustandsstufe ' + g + ' bei Rückgabe', { indent: 1 }), E.N(f.qty(grades[g])), E.C(R.n_with ? f.pct1(grades[g] / R.n_with) + ' der Prognosen bei Rückgabe' : '')]);
    }));
    var tRecord = E.TABLE('f-record', 'Welche Prognose bei Rückgabe galt', [E.H('Zeile'), E.H('Geräte', 1), E.H('Erläuterung')], rRows, {
      n: R.n_returns,
      defs: [
        { k: 'Zeile', v: 'Rückgaben insgesamt, davon mit und ohne Prognose, darunter die Zustandsstufe, mit der die Prognose gerechnet wurde.' },
        { k: 'Geräte', v: 'Stückzahl Geräte.' },
        { k: 'Erläuterung', v: 'Anteil, Zeitraum oder Grund.' }
      ],
      tags: [TAG_SIM]
    });

    /* ---------- Tabelle 5: die Laeufe ---------- */
    var runs = D.runs || [];
    var famKeys = ['iphone_like', 'android_like', 'tablet_like', 'laptop_like'];
    var fitDe = { family: 'je Geräteart', pooled: 'gemeinsam', none: 'Planwert' };
    var runRows = runs.map(function (r) {
      var by = r.by_fam || {}, fit = r.fit || {};
      var fits = famKeys.map(function (k) { return fit[k] ? (fitDe[fit[k]] || fit[k]) : ''; }).filter(function (x, i, a) { return x && a.indexOf(x) === i; }).join(', ');
      return E.ROW([E.C(r.id, { nowrap: true }), E.C(f.de(r.as_of), { nowrap: true }), E.N(f.qty(r.n_train))].concat(famKeys.map(function (k) { return E.N(f.qty(by[k] || 0)); })).concat([E.C(fits || '')]));
    });
    var tRuns = E.TABLE('f-runs', 'Die Prognoseläufe', [E.H('Lauf'), E.H('Stichtag'), E.H('Training', 1)].concat(famKeys.map(function (k) { return E.H(famName(k), 1); })).concat([E.H('Anpassung')]), runRows, {
      n: runRows.length, collapsible: runRows.length > 12,
      note: 'Ein Lauf je Monatsende seit ' + f.de(D.first_run) + ', zuletzt ' + f.de(D.last_run) + '; Läufe sind unveränderlich, die Prognose bei Rückgabe wird immer aus dem Lauf vor dem Rückgabedatum gelesen. Verfahren ' + (D.method || '') + ': Logarithmus des Preisverhältnisses zur UVP, linear in Modellalter, Zahl der Nachfolger seit Verkaufsstart, Zustandsstufe, Speicher und Kanal, je Geräteart angepasst.',
      defs: [
        { k: 'Lauf, Stichtag', v: 'Kennung und Monatsende, bis zu dem Verkäufe in die Anpassung eingingen.' },
        { k: 'Training', v: 'Verkäufe, mit denen der Lauf angepasst wurde, gesamt.' },
      ].concat(famKeys.map(function (k) { return { k: famName(k), v: 'Verkäufe dieser Geräteart im Training des Laufs.' }; })).concat([
        { k: 'Anpassung', v: 'je Geräteart: eigene Koeffizienten; gemeinsam: eine Anpassung über alle mit Merkmalen je Geräteart, wenn eine Geräteart zu wenige Verkäufe hat; Planwert: der geplante Restwertanteil aus der Konfiguration, wenn Verkäufe fehlen.' }
      ]),
      tags: [TAG_SIM]
    });

    /* ---------- Was man daraus macht ---------- */
    var actions = [];
    if (L) {
      actions.push({ lead: 'Ziel und Sicht auseinanderhalten.',
        text: 'Der Fehler der Geschäftssicht (' + pctOr(L.mape, thin) + ') enthält den Kanalmix; die Modellsicht (' + pctOr(L.mape_ch, thin) + ') misst das Modell. Ein Ziel von ' + (isNum(L.target) ? f.pct(L.target) : '') + ' braucht die Angabe, welche Sicht gemeint ist; heute steht es auf der Geschäftssicht.' });
      actions.push({ lead: 'Die Verzerrung ist der Kanal, nicht das Modell.',
        text: 'Geschäftssicht ' + sPct(L.bias) + ', kanalbereinigt ' + (isNum(L.bias_ch) ? sPct(L.bias_ch) : thin) + '. Der Abstand ist der Abschlag von Mitarbeiterkauf und Großhandel gegen den Marktplatz; das ist die Stellschraube Kanalwahl, nicht die Prognose.' });
    }
    actions.push({ lead: 'Nachkalibrierung nach Regel, nicht nach Gefühl.',
      text: 'Der Hinweis ADV02 vergleicht die kanalbereinigte Verzerrung der letzten drei vollen Monate mit der Schwelle von plus oder minus ' + (isNum(A.threshold) ? f.pct(A.threshold) : '') + '; heute ' + (isNum(A.mean_bias_ch) ? sPct(A.mean_bias_ch) : thin) + ', also ' + (fired ? 'ein Hinweis an ' + (A.owner || '') : 'kein Hinweis') + '. Ein Mensch entscheidet, das Werkzeug erinnert.' });
    actions.push({ lead: 'Die Lücke am Anfang schließen.',
      text: f.qty(R.n_missing) + ' Rückgaben tragen keine Prognose, weil sie vor dem ersten Lauf lagen. Im echten Einsatz beginnt die Messung mit dem ersten Monatslauf; je früher der läuft, desto früher gibt es Güte.' });

    /* ---------- Klappbloecke ---------- */
    var blocks = [
      { key: 'f-method', title: 'Methode, ausgeschrieben', ordered: true, intro: 'So entsteht jede Zahl auf diesem Reiter.', items: [
        { lead: 'Prognose bei Rückgabe.', text: 'Für jedes zurückgegebene Gerät der Lauf strikt vor dem Rückgabedatum (eine Rückgabe am Ersten nimmt das Monatsende davor), Zustandsstufe wie geprüft, Verkaufstag gleich Rückgabe plus erwartete Tage bis Verkauf der Geräteart, Kanal Marktplatz. Der Lauf speichert seine Kanalfaktoren mit, damit dieselbe Prognose später am genutzten Kanal gelesen werden kann.' },
        { lead: 'Fehlerreihe.', text: 'Erzielter Bruttopreis gegen diese Prognose, je Verkaufsmonat und Geräteart plus eine Reihe über alle. As-Is-Verkäufe ausgeschlossen und gezählt. Unter ' + f.qty(minRows) + ' Verkäufen mit Prognose bleiben die Kennzahlen leer, die Stückzahlen stehen trotzdem.' },
        { lead: 'Zwei Sichten.', text: 'Geschäftssicht: jeder Kanal gegen die Marktplatz-Prognose, so sieht es die Geschäftsführung, und die Definition sagt es dazu. Modellsicht: Prognose mal Kanalfaktor des genutzten Kanals; das prüft der Hinweis ADV02, weil ein Kanalabschlag kein Modellfehler ist.' },
        { lead: 'Rückblick-Test.', text: 'Anpassung nur mit Verkäufen bis zum Stichtag, Messung an den Verkäufen danach mit tatsächlichem Kanal, Zustand und Verkaufstag. Deshalb kleiner als die Fehlerreihe, und deshalb steht er daneben, nicht darüber.' },
        { lead: 'Kennzahl des Monats.', text: 'KPI_TOP_RV_FORECAST_ERROR ist der Fehler der Geschäftssicht im letzten vollen Monat vor dem Stichtag, alle Gerätearten; Ziel und Richtung stehen in der Konfiguration mit Verantwortlichem.' }
      ] },
      { key: 'f-limits', title: 'Grenzen, ausgesprochen', ordered: false, intro: '', items: [
        { lead: '', text: 'Simulierte Flotte: die Preise folgen einer Kurve, die an die öffentlichen Preisbelege kalibriert ist. Die Güte hier misst, wie gut das Modell diese Kurve zurückgewinnt, nicht wie gut es einen Markt trifft. Auf echten Daten wird der Fehler anders aussehen, die Rechnung bleibt.' },
        { lead: '', text: 'Der Punktwert ist der Median, nicht der Mittelwert; die Verzerrung zeigt die Lücke zwischen beiden, und niemand korrigiert sie still.' },
        { lead: '', text: 'Zustandsstufe D: wenn jede D-Rückgabe As-Is geht, hat das Modell für D keinen Koeffizienten; ' + (D.current && isNum(D.current.unsupported_grade) ? f.qty(D.current.unsupported_grade) + ' laufende Prognosen von ' + f.qty(D.n_current) + ' nehmen deshalb den As-Is-Anteil statt der Kurve.' : 'solche Prognosen nehmen den As-Is-Anteil statt der Kurve.') },
        { lead: '', text: 'Ziel ' + (L && isNum(L.target) ? f.pct(L.target) : '') + ' und Schwelle ' + (isNum(A.threshold) ? f.pct(A.threshold) : '') + ' sind Platzhalter ohne externe Quelle, je mit Verantwortlichem in der Konfiguration; sie sagen, wann ein Mensch hinsehen soll, nicht, was gut ist.' },
        { lead: '', text: 'Ein Mittel über Monatswerte gewichtet jeden Monat gleich, egal wie viele Verkäufe er hatte; die gewichtete Kennzahl (WAPE) steht daneben.' }
      ] }
    ];

    return {
      kicker: kicker, subject: subject, intro: intro,
      kpis: kpis, kpiDefs: kpiDefs, calcnote: calcnote,
      chart: { traces: traces, layout: layout }, chartNote: chartNote,
      actions: actions,
      tables: [tMonthly, tFam, tBack, tRecord, tRuns],
      blocks: blocks
    };
  };
  w.RE.forecast.version = 3;
})(window);
