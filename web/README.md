# The page: twelve tabs in four areas on one static file

Live: https://claude.ai/artifact/35YqfY2paPWtkn6yD32U7m (the same files, published as a multi-file artifact).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

This folder is the source of the page that the README screenshots and the slide decks show. It is
**not** the Streamlit dashboard (`python -m restwert dashboard`); it is a second, static front end
that reads nothing live. Every number on it comes from a JSON file under `web/data/`, and every
JSON file is written by a generator under `web/tools/gen/` from the tables and files that
`python -m restwert all` produces. No number is typed in the page code.

## What is where

| path | what | edited by |
|---|---|---|
| `index.template.html` | the skeleton: title, fonts, the three cdnjs scripts (React 18 UMD, ReactDOM, Plotly basic), the `<!--DATA-->` marker | hand |
| `styles.css` | the design system "Broadsheet" (tokens, components) plus the structural classes of the port; the design file is the owner's and is taken over unchanged | design owner |
| `app.js` | the shell: the four areas of the header row (Bericht, Analytics, Market Intelligence, Daten) with the tabs of the chosen area in a second row, status line, protocol, dialogs, export, everything that is operated; renders the view model a motor returns. A tab may read another tab's data (`data: 'market'`) | hand |
| `engine/<tab>.js` | one pure motor per tab: `window.RE.<tab>(D, opts, P)` turns `data/<tab>.json` into a view model (kicker, subject, KPIs, chart, tables, blocks); no DOM, no state, no typed number | hand |
| `engine/_helpers.js`, `engine/_index.js` | formatters (`E.fmt`), table builders (`E.TABLE`, `E.ROW`, `E.C`, `E.N`), the registry check | hand |
| `data/<tab>.json` | the data load of each tab, generated; tracked so the page builds from a clone without DuckDB. `series` and `studies` read `market.json` | `build.py --generate` |
| `data/config.json` | the purchase discount (value and owner) from `config/assumptions.yaml` | `build.py` |
| `tools/gen/make_<tab>_data.py` | the generators, one per data file, reading `outputs/*.csv`, `data/restwert.duckdb`, `config/*.yaml`, `data/catalogue/*.csv` | hand |
| `tools/gen/studies_de.json`, `tools/gen/faq.json` | curated knowledge with URLs: the published studies (international, 13.09.2026; German sources, 16.09.2026) and the FAQ entries. FAQ answers carry no number of the tool as text, only placeholders `{fact, fmt}` that `make_faq_data.py` fills from the run | hand, from read-only research agents |
| `tools/test_page.js` | jsdom run: builds `dist/index.html` offline, clicks every tab and control, unfolds every button, writes the visible text to `out/<tab>.txt`, fails on console errors, dashes, hedge words, external addresses, missing theme tokens, phone width | hand |
| `tools/parity.js` | every number in `ref/<tab>.txt` (the accepted version) must appear in `out/<tab>.txt` | hand |
| `ref/<tab>.txt` | the visible text of the accepted version, one file per tab | `test_page.js` (copied by hand when a version is accepted) |
| `dist/` | the build output (ignored by git): `index.html` with the data inline, `styles.css`, `app.js`, `engine/` | `build.py` |

## Build and test

```bash
python -m restwert all                       # the engine writes outputs/ and data/restwert.duckdb (about 35 s)
python web/build.py --generate               # web/data/*.json from the engine outputs, then web/dist/index.html
python web/build.py                          # embed only: rebuild dist/ from the tracked web/data/*.json
cd web && npm install && npm test            # jsdom render of every tab plus number parity against web/ref
```

`build.py` refuses en and em dashes in the page and needs the `<!--DATA-->` marker in the template. The
generators take three arguments (`<repo root> <out.json> <today>`); `--today` is the date shown as "Stand"
on the page, the data keep their own `as_of` (the valuation date of the engine run).

## The tabs

Four areas in the header row; an area with more than one tab shows them in a second row. Bericht and Daten
stand alone; Analytics holds the tabs on the fleet's own numbers; Market Intelligence holds what the public
market and published sources say (structure since 16.09.2026, on the owner's request).

| area | tab | motor | question | data source |
|---|---|---|---|---|
| Bericht | Bericht | `report.js` | what the device business earned in twelve months, as a bridge for the CFO | `silver.ledger_lines`, `silver.device_ledger`, `main.write_down_ledger`, `main.indirect_spend`, `kpi_values` |
| Analytics | Gerät | `device.js` | one device: catalogue facts, residual value curve, the calculator per term | `data/catalogue/*.csv`, `outputs/market_anchors.csv`, `outputs/market_curves.csv`, `rv_forecast_grid` |
| Analytics | Prognosegüte | `forecast.js` | how well the forecast of record hits the realised price, business view and model view | `rv_forecast_error_monthly`, `backtest_result`, `rv_forecast_of_record`, `forecast_runs`, `advisories`, `kpi_values`, `config/thresholds.yaml`, `config/kpi_targets.yaml` |
| Analytics | TCO | `tco.js` | what one device costs from order to cash, line by line, closed and open | `silver.device_ledger`, `silver.ledger_lines`, `config/assumptions.yaml` |
| Analytics | Kreislauf | `cycle.js` | the closed cycle per family and manufacturer, the levers, the cost lines | `silver.device_ledger`, `silver.ledger_lines`, `gold.levers_summary` |
| Analytics | Stellschrauben | `levers.js` | where to tighten, with owner, threshold and rule per lever | `gold.levers_summary`, `gold.levers_by_cohort`, `config/thresholds.yaml` |
| Analytics | Laufzeit | `term.js` | one year against three years on the same device, the rent a term needs | `outputs/market_curves.csv`, `outputs/market_anchors.csv`, `silver.device_ledger`, `config/lake.yaml` |
| Market Intelligence | Realisierung | `market.js` | where the public used market lands against launch RRP: curves per family and manufacturer, every price evidence with its URL | `outputs/market_anchors.csv`, `outputs/market_curves.csv` |
| Market Intelligence | Serie gegen Serie | `series.js` | one model series against the other at the age of its evidence, plus the one-day spot check marketplace vs trade-in | `data/market.json` (series, studies.gegenprobe) |
| Market Intelligence | Studien | `studies.js` | what published sources measure: German sources by kind of number, international studies, what was searched and not found | `data/market.json` (studies), from `tools/gen/studies_de.json` |
| Market Intelligence | FAQ | `faq.js` | the questions the curves raise, answered with sources; every number of the tool a placeholder filled by the run | `data/faq.json` from `tools/gen/faq.json` and `make_faq_data.py` |
| Daten | Daten | `lake.js` | which feeds are connected, what came in, what the tool rejected | `gold.ingest_summary`, `bronze.unresolved`, `silver.serial_timeline`, `config/lake.yaml` |

Two grey tabs, "Lager" and "Verträge", are planned and not built.

## Honesty box for the page

* Everything manual on the page (recording a price evidence, reading a file, changing a threshold or an
  assumption, saving a scenario, starting a run) stays in the browser (`localStorage`) and is labelled
  "Prototyp: Läufe werden protokolliert, nicht gerechnet". Nothing on the page writes to the engine.
* The page shows the synthetic fleet of the shipped run; the "Stand" date is the build date, the
  "Stichtag" is the engine's valuation date. Both are printed on every tab.
* The design system stylesheet is the owner's file and is taken over unchanged; the build does not lint it.
* Column and tile captions name what they count ("Geräte", "Belege", "Verkäufe"); the abbreviation
  "QTY" of the first versions is gone since 16.09.2026 because it read as a quantity of devices where it
  counted evidence. The TCO tab shows costs only; device counts per term live in Kreislauf.
* Written with AI assistance (Claude Code by Anthropic) against a written contract between shell and
  motors. `ref/` holds the visible text of the version published on 16.09.2026 (Version 5: the four
  areas, the Market Intelligence tabs Serie gegen Serie, Studien and FAQ, laptop curves for Dell, Lenovo
  and Microsoft). The jsdom run proves that every tab renders
  and every number is present; it does not render layout, so phone width, dark mode and legend
  heights are checked by the owner in a browser, not by the test.
