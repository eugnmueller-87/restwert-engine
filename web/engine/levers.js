/* Restwert Engine v3, Motor Stellschrauben (Reiter levers). Vertrag: v3/CONTRACT.md, Abschnitt 7.6.
   Portiert aus v3/src/levers.js und v3/src/levers.body.html: dieselben Saetze, dieselben Zahlen, dieselbe
   Formatierung ueber RE.fmt, als Ansichtsmodell statt als DOM. Klassisches Skript, kein Modul, kein Zustand.

   Aufbau der Ansicht:
   - kicker, subject, intro (Einleitung plus Simulationshinweis)
   - overview: die Uebersichtstabelle (Zeile anklicken waehlt die Stellschraube; die Huelle zeigt nur diese Tabelle)
   - dossier: die gewaehlte Stellschraube im Detail (Pruefffrage, Tun, Wer dreht daran, Herleitung, Kanaltabelle,
     ein Geraet vorgerechnet)
   - blocks: jede Stellschraube als eingeklappte Karte zum Nachlesen. Die Huelle zeigt nur EIN Dossier; heute stehen
     alle Karten untereinander. Die Karten als Klappbloecke halten jede Zahl und jeden Satz der heutigen Seite
     erreichbar. CARDS_AS_BLOCKS = false nimmt sie heraus. */
(function (w) {
  'use strict';
  var E = w.RE;
  var CARDS_AS_BLOCKS = true;

  /* Heutiger Auswahlsatz des vorgerechneten Geraets (aus src/levers.js), ohne Ziffer. */
  var PICK_RULE = 'aus der jüngsten Modellgeneration mit Hebel im Fenster (mindestens drei Geräte) das Gerät, dessen Hebel dem mittleren Hebel aller Geräte mit Hebel größer null am nächsten liegt.';
  var NO_EXAMPLE = 'Im Fenster kein Gerät mit einem Hebel größer null.';
  var CHANNEL_TITLE = 'Tage je Verkaufskanal in der Simulation';

  function isNum(x) { return typeof x === 'number' && isFinite(x); }

  /* Tage wie heute: ganze Zahl ohne Nachkommastelle (10.0 wird 10), sonst eine Nachkommastelle, deutsch. */
  function days(f, x) {
    if (!isNum(x)) return x === null || x === undefined ? '' : String(x);
    return x === Math.round(x) ? f.qty(x) : f.num(x, 1);
  }

  /* Vorformatierte Werte der vorgerechneten Kette kommen als Text aus den Daten. Ein reiner Dezimalwert mit
     Punkt (heute "30.0") wird deutsch geschrieben; alles andere bleibt, wie es ist. */
  function german(f, v) {
    var s = v === null || v === undefined ? '' : String(v);
    var m = /^(-?\d+)\.(\d+)$/.exec(s);
    return m ? f.num(Number(s), m[2].length) : s;
  }

  /* "über 90 Tagen": die Zahl steht nicht als Wert, sondern im Feldnamen over90 des Lagerstands. */
  function overKey(stock) {
    var keys = stock ? Object.keys(stock) : [];
    for (var i = 0; i < keys.length; i++) if (/^over\d+$/.test(keys[i])) return keys[i];
    return null;
  }

  function channelFoot(f, c) {
    var s = 'Mittelwerte je Gerät der abgeschlossenen Kreisläufe.';
    var sn = c.stock_now, ok = overKey(sn);
    if (!sn) return s;
    s += ' Heute im Lager: ' + f.qty(sn.n) + ' verkaufsfähige Geräte, im Mittel ' + days(f, sn.d) + ' Tage';
    if (ok) s += ', davon ' + f.qty(sn[ok]) + ' über ' + ok.replace(/^over/, '') + ' Tagen (Regel ' + c.rule + ')';
    return s + '.';
  }

  function channelCols() {
    return [E.H('Verkaufskanal'), E.H('Geräte', 1), E.H('Rückgabe bis verkaufsfähig', 1), E.H('verkaufsfähig bis verkauft', 1), E.H('Rückgabe bis Zahlungseingang', 1)];
  }

  function channelRows(f, c) {
    return (c.channel_days || []).map(function (r) {
      return E.ROW([E.C(r.kanal), E.N(f.qty(r.n)), E.N(days(f, r.d_sellable) + ' Tage'), E.N(days(f, r.d_sold) + ' Tage'), E.N(days(f, r.d_cash) + ' Tage')]);
    });
  }

  function kvSum(c) { return 'Summe der Hebel' + (c.per_cohort ? ', einmal je Gruppe' : ''); }
  function kvDev(c) { return c.per_cohort ? 'Hebel je Gerät der Gruppe; Euro je Jahr zählt jede Gruppe nur einmal' : 'Euro je Jahr geteilt durch die bewerteten Geräte'; }

  function eventNote(f, c) {
    return 'Maßgebliches Ereignis für das Zwölf-Monats-Fenster: ' + c.event + '. Über alle Jahre der Simulation: ' + f.qty(c.all_n) + ' bewertet, Summe ' + f.eur(c.all_sum) + '.';
  }

  function exampleHead(ex) { return 'Gerät ' + ex.serial + (ex.model ? ', ' + ex.model : ''); }

  /* Schwelle: der Text aus den Daten, oder die vom Nutzer geaenderte Schwelle aus opts.thresholds. */
  function thresholdOf(c, th) {
    var t = th && th[c.lever_id];
    if (!t || !String(t.text || '').trim()) return { text: c.threshold, changed: false, note: '' };
    var note = 'Bisher: ' + c.threshold + '. Geändert ' + (t.at || '') + ' durch ' + (t.by || 'ohne Rolle') + (t.note ? ', Begründung: ' + t.note : '') + '; gilt ab dem nächsten Lauf.';
    return { text: String(t.text).trim(), changed: true, note: note };
  }

  function dossierOf(D, f, c, th) {
    var ex = c.example || null, t = thresholdOf(c, th), hasChan = Array.isArray(c.channel_days) && c.channel_days.length > 0;
    return {
      id: c.lever_id, name: c.name, handle: c.handle,
      sum: f.eur(c.sum_win) + ' je Jahr', sumNeg: isNum(c.sum_win) && c.sum_win < 0,
      frage: c.frage, bedeutung: c.bedeutung, tun: (c.tun || []).slice(),
      meta: [
        { k: 'Verantwortlich', v: c.owner },
        { k: 'Schwelle', v: t.text, changed: t.changed, note: t.note },
        { k: 'Regel, die reagiert', v: c.rule }
      ],
      kv: [
        { k: 'bewertet', v: f.qty(c.n_attr), s: 'Geräte mit Referenzwert' },
        { k: 'mit Hebel', v: f.qty(c.n_pos), s: 'davon Geld liegen geblieben' },
        { k: 'Euro je Jahr', v: f.eur(c.sum_win), s: kvSum(c) },
        { k: 'Euro je Gerät', v: f.eur2(c.per_device), s: kvDev(c) }
      ],
      eventNote: eventNote(f, c),
      hasChannel: hasChan,
      channel: hasChan ? { title: CHANNEL_TITLE, cols: channelCols(), rows: channelRows(f, c), foot: channelFoot(f, c) } : null,
      hasExample: !!ex,
      example: ex ? {
        head: exampleHead(ex),
        delta: ex.event + ' am ' + f.de(ex.date) + ', Hebel ' + ex.delta,
        rows: (ex.rows || []).map(function (r) { return { k: r[0], v: german(f, r[1]) }; }),
        formula: ex.formula || '',
        note: 'Auswahl: ' + PICK_RULE
      } : null,
      threshold: t.text, owner: c.owner
    };
  }

  /* Die Karte als Klappblock: derselbe Inhalt wie das Dossier, als Liste. */
  function cardBlock(D, f, c, th, selected) {
    var ex = c.example || null, t = thresholdOf(c, th), items = [];
    /* Reihenfolge wie im Dossier: erst die Kanaltage, dann "Was man tun kann" (dessen Punkte auf die Tabelle oben verweisen) */
    if (Array.isArray(c.channel_days) && c.channel_days.length) {
      items.push({ lead: CHANNEL_TITLE + '.', text: c.channel_days.map(function (r) {
        return r.kanal + ': ' + f.qty(r.n) + ' Geräte, Rückgabe bis verkaufsfähig ' + days(f, r.d_sellable) + ' Tage, verkaufsfähig bis verkauft ' + days(f, r.d_sold) + ' Tage, Rückgabe bis Zahlungseingang ' + days(f, r.d_cash) + ' Tage';
      }).join('; ') + '. ' + channelFoot(f, c) });
    }
    items.push({ lead: 'Was man tun kann.', text: (c.tun || []).join(' ') });
    items.push({ lead: 'Herleitung, zwölf Monate bis ' + f.de(D.as_of) + '.', text: 'bewertet ' + f.qty(c.n_attr) + ' (Geräte mit Referenzwert), mit Hebel ' + f.qty(c.n_pos) + ' (davon Geld liegen geblieben), Euro je Jahr ' + f.eur(c.sum_win) + ' (' + kvSum(c) + '), Euro je Gerät ' + f.eur2(c.per_device) + ' (' + kvDev(c) + '). ' + eventNote(f, c) });
    if (ex) {
      items.push({ lead: 'Ein Gerät, vorgerechnet.', text: exampleHead(ex) + ', ' + ex.event + ' am ' + f.de(ex.date) + '; ' + PICK_RULE + ' Hebel ' + ex.delta + '.' });
      (ex.rows || []).forEach(function (r) { items.push({ lead: '', text: r[0] + ': ' + german(f, r[1]) }); });
      if (ex.formula) items.push({ lead: '', text: ex.formula });
    } else {
      items.push({ lead: 'Ein Gerät, vorgerechnet.', text: NO_EXAMPLE });
    }
    items.push({ lead: 'Wer dreht daran.', text: 'Schwelle: ' + t.text + (t.changed ? ' (geändert; ' + t.note.replace(/\.$/, '') + ')' : '') + '. Verantwortlich: ' + c.owner + '. Regel, die reagiert: ' + c.rule + '.' });
    var b = {
      key: 'l-card-' + c.lever_id,
      title: 'Karte ' + c.lever_id + ' ' + c.name + ': ' + f.eur(c.sum_win) + ' je Jahr',
      intro: 'Prüffrage: ' + c.frage + ' ' + c.bedeutung,
      items: items, ordered: false
    };
    if (selected) b.tag = { cls: 'tag-accent', text: 'gewählt' };
    return b;
  }

  w.RE.levers = function (D, opts, P) {
    if (!D || !Array.isArray(D.cards)) throw new Error('levers: D.cards fehlt');
    opts = opts || {};
    var f = E.fmt, cards = D.cards, th = opts.thresholds || {};
    var select = typeof opts.select === 'function' ? opts.select : null;
    var sel = null;
    for (var i = 0; i < cards.length; i++) if (cards[i].lever_id === opts.lever) { sel = cards[i]; break; }
    if (!sel) sel = cards[0] || null;

    /* Kennungen L01 bis L07: erste und letzte aus den Daten, nicht getippt. */
    var ids = cards.map(function (c) { return String(c.lever_id); }).sort();
    var idSpan = ids.length ? ids[0] + ' bis ' + ids[ids.length - 1] : '';

    var rows = cards.map(function (c) {
      var isSel = c === sel;
      var ro = { bold: isSel };
      if (isSel) ro.bg = 'var(--ink-04)';
      if (select) ro.onClick = function () { select(c.lever_id); };
      return E.ROW([
        E.C(c.name, { bold: true, sub: c.lever_id + ' · ' + c.handle, minW: 220 }),
        E.C(c.frage, { minW: 260 }),
        E.N(f.qty(c.n_attr)),
        E.N(f.qty(c.n_pos)),
        E.N(f.eur(c.sum_win), { neg: isNum(c.sum_win) && c.sum_win < 0 }),
        E.N(f.eur2(c.per_device), { neg: isNum(c.per_device) && c.per_device < 0 }),
        E.C(c.owner),
        E.C(c.rule)
      ], ro);
    });

    var overview = E.TABLE('l-overview', 'Übersicht, zwölf Monate bis ' + f.de(D.as_of), [
      E.H('Stellschraube'), E.H('Prüffrage'), E.H('bewertet', 1), E.H('mit Hebel', 1), E.H('Euro je Jahr', 1), E.H('Euro je Gerät', 1), E.H('Verantwortlich'), E.H('Regel')
    ], rows, {
      note: 'Diese Tabelle zeigt sieben Stellen, an denen die Simulation Geld liegen lässt: je Stellschraube der Vergleich jedes Geräts mit einem benannten Referenzwert aus der eigenen Flotte, in Euro je Gerät und als Summe der letzten zwölf Monate, jede mit verantwortlicher Rolle und der Regel, die reagiert. Eine Zeile anklicken zeigt die Stellschraube darunter im Detail; alle Karten stehen am Ende der Seite zum Nachlesen.',
      defs: [
        { k: 'Stellschraube', v: 'Name und Griff: woran die verantwortliche Rolle drehen kann; die kleine Kennung dahinter (' + idSpan + ', L für Hebel) ist nur die Nummer im Werkzeug, in den Regeln und im Code, sonst ohne Bedeutung' },
        { k: 'Prüffrage', v: 'was das Werkzeug je Gerät misst, um zu sehen, ob an dieser Stellschraube Geld liegen blieb' },
        { k: 'bewertet', v: 'Geräte, deren maßgebliches Ereignis in die zwölf Monate fällt und für die ein Referenzwert vorlag; welches Ereignis das ist (Kauf, Verkauf oder Abschluss des Kreislaufs), nennt jede Karte' },
        { k: 'mit Hebel', v: 'davon Geräte, bei denen Geld liegen blieb' },
        { k: 'Euro je Jahr', v: 'Summe der Hebel dieser Geräte; die Herleitung steht auf der Karte darunter' },
        { k: 'Euro je Gerät', v: 'Euro je Jahr geteilt durch die bewerteten Geräte; bei Laufzeit der Hebel je Gerät der Gruppe, weil Euro je Jahr dort jede Gruppe nur einmal zählt' },
        { k: 'Verantwortlich', v: 'Rolle, die die Schwelle setzt und ändern darf' },
        { k: 'Regel', v: 'Nummer der Entscheidungsregel, die auf die Schwelle reagiert; ADV (advisory) heißt nur Hinweis, keine Entscheidung' }
      ],
      foot: 'Die Zeilen dürfen nicht addiert werden: Einkaufsrabatt und Preisschutz betreffen den Einkauf, die fünf anderen bewerten denselben Verkauf aus verschiedenen Richtungen; wer sie addiert, zählt dasselbe Gerät mehrfach. Nur Einkaufsrabatt und Preisschutz lassen sich gegen das Hauptbuch nachrechnen; das Werkzeug tut das bei jedem Lauf, ohne Abweichung. Die Stellschraube Laufzeit ist ein Vergleich von Vertragspolitiken und zählt je Gruppe (Geräteart und Einkaufshalbjahr), nicht je Gerät.',
      tags: [{ cls: 'tag-neutral', text: 'simuliert' }]
    });

    var lead = 'Eine Stellschraube ist ein Griff, an dem eine benannte Rolle drehen kann. Die Prüffrage daneben sagt, wie das Werkzeug misst, ob dort Geld liegen blieb; unter jeder Karte steht, was man tun kann. Die Messung ist immer dieselbe Rechnung: für jedes Gerät der Ist-Wert minus ein benannter Referenzwert, in Euro, auf einer oder zwei benannten Buchungszeilen; das Ergebnis dieser Rechnung heißt Hebel. Größer null heißt liegengelassenes Geld, kleiner null heißt besser als die Referenz. Der Referenzwert ist nie ein Vergleich mit anderen Unternehmen, sondern die eigene Flotte, die Restwertprognose bei Rückgabe oder die Wertkurve der Simulation. Alle Zahlen auf dieser Seite beziehen sich auf dieselben zwölf Monate vor dem Stichtag.';
    var banner = 'Simulierte Daten. Die Rangfolge der Stellschrauben folgt aus den Annahmen der Simulation (Datei config/lake.yaml), nicht aus einem Markt. Die Rechnung bleibt dieselbe; mit echten Daten eines Hauses liefert sie dessen Rangfolge.';

    var blocks = CARDS_AS_BLOCKS ? cards.map(function (c) { return cardBlock(D, f, c, th, c === sel); }) : [];

    return {
      kicker: 'Simulation, Stand ' + f.de(D.today) + ' (Stichtag der Daten ' + f.de(D.as_of) + ')',
      subject: 'Stellschrauben: woran eine Rolle drehen kann, und was das Werkzeug dazu misst',
      intro: lead + ' ' + banner,
      kpis: [],
      tables: [],
      overview: overview,
      dossier: sel ? dossierOf(D, f, sel, th) : null,
      blocks: blocks
    };
  };
  w.RE.levers.version = 3;
})(window);
