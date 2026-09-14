# Contracts register v2

> THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Hand-written companion of `restwert/contracts/counterparties.py` and
`restwert/contracts/register_v2.py` (SPEC_v0.2 section 8). It answers the
question of the "7 Contracts" page: which contracts cover our spend, and which
need action?

Honesty note, first: every term below is a **synthetic placeholder**. The
counterparty list itself is public: the ten manufacturers are spelled exactly
as `data/catalogue/models.csv` spells them, every other party is named by its
role only. Every register row carries this sentence in `terms_note`:

> synthetic placeholder terms; the counterparty list is public (manufacturers from the catalogue, every other party role-only); no term is from any provider

## 1. Counterparties (the allowlist)

`counterparties.COUNTERPARTIES` is the whole list; `counterparties.is_allowed_name(name)`
is true only for an exact catalogue manufacturer or a name ending in
`" (role-only)"`. The honesty tests apply it to every supplier and counterparty
name in every landing file and every bronze table.

| counterparty_name | role | category | placeholder terms |
|---|---|---|---|
| Apple, Samsung, Google, Motorola, Fairphone, HMD Global (Nokia), Lenovo, Dell, HP, Microsoft | manufacturer | hardware | price protection on Apple (30/14), Samsung (60/30), Google (45/30), Lenovo (60/30), Dell (45/14), HP (45/30) as (price_protection_days / claim_window_days), none on the other four; warranty 12, 24 or 36 months; rebate tiers on Samsung, Lenovo, Dell, HP: 0 % from 0 EUR, 1 % from 250,000 EUR, 2 % from 750,000 EUR of 12-month spend; SLA delivery lead 14 days, DOA replacement 10 days; payment 30, 45 or 60 days |
| Rugged-device OEM (role-only) | rugged_oem | hardware | warranty 36 months; no purchase orders in the fleet (no catalogue row) |
| IT reseller A (role-only), IT reseller B (role-only) | reseller | hardware | `covers_oems` = every manufacturer bought through the reseller; price protection 30/14 on A, none on B; payment 30 days |
| Carrier partner (role-only) | carrier | connectivity | SLA activation 2 days, uptime 99.5 % |
| Refurbishment and repair partner (role-only) | refurb_repair | refurbishment (one row) and repair (one row) | SLA turnaround 7 days, first-time fix 90 % |
| Logistics partner (role-only) | logistics | logistics | SLA pickup within 2 days, delivery 3 days |
| Marketplace channel A (role-only), Marketplace channel B (role-only) | marketplace | resale_channel | fee 12 % plus 2.50 EUR, payout 28 days |
| Financing partner A (role-only), Financing partner B (role-only) | financing | financing | funding 5 days |
| Mobile threat defense partner (role-only) | mtd | security_software | 1.20 EUR per device and month |

The generator (`restwert/lakegen/contracts.py`) draws `contracts/register`
landing rows from this list: dates, notice days, auto renewal and planned spend
are drawn there; the terms above are copied as they stand.

## 2. Price protection window vs claim window (decision D16)

Two different clocks, kept apart on purpose:

- `price_protection_days`: how long after **receipt** a price drop by the
  counterparty still counts for the device. A drop with `valid_from` inside
  `(received_at, received_at + price_protection_days]` is claimable.
- `claim_window_days`: how long after the **drop** the credit can still be
  claimed. The window is open while `valid_from + claim_window_days >= as_of`.

v0.1 knew only one number, `supplier_contracts.price_protection_days`, and used
it as the claim window (rule R05 reminds when it closes). The conform step
therefore sets the v0.1 column from the v2 **claim window**:
`main.supplier_contracts.price_protection_days = bronze.ctr_register.claim_window_days`.
R05 and R06 keep running on the conformed table exactly as before; the register
v2 carries both clocks.

## 3. `silver.contracts` (register v2)

One row per `bronze.ctr_register` row, plus the derived columns. Written by
`register_v2.run_contracts_v2`, which calls the v0.1 `run_contracts` first so
that `contracts_register` and `renewal_calendar` stay populated.

| column | meaning |
|---|---|
| `contract_id`, `counterparty_name`, `counterparty_role`, `counterparty_is_public`, `category` | as landed; `counterparty_is_public` is true for the ten manufacturers only |
| `start_date`, `end_date`, `notice_days`, `notice_deadline` | `notice_deadline = end_date - notice_days` |
| `auto_renewal`, `price_protection`, `price_protection_days`, `claim_window_days`, `warranty_months`, `rebate_tiers_json`, `volume_commitment_units`, `payment_terms_days`, `sla_json` | placeholder terms as landed |
| `spend_under_contract_eur` | planned yearly spend as landed (synthetic: 12 x mean monthly PO value) |
| `spend_actual_12m_eur` | actual spend in the trailing 12 months (`add_months(as_of, -12) + 1 day` to `as_of`, inclusive), per role, see below; NULL when no source names the counterparty, never 0 |
| `spend_actual_vs_planned_pct` | `spend_actual_12m_eur / spend_under_contract_eur`, NULL when planned is 0 or actual is NULL |
| `covers_oems` | comma list of the manufacturers of the slugs bought on the contract (all time); a reseller contract shows which manufacturers it actually covers |
| `n_serials_under_contract` | goods receipts whose PO header carries this `contract_ref` (all time); NULL for non-hardware roles |
| `status` | `expired` when `end_date < as_of`, `future` when `start_date > as_of`, else `active` |
| `days_to_notice_deadline`, `days_to_end` | calendar days from `as_of`; negative when past |
| `action_required` | exactly the v0.1 `renewal_calendar` verdict: the row lies inside the six-month horizon AND (notice deadline within two months OR auto renewal); rows outside the horizon are false |
| `terms_note`, `is_synthetic`, `as_of` | the sentence above; the fleet flag; the run date |

Actual spend per role (the transaction that names the counterparty):

| role | source of `spend_actual_12m_eur` |
|---|---|
| manufacturer, reseller, rugged_oem | received unit value (`unit_price_eur` of the PO line per goods receipt) on headers with `contract_ref = contract_id`, by `received_at`; a contract with receipts but none in the window shows 0, one without any receipt shows NULL |
| marketplace | credit note gross of the `marketplace` channel by `credited_at`; the credit note carries the channel, not the counterparty, so the total is split equally over the active marketplace rows (documented allocation) |
| refurb_repair | `silver.ledger_lines` magnitudes of line type `refurbishment` (the refurbishment row) or `repair` (the repair row) by `event_date` |
| logistics | ledger line types `outbound_shipping`, `return_logistics`, `replacement_logistics` |
| carrier, financing, mtd | `bronze.fin_indirect_spend` amounts whose `supplier_name` equals the counterparty; when no invoice names it, the category total (`connectivity`, `financing`, `security_software`) split equally over the register rows of that category |

## 4. `gold.contract_coverage_by_oem`

Share of hardware spend under a contract in force, per manufacturer of the
**device**, not of the supplier:

- population: goods receipts in the trailing 12 months, valued at the PO line
  `unit_price_eur`, with the manufacturer taken from `bronze.cat_models` by slug;
- `spend_under_contract`: value whose PO header `contract_ref` names a register
  row with `start_date <= order_date <= end_date`. A reseller PO of Apple
  devices is Apple spend and is covered when the reseller contract was in force
  at the order date;
- `spend_direct` / `spend_via_reseller`: the split by `supplier_role`;
- `n_contracts_in_force`: the manufacturer's own contract plus every contract
  its purchase orders referenced, counted when in force at `as_of`;
  `next_notice_deadline` is the earliest notice deadline still ahead among them;
- `coverage_pct = spend_under_contract / spend_total`, NULL when the
  manufacturer had no receipts in the window (the row stays so the page shows
  every manufacturer with a contract).

`KPI_CTR_COVERAGE_BY_OEM` on the "7 Contracts" page is the sum over this table.

## 5. `gold.renewal_calendar_v2`

The v0.1 `renewal_calendar` logic applied to `silver.contracts` (supplier rows
only; rental contracts stay in the v0.1 calendar), so `action_required` is the
same verdict in both calendars (a test pins it). Added columns: `counterparty_role`,
`category`, `spend_under_contract_eur`, `spend_actual_12m_eur`,
`price_protection_days`, `claim_window_days` and

- `price_protection_window_open`: true when any PO line on the contract saw a
  price change inside `(received, received + price_protection_days]` (received
  = the last goods receipt of the line) whose claim window is still open at
  `as_of`. The register row's two clocks are the authority; a PO line's own
  `price_protection_days` only fills a gap.

Horizon: end date or notice deadline within six months of `as_of`, both bounds
inclusive, as in v0.1.

## 6. `gold.rebate_progress`

For every register row with `rebate_tiers_json`: `spend_12m_eur` (received unit
value on the contract in the trailing 12 months), `current_tier_pct` (the
highest tier whose `from_eur` is at or below the spend), `next_tier_from_eur`,
`next_tier_pct` and `gap_to_next_tier_eur = next_tier_from_eur - spend_12m_eur`.
A contract already in its top tier has NULL next tier and NULL gap. Without
purchase data the spend falls back to `spend_actual_12m_eur`, and stays NULL
(every tier column NULL) when neither exists.

## 7. What acts on this

- R05 (price protection reminder and minimum claim) and R06 (renewal alert and
  high-value escalation) read the conformed `main.supplier_contracts` and
  `main.purchase_orders`, unchanged from v0.1; their thresholds and owners are
  in `config/thresholds.yaml`.
- The "7 Contracts" page shows `silver.contracts` by role under the terms
  banner, the calendar v2, the coverage bars, the rebate progress and the R05
  and R06 queue rows. Nothing here sends a notice, claims a credit or renews
  anything: a named human does.
