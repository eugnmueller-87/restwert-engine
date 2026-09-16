# Restwert Engine v0.2: Implementation Spec (the closed device cycle on a layered data lake)

> THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Status: frozen for the six parallel build agents. v0.1 (`docs/SPEC.md`) stays frozen and
keeps running unchanged underneath; v0.2 is additive. Every path is relative to the repository
root `F:/01_PROJECTS/Procurement und SCM/Restwert-Engine`. UTF-8 without BOM, LF, forward
slashes, no em dash (U+2014) anywhere, no real DaaS provider or partner company name anywhere;
manufacturer names from `data/catalogue/models.csv` are allowed. Nothing orders, lists, emails
or writes to a partner. Synthetic data is labelled synthetic on every file and every row.

Owner's intent, in one line per page: where do we buy each device and at what price (1),
what does it cost us until it is sold (2), what do we expect back at lease end (3), what did we
get (4), profit or loss per serial and cohort (5), which screw to tighten (6), and every
contract with Apple, Dell and all the others (7). All of it on a data lake whose feeds are
defined (0). Mock data simulates the whole cycle.

---

## 0. Resolved decisions (conflicts between the three drafts, settled)

| # | Question | Decision |
|---|---|---|
| D1 | DuckDB naming | **Schemas** `bronze`, `silver`, `gold` in the one file `data/restwert.duckdb`. v0.1 tables stay unqualified in `main`. No fourth schema: the raw layer is files on disk, registered in `bronze.deliveries`. Bronze table names carry a source prefix (`erp_`, `wms_`, `portal_`, `sd_`, `ret_`, `rf_`, `rc_`, `ctr_`, `fin_`, `cat_`, `mkt_`) so no bronze name collides with a `main` name. |
| D2 | v0.1 compatibility | The ten v0.1 source tables are **materialised in `main` by a deterministic conform step from bronze** (same DDL, `write_df(mode="replace")`), so forecast, pnl, decide, kpis, contracts, export and all 256 tests run unchanged. `device_pnl` stays a v0.1 table written by `run_pnl`. The ledger is a second computation and `silver.reconciliation` proves the two agree to the cent. |
| D3 | Landing files | `data/lake/raw/<system>/<feed>/<YYYY-MM-DD>_<feed>_<seq:03d>.csv`, one file per delivery, quarterly cadence by default, first line `# SYNTHETIC DATA ...` (generated) or `# PUBLIC DATA ...` (catalogue copies), every row with `is_synthetic`. Never modified after landing. |
| D4 | Ledger sign | `silver.ledger_lines.amount_eur` is **signed**: revenue positive, cost negative. Result = `SUM(amount_eur)`. Wide `silver.device_ledger` stores positive magnitudes per component for readability. |
| D5 | VAT | Catalogue RRP is gross; every ledger amount is **net**. `rrp_net_eur = round(rrp_eur_launch_de / (1 + vat_rate), 2)` with `vat_rate` a new assumption block (0.19, owner "CFO (name)"). Discounts are net vs net. |
| D6 | Line vocabulary | 15 line types (section 6.1), 17 since v0.3 (`support` and `mdm_operations`, allocations per billed rental month, docs/TCO_DEFINITION.md section 5). `wipe` and `grading` are one line `wipe_grading`. `holding_cost`, `support` and `mdm_operations` are the estimate lines by construction (`is_estimate = true`). Write-downs stay a `main` management view and are never a ledger line. |
| D7 | Freight and duty | Cent-exact allocation per received unit of the PO line: floor to cents, remainder on the last serial in serial order (`allocate_cents`). The only allocation in the ledger, flagged `allocation_basis = per_unit_of_po_line`. |
| D8 | Rent lines | One line per portal rental invoice (transaction), not a computed rate. The generator dates invoice k on `billing_date(start, k)` (section 3.4) so that the count of invoices dated `<= as_of` equals v0.1 `months_billed` exactly. On real data a difference is a reconciliation finding, not an error. |
| D9 | Forecast segments | `model_family` (the forecaster's stratum) keeps `iphone_like`, `android_like`, `laptop_like` and gains **`tablet_like`**. Mapping `fleet_family(catalogue_family, oem)`: Smartphone+Apple -> iphone_like, Smartphone+other -> android_like, Tablet -> tablet_like, Laptop -> laptop_like. Catalogue family and manufacturer travel as separate columns. |
| D10 | Launch steps | **No forecaster change.** `n_launches_since` keeps counting catalogue launches of the segment; on the real catalogue that is "segment launch intensity" (about 12 per year for android_like). `config/lake.yaml` sets the calendar cadence per segment to the catalogue's observed rate so past and projected counts are on the same scale (section 3.5). Series-scoped launch steps are a v0.3 item, stated in README limitations. |
| D11 | Generator | New package `restwert/lakegen/` draws the fleet from the real catalogue, prices with a truth curve calibrated to `outputs/market_curves.csv`, **reuses the v0.1 builders** `build_rental_contracts`, `build_events`, `build_refurbishment` on a v0.1-shaped device frame, re-implements resale with the calibrated truth, and renders everything into landing files. `restwert/generate/` and `config/generator.yaml` are untouched (their tests pin them). |
| D12 | Truth calibration | `q_public(age, grade) = exp(intercept + slope x age + offset[grade])` from the `family_oem` marketplace row when `fit_quality == ok`, else the `family` marketplace row; capped at `q_young_cap` 0.90; times `ask_to_realised` 0.85; times channel multiplier; log-normal noise 0.10; clipped to [0.02, 0.95]. Grade D offset default -0.60. All design parameters with owners, listed in `data/lake/SYNTHETIC.md`. No successor step in the truth (the public curve already averages it). |
| D13 | Open cycles | Two stored columns, two tiles, never summed: `result_if_liquidated_today` and `result_projected_at_lease_end` (section 6.4). No column named `result_total` anywhere (test). |
| D14 | Levers | Seven levers (section 7). Each = actual minus a **named reference** on one ledger component, `basis` in `fleet`, `forecast_of_record`, `grid`; `additive` flag; `eur >= 0` means money left on the table (floored at 0 per device where stated). Levers do not add up and the page says so. One new rule R07 (purchase discount floor), two new advisories ADV03 (manufacturer mix), ADV04 (term gap). |
| D15 | KPIs | The v0.1 registry stays at exactly 20 KPIs and five areas (tests pin it). v0.2 KPIs live in a **separate gold registry** (`restwert/gold/`) writing `gold.kpi_values` and `gold.kpi_breakdown`, documented in `docs/GOLD_KPIS.md`. |
| D16 | Contracts | `bronze.ctr_register` (feed `contracts/register`) -> `silver.contracts` (register v2) -> conformed `main.supplier_contracts` (v0.1 shape) so R05, R06, `contracts_register`, `renewal_calendar` and `KPI_PROC_SPEND_UNDER_CONTRACT` keep working. v0.1 `supplier_contracts.price_protection_days` maps to v2 `claim_window_days` (the v0.1 meaning: days after the drop to file). |
| D17 | Counterparties | Manufacturers with their catalogue names (exact `oem` strings: Apple, Dell, Fairphone, Google, HMD Global (Nokia), HP, Lenovo, Microsoft, Motorola, Samsung) plus role-only names ending in `(role-only)`. Allowlist test on every counterparty and supplier name in bronze and in landing files. |
| D18 | CLI | `ALL_ORDER` (v0.1 tuple) is untouched (a test pins it). The new chain is `ALL_ORDER_V2`; `all` runs it by default, `all --v01` runs the legacy chain. `--csv-dir` keeps its meaning: the conform step writes the ten conformed tables as `<csv_dir>/<table>.csv` so `load --csv-dir` and the v0.1 path keep working. |
| D19 | Dashboard | `st.navigation` with groups "Cycle" and "Engine"; pages are functions. The two v0.1 headline tiles move into the sidebar so they render on every page (the headless test keeps passing untouched). |
| D20 | Timing | `all --small` (500 serials) stays under 25 s (existing test; budget was 20 s until 2026-09-16, when the run measured 20.0 to 20.6 s on the development machine); `all` default (5000 serials) under 60 s (new test), target 40 s. v0.1 measured 5.6 s on 4000 devices, so the budget is real. |

---

## 1. Repository layout v0.2

New files (N) and modified v0.1 files (M, with the only edits allowed):

```
config/
  lake.yaml                              N  module 2 (superset of generator.yaml, see 3.5)
  assumptions.yaml                       M  add tablet_like to every per-family values map; add block vat_rate; add block lake_defaults (3.6)
  thresholds.yaml                        M  add tablet_like to every per-family values map; add purchase_discount_floor_pct, oem_realisation_gap_pct, term_result_gap_alert_eur (3.6)
data/
  lake/raw/<system>/<feed>/*.csv         generated landing files (never hand-edited, never modified after landing)
  lake/SYNTHETIC.md                      generated by generate-lake
  lake/{bronze,silver,gold}/*.parquet    written by export --lake (mirrors, not sources of truth)
  SYNTHETIC.md                           rewritten by all (same text as lake/SYNTHETIC.md)
  raw_csv/<table>.csv                    rewritten by conform (compat copies of the ten conformed tables)
docs/
  SPEC_v0.2.md                           this file
  DATA_LAKE.md                           N  rendered by restwert.lake.feeds.write_data_lake_md (module 1)
  LEDGER.md                              N  module 3
  LEVERS.md                              N  module 4
  CONTRACTS.md                           N  module 5
  GOLD_KPIS.md                           N  rendered by restwert.gold.catalogue (module 5)
  DATA_MODEL.md                          M  one appended paragraph linking to DATA_LAKE.md (via schema.SCHEMA_LAYER_NOTE)
restwert/
  __init__.py                            M  __version__ = "0.2.0"
  paths.py                               M  add LAKE_DIR, LAKE_RAW_DIR, LAKE_BRONZE_DIR, LAKE_SILVER_DIR, LAKE_GOLD_DIR, CATALOGUE_DIR, LAKE_CONFIG (3.1)
  enums.py                               M  add Family.TABLET_LIKE (+ FAMILIES), LineType, LineClass, CounterpartyRole, SupplierRole, TimelineStep, SpendCategory additions (3.2)
  db.py                                  M  schema-qualified names in table_exists, _table_columns, write_df, append_rows (3.3)
  records.py                             M  Advisory.kind Literal gains "manufacturer_mix", "term_gap"
  schema.py                              M  add SCHEMA_LAYER_NOTE constant and one line in render_data_model_md (3.7)
  cli.py                                 M  module 6 (section 10)
  export.py                              M  module 6: add LAKE_EXPORT_TABLES, export_lake, manifest key lake_files
  decisions/rules.py                     M  module 4: add decide_purchase_floor (R07), NO_ACTION_OUTCOMES["R07"], outcome names
  decisions/registry.py                  M  module 4: RULES["R07"], ADVISORY_SPECS["ADV03"], ["ADV04"]
  decisions/runner.py                    M  module 4: _r07, run_all_decisions loop gains ("R07", ...), calibration_advisory_rows generalised to _ADVISORY_QUEUE kinds (7.6)
  dashboard/app.py                       M  module 6 (rewritten around st.navigation, section 11)
  dashboard/views/__init__.py            M  module 6: TABS kept, CYCLE_PAGES and ENGINE_PAGES added
  dashboard/data.py                      M  module 6: new cached readers (additive)
  dashboard/charts.py                    M  module 6: FAMILY_COLOURS gains tablet_like "#d62728"; new figures (additive)
  lake/                                  N  module 1: __init__.py, common.py, schema_lake.py, feeds.py, ingest.py, conform.py, timeline.py
  lakegen/                               N  module 2: __init__.py, config.py, catalogue.py, calibrate.py, fleet.py, purchase.py, operations.py, resale.py, contracts.py, defects.py, writer.py
  ledger/                                N  module 3: __init__.py, lines.py, device_ledger.py, result.py, reconcile.py, cohorts.py, run.py
  levers/                                N  module 4: __init__.py, references.py, attribution.py, summary.py, run.py
  contracts/counterparties.py            N  module 5
  contracts/register_v2.py               N  module 5
  gold/                                  N  module 5: __init__.py, registry.py, kpis.py, catalogue.py, run.py
  dashboard/views/cycle/                 N  module 6: __init__.py, _common.py, data_page.py, purchase.py, tco.py, residual.py, resale.py, result.py, levers.py, contracts_v2.py
tests/
  fixtures/allowlist.py                  N  module 6
  test_lake.py                           N  module 1
  test_lakegen.py                        N  module 2
  test_ledger.py                         N  module 3
  test_levers.py                         N  module 4
  test_contracts_v2.py, test_gold.py     N  module 5
  test_cli_v2.py, test_dashboard_v2.py, test_honesty_v2.py   N  module 6
  conftest.py                            M  module 6: add session fixture lake_pipeline_paths / lake_pipeline_db (10.5); the v0.1 fixtures stay
README.md                                M  module 6: restructured along the cycle (section 12)
pyproject.toml                           M  module 6: version = "0.2.0"
```

Shared foundation rule: sections 3.1 to 3.4 print the shared files **verbatim**. Module 1 owns
them. Any other module that starts before module 1 has landed writes them byte-identical from
this spec (a later write by module 1 is then a no-op). Nobody edits them beyond what is printed
here. Cross-module data flow is DuckDB tables only; no module imports another v0.2 module's
Python except the shared foundation (`restwert.lake.common`, `restwert.lake.schema_lake`) and
v0.1 modules.

---

## 2. Layers, keys and the closed cycle (the model everybody builds against)

```
landing files (raw, on disk)  --ingest-->  bronze.*  (typed, deduplicated, source-shaped)
bronze.*  --conform-->  main.<ten v0.1 source tables>  (compat)  --v0.1 chain-->  forecast, device_pnl, decisions, kpis, contracts
bronze.*  --ledger-->   silver.ledger_lines, silver.serial_timeline, silver.device_ledger, silver.reconciliation
silver.*  --levers/contracts/gold-->  gold.*  (cohorts, levers, coverage, KPIs)
```

Keys:
* **serial** is minted by `erp/goods_receipts` (the provider first sees the physical device at
  receipt). Every later serial-level feed resolves against `bronze.erp_goods_receipts.serial`;
  a serial not there is `unknown_serial` in `bronze.unresolved`, never auto-created.
* **(source_system, external_ref)** is the business key of every bronze row (`FeedSpec.business_key`).
  `silver.ledger_lines.source_ref = "<system>:<external_ref>"` so any EUR line traces to one
  bronze row, and that bronze row to one landing file line (`source_file`, `row_number`).
* Reference feeds (`catalogue/*`, `market/curves`) are public data copied into the lake with
  `is_synthetic = false` and their URLs; the fleet that references them is synthetic.

Cycle per serial: ordered -> received -> staged -> shipped (rented) -> [tickets, replacements]
-> returned -> wiped -> graded -> [refurbished] sellable -> sold -> credited. Each stage is a
timestamp in `silver.serial_timeline`; each EUR is a ledger line; the cycle closes when the
device is sold or scrapped, and `lifecycle_result_eur = SUM(amount_eur)`.

---

## 3. Shared foundation (verbatim)

### 3.1 `restwert/paths.py` (additive lines)

```python
LAKE_DIR: Path = DATA_DIR / "lake"
LAKE_RAW_DIR: Path = LAKE_DIR / "raw"
LAKE_BRONZE_DIR: Path = LAKE_DIR / "bronze"
LAKE_SILVER_DIR: Path = LAKE_DIR / "silver"
LAKE_GOLD_DIR: Path = LAKE_DIR / "gold"
CATALOGUE_DIR: Path = DATA_DIR / "catalogue"
MARKET_CURVES_CSV: Path = OUTPUTS_DIR / "market_curves.csv"
LAKE_CONFIG: Path = CONFIG_DIR / "lake.yaml"
```
(and the names appended to `__all__`).

### 3.2 `restwert/enums.py` (additive)

```python
class Family(StrEnum):
    IPHONE_LIKE = "iphone_like"
    ANDROID_LIKE = "android_like"
    LAPTOP_LIKE = "laptop_like"
    TABLET_LIKE = "tablet_like"          # v0.2

FAMILIES: tuple[str, ...] = ("iphone_like", "android_like", "laptop_like", "tablet_like")

class SpendCategory(StrEnum):            # v0.1 values unchanged, v0.2 additions:
    CONNECTIVITY = "connectivity"
    FINANCING = "financing"
    RESALE_CHANNEL = "resale_channel"
    SECURITY_SOFTWARE = "security_software"

class LineType(StrEnum):
    PURCHASE_PRICE = "purchase_price"; FREIGHT = "freight"; DUTY = "duty"; STAGING = "staging"
    OUTBOUND_SHIPPING = "outbound_shipping"; RENTAL_REVENUE = "rental_revenue"; REPAIR = "repair"
    REPLACEMENT_LOGISTICS = "replacement_logistics"; RETURN_LOGISTICS = "return_logistics"
    WIPE_GRADING = "wipe_grading"; REFURBISHMENT = "refurbishment"; HOLDING_COST = "holding_cost"
    RESALE_GROSS = "resale_gross"; CHANNEL_FEE = "channel_fee"; PRICE_PROTECTION_CREDIT = "price_protection_credit"

class LineClass(StrEnum):
    REVENUE = "revenue"; COST = "cost"

class SupplierRole(StrEnum):
    MANUFACTURER = "manufacturer"; RESELLER = "reseller"

class CounterpartyRole(StrEnum):
    MANUFACTURER = "manufacturer"; RUGGED_OEM = "rugged_oem"; RESELLER = "reseller"; CARRIER = "carrier"
    REFURB_REPAIR = "refurb_repair"; LOGISTICS = "logistics"; MARKETPLACE = "marketplace"
    FINANCING = "financing"; MTD = "mtd"

class TimelineStep(StrEnum):
    ORDERED = "ordered_at"; RECEIVED = "received_at"; STAGED = "staged_at"; SHIPPED = "shipped_at"
    RETURNED = "returned_at"; WIPED = "wiped_at"; GRADED = "graded_at"; SELLABLE = "sellable_at"
    SOLD = "sold_at"; CREDITED = "credited_at"

LINE_TYPES: tuple[str, ...] = tuple(m.value for m in LineType)
TIMELINE_STEPS: tuple[str, ...] = tuple(m.value for m in TimelineStep)
```
`Family` gaining a value does not break v0.1 tests (`test_load_generator_config_shipped`
asserts the yaml families, which are untouched). Every new name goes into `__all__`.

### 3.3 `restwert/db.py` (edit exactly these four functions; everything else untouched)

```python
def _q(table: str) -> str:
    """'bronze.x' -> '"bronze"."x"', 'x' -> '"x"'."""
    if "." in table:
        schema_name, name = table.split(".", 1)
        return f'"{schema_name}"."{name}"'
    return f'"{table}"'


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    if "." in table:
        schema_name, name = table.split(".", 1)
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema_name, name],
        ).fetchone()
    else:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
            [table],
        ).fetchone()
    return bool(row and row[0] > 0)


def _table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE {_q(table)}").fetchall()
    return [(r[0], r[1]) for r in rows]
```
In `write_df`: replace `f'DELETE FROM "{table}"'` by `f"DELETE FROM {_q(table)}"` and
`f'INSERT INTO "{table}" SELECT ...'` by `f"INSERT INTO {_q(table)} SELECT ..."`; the immutable
check `table in schema.IMMUTABLE_TABLES` stays on the bare name. `create_schema`, `load_csv_dir`,
`is_synthetic`, `new_run`, `finish_run` are untouched. A bare name behaves exactly as in v0.1
(the `table_schema = 'main'` filter is the only behavioural change and it removes a latent
ambiguity). Test: `tests/test_lake.py::test_db_accepts_schema_qualified_names`.

### 3.4 `restwert/lake/common.py` (pure helpers used by modules 1, 2, 3, 4, 5)

```python
"""Shared pure helpers of the v0.2 lake. No I/O, no DuckDB."""
from __future__ import annotations
import hashlib, math
from datetime import date, datetime
from restwert.dates import add_months, months_between

FLEET_FAMILIES: tuple[str, ...] = ("iphone_like", "android_like", "tablet_like", "laptop_like")
CATALOGUE_FAMILIES: tuple[str, ...] = ("Smartphone", "Tablet", "Laptop")
OEM_CODES: dict[str, str] = {
    "Apple": "APL", "Samsung": "SAM", "Google": "GOO", "Motorola": "MOT", "Fairphone": "FPH",
    "HMD Global (Nokia)": "HMD", "Lenovo": "LEN", "Dell": "DEL", "HP": "HPI", "Microsoft": "MSF",
}
MANUFACTURERS: tuple[str, ...] = tuple(sorted(OEM_CODES))   # exact oem strings of data/catalogue/models.csv
ROLE_ONLY_SUFFIX = " (role-only)"

def fleet_family(catalogue_family: str, oem: str) -> str:
    """Smartphone+Apple -> iphone_like; Smartphone+other -> android_like; Tablet -> tablet_like; Laptop -> laptop_like."""
    if catalogue_family == "Smartphone":
        return "iphone_like" if oem == "Apple" else "android_like"
    if catalogue_family == "Tablet":
        return "tablet_like"
    if catalogue_family == "Laptop":
        return "laptop_like"
    raise ValueError(f"unknown catalogue family {catalogue_family!r}")

def rrp_net(rrp_gross: float, vat_rate: float) -> float:
    return round(float(rrp_gross) / (1.0 + float(vat_rate)), 2)

def allocate_cents(total: float, n: int) -> list[float]:
    """Split ``total`` EUR over ``n`` units to the cent: floor per unit, remainder on the LAST unit; sums exactly."""
    if n <= 0:
        return []
    cents = int(round(total * 100))
    base, rem = divmod(cents, n)
    out = [base] * n
    out[-1] += rem
    return [c / 100.0 for c in out]

def billing_date(start: date, k: int) -> date:
    """First date d with months_between(start, d) == k (k >= 1): add_months(start, k), plus one day when the day was clamped."""
    d = add_months(start, k)
    if d.day < start.day:
        d = date.fromordinal(d.toordinal() + 1)
    assert months_between(start, d) == k
    return d

def line_id(serial: str, line_type: str, source_system: str, source_ref: str, event_date: date) -> str:
    return hashlib.sha1(f"{serial}|{line_type}|{source_system}|{source_ref}|{event_date.isoformat()}".encode("utf-8")).hexdigest()[:24]

def row_hash(values: list) -> str:
    """sha1 over the content columns of one landing row (strings, '' for missing)."""
    return hashlib.sha1("\x1f".join("" if v is None else str(v) for v in values).encode("utf-8")).hexdigest()

def delivery_id(sha256_hex: str) -> str:
    return sha256_hex[:16]

def to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    return date.fromisoformat(s[:10])
```

### 3.5 `config/lake.yaml` (module 2 writes it; the shape is fixed here)

`LakeConfig(GeneratorConfig)` (pydantic, `extra="ignore"`): every field of v0.1
`GeneratorConfig` is present (the v0.1 builders are reused), plus the v0.2 blocks. Header
comment: "EVERY value is a synthetic DESIGN PARAMETER; the truth block of each family is unused
by the lake generator (kept for GeneratorConfig validation); the lake truth is the calibrated
block `truth_v2`." Contents:

```yaml
version: 2
seed: 42
n_devices: 5000               # serials (the CLI flag --devices / --serials)
history_start: 2022-01-01
purchase_end: 2026-03-31
as_of: 2026-09-13
delivery_cadence: quarterly   # yearly | quarterly | monthly
families:                     # four blocks in the v0.1 FamilyConfig shape; share_of_fleet is unused (fleet_mix rules)
  iphone_like:  {first_launch: 2020-10-15, launch_cadence_months: 4,  launch_month: null, list_price_min: 799, list_price_max: 1299, storage_options: [128,256,512], base_storage_gb: 128, discount_min: 0.06, discount_max: 0.12, freight_duty_pct: 0.0, share_of_fleet: 0.33, term_mix: {12: 0.10, 24: 0.50, 36: 0.35, 48: 0.05}, monthly_rate_pct_of_landed: 0.042, damage_rate_pa: 0.10, repair_share: 0.70, repair_cost_min: 60, repair_cost_max: 320, truth: {base: 0.92, lambda: 0.028, lambda_after_24: 0.017, step: 0.88, noise: 0.10, storage_exp: 0.08, grade_A: 1.0, grade_B: 0.88, grade_C: 0.72, grade_D: 0.45}}
  android_like: {first_launch: 2020-04-30, launch_cadence_months: 1,  launch_month: null, ... damage_rate_pa: 0.11, repair_share: 0.65, repair_cost_min: 40, repair_cost_max: 220, term_mix: {12: 0.10, 24: 0.55, 36: 0.30, 48: 0.05}, monthly_rate_pct_of_landed: 0.045, share_of_fleet: 0.27, ...}
  tablet_like:  {first_launch: 2020-09-15, launch_cadence_months: 2,  launch_month: null, ... damage_rate_pa: 0.07, repair_share: 0.60, repair_cost_min: 50, repair_cost_max: 260, term_mix: {12: 0.05, 24: 0.40, 36: 0.45, 48: 0.10}, monthly_rate_pct_of_landed: 0.040, share_of_fleet: 0.15, ...}
  laptop_like:  {first_launch: 2020-05-15, launch_cadence_months: 2,  launch_month: null, ... damage_rate_pa: 0.08, repair_share: 0.75, repair_cost_min: 90, repair_cost_max: 450, term_mix: {12: 0.05, 24: 0.25, 36: 0.50, 48: 0.20}, monthly_rate_pct_of_landed: 0.038, share_of_fleet: 0.25, ...}
# launch_cadence_months per segment = the catalogue's observed launch rate (usable slugs per year), so that the
# forecaster's calendar rule for launches after as_of is on the same scale as the observed count (decision D10)
grade_pre_return_mix, grade_drift_worse, grade_drift_better, channel_mix_by_grade, channel_mult, fee_pct, fee_fixed_eur,
days_to_sale, slow_mover_share, slow_mover_days, refurb_days, refurb_cost, scrap_share_of_d, early_termination_rate,
replacement_share_of_damage, open_damage_share, otif_on_time_share, po_short_delivery_share, price_drop_share,
price_drop_pct, suppliers_hardware, suppliers_indirect, indirect_categories, indirect_rows_per_month, indirect_has_po,
indirect_has_contract, indirect_saving_share, indirect_confirmed_share, indirect_saving_type_mix, launch_slip_months_max:
  copied from generator.yaml (fee_pct and fee_fixed_eur MUST equal assumptions.yaml channel_fees; a test asserts it);
  suppliers_indirect: ["Facilities provider (role-only)", "Marketing agency (role-only)", "Consulting firm (role-only)",
                       "Packaging supplier (role-only)", "Software vendor (role-only)", "Logistics partner (role-only)"]
  suppliers_hardware: []      # unused by the lake generator (supplier_route decides)
fleet_mix: {Smartphone: 0.60, Tablet: 0.15, Laptop: 0.25}
oem_share:
  Smartphone: {Apple: 0.50, Samsung: 0.30, Google: 0.10, Motorola: 0.06, Fairphone: 0.03, "HMD Global (Nokia)": 0.01}
  Tablet:     {Apple: 0.65, Samsung: 0.30, Microsoft: 0.05}
  Laptop:     {Lenovo: 0.35, Dell: 0.20, HP: 0.20, Apple: 0.15, Microsoft: 0.05, Samsung: 0.05}
supplier_route: {manufacturer: 0.40, reseller: 0.60}
resellers: ["IT reseller A (role-only)", "IT reseller B (role-only)"]
discount_by_oem:            # [min, max] share below net RRP on the manufacturer route (design parameters)
  Apple: [0.05, 0.12]; Samsung: [0.12, 0.25]; Google: [0.10, 0.22]; Motorola: [0.15, 0.30]; Fairphone: [0.03, 0.08]
  "HMD Global (Nokia)": [0.15, 0.25]; Lenovo: [0.15, 0.30]; Dell: [0.15, 0.28]; HP: [0.15, 0.28]; Microsoft: [0.08, 0.15]
reseller_markup_pct: [0.00, 0.03]   # a reseller route pays this much less discount
newest_model_share: 0.70            # PLACEHOLDER (owner Category Manager Hardware): p(a slug of the CURRENT generation of the oem/family at the order date)
previous_generation_share: 0.20     # PLACEHOLDER (owner Category Manager Hardware): p(previous generation); newest + previous <= 1, the rest is the older pool
generation_window_months: 12        # PLACEHOLDER (owner Category Manager Hardware): window anchored on the NEWEST launch of the oem/family on or before the order date;
                                    # slugs launched inside this many months up to it are the current generation, the window before it the previous one
freight_per_unit_eur: [4.0, 14.0]
duty_pct_reseller_b: 0.025          # only "IT reseller B (role-only)" invoices duty (non-EU origin, design)
staging_cost_eur: [6.0, 14.0]
outbound_cost_eur: [5.5, 9.5]
wipe_grading_cost_eur: [3.0, 8.0]
mdm_enrolled_share: 0.98
wipe_certificate_share: 0.97
price_protection_claim_share: 0.60  # share of claimable drops that were actually claimed (credit note exists)
truth_v2:
  q_young_cap: 0.90
  ask_to_realised: 0.85
  grade_d_offset_default: -0.60
  noise_sigma: 0.10
  ratio_min: 0.02
  ratio_max: 0.95
  default_curve: {intercept: -0.45, slope_per_month: -0.010, grade_A_offset: 0.03, grade_C_offset: -0.15}  # used only when market_curves.csv is missing
  owner: "Head of Recommerce (name)"
defects:
  unknown_serial_share: 0.003          # wms/shipments rows with a serial typo -> unresolved unknown_serial
  identical_duplicate_share: 0.005     # rows repeated in the next delivery of the same feed -> duplicates_identical
  conflicting_duplicate_share: 0.002   # portal/rental_invoices rows repeated with amount + 0.01 -> duplicate_conflict
  missing_credit_note_share: 0.02      # sold orders without a credit note -> chain incomplete (credited_at missing)
  orphan_freight_share: 0.01           # freight invoice lines referencing a PO line that does not exist -> unknown_po
```
Every per-oem map covers exactly the ten catalogue manufacturers; `LakeConfig` validates
`fleet_mix` keys against `CATALOGUE_FAMILIES`, `oem_share` values summing to 1 per family,
`supplier_route` summing to 1.

### 3.6 `config/assumptions.yaml` and `config/thresholds.yaml` (additive)

assumptions.yaml: every `values:` map keyed by family gains `tablet_like` (planned_rv_ratio 0.15,
depreciation_months 36, expected_grade_at_return B, expected_return_to_sale_days 35,
as_is_ratio_fallback 0.09, damage_rate_pa_fallback 0.07, repair_share_fallback 0.60,
repair_cost_fallback_eur 140, refurb_cost_fallback_eur 40, logistics_cost_fallback_eur 14; all
placeholders, note says "placeholder copied from the neighbouring families"). New blocks:

```yaml
  vat_rate:
    owner: "CFO (name)"
    value: 0.19
    note: "German standard VAT; the public catalogue RRP is gross, every ledger amount is net"
  lever_reference_min_n:
    owner: "CFO (name)"
    value: 10
    note: "placeholder minimum group size before a lever reference (fleet percentile or median) is used; below it the lever is not attributed"
```

thresholds.yaml (all `valid_from: 2026-01-01`, `placeholder_default: true`): every family
`values:` map gains `tablet_like` (repair_max_share_of_rv 0.38, repair_min_rv_eur 60,
replacement_max_months_since_launch 30, replacement_min_storage_gb 128). New keys:

```yaml
  purchase_discount_floor_pct:
    values: {Apple: 0.06, Samsung: 0.15, Google: 0.12, Motorola: 0.18, Fairphone: 0.04, "HMD Global (Nokia)": 0.18, Lenovo: 0.18, Dell: 0.18, HP: 0.18, Microsoft: 0.10}
    unit: ratio
    owner: "Head of Procurement (name)"
    rationale: "placeholder, no external source: a PO line whose discount vs net launch RRP is below the manufacturer's floor is queued for review"
    rule_ids: [R07]
  oem_realisation_gap_pct:
    value: 0.05
    unit: ratio
    owner: "Category Manager Hardware (name)"
    rationale: "advisory only: a manufacturer whose realised residual value share trails its catalogue family by more than this over the trailing 12 months asks the category manager to review the allocation"
    rule_ids: [ADV03]
  term_result_gap_alert_eur:
    value: 50
    unit: EUR
    owner: "CFO (name)"
    rationale: "advisory only: when the best contract term (of 12, 24, 36, 48) closes better than another term of the same segment and purchase half-year by at least this per device (lever L07: the widest scaled gap in the cohort, per-month gap times that term's months), the term policy is reviewed"
    rule_ids: [ADV04]
```

### 3.7 `restwert/schema.py`

Add `SCHEMA_LAYER_NOTE = "v0.2: the ten source tables are conformed from the bronze layer of the data lake; see docs/DATA_LAKE.md for feeds, keys and the silver ledger."` and append
`lines.append(SCHEMA_LAYER_NOTE)` plus a blank line after the conventions paragraph in
`render_data_model_md`. Nothing else changes.

### 3.8 `restwert/lake/schema_lake.py` (module 1; DDL frozen here)

```python
LAKE_SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold")
BRONZE_TAIL = ("delivery_id VARCHAR NOT NULL, source_file VARCHAR NOT NULL, row_number INTEGER NOT NULL, "
               "ingested_at TIMESTAMP NOT NULL, row_hash VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL")
BRONZE_DDL: dict[str, str]; SILVER_DDL: dict[str, str]; GOLD_DDL: dict[str, str]
LAKE_DDL: dict[str, str] = {**BRONZE_DDL, **SILVER_DDL, **GOLD_DDL}      # keyed by qualified name
LAKE_TABLE_ORDER: list[str] = list(LAKE_DDL)
def create_lake_schema(con, drop_layers: tuple[str, ...] = ("silver", "gold")) -> None
    # CREATE SCHEMA IF NOT EXISTS x3; DROP TABLE IF EXISTS for tables of drop_layers; CREATE TABLE IF NOT EXISTS for all
def qualified(name: str) -> str        # same as db._q
def render_lake_ddl_md() -> str        # used by feeds.write_data_lake_md
```

Money `DECIMAL(12,2)`, ratios `DOUBLE`, timestamps `TIMESTAMP`, dates `DATE`. Every bronze DDL
ends with `BRONZE_TAIL`. Primary keys are the business keys.

```sql
-- registry and rejects
CREATE TABLE IF NOT EXISTS bronze.deliveries (
  delivery_id VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL, source_file VARCHAR NOT NULL,
  sha256 VARCHAR NOT NULL UNIQUE, delivered_on DATE NOT NULL, ingested_at TIMESTAMP NOT NULL,
  rows_read INTEGER NOT NULL, rows_typed INTEGER NOT NULL, rows_new INTEGER NOT NULL,
  duplicates_identical INTEGER NOT NULL, duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL,
  reasons_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS bronze.unresolved (
  unresolved_id VARCHAR PRIMARY KEY, delivery_id VARCHAR NOT NULL, source_system VARCHAR NOT NULL, feed VARCHAR NOT NULL,
  source_file VARCHAR NOT NULL, row_number INTEGER NOT NULL, key_json VARCHAR NOT NULL, reason_code VARCHAR NOT NULL,
  reason_text VARCHAR NOT NULL, row_json VARCHAR NOT NULL, ingested_at TIMESTAMP NOT NULL, is_synthetic BOOLEAN NOT NULL);

-- reference feeds (public)
CREATE TABLE IF NOT EXISTS bronze.cat_models (
  slug VARCHAR PRIMARY KEY, model_name VARCHAR NOT NULL, oem VARCHAR NOT NULL, family VARCHAR NOT NULL, series VARCHAR,
  launch_date_de VARCHAR, launch_date DATE, launch_date_kind VARCHAR, launch_source_url VARCHAR, successor VARCHAR,
  successor_launch_date DATE, notes VARCHAR, <TAIL>);
  -- launch_date = launch_date_de parsed; a YYYY-MM value takes the 15th (market.anchors._to_date rule)
CREATE TABLE IF NOT EXISTS bronze.cat_variants (
  slug VARCHAR NOT NULL, spec VARCHAR NOT NULL, storage_gb INTEGER, ram_gb INTEGER, rrp_eur_launch_de DECIMAL(12,2),
  rrp_source_url VARCHAR, rrp_source_date DATE, <TAIL>, PRIMARY KEY (slug, spec));
CREATE TABLE IF NOT EXISTS bronze.mkt_curves (
  group_kind VARCHAR NOT NULL, "group" VARCHAR NOT NULL, population VARCHAR NOT NULL, n INTEGER, age_min DOUBLE, age_max DOUBLE,
  intercept DOUBLE, slope_per_month DOUBLE, monthly_depreciation_pct DOUBLE, grade_A_offset DOUBLE, grade_C_offset DOUBLE,
  grade_D_offset DOUBLE, mape_in_sample DOUBLE, q_12 DOUBLE, q_24 DOUBLE, q_36 DOUBLE, fit_quality VARCHAR NOT NULL,
  <TAIL>, PRIMARY KEY (group_kind, "group", population));

-- ERP
CREATE TABLE IF NOT EXISTS bronze.erp_purchase_orders (
  po_number VARCHAR PRIMARY KEY, supplier_id VARCHAR NOT NULL, supplier_name VARCHAR NOT NULL, supplier_role VARCHAR NOT NULL,
  contract_ref VARCHAR, order_date DATE NOT NULL, promised_date DATE NOT NULL, currency VARCHAR NOT NULL, incoterm VARCHAR,
  payment_terms_days INTEGER, <TAIL>);
CREATE TABLE IF NOT EXISTS bronze.erp_po_lines (
  po_number VARCHAR NOT NULL, po_line INTEGER NOT NULL, slug VARCHAR NOT NULL, storage_gb INTEGER NOT NULL, colour VARCHAR,
  qty_ordered INTEGER NOT NULL, unit_price_eur DECIMAL(12,2) NOT NULL, price_protection_days INTEGER,
  <TAIL>, PRIMARY KEY (po_number, po_line));
CREATE TABLE IF NOT EXISTS bronze.erp_goods_receipts (
  gr_number VARCHAR NOT NULL, po_number VARCHAR NOT NULL, po_line INTEGER NOT NULL, serial VARCHAR PRIMARY KEY,
  received_at TIMESTAMP NOT NULL, warehouse VARCHAR, <TAIL>);
CREATE TABLE IF NOT EXISTS bronze.erp_supplier_invoices (
  invoice_number VARCHAR NOT NULL, invoice_line INTEGER NOT NULL, supplier_id VARCHAR NOT NULL, po_number VARCHAR NOT NULL,
  po_line INTEGER NOT NULL, serial VARCHAR, invoice_date DATE NOT NULL, line_kind VARCHAR NOT NULL, qty INTEGER NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, currency VARCHAR NOT NULL, <TAIL>, PRIMARY KEY (invoice_number, invoice_line));
  -- line_kind IN (unit, freight, duty, price_protection_credit); amount_eur is positive on every kind
CREATE TABLE IF NOT EXISTS bronze.erp_price_changes (
  change_id VARCHAR PRIMARY KEY, supplier_id VARCHAR NOT NULL, slug VARCHAR NOT NULL, storage_gb INTEGER NOT NULL,
  valid_from DATE NOT NULL, old_unit_price_eur DECIMAL(12,2) NOT NULL, new_unit_price_eur DECIMAL(12,2) NOT NULL, <TAIL>);

-- WMS
CREATE TABLE IF NOT EXISTS bronze.wms_staging_log (
  staging_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, staged_at TIMESTAMP NOT NULL, mdm_enrolled BOOLEAN NOT NULL,
  staging_cost_eur DECIMAL(12,2) NOT NULL, <TAIL>);
CREATE TABLE IF NOT EXISTS bronze.wms_shipments (
  shipment_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, related_serial VARCHAR, direction VARCHAR NOT NULL,
  shipped_at TIMESTAMP NOT NULL, delivered_at TIMESTAMP, rental_contract_ref VARCHAR, carrier_ref VARCHAR NOT NULL,
  cost_eur DECIMAL(12,2) NOT NULL, <TAIL>);
  -- direction IN (outbound, replacement_out, return); replacement_out: serial = the spare, related_serial = the damaged device

-- customer portal
CREATE TABLE IF NOT EXISTS bronze.portal_rental_contracts (
  contract_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, start_date DATE NOT NULL,
  term_months INTEGER NOT NULL, monthly_rate_eur DECIMAL(12,2) NOT NULL, end_date DATE NOT NULL, actual_end_date DATE,
  status VARCHAR NOT NULL, replaces_contract_id VARCHAR, <TAIL>);
CREATE TABLE IF NOT EXISTS bronze.portal_rental_invoices (
  invoice_id VARCHAR PRIMARY KEY, contract_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, period_no INTEGER NOT NULL,
  period_month DATE NOT NULL, invoice_date DATE NOT NULL, amount_eur DECIMAL(12,2) NOT NULL, <TAIL>);

-- service desk
CREATE TABLE IF NOT EXISTS bronze.sd_tickets (
  ticket_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, opened_at TIMESTAMP NOT NULL, closed_at TIMESTAMP,
  damage_type VARCHAR NOT NULL, resolution VARCHAR NOT NULL, quote_eur DECIMAL(12,2) NOT NULL, repair_cost_eur DECIMAL(12,2),
  replacement_serial VARCHAR, repair_partner_ref VARCHAR NOT NULL, <TAIL>);
  -- resolution IN (repair, replace, open)

-- returns desk
CREATE TABLE IF NOT EXISTS bronze.ret_receipts (
  receipt_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, contract_id VARCHAR, returned_at TIMESTAMP NOT NULL,
  grade_declared VARCHAR NOT NULL, grade_inspected VARCHAR NOT NULL, inspected_at TIMESTAMP NOT NULL,
  wipe_certificate_id VARCHAR, wiped_at TIMESTAMP, wipe_grading_cost_eur DECIMAL(12,2) NOT NULL, <TAIL>);

-- refurbishment partner
CREATE TABLE IF NOT EXISTS bronze.rf_work_orders (
  work_order_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP NOT NULL,
  cost_eur DECIMAL(12,2) NOT NULL, grade_out VARCHAR NOT NULL, outcome VARCHAR NOT NULL, partner_ref VARCHAR NOT NULL, <TAIL>);

-- recommerce channels
CREATE TABLE IF NOT EXISTS bronze.rc_orders (
  order_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL, listed_at TIMESTAMP NOT NULL,
  sold_at TIMESTAMP NOT NULL, gross_price_eur DECIMAL(12,2) NOT NULL, buyer_type VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL, <TAIL>);
CREATE TABLE IF NOT EXISTS bronze.rc_credit_notes (
  credit_note_id VARCHAR PRIMARY KEY, order_id VARCHAR NOT NULL, serial VARCHAR NOT NULL, channel VARCHAR NOT NULL,
  credited_at TIMESTAMP NOT NULL, gross_eur DECIMAL(12,2) NOT NULL, fee_pct_eur DECIMAL(12,2) NOT NULL,
  fee_fixed_eur DECIMAL(12,2) NOT NULL, net_eur DECIMAL(12,2) NOT NULL, <TAIL>);

-- contracts register (CLM)
CREATE TABLE IF NOT EXISTS bronze.ctr_register (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL,
  counterparty_is_public BOOLEAN NOT NULL, category VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  notice_days INTEGER NOT NULL, auto_renewal BOOLEAN NOT NULL, price_protection BOOLEAN NOT NULL, price_protection_days INTEGER,
  claim_window_days INTEGER, warranty_months INTEGER, rebate_tiers_json VARCHAR, volume_commitment_units INTEGER,
  payment_terms_days INTEGER NOT NULL, sla_json VARCHAR, spend_under_contract_eur DECIMAL(14,2) NOT NULL,
  terms_note VARCHAR NOT NULL, <TAIL>);

-- finance (indirect)
CREATE TABLE IF NOT EXISTS bronze.fin_indirect_spend (
  spend_id VARCHAR PRIMARY KEY, invoice_date DATE NOT NULL, category VARCHAR NOT NULL, supplier_name VARCHAR NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, has_po BOOLEAN NOT NULL, has_contract BOOLEAN NOT NULL, saving_eur DECIMAL(12,2) NOT NULL,
  saving_confirmed_by_controlling BOOLEAN NOT NULL, saving_type VARCHAR, baseline_amount_eur DECIMAL(12,2), <TAIL>);
```

```sql
-- SILVER
CREATE TABLE IF NOT EXISTS silver.ledger_lines (
  line_id VARCHAR PRIMARY KEY, serial VARCHAR NOT NULL, line_type VARCHAR NOT NULL, line_class VARCHAR NOT NULL,
  amount_eur DECIMAL(12,2) NOT NULL, event_date DATE NOT NULL, period_month DATE NOT NULL,
  source_system VARCHAR NOT NULL, source_table VARCHAR NOT NULL, source_ref VARCHAR NOT NULL, delivery_id VARCHAR,
  allocation_basis VARCHAR NOT NULL, is_estimate BOOLEAN NOT NULL, assumption_key VARCHAR, assumption_owner VARCHAR,
  counterparty VARCHAR, counterparty_role VARCHAR, contract_ref VARCHAR, as_of DATE NOT NULL, is_synthetic BOOLEAN NOT NULL,
  UNIQUE (serial, line_type, source_system, source_ref));
CREATE TABLE IF NOT EXISTS silver.serial_timeline (
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL, ordered_at DATE, received_at DATE, staged_at DATE, shipped_at DATE,
  returned_at DATE, wiped_at DATE, graded_at DATE, sellable_at DATE, sold_at DATE, credited_at DATE,
  lifecycle_status VARCHAR NOT NULL, steps_expected INTEGER NOT NULL, steps_present INTEGER NOT NULL,
  missing_steps VARCHAR, first_missing_step VARCHAR, is_monotonic BOOLEAN NOT NULL, non_monotonic_pair VARCHAR,
  chain_complete BOOLEAN NOT NULL, days_order_to_receipt INTEGER, days_receipt_to_ship INTEGER,
  days_return_to_sellable INTEGER, days_sellable_to_sold INTEGER, days_sold_to_credited INTEGER, days_return_to_cash INTEGER,
  source_refs_json VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS silver.device_ledger (  -- section 6.3 lists every column with its formula
  serial VARCHAR PRIMARY KEY, as_of DATE NOT NULL,
  slug VARCHAR, model_name VARCHAR, oem VARCHAR, catalogue_family VARCHAR, model_family VARCHAR, series VARCHAR,
  variant_spec VARCHAR, storage_gb INTEGER, rrp_gross_eur DECIMAL(12,2), rrp_net_eur DECIMAL(12,2), launch_date DATE,
  months_since_launch_at_as_of DOUBLE,
  po_number VARCHAR, po_line INTEGER, supplier_id VARCHAR, supplier_name VARCHAR, supplier_role VARCHAR, contract_ref VARCHAR,
  order_date DATE, received_at DATE, purchase_date DATE, cohort_month DATE, cohort_quarter VARCHAR,
  price_protection_days INTEGER, price_protection_status VARCHAR, price_protection_claimable_eur DECIMAL(12,2),
  purchase_price DECIMAL(12,2), discount_vs_rrp_eur DECIMAL(12,2), discount_vs_rrp_pct DOUBLE, freight_eur DECIMAL(12,2),
  duty_eur DECIMAL(12,2), landed_cost DECIMAL(12,2), landed_vs_rrp_pct DOUBLE, price_protection_credit_eur DECIMAL(12,2),
  staging_eur DECIMAL(12,2), outbound_shipping_eur DECIMAL(12,2), repair_eur DECIMAL(12,2), replacement_logistics_eur DECIMAL(12,2),
  return_logistics_eur DECIMAL(12,2), wipe_grading_eur DECIMAL(12,2), refurb_eur DECIMAL(12,2), holding_cost_eur DECIMAL(12,2),
  channel_fee_eur DECIMAL(12,2), days_in_stock_to_date INTEGER, tco_excl_landed_eur DECIMAL(12,2), tco_transactional_eur DECIMAL(12,2),
  tco_eur DECIMAL(12,2), n_lines INTEGER NOT NULL, n_estimate_lines INTEGER NOT NULL,
  first_contract_id VARCHAR, customer_id VARCHAR, term_months INTEGER, monthly_rate DECIMAL(12,2), contract_start DATE,
  contract_end_planned DATE, contract_end_effective DATE, months_billed INTEGER, months_remaining INTEGER,
  rental_revenue DECIMAL(12,2), remaining_contracted_rent DECIMAL(12,2),
  return_date DATE, grade_declared VARCHAR, grade_inspected VARCHAR, wipe_certificate_id VARCHAR, grade_out VARCHAR,
  refurb_outcome VARCHAR, sellable_date DATE, resale_channel VARCHAR, listed_at DATE, sale_date DATE, credited_at DATE,
  resale_gross DECIMAL(12,2), resale_net DECIMAL(12,2), days_return_to_sale INTEGER, days_return_to_cash INTEGER,
  estimate_run_id VARCHAR, grade_used VARCHAR, grade_source VARCHAR, estimate_rv_today DECIMAL(12,2),
  estimate_rv_lease_end DECIMAL(12,2), estimate_months_at_lease_end INTEGER, estimate_rv_source VARCHAR, estimate_fit_quality VARCHAR,
  estimate_rv_of_record DECIMAL(12,2), anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, anchor_rv_lease_end DECIMAL(12,2),
  estimate_vs_anchor_ratio DOUBLE, realised_vs_record_ratio DOUBLE,
  realised_rv DECIMAL(12,2), lifecycle_result_eur DECIMAL(12,2), result_v01_basis_eur DECIMAL(12,2), result_pct_of_landed DOUBLE,
  result_if_liquidated_today DECIMAL(12,2), result_projected_at_lease_end DECIMAL(12,2), projected_label VARCHAR,
  expected_remaining_cost DECIMAL(12,2), expected_cost_inputs_source VARCHAR,
  lifecycle_status VARCHAR NOT NULL, is_closed BOOLEAN NOT NULL, closed_date DATE, chain_complete BOOLEAN, is_synthetic BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS silver.reconciliation (
  serial VARCHAR NOT NULL, as_of DATE NOT NULL, field VARCHAR NOT NULL, device_pnl_value DECIMAL(12,2), ledger_value DECIMAL(12,2),
  diff DECIMAL(12,2), ok BOOLEAN NOT NULL, PRIMARY KEY (serial, field));
CREATE TABLE IF NOT EXISTS silver.contracts (  -- section 8.2
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, counterparty_is_public BOOLEAN NOT NULL,
  category VARCHAR NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, notice_days INTEGER NOT NULL, notice_deadline DATE NOT NULL,
  auto_renewal BOOLEAN NOT NULL, price_protection BOOLEAN NOT NULL, price_protection_days INTEGER, claim_window_days INTEGER,
  warranty_months INTEGER, rebate_tiers_json VARCHAR, volume_commitment_units INTEGER, payment_terms_days INTEGER NOT NULL, sla_json VARCHAR,
  spend_under_contract_eur DECIMAL(14,2) NOT NULL, spend_actual_12m_eur DECIMAL(14,2), spend_actual_vs_planned_pct DOUBLE,
  covers_oems VARCHAR, n_serials_under_contract INTEGER, status VARCHAR NOT NULL, days_to_notice_deadline INTEGER, days_to_end INTEGER,
  action_required BOOLEAN NOT NULL, terms_note VARCHAR NOT NULL, is_synthetic BOOLEAN NOT NULL, as_of DATE NOT NULL);
```

```sql
-- GOLD (rebuilt on every run; every table carries as_of)
CREATE TABLE IF NOT EXISTS gold.ingest_summary (
  feed VARCHAR PRIMARY KEY, source_system VARCHAR NOT NULL, delivering_system VARCHAR NOT NULL, n_files INTEGER NOT NULL,
  last_delivered_on DATE, rows_read INTEGER NOT NULL, rows_new INTEGER NOT NULL, duplicates_identical INTEGER NOT NULL,
  duplicates_conflict INTEGER NOT NULL, n_unresolved INTEGER NOT NULL, bronze_rows INTEGER NOT NULL, as_of DATE NOT NULL);
CREATE TABLE IF NOT EXISTS gold.chain_quality (
  lifecycle_status VARCHAR NOT NULL, step VARCHAR NOT NULL, n_serials INTEGER NOT NULL, n_present INTEGER NOT NULL,
  share_present DOUBLE, is_expected BOOLEAN NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (lifecycle_status, step));
CREATE TABLE IF NOT EXISTS gold.purchase_by_oem_month (
  oem VARCHAR NOT NULL, purchase_month DATE NOT NULL, supplier_role VARCHAR NOT NULL, n_units INTEGER NOT NULL,
  sum_rrp_net DECIMAL(14,2), sum_unit_price DECIMAL(14,2), sum_freight_duty DECIMAL(14,2), sum_landed DECIMAL(14,2),
  discount_vs_rrp_pct DOUBLE, landed_vs_rrp_pct DOUBLE, ppv_vs_po_eur DECIMAL(14,2), share_under_contract DOUBLE,
  pp_claimable_eur DECIMAL(14,2), pp_credited_eur DECIMAL(14,2), as_of DATE NOT NULL, PRIMARY KEY (oem, purchase_month, supplier_role));
CREATE TABLE IF NOT EXISTS gold.tco_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, line_type VARCHAR NOT NULL, n_devices INTEGER NOT NULL,
  mean_eur DECIMAL(12,2), sum_eur DECIMAL(14,2), is_estimate BOOLEAN NOT NULL, as_of DATE NOT NULL,
  PRIMARY KEY (cohort_kind, cohort_value, line_type));
CREATE TABLE IF NOT EXISTS gold.estimate_vs_anchor (
  catalogue_family VARCHAR NOT NULL, oem VARCHAR NOT NULL, n_rented INTEGER NOT NULL, n_with_anchor INTEGER NOT NULL,
  sum_estimate_lease_end DECIMAL(14,2), sum_anchor_lease_end DECIMAL(14,2), mean_estimate_ratio DOUBLE, mean_anchor_ratio DOUBLE,
  estimate_vs_anchor_ratio DOUBLE, anchor_curve_group VARCHAR, anchor_fit_quality VARCHAR, as_of DATE NOT NULL,
  PRIMARY KEY (catalogue_family, oem));
CREATE TABLE IF NOT EXISTS gold.resale_by_channel_grade (
  channel VARCHAR NOT NULL, grade_at_sale VARCHAR NOT NULL, n INTEGER NOT NULL, sum_gross DECIMAL(14,2), sum_fees DECIMAL(14,2),
  sum_net DECIMAL(14,2), sum_refurb DECIMAL(14,2), sum_estimate_of_record DECIMAL(14,2), realised_vs_record_ratio DOUBLE,
  median_days_return_to_cash DOUBLE, n_credit_note_missing INTEGER NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (channel, grade_at_sale));
CREATE TABLE IF NOT EXISTS gold.result_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, n INTEGER NOT NULL, n_closed INTEGER NOT NULL, n_open INTEGER NOT NULL,
  sum_result_closed DECIMAL(14,2), mean_result_closed DECIMAL(12,2), result_pct_of_landed_closed DOUBLE,
  sum_rental_revenue_closed DECIMAL(14,2), sum_realised_rv_closed DECIMAL(14,2), sum_pp_credit_closed DECIMAL(14,2),
  sum_tco_closed DECIMAL(14,2), sum_landed_closed DECIMAL(14,2), tco_per_closed_device DECIMAL(12,2), rv_per_closed_device DECIMAL(12,2),
  rent_per_closed_device DECIMAL(12,2), sum_liquidation_today_open DECIMAL(14,2), mean_liquidation_today_open DECIMAL(12,2),
  sum_projected_lease_end_open DECIMAL(14,2), mean_projected_lease_end_open DECIMAL(12,2), as_of DATE NOT NULL,
  PRIMARY KEY (cohort_kind, cohort_value));
CREATE TABLE IF NOT EXISTS gold.levers_per_device (
  serial VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, delta_eur DECIMAL(12,2), actual_value DOUBLE, reference_value DOUBLE,
  reference_source VARCHAR NOT NULL, n_reference INTEGER NOT NULL, is_attributed BOOLEAN NOT NULL, additive BOOLEAN NOT NULL,
  basis VARCHAR NOT NULL, event_date DATE, counterfactual_json VARCHAR NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (serial, lever_id));
CREATE TABLE IF NOT EXISTS gold.levers_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, n_attributed INTEGER NOT NULL,
  sum_delta_eur DECIMAL(14,2), mean_delta_eur DECIMAL(12,2), as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value, lever_id));
CREATE TABLE IF NOT EXISTS gold.levers_summary (
  lever_id VARCHAR PRIMARY KEY, lever_name VARCHAR NOT NULL, component VARCHAR NOT NULL, basis VARCHAR NOT NULL, additive BOOLEAN NOT NULL,
  n_eligible INTEGER NOT NULL, n_attributed INTEGER NOT NULL, eur_per_device DECIMAL(12,2), eur_per_device_p90 DECIMAL(12,2),
  eur_fleet_per_year DECIMAL(14,2), share_of_closed_loss DOUBLE, threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL,
  threshold_unit VARCHAR NOT NULL, threshold_owner VARCHAR NOT NULL, rule_id VARCHAR NOT NULL, reference_sentence VARCHAR NOT NULL,
  rank INTEGER NOT NULL, as_of DATE NOT NULL);
CREATE TABLE IF NOT EXISTS gold.contract_coverage_by_oem (
  oem VARCHAR PRIMARY KEY, n_units INTEGER NOT NULL, spend_total DECIMAL(14,2), spend_under_contract DECIMAL(14,2), coverage_pct DOUBLE,
  spend_direct DECIMAL(14,2), spend_via_reseller DECIMAL(14,2), n_contracts_in_force INTEGER NOT NULL, next_notice_deadline DATE, as_of DATE NOT NULL);
CREATE TABLE IF NOT EXISTS gold.renewal_calendar_v2 (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, counterparty_role VARCHAR NOT NULL, category VARCHAR NOT NULL,
  end_date DATE, notice_days INTEGER, notice_deadline DATE, days_to_notice_deadline INTEGER, days_to_end INTEGER, auto_renewal BOOLEAN,
  spend_under_contract_eur DECIMAL(14,2), spend_actual_12m_eur DECIMAL(14,2), price_protection_days INTEGER, claim_window_days INTEGER,
  price_protection_window_open BOOLEAN NOT NULL, action_required BOOLEAN NOT NULL, month_bucket VARCHAR, as_of DATE NOT NULL);
CREATE TABLE IF NOT EXISTS gold.rebate_progress (
  contract_id VARCHAR PRIMARY KEY, counterparty_name VARCHAR NOT NULL, spend_12m_eur DECIMAL(14,2), current_tier_pct DOUBLE,
  next_tier_from_eur DECIMAL(14,2), next_tier_pct DOUBLE, gap_to_next_tier_eur DECIMAL(14,2), as_of DATE NOT NULL);
CREATE TABLE IF NOT EXISTS gold.kpi_values (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, name VARCHAR NOT NULL, page VARCHAR NOT NULL, value DOUBLE, numerator DOUBLE,
  denominator DOUBLE, n INTEGER NOT NULL, unit VARCHAR NOT NULL, status VARCHAR NOT NULL, note VARCHAR, target DOUBLE, direction VARCHAR,
  formula_text VARCHAR, source_tables VARCHAR, owner VARCHAR, run_id VARCHAR, PRIMARY KEY (kpi_id, as_of));
CREATE TABLE IF NOT EXISTS gold.kpi_breakdown (
  kpi_id VARCHAR NOT NULL, as_of DATE NOT NULL, dimension VARCHAR NOT NULL, dimension_value VARCHAR NOT NULL, value DOUBLE,
  numerator DOUBLE, denominator DOUBLE, n INTEGER, PRIMARY KEY (kpi_id, as_of, dimension, dimension_value));
```

---

## 4. Module 1: lake (`restwert/lake/`)

Owns: `common.py` (3.4), `schema_lake.py` (3.8), `feeds.py`, `ingest.py`, `conform.py`,
`timeline.py`, the `db.py`, `paths.py`, `enums.py`, `schema.py` edits of section 3, the pure
entry points behind CLI `ingest`, `conform`, `timeline` (module 6 wires them), `docs/DATA_LAKE.md`,
`tests/test_lake.py`.

### 4.1 Feeds (`restwert/lake/feeds.py`)

```python
@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: Literal["str", "int", "float", "date", "datetime", "bool"]
    required: bool = True
    enum: tuple[str, ...] | None = None
    min_value: float | None = None

@dataclass(frozen=True)
class FeedSpec:
    key: str                          # "erp/po_lines"
    source_system: str                # "erp"
    feed: str                         # "po_lines"
    delivering_system: str            # prose for the doc, e.g. "ERP purchase orders"
    bronze_table: str                 # "bronze.erp_po_lines"
    business_key: tuple[str, ...]     # ("po_number", "po_line")
    serial_column: str | None         # "serial" or None
    order_column: str | None          # the timestamp that places a row in a delivery period; None for reference feeds
    columns: tuple[ColumnSpec, ...]   # landing columns (is_synthetic excluded; it is always required)
    resolves: tuple[tuple[str, str, str, str], ...]   # (child column or "col1+col2", parent bronze table, parent column(s), reason_code)
    soft_refs: tuple[tuple[str, str, str], ...]        # (child column, parent table, parent column): checked, reported as a note, row kept
    depends_on: tuple[str, ...]       # feed keys that must be ingested first
    is_reference: bool = False        # public copy, is_synthetic false
    description: str = ""

FEEDS: dict[str, FeedSpec]           # 19 feeds, insertion order = ingest order
INGEST_ORDER: tuple[str, ...] = tuple(FEEDS)
UNRESOLVED_REASONS: tuple[str, ...] = ("missing_required", "bad_type", "bad_enum", "negative_amount",
    "unknown_serial", "unknown_po", "unknown_po_line", "unknown_contract", "unknown_order", "unknown_slug",
    "unknown_variant", "duplicate_conflict")
def landing_file_name(feed: str, delivered_on: date, seq: int) -> str        # f"{delivered_on:%Y-%m-%d}_{feed}_{seq:03d}.csv"
def landing_path(raw_dir: Path, spec: FeedSpec, delivered_on: date, seq: int) -> Path   # raw_dir/<system>/<feed>/<name>
def parse_landing_name(name: str) -> tuple[date, str, int]
def render_data_lake_md() -> str
def write_data_lake_md(path: Path = DOCS_DIR / "DATA_LAKE.md") -> Path
```

Feed table (key; delivering system; business key; order column; hard resolves -> reason; soft):

| key | delivering system | business key | order column | resolves (hard) | soft |
|---|---|---|---|---|---|
| `catalogue/models` | public catalogue copy of data/catalogue/models.csv | slug | none (dated as_of) | none | none |
| `catalogue/variants` | public catalogue copy of variants.csv | (slug, spec) | none | slug -> cat_models.slug (`unknown_slug`) | none |
| `market/curves` | copy of outputs/market_curves.csv | (group_kind, group, population) | none | none | none |
| `contracts/register` | contracts register (CLM) | contract_id | start_date | none | none |
| `erp/purchase_orders` | ERP purchase orders | po_number | order_date | none | contract_ref -> ctr_register.contract_id |
| `erp/po_lines` | ERP purchase orders | (po_number, po_line) | (parent order_date, carried as column order_date in the landing file) | po_number -> erp_purchase_orders (`unknown_po`); slug+storage_gb -> cat_variants with RRP (`unknown_variant`) | none |
| `erp/goods_receipts` | ERP / WMS receiving | serial | received_at | po_number+po_line -> erp_po_lines (`unknown_po_line`) | none |
| `erp/supplier_invoices` | ERP accounts payable | (invoice_number, invoice_line) | invoice_date | po_number+po_line -> erp_po_lines (`unknown_po_line`); serial when given -> goods_receipts (`unknown_serial`) | none |
| `erp/price_changes` | ERP / supplier price lists | change_id | valid_from | slug+storage_gb -> cat_variants (`unknown_variant`) | none |
| `wms/staging_log` | staging and shipping system | staging_id | staged_at | serial -> goods_receipts | none |
| `wms/shipments` | staging and shipping system | shipment_id | shipped_at | serial -> goods_receipts; related_serial when given -> goods_receipts | rental_contract_ref -> portal_rental_contracts |
| `portal/rental_contracts` | customer portal | contract_id | start_date | serial -> goods_receipts | replaces_contract_id -> self |
| `portal/rental_invoices` | customer portal billing | invoice_id | invoice_date | contract_id -> portal_rental_contracts (`unknown_contract`); serial -> goods_receipts | none |
| `servicedesk/tickets` | incident and repair tickets | ticket_id | opened_at | serial -> goods_receipts | contract_id -> portal_rental_contracts; replacement_serial -> goods_receipts |
| `returns/receipts` | returns desk with grading and wipe certificate | receipt_id | returned_at | serial -> goods_receipts | contract_id -> portal_rental_contracts |
| `refurb/work_orders` | refurbishment partner work orders | work_order_id | started_at | serial -> goods_receipts | none |
| `recommerce/orders` | resale order system per channel | order_id | sold_at | serial -> goods_receipts | none |
| `recommerce/credit_notes` | channel settlement | credit_note_id | credited_at | order_id -> rc_orders (`unknown_order`); serial -> goods_receipts | none |
| `finance/indirect_spend` | accounts payable (indirect) | spend_id | invoice_date | none | none |

Order in `FEEDS` is the order of that table. `erp/po_lines` landing files carry an extra
`order_date` column (the header date) so the generator can split them into deliveries; it is
dropped at bronze (not in the DDL). Column lists are the bronze DDL columns of section 3.8
minus the tail, plus `is_synthetic` (always required); `cat_models` landing files have no
`launch_date` column (derived at typing: 10 chars ISO date, 7 chars YYYY-MM takes the 15th,
empty NULL). Enum columns: supplier_role (manufacturer, reseller), line_kind (unit, freight,
duty, price_protection_credit), direction (outbound, replacement_out, return), status (active,
ended, terminated_early, replaced), resolution (repair, replace, open), damage_type (screen,
battery, housing, water, other), grades (A, B, C, D), outcome (sellable, as_is, scrap), channel
(employee_buyout, marketplace, b2b_wholesale, as_is), buyer_type (employee, consumer, trader,
recycler), counterparty_role (CounterpartyRole values), register category (hardware,
connectivity, refurbishment, repair, logistics, resale_channel, financing, security_software),
saving_type (hard_price_reduction, cost_avoidance, rebate). Money columns carry `min_value = 0`.

`docs/DATA_LAKE.md` is rendered from `FEEDS` and `schema_lake` (one section per feed:
delivering system, landing file pattern, columns with types, key, order column, what resolves
against what and the reason code when it does not, the DDL) plus fixed prose: the layer rules
of section 2, the ingest algorithm (4.3), the conform mapping (4.4), the timeline rules (4.5),
the compat sentence of D2 and the VAT convention of D5.

### 4.2 Landing file format

* Path `data/lake/raw/<source_system>/<feed>/<YYYY-MM-DD>_<feed>_<seq:03d>.csv`.
* Line 1: `# SYNTHETIC DATA - restwert generate-lake seed=<seed> feed=<key> delivery=<date>` or
  `# PUBLIC DATA - copy of <path>, every row carries its source URL`. A real export has no `#`
  line and `is_synthetic=false` on every row.
* Header, then rows; ISO dates, ISO timestamps `YYYY-MM-DDTHH:MM:SS`, decimal point, `true`/`false`.
* `is_synthetic` column mandatory; the reader refuses a file without it (v0.1 rule).
* Never modified after landing; a correction is a new delivery.

### 4.3 Ingest (`restwert/lake/ingest.py`)

```python
@dataclass
class IngestReport:
    delivery_id: str; feed: str; source_system: str; source_file: str; sha256: str; delivered_on: date
    rows_read: int; rows_typed: int; rows_new: int; duplicates_identical: int; duplicates_conflict: int
    unresolved: dict[str, int]; notes: list[str]; already_ingested: bool; dry_run: bool; seconds: float
    def line(self) -> str   # "erp/goods_receipts 2023-12-31_goods_receipts_002.csv read=1210 new=1198 dup=8 conflict=1 unresolved=3 (unknown_po_line=3)"

def read_landing_file(path: Path) -> tuple[pd.DataFrame, bool]              # strings only; (frame, is_synthetic); ValueError without the column
def type_rows(spec: FeedSpec, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]   # (typed, rejected[row_number, reason_code, reason_text])
def resolve_keys(con, spec: FeedSpec, typed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]   # (resolved, unresolved, soft notes)
def dedupe(con, spec: FeedSpec, resolved: pd.DataFrame) -> tuple[pd.DataFrame, int, pd.DataFrame]         # (new, n_identical, conflicts)
def ingest_file(con, feed_key: str, path: Path, *, dry_run: bool = False, ingested_at: datetime | None = None) -> IngestReport
def ingest_all(con, raw_dir: Path = LAKE_RAW_DIR, *, dry_run: bool = False) -> list[IngestReport]
def run_ingest(con, raw_dir: Path, feed_key: str | None, path: Path | None, dry_run: bool) -> RunSummary
def ingest_summary_frame(con, as_of: date) -> pd.DataFrame     # gold.ingest_summary rows (written by run_ingest when not dry_run)
```

Algorithm of `ingest_file`, inside one DuckDB transaction (`con.begin()`; `con.rollback()` on
`dry_run` or on any exception, `con.commit()` otherwise; a dry run leaves nothing behind, not
even a `runs` row):
1. `create_lake_schema(con, drop_layers=())` (idempotent). sha256 the file; when the digest is
   already in `bronze.deliveries.sha256` return `already_ingested=True` with the stored counts
   (a rerun of `ingest --all` is a no-op).
2. `read_landing_file`: skip leading `#` lines, all columns as strings, `is_synthetic` parsed with
   the v0.1 `db._parse_bool_column` rule; a reference feed must be `false`, everything else one
   uniform value per file.
3. `type_rows`, vectorised per column: missing required -> `missing_required`; `pd.to_numeric` /
   `pd.to_datetime(errors="coerce")` producing NaN on a non-empty raw value -> `bad_type`;
   value outside `enum` -> `bad_enum`; below `min_value` -> `negative_amount`. A rejected row
   carries every reason it hit (comma list in `reason_code`, first reason as the code) and never
   aborts the file.
4. `resolve_keys`: each hard `resolves` entry checks the child column(s) against the parent
   bronze table's key set plus keys inside the same frame (self-parenting). Misses go to
   unresolved with the entry's reason code. Soft references only add a note.
5. `dedupe`: `row_hash = common.row_hash(content values in FeedSpec column order)`. Same
   business key already in bronze with the same hash -> `duplicates_identical`, skipped. Same key
   with a different hash -> `duplicate_conflict` in unresolved, **the existing row stays**.
   Duplicates inside one file follow the same rule (first occurrence wins).
6. Append the new rows with the tail columns (`delivery_id`, `source_file` = file name,
   `row_number` = 1-based data row index, `ingested_at`, `row_hash`, `is_synthetic`) via
   `db.write_df(con, spec.bronze_table, frame, mode="append")`.
7. Insert unresolved rows (`unresolved_id = sha1(delivery_id|row_number|reason_code)[:24]`,
   `key_json` = the business key values, `row_json` = the raw row) and the `bronze.deliveries`
   row (`delivered_on` from the file name; `reasons_json` = counts by reason).

`ingest_all` walks `raw_dir` in `INGEST_ORDER`, then by file name, calls `ingest_file` per file,
prints `report.line()` per file, rebuilds `gold.ingest_summary` and returns the reports. It never
reads `_manifest.json` (it re-hashes every file).

### 4.4 Conform (`restwert/lake/conform.py`)

```python
CONFORM_TABLES: tuple[str, ...] = schema.SOURCE_TABLES
def read_bronze(con) -> dict[str, pd.DataFrame]        # every bronze table keyed by short name (cat_models, erp_po_lines, ...)
def conform_frames(bronze: dict[str, pd.DataFrame], vat_rate: float, purchase_discount_pct: float, fee_assumptions: dict, as_of: date) -> tuple[dict[str, pd.DataFrame], list[str]]   # (frames, excluded slugs)
def write_compat_csvs(frames: dict[str, pd.DataFrame], csv_dir: Path, seed: int | None) -> list[Path]
def run_conform(con, as_of: date, a: Assumptions, csv_dir: Path | None = None, seed: int | None = None) -> RunSummary
```

`conform_frames` returns the ten v0.1 frames (passed through `schema.validate_frame` and
`db.referential_checks`), `is_synthetic` = the fleet flag on every row (the catalogue rows are
public one join away; the fleet that references them is synthetic; this keeps
`test_source_tables_are_synthetic` true and honest), `source_file = "bronze.<table>"`. Mapping:

* `model_catalogue`: one row per slug with `launch_date` and at least one priced variant (210 of
  233 since the laptop research of 2026-09-16, 208 after catalogue round 4; the excluded 23 are returned and listed in `SYNTHETIC.md`): `model = slug`,
  `model_family = fleet_family(family, oem)`, `generation` = dense rank of `launch_date` within
  (oem, series), `launch_date`, `list_price = rrp_net(min priced variant RRP, vat_rate)`,
  `base_storage_gb` = storage of that variant.
* `devices`: one row per goods receipt: `serial`, `model_family`, `model = slug`, `storage_gb`,
  `colour` (po line), `launch_date`, `purchase_date = received_at::date`, `purchase_price` = the
  `unit` invoice line amount for the serial when it exists else `po_lines.unit_price_eur`,
  `landed_cost = purchase_price + freight_share + duty_share` (`allocate_cents` of each freight
  and duty invoice line of the PO line over the received serials of that line in serial order;
  the ledger uses the same call), `supplier = supplier_name`, `channel_in` = manufacturer ->
  `oem_direct`, reseller -> `distributor`, `po_number = f"{po_number}-{po_line}"`,
  `contract_id` = earliest portal contract of the serial by start_date.
* `purchase_orders`: one row per PO line, `po_number` composite as above, `supplier`,
  `supplier_contract_id = contract_ref` when that `ctr_register` row has `start_date <= order_date <= end_date`
  else NULL, `model = slug`, `order_date`, `promised_date`, `delivered_date = max(received_at)::date`
  (NULL without receipts), `qty_ordered`, `qty_delivered` = count of receipts, `unit_price`,
  `benchmark_price = round(rrp_net x (1 - purchase_discount_pct), 2)` (assumption placeholder),
  `price_drop_date` / `price_drop_amount` = first `erp_price_changes` row for (supplier_id,
  slug, storage_gb) with `valid_from` in `(delivered_date, delivered_date + price_protection_days]`,
  amount = old - new; NULL when the line has no `price_protection_days`.
* `benchmarks`: per slug `valid_from = launch_date`, `landed_cost_benchmark = round(benchmark_price x 1.015, 2)`,
  `source_note = "public net launch RRP x (1 - purchase_discount_pct placeholder) x 1.015; URL in bronze.cat_variants"`.
* `rental_contracts`: column for column from `portal_rental_contracts` (`monthly_rate = monthly_rate_eur`).
* `events`: UNION of (a) `sd_tickets`: damage row `EV-T-<ticket_id>-D` at `opened_at::date`,
  `resolved = resolution != 'open'`, `cost = quote_eur when open else 0`; repair row
  `EV-T-<ticket_id>-R` at `closed_at::date` with `cost = repair_cost_eur` when resolution =
  repair; replacement row `EV-T-<ticket_id>-X` at `shipped_at::date` of the `replacement_out`
  shipment with `related_serial = serial` (matched by `rental_contract_ref = contract_id`, else
  the nearest by date), `cost = that shipment's cost_eur`, `replacement_serial`; and (b)
  `ret_receipts` joined to the `return` shipment of the serial (same `rental_contract_ref`, else
  the nearest by date): `EV-R-<receipt_id>`, `event_type = return`,
  `event_date = return_date = returned_at::date`, `cost = shipment cost_eur` (0 when none),
  `grade_pre_return = grade_declared`, `grade_inspected`, `wipe_certificate = wipe_certificate_id IS NOT NULL`,
  `contract_id`.
* `refurbishment`: `refurb_id = work_order_id`, `start_date = started_at::date`,
  `end_date = finished_at::date`, `days = end - start`, `cost = cost_eur`, `grade_out`, `outcome`.
* `resale`: from `rc_orders`: `sale_id = order_id`, `channel`, `sale_date = sold_at::date`,
  `price = gross_price_eur`, `fees = fee_pct_eur + fee_fixed_eur` of the credit note when one
  exists else `round(gross x fee_pct + fee_fixed_eur, 2)` from `fee_assumptions[channel]`
  (`assumptions.channel_fees`), `buyer_type`, `grade_at_sale`.
* `supplier_contracts`: `ctr_register` rows with role in (manufacturer, rugged_oem, reseller,
  refurb_repair, logistics, mtd): `supplier_contract_id = contract_id`, `supplier = counterparty_name`,
  `category` = `hardware` for manufacturer / rugged_oem / reseller, the register category
  (`refurbishment` or `repair`) for refurb_repair, `logistics`, `software` for mtd;
  `price_protection`, `price_protection_days = claim_window_days` (the v0.1 meaning),
  `payment_terms_days`, `spend_under_contract = spend_under_contract_eur`, `auto_renewal`,
  `notice_days`, dates. Carrier, marketplace and financing rows are register-only.
* `indirect_spend`: `fin_indirect_spend` with the v0.1 column names (`supplier = supplier_name`,
  `amount = amount_eur`, `saving = saving_eur`, `baseline_amount = baseline_amount_eur`).

`run_conform` calls `db.create_schema(con)`, writes the ten tables into `main` with
`write_df(mode="replace")`, writes `<csv_dir>/<table>.csv` compat copies when `csv_dir` is given
(line 1 `# SYNTHETIC DATA - conformed from data/lake by restwert conform seed=<seed>`, header, rows,
ISO dates) and returns counts per table plus `excluded_slugs`.

### 4.5 Timeline (`restwert/lake/timeline.py`)

```python
STEPS: tuple[str, ...] = TIMELINE_STEPS
EXPECTED_STEPS: dict[str, tuple[str, ...]] = {
    "not_deployed":    ("ordered_at", "received_at"),
    "rented":          ("ordered_at", "received_at", "staged_at", "shipped_at"),
    "awaiting_return": ("ordered_at", "received_at", "staged_at", "shipped_at"),
    "wip":             ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at"),
    "in_stock":        ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at", "wiped_at", "graded_at", "sellable_at"),
    "sold":            STEPS,
    "scrapped":        ("ordered_at", "received_at", "staged_at", "shipped_at", "returned_at", "wiped_at", "graded_at"),
}
def build_timeline(bronze: dict[str, pd.DataFrame], device_status: pd.DataFrame, as_of: date) -> pd.DataFrame   # silver.serial_timeline
def chain_quality(timeline: pd.DataFrame, as_of: date) -> pd.DataFrame                                            # gold.chain_quality
def run_timeline(con, as_of: date) -> RunSummary
```

Sources (dates, only events `<= as_of`): `ordered_at` = header `order_date` of the receipt's PO;
`received_at`; `staged_at` = first staging; `shipped_at` = first `outbound` or `replacement_out`
shipment where the serial is the shipped device; `returned_at` = latest receipt; `wiped_at`;
`graded_at = inspected_at`; `sellable_at` = `finished_at` of the latest work order with
outcome != scrap; `sold_at`; `credited_at`. `device_status` = `device_pnl[["serial", "lifecycle_status"]]`
(`run_timeline` reads it; when `device_pnl` is absent every status is `not_deployed` and the
summary notes it). `steps_present` counts expected steps present; `missing_steps` comma list;
`first_missing_step`; `is_monotonic` = every present step date >= the previous present step in
`STEPS` order (`non_monotonic_pair` names the first violation);
`chain_complete = steps_present == steps_expected AND is_monotonic`. `source_refs_json` maps
each present step to its bronze `source_ref`. `gold.chain_quality`: per (lifecycle_status,
step) the share present and `is_expected`.

### 4.6 Tests (`tests/test_lake.py`)

* `test_db_accepts_schema_qualified_names`: `create_lake_schema` on `:memory:`, `write_df("bronze.erp_po_lines", ...)`, `table_exists("bronze.erp_po_lines")` true and `table_exists("erp_po_lines")` false.
* `test_every_lake_table_created_in_memory`: every key of `LAKE_DDL` exists after `create_lake_schema`.
* `test_feeds_cover_every_bronze_table_and_order_respects_depends_on`.
* `test_ingest_types_dedupes_and_unresolves` on hand-written landing files in `tmp_path`: a bad date, a negative amount, an unknown serial, an identical duplicate, a conflicting duplicate; counts and reason codes exact; bronze unchanged by the conflict.
* `test_ingest_dry_run_writes_nothing` (no deliveries row, no bronze rows, no runs row).
* `test_ingest_same_file_twice_is_noop` (`already_ingested`, counts unchanged).
* `test_conform_frames_validate_and_allocate_cents`: two serials on one PO line with freight 10.01 -> landed shares 5.00 and 5.01, sum exact; every frame passes `validate_frame`.
* `test_timeline_expected_steps_per_status` (one serial per status) and `test_timeline_non_monotonic_flagged`.
* `test_data_lake_md_renders_every_feed_and_has_no_em_dash`.
* `test_lake_pipeline_bronze_rows_and_unresolved` on the session lake DB (10.5): every transactional bronze table has rows, `bronze.unresolved` is non-empty with only reasons from `UNRESOLVED_REASONS`, and no serial in any serial-level bronze table is absent from `erp_goods_receipts`.

---

## 5. Module 2: generate (`restwert/lakegen/`, `config/lake.yaml`)

Owns: `restwert/lakegen/*`, `config/lake.yaml` (3.5), the pure entry point behind CLI
`generate-lake`, `data/lake/SYNTHETIC.md`, `tests/test_lakegen.py`. Does not touch
`restwert/generate/` or `config/generator.yaml`.

### 5.1 Config (`restwert/lakegen/config.py`)

```python
class TruthV2(BaseModel): q_young_cap: float; ask_to_realised: float; grade_d_offset_default: float; noise_sigma: float
                          ratio_min: float; ratio_max: float; default_curve: dict[str, float]; owner: str
class DefectsBlock(BaseModel): unknown_serial_share: float; identical_duplicate_share: float; conflicting_duplicate_share: float
                               missing_credit_note_share: float; orphan_freight_share: float
class LakeConfig(GeneratorConfig):            # every GeneratorConfig field plus:
    delivery_cadence: Literal["yearly", "quarterly", "monthly"] = "quarterly"
    fleet_mix: dict[str, float]; oem_share: dict[str, dict[str, float]]; supplier_route: dict[str, float]
    resellers: list[str]; discount_by_oem: dict[str, list[float]]; reseller_markup_pct: list[float]; newest_model_share: float; previous_generation_share: float; generation_window_months: int
    freight_per_unit_eur: list[float]; duty_pct_reseller_b: float; staging_cost_eur: list[float]; outbound_cost_eur: list[float]
    wipe_grading_cost_eur: list[float]; mdm_enrolled_share: float; wipe_certificate_share: float; price_protection_claim_share: float
    truth_v2: TruthV2; defects: DefectsBlock
    # validators: fleet_mix keys == CATALOGUE_FAMILIES and sum 1; each oem_share map sums to 1 and its keys are in MANUFACTURERS;
    # supplier_route sums to 1; discount_by_oem covers every oem in oem_share; families has exactly the four FLEET_FAMILIES
def load_lake_config(path: Path = LAKE_CONFIG) -> LakeConfig
```
`LakeConfig` is a `GeneratorConfig`, so it is accepted wherever v0.1 code takes `cfg`
(`run_forecast` reads `cfg.families` and `cfg.seed`; `build_rental_contracts`, `build_events`,
`build_refurbishment` read the family blocks, `as_of`, grade and refurb blocks).

### 5.2 Catalogue (`restwert/lakegen/catalogue.py`)

```python
@dataclass(frozen=True)
class FleetCatalogue:
    models: pd.DataFrame        # every models.csv row + launch_date (parsed) + fleet_family + usable flag
    variants: pd.DataFrame      # every variants.csv row
    pool: pd.DataFrame          # one row per priced variant of a usable slug: slug, model_name, oem, family, series, launch_date,
                                # successor_launch_date, spec, storage_gb, rrp_gross, rrp_net, fleet_family, base_storage_gb
    excluded: pd.DataFrame      # slug, oem, family, reason (no_launch_date | no_priced_variant)
def load_fleet_catalogue(catalogue_dir: Path = CATALOGUE_DIR, vat_rate: float = 0.19) -> FleetCatalogue   # via market.anchors.read_tables
def latest_slugs(pool: pd.DataFrame, family: str, oem: str, before: date) -> list[str]   # distinct slugs launched <= before, newest first
def to_v01_devices_frame(fleet: pd.DataFrame) -> pd.DataFrame   # the v0.1 devices shape (+ _spare) the v0.1 builders take
```

### 5.3 Truth calibration (`restwert/lakegen/calibrate.py`)

```python
@dataclass(frozen=True)
class TruthCurve:
    group: str; source: Literal["family_oem", "family", "default"]; fit_quality: str; n: int
    intercept: float; slope_per_month: float; age_min: float; age_max: float; grade_offsets: dict[str, float]   # A, B (0), C, D
def load_curves(path: Path = MARKET_CURVES_CSV) -> pd.DataFrame          # the 17 columns; empty frame when missing
def truth_curve(curves: pd.DataFrame, catalogue_family: str, oem: str, cfg: TruthV2) -> TruthCurve
def q_public(curve: TruthCurve, age_months: float, grade: str) -> float   # exp(intercept + slope*age + offset[grade])
def true_price(rrp_net: float, curve: TruthCurve, age_months: float, grade: str, channel: str, channel_mult: dict[str, float], cfg: TruthV2, noise: float) -> float
```
`truth_curve`: the `(family_oem, "<Family> / <oem>", marketplace)` row when `fit_quality == "ok"`,
else `(family, "<Family>", marketplace)` (ok for all three families in the shipped file), else
`cfg.default_curve` (source `default`, only when the file is missing; the run summary says so).
Grade offsets: A and C from the row (NaN -> 0.0), B = 0, D = `grade_d_offset_default` (the
public rows carry no grade D fit). `true_price` = `rrp_net x min(q_public, q_young_cap) x
ask_to_realised x channel_mult[channel] x exp(noise)`, clipped to `[ratio_min x rrp_net,
ratio_max x rrp_net]`, rounded to 2 decimals; `noise ~ N(0, noise_sigma)` drawn by the caller.
No successor step. Documented in `SYNTHETIC.md` as "calibrated to public refurbisher asks,
haircut and cap are design parameters, not market facts".

### 5.4 World build (`restwert/lakegen/__init__.py` and builders)

One `numpy.random.default_rng(seed)` passed through the builders in this fixed order:
catalogue pool -> fleet -> purchase -> (v0.1) rentals -> (v0.1) events -> (v0.1) refurbishment
-> resale -> credit notes -> contracts register -> indirect -> defects -> writer.

```python
@dataclass
class World:            # in-memory frames in landing-file column order, keyed by feed key, plus helpers
    frames: dict[str, pd.DataFrame]
    v01: dict[str, pd.DataFrame]          # the v0.1-shaped devices, rental_contracts, events, refurbishment, resale used on the way
    truth_sources: dict[str, str]         # "<Family> / <oem>" -> family_oem | family | default
    excluded_slugs: list[str]
    defects_injected: dict[str, int]
    seed: int; n_serials: int
def generate_world(cfg: LakeConfig, cat: FleetCatalogue, curves: pd.DataFrame, seed: int | None = None, n_serials: int | None = None) -> World
def run_generate_lake(cfg: LakeConfig, seed: int, n_serials: int, raw_dir: Path, catalogue_dir: Path = CATALOGUE_DIR, curves_path: Path = MARKET_CURVES_CSV) -> RunSummary
```

`fleet.build_fleet(cfg, cat, rng, n)` -> one row per serial (n plus 5 % spares per catalogue
family, `_spare` flag): order month uniform over [history_start, purchase_end]; `catalogue_family`
by `fleet_mix`; `oem` by `oem_share[family]`; slug by model GENERATION: the window is anchored on
`newest`, the latest `launch_date <= order_date` of (family, oem), not on the order date: the current
generation = every slug launched in `(newest - generation_window_months, newest]`, the previous
generation = the slugs of the window before that, older = the rest; the draw picks the current generation
with p `newest_model_share`, the previous one with `previous_generation_share`, the older pool with the
remainder, then one slug uniformly inside the picked set (an empty set falls back to the next newer one;
three slugs of one generation launched weeks apart are bought alike, and stay one set however long before
the order they launched, so no single slug takes the whole share). An oem with no slug launched by then is redrawn, counted in the summary; variant = uniform over
the slug's priced variants; `supplier_role` by `supplier_route`; reseller name uniform over
`resellers` when reseller; discount `d ~ U(discount_by_oem[oem])` minus `U(reseller_markup_pct)`
on the reseller route; `unit_price_eur = round(rrp_net x (1 - d), 2)`; `serial = f"SN-{OEM_CODES[oem]}-{rng 8 hex upper}"` (unique);
`colour` from a fixed list.

`purchase.build_purchase(cfg, fleet, register, rng)` -> frames `erp/purchase_orders`,
`erp/po_lines`, `erp/goods_receipts`, `erp/supplier_invoices`, `erp/price_changes`:
* one PO per (order month, supplier, catalogue family): `po_number = f"PO-{yyyy}-{seq:06d}"`,
  `supplier_id` = `SUP-<OEM code>` or `SUP-RSL-A/B`, `supplier_name` = oem string or the reseller
  role name, `contract_ref` = the register contract of that counterparty when in force at
  `order_date` (90 % of manufacturer POs, 100 % of reseller POs; the rest empty = not under
  contract), `promised_date = order_date + 21 days`, `currency = EUR`, `incoterm = DAP`,
  `payment_terms_days` from the contract (default 30);
* one line per (PO, slug, storage_gb): `qty_ordered` = serials on it, `unit_price_eur` = the
  serials' price (all serials of a line share the draw of the first), `price_protection_days`
  from the contract (NULL when none);
* one goods receipt per serial: `gr_number = f"GR-{yyyy}-{seq:06d}"`, `received_at = order_date + U(14, 35) days`
  at 10:00; 10 % of lines are short deliveries (one serial of the line lands 20 days later on a second receipt);
* invoices: `invoice_number = f"INV-{yyyy}-{seq:06d}"`; a `unit` line per serial
  (`invoice_date = received_at + U(2, 10)`, qty 1, amount = unit price); one `freight` line per PO
  line without serial (`amount = round(qty_received x U(freight_per_unit_eur), 2)`); one `duty`
  line per PO line of "IT reseller B (role-only)" (`amount = round(duty_pct_reseller_b x qty x unit price, 2)`);
* price changes: for `price_drop_share` (0.15) of (supplier_id, slug, storage_gb) one drop of
  `price_drop_pct` dated at the slug's `successor_launch_date` when known and after the first
  order, else a uniform month after the first order; `change_id = f"PC-{seq:05d}"`;
* price protection credits: for every PO line whose drop falls inside `(received, received + price_protection_days]`,
  with probability `price_protection_claim_share` an invoice line `line_kind = price_protection_credit`,
  `qty = qty_received`, `amount = round(drop x qty, 2)`, `invoice_date = valid_from + U(10, 40)`
  (only when `<= as_of`); the rest stay unclaimed (the lever L02 measures them).

`operations.build_operations(cfg, world, rng)` calls the v0.1 builders on
`to_v01_devices_frame(fleet)` (with `purchase_date = received_at::date`, `landed_cost` = unit +
allocated freight + duty via `allocate_cents`, `model_family = fleet_family`): `build_rental_contracts`
(start = purchase_date + U(3, 30) days), `build_events`, `build_refurbishment`; then renders:
* `portal/rental_contracts` (`contract_id` RC-..., `monthly_rate_eur = monthly_rate`),
* `portal/rental_invoices`: for each contract, k = 1 .. `months_between(start, min(coalesce(actual_end, end), as_of))`:
  `invoice_id = f"RI-{contract_id[3:]}-{k:03d}"`, `period_no = k`, `period_month = month_floor(add_months(start, k - 1))`,
  `invoice_date = billing_date(start, k)`, `amount_eur = monthly_rate`;
* `wms/staging_log`: per contract start (initial and replacement contracts): `staged_at = max(received_at + 1 day, start - U(1, 3) days)`,
  `mdm_enrolled` with `mdm_enrolled_share`, `staging_cost_eur ~ U(staging_cost_eur)`; spares never deployed are not staged;
* `wms/shipments`: `outbound` per initial contract (`shipped_at = start - 1 day`, `delivered_at = start`,
  `cost ~ U(outbound_cost_eur)`, `rental_contract_ref`), `replacement_out` per v0.1 replacement
  event (`serial` = the spare, `related_serial` = the damaged serial, `shipped_at` = event date,
  `cost` = event cost, `rental_contract_ref` = the replaced contract), `return` per v0.1 return event
  (`shipped_at = return_date - U(1, 3)`, `delivered_at = return_date`, `cost` = event cost);
  `carrier_ref = "Logistics partner (role-only)"`, `shipment_id = f"SH-{seq:07d}"`;
* `servicedesk/tickets` from v0.1 damage/repair/replacement events: one ticket per damage event
  (`ticket_id = f"TK-{seq:06d}"`, `opened_at` = damage date, `resolution` = repair / replace / open,
  `closed_at` = repair date or replacement date or NULL, `quote_eur`, `repair_cost_eur` = repair
  cost, `replacement_serial`, `repair_partner_ref = "Refurbishment and repair partner (role-only)"`);
* `returns/receipts` from v0.1 return events: `receipt_id = f"RR-{seq:06d}"`, `returned_at`,
  `grade_declared = grade_pre_return`, `grade_inspected`, `inspected_at = returned_at + U(1, 3) days`,
  `wipe_certificate_id = f"WIPE-{8 hex}"` and `wiped_at = returned_at + U(0, 2) days` when the v0.1
  event has `wipe_certificate` true, else both NULL, `wipe_grading_cost_eur ~ U(wipe_grading_cost_eur)`;
* `refurb/work_orders` from v0.1 refurbishment: `work_order_id = f"WO-{seq:06d}"`,
  `started_at = start_date`, `finished_at = end_date`, `cost_eur`, `grade_out`, `outcome`,
  `partner_ref = "Refurbishment and repair partner (role-only)"`.

`resale.build_resale(cfg, world, cat, curves, rng)` -> `recommerce/orders`, `recommerce/credit_notes`
(and the v0.1-shaped `resale` frame kept in `world.v01`): for every sellable or as_is work order
finished `<= as_of`: channel by `channel_mix_by_grade[grade_out]`; `listed_at = finished_at + U(0, 3)`;
`sold_at = listed_at + U(days_to_sale[channel])`, slow movers (`slow_mover_share`) instead
`U(slow_mover_days)`; skip when `sold_at > as_of`; `age = months_between_float(launch_date, sold_at)`;
`gross_price_eur = true_price(rrp_net, truth_curve(family, oem), age, grade_out, channel, cfg.channel_mult, cfg.truth_v2, noise)`;
`buyer_type` by channel (employee / consumer / trader / recycler); `order_id = f"RO-{seq:06d}"`.
Credit note per order: `credited_at = sold_at + days_to_cash[channel]` (assumptions.yaml
channel_fees), only when `<= as_of`; `fee_pct_eur = round(gross x fee_pct, 2)`, `fee_fixed_eur`,
`net_eur = gross - fees`; `credit_note_id = f"CN-{seq:06d}"`.

`contracts.build_register(cfg, rng, as_of)` -> `contracts/register` with the counterparties of
section 8.1 (about 24 rows): manufacturers one hardware contract each (24 to 36 months, most
starting before `history_start`, two ending within 6 months after `as_of`, one expired before
`as_of` so R06 fires), price protection on six of ten (`price_protection_days` 30/45/60,
`claim_window_days` 14/30), warranty 12/24/36, rebate tiers on four, `sla_json` per role
(section 8.1), `spend_under_contract_eur` = 12 x mean monthly PO value of the counterparty
(computed after the purchase step; 0 for non-hardware roles is replaced by a design band),
`terms_note` = the fixed sentence of 8.1. `contracts.build_indirect(cfg, register, rng)` wraps
v0.1 `build_indirect_spend` with the role-only indirect suppliers of `lake.yaml`.

`defects.inject(world, cfg, rng)` mutates landing frames after everything else and returns
counts: `unknown_serial` (a random `wms/shipments` outbound row gets one hex digit of its serial
changed), `identical_duplicate` (rows of any transactional feed copied into the next delivery
period unchanged), `conflicting_duplicate` (`portal/rental_invoices` rows copied into the next
delivery with `amount_eur + 0.01`), `missing_credit_note` (credit notes deleted for that share of
sold orders), `orphan_freight` (freight invoice lines with `po_line + 90`). Defects never touch
`catalogue/*`, `market/curves`, `contracts/register`.

### 5.5 Writer (`restwert/lakegen/writer.py`)

```python
def delivery_periods(cfg: LakeConfig) -> list[tuple[date, date]]       # (period_start, delivered_on = period end) from history_start to as_of by cadence
def split_deliveries(df: pd.DataFrame, order_column: str | None, periods, as_of: date) -> list[tuple[date, pd.DataFrame]]
def write_landing_files(world: World, raw_dir: Path, cfg: LakeConfig, seed: int) -> list[Path]
def write_manifest(raw_dir: Path, files: list[Path], seed: int) -> Path        # data/lake/raw/_manifest.json: seed, per file sha256 and row count (informational)
def render_synthetic_md(world: World, cfg: LakeConfig, files: list[Path]) -> str
def write_synthetic_md(lake_dir: Path, text: str) -> Path                       # data/lake/SYNTHETIC.md
```
Rows go into the delivery whose period contains `order_column` (reference feeds and
`contracts/register`: one delivery dated `as_of`, `seq` 001). A row whose `order_column` is after
`as_of` is never written. **Leakage rule, tested:** every timestamp column of every row in a file
dated `delivered_on` is `<= delivered_on` (except the planned future dates `end_date`,
`promised_date`, `successor_launch_date`, contract `end_date`). Files are written with `csv`
module output, `newline=""`, UTF-8, LF, `is_synthetic` last. Empty periods write no file.
`SYNTHETIC.md` lists: seed, n_serials, spares, cadence, per feed file and row counts, the truth
source per (family, oem) with fit_quality and n, `q_young_cap`, `ask_to_realised`,
`grade_d_offset_default` with the owner, the excluded slugs, the injected defect counts, the
counterparty allowlist, the VAT convention, and the sentence "Every number is a synthetic design
parameter from config/lake.yaml; the catalogue and the anchor curves are public; no market
benchmark, no customer, no supplier beyond the public manufacturer names, no employer is real."

### 5.6 Tests (`tests/test_lakegen.py`)

* `test_lake_config_loads_and_validates` (four families, mixes sum to 1, fee blocks equal `assumptions.channel_fees`).
* `test_fleet_catalogue_uses_167_usable_slugs_and_lists_excluded`.
* `test_truth_curve_selection` (Smartphone/Apple -> family_oem ok; Laptop/HP -> family; missing file -> default) and `test_true_price_inside_caps`.
* `test_generate_world_is_reproducible` (same seed, byte-identical landing files in two tmp dirs) and `test_different_seed_differs`.
* `test_every_landing_file_has_synthetic_line_and_column` and `test_reference_files_are_public_false`.
* `test_no_leakage_per_delivery` (max event timestamp <= delivered_on per file).
* `test_rental_invoices_match_months_billed` (per contract, invoice count == v0.1 `months_billed`).
* `test_freight_allocation_sums_to_invoice_line`.
* `test_every_supplier_and_counterparty_name_is_allowed` (MANUFACTURERS or ends with `(role-only)`).
* `test_realisation_sanity` (grade B marketplace sales at 30 to 40 months: mean gross / rrp_net per catalogue family within 0.12 of `q_36 x ask_to_realised` of the family curve).
* `test_defects_are_injected_and_counted` (each defect count > 0 on 800 serials, counts equal the rows that differ).
* `test_generate_lake_small_under_5_seconds` (500 serials).

---

## 6. Module 3: ledger (`restwert/ledger/`)

Owns: `lines.py`, `device_ledger.py`, `result.py`, `reconcile.py`, `cohorts.py`, `run.py`,
the pure entry point behind CLI `ledger`, `docs/LEDGER.md`, `tests/test_ledger.py`. Reads
bronze, `device_pnl`, `rv_forecast_current`, `rv_forecast_of_record`, `rv_forecast_grid`,
`silver.serial_timeline` (module 1) and `main.model_catalogue`; writes `silver.ledger_lines`,
`silver.device_ledger`, `silver.reconciliation`, `gold.result_by_cohort`, `gold.tco_by_cohort`,
`gold.purchase_by_oem_month`, `gold.estimate_vs_anchor`, `gold.resale_by_channel_grade`.
Runs after `forecast` and `pnl` (it needs `device_pnl.lifecycle_status` and the forecast tables)
and after `timeline`.

### 6.1 Line vocabulary (`restwert/ledger/lines.py`)

```python
LINE_TYPES: dict[str, str] = {  # line_type -> line_class
  "purchase_price": "cost", "freight": "cost", "duty": "cost", "staging": "cost", "outbound_shipping": "cost",
  "rental_revenue": "revenue", "repair": "cost", "replacement_logistics": "cost", "return_logistics": "cost",
  "wipe_grading": "cost", "refurbishment": "cost", "holding_cost": "cost", "resale_gross": "revenue",
  "channel_fee": "cost", "price_protection_credit": "revenue"}
LEDGER_ORDER: tuple[str, ...] = tuple(LINE_TYPES)              # display order = cycle order
ESTIMATE_LINE_TYPES: tuple[str, ...] = ("holding_cost",)
V01_BRIDGE_LINE_TYPES: tuple[str, ...] = ("staging", "outbound_shipping", "wipe_grading", "holding_cost", "price_protection_credit")
LANDED_LINE_TYPES = ("purchase_price", "freight", "duty")
```

| line_type | sign | bronze source and rule | source_ref | allocation_basis |
|---|---|---|---|---|
| purchase_price | - | `erp_supplier_invoices` line_kind unit for the serial (`invoice_date <= as_of`); when no unit invoice line exists yet, `erp_po_lines.unit_price_eur` with `is_estimate = true`, `assumption_key = "po_line_price_pending_invoice"`, owner "Head of Procurement (name)" | `erp:INV-2024-000183/2` or `erp:PO-2024-000412/1` | direct |
| freight, duty | - | invoice lines of that kind on the PO line, `allocate_cents(amount, n received serials)` in serial order; `event_date = invoice_date` | `erp:<invoice>/<line>` | per_unit_of_po_line |
| price_protection_credit | + | invoice line_kind price_protection_credit on the PO line, same allocation | `erp:<invoice>/<line>` | per_unit_of_po_line |
| staging | - | `wms_staging_log.staging_cost_eur`, `event_date = staged_at` | `wms:<staging_id>` | direct |
| outbound_shipping | - | `wms_shipments` direction outbound on the serial | `wms:<shipment_id>` | direct |
| rental_revenue | + | one line per `portal_rental_invoices` row with `invoice_date <= as_of`, `contract_ref = contract_id` | `portal:<invoice_id>` | direct |
| repair | - | `sd_tickets` resolution repair, `closed_at <= as_of`, `repair_cost_eur` | `servicedesk:<ticket_id>` | direct |
| replacement_logistics | - | `wms_shipments` direction replacement_out where `related_serial = serial` (booked on the damaged device, as v0.1) | `wms:<shipment_id>` | direct |
| return_logistics | - | `wms_shipments` direction return on the serial | `wms:<shipment_id>` | direct |
| wipe_grading | - | `ret_receipts.wipe_grading_cost_eur`, `event_date = returned_at` | `returns:<receipt_id>` | direct |
| refurbishment | - | `rf_work_orders.cost_eur`, `finished_at <= as_of` | `refurb:<work_order_id>` | direct |
| resale_gross | + | `rc_orders.gross_price_eur`, `event_date = sold_at`, `sold_at <= as_of` | `recommerce:<order_id>` | direct |
| channel_fee | - | credit note `fee_pct_eur + fee_fixed_eur` when the order has one, `event_date = sold_at`; else `round(gross x fee_pct + fee_fixed, 2)` from `assumptions.channel_fees[channel]` with `is_estimate = true`, `assumption_key = "channel_fees"`, owner Head of Recommerce (name) | `recommerce:<credit_note_id>` or `assumptions:channel_fees[<channel>]` | direct |
| holding_cost | - | `days_in_stock_to_date x holding_cost_per_day_eur`, `days = (min(sold_at, as_of) - sellable_at).days` from `silver.serial_timeline`, 0 when not sellable; `event_date = min(sold_at, as_of)`; `is_estimate = true`, `assumption_key = "holding_cost_per_day_eur"`, owner CFO (name); only written when days > 0 | `assumptions:holding_cost_per_day_eur:<serial>` | days_x_rate |

Rules: every line except the two flagged estimates and the pending-invoice fallback is one
bronze transaction; `event_date <= as_of` or the line is not booked; open ticket quotes are never
lines; a scrapped device has no `resale_gross` line (realised RV 0 by absence); a sale dated after
`as_of` is not a sale; `line_id = common.line_id(...)`; `period_month = month_floor(event_date)`;
amounts rounded to 2 decimals before signing; `counterparty` = supplier name, customer id,
partner role name or channel; `delivery_id` copied from the bronze row (NULL on assumption lines).

```python
@dataclass
class BronzeFrames:   # short names, every field a DataFrame (possibly empty)
    cat_models; cat_variants; mkt_curves; erp_purchase_orders; erp_po_lines; erp_goods_receipts; erp_supplier_invoices
    erp_price_changes; wms_staging_log; wms_shipments; portal_rental_contracts; portal_rental_invoices; sd_tickets
    ret_receipts; rf_work_orders; rc_orders; rc_credit_notes; ctr_register; fin_indirect_spend
def read_bronze_frames(con) -> BronzeFrames
def build_ledger_lines(b: BronzeFrames, timeline: pd.DataFrame, a: Assumptions, as_of: date, is_synthetic: bool) -> pd.DataFrame   # pure
def holding_cost_lines(timeline: pd.DataFrame, rate_per_day: float, owner: str, as_of: date) -> pd.DataFrame
def lines_of(lines: pd.DataFrame, serial: str) -> pd.DataFrame                                                     # sorted by event_date, LEDGER_ORDER
```

### 6.2 Results (`restwert/ledger/result.py`, pure scalars, hand-testable)

```python
def result_closed(lines_of_serial: pd.DataFrame) -> float                                         # round(sum(amount_eur), 2)
def result_v01_basis(lines_of_serial: pd.DataFrame) -> float                                      # sum excluding V01_BRIDGE_LINE_TYPES
def result_if_liquidated_today(sum_lines_to_date: float, estimate_rv_today: float, fee_pct: float, fee_fixed: float) -> float
def result_projected_at_lease_end(sum_lines_to_date: float, remaining_rent: float, estimate_rv_lease_end: float, fee_pct: float, fee_fixed: float, expected_remaining_cost: float) -> float
def expected_remaining_cost(status: str, family: str, months_remaining: int, has_finished_work_order: bool, inputs: dict, a: Assumptions) -> tuple[float, str]
def anchor_rv(rrp_net: float, curve_row: pd.Series | None, age_months: float, grade: str, grade_d_default: float) -> tuple[float | None, str | None, str | None]
```

* `result_if_liquidated_today = sum_lines_to_date + estimate_rv_today x (1 - fee_pct_marketplace) - fee_fixed_marketplace`;
  `estimate_rv_today = rv_forecast_current.forecast_rv` (grade_used, grade_source carried).
  Label: "if every open device were sold today at the fleet model's marketplace estimate;
  remaining rent and the value at lease end are NOT in this number". It is the v0.1
  `margin_if_liquidated_today` plus the bridge lines and the fixed fee; both are stored.
* `result_projected_at_lease_end = sum_lines_to_date + remaining_contracted_rent + estimate_rv_lease_end x (1 - fee_pct) - fee_fixed - expected_remaining_cost`
  for rented and awaiting_return devices (`projected_label = "projected at lease end"`); for wip
  and in_stock devices `remaining_contracted_rent = 0` and `projected_label = "projected at sale"`;
  NULL for closed devices and for `not_deployed`.
  * `remaining_contracted_rent = monthly_rate x months_remaining`, `months_remaining = max(term_months - months_billed_on_the_latest_active_contract, 0)`; 0 unless the latest contract is `active`.
  * `estimate_months_at_lease_end = round(months_between_float(launch_date, contract_end_planned) + expected_return_to_sale_days[model_family] / DAYS_PER_MONTH)`
    (for returned devices: months at `as_of + expected_return_to_sale_days`);
    `estimate_rv_lease_end = purchase_price x grid_ratio` via `forecast.registry.grid_lookup(rv_grid, model, grade_used, months)`
    (grid at base storage, stated), `estimate_rv_source` in (grid, grid_clipped, planned_ratio_on_landed_cost)
    exactly as `pnl.tco.tco_per_model`; `grade_used` = `grade_out` or `grade_inspected` when returned, else
    `expected_grade_at_return[model_family]` (grade_source `inspected` | `refurbished` | `assumption`).
  * `expected_remaining_cost` per status with the realised-or-fallback rule of `pnl.tco._family_realised_inputs`
    (reused; `min_n_for_realised_inputs`), `expected_cost_inputs_source` in (realised, assumptions, mixed):
    rented: `damage_rate_pa x (months_remaining / 12) x repair_share x mean_repair_cost + logistics + refurb + wipe_grading_mean + holding_cost_per_day x expected_return_to_sale_days`;
    awaiting_return: the same without the repair term; wip: `refurb + holding`; in_stock: `holding_cost_per_day x days_to_cash[marketplace]`.
    `wipe_grading_mean` = fleet mean of the family's wipe_grading lines (n >= min_n) else `logistics_cost_fallback_eur / 2` (stated).
* `anchor_rv`: `rrp_net x exp(intercept + slope x age + offset[grade])` from the `bronze.mkt_curves`
  row chosen like `lakegen.calibrate.truth_curve` (family_oem ok, else family), grade B offset 0,
  grade D = `grade_d_default` (lake.yaml `truth_v2.grade_d_offset_default`, read through
  `load_lake_config`; -0.60 when the file is absent); **NULL when age is outside
  `[age_min, age_max]` of the row** (no extrapolation for an advisory number); returns
  `(value, curve_group, fit_quality)`. It enters no result, no rule, no lever.

### 6.3 `silver.device_ledger` (`restwert/ledger/device_ledger.py`)

```python
def sums_by_line_type(lines: pd.DataFrame) -> pd.DataFrame           # serial x line_type pivot of -amount for cost, +amount for revenue (positive magnitudes)
def build_device_ledger(lines, timeline, b: BronzeFrames, device_pnl, catalogue, rv_current, rv_of_record, rv_grid, a: Assumptions, lake_truth: TruthV2 | None, as_of: date) -> pd.DataFrame
```
Column formulas (L = the serial's lines with `event_date <= as_of`; every money column rounded to 2):
identity from `erp_goods_receipts` + `erp_po_lines` + `erp_purchase_orders` + `cat_models` +
`cat_variants` (variant matched on slug and storage_gb, lowest RRP when several);
`rrp_net_eur = rrp_net(rrp_gross_eur, vat_rate)`; `cohort_month = month_floor(purchase_date)`,
`cohort_quarter = quarter_label(purchase_date)` (equals `device_pnl.cohort`);
`purchase_price = sum(purchase_price lines)`, `freight_eur`, `duty_eur`, `landed_cost = purchase_price + freight + duty`;
`discount_vs_rrp_eur = rrp_net - purchase_price`, `discount_vs_rrp_pct = discount_vs_rrp_eur / rrp_net`,
`landed_vs_rrp_pct = landed_cost / rrp_net`; `price_protection_status` in (not_applicable: no
drop on the PO line inside the window; claimed: a credit line exists; open: drop, `valid_from + claim_window_days > as_of`, no credit;
missed: window closed, no credit), `price_protection_claimable_eur` = drop per unit when a drop qualifies else 0;
each TCO column = the magnitude of its line type; `tco_excl_landed_eur` = staging + outbound_shipping + repair +
replacement_logistics + return_logistics + wipe_grading + refurbishment + holding_cost + channel_fee;
`tco_transactional_eur = landed_cost + tco_excl_landed_eur - holding_cost_eur`; `tco_eur = landed_cost + tco_excl_landed_eur`;
`n_lines`, `n_estimate_lines`; contract block from the latest contract by start_date
(`first_contract_id` = earliest); `months_billed = count(rental_revenue lines)`,
`rental_revenue = sum(rental_revenue lines)`; return, refurb and resale block from bronze
(`resale_net = resale_gross - channel_fee_eur`, `days_return_to_sale`, `days_return_to_cash`
from the timeline); estimates per 6.2 (`estimate_rv_of_record = rv_forecast_of_record.forecast_rv`,
`realised_vs_record_ratio = resale_gross / estimate_rv_of_record` for sold devices with a record);
`realised_rv = resale_gross` when sold, 0.00 when scrapped, NULL otherwise (v0.1 convention);
`lifecycle_result_eur = result_closed(L)` and `result_v01_basis_eur = result_v01_basis(L)` for
closed devices (NULL open); `result_pct_of_landed = lifecycle_result_eur / landed_cost`;
`lifecycle_status`, `is_closed`, `closed_date` copied from `device_pnl`; `chain_complete` from the timeline.

### 6.4 Reconciliation (`restwert/ledger/reconcile.py`)

```python
RECONCILED_FIELDS: tuple[tuple[str, str], ...] = (       # (device_pnl column, device_ledger column)
  ("landed_cost", "landed_cost"), ("purchase_price", "purchase_price"), ("months_billed", "months_billed"),
  ("rental_revenue", "rental_revenue"), ("repair_cost", "repair_eur"), ("replacement_logistics_cost", "replacement_logistics_eur"),
  ("return_logistics_cost", "return_logistics_eur"), ("refurb_cost", "refurb_eur"), ("channel_fees", "channel_fee_eur"),
  ("realised_rv", "realised_rv"), ("lifecycle_margin", "result_v01_basis_eur"))
def reconcile_to_device_pnl(device_ledger: pd.DataFrame, device_pnl: pd.DataFrame, as_of: date, tol: float = 0.01) -> pd.DataFrame   # silver.reconciliation, one row per (serial, field)
def assert_reconciled(recon: pd.DataFrame) -> None       # raises ValueError naming the first failing serial and field
```
Identity: `lifecycle_margin (v0.1) = result_v01_basis_eur`, and
`lifecycle_result_eur = result_v01_basis_eur - (staging + outbound_shipping + wipe_grading + holding_cost) + price_protection_credit`.
`run_ledger` calls `assert_reconciled` and **refuses to finish** (raises) when any serial fails.
Because `main.devices.landed_cost`, `events.cost`, `refurbishment.cost`, `resale.price/fees` are
conformed from the same bronze rows the ledger reads and the rent lines use the same
`billing_date`, equality holds by construction on synthetic data; the test guards regressions,
and on real data a non-empty diff is the finding the Data page shows.

### 6.5 Cohorts and gold (`restwert/ledger/cohorts.py`)

```python
COHORT_KINDS: tuple[str, ...] = ("purchase_month", "purchase_quarter", "oem", "catalogue_family", "model_family", "term_months", "resale_channel", "supplier_role", "customer_id")
def result_by_cohort(dl: pd.DataFrame, kind: str, as_of: date) -> pd.DataFrame       # gold.result_by_cohort rows; ratios from sums
def tco_by_cohort(dl: pd.DataFrame, lines: pd.DataFrame, kind: str, as_of: date) -> pd.DataFrame   # closed devices only, kinds catalogue_family, oem, model_family, term_months
def purchase_by_oem_month(dl: pd.DataFrame, b: BronzeFrames, as_of: date) -> pd.DataFrame           # ppv_vs_po_eur = sum(unit invoice amount - po unit price); share_under_contract by unit count
def estimate_vs_anchor(dl: pd.DataFrame, as_of: date) -> pd.DataFrame                              # rented + awaiting_return devices
def resale_by_channel_grade(dl: pd.DataFrame, as_of: date) -> pd.DataFrame                         # sold in the trailing 12 months
```
The identity `sum_result_closed = sum_rental_revenue_closed + sum_realised_rv_closed + sum_pp_credit_closed - sum_tco_closed`
holds per row (test). Closed and open columns are never added into one number (a test asserts
no column named `result_total` exists in any silver or gold DDL).

### 6.6 Runner (`restwert/ledger/run.py`)

```python
def run_ledger(con, as_of: date, a: Assumptions) -> RunSummary
```
Reads bronze, `silver.serial_timeline`, `device_pnl`, `model_catalogue`, `rv_forecast_current`,
`rv_forecast_of_record`, `rv_forecast_grid` (missing forecast tables degrade to NULL estimates
with a note); writes the five silver and gold tables in `mode="replace"`; counts:
`ledger_lines`, `device_ledger`, `closed`, `open`, `reconciled_ok`, `reconciled_fail`,
`estimate_lines`, `result_by_cohort`.

### 6.7 `docs/LEDGER.md`

Hand-written by module 3: the line table of 6.1, the sign convention, the VAT convention, the
three result formulas with the two open-cycle labels, the bridge to v0.1, a worked example for
one serial (ten lines, hand-summed), and the sentence "TCO is a sum of lines; the only rate is
holding cost and every holding line says so".

### 6.8 Tests (`tests/test_ledger.py`)

* `test_line_types_closed_and_signed`: every line_type in `LINE_TYPES`, sign matches class on a built frame, `holding_cost` the only `is_estimate` type besides the pending-invoice and assumption-fee fallbacks (checked by `assumption_key`).
* `test_hand_serial_ten_lines`: hand-built bronze frames for one serial (unit 900.00, freight 10.01 over 2 serials, staging 8.50, outbound 7.00, 12 rent invoices of 40.00, one repair 120.00, return 9.50, wipe 4.00, refurb 30.00, sale 400.00 with credit note fees 50.50, holding 10 days x 0.30) -> `lifecycle_result_eur` == hand sum to the cent; `result_v01_basis_eur` == v0.1 `lifecycle_margin` computed with `pnl.lifecycle.lifecycle_margin`.
* `test_allocate_cents_exact` (10.01 / 3 -> 3.33, 3.33, 3.35).
* `test_rent_lines_equal_months_billed` using `billing_date` on clamped start dates (Jan 31, Jan 30, Feb 29).
* `test_two_open_numbers_differ_and_are_both_stored` (a rented serial: liquidation < projected, labels present).
* `test_projected_uses_expected_grade_and_grid`.
* `test_anchor_null_outside_age_range`.
* `test_reconciliation_identity_on_pipeline` on the session lake DB: every row of `silver.reconciliation` ok, count of serials == count(device_pnl), and `lifecycle_result_eur == result_v01_basis_eur - bridge + pp_credit` per closed serial.
* `test_cohort_identity_per_row` and `test_no_result_total_column_anywhere`.

---

## 7. Module 4: levers (`restwert/levers/`, rule R07, advisories ADV03 and ADV04)

Owns: `references.py`, `attribution.py`, `summary.py`, `run.py`, the pure entry point behind
CLI `levers`, the `decisions/rules.py`, `decisions/registry.py`, `decisions/runner.py` edits
listed in section 1, the three threshold keys of 3.6 (module 4 adds them to `thresholds.yaml`;
module 1 adds the tablet_like values; both edits are additive and non-overlapping),
`docs/LEVERS.md`, `tests/test_levers.py`. Reads `silver.device_ledger`, `silver.ledger_lines`,
`silver.serial_timeline`, bronze, `rv_forecast_of_record`, `rv_forecast_grid`,
`rv_forecast_current`; writes `gold.levers_per_device`, `gold.levers_by_cohort`,
`gold.levers_summary` and the ADV03 / ADV04 rows of `advisories`.

Principle: a lever is **actual minus a named reference on one ledger component**, in EUR per
device, `delta >= 0` = money left on the table. References are fleet rows anyone can list, the
forecast of record, or the grid; never an external benchmark. Below `lever_reference_min_n`
(assumptions.yaml, 10) a lever is `is_attributed = false` with `delta_eur NULL`, never guessed.
Levers do not add up; `additive` says which ones sit on disjoint components.

### 7.1 References (`restwert/levers/references.py`)

```python
def half_year(d: date) -> str                                           # "2024-H1"
def reference_discount(dl: pd.DataFrame, min_n: int) -> pd.DataFrame     # per serial: ref_pct, source ("oem+role+half_year" | "oem"), n; p75 of discount_vs_rrp_pct over the group
def realised_channel_net(dl: pd.DataFrame, min_n: int) -> pd.DataFrame   # (model_family, grade_at_sale, sale_quarter, channel) -> median resale_net, n
def family_realisation_median(dl: pd.DataFrame, min_n: int) -> pd.DataFrame   # (catalogue_family, age_bucket_6m, grade_at_sale) -> median resale_gross / rrp_net, n
def term_result_medians(dl: pd.DataFrame, min_n: int) -> pd.DataFrame    # (model_family, purchase_half_year, term_months) -> median lifecycle_result_eur, n
def grid_ratio(rv_grid: pd.DataFrame, model: str, grade: str, months: int) -> float | None   # forecast.registry.grid_lookup
def admissible_channels(grade: str, buyout_eligible: bool, days_to_cash: dict[str, float], thr: Thresholds, as_of: date) -> list[str]   # the R02 filter, same rules as decide_channel
```

### 7.2 The seven levers (`restwert/levers/attribution.py`)

```python
@dataclass(frozen=True)
class LeverSpec:
    lever_id: str; name: str; component: str; basis: Literal["fleet", "forecast_of_record", "grid"]; additive: bool
    threshold_key: str; threshold_sub: Literal["oem", "model_family", None]; rule_id: str; reference_sentence: str
LEVERS: dict[str, LeverSpec]      # L01..L07 in this order
@dataclass
class LeverResult:
    lever_id: str; delta_eur: float | None; actual_value: float | None; reference_value: float | None
    reference_source: str; n_reference: int; is_attributed: bool; event_date: date | None; counterfactual: dict
def lever_purchase_discount(row, ref: pd.Series | None) -> LeverResult
def lever_price_protection(row) -> LeverResult
def lever_channel(row, rv_record_row, thr: Thresholds, a: Assumptions, as_of: date) -> LeverResult
def lever_grade_repair(row, lines_of_serial: pd.DataFrame, rv_grid: pd.DataFrame, thr: Thresholds) -> LeverResult
def lever_aging(row, rv_grid: pd.DataFrame, a: Assumptions, as_of: date) -> LeverResult
def lever_oem_mix(row, family_ref: pd.Series | None) -> LeverResult
def lever_term(row, term_ref: pd.DataFrame) -> LeverResult
def attribute_all(dl, lines, timeline, rv_of_record, rv_grid, thr, a, as_of) -> pd.DataFrame    # gold.levers_per_device
def check_additivity(dl: pd.DataFrame, per_device: pd.DataFrame, tol: float = 0.01) -> pd.DataFrame   # rows breaking the identity; empty when fine
```

| id | name | component | basis | additive | eligible serials | delta_eur (per device, floored at 0 unless stated) | threshold (owner) | rule |
|---|---|---|---|---|---|---|---|---|
| L01 | purchase_discount | purchase_price | fleet | yes | every received serial | `(d_ref - discount_vs_rrp_pct) x rrp_net`, `d_ref` = p75 of `discount_vs_rrp_pct` over serials with the same (oem, supplier_role, purchase half-year) when n >= min_n, else same oem all time when n >= min_n, else not attributed | `purchase_discount_floor_pct[oem]` (Head of Procurement) | R07 |
| L02 | price_protection | price_protection_credit | fleet | yes | serials with `price_protection_status = missed` (delta = `price_protection_claimable_eur`); claimed and not_applicable serials contribute 0 with reference "claimed"/"n/a"; open ones are not attributed | `price_protection_min_claim_eur` (Category Manager Hardware) | R05 |
| L03 | channel_choice | resale_gross + channel_fee | forecast_of_record | no | sold serials with a forecast of record | `best_net - actual_net`, `net_c = estimate_rv_of_record x channel_factor_c x (1 - fee_pct_c) - fee_fixed_c - holding_cost_per_day x days_to_cash_c` over `admissible_channels` (grade at sale, buyout only inside `employee_buyout_window_days` after the effective contract end, channels above `channel_max_days_to_cash` dropped, never the last one), channel factors from `rv_forecast_of_record` (marketplace 1.0, as_is uses `as_is_ratio_fallback` share), `actual_net = resale_net - holding_cost_per_day x days_return_to_cash` | `channel_min_net_uplift_eur` (Head of Recommerce) | R02 |
| L04 | grade_and_repair | resale_gross, repair, refurbishment | grid | no | closed serials with a return | grade part `purchase_price x (grid_ratio(model, grade_declared, m_ret) - grid_ratio(model, grade_inspected, m_ret))` at months since launch at return (value lost between declared and inspected; negative when better, not floored), plus repair part `sum over repair lines of max(0, repair - repair_max_share_of_rv[family] x purchase_price x grid_ratio(model, grade_used, m_repair))` | `repair_max_share_of_rv[model_family]` (Head of Service Operations) | R01 |
| L05 | aging | holding_cost, resale_gross | grid | no | sold serials and in_stock serials (at as_of) | `excess_days x holding_cost_per_day + purchase_price x max(0, grid_ratio(model, grade_out, m_expected) - grid_ratio(model, grade_out, m_actual))`, `excess_days = max(0, days_sellable_to_sold - expected_return_to_sale_days[family])`, `m_expected` = months at `sellable_at + expected days`, `m_actual` = months at the sale (or `as_of`) | `aging_days_90` (CFO) | R03 |
| L06 | manufacturer_mix | resale_gross | fleet | no | sold serials | `(median_ratio_family - realised_ratio) x rrp_net`, `realised_ratio = resale_gross / rrp_net`, median over the fleet's own sold devices of the same (catalogue_family, 6-month age bucket at sale, grade_at_sale) with n >= min_n (2 x min_n for this lever); not floored per device (an oem that beats its family shows negative); summarised by oem | `oem_realisation_gap_pct` (Category Manager Hardware) | ADV03 |
| L07 | term_length | lifecycle_result | fleet | no | closed serials | `(median(result / term_months) of the best other term of 12, 24, 36, 48 - the same of this term) x this term's months` within (model_family, purchase half-year), this term and at least one other term at min_n; positive when the best other term closed better per month of term; the same value on every serial of the cohort | `term_result_gap_alert_eur` (CFO) | ADV04 |

`check_additivity`: for every closed serial `result_v01_basis + L01 + L02 == the same sum with
purchase_price at reference and the missed credit received`, i.e. an identity by construction
on the additive levers; the table of violations is stored in the run notes and asserted empty.
`counterfactual_json` stores every input used (reference group, n, the two values, the months
and grades read from the grid).

### 7.3 Summary (`restwert/levers/summary.py`)

```python
def levers_by_cohort(per_device: pd.DataFrame, dl: pd.DataFrame, kinds: tuple[str, ...] = ("oem", "catalogue_family", "model_family", "term_months", "purchase_quarter", "resale_channel"), as_of: date) -> pd.DataFrame
def levers_summary(per_device: pd.DataFrame, dl: pd.DataFrame, thr: Thresholds, as_of: date) -> pd.DataFrame     # gold.levers_summary, the where-to-tighten table
```
Per lever: `n_eligible`, `n_attributed`, `eur_per_device` = mean `delta_eur` over attributed
serials, `eur_per_device_p90`, `eur_fleet_per_year` = sum of `delta_eur` over attributed serials
whose `event_date` (purchase date for L01/L02, sale date for L03/L05/L06, closed date for
L04/L07; `as_of` for in_stock L05 rows) lies in the trailing 12 months before `as_of`,
`share_of_closed_loss` = `eur_fleet_per_year / |sum of negative lifecycle_result_eur over serials closed in the same window|`
(NULL when that sum is 0), `threshold_key`/`threshold_value`/`threshold_unit`/`threshold_owner`
resolved with `thr.get(key, sub, as_of)` (per-oem keys resolved with the oem carrying the largest
`eur_fleet_per_year`, the value column then reads `"<oem>: <value>"`), `rule_id`,
`reference_sentence` = `LeverSpec.reference_sentence`, `rank` by `eur_fleet_per_year`
descending. No total row is ever produced.

### 7.4 R07 (`restwert/decisions/rules.py`, same shape as R01..R06)

```python
def decide_purchase_floor(*, po_line_id: str, oem: str, supplier_name: str, supplier_role: str, unit_price_eur: float,
                          rrp_net_eur: float, qty: int, thr: Thresholds, as_of: date, run_id: str) -> DecisionRecord
```
`discount = 1 - unit_price_eur / rrp_net_eur`; `floor = thr.get("purchase_discount_floor_pct", oem, as_of)`;
`discount >= floor.value` (equality passes) -> outcome `at_or_above_floor` (no action);
else `below_floor_discount`, `value_at_stake_eur = (floor price - unit_price) x qty` with
`floor price = rrp_net x (1 - floor)`, `outcome_detail` = "discount 9.1 % vs floor 15.0 % for Samsung",
`subject_type = "purchase_order"`, `subject_id = po_line_id` (`"<po_number>-<po_line>"`).
Registry: `RULES["R07"] = RuleSpec("R07", "purchase_discount_floor", RULE_VERSION, "purchase_order", ("purchase_discount_floor_pct",), ("at_or_above_floor", "below_floor_discount"), description, decide_purchase_floor)`;
`NO_ACTION_OUTCOMES["R07"] = {"at_or_above_floor"}`; priority 2 (not in `PRIORITY_1_OUTCOMES`).
Runner `_r07(con, thr, as_of, run_id)`: reads `silver.device_ledger` grouped by (po_number,
po_line) when the table exists (`db.table_exists(con, "silver.device_ledger")`), else returns
`([], 0)`; one record per PO line (qty = serials on it). `run_all_decisions` gains
`("R07", lambda: _r07(con, thr, as_of, run_id))` after R06. On the v0.1 fixture database R07
yields 0 records, so `test_runner_end_to_end` keeps passing.

### 7.5 ADV03 and ADV04 (`restwert/levers/run.py` writes them; registry documents them)

`ADVISORY_SPECS["ADV03"] = {"kind": "manufacturer_mix", "name": "manufacturer_mix", "subject_type": "oem", "threshold_keys": ("oem_realisation_gap_pct",), "attached_to": (), "description": ...}`
and `ADV04 = {"kind": "term_gap", "subject_type": "cohort", "threshold_keys": ("term_result_gap_alert_eur",), ...}`.
`records.Advisory.kind` gains the two literals. `run_levers` deletes rows of those two kinds
from `advisories` and appends: ADV03 one row per oem whose mean L06 gap ratio over sales in the
trailing 12 months exceeds `oem_realisation_gap_pct` (payload: oem, family, n, mean_gap_pct,
threshold, eur_fleet_per_year; confidence low/medium/high by n < 30 / < 100 / else); ADV04 one
row per (model_family, half-year) whose L07 gap >= `term_result_gap_alert_eur` (payload: family,
half_year, better_term, gap_eur, n_24, n_36). `advisory_id = f"ADV03-{oem}-{as_of}"` etc.

Runner edit (minimal): `calibration_advisory_rows` becomes `advisory_queue_rows` (the old name
kept as an alias) with
`_ADVISORY_QUEUE = {"forecast_calibration": ("ADV02", "forecast_calibration", "review_model"), "manufacturer_mix": ("ADV03", "manufacturer_mix", "review_oem_allocation"), "term_gap": ("ADV04", "term_gap", "review_term_policy")}`
and `WHERE kind IN (...)`; `threshold_value` from the payload key `threshold`. Every such row is
priority 3, owner from `threshold_owner`. Because levers run before decide in the chain, the
queue carries them.

### 7.6 Runner (`restwert/levers/run.py`)

```python
def run_levers(con, as_of: date, thr: Thresholds, a: Assumptions) -> RunSummary
```
Counts: `levers_per_device`, `levers_attributed`, `levers_summary`, `levers_by_cohort`,
`adv03`, `adv04`, `additivity_violations` (must be 0). Missing `silver.device_ledger` raises
`ValueError("run ledger first")`.

### 7.7 `docs/LEVERS.md`

Hand-written by module 4: the table of 7.2 with the formulas, the additivity statement, a
worked example per lever on one hand serial, the sentence "a lever is a reference, not a
counterfactual fact", and the threshold and rule per lever with the owner read from
`thresholds.yaml` at render time (`render_levers_md(thr)` in `summary.py`, written by
`run_levers` only on the default DB path, same `_docs_wanted` rule as v0.1).

### 7.8 Tests (`tests/test_levers.py`)

* One test per lever on a hand-built `device_ledger` frame of 12 serials (exact EUR asserted, including "not attributed below min_n" and "negative allowed for L06").
* `test_admissible_channels_matches_decide_channel_rules`.
* `test_check_additivity_identity_holds_and_detects_a_break`.
* `test_levers_summary_has_owner_rule_and_no_total_row` (owners resolved through `Thresholds.get`, `rank` unique, no lever_id "total").
* `test_r07_pure_function_outcomes` (equality passes, value_at_stake exact) and `test_r07_zero_records_without_ledger` (v0.1 fixture DB).
* `test_adv03_adv04_rows_and_queue_priority_3` on the session lake DB.
* `test_levers_pipeline_tables_populated` (every lever_id present in `gold.levers_summary`, `additivity_violations == 0` in the runs row counts).
* `test_levers_md_no_em_dash_and_names_every_lever`.

---

## 8. Module 5: contracts v2 and gold KPIs (`restwert/contracts/register_v2.py`, `restwert/contracts/counterparties.py`, `restwert/gold/`)

Owns: `contracts/counterparties.py`, `contracts/register_v2.py`, `gold/{__init__,registry,kpis,catalogue,run}.py`,
the pure entry points behind CLI `contracts` (v2 part) and `gold-kpis`, `docs/CONTRACTS.md`,
`docs/GOLD_KPIS.md`, `tests/test_contracts_v2.py`, `tests/test_gold.py`. v0.1
`contracts/register.py` is untouched: `run_contracts` keeps producing `contracts_register` and
`renewal_calendar` from the conformed `supplier_contracts` and `rental_contracts`.

### 8.1 Counterparties (`restwert/contracts/counterparties.py`)

```python
@dataclass(frozen=True)
class Counterparty:
    name: str; role: str; category: str; is_public: bool; sla: dict; price_protection_days: int | None
    claim_window_days: int | None; warranty_months: int | None; rebate_tiers: list[dict] | None; payment_terms_days: int
COUNTERPARTIES: tuple[Counterparty, ...]
ROLE_ONLY_NAMES: tuple[str, ...]
TERMS_NOTE = "synthetic placeholder terms; the counterparty list is public (manufacturers from the catalogue, every other party role-only); no term is from any provider"
def is_allowed_name(name: str) -> bool     # name in common.MANUFACTURERS or name.endswith(common.ROLE_ONLY_SUFFIX)
```

| counterparty_name | role | category | terms (all placeholders) |
|---|---|---|---|
| Apple, Samsung, Google, Motorola, Fairphone, HMD Global (Nokia), Lenovo, Dell, HP, Microsoft | manufacturer | hardware | 24 to 36 months; price protection on Apple (30/14), Samsung (60/30), Google (45/30), Lenovo (60/30), Dell (45/14), HP (45/30) as (price_protection_days/claim_window_days), none on the other four; warranty 12/24/36; rebate tiers on Samsung, Lenovo, Dell, HP `[{"from_eur": 0, "pct": 0.0}, {"from_eur": 250000, "pct": 0.01}, {"from_eur": 750000, "pct": 0.02}]`; `sla_json {"delivery_lead_days": 14, "doa_replacement_days": 10}`; payment 30/45/60 |
| Rugged-device OEM (role-only) | rugged_oem | hardware | warranty 36, no POs in the fleet (no catalogue row) |
| IT reseller A (role-only), IT reseller B (role-only) | reseller | hardware | `covers_oems` = every oem bought through it; price protection 30/14 on A, none on B; payment 30 |
| Carrier partner (role-only) | carrier | connectivity | `sla_json {"activation_days": 2, "uptime_pct": 0.995}` |
| Refurbishment and repair partner (role-only) | refurb_repair | refurbishment (one row) and repair (one row) | `sla_json {"turnaround_days": 7, "first_time_fix_pct": 0.90}` |
| Logistics partner (role-only) | logistics | logistics | `sla_json {"pickup_within_days": 2, "delivery_days": 3}` |
| Marketplace channel A (role-only), Marketplace channel B (role-only) | marketplace | resale_channel | `sla_json {"fee_pct": 0.12, "fee_fixed_eur": 2.5, "payout_days": 28}` |
| Financing partner A (role-only), Financing partner B (role-only) | financing | financing | `sla_json {"funding_days": 5}` |
| Mobile threat defense partner (role-only) | mtd | security_software | `sla_json {"per_device_month_eur": 1.2}` |

The generator (module 2) builds `contracts/register` from `COUNTERPARTIES` (dates, notice
days, auto renewal and spend drawn there); module 5 owns the list and the allowlist test.

### 8.2 Register v2 (`restwert/contracts/register_v2.py`)

```python
def contracts_v2(ctr: pd.DataFrame, po_headers, po_lines, goods_receipts, credit_notes, lines, indirect, as_of: date) -> pd.DataFrame   # silver.contracts
def coverage_by_oem(po_headers, po_lines, goods_receipts, cat_models, ctr: pd.DataFrame, as_of: date) -> pd.DataFrame           # gold.contract_coverage_by_oem
def renewal_calendar_v2(contracts: pd.DataFrame, po_lines, price_changes, goods_receipts, as_of: date, horizon_months: int = 6) -> pd.DataFrame   # gold.renewal_calendar_v2
def rebate_progress(contracts: pd.DataFrame, po_headers, po_lines, goods_receipts, as_of: date) -> pd.DataFrame                  # gold.rebate_progress
def run_contracts_v2(con, as_of: date, thr: Thresholds) -> RunSummary       # calls v0.1 run_contracts first, then writes silver.contracts and the three gold tables
```
`silver.contracts` = every `ctr_register` row plus `status` (active | expired | future by
`as_of`), `notice_deadline = end_date - notice_days`, `days_to_notice_deadline`, `days_to_end`,
`action_required` (the v0.1 rule of `renewal_calendar`: notice deadline within `horizon_months`
or auto renewal with a deadline inside it), `spend_actual_12m_eur` (manufacturer and reseller:
received unit value `qty_received x unit_price_eur` of PO lines on headers with
`contract_ref = contract_id` in the trailing 12 months by `received_at`; marketplace: credit note
gross; refurb_repair / logistics: the matching ledger line types in the window (refurbishment
and repair; outbound + return + replacement logistics); carrier / financing / mtd:
`fin_indirect_spend` amounts of the category), `spend_actual_vs_planned_pct`, `covers_oems`
(reseller rows: comma list of oems of slugs bought on that contract), `n_serials_under_contract`.

`gold.contract_coverage_by_oem`: `numerator` = received unit value of PO lines in the trailing
12 months whose header `contract_ref` points to a register row in force at `order_date`
(a reseller PO for Apple devices counts as Apple spend, covered if the reseller contract is in
force); `denominator` = all received unit value of that oem; `spend_direct` / `spend_via_reseller`
split by `supplier_role`; `n_contracts_in_force`; `next_notice_deadline`.

`gold.renewal_calendar_v2` = v0.1 `renewal_calendar` logic applied to `silver.contracts`
(supplier rows only), plus role, category, spend columns, `price_protection_days`,
`claim_window_days` and `price_protection_window_open` = any PO line of the counterparty with a
price change inside `(received, received + price_protection_days]` whose claim window is still
open at `as_of`. `gold.rebate_progress`: for rows with `rebate_tiers_json`: `spend_12m_eur`,
`current_tier_pct`, `next_tier_from_eur`, `next_tier_pct`, `gap_to_next_tier_eur`.

### 8.3 Gold KPIs (`restwert/gold/`)

`registry.py`: `GoldKpiSpec(kpi_id, name, page, definition, formula_text, source_tables, unit,
direction, min_n, owner, fn)`, `GOLD_KPI_REGISTRY: dict[str, GoldKpiSpec]`, `PAGES: tuple = ("0 Data", "1 Purchase", "2 TCO", "3 Residual estimate", "4 Resale", "5 Result", "6 Levers", "7 Contracts")`,
decorator `register_gold(...)` (same validation as v0.1 `kpi.registry.register`), reusing
`kpi.registry` helpers (`load_table`, `to_numeric`, `ratio`, `make_breakdown`, `trailing_window`,
`KpiValue`). `kpis.py` registers exactly these 14:

| kpi_id | page | definition | direction | breakdown |
|---|---|---|---|---|
| KPI_DATA_CHAIN_COMPLETE | 0 Data | serials with `chain_complete` / all serials in `silver.serial_timeline` | up | lifecycle_status, first_missing_step |
| KPI_DATA_UNRESOLVED_SHARE | 0 Data | `sum(n_unresolved) / sum(rows_read)` over `bronze.deliveries` | down | feed, reason_code |
| KPI_DATA_RECONCILED | 0 Data | serials with every `silver.reconciliation` row ok / serials | one | field |
| KPI_PUR_DISCOUNT_VS_RRP | 1 Purchase | `1 - sum(purchase_price) / sum(rrp_net)` over serials received in the trailing 12 months | up | oem, supplier_role |
| KPI_PUR_LANDED_VS_RRP | 1 Purchase | `sum(landed_cost) / sum(rrp_net)`, same window | down | oem |
| KPI_PUR_PRICE_PROTECTION_CAPTURE | 1 Purchase | credited / (credited + missed) price protection value, trailing 12 months | up | oem |
| KPI_TCO_PER_CLOSED_DEVICE | 2 TCO | `sum(tco_eur) / n` over serials closed in the trailing 12 months | down | catalogue_family, oem |
| KPI_TCO_ESTIMATE_SHARE | 2 TCO | `sum(holding_cost_eur) / sum(tco_eur)` over the same serials | down | catalogue_family |
| KPI_RES_ESTIMATE_VS_ANCHOR | 3 Residual estimate | `sum(estimate_rv_lease_end) / sum(anchor_rv_lease_end)` over rented serials with an anchor | one | catalogue_family, oem |
| KPI_RSL_REALISED_VS_RECORD | 4 Resale | `sum(resale_gross) / sum(estimate_rv_of_record)` over non-as-is sales in the trailing 12 months | one | resale_channel, oem |
| KPI_RSL_DAYS_RETURN_TO_CASH | 4 Resale | median `days_return_to_cash` over credited sales in the window | down | resale_channel |
| KPI_RSLT_CLOSED_PER_DEVICE | 5 Result | `sum(lifecycle_result_eur) / n_closed` in the window | up | oem, catalogue_family, term_months |
| KPI_LEV_ADDITIVE_EUR_PA | 6 Levers | `sum(eur_fleet_per_year)` over additive levers | down | lever_id |
| KPI_CTR_COVERAGE_BY_OEM | 7 Contracts | `sum(spend_under_contract) / sum(spend_total)` of `gold.contract_coverage_by_oem` | up | oem |

The count of 14 is pinned by a test. `run.py`:
`run_gold_kpis(con, as_of, targets: KpiTargets) -> RunSummary` writes `gold.kpi_values` and
`gold.kpi_breakdown` (replace for the `as_of`), `owner` = the assumption or threshold owner
named in the spec (CFO for Result and TCO, Head of Procurement for Purchase, Head of Recommerce
for Residual and Resale, Category Manager Hardware for Contracts, "Data owner (name)" for Data);
targets read from `kpi_targets.yaml` `targets` map when a key matches. `catalogue.py`:
`render_gold_catalogue() -> str` and `write_gold_catalogue(path = DOCS_DIR / "GOLD_KPIS.md")`.

### 8.4 `docs/CONTRACTS.md`

Hand-written: the counterparty table of 8.1, the register v2 columns, the coverage definition,
the price protection window vs claim window distinction with the v0.1 mapping (D16), the
renewal calendar v2 columns, and the `TERMS_NOTE` sentence.

### 8.5 Tests

`tests/test_contracts_v2.py`: `test_every_counterparty_name_is_allowed`, `test_contracts_v2_status_and_deadlines`
(hand rows: active, expired, future; notice deadline exact), `test_coverage_by_oem_counts_reseller_spend_for_the_oem`,
`test_renewal_calendar_v2_matches_v01_action_required`, `test_rebate_progress_next_tier`,
`test_conformed_supplier_contracts_keep_r05_and_r06_running` (session lake DB: `decision_log`
has R05 and R06 rows). `tests/test_gold.py`: `test_gold_registry_has_14_kpis_and_pages`,
`test_gold_kpis_not_measurable_never_zero` (empty DB), `test_gold_kpis_on_pipeline_all_ok`
(session lake DB: every status ok except where n < min_n), `test_gold_catalogue_renders_every_id_no_em_dash`.

---

## 9. Module 6: app (CLI, dashboard v0.2, export, README, tests scaffold, allowlist)

Owns: `restwert/cli.py`, `restwert/export.py`, `restwert/dashboard/*`, `README.md`,
`pyproject.toml` version, `tests/conftest.py` (additive fixtures), `tests/fixtures/allowlist.py`,
`tests/test_cli_v2.py`, `tests/test_dashboard_v2.py`, `tests/test_honesty_v2.py`, and the
`__version__` bump.

### 9.1 CLI (`restwert/cli.py`)

Existing commands and flags are untouched (a test pins them). `ALL_ORDER` stays the v0.1 tuple.
New constant and commands:

```python
ALL_ORDER_V2: tuple[str, ...] = ("generate-lake", "ingest", "conform", "forecast", "pnl", "timeline", "ledger", "levers", "decide", "contracts", "kpis", "export")
SMALL_SERIALS = 500
```

| command | flags | entry point |
|---|---|---|
| `generate-lake` | `--seed`, `--serials` (alias `--devices`), `--lake-dir` (default `data/lake`), `--config` (default `config/lake.yaml`), `--catalogue-dir`, `--curves` | `lakegen.run_generate_lake(cfg, seed, n, lake_dir / "raw", ...)`; also writes `<lake_dir>/SYNTHETIC.md` |
| `ingest` | `--source <feed key>` + `--file <path>`, or `--all` (with `--lake-dir`), `--dry-run`, `--db` | `lake.ingest.run_ingest`; prints one report line per file; exit 1 when a file is refused (missing `is_synthetic`, unknown feed key) |
| `conform` | `--as-of`, `--db`, `--csv-dir` (optional compat copies), `--docs/--no-docs` | `lake.conform.run_conform` (+ `feeds.write_data_lake_md` and `schema.write_data_model_md` when docs wanted) |
| `timeline` | `--as-of`, `--db` | `lake.timeline.run_timeline` |
| `ledger` | `--as-of`, `--db` | `ledger.run.run_ledger` |
| `levers` | `--as-of`, `--db`, `--docs/--no-docs` | `levers.run.run_levers` |
| `contracts` | unchanged flags | v0.1 `run_contracts` **then** `contracts.register_v2.run_contracts_v2` when `bronze.ctr_register` exists (note otherwise) |
| `kpis` | unchanged flags | v0.1 `run_kpis` **then** `gold.run.run_gold_kpis` when `silver.device_ledger` exists (note otherwise); `--catalogue` also writes `docs/GOLD_KPIS.md` |
| `export` | unchanged flags plus `--lake` (also write parquet mirrors under `<lake-dir>/{bronze,silver,gold}/`) | `export_all` + `export_lake` + `write_manifest` |
| `all` | unchanged flags plus `--lake-dir` (default: `data/lake` when `--csv-dir` is the default, else `<csv_dir>.parent / "lake"`), `--serials`, `--cadence {yearly,quarterly,monthly}`, `--v01` (run the legacy `ALL_ORDER` chain) | the v0.2 chain below |
| `forecast`, `pnl`, `decide` | unchanged | unchanged, except `_pipeline_cfg(con, args)`: when `bronze.cat_models` exists in the DB, `cfg = load_lake_config(...)` (four families) is passed to `run_forecast` instead of `GeneratorConfig`; `_default_as_of` reads `lake.yaml` `as_of` on a lake DB |

`cmd_all` (v0.2): delete the DB file unless `--keep-db`; `lake_cfg = load_lake_config(args.config if it ends with lake.yaml else LAKE_CONFIG)`;
`n = SMALL_SERIALS if --small else --serials/--devices or lake_cfg.n_devices`; steps through
`_timed`: `generate-lake` -> `ingest --all` (never dry) -> `conform` (with `csv_dir`) ->
`forecast(cfg=lake_cfg)` -> `pnl` -> `timeline` -> `ledger` -> `levers` -> `decide` ->
`contracts` (v1 + v2) -> `kpis` (v1 + gold) -> `export` (out_dir + `--lake` mirrors). Then it
writes `<csv_dir>.parent / "SYNTHETIC.md"` with the lake `SYNTHETIC.md` text (keeps the v0.1
location valid: `data/SYNTHETIC.md`). Summary table as v0.1, plus the line
`serials=<n> lake=<lake_dir> cadence=<c>`. `all --v01` runs exactly the v0.1 `cmd_all` body
(kept as `_cmd_all_v01`).

### 9.2 Export (`restwert/export.py`, additive)

```python
LAKE_EXPORT_TABLES: tuple[str, ...] = ("bronze.deliveries", "bronze.unresolved", "silver.ledger_lines", "silver.device_ledger",
    "silver.serial_timeline", "silver.reconciliation", "silver.contracts", "gold.result_by_cohort", "gold.tco_by_cohort",
    "gold.purchase_by_oem_month", "gold.estimate_vs_anchor", "gold.resale_by_channel_grade", "gold.chain_quality", "gold.ingest_summary",
    "gold.levers_per_device", "gold.levers_by_cohort", "gold.levers_summary", "gold.contract_coverage_by_oem",
    "gold.renewal_calendar_v2", "gold.rebate_progress", "gold.kpi_values", "gold.kpi_breakdown")
def export_lake(con, out_dir: Path, lake_dir: Path | None, fmt: Format = "both") -> list[Path]
```
Into `out_dir` as `<schema>__<table>.csv/.parquet`; when `lake_dir` is given also
`<lake_dir>/<schema>/<table>.parquet`. `write_manifest` gains keys `lake_tables` (row counts) and
`lake_files`; `EXPORT_TABLES` and the v0.1 keys are untouched. A missing lake table is skipped and
listed under `missing_lake_tables`.

### 9.3 Dashboard (`restwert/dashboard/`)

`app.py`: `st.set_page_config` once; the sidebar keeps DB path, `as_of`, KPI snapshot caption,
the governance sentence, the reload button, and **gains the two headline tiles** ("Lifecycle
margin per device", "Residual value forecast error", rendered with `charts.kpi_tile` from
`kpi_values` at `as_of`, placeholder "n/a" when absent) so they appear on every page. Then
`st.warning(SYNTHETIC_BANNER)` when synthetic, the title line, and
`pg = st.navigation({"Cycle": [...], "Engine": [...]}); pg.run()`; after `pg.run()` the v0.1
footer caption (governance + `FOOTER_NOTE`). Pages are `st.Page(fn, title=..., url_path=...)`
built from closures `_page(view_module)` that open a connection, call `view.render(con, as_of)`
inside the v0.1 try/except, and close it. `views/__init__.py` keeps `TABS` verbatim and adds:

```python
CYCLE_PAGES: list[tuple[str, str, object]] = [        # (title, url_path, module)
  ("Realisation", "realisation", market), ("0 Data", "data", cycle.data_page), ("1 Purchase", "purchase", cycle.purchase),
  ("2 TCO", "tco", cycle.tco), ("3 Residual estimate", "residual", cycle.residual), ("4 Resale", "resale", cycle.resale),
  ("5 Result", "result", cycle.result), ("6 Levers", "levers", cycle.levers), ("7 Contracts", "contracts-v2", cycle.contracts_v2)]
ENGINE_PAGES: list[tuple[str, str, object]] = [("Overview", "overview", overview), ("Residual value curves", "curves", curves),
  ("Inventory", "inventory", inventory), ("Decision queue", "decisions", decisions), ("Contracts (v0.1)", "contracts", contracts), ("Export", "export", export)]
```
Every Cycle page (`views/cycle/_common.py` helpers `question(title)`, `tiles(items)`,
`folded(label, frame)`): one question as `st.subheader`, at most four `st.metric` tiles, one
chart above the fold, one table, everything else in collapsed `st.expander`s; a filter row
(catalogue family, oem) under the title defaulting to "all"; EUR without decimals in tiles, two
decimals in tables; every number from an estimate line names the assumption owner in a caption;
a page whose tables are missing shows one `st.info("run python -m restwert all")` and nothing
else. `data.py` gains cached readers (all `(db_path, ...)`): `deliveries`, `unresolved`,
`ingest_summary`, `chain_quality`, `timeline`, `reconciliation_failures`, `device_ledger`,
`ledger_lines(db_path, serial)`, `purchase_by_oem_month`, `tco_by_cohort`, `estimate_vs_anchor`,
`resale_by_channel_grade`, `result_by_cohort(db_path, kind)`, `levers_summary`,
`levers_by_cohort`, `levers_per_device(db_path, lever_id)`, `contracts_v2`, `coverage_by_oem`,
`renewal_calendar_v2`, `rebate_progress`, `gold_kpi_values(db_path, as_of)`,
`gold_kpi_breakdown(db_path, kpi_id, as_of)`, `curves_rows`.

| page | the one question | tiles (max 4) | chart | table | folded |
|---|---|---|---|---|---|
| Realisation | unchanged v0.1 market page: where does the public used market land against launch RRP? | | | | |
| 0 Data | Can we trust the numbers on the next seven pages? | files ingested (last delivery), bronze rows, unresolved rows (KPI_DATA_UNRESOLVED_SHARE), serials with a complete chain (KPI_DATA_CHAIN_COMPLETE) | horizontal bars per feed: rows new, duplicates, unresolved | `gold.ingest_summary` | unresolved rows by reason with `row_json`; `gold.chain_quality` heatmap status x step; reconciliation failures (KPI_DATA_RECONCILED); the source contracts table rendered from `FEEDS` |
| 1 Purchase | What did we pay per device against the net launch RRP, and to whom? | units received (12 m), discount vs RRP (KPI_PUR_DISCOUNT_VS_RRP), landed vs RRP, price protection claimed vs missed EUR | lines: discount vs RRP by oem over purchase month | `gold.purchase_by_oem_month` aggregated per oem and role | per PO line (`silver.device_ledger` purchase block grouped), price changes inside the window, R07 queue rows |
| 2 TCO | What does one device cost us from order to cash, line by line? | TCO per closed device (KPI_TCO_PER_CLOSED_DEVICE), TCO excluding landed, landed share of TCO, estimate share (holding, with the CFO owner caption) | stacked bar per catalogue family (or oem) of mean line magnitude in `LEDGER_ORDER`, estimate lines hatched | `gold.tco_by_cohort` pivot line_type x cohort | serial picker -> `silver.ledger_lines` of one serial with `source_ref`, `allocation_basis`, `is_estimate`; `tco_per_model` (v0.1 plan view) with the note "a plan, not a sum of lines" |
| 3 Residual estimate | What will the rented fleet be worth at lease end? | rented serials, sum estimate at lease end (fleet model), sum anchor at lease end (public ask, upper bound), estimate vs anchor (KPI_RES_ESTIMATE_VS_ANCHOR) | per (family, oem) two bars as share of RRP: fleet model (decides) and anchor (advises, hatched), caption "the fleet model decides, the anchor curve advises; anchor NULL outside the anchor age range" | `gold.estimate_vs_anchor` | per serial: grade_used, grade_source, months at lease end, run_id, estimate_rv_source, anchor group and fit_quality; the v0.1 curves figure for one model |
| 4 Resale | What did we actually get back, and how fast? | sales (12 m), realised vs estimate of record (KPI_RSL_REALISED_VS_RECORD), median days return to cash, credit notes missing | scatter realised vs estimate of record coloured by channel with the identity line | `gold.resale_by_channel_grade` | per sale (grade at inspection, grade out, refurb cost, channel, gross, fees, net, credited_at, days); grading drift declared vs inspected |
| 5 Result | Do we make money per device, and on which cohort? | closed result per device (KPI_RSLT_CLOSED_PER_DEVICE, n in the label), sum closed result (12 m), open fleet "if liquidated today" (sum), open fleet "projected at lease end" (sum); the last two tiles carry their full names and one caption: "two different numbers, neither is a closed result, never add them" | bars: mean closed result per device by the selected cohort kind, coloured by sign, n in hover | `gold.result_by_cohort` with a cohort kind selector (purchase month default); closed columns and open columns in two visually separated blocks, definition text under each | per-serial `silver.device_ledger` (filterable), the formula box (closed, liquidation, projected, the bridge to v0.1) |
| 6 Levers | Where do we tighten, and who owns the screw? | additive levers EUR per year (KPI_LEV_ADDITIVE_EUR_PA), largest single lever, open queue rows acting on levers (R01, R02, R03, R05, R07), lever rows not attributed | horizontal bar of `eur_fleet_per_year` per lever, additive levers one colour, others another | `gold.levers_summary` as is (lever, EUR per device, EUR per year, additive, threshold key and value, owner, rule id, reference sentence) with the sentence "levers are references against named fleet rows and do not add up" | `gold.levers_by_cohort` heatmap lever x oem; top 50 serials per lever with `counterfactual_json`; ADV03 and ADV04 rows |
| 7 Contracts | Which contracts cover our spend, and which need action? | coverage by manufacturer (KPI_CTR_COVERAGE_BY_OEM), contracts ending within 6 months, notice deadlines within 60 days, price protection windows open | `charts.renewal_calendar_figure` on `gold.renewal_calendar_v2` plus a bar of coverage per oem | `silver.contracts` by role (name, category, end, notice deadline, spend planned vs actual, price protection days, claim window, warranty) under the `TERMS_NOTE` banner | rebate progress; SLA fields as text; R05 and R06 queue rows; the counterparty allowlist |

Engine pages are the v0.1 modules unchanged (the v0.1 Contracts tab is listed as "Contracts (v0.1)").

### 9.4 README v0.2 (restructured, module 6)

Sections: 1 What it is (the cycle in seven questions) · 2 Honesty box (synthetic, public
catalogue, role-only partners, no benchmark, AI-assisted disclosure kept) · 3 Install and run
(`python -m restwert all`, measured seconds on the default fleet, `dashboard`) · 4 The data lake
(layers, feeds table short form, `ingest --dry-run`, unresolved, link to DATA_LAKE.md) · 5 The
cycle page by page (0 to 7, one paragraph each with the formula) · 6 Governance in code (rules
R01 to R07, advisories, owners, levers reference the thresholds) · 7 Mock data (catalogue,
calibrated truth, defects, SYNTHETIC.md) · 8 v0.1 engine underneath (compat, reconciliation) ·
9 Limitations (segment launch intensity instead of series steps; holding cost is a rate;
anchor curves are asks; levers do not add up; synthetic realisation is a design consequence) ·
10 Plugging in real feeds · 11 Disclosure and licence. The governance sentence stays verbatim.
No em dash.

### 9.5 Tests

`tests/conftest.py` (additive): session fixture `lake_pipeline_paths` runs
`main(["all", "--small", "--db", ..., "--out", ..., "--csv-dir", <base>/raw_csv, "--lake-dir", <base>/lake])`
once and `lake_pipeline_db` yields a connection. The v0.1 `full_pipeline_paths` fixture is
unchanged and therefore now also runs the v0.2 chain (its `--csv-dir` gets the compat copies
and the lake lands in `<base>/lake`); modules 1 to 5 use `lake_pipeline_db`.

`tests/fixtures/allowlist.py`: `MANUFACTURERS` (from `restwert.lake.common`), `ROLE_ONLY_SUFFIX`,
`def is_allowed(name) -> bool`, `def scan_frame_names(df, columns) -> list[str]` (offenders).

`tests/test_cli_v2.py`: `test_all_v2_default_chain_order` (`ALL_ORDER_V2` exact),
`test_new_subcommands_and_flags` (generate-lake, ingest, conform, timeline, ledger, levers,
`export --lake`, `all --lake-dir --serials --cadence --v01`), `test_all_small_v2_under_25s`
(on `lake_pipeline_paths`), `test_all_default_fleet_under_60s` (marked `slow`, runs
`all --serials 5000` into tmp, asserts `< 60`), `test_every_lake_table_exists_and_populated`
(all `LAKE_DDL` tables exist; `bronze.deliveries`, every transactional bronze table, every silver
table and every gold table have rows), `test_compat_csvs_loadable_by_v01_load`,
`test_ingest_dry_run_cli_prints_counts_and_writes_nothing`, `test_ingest_all_rerun_is_noop`,
`test_export_lake_files_and_manifest`, `test_all_v01_flag_runs_legacy_chain`,
`test_synthetic_md_both_locations`, `test_forecast_rerun_on_lake_db_is_immutable` (four families).

`tests/test_dashboard_v2.py`: `test_dashboard_navigation_has_cycle_and_engine_pages` (AppTest on
`app.py` against `lake_pipeline_paths.db`: no exception, sidebar tiles present, synthetic
warning, governance caption), `test_each_cycle_page_renders_headless` (`at.switch_page(url_path)`
for every `CYCLE_PAGES` entry: no `at.exception`, no `at.error`, at most four metrics on the
page body, exactly one `st.subheader` question), `test_result_page_never_totals_open_and_closed`
(no metric or column label containing "total result").

`tests/test_honesty_v2.py`: `test_no_denylisted_names_in_lake_files_and_bronze` (scan every
landing file of the session lake and every VARCHAR column of every bronze table with
`find_denylisted`), `test_every_supplier_and_counterparty_is_allowed` (bronze `erp_purchase_orders.supplier_name`,
`ctr_register.counterparty_name`, `fin_indirect_spend.supplier_name`, `wms_shipments.carrier_ref`,
`sd_tickets.repair_partner_ref`, `rf_work_orders.partner_ref`), `test_no_em_dash_in_v02_docs_configs_and_landing_headers`
(docs/*.md, config/lake.yaml, README.md, every `#` line of every landing file, `data/lake/SYNTHETIC.md`),
`test_every_landing_file_first_line_and_is_synthetic_column`, `test_no_side_effects_in_new_packages`
(the v0.1 import scan extended to `restwert/lake`, `lakegen`, `ledger`, `levers`, `gold`).

---

## 10. The chain, budgets and acceptance

```
python -m restwert all            # generate-lake -> ingest --all -> conform -> forecast -> pnl -> timeline -> ledger -> levers -> decide -> contracts -> kpis -> export
python -m restwert all --small    # 500 serials, must stay under 25 s (existing test; 20 s until 2026-09-16)
python -m restwert dashboard
python -m restwert ingest --source erp/goods_receipts --file data/lake/raw/erp/goods_receipts/2024-03-31_goods_receipts_001.csv --dry-run
python -m restwert ingest --all --dry-run
```

Budget on the default fleet (5000 serials, quarterly deliveries, about 19 feeds x up to 18
periods = fewer than 300 files, about 250k landing rows, about 200k ledger lines):
generate-lake 6 s, ingest 8 s, conform 2 s, forecast 6 s, pnl 1 s, timeline 1 s, ledger 5 s,
levers 3 s, decide 2 s, contracts 1 s, kpis 2 s, export 3 s: about 40 s, hard limit 60 s
(test). Vectorised pandas and one `write_df` per file or table; no per-row Python in ingest,
ledger sums or levers (per-serial Python is allowed only where v0.1 already does it).

Acceptance (definition of done for the merged build):
1. The 256 v0.1 tests pass unchanged (the only v0.1 test files touched are `conftest.py`, additively).
2. Every new test file of sections 4 to 9 passes; `all --small` under 20 s; `all` under 60 s.
3. `silver.reconciliation` has zero failing rows on the default fleet; `additivity_violations = 0`.
4. `bronze.unresolved` is non-empty on the default fleet (the injected defects) and every reason is from the closed list.
5. Every landing file starts with `# SYNTHETIC DATA` or `# PUBLIC DATA`; every counterparty and supplier name is a catalogue manufacturer or ends with `(role-only)`; the denylist scan of the whole tree plus lake files plus bronze is clean; no em dash in any `.md`, `.py`, `.yaml`, landing header.
6. `docs/DATA_LAKE.md`, `docs/GOLD_KPIS.md`, `docs/DATA_MODEL.md`, `docs/DECISION_RULES.md`, `docs/KPI_CATALOGUE.md` are regenerated by `all` on the default paths and untouched off them.
7. The dashboard renders every Cycle page headless with no error; the Result page shows the two open numbers with their labels and no total.
8. README section 3 states the measured seconds of the default run.

---

## 11. Risks and guards

1. **Synthetic realisation is a design consequence.** The truth is calibrated to public refurbisher asks, hair-cut (0.85) and capped (0.90) by design parameters. Guard: `SYNTHETIC.md` names truth source, haircut, cap and owner per (family, oem); the synthetic banner; the anchor labelled "ask, upper bound"; no test compares synthetic realisation to the public curve as validation (only the sanity band of 5.6).
2. **Launch steps count segment intensity, not series successors** (D10). Guard: `lake.yaml` cadence per segment documented as the observed rate; README limitation; v0.3 item.
3. **Two open numbers read as one.** Guard: separate columns, tiles with full names and a caption, no `result_total` column (test), `projected_label` per serial, v0.1 `margin_if_liquidated_today` kept for continuity.
4. **Estimates masquerade as transactions.** Guard: `is_estimate`, `assumption_key`, `assumption_owner`, `allocation_basis` on every line; `tco_transactional_eur` beside `tco_eur`; the TCO page shows the estimate share with the owner.
5. **Unresolved rows vanish unnoticed.** Guard: `KPI_DATA_UNRESOLVED_SHARE`, the Data page lists them first, one report line per file, injected defects prove the path on every run, a test asserts no bronze serial-level row references a serial outside `erp_goods_receipts`.
6. **Silent overwrite on re-ingest.** Guard: sha256 idempotence, first delivery wins, conflicts to unresolved, a test ingests the same file twice and a changed copy once.
7. **Reconciliation by construction hides a bug on both sides.** Guard: hand-computed fixtures (the ten-line serial, `allocate_cents`, `billing_date` on clamped dates) assert absolute numbers, not only equality.
8. **Lever references reward outliers or thin groups.** Guard: p75 (not max), `lever_reference_min_n`, `is_attributed`, `n_reference` and `reference_source` on every row, p90 beside the mean, no total row, the additivity flag.
9. **VAT confusion.** Guard: one `vat_rate` assumption with owner, `rrp_net_eur` stored, discounts net vs net, DATA_LAKE.md states it in its first paragraph.
10. **Real names.** Guard: manufacturer allowlist = the exact catalogue `oem` strings; every other name ends with `(role-only)`; the denylist scan extended to landing files and bronze; the em-dash test extended to every new doc, config and landing header.
11. **KPI and rule surface growth.** Guard: v0.1 registry frozen at 20 KPIs and R01..R06 counts on the fixture DB (R07 yields 0 there); gold KPIs in their own registry pinned at 14; every new threshold has owner, rationale, valid_from, placeholder flag.
12. **Runtime.** Guard: quarterly cadence default, per-file vectorised typing, one insert per file, the two timing tests, README reports measured seconds.
13. **Parallel build drift.** Guard: the shared foundation (3.1 to 3.4, 3.8) is verbatim; cross-module flow is DuckDB tables with the DDL of 3.8; each module has its own test file and a session fixture on the merged chain; the CLI wires entry points by name.
14. **Timeline completeness depends on status.** Guard: `EXPECTED_STEPS` is a pure table in DATA_LAKE.md with one test per status; a sold device missing `credited_at` is incomplete, a rented device missing eight steps is complete.
15. **The forecaster meets a fourth family.** `model.fit` already fits any family with at least 50 sales and pools the rest (without a tablet dummy, stated). Guard: `assumptions.yaml` and `thresholds.yaml` carry tablet_like placeholders with owners; `charts.FAMILY_COLOURS` gains the colour; `test_forecast_rerun_on_lake_db_is_immutable`.
