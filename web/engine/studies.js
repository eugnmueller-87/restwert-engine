/* Restwert Engine v3, Motor Studien (Reiter "studies", Bereich Market Intelligence). Vertrag: v3/CONTRACT.md, Abschnitte 1 bis 6.
   Reine Funktion window.RE.studies(D, opts, P): liest data/market.json (die Huelle reicht es unter dem Schluessel market weiter)
   und zeigt, was veroeffentlichte Quellen zum Wertverlust sagen: die deutschen Quellen der Recherche vom 16.09.2026 (D.studies.deutschland,
   mit Art der Zahl und Interessenlage), die internationalen Studien (D.studies.smartphone, D.studies.laptop) und, als Klappbloecke,
   was gesucht und nicht gefunden wurde. Jede Zahl in den Tabellen steht so in der Quelle; jede Zeile traegt die Adresse.
   Kein DOM, kein Zustand. */
(function (w) {
  'use strict';
  var E = w.RE, f = E.fmt;

  var TAG_PUB = { cls: 'tag-neutral', text: 'öffentlich' };
  var TAG_DE = { cls: 'tag-accent', text: 'Deutschland' };
  var TAG_INT = { cls: 'tag-neutral', text: 'international' };
  var ART_PCT = 'Prozent der UVP';          /* Praefix der Art, die eine Realisierung gegen UVP nennt */
  var ART_NEW = 'Neupreis-Erosion';         /* Praefix der Art, die keinen Gebrauchtwert misst */
  var ART_UNBELEGT = 'Prozentspanne ohne Beleg';

  function isStr(x) { return typeof x === 'string' && x.length > 0; }
  function startsWith(s, p) { return String(s || '').indexOf(p) === 0; }

  w.RE.studies = function (D, opts, P) {
    if (!D || !D.studies) throw new Error('studies: D.studies fehlt');
    var S = D.studies;
    var de = Array.isArray(S.deutschland) ? S.deutschland : [];
    var intl = (S.smartphone || []).map(function (x) { return { fam: 'Smartphone', x: x }; }).concat((S.laptop || []).map(function (x) { return { fam: 'Laptop, Tablet', x: x }; }));
    var notFound = Array.isArray(S.deutschland_nicht_gefunden) ? S.deutschland_nicht_gefunden : [];
    var blocked = Array.isArray(S.deutschland_geblockt) ? S.deutschland_geblockt : [];
    var asOfDe = S.deutschland_abrufdatum || D.today, asOfIntl = S.as_of || D.today;

    /* ---------- Kopf ---------- */
    var kicker = 'Market Intelligence, Stand ' + f.de(D.today);
    var subject = 'Was veröffentlichte Quellen zum Wertverlust sagen';
    var intro = (isStr(S.deutschland_kernbefund) ? S.deutschland_kernbefund + ' ' : '')
      + 'Die Tabellen unten sind das Ergebnis zweier Recherchen: deutsche Quellen am ' + f.de(asOfDe) + ', internationale Studien am ' + f.de(asOfIntl) + '. '
      + 'Sie ordnen die eigenen Preisbelege ein (Reiter Realisierung); sie ersetzen sie nicht, weil keine dieser Quellen den Wert eines Geräts nach Hersteller und Modellalter für Deutschland misst.';

    /* ---------- Kennzahlen ---------- */
    var nPct = de.filter(function (r) { return startsWith(r.art, ART_PCT); }).length;
    var nNew = de.filter(function (r) { return startsWith(r.art, ART_NEW); }).length;
    var nUnbelegt = de.filter(function (r) { return startsWith(r.art, ART_UNBELEGT); }).length;
    var kpis = [
      { label: 'Deutsche Quellen gelesen', value: f.qty(de.length), tags: [TAG_DE], lines: [
        'davon mit einer Realisierung gegen UVP an einem Stichtag: ' + f.qty(nPct),
        'davon Neupreis-Erosion, also kein Gebrauchtwert: ' + f.qty(nNew),
        'davon Prozentspannen ohne Datengrundlage: ' + f.qty(nUnbelegt),
        'deutsche Restwertkurve nach Hersteller und Monat: keine'
      ] },
      { label: 'Internationale Studien', value: f.qty(intl.length), tags: [TAG_INT], lines: [
        'USA und Großbritannien: Ankauf-Gebote, also die Ankaufsseite, niedriger als Marktplatz-Angebote',
        'Herausgeber mit Interessenlage stehen in der Spalte Markt, Methode'
      ] },
      { label: 'Gesucht, nicht gefunden', value: f.qty(notFound.length), tags: [TAG_DE], lines: [
        'Portale, Verbände, Leasinggesellschaften, Berater und Fachpresse ohne veröffentlichte Restwertkurve',
        'die Liste steht unten im Block „Gesucht und nicht gefunden“'
      ] }
    ];
    var kpiDefs = [
      { k: 'Deutsche Quellen', v: 'Seiten mit deutschen Zahlen zum Wert gebrauchter Geräte; die Spalte Art sagt, was die Zahl misst: Euro-Punktwerte ohne UVP, Prozent der UVP an einem Stichtag, Prozentspannen ohne Beleg, Neupreis-Erosion (Preis der Neuware sinkt, kein Gebrauchtwert), Ankauf-Höchstwerte „bis zu“, europäische Indizes ohne Deutschland-Ausweis.' },
      { k: 'Internationale Studien', v: 'Berichte, die den Wertverlust nach Hersteller messen, mit Markt, Methode und Interessenlage; ihre Zahlen passen nicht eins zu eins auf ein deutsches Leasinghaus.' },
      { k: 'Gesucht, nicht gefunden', v: 'Quellen, bei denen eine Restwertkurve zu erwarten war und keine veröffentlicht ist.' }
    ];

    /* ---------- Tabelle 1: deutsche Quellen ---------- */
    var deRows = de.map(function (r) {
      return E.ROW([
        E.C(r.geraeteart, { nowrap: true }),
        E.C(r.art, { minW: 150 }),
        E.C('', { links: [{ href: r.url, text: r.quelle + ',', rest: r.datum }], minW: 220 }),
        E.C(r.markt + '; ' + r.methode + '; Interessenlage: ' + r.interessenlage, { minW: 240 }),
        E.C(r.zahlen, { minW: 280 })
      ]);
    });
    var tDe = E.TABLE('s-de', 'Deutsche Quellen, abgerufen ' + f.de(asOfDe), [
      E.H('Geräteart'), E.H('Art der Zahl'), E.H('Quelle, Datum'), E.H('Markt, Methode, Interessenlage'), E.H('Zahlen')
    ], deRows, {
      tags: [TAG_DE, TAG_PUB], collapsible: false,
      note: 'Alles, was die Recherche in deutschen Quellen gefunden hat, mit der Art der Zahl vorneweg, damit niemand einen Euro-Punktwert oder eine Neupreis-Erosion mit einer Restwertkurve verwechselt. „undatiert“ heißt: die Seite nennt kein Datum, gelesen am ' + f.de(asOfDe) + '.',
      empty: 'Keine deutsche Quelle hinterlegt.',
      defs: [
        { k: 'Geräteart', v: 'Geräteart, für die die Quelle Zahlen nennt.' },
        { k: 'Art der Zahl', v: 'was die Zahl misst; nur „Prozent der UVP“ ist mit der Realisierung dieser Seite vergleichbar, und auch das nur an einem Stichtag.' },
        { k: 'Quelle, Datum', v: 'Herausgeber mit Link auf die Seite, dahinter das Datum, wie die Quelle es nennt.' },
        { k: 'Markt, Methode, Interessenlage', v: 'Land, Preisart und Bezugsgröße, dazu wer die Zahl veröffentlicht und warum.' },
        { k: 'Zahlen', v: 'die dort genannten Werte, wie die Quelle sie nennt; Euro ohne Prozent, wo die Quelle keine UVP nennt.' }
      ]
    });

    /* ---------- Tabelle 2: internationale Studien ---------- */
    var noNum = ['Dell', 'HP'].filter(function (o) { return !intl.some(function (e) { return String(e.x.zahlen).indexOf(o) >= 0; }); });
    var intlRows = intl.map(function (e) {
      return E.ROW([
        E.C(e.fam, { nowrap: true }),
        E.C('', { links: [{ href: e.x.url, text: e.x.quelle + ',', rest: e.x.datum }], minW: 170 }),
        E.C(e.x.markt),
        E.C(e.x.zahlen, { minW: 260 })
      ]);
    });
    var tIntl = E.TABLE('s-intl', 'Internationale Studien, abgerufen ' + f.de(asOfIntl), [
      E.H('Geräteart'), E.H('Quelle, Datum'), E.H('Markt, Methode'), E.H('Zahlen')
    ], intlRows, {
      tags: [TAG_INT, TAG_PUB],
      note: 'Berichte, die den Wertverlust nach Hersteller messen, mit Markt, Methode und Interessenlage. Ankaufsangebote (USA, Großbritannien) sind die Ankaufsseite, also niedriger als Marktplatz-Angebote.' + (noNum.length ? ' Für ' + noNum.join(' und ') + ' nennt keine dieser Studien eine Zahl.' : ''),
      empty: 'Keine Studie hinterlegt.',
      defs: [
        { k: 'Geräteart', v: 'Geräteart, für die die Studie Zahlen nennt.' },
        { k: 'Quelle, Datum', v: 'Herausgeber der Studie mit Link auf die Seite, dahinter das Datum der Veröffentlichung oder des Abrufs, wie in der Quelle genannt.' },
        { k: 'Markt, Methode', v: 'Land, Preisart und Bezugsgröße der Studie, dazu die Interessenlage des Herausgebers.' },
        { k: 'Zahlen', v: 'die dort genannten Werte, wie die Quelle sie nennt.' }
      ]
    });

    /* ---------- Klappbloecke ---------- */
    var blocks = [
      { key: 's-notfound', title: 'Gesucht und nicht gefunden', ordered: false, intro: 'Quellen, bei denen eine deutsche Restwertkurve zu erwarten war. Was dort steht und was fehlt:',
        items: notFound.map(function (t) { return { lead: '', text: t }; }) },
      { key: 's-blocked', title: 'Geblockt oder nicht lesbar', ordered: false, intro: 'Seiten, die die Recherche nicht lesen konnte (Fehlercode oder Bezahlschranke); dort kann eine Zahl stehen, die hier fehlt:',
        items: blocked.map(function (t) { return { lead: '', text: t }; }) },
      { key: 's-relais', title: 'Was die deutsche Fachpresse als Studie führt', ordered: false, intro: '',
        items: [{ lead: '', text: isStr(S.deutschland_us_uk_relais) ? S.deutschland_us_uk_relais : 'keine Angabe' }] },
      { key: 's-limits', title: 'Warum keine dieser Zahlen die eigenen Kurven ersetzt', ordered: false, intro: '',
        items: [
          { lead: 'Ankaufsseite gegen Marktplatz:', text: 'Ankauf-Gebote sind Höchstwerte „bis zu“ vor der Zustandsprüfung; Marktplatz-Angebote enthalten die Marge des Aufbereiters. Beides liegt an einem anderen Ende als der Erlös eines Leasinghauses.' },
          { lead: 'Neupreis-Erosion:', text: 'misst, wie der Preis der Neuware nach dem Verkaufsstart fällt; das ist kein Gebrauchtwert und keine Realisierung.' },
          { lead: 'Punktwerte:', text: 'Euro-Beträge ohne UVP und ohne Modellalter lassen sich nicht in eine Kurve legen.' },
          { lead: 'Interessenlage:', text: 'Marktplätze wollen Verkaufsanreize setzen, Ankaufportale werben mit dem Wertverlust, Hersteller lassen Studien beauftragen. Die Spalte sagt es je Zeile.' },
          { lead: 'Erstellt mit KI-Unterstützung:', text: 'Recherche durch nur-lesende Web-Agenten; jede Zeile trägt ihre Adresse. Geprüft wurde am 16.09.2026, dass jede Adresse der beiden Tabellen erreichbar ist; ein zweiter Agent hat alle deutschen Zeilen gegen die Seiten nachgelesen, acht Abweichungen (Datum, Rundung, Methode) wurden eingearbeitet.' }
        ] }
    ];

    return {
      kicker: kicker, subject: subject, intro: intro,
      kpis: kpis, kpiDefs: kpiDefs,
      tables: [tDe, tIntl],
      blocks: blocks
    };
  };
  w.RE.studies.version = 3;
})(window);
