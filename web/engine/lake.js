/* Restwert Engine v3, Motor Daten (lake). Port von v3/src/lake.js und v3/src/lake.body.html nach v3/CONTRACT.md 7.8.
   Reine Funktion: window.RE.lake(D, opts, P) baut das Ansichtsmodell aus data/lake.json. Kein DOM, kein Zustand,
   keine getippte Zahl: jede Zahl kommt aus D oder opts und geht durch E.fmt. Kein Diagramm, P bleibt ungenutzt. */
(function (w) {
  'use strict';
  var E = w.RE;

  var CADENCE = { quarterly: 'je Quartal', monthly: 'je Monat', yearly: 'je Jahr' };
  var ZAHLWORT = ['null', 'ein', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwölf'];
  var QUOTE_OPEN = '„';

  /* Zahlwort fuer kleine Stueckzahlen im Fliesstext ("zehn Stationen"); groessere Zahlen als Ziffern */
  function zahlwort(n) {
    n = Math.round(Number(n));
    return (n >= 0 && n < ZAHLWORT.length) ? ZAHLWORT[n] : E.fmt.qty(n);
  }

  w.RE.lake = function (D, opts, P) {
    opts = opts || {};
    var f = E.fmt, T = D.tot || {};
    var feeds = Array.isArray(D.feeds) ? D.feeds : [];
    var unresolved = Array.isArray(D.unresolved) ? D.unresolved : [];
    var steps = Array.isArray(D.steps) ? D.steps : [];
    var status = Array.isArray(D.status) ? D.status : [];
    var missing = D.missing || {};
    var release = typeof opts.release === 'function' ? opts.release : function () {};
    var remove = typeof opts.remove === 'function' ? opts.remove : function () {};
    var deliveries = Array.isArray(opts.deliveries) ? opts.deliveries : [];
    var ungeklaert = QUOTE_OPEN + 'ungeklärt“';

    /* ---------- Kopf ---------- */
    var kicker = 'Simulation, Stand ' + f.de(D.today) + ' (Stichtag der Daten ' + f.de(D.as_of) + ')';
    var subject = 'Daten: welche Kanäle angeschlossen sind, was reinkommt, was das Werkzeug abweist';
    var intro = 'Das Werkzeug liest keine Quellsysteme direkt. Jeder Kanal liefert Dateien in einen festen Ordner; jede Datei wird einmal geprüft, gezählt und übernommen. '
      + 'Was sich keiner Bestellung oder Seriennummer zuordnen lässt, bleibt als ' + ungeklaert + ' liegen und wird nie geraten. '
      + 'Alles unten kommt aus der Simulation; die Regeln gelten für echte Daten genauso.';

    /* ---------- Kacheln ---------- */
    var unresLines = unresolved
      .filter(function (u) { return String(u.reason).indexOf('Doppelte') < 0; })
      .map(function (u) { return f.qty(u.n) + ' mal ' + u.reason; })
      .concat(['nichts davon wird geraten']);
    var kpis = [
      { label: 'Datenkanäle angeschlossen', value: f.qty(T.feeds), tags: [{ cls: 'tag-neutral', text: 'simuliert' }], lines: [
        'aus ' + f.qty(T.systems) + ' Quellsystemen, plus ' + f.qty(T.public) + ' öffentliche Kanäle (Katalog, Marktkurven)',
        f.qty(T.needed) + ' davon für den Kreislauf nötig',
        f.qty(T.files) + ' Dateien seit ' + f.de(D.history_start)
      ] },
      { label: 'Zeilen gelesen', value: f.qty(T.read), tags: [{ cls: 'tag-neutral', text: 'simuliert' }], lines: [
        f.qty(T.new) + ' übernommen',
        f.qty(T.dup_same) + ' identische Doppelte übersprungen',
        f.qty(T.dup_conflict) + ' Doppelte mit anderem Inhalt gemeldet'
      ] },
      { label: 'Ungeklärte Zeilen', value: f.qty(T.unresolved), lines: unresLines },
      { label: 'Datumskette vollständig', value: D.serials ? f.pct1(D.chain_ok / D.serials) : '', tags: [{ cls: 'tag-neutral', text: 'simuliert' }], lines: [
        f.qty(D.chain_ok) + ' von ' + f.qty(D.serials) + ' Seriennummern',
        'jede erwartete Station von Bestellung bis Zahlungseingang hat ein Datum'
      ] }
    ];
    var kpiDefs = [
    ];

    /* ---------- Die Datenkanaele ---------- */
    var feedRows = feeds.map(function (x) {
      return E.ROW([
        E.C(x.name, { bold: true, tag: x.public ? 'tag-neutral' : '', tagText: x.public ? 'öffentlich' : '' }),
        E.C(x.system),
        E.C(x.content),
        E.C(x.ohne, { sub: x.needed ? 'nötig für den Kreislauf' : 'ergänzend, Kreislauf rechnet auch ohne' }),
        E.N(f.qty(x.files)),
        E.C(f.de(x.last), { nowrap: true }),
        E.N(f.qty(x.read)),
        E.N(f.qty(x.new)),
        E.N(f.qty(Number(x.dup_same) + Number(x.dup_conflict))),
        E.N(f.qty(x.unresolved), { neg: Number(x.unresolved) > 0 })
      ]);
    });
    feedRows.push(E.ROW([
      E.C('Summe'), E.C(''), E.C(''), E.C(''),
      E.N(f.qty(T.files)),
      E.C(''),
      E.N(f.qty(T.read)),
      E.N(f.qty(T.new)),
      E.N(f.qty(Number(T.dup_same) + Number(T.dup_conflict))),
      E.N(f.qty(T.unresolved))
    ], { bold: true }));
    var tFeeds = E.TABLE('l-feeds', 'Die Datenkanäle', [
      E.H('Kanal'), E.H('Liefert'), E.H('Inhalt'), E.H('Ohne diesen Kanal fehlt'), E.H('Dateien', 1), E.H('Letzte Lieferung'),
      E.H('Zeilen gelesen', 1), E.H('übernommen', 1), E.H('doppelt', 1), E.H('ungeklärt', 1)
    ], feedRows, {
      n: feeds.length,
      defs: [
        { k: 'Kanal', v: 'eine Dateiart aus einem Quellsystem; öffentlich heißt: kommt aus dem Katalog oder den Preisbelegen, nicht aus einem Unternehmen' },
        { k: 'Liefert', v: 'das Quellsystem, das die Dateien dieses Kanals schreibt' },
        { k: 'Inhalt', v: 'was in einer Zeile der Datei steht' },
        { k: 'Ohne diesen Kanal fehlt', v: 'was das Werkzeug ohne diesen Kanal nicht mehr rechnen kann. Steht dort eine Station des Kreislaufs (Einkaufspreis, Versand, Mieterlös, Restwert, Zahlungseingang), ist der Kanal nötig: ohne ihn gibt es keine Lifecycle-Marge je Gerät. Steht dort ' + QUOTE_OPEN + 'nur ...", ist der Kanal ergänzend: es fehlt eine Stellschraube oder ein Vergleich, der Kreislauf rechnet trotzdem' },
        { k: 'Dateien', v: 'Dateien, die dieser Kanal seit ' + f.de(D.history_start) + ' geliefert hat; die Summenzeile zählt alle Kanäle zusammen' },
        { k: 'Letzte Lieferung', v: 'Datum der jüngsten Datei dieses Kanals' },
        { k: 'Zeilen gelesen, übernommen', v: 'gelesen minus doppelt minus ungeklärt; doppelt sind Zeilen, die schon da waren (identisch: übersprungen; mit anderem Inhalt: gemeldet, die erste Lieferung gilt)' },
        { k: 'doppelt', v: 'identische Doppelte plus Doppelte mit anderem Inhalt; beide Zahlen stehen in den Kacheln oben' },
        { k: 'ungeklärt', v: 'Zeilen, die sich keinem Schlüssel zuordnen lassen (Bestellposition, Seriennummer); sie liegen in einer eigenen Tabelle und gehen an den Lieferanten der Datei zurück' }
      ]
    });

    /* ---------- Eingelesene Dateien dieser Installation (Browser, opts.deliveries) ---------- */
    var delRows = deliveries.map(function (x) {
      var free = x.status === 'freigegeben';
      var actions = free
        ? [{ label: 'Entfernen', onClick: function () { remove(x.id); } }]
        : [{ label: 'Freigeben', onClick: function () { release(x.id); } }, { label: 'Entfernen', onClick: function () { remove(x.id); } }];
      return E.ROW([
        E.C(x.feed, { bold: true }),
        E.C(x.fileName),
        E.N(f.qty(x.rows)),
        E.N(f.qty((x.cols || []).length)),
        E.C(x.at, { nowrap: true }),
        E.C('', { tag: free ? 'tag-accent' : 'tag-accent-2', tagText: x.status }),
        E.C('', { actions: actions })
      ]);
    });
    var tDeliveries = E.TABLE('l-deliveries', 'Eingelesene Dateien dieser Installation', [
      E.H('Kanal'), E.H('Datei'), E.H('Zeilen', 1), E.H('Spalten', 1), E.H('Gelesen am'), E.H('Status'), E.H('')
    ], delRows, {
      note: 'Prototyp: Läufe werden protokolliert, nicht gerechnet.',
      empty: 'Noch keine Datei eingelesen.',
      defs: [
        { k: 'Kanal', v: 'der Kanal, dem die Datei beim Einlesen zugeordnet wurde' },
        { k: 'Datei', v: 'Name der eingelesenen Datei' },
        { k: 'Zeilen', v: 'Datenzeilen der Datei, ohne Kopfzeile' },
        { k: 'Spalten', v: 'Spalten der Kopfzeile' },
        { k: 'Gelesen am', v: 'Zeitpunkt des Einlesens in diesem Browser' },
        { k: 'Status', v: 'Probelauf heißt: gelesen und gezählt, nichts geschrieben; freigegeben heißt: für den nächsten Lauf vorgemerkt' },
        { k: 'Freigeben, Entfernen', v: 'Freigeben merkt die Datei für den nächsten Lauf vor; Entfernen nimmt sie aus dieser Installation' }
      ],
      tags: [{ cls: 'tag-accent-2', text: 'Prototyp' }]
    });

    /* ---------- Ungeklaerte Zeilen, nach Grund ---------- */
    var unresRows = unresolved.map(function (u) {
      return E.ROW([E.C(u.feed), E.C(u.reason), E.N(f.qty(u.n))]);
    });
    var tUnres = E.TABLE('l-unres', 'Ungeklärte Zeilen, nach Grund', [E.H('Kanal'), E.H('Grund'), E.H('Zeilen', 1)], unresRows, {
      defs: [
        { k: 'Kanal', v: 'der Kanal, dessen Datei die Zeilen geliefert hat' },
        { k: 'Grund', v: 'warum die Zeile liegen bleibt' },
        { k: 'Zeilen', v: 'Stückzahl der Zeilen mit diesem Grund, über alle Dateien des Kanals' }
      ],
      foot: 'Nichts davon wird geraten oder still ergänzt. Eine Zeile bleibt ungeklärt, bis die Quelle sie berichtigt liefert; erst dann rechnet sie mit. Doppelte mit anderem Inhalt stehen hier, weil ein Mensch entscheiden muss, welche Fassung stimmt.'
    });

    /* ---------- Die Datumskette je Seriennummer ---------- */
    var stepRows = steps.map(function (s) {
      var share = s.expected ? s.present / s.expected : null;
      var width = share === null ? 0 : Math.round(100 * share);
      var m = (missing[s.step] || []).length;
      return E.ROW([
        E.C(s.step),
        E.C(s.channel + ': ' + s.field),
        E.N(f.qty(s.expected)),
        E.N(f.qty(s.present)),
        E.C(share === null ? '' : f.pct1(share), { bar: width, nowrap: true }),
        E.N(m ? f.qty(m) : '', { neg: m > 0 })
      ]);
    });
    var tSteps = E.TABLE('l-steps', 'Die Datumskette je Seriennummer', [
      E.H('Station'), E.H('Das Datum kommt aus'), E.H('erwartet bei Geräten', 1), E.H('mit Datum', 1), E.H('Anteil'), E.H('ohne Datum', 1)
    ], stepRows, {
      note: 'Jede Seriennummer hat ' + zahlwort(steps.length) + ' Stationen. Für jede Station, die ein Gerät in seinem Zustand schon erreicht haben muss, prüft das Werkzeug, ob ein Datum da ist. Vollständig heißt: jede erwartete Station hat ein Datum.',
      defs: [
        { k: 'Station', v: 'ein Schritt des Kreislaufs, von der Bestellung bis zum Zahlungseingang, in seiner Reihenfolge' },
        { k: 'Das Datum kommt aus', v: 'der Datenkanal und die Zeile, aus der das Werkzeug das Datum liest; nichts wird von Hand eingetragen, jedes Datum zeigt auf die Zeile in der Rohdatei (Kanal und Belegnummer stehen je Seriennummer im Hauptbuch)' },
        { k: 'erwartet bei Geräten', v: 'Stückzahl der Geräte, die diese Station in ihrem Zustand schon erreicht haben müssen' },
        { k: 'mit Datum', v: 'Stückzahl der erwarteten Geräte, bei denen ein Datum vorliegt' },
        { k: 'Anteil', v: 'mit Datum geteilt durch erwartet, in Prozent' },
        { k: 'ohne Datum', v: 'Geräte, bei denen die Station erwartet wird und kein Datum vorliegt; die Liste je Station steht darunter, mit dem Grund, soweit das Werkzeug ihn kennt' }
      ]
    });

    /* ---------- Geraete ohne Datum, je Station (eingeklappt) ---------- */
    var missDefs = [
      { k: 'Seriennummer', v: 'das Gerät, dem an dieser Station das Datum fehlt' },
      { k: 'Modell', v: 'Modell laut Katalog' },
      { k: 'Zustand', v: 'Zustand des Geräts im Hauptbuch am Stichtag' },
      { k: 'letzte Station mit Datum', v: 'die jüngste Station, für die ein Datum vorliegt, mit diesem Datum' },
      { k: 'Tage seitdem', v: 'Tage zwischen diesem Datum und dem ' + f.de(D.today) },
      { k: 'Grund, soweit bekannt', v: 'was das Werkzeug über die fehlende Zeile weiß; der Rest steht in der Quelle' }
    ];
    var missTables = [];
    steps.forEach(function (s, i) {
      var rows = missing[s.step] || [];
      if (!rows.length) return;
      var body = rows.map(function (r) {
        return E.ROW([
          E.C(r.serial, { nowrap: true }),
          E.C(r.model),
          E.C(r.status),
          E.C(r.prev + (r.prev_date ? ' am ' + f.de(r.prev_date) : '')),
          E.N(r.days === null || r.days === undefined ? '' : f.qty(r.days)),
          E.C(r.hint)
        ]);
      });
      missTables.push(E.TABLE('l-miss-' + i, s.step + ': ' + f.qty(rows.length) + ' Geräte ohne Datum, Kanal ' + s.channel, [
        E.H('Seriennummer'), E.H('Modell'), E.H('Zustand'), E.H('letzte Station mit Datum'), E.H('Tage seitdem', 1), E.H('Grund, soweit bekannt')
      ], body, { n: 0, collapsible: true, defs: missDefs }));
    });

    /* ---------- Kette vollstaendig, nach Zustand ---------- */
    var statusRows = status.map(function (s) {
      return E.ROW([E.C(s.status), E.N(f.qty(s.n)), E.N(f.qty(s.complete)), E.N(s.n ? f.pct1(s.complete / s.n) : '')]);
    });
    var tStatus = E.TABLE('l-status', 'Datumskette nach Zustand des Geräts', [
      E.H('Zustand des Geräts'), E.H('Geräte', 1), E.H('Kette vollständig', 1), E.H('Anteil', 1)
    ], statusRows, {
      defs: [
        { k: 'Zustand des Geräts', v: 'Zustand im Hauptbuch am Stichtag' },
        { k: 'Geräte', v: 'Stückzahl der Seriennummern in diesem Zustand' },
        { k: 'Kette vollständig', v: 'davon mit einem Datum an jeder erwarteten Station' },
        { k: 'Anteil', v: 'Kette vollständig geteilt durch Geräte, in Prozent' }
      ]
    });

    /* ---------- Klappbloecke ---------- */
    var cadence = CADENCE[D.cadence] || String(D.cadence || '');
    var blocks = [
      { key: 'l-capture', title: 'So werden die Daten erfasst', ordered: true,
        intro: 'Keine Station wird im Werkzeug eingetippt. Jede Station ist eine Zeile, die ein Quellsystem in seine Datei schreibt (Bestellung im ERP, Versand im Lagersystem, Löschzertifikat auf dem Rückläufer-Beleg, Gutschrift in der Kanalabrechnung). Das Werkzeug liest die Datei, hängt die Zeile an die Seriennummer und merkt sich Kanal und Belegnummer. Fehlt ein Datum, fehlt die Zeile in der Quelle, und genau dort muss sie nachgeliefert werden.',
        items: [
          { lead: '', text: 'Jeder Kanal legt Dateien in seinen Ordner, je Lieferperiode eine (in der Simulation ' + cadence + ', benannt nach Datum und laufender Nummer).' },
          { lead: '', text: 'Prüfsumme je Datei: dieselbe Datei ein zweites Mal ist wirkungslos, kein Doppelzählen.' },
          { lead: '', text: 'Probelauf ohne Spuren möglich: das Werkzeug meldet, was passieren würde, und schreibt nichts.' },
          { lead: '', text: 'Jede Zeile wird auf Format geprüft (Pflichtfelder, Zahlen, erlaubte Werte, keine negativen Beträge) und jeder Schlüssel gegen die Elterntabelle aufgelöst (Bestellung, Bestellposition, Seriennummer, Vertrag).' },
          { lead: '', text: 'Was passt, wird übernommen; was doppelt ist, wird gezählt; was nicht passt, landet in ' + ungeklaert + ' mit Grund und Zeilennummer.' },
          { lead: '', text: 'Vier Schichten: Rohdateien (unverändert), geprüfte Zeilen, das Geräte-Hauptbuch je Seriennummer, Kennzahlen und Stellschrauben. Jede Zahl auf den anderen Reiter lässt sich bis zur Zeile in der Rohdatei zurückverfolgen.' }
        ] },
      { key: 'l-real', title: 'Für den echten Einsatz', ordered: false, tag: { cls: 'tag-accent-2', text: 'Anforderung, noch nicht gebaut' },
        items: [
          { lead: 'Nur die vereinbarten Kanäle.', text: 'Je Installation eine Freigabeliste; eine Datei aus einem nicht freigegebenen Kanal wird abgewiesen, nicht abgelegt.' },
          { lead: 'Automatisierte Läufe.', text: 'Jeder Kanal hat einen Lieferplan (täglich, wöchentlich, monatlich); das Werkzeug holt die Dateien aus dem Ablageordner, prüft, übernimmt und schreibt je Lauf einen Bericht. Kein Klick nötig, kein Zugriff auf ein Quellsystem.' },
          { lead: 'Abnahmeschwellen mit Verantwortlichem.', text: 'Ungeklärte Zeilen und Vollständigkeit der Datumskette je Kanal; unter der Schwelle geht der Lauf durch, darüber stoppt er und meldet, wer liefern muss.' },
          { lead: 'Eine verantwortliche Rolle je Kanal,', text: 'die die Datei abnimmt und ungeklärte Zeilen zurückbekommt.' },
          { lead: 'Nichts aus einem Unternehmen ohne Freigabe.', text: 'Bis dahin läuft das Werkzeug ausschließlich mit der Simulation und den öffentlichen Preisbelegen.' }
        ] }
    ];

    return {
      kicker: kicker,
      subject: subject,
      intro: intro,
      kpis: kpis,
      kpiDefs: kpiDefs,
      tables: [tFeeds, tDeliveries, tUnres, tSteps].concat(missTables, [tStatus]),
      blocks: blocks
    };
  };
  w.RE.lake.version = 3;
})(window);
