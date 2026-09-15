# The device ledger (v0.2, module `restwert.ledger`)

> THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The ledger is the closed device cycle in money. Every EUR that a serial earns or costs is one
row of `silver.ledger_lines` with a signed amount and a `source_ref` that points to exactly one
bronze row, and that bronze row to one line of one landing file (`source_file`, `row_number`).
`silver.device_ledger` folds the lines into one row per serial and adds the estimates of an
open cycle; `silver.reconciliation` proves that the ledger and the v0.1 `device_pnl` agree to
the cent; the five gold tables roll the result up by cohort.

**TCO is a sum of lines; the only rate is holding cost and every holding line says so.**

## 1. The 17 line types

`restwert.ledger.lines.LINE_TYPES`, in cycle order (`LEDGER_ORDER`). Sign: revenue positive,
cost negative (decision D4). Every amount is net of VAT (decision D5).

| line_type | class | bronze source and booking rule | source_ref | allocation_basis |
|---|---|---|---|---|
| purchase_price | cost | `erp_supplier_invoices`, `line_kind = unit` for the serial, `invoice_date <= as_of`. While no unit invoice exists yet: `erp_po_lines.unit_price_eur` with `is_estimate = true`, `assumption_key = po_line_price_pending_invoice`, owner Head of Procurement (name) | `erp:INV-2024-000183/2` or `erp:PO-2024-000001/1` | direct |
| freight | cost | freight invoice lines of the PO line, `allocate_cents(amount, n received serials)` in serial order, `event_date = invoice_date` | `erp:<invoice>/<line>` | per_unit_of_po_line |
| duty | cost | duty invoice lines of the PO line, same allocation | `erp:<invoice>/<line>` | per_unit_of_po_line |
| staging | cost | `wms_staging_log.staging_cost_eur`, `event_date = staged_at` | `wms:<staging_id>` | direct |
| outbound_shipping | cost | `wms_shipments`, direction `outbound`, the serial itself | `wms:<shipment_id>` | direct |
| rental_revenue | revenue | one line per `portal_rental_invoices` row with `invoice_date <= as_of`, `contract_ref = contract_id` | `portal:<invoice_id>` | direct |
| support | cost | one estimate line per `portal_rental_invoices` row of the serial with `invoice_date <= as_of`: `support_cost_per_device_month_eur` (first-level helpdesk, incident handling, replacement coordination allocated per billed month; a team cost, not a transaction), `event_date = invoice_date`, `contract_ref = contract_id`, `is_estimate = true`, owner Head of Service Operations (name); a rate of 0 books nothing | `assumptions:support_cost_per_device_month_eur:<serial>:<invoice_id>` | months_x_rate |
| mdm_operations | cost | the same, `mdm_cost_per_device_month_eur` (MDM enrolment, policy operations, managed service), only for serials whose `wms_staging_log.mdm_enrolled` is true; the MDM licence itself is the customer's cost and is never booked | `assumptions:mdm_cost_per_device_month_eur:<serial>:<invoice_id>` | months_x_rate |
| repair | cost | `sd_tickets`, resolution `repair`, `closed_at <= as_of`, `repair_cost_eur` | `servicedesk:<ticket_id>` | direct |
| replacement_logistics | cost | `wms_shipments`, direction `replacement_out` where `related_serial = serial` (booked on the damaged device, as in v0.1) | `wms:<shipment_id>` | direct |
| return_logistics | cost | `wms_shipments`, direction `return`, the serial itself | `wms:<shipment_id>` | direct |
| wipe_grading | cost | `ret_receipts.wipe_grading_cost_eur`, `event_date = returned_at` | `returns:<receipt_id>` | direct |
| refurbishment | cost | `rf_work_orders.cost_eur`, `finished_at <= as_of` | `refurb:<work_order_id>` | direct |
| holding_cost | cost | one line per stock phase of the serial (`lines.HOLDING_PHASES`): inbound `received_at -> shipped_at`, return `returned_at -> sellable_at`, sale `sellable_at -> sold_at`, each `days x holding_cost_per_day_eur` with the end capped at `as_of`; a phase whose end step is still missing is open only while the lifecycle status says the device is in it (`not_deployed`, `wip`, `in_stock`), otherwise the chain is broken and the phase is not costed; a scrapped device's return phase is not costed (no scrap date in the timeline); only when `days > 0`; `event_date` = the phase end; `is_estimate = true`, `assumption_key = holding_cost_per_day_eur`, owner CFO (name) | `assumptions:holding_cost_per_day_eur:<serial>:<phase>` | days_x_rate |
| resale_gross | revenue | `rc_orders.gross_price_eur`, `event_date = sold_at <= as_of` | `recommerce:<order_id>` | direct |
| channel_fee | cost | credit note `fee_pct_eur + fee_fixed_eur` when the order has one with `credited_at <= as_of`, `event_date = credited_at` (a credit note dated after `as_of` does not exist yet, the conform step applies the same cut); else `round(gross x fee_pct + fee_fixed, 2)` from `assumptions.channel_fees[channel]` with `event_date = sold_at`, `is_estimate = true`, `assumption_key = channel_fees`, owner Head of Recommerce (name) | `recommerce:<credit_note_id>` or `assumptions:channel_fees[<channel>]` | direct |
| price_protection_credit | revenue | invoice lines of kind `price_protection_credit` on the PO line, allocated like freight | `erp:<invoice>/<line>` | per_unit_of_po_line |

Rules that hold on every line:

* `event_date <= as_of` or the line is not booked; a sale dated after `as_of` is not a sale.
* Open ticket quotes are never lines. A scrapped device has no `resale_gross` line (its
  realised residual value is 0 by absence).
* `line_id = sha1(serial | line_type | source_system | source_ref | event_date)[:24]`;
  `(serial, line_type, source_system, source_ref)` is unique; `period_month` is the first day
  of the event month.
* `delivery_id` is copied from the bronze row and NULL on the three assumption lines.
* `counterparty` is the supplier name, the customer id, the partner's role-only name or the
  channel; `counterparty_role` the role.

Freight, duty and price protection credits are the only allocated lines: an invoice line of
the PO line is split to the cent over the received serials of that line in serial order,
floor per unit and the remainder on the last serial (`restwert.lake.common.allocate_cents`,
10.01 EUR over 3 serials gives 3.33, 3.33, 3.35). The conform step uses the same call for
`devices.landed_cost`, which is why the reconciliation holds.

## 2. Sign and VAT conventions

* `silver.ledger_lines.amount_eur` is signed. The lifecycle result of a serial is
  `SUM(amount_eur)`. The wide `silver.device_ledger` stores positive magnitudes per component
  (`purchase_price`, `freight_eur`, ..., `channel_fee_eur`) so the page reads like an invoice.
* The catalogue RRP (`data/catalogue/variants.csv`) is gross. Every ledger amount is net.
  `rrp_net_eur = round(rrp_gross / (1 + vat_rate), 2)` with `vat_rate` from
  `config/assumptions.yaml` (0.19, owner CFO (name)). Discounts are net against net:
  `discount_vs_rrp_eur = rrp_net - purchase_price`.

## 3. The three result formulas

Closed cycle (`lifecycle_status` in sold, scrapped):

    lifecycle_result_eur = round(SUM(amount_eur), 2)
    result_v01_basis_eur = SUM(amount_eur) excluding staging, outbound_shipping, wipe_grading,
                           holding_cost, support, mdm_operations,
                           price_protection_credit                        (= v0.1 lifecycle_margin)

Open cycle: two numbers, two columns, two tiles, never added (decision D13). There is no
column named `result_total` anywhere, and a test asserts it.

    result_if_liquidated_today = sum_lines_to_date + estimate_rv_today x (1 - fee_pct_marketplace)
                                 - fee_fixed_marketplace

Label: *if every open device were sold today at the fleet model's marketplace estimate;
remaining rent and the value at lease end are NOT in this number.* `estimate_rv_today` is
`rv_forecast_current.forecast_rv`. It is the v0.1 `margin_if_liquidated_today` plus the bridge
lines and the fixed fee.

    result_projected_at_lease_end = sum_lines_to_date + remaining_contracted_rent
                                    + estimate_rv_lease_end x (1 - fee_pct) - fee_fixed
                                    - expected_remaining_cost

* rented and awaiting_return devices: `projected_label = "projected at lease end"`;
  `remaining_contracted_rent = monthly_rate x max(term_months - months_billed on the latest
  active contract, 0)`.
* wip and in_stock devices: remaining rent 0, `projected_label = "projected at sale"`.
* closed and not_deployed devices: NULL.
* `estimate_months_at_lease_end = round(months_between_float(launch_date, contract_end_planned)
  + expected_return_to_sale_days[model_family] / 30.4375)`; for a returned device the months at
  `as_of` plus the same return-to-sale days. `estimate_rv_lease_end = purchase_price x
  grid ratio` read from `rv_forecast_grid` at (model, grade_used, months) with the months
  clipped to the grid range (`estimate_rv_source` = `grid`, `grid_clipped`, or
  `planned_ratio_on_landed_cost` when the grid has no row, exactly as `pnl.tco`).
* `grade_used` = the refurbishment `grade_out` (`grade_source = refurbished`), else the
  inspected grade at return (`inspected`), else `expected_grade_at_return[model_family]`
  (`assumption`).
* `expected_remaining_cost` reuses the realised-or-fallback rule of `pnl.tco` with
  `min_n_for_realised_inputs`; `expected_cost_inputs_source` says `realised`, `assumptions`
  or `mixed`. Rented: `damage_rate_pa x (months_remaining / 12) x repair_share x
  mean_repair_cost + logistics + refurbishment + wipe_grading_mean + holding_cost_per_day x
  expected_return_to_sale_days`; awaiting_return: the same without the repair term; wip:
  refurbishment (unless a work order already finished) plus holding; the holding term uses
  the same stock phases the ledger books: the return and sale phases together are expected
  to last `expected_return_to_sale_days`, and for wip and in_stock devices the days since
  the return receipt (already booked as holding lines up to `as_of`) are deducted, floored
  at 0, so a projected result and a closed result share one holding basis. `wipe_grading_mean`
  is the fleet mean of the family's wipe_grading lines when at least `min_n` exist, else
  `logistics_cost_fallback_eur / 2`.

The public anchor: `anchor_rv_lease_end = rrp_net x exp(intercept + slope x age +
offset[grade])` from the marketplace curve of `bronze.mkt_curves` for the same catalogue family
and manufacturer (the `family_oem` row when its `fit_quality` is `ok`, else the `family` row;
grade B offset 0, grade D offset `lake.yaml truth_v2.grade_d_offset_default`, -0.60 without
the file). It is NULL outside the row's `[age_min, age_max]`: no extrapolation for an advisory
number. The anchor is a refurbisher ask, an upper bound; it enters no result, no rule and no
lever. The fleet model decides, the anchor advises.

## 4. The bridge to v0.1

v0.1 `device_pnl.lifecycle_margin = rental_revenue - (landed_cost - realised_rv) -
(repair + replacement_logistics + return_logistics + refurbishment + channel_fees)`. The ledger
books seven more things that v0.1 never saw: staging, outbound shipping, wipe and grading,
holding cost, the support and MDM allocations per billed month, and price protection credits.
Hence

    lifecycle_margin (v0.1) = result_v01_basis_eur
    lifecycle_result_eur    = result_v01_basis_eur
                              - (staging + outbound_shipping + wipe_grading + holding_cost
                                 + support + mdm_operations)
                              + price_protection_credit

`silver.reconciliation` compares, per serial, `landed_cost`, `purchase_price`,
`months_billed`, `rental_revenue`, `repair_cost`, `replacement_logistics_cost`,
`return_logistics_cost`, `refurb_cost`, `channel_fees`, `realised_rv` and `lifecycle_margin`
of `device_pnl` with their ledger counterparts (`RECONCILED_FIELDS`), tolerance one cent.
`run_ledger` writes the table and then refuses to finish when a single row fails. On synthetic
data equality holds by construction (the same bronze rows, the same `allocate_cents`, rent
invoices dated by `billing_date` so that their count equals v0.1 `months_billed`); on real data
a non-empty diff is the finding the Data page shows.

## 5. Worked example: one serial, hand-summed

Serial S1, a Samsung smartphone (net RRP 1000.00), bought on PO-2024-000001 line 1 together
with S2, rented 12 months at 40.00, repaired once, returned, refurbished and sold on the
marketplace. `as_of` 2026-06-30. The same numbers are the fixture of `tests/test_ledger.py`.

| # | line_type | event_date | amount_eur | source_ref | note |
|---|---|---|---|---|---|
| 1 | purchase_price | 2024-02-05 | -900.00 | erp:INV-2024-000183/2 | unit invoice line |
| 2 | freight | 2024-02-05 | -5.00 | erp:INV-2024-000183/3 | 10.01 over 2 serials: 5.00 and 5.01, S1 first in serial order |
| 3 | staging | 2024-02-09 | -8.50 | wms:ST-1 | |
| 4 | outbound_shipping | 2024-02-09 | -7.00 | wms:SH-1 | |
| 5 | rental_revenue | 2024-03-10 .. 2025-02-10 | +480.00 | portal:RI-RC-1-001 .. 012 | 12 invoices of 40.00, one line each |
| 6 | repair | 2024-08-09 | -120.00 | servicedesk:TK-1 | ticket closed with resolution repair |
| 7 | return_logistics | 2025-02-12 | -9.50 | wms:SH-2 | |
| 8 | wipe_grading | 2025-02-14 | -4.00 | returns:RR-1 | |
| 9 | refurbishment | 2025-02-20 | -30.00 | refurb:WO-1 | grade out B, sellable |
| 10 | holding_cost | 2024-02-09 | -2.40 | assumptions:holding_cost_per_day_eur:S1:inbound | 8 days goods receipt to shipment x 0.30, is_estimate |
| 11 | holding_cost | 2025-02-20 | -1.80 | assumptions:holding_cost_per_day_eur:S1:return | 6 days return receipt to sellable x 0.30, is_estimate |
| 12 | resale_gross | 2025-03-02 | +400.00 | recommerce:RO-1 | marketplace |
| 13 | holding_cost | 2025-03-02 | -3.00 | assumptions:holding_cost_per_day_eur:S1:sale | 10 days sellable to sold x 0.30, is_estimate |
| 14 | channel_fee | 2025-03-30 | -50.50 | recommerce:CN-1 | 48.00 percentage fee + 2.50 fixed, from the credit note, dated at the credit note |
| 15 | support | 2024-03-10 .. 2025-02-10 | -30.00 | assumptions:support_cost_per_device_month_eur:S1:RI-RC-1-001 .. 012 | 12 billed months x 2.50, one line per rent invoice, is_estimate |
| 16 | mdm_operations | 2024-03-10 .. 2025-02-10 | -18.00 | assumptions:mdm_cost_per_device_month_eur:S1:RI-RC-1-001 .. 012 | 12 billed months x 1.50, S1 is mdm_enrolled, is_estimate |

    lifecycle_result_eur = -900.00 - 5.00 - 8.50 - 7.00 + 480.00 - 120.00 - 9.50 - 4.00 - 30.00
                           - 2.40 - 1.80 + 400.00 - 3.00 - 50.50 - 30.00 - 18.00
                         = -309.70

    result_v01_basis_eur = -309.70 + 8.50 + 7.00 + 4.00 + 7.20 + 30.00 + 18.00 = -235.00
    v0.1 lifecycle_margin = 480.00 - (905.00 - 400.00) - (120.00 + 9.50 + 30.00 + 50.50) = -235.00

Wide row of S1: `landed_cost` 905.00, `tco_excl_landed_eur` 284.70, `tco_eur` 1189.70,
`tco_transactional_eur` 1134.50 (every flagged estimate taken out: the three holding lines,
the 12 support and the 12 MDM lines), `support_eur` 30.00, `mdm_eur` 18.00, `resale_net`
349.50, `realised_rv` 400.00, `result_pct_of_landed` -0.3422, `n_lines` 49,
`n_estimate_lines` 27. S2, still rented on the same PO line, carries the inbound phase too
(8 days, 2.40) next to its pending-invoice purchase price, and 2.50 plus 1.50 on every
billed month so far.

## 6. The gold tables of the ledger

* `gold.result_by_cohort`: per cohort kind (`purchase_month`, `purchase_quarter`, `oem`,
  `catalogue_family`, `model_family`, `term_months`, `resale_channel`, `supplier_role`,
  `customer_id`) the closed block (sums and per-device means) and the open block (sum and mean
  of each of the two open numbers). Per row `sum_result_closed = sum_rental_revenue_closed +
  sum_realised_rv_closed + sum_pp_credit_closed - sum_tco_closed` (tested).
* `gold.tco_by_cohort`: per cohort and cost line type the mean and sum of the magnitude over
  the cohort's closed devices; `estimate_eur` is the part of the sum that comes from lines
  flagged `is_estimate` (holding cost always; channel fees without a credit note; PO prices
  without a unit invoice) and `is_estimate` is true when that part is above zero. The means
  stack to the TCO per closed device.
* `gold.purchase_by_oem_month`: units, net RRP, unit price, freight and duty, landed cost,
  discount and landed cost vs RRP (from sums, net of the price protection credits received:
  a credit is a purchase price reduction, the ledger keeps its own line), `ppv_vs_po_eur`
  (unit invoice amount minus PO line price), the share of units under a contract, price
  protection claimable and credited.
* `gold.estimate_vs_anchor`: the rented fleet per (catalogue family, manufacturer): the sum
  of the estimate at lease end, the sum of the anchor over the devices with one, the mean
  ratios to net RRP and `estimate_vs_anchor_ratio` (estimate over the devices with an anchor
  divided by the anchor).
* `gold.resale_by_channel_grade`: sales of the trailing 12 months per channel and grade at
  sale: gross, fees, net, refurbishment, the forecast of record and the realised-vs-record
  ratio, the median days from return to cash, credit notes missing.

## 7. What is an estimate and what is not

`is_estimate`, `assumption_key`, `assumption_owner` and `allocation_basis` sit on every line.
Five things are estimates: holding cost (always, one line per stock phase), the support and
MDM allocations (always, one line per billed rental month, a team cost spread by a rate the
Head of Service Operations owns), the channel fee until the credit note arrives, and the PO
price until the unit invoice arrives.
`tco_transactional_eur = tco_eur - every cost line flagged is_estimate` stands beside
`tco_eur` so that the estimate share is visible on the TCO page and in
`KPI_TCO_ESTIMATE_SHARE`; a closed result is therefore booked lines plus flagged estimates,
and the Result page says so. Write-downs stay a management view in
`main.write_down_ledger` and are never a ledger line.
