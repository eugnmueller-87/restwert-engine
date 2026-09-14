# Levers: where to tighten, and who owns the screw

> THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

A lever is actual minus a named reference on one ledger component, in EUR per device, with `delta_eur >= 0` meaning money left on the table. The reference is a row set of the fleet itself (a percentile or a median of the provider's own devices), the forecast of record, or the forecast grid. Never an external benchmark. **A lever is a reference, not a counterfactual fact**: it says how far a device sits from a stated reference, not what would have happened.

Below `lever_reference_min_n` (`config/assumptions.yaml`, owner CFO) a lever is `is_attributed = false` with `delta_eur` NULL, never guessed. `counterfactual_json` on every row stores every input used: the reference group and its n, the two values, and the months and grades read from the grid. Rendered by `restwert.levers.summary.render_levers_md` on `config/thresholds.yaml` version 1; owners come from that file at render time.

## The seven levers

| id | name | component | basis | additive | eligible serials | delta_eur per device |
|---|---|---|---|---|---|---|
| L01 | purchase_discount | purchase_price | fleet | yes | every received serial | `(d_ref - discount_vs_rrp_pct) x rrp_net`, floored at 0, with the purchase price net of the price protection credit received; `d_ref` = p75 of `discount_vs_rrp_pct` over the fleet group (oem, supplier role, purchase half-year) with n >= min_n, else the oem over all time with n >= min_n, else not attributed |
| L02 | price_protection | price_protection_credit | fleet | yes | every received serial (status decides the value) | `price_protection_claimable_eur` when `price_protection_status = missed`; 0 when claimed or not applicable (reference `claimed` / `n/a`); an open window is not attributed |
| L03 | channel_choice | resale_gross + channel_fee | forecast_of_record | no | sold serials with a forecast of record | `net[best admissible channel] - net[actual channel]`, floored at 0, with `net_c = rv_of_record x channel_factor_c x (1 - fee_pct_c) - fee_fixed_c - holding_per_day x days_to_cash_c` for EVERY channel, the actual one included (the R02 filter decides admissibility: grade, buyout window after the effective contract end, maximum days to cash, never the last channel); zero when the best channel was used. What the actual channel realised against the record (`realised_vs_record_gap_eur` in the record) is forecast accuracy and belongs to `KPI_RSL_REALISED_VS_RECORD`, never to the lever |
| L04 | grade_and_repair | resale_gross, repair, refurbishment | grid | no | closed serials with a return | grade part `purchase_price x (grid(model, grade_declared, m_ret) - grid(model, grade_inspected, m_ret))` at the months since launch at return, NOT floored, read only where the grid orders the grades (A >= B >= C >= D at that model and month) and neither grade is unsupported (`fit_quality = unsupported_grade`, the As-Is fallback); otherwise the lever is not attributed and the record says why; plus repair part `sum over repair lines of max(0, repair - repair_max_share_of_rv[family] x purchase_price x grid(model, grade_used, m_repair))` |
| L05 | aging | holding_cost, resale_gross | grid | no | sold serials and in-stock serials | `excess_days x holding_per_day + purchase_price x max(0, grid(grade_out, m_expected) - grid(grade_out, m_actual))`; `excess_days = max(0, days_sellable_to_sold - expected_return_to_sale_days[family])`; in-stock devices measured at as_of |
| L06 | manufacturer_mix | resale_gross | fleet | no | sold serials | `(median_ratio_family_bucket_grade - resale_gross / rrp_net) x rrp_net`, NOT floored; the median runs over the fleet's own sold devices of the same (catalogue family, 6-month age bucket at sale, grade at sale) with n >= 2 x min_n; summarised by oem |
| L07 | term_length | lifecycle_result | fleet | no | closed serials | `(median(result / term_months) of the best other term - the same of this term) x this term's months` inside (model_family, purchase half-year) over the terms 12, 24, 36 and 48 months, this term and the other term n >= min_n, floored at 0; per month of term so the longer term is not credited with its extra months of rent; the same value on every serial of the cohort and counted ONCE PER COHORT in the summary: a policy comparison, not money per device |

## Threshold, owner, reacting rule and reference parameter per lever

`rule` is the rule (R01, R02, R03, R05, R07) or advisory (ADV03, ADV04) that REACTS to the lever's component and the threshold it reads; an advisory never acts, it asks a human. `reference parameter` is the `config/assumptions.yaml` key the lever's own arithmetic rests on, with its owner: for L05 that is `expected_return_to_sale_days` (Head of Recommerce), not `aging_days_90`, which only tells R03 when to write down aged stock.

| id | threshold | value | unit | owner | rule | reference parameter | reference |
|---|---|---|---|---|---|---|---|
| L01 | `purchase_discount_floor_pct` | Apple: 0.06, Samsung: 0.15, Google: 0.12, Motorola: 0.18, Fairphone: 0.04, HMD Global (Nokia): 0.18, Lenovo: 0.18, Dell: 0.18, HP: 0.18, Microsoft: 0.1 | ratio | Head of Procurement (name) | R07 | `lever_reference_min_n` (CFO (name)) | the 75th percentile of the discount vs net launch RRP (net of price protection credits) over the fleet's own purchases of the same manufacturer, supplier role and purchase half-year (else the manufacturer over all time) |
| L02 | `price_protection_min_claim_eur` | 500 | EUR | Category Manager Hardware (name) | R05 | the serial's own data | the price protection credit the PO line was entitled to; missed claims count, claimed and not applicable ones are 0, open windows are not attributed |
| L03 | `channel_min_net_uplift_eur` | 15 | EUR | Head of Recommerce (name) | R02 | `channel_fees` (Head of Recommerce (name)) | the best net over the channels R02 would have admitted minus the net of the channel used, both valued at the forecast of record times the run's channel factor, net of fees and holding cost to cash; zero when the best channel was used |
| L04 | `repair_max_share_of_rv` | iphone_like: 0.4, android_like: 0.35, laptop_like: 0.45, tablet_like: 0.38 | ratio | Head of Service Operations (name) | R01 | the serial's own data | the grid value at the grade the customer declared versus the grade inspected (read only where the grid orders the grades and neither grade is unsupported), plus every repair line above the family's maximum share of the grid value at repair time |
| L05 | `aging_days_90` | 90 | days | CFO (name) | R03 | `expected_return_to_sale_days` (Head of Recommerce (name)) | the sale at the family's expected return-to-sale days (expected_return_to_sale_days, owner Head of Recommerce): holding cost of the excess days plus the grid value lost between the expected and the actual sale month; R03 and aging_days_90 are the rule and threshold that react to aged stock |
| L06 | `oem_realisation_gap_pct` | 0.05 | ratio | Category Manager Hardware (name) | ADV03 | `lever_reference_min_n` (CFO (name)) | the median realised share of net RRP over the fleet's own sales of the same catalogue family, 6-month age bucket and grade at sale; negative when the manufacturer beat it |
| L07 | `term_result_gap_alert_eur` | 50 | EUR | CFO (name) | ADV04 | `lever_reference_min_n` (CFO (name)) | the median closed result per month of term of the best other contract term (of 12, 24, 36 and 48 months) inside the same segment and purchase half-year, times this term's months; the same value on every serial of the cohort and counted once per cohort in the summary (a policy comparison) |

## Additivity

Levers do not add up. Only L01 and L02 sit on disjoint components (purchase price and the price protection credit); for every closed serial the identity `result_v01_basis + L01 + L02 == the same result with the purchase paid at the (capped) reference price and the missed credit received` must hold. `check_additivity` evaluates the right side from `silver.ledger_lines` (every non-bridge line except the purchase, the credit received netted, the capped reference price paid, the missed credit added) and the left side from the device ledger's stored basis and the two stored deltas, so a wrong delta, a basis that drifted from the lines or a purchase price that does not match its invoice line all surface; without the lines (hand tests) it degrades to a formula consistency test and says so. The run refuses to report success with a violation; the count `additivity_violations` in the run summary must be 0. L03 to L07 overlap on the resale line and on the grid and are read one at a time. `gold.levers_summary` therefore never carries a total row, and the dashboard says so next to the table.

## The where-to-tighten table (`gold.levers_summary`)

Per lever: `n_eligible`, `n_attributed`, `eur_per_device` (mean delta over attributed serials), `eur_per_device_p90`, `eur_fleet_per_year` (sum of delta over attributed serials whose event date lies in the trailing 12 months before as_of: purchase date for L01 and L02, sale date for L03, L05 and L06, closed date for L04 and L07, as_of for in-stock L05 rows; L07 counted ONCE PER COHORT, not per serial), `share_of_lever_basis` (that sum over `lever_basis_eur`, the population the lever itself measures and names in `lever_basis`: the landed cost of the same purchases for L01 and L02, the absolute closed result of the same serials for L03 to L07; NULL when the basis is zero; the ratio can exceed 1 because a lever is measured on every attributed serial, not only on the ones that lost money, and it never says that the lever explains the fleet's loss), the threshold key, value, unit and owner resolved through `Thresholds.get` (per-oem keys shown for the oem with the largest yearly EUR as `oem: value`), `rule_id` (the rule or advisory that reacts), `reference_key` and `reference_owner` (the assumption the arithmetic rests on), the reference sentence and `rank` by `eur_fleet_per_year`.

## Rule R07 and the advisories ADV03 and ADV04

R07 `purchase_discount_floor` is a pure rule on every PO line of the ledger: `discount = 1 - unit_price / rrp_net`; at or above `purchase_discount_floor_pct[oem]` nothing happens (`at_or_above_floor`, logged, not queued); below it the line is queued at priority 2 with `value_at_stake = (unit_price - rrp_net x (1 - floor)) x qty`, positive when the rule fires (as `restwert.decisions.rules` and docs/DECISION_RULES.md compute it). ADV03 `manufacturer_mix` asks the category manager to review the allocation when a manufacturer's mean L06 gap over the trailing 12 months exceeds `oem_realisation_gap_pct`; ADV04 `term_gap` asks the CFO to review the term policy when a cohort's L07 gap reaches `term_result_gap_alert_eur`. Both are advisories: priority 3 in the queue, never an outcome.

## Worked example: one hand serial

Serial `SN-EXAMPLE`, an android_like smartphone: net RRP 700.00, purchase price 595.00
(discount 15.0 %), 24-month term, returned declared B and inspected C, one repair line of
120.00 at month 20, refurbished to grade C, sellable on day 0, sold on day 50 through the
marketplace at 210.00 gross with 27.70 fees (net 182.30), credit note 28 days after the sale.
Assumptions: holding cost 0.30 per day, marketplace fee 12 % + 2.50, 28 days to cash,
expected return-to-sale days 35, `repair_max_share_of_rv[android_like]` 0.35. Grid ratios at
the months in question: grade B 0.40 and grade C 0.32 at return (month 26); grade C 0.32 at
month 20; grade C 0.31 at the expected sale month and 0.30 at the actual sale month.

| lever | reference | arithmetic | delta_eur |
|---|---|---|---|
| L01 | p75 discount of the group = 0.20 (n = 40); no price protection credit received | reference price 700 x 0.80 = 560.00; 595.00 - 560.00 | 35.00 |
| L02 | status `missed`, claimable 25.00 | the credit the PO line was entitled to | 25.00 |
| L03 | forecast of record 260.00, channel factors buyout 0.90 and b2b 0.97, fees buyout 0 %, marketplace 12 % + 2.50, b2b 3 %; admissible: employee_buyout, marketplace, b2b_wholesale; sold on the marketplace | nets at the record: buyout 260 x 0.90 - 0.30 x 14 = 229.80; marketplace 260 x 0.88 - 2.50 - 0.30 x 28 = 217.90; b2b 260 x 0.97 x 0.97 - 0.30 x 45 = 231.13; 231.13 - 217.90 (the realised 210.00 against the record 260.00 is forecast accuracy, not in the lever) | 13.23 |
| L04 | grid at return: A 0.46, B 0.40, C 0.32, D 0.15 (ordered, none unsupported); repair limit 0.35 x 595 x 0.32 = 66.64 | grade part 595 x (0.40 - 0.32) = 47.60; repair part max(0, 120.00 - 66.64) = 53.36 | 100.96 |
| L05 | expected 35 days | excess 15 days x 0.30 = 4.50; value part 595 x max(0, 0.31 - 0.30) = 5.95 | 10.45 |
| L06 | family median ratio 0.33 (n = 25) at bucket 24-30, grade C | (0.33 - 210 / 700) x 700 = (0.33 - 0.30) x 700 | 21.00 |
| L07 | best other term of the cohort (12, 24, 36 or 48 months, by median result per month): the 36-month cohort median result per month 40.00 / 36 = 1.1111 vs this serial's 24-month median -5.00 / 24 = -0.2083 (n = 12 and 15) | (1.1111 - (-0.2083)) x 24, floored at 0; counted once for the cohort in the summary | 31.67 |

These seven numbers do not add up to one figure: L01 and L02 sit on disjoint purchase
components and are additive; L03 to L07 overlap on the resale line and the grid, and each is
read on its own against its named reference.
