"""Landing files, manifest and SYNTHETIC.md of the lake generator (SPEC_v0.2 section 5.5).

Landing format (decision D3, section 4.2): one file per (feed, delivery period) at
``<raw_dir>/<source_system>/<feed>/<YYYY-MM-DD>_<feed>_<seq:03d>.csv``; line 1 is
``# SYNTHETIC DATA - restwert generate-lake seed=<seed> feed=<key> delivery=<date>``
(``# PUBLIC DATA - copy of <path>, every row carries its source URL`` for the three
reference feeds); then the header and the rows: ISO dates, ISO timestamps
``YYYY-MM-DDTHH:MM:SS``, decimal point, ``true`` / ``false``, ``is_synthetic`` last.
UTF-8 without BOM, LF, never modified after landing.

Which delivery a row lands in: the period containing its ``order_column``, bumped to
the period containing the row's latest known timestamp when that is later (a source
system exports a row once the row is complete: a shipment with its delivery time, a
ticket with its closing time, a receipt with its inspection). A row whose latest
timestamp is after ``as_of`` is never written. Planned future dates (``end_date``,
``promised_date``, ``successor_launch_date``) do not count. Injected duplicates carry a
hidden ``_delivery_shift`` that moves the copy one period later. Reference feeds and
the contracts register are one delivery dated ``as_of``, seq 001.

Leakage rule (tested): every timestamp of every row in a file dated ``delivered_on``
is on or before ``delivered_on``, the planned future dates excepted; reference feeds
are copies of public files and carry source dates, not events, so the rule is not
applied to them.

The column lists below mirror the bronze DDL of ``restwert/lake/schema_lake.py`` minus
the tail columns (``erp/po_lines`` carries the header ``order_date`` in addition, for
the delivery split; bronze drops it). ``tests/test_lakegen.py`` checks them against
``restwert.lake.feeds.FEEDS`` when that module is importable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from restwert import __version__
from restwert.dates import add_months, month_floor
from restwert.lake.common import MANUFACTURERS, ROLE_ONLY_SUFFIX
from restwert.lakegen.config import LakeConfig

if TYPE_CHECKING:  # pragma: no cover
    from restwert.lakegen import World

SYNTHETIC_SENTENCE = (
    "Every number is a synthetic design parameter from config/lake.yaml; the catalogue and the anchor curves "
    "are public; no market benchmark, no customer, no supplier beyond the public manufacturer names, no employer is real."
)
PLANNED_FUTURE_COLUMNS: tuple[str, ...] = ("end_date", "promised_date", "successor_launch_date")
MANIFEST_NAME = "_manifest.json"
CADENCE_MONTHS: dict[str, int] = {"monthly": 1, "quarterly": 3, "yearly": 12}


@dataclass(frozen=True)
class FeedLanding:
    """Landing shape of one feed: path parts, typed columns, the delivery column."""

    key: str
    source_system: str
    feed: str
    columns: tuple[tuple[str, str], ...]     # (name, dtype) with dtype in str|int|float|money|date|datetime|bool
    order_column: str | None
    is_reference: bool = False
    public_source: str = ""                   # repository path of the public file a reference feed copies
    public_note: str = "every row carries its source URL"
    single_delivery: bool = False             # one delivery dated as_of (reference feeds and the register)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.columns)

    @property
    def timestamp_columns(self) -> tuple[str, ...]:
        return tuple(n for n, t in self.columns if t in ("date", "datetime") and n not in PLANNED_FUTURE_COLUMNS)


def _f(key: str, columns: list[tuple[str, str]], order_column: str | None, **kw) -> FeedLanding:
    system, feed = key.split("/", 1)
    single = kw.pop("single_delivery", None)
    if single is None:
        single = bool(kw.get("is_reference", False)) or order_column is None
    return FeedLanding(key=key, source_system=system, feed=feed, columns=tuple(columns), order_column=order_column,
                       single_delivery=single, **kw)


FEEDS: dict[str, FeedLanding] = {
    f.key: f
    for f in (
        _f("catalogue/models", [
            ("slug", "str"), ("model_name", "str"), ("oem", "str"), ("family", "str"), ("series", "str"),
            ("launch_date_de", "str"), ("launch_date_kind", "str"), ("launch_source_url", "str"), ("successor", "str"),
            ("successor_launch_date", "str"), ("notes", "str"),
        ], None, is_reference=True, public_source="data/catalogue/models.csv"),
        _f("catalogue/variants", [
            ("slug", "str"), ("spec", "str"), ("storage_gb", "str"), ("ram_gb", "str"), ("rrp_eur_launch_de", "str"),
            ("rrp_source_url", "str"), ("rrp_source_date", "str"),
        ], None, is_reference=True, public_source="data/catalogue/variants.csv"),
        _f("market/curves", [
            ("group_kind", "str"), ("group", "str"), ("population", "str"), ("n", "str"), ("age_min", "str"),
            ("age_max", "str"), ("intercept", "str"), ("slope_per_month", "str"), ("monthly_depreciation_pct", "str"),
            ("grade_A_offset", "str"), ("grade_C_offset", "str"), ("grade_D_offset", "str"), ("mape_in_sample", "str"),
            ("q_12", "str"), ("q_24", "str"), ("q_36", "str"), ("fit_quality", "str"),
        ], None, is_reference=True, public_source="outputs/market_curves.csv",
           public_note="fitted on data/anchors/used_prices.csv where every row carries its source URL"),
        _f("contracts/register", [
            ("contract_id", "str"), ("counterparty_name", "str"), ("counterparty_role", "str"),
            ("counterparty_is_public", "bool"), ("category", "str"), ("start_date", "date"), ("end_date", "date"),
            ("notice_days", "int"), ("auto_renewal", "bool"), ("price_protection", "bool"),
            ("price_protection_days", "int"), ("claim_window_days", "int"), ("warranty_months", "int"),
            ("rebate_tiers_json", "str"), ("volume_commitment_units", "int"), ("payment_terms_days", "int"),
            ("sla_json", "str"), ("spend_under_contract_eur", "money"), ("terms_note", "str"),
        ], "start_date", single_delivery=True),
        _f("erp/purchase_orders", [
            ("po_number", "str"), ("supplier_id", "str"), ("supplier_name", "str"), ("supplier_role", "str"),
            ("contract_ref", "str"), ("order_date", "date"), ("promised_date", "date"), ("currency", "str"),
            ("incoterm", "str"), ("payment_terms_days", "int"),
        ], "order_date"),
        _f("erp/po_lines", [
            ("po_number", "str"), ("po_line", "int"), ("slug", "str"), ("storage_gb", "int"), ("colour", "str"),
            ("qty_ordered", "int"), ("unit_price_eur", "money"), ("price_protection_days", "int"), ("order_date", "date"),
        ], "order_date"),
        _f("erp/goods_receipts", [
            ("gr_number", "str"), ("po_number", "str"), ("po_line", "int"), ("serial", "str"),
            ("received_at", "datetime"), ("warehouse", "str"),
        ], "received_at"),
        _f("erp/supplier_invoices", [
            ("invoice_number", "str"), ("invoice_line", "int"), ("supplier_id", "str"), ("po_number", "str"),
            ("po_line", "int"), ("serial", "str"), ("invoice_date", "date"), ("line_kind", "str"), ("qty", "int"),
            ("amount_eur", "money"), ("currency", "str"),
        ], "invoice_date"),
        _f("erp/price_changes", [
            ("change_id", "str"), ("supplier_id", "str"), ("slug", "str"), ("storage_gb", "int"), ("valid_from", "date"),
            ("old_unit_price_eur", "money"), ("new_unit_price_eur", "money"),
        ], "valid_from"),
        _f("wms/staging_log", [
            ("staging_id", "str"), ("serial", "str"), ("staged_at", "datetime"), ("mdm_enrolled", "bool"),
            ("staging_cost_eur", "money"),
        ], "staged_at"),
        _f("wms/shipments", [
            ("shipment_id", "str"), ("serial", "str"), ("related_serial", "str"), ("direction", "str"),
            ("shipped_at", "datetime"), ("delivered_at", "datetime"), ("rental_contract_ref", "str"),
            ("carrier_ref", "str"), ("cost_eur", "money"),
        ], "shipped_at"),
        _f("portal/rental_contracts", [
            ("contract_id", "str"), ("customer_id", "str"), ("serial", "str"), ("start_date", "date"),
            ("term_months", "int"), ("monthly_rate_eur", "money"), ("end_date", "date"), ("actual_end_date", "date"),
            ("status", "str"), ("replaces_contract_id", "str"),
        ], "start_date"),
        _f("portal/rental_invoices", [
            ("invoice_id", "str"), ("contract_id", "str"), ("serial", "str"), ("period_no", "int"),
            ("period_month", "date"), ("invoice_date", "date"), ("amount_eur", "money"),
        ], "invoice_date"),
        _f("servicedesk/tickets", [
            ("ticket_id", "str"), ("serial", "str"), ("contract_id", "str"), ("opened_at", "datetime"),
            ("closed_at", "datetime"), ("damage_type", "str"), ("resolution", "str"), ("quote_eur", "money"),
            ("repair_cost_eur", "money"), ("replacement_serial", "str"), ("repair_partner_ref", "str"),
        ], "opened_at"),
        _f("returns/receipts", [
            ("receipt_id", "str"), ("serial", "str"), ("contract_id", "str"), ("returned_at", "datetime"),
            ("grade_declared", "str"), ("grade_inspected", "str"), ("inspected_at", "datetime"),
            ("wipe_certificate_id", "str"), ("wiped_at", "datetime"), ("wipe_grading_cost_eur", "money"),
        ], "returned_at"),
        _f("refurb/work_orders", [
            ("work_order_id", "str"), ("serial", "str"), ("started_at", "datetime"), ("finished_at", "datetime"),
            ("cost_eur", "money"), ("grade_out", "str"), ("outcome", "str"), ("partner_ref", "str"),
        ], "started_at"),
        _f("recommerce/orders", [
            ("order_id", "str"), ("serial", "str"), ("channel", "str"), ("listed_at", "datetime"), ("sold_at", "datetime"),
            ("gross_price_eur", "money"), ("buyer_type", "str"), ("grade_at_sale", "str"),
        ], "sold_at"),
        _f("recommerce/credit_notes", [
            ("credit_note_id", "str"), ("order_id", "str"), ("serial", "str"), ("channel", "str"),
            ("credited_at", "datetime"), ("gross_eur", "money"), ("fee_pct_eur", "money"), ("fee_fixed_eur", "money"),
            ("net_eur", "money"),
        ], "credited_at"),
        _f("finance/indirect_spend", [
            ("spend_id", "str"), ("invoice_date", "date"), ("category", "str"), ("supplier_name", "str"),
            ("amount_eur", "money"), ("has_po", "bool"), ("has_contract", "bool"), ("saving_eur", "money"),
            ("saving_confirmed_by_controlling", "bool"), ("saving_type", "str"), ("baseline_amount_eur", "money"),
        ], "invoice_date"),
    )
}
FEED_KEYS: tuple[str, ...] = tuple(FEEDS)
TRANSACTIONAL_FEEDS: tuple[str, ...] = tuple(k for k, f in FEEDS.items() if not f.single_delivery)
SERIAL_NAME_COLUMNS: dict[str, tuple[str, ...]] = {
    "erp/purchase_orders": ("supplier_name",),
    "contracts/register": ("counterparty_name",),
    "finance/indirect_spend": ("supplier_name",),
    "wms/shipments": ("carrier_ref",),
    "servicedesk/tickets": ("repair_partner_ref",),
    "refurb/work_orders": ("partner_ref",),
}


# --------------------------------------------------------------------------- periods


def delivery_periods(cfg: LakeConfig) -> list[tuple[date, date]]:
    """``(period_start, delivered_on)`` from ``history_start`` to ``as_of`` by cadence; the last one ends at ``as_of``."""
    step = CADENCE_MONTHS[cfg.delivery_cadence]
    out: list[tuple[date, date]] = []
    start = month_floor(cfg.history_start)
    while start <= cfg.as_of:
        nxt = add_months(start, step)
        delivered_on = min(date.fromordinal(nxt.toordinal() - 1), cfg.as_of)
        out.append((start, delivered_on))
        start = nxt
    return out


def landing_file_name(feed: str, delivered_on: date, seq: int) -> str:
    return f"{delivered_on:%Y-%m-%d}_{feed}_{seq:03d}.csv"


def landing_path(raw_dir: Path, spec: FeedLanding, delivered_on: date, seq: int) -> Path:
    return Path(raw_dir) / spec.source_system / spec.feed / landing_file_name(spec.feed, delivered_on, seq)


def _as_dates(col: pd.Series) -> pd.Series:
    """A datetime64 series normalised to midnight (NaT for missing)."""
    return pd.to_datetime(col, errors="coerce").dt.normalize()


def assign_periods(df: pd.DataFrame, spec: FeedLanding, periods: list[tuple[date, date]], as_of: date) -> np.ndarray:
    """Period index per row (``-1`` = never written), by the rule in the module docstring."""
    n = len(df)
    last = len(periods) - 1
    if n == 0:
        return np.zeros(0, dtype=int)
    if spec.single_delivery or spec.order_column is None:
        return np.full(n, last, dtype=int)
    ends = np.array([pd.Timestamp(p[1]) for p in periods], dtype="datetime64[ns]")
    as_of_ts = pd.Timestamp(as_of).to_datetime64()
    latest = _as_dates(df[spec.order_column]).to_numpy(dtype="datetime64[ns]")
    for c in spec.timestamp_columns:
        if c == spec.order_column or c not in df.columns:
            continue
        other = _as_dates(df[c]).to_numpy(dtype="datetime64[ns]")
        mask = ~np.isnat(other) & (np.isnat(latest) | (other > latest))
        latest = np.where(mask, other, latest)
    unknown = np.isnat(latest)
    beyond = as_of_ts + np.timedelta64(1, "D")
    safe = np.where(unknown, beyond, latest)
    idx = np.searchsorted(ends, safe, side="left").astype(int)
    idx = np.where(unknown | (safe > as_of_ts), -1, idx)
    if "_delivery_shift" in df.columns:
        shift = pd.to_numeric(df["_delivery_shift"], errors="coerce").fillna(0).astype(int).to_numpy()
        idx = np.where(idx >= 0, idx + shift, idx)
    idx = np.where(idx > last, -1, idx)
    return idx


def split_deliveries(
    df: pd.DataFrame, order_column: str | None, periods: list[tuple[date, date]], as_of: date, spec: FeedLanding | None = None
) -> list[tuple[date, pd.DataFrame]]:
    """``[(delivered_on, rows)]`` in period order; empty periods are skipped."""
    if spec is None:
        spec = FeedLanding(key="ad-hoc/frame", source_system="ad-hoc", feed="frame",
                           columns=tuple((c, "str") for c in df.columns), order_column=order_column)
    idx = assign_periods(df, spec, periods, as_of)
    out: list[tuple[date, pd.DataFrame]] = []
    for i, (_, delivered_on) in enumerate(periods):
        rows = df[idx == i]
        if len(rows):
            out.append((delivered_on, rows))
    return out


# --------------------------------------------------------------------------- formatting


def _money_text(col: pd.Series) -> pd.Series:
    """Two-decimal text of a money column, vectorised (``""`` for missing)."""
    x = pd.to_numeric(col, errors="coerce")
    cents = np.round(x.to_numpy(dtype=float) * 100.0)
    missing = np.isnan(cents)
    safe = np.where(missing, 0.0, cents).astype(np.int64)
    sign = np.where(safe < 0, "-", "")
    mag = np.abs(safe)
    whole = pd.Series(mag // 100).astype(str)
    frac = pd.Series(mag % 100).astype(str).str.zfill(2)
    out = pd.Series(sign, index=whole.index) + whole + "." + frac
    out = out.where(~missing, "")
    out.index = col.index
    return out


def _fmt_column(col: pd.Series, dtype: str) -> pd.Series:
    if dtype in ("date", "datetime"):
        arr = pd.to_datetime(col, errors="coerce").to_numpy(dtype="datetime64[s]")
        text = np.datetime_as_string(arr, unit="D" if dtype == "date" else "s")
        out = pd.Series(text, index=col.index, dtype=object)
        return out.where(~np.isnat(arr), "")
    if dtype == "money":
        return _money_text(col)
    if dtype == "float":
        s = pd.to_numeric(col, errors="coerce")
        return s.map(lambda x: "" if pd.isna(x) else repr(float(x)))
    if dtype == "int":
        s = pd.to_numeric(col, errors="coerce")
        out = s.astype("Int64").astype(str)
        return out.where(s.notna(), "")
    if dtype == "bool":
        out = col.map({True: "true", False: "false"})
        return out.where(col.notna(), "").astype(str)
    out = col.where(col.notna(), "")
    return out.astype(str)


def format_frame(df: pd.DataFrame, spec: FeedLanding, is_synthetic: bool) -> pd.DataFrame:
    """Every landing column as text in landing order, ``is_synthetic`` last."""
    data: dict[str, pd.Series | str] = {}
    for name, dtype in spec.columns:
        data[name] = _fmt_column(df[name], dtype) if name in df.columns else ""
    data["is_synthetic"] = "true" if is_synthetic else "false"
    return pd.DataFrame(data, index=df.index)


def first_line(spec: FeedLanding, seed: int, delivered_on: date) -> str:
    if spec.is_reference:
        return f"# PUBLIC DATA - copy of {spec.public_source}, {spec.public_note}"
    return f"# SYNTHETIC DATA - restwert generate-lake seed={seed} feed={spec.key} delivery={delivered_on.isoformat()}"


def write_one(path: Path, spec: FeedLanding, rows: pd.DataFrame, seed: int, delivered_on: date) -> int:
    """Write one landing file; returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = format_frame(rows, spec, is_synthetic=not spec.is_reference)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(first_line(spec, seed, delivered_on) + "\n")
        text.to_csv(fh, index=False, lineterminator="\n")
    return int(len(text))


def write_landing_files(world: "World", raw_dir: Path, cfg: LakeConfig, seed: int) -> list[Path]:
    """Write every feed of ``world.frames`` into ``raw_dir``; fills ``world.written`` (feed -> files, rows)."""
    raw_dir = Path(raw_dir)
    periods = delivery_periods(cfg)
    written: list[Path] = []
    world.written = {}
    for key in FEED_KEYS:
        spec = FEEDS[key]
        df = world.frames.get(key)
        if df is None:
            df = pd.DataFrame(columns=list(spec.column_names))
        n_files = 0
        n_rows = 0
        for delivered_on, rows in split_deliveries(df, spec.order_column, periods, cfg.as_of, spec=spec):
            path = landing_path(raw_dir, spec, delivered_on, 1)
            n_rows += write_one(path, spec, rows, seed, delivered_on)
            n_files += 1
            written.append(path)
        world.written[key] = (n_files, n_rows)
    return written


# --------------------------------------------------------------------------- manifest and SYNTHETIC.md


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_rows(path: Path) -> int:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        n = sum(1 for line in fh if line and not line.startswith("#"))
    return max(n - 1, 0)


def write_manifest(raw_dir: Path, files: list[Path], seed: int) -> Path:
    """``<raw_dir>/_manifest.json``: seed, per file sha256 and row count (informational; ingest re-hashes)."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in sorted(files, key=lambda x: x.relative_to(raw_dir).as_posix()):
        entries.append({"path": p.relative_to(raw_dir).as_posix(), "sha256": sha256_of(p), "rows": _count_rows(p)})
    payload = {"generator": f"restwert generate-lake {__version__}", "seed": int(seed), "n_files": len(entries),
               "informational": "ingest never reads this file; it re-hashes every landing file", "files": entries}
    path = raw_dir / MANIFEST_NAME
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def render_synthetic_md(world: "World", cfg: LakeConfig, files: list[Path]) -> str:
    """The provenance page of the lake: seed, counts, truth sources, caps, exclusions, defects, allowlist."""
    truth = cfg.truth_v2
    written = getattr(world, "written", None) or {}
    if not written and files:
        counts: dict[str, list[int]] = {}
        for p in files:
            key = f"{p.parent.parent.name}/{p.parent.name}"
            c = counts.setdefault(key, [0, 0])
            c[0] += 1
            c[1] += _count_rows(p)
        written = {k: (v[0], v[1]) for k, v in counts.items()}
    n_spares = int(getattr(world, "n_spares", 0))
    lines = [
        "# SYNTHETIC DATA (data lake)",
        "",
        f"Generated by `restwert generate-lake` (package version {__version__}).",
        "",
        f"- seed: `{world.seed}`",
        f"- n_serials: `{world.n_serials}` (plus {n_spares} spares, 5 % per catalogue family)",
        f"- history: `{cfg.history_start.isoformat()}` to `{cfg.purchase_end.isoformat()}`, as_of `{cfg.as_of.isoformat()}`",
        f"- delivery cadence: `{cfg.delivery_cadence}` ({len(delivery_periods(cfg))} delivery periods)",
        f"- catalogue: {world.n_usable_slugs} usable slugs (launch date and priced variant) of {world.n_catalogue_slugs}; "
        f"{world.n_pool_slugs} of them carry a storage on a priced variant and are drawn",
        f"- VAT convention: the public catalogue RRP is gross; every price in the lake is net "
        f"(`rrp_net = round(rrp_gross / (1 + {world.vat_rate}), 2)`, owner CFO (name))",
        "",
        "## Files and rows per feed",
        "",
        "| Feed | Files | Rows | Kind |",
        "|---|---:|---:|---|",
    ]
    for key in FEED_KEYS:
        n_files, n_rows = written.get(key, (0, 0))
        kind = "public copy (`is_synthetic = false`)" if FEEDS[key].is_reference else "synthetic"
        lines.append(f"| `{key}` | {n_files} | {n_rows} | {kind} |")
    lines += [
        "",
        "## Truth: how resale prices were made",
        "",
        "Calibrated to public refurbisher asks (`outputs/market_curves.csv`, realisation vs gross launch RRP); "
        "the haircut and the cap are design parameters, not market facts. No successor step.",
        "",
        "```",
        "q_public   = exp(intercept + slope_per_month * age_months + offset[grade])",
        f"true_price = rrp_net * min(q_public, {truth.q_young_cap}) * {truth.ask_to_realised} * channel_mult[channel] * exp(N(0, {truth.noise_sigma}))",
        f"             clipped to [{truth.ratio_min} * rrp_net, {truth.ratio_max} * rrp_net], rounded to cents",
        "```",
        "",
        f"- `q_young_cap` {truth.q_young_cap}, `ask_to_realised` {truth.ask_to_realised}, `noise_sigma` {truth.noise_sigma}, "
        f"`grade_d_offset_default` {truth.grade_d_offset_default} (the public rows carry no grade D fit); owner: {truth.owner}",
        "- grade B is the fitted reference (offset 0); A and C offsets come from the row",
        "",
        "| Family / manufacturer | Truth source | fit_quality | n |",
        "|---|---|---|---:|",
    ]
    for group in sorted(world.truth_sources):
        c = world.truth_curves.get(group)
        fq = c.fit_quality if c is not None else ""
        n = c.n if c is not None else 0
        lines.append(f"| {group} | {world.truth_sources[group]} | {fq} | {n} |")
    lines += [
        "",
        "`family_oem` = the marketplace row of that family and manufacturer (fit_quality ok); `family` = the "
        "family row (used when the manufacturer row is thin or has no fit); `default` = the configured fallback "
        "curve (only when the curves file is missing).",
        "",
        "## Excluded catalogue slugs",
        "",
        "| Slug | Manufacturer | Family | Reason |",
        "|---|---|---|---|",
    ]
    for _, r in world.excluded.iterrows():
        lines.append(f"| `{r['slug']}` | {r['oem']} | {r['family']} | {r['reason']} |")
    lines += [
        "",
        "## Injected defects (so that ingest has something to refuse)",
        "",
        "| Defect | Rows | What it does |",
        "|---|---:|---|",
    ]
    explain = {
        "unknown_serial": "wms/shipments outbound rows with one hex digit of the serial changed (unresolved: unknown_serial)",
        "identical_duplicate": "rows repeated unchanged in the next delivery of the same feed (duplicates_identical)",
        "conflicting_duplicate": "portal/rental_invoices rows repeated in the next delivery with amount + 0.01 (duplicate_conflict)",
        "missing_credit_note": "credit notes deleted for sold orders (credited_at missing, chain incomplete)",
        "orphan_freight": "freight invoice lines pointing at po_line + 90 (unresolved: unknown_po_line)",
    }
    for k, v in world.defects_injected.items():
        lines.append(f"| `{k}` | {v} | {explain.get(k, '')} |")
    lines += [
        "",
        "## Counterparty allowlist",
        "",
        "Exact catalogue manufacturer names: " + ", ".join(f"`{m}`" for m in MANUFACTURERS) + ".",
        f"Every other supplier, partner or channel name ends in `{ROLE_ONLY_SUFFIX.strip()}`: "
        + ", ".join(f"`{n}`" for n in world.role_only_names) + ".",
        "",
        "Contract terms in `contracts/register` are synthetic placeholders; the counterparty list itself is public.",
        "",
        SYNTHETIC_SENTENCE,
        "",
        "Every synthetic row carries `is_synthetic = true` and every synthetic landing file starts with a "
        "`# SYNTHETIC DATA` line; the three reference feeds are public copies with `is_synthetic = false` and a "
        "`# PUBLIC DATA` line. Delete `data/lake/raw/` and land your own exports (no `#` line, "
        "`is_synthetic = false`) to work on real data.",
        "",
    ]
    return "\n".join(lines)


def write_synthetic_md(lake_dir: Path, text: str) -> Path:
    """Write ``<lake_dir>/SYNTHETIC.md`` (UTF-8, LF)."""
    lake_dir = Path(lake_dir)
    lake_dir.mkdir(parents=True, exist_ok=True)
    path = lake_dir / "SYNTHETIC.md"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


__all__ = [
    "FeedLanding",
    "FEEDS",
    "FEED_KEYS",
    "TRANSACTIONAL_FEEDS",
    "SERIAL_NAME_COLUMNS",
    "PLANNED_FUTURE_COLUMNS",
    "SYNTHETIC_SENTENCE",
    "MANIFEST_NAME",
    "delivery_periods",
    "landing_file_name",
    "landing_path",
    "assign_periods",
    "split_deliveries",
    "format_frame",
    "first_line",
    "write_one",
    "write_landing_files",
    "sha256_of",
    "write_manifest",
    "render_synthetic_md",
    "write_synthetic_md",
]
