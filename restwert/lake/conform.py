"""Conform bronze into the ten v0.1 source tables (SPEC_v0.2 section 4.4, decision D2).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

The v0.1 engines (forecast, P&L, decisions, KPIs, contracts, export) read ten
source tables in ``main``. v0.2 materialises them from bronze with the
deterministic mapping below, so everything underneath runs unchanged and the
256 v0.1 tests keep passing. The only arithmetic here is the landed cost:
``purchase_price + freight share + duty share`` with every freight and duty
invoice line of a PO line split over its received serials to the cent
(``common.allocate_cents``, the same call the ledger uses).

Every conformed row carries ``is_synthetic`` = the fleet flag (the catalogue
rows are public one join away; the fleet that references them is synthetic)
and ``source_file = "bronze.<table>"``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from restwert import db, schema
from restwert.config import Assumptions
from restwert.lake.common import fleet_family, rrp_net
from restwert.lake.schema_lake import BRONZE_DDL, create_lake_schema
from restwert.records import RunSummary

CONFORM_TABLES: tuple[str, ...] = schema.SOURCE_TABLES

DEFAULT_VAT_RATE = 0.19               # used only when assumptions.yaml has no vat_rate block; the run summary says so
DEFAULT_PURCHASE_DISCOUNT = 0.12      # idem for purchase_discount_pct
LANDED_BENCHMARK_UPLIFT = 1.015       # freight and duty placeholder on top of the benchmark price (v0.1 convention)
BENCHMARK_SOURCE_NOTE = (
    "public net launch RRP x (1 - purchase_discount_pct placeholder) x 1.015; URL in bronze.cat_variants"
)

_CHANNEL_IN = {"manufacturer": "oem_direct", "reseller": "distributor"}
_ROLE_CATEGORY = {"manufacturer": "hardware", "rugged_oem": "hardware", "reseller": "hardware", "logistics": "logistics", "mtd": "software"}
_SUPPLIER_ROLES = ("manufacturer", "rugged_oem", "reseller", "refurb_repair", "logistics", "mtd")

# --------------------------------------------------------------------------- helpers


def _short(table: str) -> str:
    return table.split(".", 1)[1]


def read_bronze(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    """Every bronze table keyed by its short name (``cat_models``, ``erp_po_lines``, ...); missing tables are empty frames."""
    out: dict[str, pd.DataFrame] = {}
    for qualified in BRONZE_DDL:
        short = _short(qualified)
        if db.table_exists(con, qualified):
            out[short] = db.read_df(con, f"SELECT * FROM {db._q(qualified)}")
        else:
            out[short] = pd.DataFrame(columns=[name for name, _ in schema._ddl_columns(BRONZE_DDL[qualified])])
    return out


def _dates(s: pd.Series) -> pd.Series:
    """Timestamps or dates -> python ``date`` objects (None for missing)."""
    dt = pd.to_datetime(s, errors="coerce")
    return pd.Series([d.date() if not pd.isna(d) else None for d in dt], index=s.index, dtype=object)


def _f(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype("float64")


def _plain(df: pd.DataFrame) -> pd.DataFrame:
    """Extension dtypes (Int64, string, boolean) -> object with ``None`` so pydantic sees plain values."""
    out = df.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.api.extensions.ExtensionDtype):
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out


def _fleet_flag(bronze: dict[str, pd.DataFrame]) -> bool:
    for name in ("erp_goods_receipts", "erp_purchase_orders", "portal_rental_contracts", "ctr_register", "fin_indirect_spend"):
        df = bronze.get(name)
        if df is not None and len(df) and "is_synthetic" in df.columns:
            return bool(df["is_synthetic"].iloc[0])
    return True


def _fallback_storage(spec: str | None, family: str) -> int:
    from restwert.market.anchors import parse_storage_gb

    parsed = parse_storage_gb(spec) if spec is not None else None
    if parsed:
        return int(parsed)
    return 256 if family == "Laptop" else 128


def allocate_over_serials(lines: pd.DataFrame, serials: pd.DataFrame, amount_col: str) -> pd.Series:
    """Cent-exact split of every invoice line over the received serials of its PO line.

    ``lines``: ``po_number, po_line, <amount_col>``; ``serials``: ``po_number, po_line, serial``.
    Returns a Series indexed by serial with the summed shares (floor per unit, remainder
    on the last serial in serial order: the vectorised form of ``common.allocate_cents``).
    """
    if len(lines) == 0 or len(serials) == 0:
        return pd.Series(dtype="float64")
    ser = serials[["po_number", "po_line", "serial"]].sort_values(["po_number", "po_line", "serial"]).copy()
    ser["_rank"] = ser.groupby(["po_number", "po_line"]).cumcount()
    ser["_n"] = ser.groupby(["po_number", "po_line"])["serial"].transform("size")
    li = lines[["po_number", "po_line", amount_col]].copy()
    li["_line_no"] = np.arange(len(li))
    m = li.merge(ser, on=["po_number", "po_line"], how="inner")
    if len(m) == 0:
        return pd.Series(dtype="float64")
    cents = np.rint(m[amount_col].astype(float).to_numpy() * 100).astype("int64")
    n = m["_n"].to_numpy().astype("int64")
    base = cents // n
    rem = cents - base * n
    share = base + np.where(m["_rank"].to_numpy() == n - 1, rem, 0)
    m["_share"] = share / 100.0
    return m.groupby("serial")["_share"].sum()


# --------------------------------------------------------------------------- the mapping


def conform_frames(
    bronze: dict[str, pd.DataFrame],
    vat_rate: float,
    purchase_discount_pct: float,
    fee_assumptions: dict,
    as_of: date,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Build the ten v0.1 frames from bronze; returns ``(frames, excluded slugs)``.

    Every frame passes ``schema.validate_frame`` and ``db.referential_checks`` before
    it is returned. Receipts whose slug is not in the usable catalogue are dropped
    and counted in ``frames["devices"].attrs["dropped_no_catalogue"]``; the count of
    catalogue rows whose base storage needed a fallback is in
    ``frames["model_catalogue"].attrs["base_storage_fallbacks"]``.
    """
    b = {k: v.copy() for k, v in bronze.items()}
    syn = _fleet_flag(b)
    frames: dict[str, pd.DataFrame] = {}

    # ---- model_catalogue --------------------------------------------------------------
    models = b["cat_models"]
    variants = b["cat_variants"]
    models["launch_date"] = _dates(models["launch_date"]) if len(models) else models.get("launch_date")
    priced = variants[_f(variants["rrp_eur_launch_de"]).notna()].copy() if len(variants) else variants
    excluded: list[str] = []
    cat_rows: list[dict] = []
    fallbacks = 0
    if len(models):
        priced["rrp"] = _f(priced["rrp_eur_launch_de"])
        priced["storage_gb"] = _f(priced["storage_gb"])
        priced = priced.sort_values(["slug", "rrp", "storage_gb", "spec"], na_position="last")
        first_variant = priced.drop_duplicates("slug", keep="first").set_index("slug")
        min_storage = priced.groupby("slug")["storage_gb"].min()
        usable = models[models["launch_date"].notna() & models["slug"].isin(first_variant.index)].copy()
        excluded = sorted(set(models["slug"]) - set(usable["slug"]))
        usable["_series"] = usable["series"].fillna("")
        usable["_launch_ts"] = pd.to_datetime(usable["launch_date"])
        usable["generation"] = (
            usable.groupby(["oem", "_series"])["_launch_ts"].rank(method="dense").astype(int)
        )
        for r in usable.itertuples(index=False):
            fv = first_variant.loc[r.slug]
            storage = fv["storage_gb"]
            if pd.isna(storage):
                alt = min_storage.get(r.slug)
                if alt is not None and not pd.isna(alt):
                    storage = alt
                else:
                    storage = _fallback_storage(fv["spec"], r.family)
                fallbacks += 1
            cat_rows.append({
                "model": r.slug, "model_family": fleet_family(r.family, r.oem), "generation": int(r.generation),
                "launch_date": r.launch_date, "list_price": rrp_net(fv["rrp"], vat_rate), "base_storage_gb": int(storage),
                "is_synthetic": syn, "source_file": "bronze.cat_models",
            })
    model_catalogue = pd.DataFrame(cat_rows, columns=list(schema.ROW_MODELS["model_catalogue"].model_fields))
    frames["model_catalogue"] = model_catalogue
    cat_index = model_catalogue.set_index("model") if len(model_catalogue) else model_catalogue
    slug_meta = models.set_index("slug")[["family", "oem"]] if len(models) else pd.DataFrame(columns=["family", "oem"])

    # ---- purchase side -----------------------------------------------------------------
    po = b["erp_purchase_orders"]
    lines = b["erp_po_lines"]
    gr = b["erp_goods_receipts"]
    inv = b["erp_supplier_invoices"]
    pc = b["erp_price_changes"]
    ctr = b["ctr_register"]
    po["order_date"] = _dates(po["order_date"])
    po["promised_date"] = _dates(po["promised_date"])
    lines["po_line"] = _f(lines["po_line"]).astype("Int64")
    lines["unit_price_eur"] = _f(lines["unit_price_eur"])
    lines["storage_gb"] = _f(lines["storage_gb"]).astype("Int64")
    gr["po_line"] = _f(gr["po_line"]).astype("Int64")
    gr["received_date"] = _dates(gr["received_at"])
    inv["po_line"] = _f(inv["po_line"]).astype("Int64")
    inv["amount_eur"] = _f(inv["amount_eur"])
    inv["invoice_date"] = _dates(inv["invoice_date"])

    # variant RRP per (slug, storage)
    if len(variants):
        vr = variants.copy()
        vr["rrp"] = _f(vr["rrp_eur_launch_de"])
        vr["storage_gb"] = _f(vr["storage_gb"]).astype("Int64")
        vr = vr[vr["rrp"].notna() & vr["storage_gb"].notna()].sort_values(["slug", "storage_gb", "rrp"])
        vr = vr.drop_duplicates(["slug", "storage_gb"], keep="first")[["slug", "storage_gb", "rrp"]]
    else:
        vr = pd.DataFrame(columns=["slug", "storage_gb", "rrp"])

    # devices: one per goods receipt
    dev = gr.merge(lines[["po_number", "po_line", "slug", "storage_gb", "colour", "unit_price_eur", "price_protection_days"]],
                   on=["po_number", "po_line"], how="left")
    dev = dev.merge(po[["po_number", "supplier_id", "supplier_name", "supplier_role", "contract_ref"]], on="po_number", how="left")
    dropped = 0
    if len(dev):
        in_cat = dev["slug"].isin(cat_index.index) if len(cat_index) else pd.Series(False, index=dev.index)
        dropped = int((~in_cat).sum())
        dev = dev[in_cat].copy()
    unit = inv[(inv["line_kind"] == "unit") & inv["serial"].notna()].sort_values(["serial", "invoice_date"]).drop_duplicates("serial")
    unit_map = unit.set_index("serial")["amount_eur"] if len(unit) else pd.Series(dtype="float64")
    freight = allocate_over_serials(inv[inv["line_kind"] == "freight"], gr, "amount_eur")
    duty = allocate_over_serials(inv[inv["line_kind"] == "duty"], gr, "amount_eur")
    contracts = b["portal_rental_contracts"].copy()
    contracts["start_date"] = _dates(contracts["start_date"])
    contracts["end_date"] = _dates(contracts["end_date"])
    contracts["actual_end_date"] = _dates(contracts["actual_end_date"])
    first_contract = (
        contracts.sort_values(["serial", "start_date", "contract_id"]).drop_duplicates("serial").set_index("serial")["contract_id"]
        if len(contracts) else pd.Series(dtype=object)
    )
    if len(dev):
        dev["purchase_price"] = dev["serial"].map(unit_map).astype("float64")
        dev["purchase_price"] = dev["purchase_price"].where(dev["purchase_price"].notna(), dev["unit_price_eur"].astype("float64"))
        dev["freight_share"] = dev["serial"].map(freight).fillna(0.0)
        dev["duty_share"] = dev["serial"].map(duty).fillna(0.0)
        dev["landed_cost"] = (dev["purchase_price"] + dev["freight_share"] + dev["duty_share"]).round(2)
        dev["purchase_price"] = dev["purchase_price"].round(2)
        dev["model_family"] = dev["slug"].map(cat_index["model_family"])
        dev["launch_date"] = dev["slug"].map(cat_index["launch_date"])
        dev["channel_in"] = dev["supplier_role"].map(_CHANNEL_IN).fillna("distributor")
        dev["po_number_composite"] = dev["po_number"].astype(str) + "-" + dev["po_line"].astype(str)
        dev["contract_id"] = dev["serial"].map(first_contract)
    devices = pd.DataFrame({
        "serial": dev["serial"] if len(dev) else pd.Series(dtype=object),
        "model_family": dev.get("model_family"), "model": dev.get("slug"),
        "storage_gb": dev["storage_gb"].astype("Int64") if len(dev) else pd.Series(dtype="Int64"),
        "colour": dev.get("colour"), "launch_date": dev.get("launch_date"), "purchase_date": dev.get("received_date"),
        "purchase_price": dev.get("purchase_price"), "landed_cost": dev.get("landed_cost"), "supplier": dev.get("supplier_name"),
        "channel_in": dev.get("channel_in"), "po_number": dev.get("po_number_composite"), "contract_id": dev.get("contract_id"),
    })
    devices["is_synthetic"] = syn
    devices["source_file"] = "bronze.erp_goods_receipts"
    devices.attrs["dropped_no_catalogue"] = dropped
    frames["devices"] = devices

    # purchase_orders: one row per PO line
    pol = lines.merge(po[["po_number", "supplier_id", "supplier_name", "contract_ref", "order_date", "promised_date"]], on="po_number", how="left")
    if len(pol):
        rec = gr.groupby(["po_number", "po_line"]).agg(qty_delivered=("serial", "size"), delivered_date=("received_date", "max")).reset_index()
        pol = pol.merge(rec, on=["po_number", "po_line"], how="left")
        pol["qty_delivered"] = pol["qty_delivered"].fillna(0).astype(int)
        pol["delivered_date"] = pol["delivered_date"].where(pol["delivered_date"].notna(), None)
        # contract in force at order_date
        if len(ctr):
            c = ctr[["contract_id", "start_date", "end_date"]].copy()
            c["_cs"] = pd.to_datetime(c["start_date"], errors="coerce")
            c["_ce"] = pd.to_datetime(c["end_date"], errors="coerce")
            pol = pol.merge(c[["contract_id", "_cs", "_ce"]].rename(columns={"contract_id": "contract_ref"}), on="contract_ref", how="left")
            od = pd.to_datetime(pol["order_date"], errors="coerce")
            in_force = pol["_cs"].notna() & (pol["_cs"] <= od) & (od <= pol["_ce"])
            pol["supplier_contract_id"] = pol["contract_ref"].where(in_force, None)
        else:
            pol["supplier_contract_id"] = None
        pol = pol.merge(vr, on=["slug", "storage_gb"], how="left")
        pol["rrp_net"] = pol["rrp"].map(lambda x: rrp_net(x, vat_rate) if not pd.isna(x) else np.nan)
        pol["benchmark_price"] = (pol["rrp_net"] * (1.0 - purchase_discount_pct)).round(2)
        # first price change inside the protection window
        pol["price_drop_date"] = None
        pol["price_drop_amount"] = np.nan
        if len(pc):
            p = pc[["supplier_id", "slug", "storage_gb", "valid_from", "old_unit_price_eur", "new_unit_price_eur"]].copy()
            p["storage_gb"] = _f(p["storage_gb"]).astype("Int64")
            p["valid_from"] = _dates(p["valid_from"])
            p["drop"] = (_f(p["old_unit_price_eur"]) - _f(p["new_unit_price_eur"])).round(2)
            cand = pol[pol["delivered_date"].notna() & pol["price_protection_days"].notna()][
                ["po_number", "po_line", "supplier_id", "slug", "storage_gb", "delivered_date", "price_protection_days"]
            ].merge(p, on=["supplier_id", "slug", "storage_gb"], how="inner")
            if len(cand):
                deliv = pd.to_datetime(cand["delivered_date"])
                valid = pd.to_datetime(cand["valid_from"])
                window_end = deliv + pd.to_timedelta(cand["price_protection_days"].astype(float), unit="D")
                cand = cand[(valid > deliv) & (valid <= window_end)]
                cand = cand.assign(_valid_ts=valid).sort_values(["po_number", "po_line", "_valid_ts"]).drop_duplicates(["po_number", "po_line"])
                pol = pol.drop(columns=["price_drop_date", "price_drop_amount"]).merge(
                    cand[["po_number", "po_line", "valid_from", "drop"]].rename(columns={"valid_from": "price_drop_date", "drop": "price_drop_amount"}),
                    on=["po_number", "po_line"], how="left",
                )
                pol["price_drop_date"] = pol["price_drop_date"].where(pol["price_drop_date"].notna(), None)
        pol["po_number_composite"] = pol["po_number"].astype(str) + "-" + pol["po_line"].astype(str)
    purchase_orders = pd.DataFrame({
        "po_number": pol.get("po_number_composite", pd.Series(dtype=object)),
        "supplier": pol.get("supplier_name"), "supplier_contract_id": pol.get("supplier_contract_id"), "model": pol.get("slug"),
        "order_date": pol.get("order_date"), "promised_date": pol.get("promised_date"), "delivered_date": pol.get("delivered_date"),
        "qty_ordered": pol.get("qty_ordered"), "qty_delivered": pol.get("qty_delivered"), "unit_price": pol.get("unit_price_eur"),
        "benchmark_price": pol.get("benchmark_price"), "price_drop_date": pol.get("price_drop_date"),
        "price_drop_amount": pol.get("price_drop_amount"),
    })
    purchase_orders["is_synthetic"] = syn
    purchase_orders["source_file"] = "bronze.erp_po_lines"
    frames["purchase_orders"] = purchase_orders

    # benchmarks: per usable slug
    if len(model_catalogue):
        bench = pd.DataFrame({
            "model": model_catalogue["model"], "valid_from": model_catalogue["launch_date"],
            "landed_cost_benchmark": ((model_catalogue["list_price"] * (1.0 - purchase_discount_pct)).round(2) * LANDED_BENCHMARK_UPLIFT).round(2),
            "source_note": BENCHMARK_SOURCE_NOTE,
        })
    else:
        bench = pd.DataFrame(columns=["model", "valid_from", "landed_cost_benchmark", "source_note"])
    bench["is_synthetic"] = syn
    bench["source_file"] = "bronze.cat_variants"
    frames["benchmarks"] = bench

    # ---- rental contracts ------------------------------------------------------------------
    rc = pd.DataFrame({
        "contract_id": contracts.get("contract_id", pd.Series(dtype=object)), "serial": contracts.get("serial"),
        "customer_id": contracts.get("customer_id"), "start_date": contracts.get("start_date"),
        "term_months": contracts.get("term_months"), "monthly_rate": contracts.get("monthly_rate_eur"),
        "end_date": contracts.get("end_date"), "actual_end_date": contracts.get("actual_end_date"),
        "status": contracts.get("status"), "replaces_contract_id": contracts.get("replaces_contract_id"),
    })
    rc["is_synthetic"] = syn
    rc["source_file"] = "bronze.portal_rental_contracts"
    frames["rental_contracts"] = rc

    # ---- events: tickets and receipts ------------------------------------------------------
    tickets = b["sd_tickets"].copy()
    receipts = b["ret_receipts"].copy()
    ship = b["wms_shipments"].copy()
    ship["shipped_ts"] = pd.to_datetime(ship["shipped_at"], errors="coerce")
    ship["cost_eur"] = _f(ship["cost_eur"])
    event_cols = list(schema.ROW_MODELS["events"].model_fields)
    parts: list[pd.DataFrame] = []
    if len(tickets):
        tickets["opened_ts"] = pd.to_datetime(tickets["opened_at"], errors="coerce")
        tickets["closed_ts"] = pd.to_datetime(tickets["closed_at"], errors="coerce")
        tickets["quote_eur"] = _f(tickets["quote_eur"])
        tickets["repair_cost_eur"] = _f(tickets["repair_cost_eur"])
        is_open = tickets["resolution"] == "open"
        damage = pd.DataFrame({
            "event_id": "EV-T-" + tickets["ticket_id"].astype(str) + "-D", "serial": tickets["serial"],
            "contract_id": tickets["contract_id"], "event_type": "damage", "event_date": _dates(tickets["opened_ts"]),
            "cost": np.where(is_open, tickets["quote_eur"].fillna(0.0), 0.0), "damage_type": tickets["damage_type"],
            "resolved": ~is_open, "replacement_serial": None, "return_date": None, "grade_pre_return": None,
            "grade_inspected": None, "wipe_certificate": None, "note": None,
        })
        parts.append(damage)
        rep = tickets[(tickets["resolution"] == "repair") & tickets["closed_ts"].notna()]
        if len(rep):
            parts.append(pd.DataFrame({
                "event_id": "EV-T-" + rep["ticket_id"].astype(str) + "-R", "serial": rep["serial"], "contract_id": rep["contract_id"],
                "event_type": "repair", "event_date": _dates(rep["closed_ts"]), "cost": rep["repair_cost_eur"].fillna(0.0),
                "damage_type": None, "resolved": None, "replacement_serial": None, "return_date": None,
                "grade_pre_return": None, "grade_inspected": None, "wipe_certificate": None, "note": None,
            }))
        rpl = tickets[tickets["resolution"] == "replace"].copy()
        if len(rpl):
            ro = ship[ship["direction"] == "replacement_out"][["serial", "related_serial", "rental_contract_ref", "shipped_ts", "cost_eur"]]
            m = rpl.merge(ro.rename(columns={"serial": "_ship_serial", "related_serial": "serial"}), on="serial", how="left")
            m["_ctr_match"] = (m["rental_contract_ref"].notna()) & (m["rental_contract_ref"] == m["contract_id"])
            m["_dist"] = (m["shipped_ts"] - m["opened_ts"]).abs()
            m = m.sort_values(["ticket_id", "_ctr_match", "_dist"], ascending=[True, False, True]).drop_duplicates("ticket_id")
            fallback = m["closed_ts"].where(m["closed_ts"].notna(), m["opened_ts"])
            ev_date = m["shipped_ts"].where(m["shipped_ts"].notna(), fallback)
            parts.append(pd.DataFrame({
                "event_id": "EV-T-" + m["ticket_id"].astype(str) + "-X", "serial": m["serial"], "contract_id": m["contract_id"],
                "event_type": "replacement", "event_date": _dates(ev_date), "cost": m["cost_eur"].fillna(0.0),
                "damage_type": None, "resolved": None,
                "replacement_serial": m["replacement_serial"].where(m["replacement_serial"].notna(), m["_ship_serial"]),
                "return_date": None, "grade_pre_return": None, "grade_inspected": None, "wipe_certificate": None, "note": None,
            }))
    if len(receipts):
        receipts["returned_ts"] = pd.to_datetime(receipts["returned_at"], errors="coerce")
        rs = ship[ship["direction"] == "return"][["serial", "rental_contract_ref", "shipped_ts", "cost_eur"]]
        m = receipts.merge(rs, on="serial", how="left")
        m["_ctr_match"] = (m["rental_contract_ref"].notna()) & (m["rental_contract_ref"] == m["contract_id"])
        m["_dist"] = (m["shipped_ts"] - m["returned_ts"]).abs()
        m = m.sort_values(["receipt_id", "_ctr_match", "_dist"], ascending=[True, False, True]).drop_duplicates("receipt_id")
        ret_date = _dates(m["returned_ts"])
        parts.append(pd.DataFrame({
            "event_id": "EV-R-" + m["receipt_id"].astype(str), "serial": m["serial"], "contract_id": m["contract_id"],
            "event_type": "return", "event_date": ret_date, "cost": m["cost_eur"].fillna(0.0), "damage_type": None,
            "resolved": None, "replacement_serial": None, "return_date": ret_date, "grade_pre_return": m["grade_declared"],
            "grade_inspected": m["grade_inspected"], "wipe_certificate": m["wipe_certificate_id"].notna(), "note": None,
        }))
    events = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=event_cols)
    events["is_synthetic"] = syn
    events["source_file"] = "bronze.sd_tickets+bronze.ret_receipts"
    frames["events"] = events

    # ---- refurbishment -----------------------------------------------------------------------
    wo = b["rf_work_orders"]
    if len(wo):
        sd = _dates(wo["started_at"])
        ed = _dates(wo["finished_at"])
        refurb = pd.DataFrame({
            "refurb_id": wo["work_order_id"], "serial": wo["serial"], "start_date": sd, "end_date": ed,
            "days": [(e - s).days if (s is not None and e is not None) else None for s, e in zip(sd, ed)],
            "cost": _f(wo["cost_eur"]), "grade_out": wo["grade_out"], "outcome": wo["outcome"],
        })
    else:
        refurb = pd.DataFrame(columns=["refurb_id", "serial", "start_date", "end_date", "days", "cost", "grade_out", "outcome"])
    refurb["is_synthetic"] = syn
    refurb["source_file"] = "bronze.rf_work_orders"
    frames["refurbishment"] = refurb

    # ---- resale ------------------------------------------------------------------------------
    orders = b["rc_orders"]
    cn = b["rc_credit_notes"]
    if len(orders):
        o = orders.copy()
        o["gross"] = _f(o["gross_price_eur"])
        if len(cn):
            # a credit note dated after as_of does not exist yet (the ledger applies the same cut)
            credited = _dates(cn["credited_at"])
            c = cn[[d is not None and d <= as_of for d in credited]].copy()
            c["cn_fee"] = _f(c["fee_pct_eur"]).fillna(0.0) + _f(c["fee_fixed_eur"]).fillna(0.0)
            c = c.sort_values(["order_id", "credited_at"]).drop_duplicates("order_id")[["order_id", "cn_fee"]]
            o = o.merge(c, on="order_id", how="left")
        else:
            o["cn_fee"] = np.nan
        fee_pct = o["channel"].map(lambda ch: float(fee_assumptions.get(ch, {}).get("fee_pct", 0.0)))
        fee_fixed = o["channel"].map(lambda ch: float(fee_assumptions.get(ch, {}).get("fee_fixed_eur", 0.0)))
        assumed = (o["gross"] * fee_pct + fee_fixed).round(2)
        o["fees"] = o["cn_fee"].where(o["cn_fee"].notna(), assumed).round(2)
        resale = pd.DataFrame({
            "sale_id": o["order_id"], "serial": o["serial"], "channel": o["channel"], "sale_date": _dates(o["sold_at"]),
            "price": o["gross"].round(2), "fees": o["fees"], "buyer_type": o["buyer_type"], "grade_at_sale": o["grade_at_sale"],
        })
    else:
        resale = pd.DataFrame(columns=["sale_id", "serial", "channel", "sale_date", "price", "fees", "buyer_type", "grade_at_sale"])
    resale["is_synthetic"] = syn
    resale["source_file"] = "bronze.rc_orders"
    frames["resale"] = resale

    # ---- supplier contracts ------------------------------------------------------------------
    if len(ctr):
        sc = ctr[ctr["counterparty_role"].isin(_SUPPLIER_ROLES)].copy()
        cat = sc["counterparty_role"].map(_ROLE_CATEGORY)
        cat = cat.where(cat.notna(), sc["category"])  # refurb_repair keeps the register category (refurbishment or repair)
        supplier_contracts = pd.DataFrame({
            "supplier_contract_id": sc["contract_id"], "supplier": sc["counterparty_name"], "category": cat,
            "start_date": _dates(sc["start_date"]), "end_date": _dates(sc["end_date"]), "auto_renewal": sc["auto_renewal"].astype(bool),
            "notice_days": _f(sc["notice_days"]).astype("Int64"), "price_protection": sc["price_protection"].astype(bool),
            "price_protection_days": _f(sc["claim_window_days"]).astype("Int64"),
            "payment_terms_days": _f(sc["payment_terms_days"]).astype("Int64"),
            "spend_under_contract": _f(sc["spend_under_contract_eur"]).round(2),
        })
    else:
        supplier_contracts = pd.DataFrame(columns=[
            "supplier_contract_id", "supplier", "category", "start_date", "end_date", "auto_renewal", "notice_days",
            "price_protection", "price_protection_days", "payment_terms_days", "spend_under_contract",
        ])
    supplier_contracts["is_synthetic"] = syn
    supplier_contracts["source_file"] = "bronze.ctr_register"
    frames["supplier_contracts"] = supplier_contracts

    # ---- indirect spend ----------------------------------------------------------------------
    fin = b["fin_indirect_spend"]
    indirect = pd.DataFrame({
        "spend_id": fin.get("spend_id", pd.Series(dtype=object)), "invoice_date": _dates(fin["invoice_date"]) if len(fin) else fin.get("invoice_date"),
        "category": fin.get("category"), "supplier": fin.get("supplier_name"), "amount": fin.get("amount_eur"),
        "has_po": fin.get("has_po"), "has_contract": fin.get("has_contract"), "saving": fin.get("saving_eur"),
        "saving_confirmed_by_controlling": fin.get("saving_confirmed_by_controlling"), "saving_type": fin.get("saving_type"),
        "baseline_amount": fin.get("baseline_amount_eur"),
    })
    indirect["is_synthetic"] = syn
    indirect["source_file"] = "bronze.fin_indirect_spend"
    frames["indirect_spend"] = indirect

    # ---- validate ----------------------------------------------------------------------------
    validated: dict[str, pd.DataFrame] = {}
    for table in CONFORM_TABLES:
        validated[table] = schema.validate_frame(table, _plain(frames[table]))
    db.referential_checks(validated)
    validated["devices"].attrs["dropped_no_catalogue"] = dropped
    validated["model_catalogue"].attrs["base_storage_fallbacks"] = fallbacks
    validated["model_catalogue"].attrs["is_synthetic"] = syn
    return validated, excluded


# --------------------------------------------------------------------------- compat CSVs


def write_compat_csvs(frames: dict[str, pd.DataFrame], csv_dir: Path, seed: int | None) -> list[Path]:
    """Write ``<csv_dir>/<table>.csv`` for the ten tables (comment line, header, rows, ISO dates, LF)."""
    csv_dir = Path(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)
    seed_text = "unknown" if seed is None else str(seed)
    written: list[Path] = []
    for table in CONFORM_TABLES:
        df = frames[table]
        syn = bool(df["is_synthetic"].iloc[0]) if len(df) and "is_synthetic" in df.columns else True
        header = (
            f"# SYNTHETIC DATA - conformed from data/lake by restwert conform seed={seed_text}"
            if syn else "# REAL DATA - conformed from data/lake by restwert conform"
        )
        path = csv_dir / f"{table}.csv"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(header + "\n")
            df.to_csv(fh, index=False, lineterminator="\n", date_format="%Y-%m-%d")
        written.append(path)
    return written


# --------------------------------------------------------------------------- runner


def _assumption(a: Assumptions | None, key: str, default, notes: list[str]):
    if a is None:
        notes.append(f"assumptions not loaded: {key} = {default} (default)")
        return default
    try:
        return a.get(key)
    except KeyError:
        notes.append(f"assumptions.yaml has no block {key}: using {default} (default)")
        return default


def run_conform(
    con: duckdb.DuckDBPyConnection,
    as_of: date,
    a: Assumptions | None,
    csv_dir: Path | None = None,
    seed: int | None = None,
) -> RunSummary:
    """Materialise the ten v0.1 source tables in ``main`` from bronze; optional compat CSV copies."""
    started = datetime.now(timezone.utc)
    db.create_schema(con)
    create_lake_schema(con, drop_layers=())
    run_id = db.new_run(con, "conform", seed, as_of, None)
    notes: list[str] = []
    vat_rate = float(_assumption(a, "vat_rate", DEFAULT_VAT_RATE, notes))
    discount = float(_assumption(a, "purchase_discount_pct", DEFAULT_PURCHASE_DISCOUNT, notes))
    fees = _assumption(a, "channel_fees", {}, notes) or {}

    bronze = read_bronze(con)
    frames, excluded = conform_frames(bronze, vat_rate, discount, fees, as_of)
    counts: dict[str, int] = {}
    for table in CONFORM_TABLES:
        counts[table] = db.write_df(con, table, frames[table], mode="replace")
    counts["excluded_slugs"] = len(excluded)
    dropped = int(frames["devices"].attrs.get("dropped_no_catalogue", 0))
    fallbacks = int(frames["model_catalogue"].attrs.get("base_storage_fallbacks", 0))
    counts["devices_dropped_no_catalogue"] = dropped
    if excluded:
        shown = ", ".join(excluded[:8]) + (" ..." if len(excluded) > 8 else "")
        notes.append(f"{len(excluded)} catalogue slug(s) excluded (no launch date or no priced variant): {shown}")
    if dropped:
        notes.append(f"{dropped} goods receipt(s) dropped: slug not in the usable catalogue")
    if fallbacks:
        notes.append(f"{fallbacks} catalogue row(s) needed a base storage fallback (variant without storage_gb)")
    notes.append(f"vat_rate={vat_rate} purchase_discount_pct={discount} (assumption placeholders with owners)")
    if csv_dir is not None:
        written = write_compat_csvs(frames, Path(csv_dir), seed)
        counts["compat_csvs"] = len(written)
    db.finish_run(con, run_id, counts)
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="conform", run_id=run_id, started_at=started, finished_at=finished,
        seconds=(finished - started).total_seconds(), counts=counts, notes=notes,
    )


__all__ = [
    "CONFORM_TABLES",
    "read_bronze",
    "allocate_over_serials",
    "conform_frames",
    "write_compat_csvs",
    "run_conform",
]
