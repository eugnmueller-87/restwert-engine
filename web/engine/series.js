/* Restwert Engine v3, Motor Serie gegen Serie (Reiter "series", Bereich Market Intelligence). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6.
   Reine Funktion window.RE.series(D, opts, P): liest dieselben Daten wie der Motor Realisierung (data/market.json, die Huelle
   reicht sie unter dem Schluessel market weiter) und baut die beiden Tabellen, die bis zum 16.09.2026 auf dem Reiter Realisierung
   standen: Serie gegen Serie und die Gegenprobe deutscher Markt. Jede Zahl kommt aus D und geht durch E.fmt. Kein DOM, kein Zustand. */
(function (w) {
  'use strict';
  var E = w.RE, f = E.fmt;

  var TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' };

  function isNum(x) { return typeof x === 'number' && isFinite(x); }

  w.RE.series = function (D, opts, P) {
    if (!D || !Array.isArray(D.anchors) || !Array.isArray(D.curves)) throw new Error('series: D.anchors oder D.curves fehlen');
    if (!isNum(D.series_min) || typeof D.series_grade !== 'string') throw new Error('series: D.series_min oder D.series_grade fehlen (make_market_data.py)');
    var SERIES_MIN = D.series_min, GRADE_READ = D.series_grade;
    var series = Array.isArray(D.series) ? D.series : [];
    var studies = D.studies || {}, asOf = studies.as_of || D.today;
    var fams = []; D.anchors.forEach(function (r) { if (fams.indexOf(r.family) < 0) fams.push(r.family); }); fams.sort();

    /* ---------- Kopf ---------- */
    var kicker = 'Market Intelligence, Stand ' + f.de(D.today);
    var subject = 'Serie gegen Serie: was Gebrauchtgeräte einer Modellreihe heute noch bringen, beim mittleren Alter ihrer Belege';
    var intro = 'Eine Modellreihe gegen die andere, nur Marktplatz-Angebote in Zustandsstufe ' + GRADE_READ + ' (sehr gut), also die Obergrenze. '
      + 'Das ist der Restwert aus Sicht des Marktes: was ein gebrauchtes Gerät dieser Reihe heute auf deutschen Marktplätzen kostet, in Prozent seiner UVP beim Verkaufsstart. '
      + 'Nicht der Restwert des Reiter Kreislauf (der ist der Euro-Betrag, den ein eigenes Gerät beim Verkauf nach dem Leasing erzielt hat), sondern der Maßstab, an dem die Restwertprognose geeicht wird. '
      + 'Die Kurven je Hersteller und alle Preisbelege stehen im Reiter Realisierung.';

    /* ---------- Kennzahlen: eine Kachel je Familie, die Reihe mit dem hoechsten und dem niedrigsten Mittel ---------- */
    var kpis = fams.map(function (fam) {
      var rows = series.filter(function (r) { return r.family === fam && isNum(r.mitte); });
      if (!rows.length) return { label: fam + ': Reihen mit genug Belegen', value: 'keine', lines: ['keine Modellreihe mit mindestens ' + f.qty(SERIES_MIN) + ' Belegen der Stufe ' + GRADE_READ], tags: [TAG_PUB] };
      var hi = rows.reduce(function (a, b) { return b.mitte > a.mitte ? b : a; }), lo = rows.reduce(function (a, b) { return b.mitte < a.mitte ? b : a; });
      var name = function (r) { return r.series === r.oem ? r.series : r.oem + ' ' + r.series; };   /* Fairphone heisst wie sein Hersteller */
      return {
        label: fam + ': Reihen mit genug Belegen', value: f.qty(rows.length),
        lines: [
          'am meisten hält ' + name(hi) + ' mit ' + f.pct(hi.mitte) + ' bei ' + f.qty(hi.monate) + ' Monaten',
          'am wenigsten ' + name(lo) + ' mit ' + f.pct(lo.mitte) + ' bei ' + f.qty(lo.monate) + ' Monaten',
          'das Alter der Belege unterscheidet sich je Reihe; die Spalte Monate sagt, bei welchem Alter der Wert gilt'
        ],
        tags: [TAG_PUB]
      };
    });
    var kpiDefs = [
      { k: 'Reihen mit genug Belegen', v: 'Modellreihen der Familie mit mindestens ' + f.qty(SERIES_MIN) + ' Marktplatz-Angeboten in Zustandsstufe ' + GRADE_READ + '; darunter zeigt die Tabelle die Reihe nicht.' },
      { k: 'am meisten, am wenigsten', v: 'die Reihe mit dem höchsten und dem niedrigsten mittleren Angebot in Prozent der UVP; kein Vergleich bei gleichem Alter, deshalb steht das mittlere Modellalter der Belege dabei.' }
    ];

    /* ---------- Tabelle 1: Serie gegen Serie ---------- */
    var seriesRows = series.map(function (r) {
      return E.ROW([
        E.C(r.family), E.C(r.oem), E.C(r.series), E.N(f.qty(r.qty)), E.N(f.qty(r.monate)), E.N(f.pct(r.mitte)), E.N(f.pct(r.lo) + ' bis ' + f.pct(r.hi))
      ]);
    });
    var tSeries = E.TABLE('m-series', 'Serie gegen Serie', [
      E.H('Familie'), E.H('Hersteller'), E.H('Modellreihe'), E.H('Belege', 1), E.H('Monate', 1), E.H('Realisierung', 1), E.H('Spanne', 1)
    ], seriesRows, {
      tags: [TAG_PUB],
      note: 'Mittleres Angebot je Reihe beim mittleren Modellalter der Belege; die Spanne zeigt das günstigste und das teuerste Angebot der Reihe. Sortiert nach Familie und Alter.',
      empty: 'Keine Modellreihe mit genug Belegen.',
      defs: [
        { k: 'Familie', v: 'Geräteart der Reihe (' + fams.join(', ') + ').' },
        { k: 'Hersteller', v: 'Hersteller der Reihe.' },
        { k: 'Modellreihe', v: 'Modellreihe, wie der Hersteller sie nennt; mehrere Modelle und Ausstattungen je Reihe.' },
        { k: 'Belege', v: 'Marktplatz-Angebote in Zustandsstufe ' + GRADE_READ + ' dieser Reihe; unter ' + f.qty(SERIES_MIN) + ' wird die Reihe nicht gezeigt. Wenige Belege heißen: der Wert hängt an wenigen Angeboten.' },
        { k: 'Monate', v: 'mittleres Modellalter der Belege in Monaten seit deutschem Verkaufsstart' },
        { k: 'Realisierung', v: 'mittleres Marktplatz-Angebot in Prozent der UVP (die Hälfte der Belege liegt darüber, die Hälfte darunter)' },
        { k: 'Spanne', v: 'günstigstes bis teuerstes Marktplatz-Angebot der Reihe, in Prozent der UVP; ein Wert über ' + f.pct(1) + ' ist ein echter Beleg, bei dem ein Aufbereiter mehr verlangt als die UVP (kommt bei knapper oder sehr junger Ware vor), er bleibt drin, weil die Tabelle Belege zeigt, keine Meinung' }
      ]
    });

    /* ---------- Tabelle 2: Gegenprobe deutscher Markt (Stichproben) ---------- */
    var checkRows = (studies.gegenprobe || []).map(function (x) {
      return E.ROW([
        E.C(x.geraet), E.N(isNum(x.uvp) ? f.eur(x.uvp) : 'keine deutsche UVP'), E.N(f.qty(x.alter)), E.C(x.marktplatz), E.C(x.ankauf)
      ]);
    });
    var tCheck = E.TABLE('m-check', 'Gegenprobe deutscher Markt, ' + f.de(asOf) + ', Zustandsstufe ' + GRADE_READ + ' (sehr gut)', [
      E.H('Gerät'), E.H('UVP', 1), E.H('Monate', 1), E.H('Marktplatz-Angebot'), E.H('Ankauf-Gebot')
    ], checkRows, {
      n: 0, tags: [TAG_PUB],
      note: 'Stichproben einzelner Geräte an einem Tag: Marktplatz-Angebot gegen Ankauf-Gebot, beide in Prozent der UVP. Zeigt den Abstand zwischen dem, was ein Aufbereiter verlangt, und dem, was ein Ankäufer höchstens nennt.',
      empty: 'Keine Stichprobe hinterlegt.',
      foot: '*„bis zu“ ist der Höchstwert vor der Zustandsabfrage, kein Gebot. Quellen je Zeile: refurbed, rebuy, AfB, Clevertronic, ZOXS, alle abgerufen am ' + f.de(asOf) + '; die vollständige Liste mit Adressen liegt im Projektordner.',
      defs: [
        { k: 'Gerät', v: 'Modell und Ausstattung der Stichprobe.' },
        { k: 'UVP', v: 'unverbindliche Preisempfehlung des Herstellers beim deutschen Verkaufsstart, einschließlich Mehrwertsteuer; ohne deutsche UVP bleibt die Realisierung offen.' },
        { k: 'Monate', v: 'Modellalter am Abrufdatum, in Monaten seit deutschem Verkaufsstart.' },
        { k: 'Marktplatz-Angebot', v: 'günstigstes bis teuerstes Marktplatz-Angebot in Zustandsstufe ' + GRADE_READ + ' (sehr gut) am Abrufdatum, in Euro und in Klammern in Prozent der UVP.' },
        { k: 'Ankauf-Gebot', v: 'Ankauf-Gebot am Abrufdatum, in Euro und in Klammern in Prozent der UVP; „bis zu“ mit Stern ist der Höchstwert vor der Zustandsabfrage, kein Gebot.' }
      ]
    });

    return {
      kicker: kicker, subject: subject, intro: intro,
      kpis: kpis, kpiDefs: kpiDefs,
      /* Die Gegenprobe (studies.gegenprobe) traegt keine Adresse je Zeile; bis sie eine hat, steht sie nicht auf der Seite (Regel: jede externe Zahl hat eine Adresse) */
      tables: [tSeries]
    };
  };
  w.RE.series.version = 3;
})(window);
