"""Source contracts of the data lake: one ``FeedSpec`` per landing feed (SPEC_v0.2 section 4.1).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

A feed is what one source system delivers: its landing columns with types, the
business key, the timestamp that places a row in a delivery period, the parent
keys a row must resolve against (and the reason code it gets when it does not),
and the feeds that must be ingested first. ``FEEDS`` is the single source of
truth for ``restwert.lake.ingest`` and for ``docs/DATA_LAKE.md``
(``write_data_lake_md``): the document is rendered from the code, never typed by
hand, so the two cannot drift apart.

A row that cannot be matched lands in ``bronze.unresolved`` with one of the
closed ``UNRESOLVED_REASONS``; nothing is ever guessed or auto-created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from restwert import GOVERNANCE_PRINCIPLE
from restwert.enums import (
    BuyerType,
    Channel,
    ContractStatus,
    CounterpartyRole,
    DamageType,
    Grade,
    RefurbOutcome,
    SavingType,
    SpendCategory,
    SupplierRole,
)
from restwert.lake import schema_lake
from restwert.lake.common import CATALOGUE_FAMILIES
from restwert.paths import DOCS_DIR

# --------------------------------------------------------------------------- specs


@dataclass(frozen=True)
class ColumnSpec:
    """One landing column: name, type, whether it may be empty, closed list, lower bound."""

    name: str
    dtype: Literal["str", "int", "float", "date", "datetime", "bool"]
    required: bool = True
    enum: tuple[str, ...] | None = None
    min_value: float | None = None


@dataclass(frozen=True)
class FeedSpec:
    """The contract of one feed (see the module docstring)."""

    key: str                                          # "erp/po_lines"
    source_system: str                                # "erp"
    feed: str                                         # "po_lines"
    delivering_system: str                            # prose for the doc
    bronze_table: str                                 # "bronze.erp_po_lines"
    business_key: tuple[str, ...]                     # ("po_number", "po_line")
    serial_column: str | None                         # "serial" or None
    order_column: str | None                          # timestamp placing a row in a delivery; None for reference feeds
    columns: tuple[ColumnSpec, ...]                   # landing columns (is_synthetic excluded; always required)
    resolves: tuple[tuple[str, str, str, str], ...]   # (child col or "a+b", parent bronze table, parent col(s), reason_code)
    soft_refs: tuple[tuple[str, str, str], ...]       # (child col, parent table, parent col): note only, row kept
    depends_on: tuple[str, ...]                       # feed keys ingested first
    is_reference: bool = False                        # public copy, is_synthetic false
    description: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def landing_pattern(self) -> str:
        return f"data/lake/raw/{self.source_system}/{self.feed}/<YYYY-MM-DD>_{self.feed}_<seq:03d>.csv"


UNRESOLVED_REASONS: tuple[str, ...] = (
    "missing_required",
    "bad_type",
    "bad_enum",
    "negative_amount",
    "unknown_serial",
    "unknown_po",
    "unknown_po_line",
    "unknown_contract",
    "unknown_order",
    "unknown_slug",
    "unknown_variant",
    "duplicate_conflict",
)

# Extra WHERE clause on the parent key set of one hard resolve, keyed by (feed key, parent table).
RESOLVE_FILTERS: dict[tuple[str, str], str] = {
    ("erp/po_lines", "bronze.cat_variants"): "rrp_eur_launch_de IS NOT NULL",
}

# --------------------------------------------------------------------------- closed lists

_GRADES = tuple(g.value for g in Grade)
_SUPPLIER_ROLES = tuple(r.value for r in SupplierRole)
_COUNTERPARTY_ROLES = tuple(r.value for r in CounterpartyRole)
_REGISTER_CATEGORIES = ("hardware", "connectivity", "refurbishment", "repair", "logistics", "resale_channel", "financing", "security_software")
_LINE_KINDS = ("unit", "freight", "duty", "price_protection_credit")
_DIRECTIONS = ("outbound", "replacement_out", "return")
_CONTRACT_STATUS = tuple(s.value for s in ContractStatus)
_RESOLUTIONS = ("repair", "replace", "open")
_DAMAGE_TYPES = tuple(d.value for d in DamageType)
_OUTCOMES = tuple(o.value for o in RefurbOutcome)
_CHANNELS = tuple(c.value for c in Channel)
_BUYER_TYPES = tuple(b.value for b in BuyerType)
_SPEND_CATEGORIES = tuple(c.value for c in SpendCategory)
_SAVING_TYPES = tuple(s.value for s in SavingType)


def _c(name: str, dtype: str, required: bool = True, enum: tuple[str, ...] | None = None, min_value: float | None = None) -> ColumnSpec:
    return ColumnSpec(name=name, dtype=dtype, required=required, enum=enum, min_value=min_value)  # type: ignore[arg-type]


def _money(name: str, required: bool = True) -> ColumnSpec:
    return _c(name, "float", required, min_value=0.0)


_GR = "bronze.erp_goods_receipts"

# --------------------------------------------------------------------------- the 19 feeds (insertion order = ingest order)

_FEED_LIST: list[FeedSpec] = [
    FeedSpec(
        key="catalogue/models", source_system="catalogue", feed="models",
        delivering_system="public catalogue copy of data/catalogue/models.csv",
        bronze_table="bronze.cat_models", business_key=("slug",), serial_column=None, order_column=None,
        columns=(
            _c("slug", "str"), _c("model_name", "str"), _c("oem", "str"), _c("family", "str", enum=CATALOGUE_FAMILIES),
            _c("series", "str", False), _c("launch_date_de", "str", False), _c("launch_date_kind", "str", False),
            _c("launch_source_url", "str", False), _c("successor", "str", False), _c("successor_launch_date", "date", False),
            _c("notes", "str", False),
        ),
        resolves=(), soft_refs=(), depends_on=(), is_reference=True,
        description="One row per real model: manufacturer, family, series, German launch date with its source URL. "
                    "launch_date is derived at typing (ISO date, or YYYY-MM takes the 15th).",
    ),
    FeedSpec(
        key="catalogue/variants", source_system="catalogue", feed="variants",
        delivering_system="public catalogue copy of data/catalogue/variants.csv",
        bronze_table="bronze.cat_variants", business_key=("slug", "spec"), serial_column=None, order_column=None,
        columns=(
            _c("slug", "str"), _c("spec", "str"), _c("storage_gb", "int", False), _c("ram_gb", "int", False),
            _money("rrp_eur_launch_de", False), _c("rrp_source_url", "str", False), _c("rrp_source_date", "date", False),
        ),
        resolves=(("slug", "bronze.cat_models", "slug", "unknown_slug"),), soft_refs=(), depends_on=("catalogue/models",),
        is_reference=True,
        description="One row per model and spec with the gross launch RRP (EUR, Germany) and its source URL.",
    ),
    FeedSpec(
        key="market/curves", source_system="market", feed="curves",
        delivering_system="copy of outputs/market_curves.csv (restwert market)",
        bronze_table="bronze.mkt_curves", business_key=("group_kind", "group", "population"), serial_column=None, order_column=None,
        columns=(
            _c("group_kind", "str"), _c("group", "str"), _c("population", "str"), _c("n", "int", False),
            _c("age_min", "float", False), _c("age_max", "float", False), _c("intercept", "float", False),
            _c("slope_per_month", "float", False), _c("monthly_depreciation_pct", "float", False),
            _c("grade_A_offset", "float", False), _c("grade_C_offset", "float", False), _c("grade_D_offset", "float", False),
            _c("mape_in_sample", "float", False), _c("q_12", "float", False), _c("q_24", "float", False), _c("q_36", "float", False),
            _c("fit_quality", "str"),
        ),
        resolves=(), soft_refs=(), depends_on=(), is_reference=True,
        description="Fitted public realisation curves per family and manufacturer (asks, an upper bound); the anchor that advises.",
    ),
    FeedSpec(
        key="contracts/register", source_system="contracts", feed="register",
        delivering_system="contracts register (CLM)",
        bronze_table="bronze.ctr_register", business_key=("contract_id",), serial_column=None, order_column="start_date",
        columns=(
            _c("contract_id", "str"), _c("counterparty_name", "str"), _c("counterparty_role", "str", enum=_COUNTERPARTY_ROLES),
            _c("counterparty_is_public", "bool"), _c("category", "str", enum=_REGISTER_CATEGORIES), _c("start_date", "date"),
            _c("end_date", "date"), _c("notice_days", "int", min_value=0), _c("auto_renewal", "bool"), _c("price_protection", "bool"),
            _c("price_protection_days", "int", False, min_value=0), _c("claim_window_days", "int", False, min_value=0),
            _c("warranty_months", "int", False, min_value=0), _c("rebate_tiers_json", "str", False),
            _c("volume_commitment_units", "int", False, min_value=0), _c("payment_terms_days", "int", min_value=0),
            _c("sla_json", "str", False), _money("spend_under_contract_eur"), _c("terms_note", "str"),
        ),
        resolves=(), soft_refs=(), depends_on=(),
        description="Every contract with a counterparty: manufacturers by name, every other party role-only; terms are placeholders.",
    ),
    FeedSpec(
        key="erp/purchase_orders", source_system="erp", feed="purchase_orders",
        delivering_system="ERP purchase orders",
        bronze_table="bronze.erp_purchase_orders", business_key=("po_number",), serial_column=None, order_column="order_date",
        columns=(
            _c("po_number", "str"), _c("supplier_id", "str"), _c("supplier_name", "str"), _c("supplier_role", "str", enum=_SUPPLIER_ROLES),
            _c("contract_ref", "str", False), _c("order_date", "date"), _c("promised_date", "date"), _c("currency", "str"),
            _c("incoterm", "str", False), _c("payment_terms_days", "int", False, min_value=0),
        ),
        resolves=(), soft_refs=(("contract_ref", "bronze.ctr_register", "contract_id"),), depends_on=("contracts/register",),
        description="PO header per supplier (manufacturer direct or reseller) with the contract it was placed under.",
    ),
    FeedSpec(
        key="erp/po_lines", source_system="erp", feed="po_lines",
        delivering_system="ERP purchase orders",
        bronze_table="bronze.erp_po_lines", business_key=("po_number", "po_line"), serial_column=None, order_column="order_date",
        columns=(
            _c("po_number", "str"), _c("po_line", "int", min_value=1), _c("slug", "str"), _c("storage_gb", "int", min_value=0),
            _c("colour", "str", False), _c("qty_ordered", "int", min_value=1), _money("unit_price_eur"),
            _c("price_protection_days", "int", False, min_value=0), _c("order_date", "date"),
        ),
        resolves=(
            ("po_number", "bronze.erp_purchase_orders", "po_number", "unknown_po"),
            ("slug+storage_gb", "bronze.cat_variants", "slug+storage_gb", "unknown_variant"),
        ),
        soft_refs=(), depends_on=("erp/purchase_orders", "catalogue/variants"),
        description="One line per model and storage on a PO with the net unit price. The landing file carries the header "
                    "order_date so deliveries can be split; it is dropped at bronze. A line must reference a priced catalogue variant.",
    ),
    FeedSpec(
        key="erp/goods_receipts", source_system="erp", feed="goods_receipts",
        delivering_system="ERP / WMS receiving",
        bronze_table="bronze.erp_goods_receipts", business_key=("serial",), serial_column="serial", order_column="received_at",
        columns=(
            _c("gr_number", "str"), _c("po_number", "str"), _c("po_line", "int", min_value=1), _c("serial", "str"),
            _c("received_at", "datetime"), _c("warehouse", "str", False),
        ),
        resolves=(("po_number+po_line", "bronze.erp_po_lines", "po_number+po_line", "unknown_po_line"),),
        soft_refs=(), depends_on=("erp/po_lines",),
        description="Mints the serial: the provider first sees the physical device at receipt. Every later serial-level feed resolves against it.",
    ),
    FeedSpec(
        key="erp/supplier_invoices", source_system="erp", feed="supplier_invoices",
        delivering_system="ERP accounts payable",
        bronze_table="bronze.erp_supplier_invoices", business_key=("invoice_number", "invoice_line"), serial_column="serial",
        order_column="invoice_date",
        columns=(
            _c("invoice_number", "str"), _c("invoice_line", "int", min_value=1), _c("supplier_id", "str"), _c("po_number", "str"),
            _c("po_line", "int", min_value=1), _c("serial", "str", False), _c("invoice_date", "date"),
            _c("line_kind", "str", enum=_LINE_KINDS), _c("qty", "int", min_value=0), _money("amount_eur"), _c("currency", "str"),
        ),
        resolves=(
            ("po_number+po_line", "bronze.erp_po_lines", "po_number+po_line", "unknown_po_line"),
            ("serial", _GR, "serial", "unknown_serial"),
        ),
        soft_refs=(), depends_on=("erp/po_lines", "erp/goods_receipts"),
        description="Unit lines per serial, freight and duty lines per PO line, price protection credits; amount_eur is positive on every kind.",
    ),
    FeedSpec(
        key="erp/price_changes", source_system="erp", feed="price_changes",
        delivering_system="ERP / supplier price lists",
        bronze_table="bronze.erp_price_changes", business_key=("change_id",), serial_column=None, order_column="valid_from",
        columns=(
            _c("change_id", "str"), _c("supplier_id", "str"), _c("slug", "str"), _c("storage_gb", "int", min_value=0),
            _c("valid_from", "date"), _money("old_unit_price_eur"), _money("new_unit_price_eur"),
        ),
        resolves=(("slug+storage_gb", "bronze.cat_variants", "slug+storage_gb", "unknown_variant"),),
        soft_refs=(), depends_on=("catalogue/variants",),
        description="Supplier price list changes per variant; a drop inside the price protection window is claimable.",
    ),
    FeedSpec(
        key="wms/staging_log", source_system="wms", feed="staging_log",
        delivering_system="staging and shipping system",
        bronze_table="bronze.wms_staging_log", business_key=("staging_id",), serial_column="serial", order_column="staged_at",
        columns=(
            _c("staging_id", "str"), _c("serial", "str"), _c("staged_at", "datetime"), _c("mdm_enrolled", "bool"),
            _money("staging_cost_eur"),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),), soft_refs=(), depends_on=("erp/goods_receipts",),
        description="Device staging (enrolment, configuration) before shipment with its cost.",
    ),
    FeedSpec(
        key="wms/shipments", source_system="wms", feed="shipments",
        delivering_system="staging and shipping system",
        bronze_table="bronze.wms_shipments", business_key=("shipment_id",), serial_column="serial", order_column="shipped_at",
        columns=(
            _c("shipment_id", "str"), _c("serial", "str"), _c("related_serial", "str", False), _c("direction", "str", enum=_DIRECTIONS),
            _c("shipped_at", "datetime"), _c("delivered_at", "datetime", False), _c("rental_contract_ref", "str", False),
            _c("carrier_ref", "str"), _money("cost_eur"),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"), ("related_serial", _GR, "serial", "unknown_serial")),
        soft_refs=(("rental_contract_ref", "bronze.portal_rental_contracts", "contract_id"),),
        depends_on=("erp/goods_receipts",),
        description="Outbound, replacement and return shipments with cost; replacement_out: serial = the spare, related_serial = the damaged device.",
    ),
    FeedSpec(
        key="portal/rental_contracts", source_system="portal", feed="rental_contracts",
        delivering_system="customer portal",
        bronze_table="bronze.portal_rental_contracts", business_key=("contract_id",), serial_column="serial", order_column="start_date",
        columns=(
            _c("contract_id", "str"), _c("customer_id", "str"), _c("serial", "str"), _c("start_date", "date"),
            _c("term_months", "int", min_value=1), _money("monthly_rate_eur"), _c("end_date", "date"), _c("actual_end_date", "date", False),
            _c("status", "str", enum=_CONTRACT_STATUS), _c("replaces_contract_id", "str", False),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),),
        soft_refs=(("replaces_contract_id", "bronze.portal_rental_contracts", "contract_id"),),
        depends_on=("erp/goods_receipts",),
        description="Rental contract per serial: term, monthly rate, planned and actual end, status.",
    ),
    FeedSpec(
        key="portal/rental_invoices", source_system="portal", feed="rental_invoices",
        delivering_system="customer portal billing",
        bronze_table="bronze.portal_rental_invoices", business_key=("invoice_id",), serial_column="serial", order_column="invoice_date",
        columns=(
            _c("invoice_id", "str"), _c("contract_id", "str"), _c("serial", "str"), _c("period_no", "int", min_value=1),
            _c("period_month", "date"), _c("invoice_date", "date"), _money("amount_eur"),
        ),
        resolves=(
            ("contract_id", "bronze.portal_rental_contracts", "contract_id", "unknown_contract"),
            ("serial", _GR, "serial", "unknown_serial"),
        ),
        soft_refs=(), depends_on=("portal/rental_contracts", "erp/goods_receipts"),
        description="One invoice per billed month and contract; the rental revenue lines of the ledger.",
    ),
    FeedSpec(
        key="servicedesk/tickets", source_system="servicedesk", feed="tickets",
        delivering_system="incident and repair tickets",
        bronze_table="bronze.sd_tickets", business_key=("ticket_id",), serial_column="serial", order_column="opened_at",
        columns=(
            _c("ticket_id", "str"), _c("serial", "str"), _c("contract_id", "str", False), _c("opened_at", "datetime"),
            _c("closed_at", "datetime", False), _c("damage_type", "str", enum=_DAMAGE_TYPES), _c("resolution", "str", enum=_RESOLUTIONS),
            _money("quote_eur"), _money("repair_cost_eur", False), _c("replacement_serial", "str", False), _c("repair_partner_ref", "str"),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),),
        soft_refs=(("contract_id", "bronze.portal_rental_contracts", "contract_id"), ("replacement_serial", _GR, "serial")),
        depends_on=("erp/goods_receipts",),
        description="Damage tickets with quote, resolution (repair, replace, open) and repair cost.",
    ),
    FeedSpec(
        key="returns/receipts", source_system="returns", feed="receipts",
        delivering_system="returns desk with grading and wipe certificate",
        bronze_table="bronze.ret_receipts", business_key=("receipt_id",), serial_column="serial", order_column="returned_at",
        columns=(
            _c("receipt_id", "str"), _c("serial", "str"), _c("contract_id", "str", False), _c("returned_at", "datetime"),
            _c("grade_declared", "str", enum=_GRADES), _c("grade_inspected", "str", enum=_GRADES), _c("inspected_at", "datetime"),
            _c("wipe_certificate_id", "str", False), _c("wiped_at", "datetime", False), _money("wipe_grading_cost_eur"),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),),
        soft_refs=(("contract_id", "bronze.portal_rental_contracts", "contract_id"),),
        depends_on=("erp/goods_receipts",),
        description="Return receipt with declared and inspected grade, wipe certificate and the wipe and grading cost.",
    ),
    FeedSpec(
        key="refurb/work_orders", source_system="refurb", feed="work_orders",
        delivering_system="refurbishment partner work orders",
        bronze_table="bronze.rf_work_orders", business_key=("work_order_id",), serial_column="serial", order_column="started_at",
        columns=(
            _c("work_order_id", "str"), _c("serial", "str"), _c("started_at", "datetime"), _c("finished_at", "datetime"),
            _money("cost_eur"), _c("grade_out", "str", enum=_GRADES), _c("outcome", "str", enum=_OUTCOMES), _c("partner_ref", "str"),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),), soft_refs=(), depends_on=("erp/goods_receipts",),
        description="Refurbishment work order with cost, grade out and outcome (sellable, as_is, scrap).",
    ),
    FeedSpec(
        key="recommerce/orders", source_system="recommerce", feed="orders",
        delivering_system="resale order system per channel",
        bronze_table="bronze.rc_orders", business_key=("order_id",), serial_column="serial", order_column="sold_at",
        columns=(
            _c("order_id", "str"), _c("serial", "str"), _c("channel", "str", enum=_CHANNELS), _c("listed_at", "datetime"),
            _c("sold_at", "datetime"), _money("gross_price_eur"), _c("buyer_type", "str", enum=_BUYER_TYPES),
            _c("grade_at_sale", "str", enum=_GRADES),
        ),
        resolves=(("serial", _GR, "serial", "unknown_serial"),), soft_refs=(), depends_on=("erp/goods_receipts",),
        description="Resale order per serial: channel, listing and sale timestamps, gross price, buyer type, grade at sale.",
    ),
    FeedSpec(
        key="recommerce/credit_notes", source_system="recommerce", feed="credit_notes",
        delivering_system="channel settlement",
        bronze_table="bronze.rc_credit_notes", business_key=("credit_note_id",), serial_column="serial", order_column="credited_at",
        columns=(
            _c("credit_note_id", "str"), _c("order_id", "str"), _c("serial", "str"), _c("channel", "str", enum=_CHANNELS),
            _c("credited_at", "datetime"), _money("gross_eur"), _money("fee_pct_eur"), _money("fee_fixed_eur"), _money("net_eur"),
        ),
        resolves=(("order_id", "bronze.rc_orders", "order_id", "unknown_order"), ("serial", _GR, "serial", "unknown_serial")),
        soft_refs=(), depends_on=("recommerce/orders", "erp/goods_receipts"),
        description="Channel settlement per order: gross, percentage fee, fixed fee, net, credit date (cash).",
    ),
    FeedSpec(
        key="finance/indirect_spend", source_system="finance", feed="indirect_spend",
        delivering_system="accounts payable (indirect)",
        bronze_table="bronze.fin_indirect_spend", business_key=("spend_id",), serial_column=None, order_column="invoice_date",
        columns=(
            _c("spend_id", "str"), _c("invoice_date", "date"), _c("category", "str", enum=_SPEND_CATEGORIES), _c("supplier_name", "str"),
            _money("amount_eur"), _c("has_po", "bool"), _c("has_contract", "bool"), _money("saving_eur"),
            _c("saving_confirmed_by_controlling", "bool"), _c("saving_type", "str", False, enum=_SAVING_TYPES),
            _money("baseline_amount_eur", False),
        ),
        resolves=(), soft_refs=(), depends_on=(),
        description="Indirect invoice lines (v0.1 shape with _eur suffixes) for the procurement KPIs.",
    ),
]

FEEDS: dict[str, FeedSpec] = {f.key: f for f in _FEED_LIST}
INGEST_ORDER: tuple[str, ...] = tuple(FEEDS)
FEEDS_BY_TABLE: dict[str, FeedSpec] = {f.bronze_table: f for f in _FEED_LIST}

assert len(FEEDS) == 19, "the lake has 19 feeds"

# --------------------------------------------------------------------------- landing file names

_NAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<feed>[a-z_]+)_(?P<seq>\d{3})\.csv$")


def landing_file_name(feed: str, delivered_on: date, seq: int) -> str:
    """``<YYYY-MM-DD>_<feed>_<seq:03d>.csv``."""
    return f"{delivered_on:%Y-%m-%d}_{feed}_{seq:03d}.csv"


def landing_path(raw_dir: Path, spec: FeedSpec, delivered_on: date, seq: int) -> Path:
    """``raw_dir/<system>/<feed>/<name>``."""
    return Path(raw_dir) / spec.source_system / spec.feed / landing_file_name(spec.feed, delivered_on, seq)


def parse_landing_name(name: str) -> tuple[date, str, int]:
    """Inverse of ``landing_file_name``; raises ``ValueError`` on any other name."""
    m = _NAME_RE.match(Path(name).name)
    if not m:
        raise ValueError(f"{name!r} is not a landing file name (<YYYY-MM-DD>_<feed>_<seq:03d>.csv)")
    return date.fromisoformat(m.group("date")), m.group("feed"), int(m.group("seq"))


# --------------------------------------------------------------------------- docs/DATA_LAKE.md

_LAYER_RULES = """\
```
landing files (raw, on disk)  --ingest-->  bronze.*  (typed, deduplicated, source-shaped)
bronze.*  --conform-->  main.<ten v0.1 source tables>  (compat)  --v0.1 chain-->  forecast, device_pnl, decisions, kpis, contracts
bronze.*  --ledger-->   silver.ledger_lines, silver.serial_timeline, silver.device_ledger, silver.reconciliation
silver.*  --levers/contracts/gold-->  gold.*  (cohorts, levers, coverage, KPIs)
```

* **raw**: files on disk under `data/lake/raw/<system>/<feed>/`, exactly as a source system would export them,
  one file per delivery, never modified after landing. A correction is a new delivery. The raw layer is not a
  DuckDB schema; every file is registered in `bronze.deliveries` by its sha256. The only thing that ever removes
  a landing file is a regeneration of the synthetic fleet (`generate-lake`, `all`), and it removes only files
  whose first line is `# SYNTHETIC DATA` or `# PUBLIC DATA`; a real export in a feed folder blocks the
  regeneration by name unless `--wipe-raw` says otherwise.
* **bronze**: typed, deduplicated, source-shaped rows in the DuckDB schema `bronze` of the one file
  `data/restwert.duckdb`. Table names carry a source prefix (`erp_`, `wms_`, `portal_`, `sd_`, `ret_`, `rf_`,
  `rc_`, `ctr_`, `fin_`, `cat_`, `mkt_`) so nothing collides with the v0.1 tables in `main`. Every row ends with
  the tail `delivery_id, source_file, row_number, ingested_at, row_hash, is_synthetic`.
* **silver**: the device ledger. `silver.ledger_lines` holds one signed EUR line per transaction
  (`serial, line_type, amount_eur, event_date, source_ref`; revenue positive, cost negative, result = `SUM(amount_eur)`),
  `silver.serial_timeline` the ten timestamps of the cycle, `silver.device_ledger` one wide row per serial,
  `silver.reconciliation` the proof that ledger and `device_pnl` agree to the cent, `silver.contracts` the register v2.
* **gold**: KPIs, curves, cohorts and lever tables, rebuilt on every run.

Keys:

* **serial** is minted by `erp/goods_receipts` (the provider first sees the physical device at receipt). Every later
  serial-level feed resolves against `bronze.erp_goods_receipts.serial`; a serial not there is `unknown_serial` in
  `bronze.unresolved`, never auto-created.
* **(source_system, external_ref)** is the business key of every bronze row (`FeedSpec.business_key`).
  `silver.ledger_lines.source_ref = "<system>:<external_ref>"` so any EUR line traces to one bronze row, and that
  bronze row to one landing file line (`source_file`, `row_number`).
* Reference feeds (`catalogue/*`, `market/curves`) are public data copied into the lake with `is_synthetic = false`
  and their URLs; the fleet that references them is synthetic.

Cycle per serial: ordered -> received -> staged -> shipped (rented) -> [tickets, replacements] -> returned -> wiped
-> graded -> [refurbished] sellable -> sold -> credited. Each stage is a timestamp in `silver.serial_timeline`; each
EUR is a ledger line; the cycle closes when the device is sold or scrapped.
"""

_VAT = """\
The public catalogue RRP (`bronze.cat_variants.rrp_eur_launch_de`) is **gross** (EUR, Germany). Every ledger amount,
every purchase price and every discount is **net**: `rrp_net_eur = round(rrp_eur_launch_de / (1 + vat_rate), 2)` with
`vat_rate` the owned assumption block in `config/assumptions.yaml` (0.19, owner CFO). Discounts are net against net.
"""

_LANDING = """\
* Path `data/lake/raw/<source_system>/<feed>/<YYYY-MM-DD>_<feed>_<seq:03d>.csv`; the date is the delivery date, the
  sequence number separates several files of one day.
* Line 1: `# SYNTHETIC DATA - restwert generate-lake seed=<seed> feed=<key> delivery=<date>` (generated) or
  `# PUBLIC DATA - copy of <path>, every row carries its source URL` (catalogue copies). A real export has no `#`
  line and `is_synthetic=false` on every row.
* Header, then rows; ISO dates `YYYY-MM-DD`, ISO timestamps `YYYY-MM-DDTHH:MM:SS`, decimal point, `true` / `false`.
* `is_synthetic` is mandatory; the reader refuses a file without it (the v0.1 rule) and a reference feed must carry `false`.
* Never modified after landing; a correction is a new delivery.
"""

_INGEST = """\
`python -m restwert ingest --source <feed key> --file <path> [--dry-run]` loads one landing file, `ingest --all`
walks `data/lake/raw` in feed order and then by file name. One DuckDB transaction per file; a dry run rolls it back
and leaves nothing behind, not even a `runs` row. One report line per file:
`erp/goods_receipts 2023-12-31_goods_receipts_002.csv read=1210 new=1198 dup=8 conflict=1 unresolved=3 (unknown_po_line=3)`.

1. sha256 the file. When the digest is already in `bronze.deliveries.sha256` the file is reported as already
   ingested with its stored counts; a rerun of `ingest --all` is a no-op.
2. Read every column as text, skip leading `#` lines, parse `is_synthetic` (one uniform value per file).
3. Type every column, vectorised: missing required -> `missing_required`; a value that does not parse as its type ->
   `bad_type`; a value outside the closed list -> `bad_enum`; a money value below zero -> `negative_amount`. A rejected
   row carries every reason it hit in `reason_text` and the first one as `reason_code`; it never aborts the file.
4. Resolve keys: every hard reference of the feed is checked against the parent bronze table (plus keys inside the
   same file). A miss goes to `bronze.unresolved` with the feed's reason code. Soft references only add a note.
5. Deduplicate by `row_hash` over the content columns: the same business key already in bronze with the same hash is
   an identical duplicate (skipped, counted); the same key with a different hash is a `duplicate_conflict` in
   `bronze.unresolved` and **the existing row stays** (first delivery wins). Duplicates inside one file follow the same rule.
6. Append the new rows with the tail columns; insert the unresolved rows and the `bronze.deliveries` row.

`bronze.unresolved` is a first-class table: the Data page shows it first, `KPI_DATA_UNRESOLVED_SHARE` measures it
(typing and key resolution only; conflicting duplicates are counted as duplicates in `bronze.deliveries`, not as
unresolved rows), and nothing downstream ever guesses a missing key. Its key is (delivery, row number, reason): a
source row that a system re-delivers in a NEW file (a different sha256) and that still cannot be resolved is recorded
again under the new delivery, so the table and the Data page list such a row once per delivery that carried it; the
same file twice is a no-op and adds nothing. Counting distinct source rows would need a source-side key that not
every feed carries, so the count stays per delivery and says so.
"""

_CONFORM = """\
`python -m restwert conform` materialises the ten v0.1 source tables in `main` from bronze with a deterministic
mapping, so the v0.1 forecast, P&L, decision, KPI and contract engines run unchanged underneath v0.2 (decision D2).
`device_pnl` stays written by the v0.1 P&L; `silver.reconciliation` proves that ledger and `device_pnl` agree to the cent.

* `model_catalogue`: one row per slug with a launch date and at least one priced variant; `model = slug`,
  `model_family = fleet_family(family, oem)` (Smartphone+Apple -> iphone_like, Smartphone+other -> android_like,
  Tablet -> tablet_like, Laptop -> laptop_like), `generation` = dense rank of the launch date within (oem, series),
  `list_price = rrp_net(min priced variant RRP)`, `base_storage_gb` = storage of that variant. Slugs without a launch
  date or without a priced variant are excluded and listed in `data/lake/SYNTHETIC.md`.
* `devices`: one row per goods receipt; `purchase_date = received_at`, `purchase_price` = the unit invoice line of the
  serial when it exists, else the PO line price; `landed_cost = purchase_price + freight share + duty share`, where each
  freight and duty invoice line of the PO line is split over the received serials of that line to the cent
  (`allocate_cents`: floor per unit, remainder on the last serial in serial order; the ledger uses the same call);
  `supplier = supplier_name`, `channel_in` manufacturer -> `oem_direct`, reseller -> `distributor`,
  `po_number = "<po_number>-<po_line>"`, `contract_id` = earliest portal contract of the serial.
* `purchase_orders`: one row per PO line (composite `po_number`), `supplier_contract_id = contract_ref` when that
  register row is in force at `order_date`, `delivered_date = max(received_at)`, `qty_delivered` = receipts,
  `benchmark_price = round(rrp_net x (1 - purchase_discount_pct), 2)` (a labelled assumption placeholder),
  `price_drop_date` / `price_drop_amount` = the first price change of (supplier, slug, storage) with `valid_from` in
  `(delivered_date, delivered_date + price_protection_days]`, amount = old - new.
* `benchmarks`: per slug `valid_from = launch_date`, `landed_cost_benchmark = round(benchmark_price x 1.015, 2)`,
  `source_note` names the placeholder and points at the URL in `bronze.cat_variants`.
* `rental_contracts`: column for column from `portal_rental_contracts` (`monthly_rate = monthly_rate_eur`).
* `events`: tickets become a damage row `EV-T-<ticket>-D` at `opened_at` (cost = quote when open, else 0, resolved =
  resolution != open), a repair row `EV-T-<ticket>-R` at `closed_at` with the repair cost, a replacement row
  `EV-T-<ticket>-X` at the `replacement_out` shipment of the damaged device with that shipment's cost; receipts become
  a return row `EV-R-<receipt>` at `returned_at` with the `return` shipment's cost, declared and inspected grade and
  `wipe_certificate = wipe_certificate_id IS NOT NULL`.
* `refurbishment` from work orders, `resale` from orders (`fees` = the credit note's percentage plus fixed fee when
  one exists, else the channel fee assumption), `supplier_contracts` from register rows with a supplier role
  (`price_protection_days = claim_window_days`, the v0.1 meaning), `indirect_spend` renamed column for column.

Every conformed row carries `is_synthetic` = the fleet flag (the catalogue rows are public one join away; the fleet
that references them is synthetic) and `source_file = "bronze.<table>"`. With `--csv-dir` the ten tables are also
written as `<csv_dir>/<table>.csv` compat copies that `python -m restwert load` reads unchanged.
"""

_TIMELINE_INTRO = """\
`python -m restwert timeline` writes `silver.serial_timeline`: one row per serial with the ten dates of the cycle
(only events on or before `as_of`) and a completeness verdict. Sources: `ordered_at` = the header order date of the
receipt's PO; `received_at`; `staged_at` = first staging; `shipped_at` = first outbound or replacement shipment where
the serial is the shipped device; `returned_at` = latest receipt; `wiped_at` and `graded_at` (= `inspected_at`) of that
receipt; `sellable_at` = `finished_at` of the latest work order whose outcome is not scrap; `sold_at`; `credited_at`.

The expected steps depend on the lifecycle status of `device_pnl` (every status is `not_deployed` when the P&L has
not run yet, and the run summary says so):
"""

_TIMELINE_OUTRO = """\
`steps_present` counts the expected steps that are present, `missing_steps` lists the rest, `is_monotonic` is true
when every present date is on or after the previous present date in cycle order (`non_monotonic_pair` names the first
violation), and `chain_complete = steps_present == steps_expected AND is_monotonic`. `source_refs_json` maps every
present step to the bronze row it came from. `gold.chain_quality` reports, per status and step, the share of serials
with the step present; `KPI_DATA_CHAIN_COMPLETE` is the share of serials with a complete chain.
"""


def _feed_section(spec: FeedSpec) -> list[str]:
    out: list[str] = []
    out.append(f"### `{spec.key}`")
    out.append("")
    if spec.description:
        out.append(spec.description)
        out.append("")
    out.append(f"* Delivering system: {spec.delivering_system}")
    out.append(f"* Landing file: `{spec.landing_pattern}`")
    out.append(f"* Bronze table: `{spec.bronze_table}`")
    out.append(f"* Business key: {', '.join(f'`{c}`' for c in spec.business_key)}")
    order = f"`{spec.order_column}`" if spec.order_column else "none (reference feed, dated as_of)"
    out.append(f"* Order column (places a row in a delivery period): {order}")
    out.append(f"* Serial column: {'`' + spec.serial_column + '`' if spec.serial_column else 'none'}")
    out.append(f"* Reference feed (public copy, `is_synthetic = false`): {'yes' if spec.is_reference else 'no'}")
    out.append(f"* Depends on: {', '.join(f'`{d}`' for d in spec.depends_on) if spec.depends_on else 'nothing'}")
    if spec.resolves:
        out.append("* Resolves (hard, a miss goes to `bronze.unresolved`):")
        for child, parent, pcol, reason in spec.resolves:
            extra = RESOLVE_FILTERS.get((spec.key, parent))
            flt = f" where `{extra}`" if extra else ""
            out.append(f"  * `{child}` -> `{parent}.{pcol}`{flt}: reason `{reason}`")
    else:
        out.append("* Resolves: nothing (top-level feed)")
    if spec.soft_refs:
        out.append("* Soft references (checked, reported as a note, row kept):")
        for child, parent, pcol in spec.soft_refs:
            out.append(f"  * `{child}` -> `{parent}.{pcol}`")
    out.append("")
    out.append("| Column | Type | Required | Closed list | Minimum |")
    out.append("|---|---|---|---|---|")
    for c in spec.columns:
        enum = ", ".join(c.enum) if c.enum else ""
        mn = "" if c.min_value is None else f"{c.min_value:g}"
        out.append(f"| `{c.name}` | {c.dtype} | {'yes' if c.required else 'no'} | {enum} | {mn} |")
    out.append("| `is_synthetic` | bool | yes | true, false | |")
    dropped = [c.name for c in spec.columns if c.name not in _bronze_columns(spec.bronze_table)]
    if dropped:
        out.append("")
        out.append(f"Landing-only columns dropped at bronze: {', '.join(f'`{d}`' for d in dropped)}.")
    out.append("")
    out.append("```sql")
    out.append(schema_lake.LAKE_DDL[spec.bronze_table].strip())
    out.append("```")
    out.append("")
    return out


def _bronze_columns(table: str) -> set[str]:
    """Column names of a bronze DDL statement (constraints excluded)."""
    from restwert.schema import _ddl_columns

    return {name for name, _ in _ddl_columns(schema_lake.LAKE_DDL[table])}


def render_data_lake_md() -> str:
    """Render ``docs/DATA_LAKE.md`` from ``FEEDS`` and ``schema_lake`` (generated file, never hand-edited)."""
    from restwert import __version__
    from restwert.lake.timeline import EXPECTED_STEPS, STEPS

    lines: list[str] = []
    lines.append("# Restwert Engine: data lake")
    lines.append("")
    lines.append(
        f"GENERATED by `restwert.lake.feeds.write_data_lake_md` from `restwert/lake/feeds.py` and "
        f"`restwert/lake/schema_lake.py` (package version {__version__}). Do not edit by hand."
    )
    lines.append("")
    lines.append(f"> {GOVERNANCE_PRINCIPLE}")
    lines.append("")
    lines.append("## 1. VAT convention (read this first)")
    lines.append("")
    lines.append(_VAT)
    lines.append("## 2. Layers, keys and the closed cycle")
    lines.append("")
    lines.append(_LAYER_RULES)
    lines.append("## 3. Landing file format")
    lines.append("")
    lines.append(_LANDING)
    lines.append("## 4. Ingest")
    lines.append("")
    lines.append(_INGEST)
    lines.append("## 5. Feeds (source contracts)")
    lines.append("")
    lines.append("Ingest order is the order of this list. A row that cannot be matched lands in `bronze.unresolved` with one of the closed reasons: "
                 + ", ".join(f"`{r}`" for r in UNRESOLVED_REASONS) + ".")
    lines.append("")
    lines.append("| Feed | Delivering system | Bronze table | Business key | Order column | Depends on |")
    lines.append("|---|---|---|---|---|---|")
    for spec in FEEDS.values():
        lines.append(
            f"| `{spec.key}` | {spec.delivering_system} | `{spec.bronze_table}` | {', '.join(spec.business_key)} | "
            f"{spec.order_column or 'none'} | {', '.join(spec.depends_on) or ''} |"
        )
    lines.append("")
    for spec in FEEDS.values():
        lines.extend(_feed_section(spec))
    lines.append("## 6. Conform (bronze to the ten v0.1 tables)")
    lines.append("")
    lines.append(_CONFORM)
    lines.append("## 7. Timeline (the timestamp chain per serial)")
    lines.append("")
    lines.append(_TIMELINE_INTRO)
    lines.append("| lifecycle_status | expected steps |")
    lines.append("|---|---|")
    for status, steps in EXPECTED_STEPS.items():
        lines.append(f"| `{status}` | {', '.join(f'`{s}`' for s in steps)} |")
    lines.append("")
    lines.append("Cycle order of the steps: " + " -> ".join(f"`{s}`" for s in STEPS) + ".")
    lines.append("")
    lines.append(_TIMELINE_OUTRO)
    lines.append("## 8. Registry, silver and gold tables (DDL)")
    lines.append("")
    lines.append("`bronze.deliveries` registers every ingested file, `bronze.unresolved` every row that could not be matched.")
    lines.append("")
    lines.append(schema_lake.render_lake_ddl_md())
    text = "\n".join(lines).rstrip() + "\n"
    assert chr(0x2014) not in text, "no em dash in generated prose"
    return text


def write_data_lake_md(path: Path = DOCS_DIR / "DATA_LAKE.md") -> Path:
    """Write ``docs/DATA_LAKE.md`` as UTF-8 without BOM, LF line endings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_data_lake_md())
    return path


__all__ = [
    "ColumnSpec",
    "FeedSpec",
    "FEEDS",
    "FEEDS_BY_TABLE",
    "INGEST_ORDER",
    "UNRESOLVED_REASONS",
    "RESOLVE_FILTERS",
    "landing_file_name",
    "landing_path",
    "parse_landing_name",
    "render_data_lake_md",
    "write_data_lake_md",
]
