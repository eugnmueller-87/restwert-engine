# Konnektoren: wie ein Quellsystem des Hauses automatisch liefert

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Ein Konnektor holt Datensätze aus einem System des Hauses, bildet sie auf einen Feed-Vertrag ab und schiebt sie an die Schnittstelle (`POST /v1/feeds/<feed>`). Die Schnittstelle landet die Datei unter `data/lake/raw/<system>/<feed>/` und importiert sie wie eine Handlieferung. Der Konnektor kennt keine Datenbank, keine Landung und keine Regel; er kennt Felder, eine Mapping-Datei und einen Schlüssel.

Konnektoren liegen hier und nie in `restwert/`: sie dürfen `httpx` benutzen, der Motor nicht (`tests/test_no_side_effects.py` greppt das Paket `restwert/` auf Netzwerkbibliotheken). Kein Geheimnis in einer Datei des Repos: URL, Token und API-Schlüssel kommen aus Umgebungsvariablen, die die Mapping-Datei beim Namen nennt.

## Das Muster: `servicenow_tickets.py`

```
python -m connectors.servicenow_tickets --dry-run                                         # holt, bildet ab, schiebt mit dry_run=true, Wasserzeichen bleibt
python -m connectors.servicenow_tickets --fixture tests/fixtures/servicenow_sample.json --dry-run   # ohne Netz zur Quelle
python -m connectors.servicenow_tickets                                                   # echt: Datei landet, Import läuft, Wasserzeichen rückt vor
```

Umgebung: `SERVICENOW_URL`, `SERVICENOW_TOKEN` (Bearer), `RESTWERT_API_URL` (zum Beispiel `http://127.0.0.1:8420`), `RESTWERT_API_KEY` (das Geheimnis eines Schlüssels mit `source_systems: [servicedesk]`, siehe `config/api_keys.example.yaml`).

Drei Teile, jeder für sich testbar (`tests/test_connector_servicenow.py`):

| Teil | Datei | Was |
|---|---|---|
| Mapping | `connectors/mappings/servicenow_tickets.yaml` | `columns` (Spalte des Feeds: Feld des Hauses, Punktpfad erlaubt), `values` (Wertelisten des Hauses auf die geschlossene Liste des Feeds), `defaults` (fester Wert, wenn die Quelle nichts liefert), `transforms` (`datetime`, `date`, `number`, `text`), `source` (Tabelle, Abfrage, Wasserzeichenfeld, Namen der Umgebungsvariablen), `api` (Namen der Umgebungsvariablen). Der Konnektor prüft die Datei gegen `restwert.lake.feeds.FEEDS`: eine Spalte, die der Vertrag nicht hat, oder eine Pflichtspalte ohne Quelle und ohne Standard bricht den Start ab. |
| Wasserzeichen | `connectors/state/servicenow_tickets.json` | der größte `sys_updated_on` der zuletzt erfolgreich geschobenen Lieferung, `last_run`, `last_delivery_id`, die letzten 50 Läufe. Per `.gitignore` ausgeschlossen. Rückt nur vor, wenn die Schnittstelle eine `delivery_id` zurückgegeben hat und `dry_run` aus war. |
| Code | `connectors/servicenow_tickets.py` | `fetch_servicenow` (seitenweise `GET /api/now/table/<table>` mit `sysparm_query` plus `sys_updated_on><Wasserzeichen>`), `map_record` (ein Datensatz zu einer Zeile), `push_rows` (`POST /v1/feeds/sd_tickets`), `run` (holen, abbilden, in Teilen schieben, Wasserzeichen). `run` nimmt `fetch` und `push` als Funktionen, deshalb läuft der Test mit einer Fixture und dem FastAPI-TestClient statt mit Netz. |

Teile: die Schnittstelle nimmt höchstens `RESTWERT_API_MAX_ROWS` Zeilen (Standard 50000) und `RESTWERT_API_MAX_BODY_MB` (Standard 25 MiB) je Lieferung, darüber 413. Der Konnektor schneidet deshalb selbst: `api.batch_size` in der Mapping-Datei (Standard 5000), eine Lieferung je Teil, `delivery_ids` im Ausgabe-JSON, das Wasserzeichen rückt erst vor, wenn jeder Teil angenommen wurde. Ein erster Lauf ohne Wasserzeichen (Vollabzug) bleibt so unter der Grenze; die Deduplizierung per SHA-256 und `row_hash` macht Teile gefahrlos.

Was der Konnektor nicht tut: Typen, Pflichtfelder, geschlossene Listen und Schlüssel prüfen. Das tut der Import je Zeile und schreibt den Grund nach `bronze.unresolved` (zwölf Grundcodes aus `restwert/lake/feeds.py`). Ein Wert, den `values` nicht kennt, geht unverändert durch und wird `bad_enum`; der Konnektor rät nie. Die Antwort der Schnittstelle (`rows_read`, `rows_new`, `n_unresolved`, `unresolved_by_reason`, die ersten 20 ungeklärten Zeilen) steht im Ausgabe-JSON des Konnektors und im Audit-Log der Schnittstelle (`data/api_audit.jsonl`).

## Die Vorlage für n8n

`connectors/n8n/servicenow_to_restwert.json` ist derselbe Ablauf als n8n-Workflow, importierbar über „Import from File": Schedule Trigger (stündlich) -> HTTP Request ServiceNow -> Code-Node (dasselbe Mapping wie die YAML, in JavaScript) -> If (gibt es Zeilen) -> HTTP Request `POST /v1/feeds/sd_tickets` -> Code-Node (Wasserzeichen in `$getWorkflowStaticData('global')` vorrücken, nur bei `delivery_id`). Platzhalter: die Variablen `SERVICENOW_URL` und `RESTWERT_API_URL`, die Credentials „ServiceNow Bearer" (Header `Authorization: Bearer <token>`) und „Restwert API Key" (Header `X-API-Key`). Werte gehören in n8n-Credentials und Variablen, nie in die Datei. Die Vorlage schiebt alle Zeilen eines Laufs in einer Lieferung; das reicht für den stündlichen Takt. Für einen Vollabzug über 50000 Zeilen (oder 25 MiB) antwortet die Schnittstelle 413, dann die Zeilen im Code-Node in Teile von 5000 schneiden (ein Item je Teil) oder den ersten Abzug über den Python-Konnektor fahren, der das selbst tut.

## Ein zweiter Konnektor in fünf Schritten

Beispiele: das Vertragsregister aus dem CLM (`contracts/register`), Verkaufsaufträge vom Marktplatz (`recommerce/orders`), Sendungen vom Carrier (`wms/shipments`).

1. **Feed wählen.** Tabelle unten, Vollständiges in `docs/DATA_LAKE.md` (aus dem Code gerendert). Der Konnektor muss die Pflichtspalten liefern; was fehlt, wird `missing_required` je Zeile, was nicht auflöst, `unknown_serial` und so weiter. Ein Feed mit `löst auf gegen` braucht, dass die Elternfeeds vorher gelandet sind (`erp/goods_receipts` prägt jede Seriennummer).
2. **Mapping-Datei anlegen**: `connectors/mappings/<name>.yaml` mit `feed`, `source`, `api`, `columns`, gegebenenfalls `values`, `defaults`, `transforms`. `load_mapping` aus `servicenow_tickets.py` liest und prüft sie; für einen anderen Feed ist keine Zeile Code nötig, nur ein anderer `feed`.
3. **Holen schreiben.** Eine Funktion `fetch(since) -> list[dict]` für die Quelle: REST wie bei ServiceNow, oder eine CSV aus einem SFTP-Ordner, oder eine Datenbanksicht. Token aus der Umgebung. Wasserzeichenfeld in `source.watermark_field` nennen; die Quelle muss danach sortieren oder filtern können, sonst bleibt nur ein Vollabzug je Lauf (die Schnittstelle dedupliziert per SHA-256 und `row_hash`, ein Vollabzug ist deshalb teuer, aber nie falsch).
4. **Schlüssel anlegen.** In `config/api_keys.yaml` (oder `RESTWERT_API_KEYS`) einen Schlüssel mit `source_systems: [<system>]`; der Konnektor bekommt das Geheimnis in seiner Umgebungsvariable. Ein Schlüssel für `erp` darf nicht für `servicedesk` liefern (403).
5. **Test mit Fixture.** Datensätze aus der Quelle einmal aufzeichnen (Namen und Nummern ersetzen; keine echten Firmennamen, Partner rollenbasiert wie `Logistics partner (role-only)`), als Fixture unter `tests/fixtures/` ablegen, `run(mapping, state, fetch=lambda since: FIXTURE, push=<TestClient>, dry_run=True)` und die Zähler prüfen. Erst danach `python -m connectors.<name> --dry-run` gegen die echte Quelle, dann echt.

Takt: `POST /v1/runs` mit `as_of` nach der letzten Lieferung des Tages (der Schlüssel braucht `may_run: true`); die Kette `ingest` bis `export` läuft dann seriell, `GET /v1/runs/<id>` zeigt den Stand.

## Feeds und ihre Pflichtspalten

Reihenfolge ist Importreihenfolge; Referenzfeeds (`catalogue/*`, `market/curves`) sind öffentliche Kopien und stehen hier nicht. Landing-Datei je Lieferung: `data/lake/raw/<system>/<feed>/<YYYY-MM-DD>_<feed>_<seq>.csv`, `is_synthetic=false` auf jeder Zeile, keine `#`-Zeile. Optionale Spalten und Typen: `docs/DATA_LAKE.md` oder `GET /v1/feeds/<kurz>`.

| Feed | kurz | Quellsystem | Pflichtspalten | Schlüssel | löst auf gegen |
|---|---|---|---|---|---|
| `contracts/register` | `ctr_register` | contracts | `contract_id`, `counterparty_name`, `counterparty_role`, `counterparty_is_public`, `category`, `start_date`, `end_date`, `notice_days`, `auto_renewal`, `price_protection`, `payment_terms_days`, `spend_under_contract_eur`, `terms_note` | contract_id | nichts |
| `erp/purchase_orders` | `erp_purchase_orders` | erp | `po_number`, `supplier_id`, `supplier_name`, `supplier_role`, `order_date`, `promised_date`, `currency` | po_number | nichts |
| `erp/po_lines` | `erp_po_lines` | erp | `po_number`, `po_line`, `slug`, `storage_gb`, `qty_ordered`, `unit_price_eur`, `order_date` | po_number, po_line | `bronze.erp_purchase_orders`, `bronze.cat_variants` |
| `erp/goods_receipts` | `erp_goods_receipts` | erp | `gr_number`, `po_number`, `po_line`, `serial`, `received_at` | serial | `bronze.erp_po_lines` |
| `erp/supplier_invoices` | `erp_supplier_invoices` | erp | `invoice_number`, `invoice_line`, `supplier_id`, `po_number`, `po_line`, `invoice_date`, `line_kind`, `qty`, `amount_eur`, `currency` | invoice_number, invoice_line | `bronze.erp_po_lines`, `bronze.erp_goods_receipts` |
| `erp/price_changes` | `erp_price_changes` | erp | `change_id`, `supplier_id`, `slug`, `storage_gb`, `valid_from`, `old_unit_price_eur`, `new_unit_price_eur` | change_id | `bronze.cat_variants` |
| `wms/staging_log` | `wms_staging_log` | wms | `staging_id`, `serial`, `staged_at`, `mdm_enrolled`, `staging_cost_eur` | staging_id | `bronze.erp_goods_receipts` |
| `wms/shipments` | `wms_shipments` | wms | `shipment_id`, `serial`, `direction`, `shipped_at`, `carrier_ref`, `cost_eur` | shipment_id | `bronze.erp_goods_receipts` (serial und related_serial) |
| `portal/rental_contracts` | `portal_rental_contracts` | portal | `contract_id`, `customer_id`, `serial`, `start_date`, `term_months`, `monthly_rate_eur`, `end_date`, `status` | contract_id | `bronze.erp_goods_receipts` |
| `portal/rental_invoices` | `portal_rental_invoices` | portal | `invoice_id`, `contract_id`, `serial`, `period_no`, `period_month`, `invoice_date`, `amount_eur` | invoice_id | `bronze.portal_rental_contracts`, `bronze.erp_goods_receipts` |
| `servicedesk/tickets` | `sd_tickets` | servicedesk | `ticket_id`, `serial`, `opened_at`, `damage_type`, `resolution`, `quote_eur`, `repair_partner_ref` | ticket_id | `bronze.erp_goods_receipts` |
| `returns/receipts` | `ret_receipts` | returns | `receipt_id`, `serial`, `returned_at`, `grade_declared`, `grade_inspected`, `inspected_at`, `wipe_grading_cost_eur` | receipt_id | `bronze.erp_goods_receipts` |
| `refurb/work_orders` | `rf_work_orders` | refurb | `work_order_id`, `serial`, `started_at`, `finished_at`, `cost_eur`, `grade_out`, `outcome`, `partner_ref` | work_order_id | `bronze.erp_goods_receipts` |
| `recommerce/orders` | `rc_orders` | recommerce | `order_id`, `serial`, `channel`, `listed_at`, `sold_at`, `gross_price_eur`, `buyer_type`, `grade_at_sale` | order_id | `bronze.erp_goods_receipts` |
| `recommerce/credit_notes` | `rc_credit_notes` | recommerce | `credit_note_id`, `order_id`, `serial`, `channel`, `credited_at`, `gross_eur`, `fee_pct_eur`, `fee_fixed_eur`, `net_eur` | credit_note_id | `bronze.rc_orders`, `bronze.erp_goods_receipts` |
| `finance/indirect_spend` | `fin_indirect_spend` | finance | `spend_id`, `invoice_date`, `category`, `supplier_name`, `amount_eur`, `has_po`, `has_contract`, `saving_eur`, `saving_confirmed_by_controlling` | spend_id | nichts |

Namen von Lieferanten und Gegenparteien (`supplier_name`, `counterparty_name`, `partner_ref`, `repair_partner_ref`, `carrier_ref`) folgen der Regel des Repos: ein Herstellername aus `data/catalogue/models.csv` oder ein rollenbasierter Name mit dem Suffix ` (role-only)`. Ein Konnektor, der Klarnamen von Partnern liefert, verstößt gegen `tests/fixtures/allowlist.py`; das Mapping ersetzt sie (`values` oder `defaults`).
