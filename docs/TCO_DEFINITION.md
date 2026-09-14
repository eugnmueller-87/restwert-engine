# TCO definition: what the tool measures per device, and what it does not

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

This is the deposited definition of "total cost of ownership per device" as the Restwert Engine
computes it. A number without this page is a number without a meaning. The booking rules per line
are in [LEDGER.md](LEDGER.md) section 1; this page says what is in, what is out, why, and who owns
the parts that are assumptions.

## 1. Whose TCO

The **provider's** TCO: every euro the DaaS provider spends on one serial from the purchase order
to the credit note of the resale. It is not the customer's TCO of running a device (software
subscriptions, helpdesk for users, productivity loss): those stay with the customer or are what the
customer buys as a monthly rent. The two are different cash boxes and are never mixed.

## 2. The formula

```
tco_eur                = purchase_price + cost_to_sale
cost_to_sale           = freight + duty + staging + outbound_shipping + repair + replacement_logistics
                         + return_logistics + wipe_grading + refurbishment + holding_cost + channel_fee
tco_transactional_eur  = tco_eur minus every line flagged is_estimate
lifecycle_result_eur   = rental_revenue + resale_gross + price_protection_credit - tco_eur
```

Every term is a sum of `silver.ledger_lines` rows for that serial (`amount_eur` is negative for cost
lines, so the code sums with sign; this page states the intuitive form). Amounts are net of VAT.
A line exists only when a source row exists; nothing is a rate applied to a total when a
transaction exists.

## 3. What is in, line by line

| Line | Cost block | Source system and table | Booked when | Estimate? | Owner of the assumption |
|---|---|---|---|---|---|
| purchase_price | acquisition | ERP supplier invoice, unit line for the serial | invoice date <= as_of | until the unit invoice exists: PO unit price, flagged | Head of Procurement (name) |
| freight | acquisition | ERP supplier invoice, freight line of the PO line, split to the cent over received serials | invoice date | no | n/a |
| duty | acquisition | ERP supplier invoice, duty line of the PO line, same split | invoice date | no | n/a |
| staging | deployment | WMS staging log, cost per serial | staged_at | no | n/a |
| outbound_shipping | deployment | WMS shipments, direction outbound | shipped_at | no | n/a |
| repair | service | service desk ticket with resolution repair and repair cost | closed_at | no | n/a |
| replacement_logistics | service | WMS shipments, direction replacement_out, booked on the damaged device | shipped_at | no | n/a |
| return_logistics | return | WMS shipments, direction return | shipped_at | no | n/a |
| wipe_grading | return | returns receipt, wipe and grading cost | returned_at | no | n/a |
| refurbishment | recommerce | refurbishment work order, cost | finished_at | no | n/a |
| holding_cost | capital and storage | days in stock per phase (inbound, return, sale) x holding_cost_per_day_eur | phase end, capped at as_of | **yes, always** | CFO (name), `assumptions.holding_cost_per_day_eur` |
| channel_fee | recommerce | credit note of the resale order (fee percent plus fixed) | credited_at | until the credit note exists: assumed fee, flagged | Head of Recommerce (name), `assumptions.channel_fees` |

Recycling of a scrapped device is booked through `wipe_grading` and `refurbishment` when the
partner invoices it; there is no separate recycling line in v0.2.

## 4. What is out, and why

| Cost | Why it is not in the provider's TCO |
|---|---|
| Software subscriptions on the device (productivity suite, ERP access, security software) | the customer's licences, not the provider's cost; the rent does not cover them |
| User productivity loss, training | the customer's cost |
| Rental revenue, resale proceeds, price protection credits | revenue, on the other side of the result formula |
| Write-downs | a management view in `main.write_down_ledger`, never a ledger line (a write-down is not cash) |
| Cost of capital beyond the holding cost | not in v0.2; `holding_cost_per_day_eur` is a storage-plus-capital placeholder, one rate, owned by the CFO |

## 5. Known gaps: provider costs that exist but are not measured yet

Two provider cost blocks are real, per device, and missing, because they are team costs, not
transactions per serial. They can only enter as an allocation (EUR per device-month), flagged as
estimate, with an owner:

| Missing block | What it would cover | How it would enter | Owner |
|---|---|---|---|
| support | first-level helpdesk, incident handling, replacement coordination | EUR per device-month x months rented, `is_estimate = true` | Head of Service Operations (name) |
| mdm_operations | MDM enrolment, policy operations, managed service | EUR per device-month x months rented, only for devices under a managed MDM service | Head of Service Operations (name) |

Until they are added, the result per device is overstated by exactly these two blocks. The
dashboard TCO page says so. Adding them is a config change plus one line builder each, not a
model change.

## 6. Where the definition is enforced

* `restwert/ledger/lines.py` `LINE_TYPES` is the vocabulary; a line type outside it cannot be booked.
* `tests/test_ledger.py` checks the result identity per serial and the reconciliation to v0.1.
* `KPI_TCO_ESTIMATE_SHARE` (gold) reports the estimated share of TCO so that a TCO built mostly
  from assumptions is visible as such.
* This page is hand-written and versioned with the code; when a line type changes, this page
  changes in the same commit.
