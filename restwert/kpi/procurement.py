"""Procurement KPIs (SPEC 7.2, area "Procurement").

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

- ``KPI_PROC_LANDED_VS_BENCHMARK`` "Landed cost per device vs benchmark"
- ``KPI_PROC_SPEND_UNDER_CONTRACT`` "Share of spend under contract" (delegates
  to ``restwert.contracts.register.contract_coverage``, the single definition)
- ``KPI_PROC_SUPPLIER_OTIF`` "Supplier OTIF"
- ``KPI_PROC_PPV`` "Purchase price variance (PPV)"

Choices where the spec is silent: OTIF and PPV window purchase orders by
``delivered_date`` (a PO without a delivery has neither an OTIF verdict nor a
paid price). The synthetic benchmark rows are generator band midpoints, as
their ``source_note`` says; nothing here quotes a market figure.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from restwert.config import KpiTargets
from restwert.contracts.register import contract_coverage
from restwert.kpi.registry import (
    KpiValue,
    fmt_window,
    load_table,
    make_breakdown,
    ok,
    ratio,
    ratio_breakdown,
    register,
    to_datetime,
    to_numeric,
    trailing_window,
    window_mask,
)


@register(
    kpi_id="KPI_PROC_LANDED_VS_BENCHMARK",
    name="Landed cost per device vs benchmark",
    area="Procurement",
    definition=(
        "Landed cost of devices purchased in the trailing 12 months relative to the benchmark landed cost valid "
        "at their purchase date (latest benchmarks row with valid_from <= purchase_date for the model). "
        "0 means on benchmark, positive means paid above it. Devices without a benchmark are excluded and counted."
    ),
    formula_text=(
        "sum(devices.landed_cost) / sum(benchmark valid at purchase_date) - 1, purchases in trailing 12 months, "
        "benchmark = latest benchmarks row with valid_from <= purchase_date for the model; "
        "devices without a benchmark excluded and counted in note"
    ),
    source_tables=("devices", "benchmarks"),
    measurable_from=(
        "a benchmark row per model; synthetic benchmark is a generator band (source_note), not a market figure"
    ),
    unit="ratio",
    direction="zero",
)
def landed_cost_vs_benchmark(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Landed cost over benchmark minus one, breakdown by model."""

    devices, note = load_table(con, "devices", ("model", "purchase_date", "landed_cost"))
    if devices is None:
        return KpiValue.not_measurable(note)
    bench, note = load_table(con, "benchmarks", ("model", "valid_from", "landed_cost_benchmark"))
    if bench is None:
        return KpiValue.not_measurable(note)
    to_datetime(devices, ["purchase_date"])
    to_numeric(devices, ["landed_cost"])
    to_datetime(bench, ["valid_from"])
    to_numeric(bench, ["landed_cost_benchmark"])

    start, end = trailing_window(as_of, 12)
    dev = devices[window_mask(devices["purchase_date"], start, end) & devices["landed_cost"].notna()].copy()
    if dev.empty:
        return KpiValue.not_measurable(f"no devices purchased between {fmt_window(start, end)}")

    bench = bench.dropna(subset=["valid_from", "landed_cost_benchmark"]).sort_values("valid_from")
    dev = dev.sort_values("purchase_date")
    dev["model"] = dev["model"].astype(str)
    bench["model"] = bench["model"].astype(str)
    merged = pd.merge_asof(
        dev,
        bench[["model", "valid_from", "landed_cost_benchmark"]],
        left_on="purchase_date",
        right_on="valid_from",
        by="model",
        direction="backward",
    )
    without = merged["landed_cost_benchmark"].isna()
    n_without = int(without.sum())
    matched = merged[~without]
    n = int(len(matched))
    if n == 0:
        return KpiValue.not_measurable(
            f"{n_without} device(s) purchased {fmt_window(start, end)} but none has a benchmark valid at purchase_date"
        )
    numerator = float(matched["landed_cost"].sum())
    denominator = float(matched["landed_cost_benchmark"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("benchmark sum is zero")
    rows = []
    for model, grp in matched.groupby("model", sort=True):
        num = float(grp["landed_cost"].sum())
        den = float(grp["landed_cost_benchmark"].sum())
        r = ratio(num, den)
        rows.append({"dimension_value": model, "value": None if r is None else r - 1.0, "numerator": num, "denominator": den, "n": int(len(grp))})
    note = (
        f"{n} devices with benchmark, {n_without} device(s) without benchmark excluded; window {fmt_window(start, end)}; "
        "benchmark rows are labelled by their source_note (synthetic: generator band midpoint)"
    )
    return ok(value - 1.0, numerator, denominator, n, note=note, breakdown=make_breakdown("model", rows))


@register(
    kpi_id="KPI_PROC_SPEND_UNDER_CONTRACT",
    name="Share of spend under contract",
    area="Procurement",
    definition=(
        "Share of hardware PO value (delivered, linked to a supplier contract) plus indirect spend flagged "
        "has_contract in all delivered PO value plus all indirect spend, trailing 12 months. One definition, "
        "shared with the contracts register (contracts.register.contract_coverage)."
    ),
    formula_text=(
        "(sum(po.qty_delivered * po.unit_price where supplier_contract_id is not null) + "
        "sum(indirect_spend.amount where has_contract)) / (sum(all PO value with delivered_date not null) + "
        "sum(all indirect amount)), trailing 12 months by delivered_date / invoice_date"
    ),
    source_tables=("purchase_orders", "supplier_contracts", "indirect_spend"),
    measurable_from="purchase orders carry supplier_contract_id and indirect invoices carry a has_contract flag",
    unit="ratio",
    direction="up",
)
def share_of_spend_under_contract(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """Delegates to ``contract_coverage`` so the KPI and the register never diverge."""

    po, _ = load_table(con, "purchase_orders", allow_empty=True)
    ind, _ = load_table(con, "indirect_spend", allow_empty=True)
    if po is None and ind is None:
        return KpiValue.not_measurable("neither purchase_orders nor indirect_spend exists")
    return contract_coverage(po, ind, as_of)


@register(
    kpi_id="KPI_PROC_SUPPLIER_OTIF",
    name="Supplier OTIF",
    area="Procurement",
    definition=(
        "Share of purchase orders delivered on time (delivered_date <= promised_date) and in full "
        "(qty_delivered >= qty_ordered) among purchase orders delivered in the trailing 12 months."
    ),
    formula_text=(
        "count(po where delivered_date <= promised_date and qty_delivered >= qty_ordered) / "
        "count(po where delivered_date is not null), trailing 12 months"
    ),
    source_tables=("purchase_orders",),
    measurable_from="promised_date populated",
    unit="ratio",
    direction="up",
)
def supplier_otif(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """On time in full share, breakdown by supplier."""

    po, note = load_table(con, "purchase_orders", ("promised_date", "delivered_date", "qty_ordered", "qty_delivered"))
    if po is None:
        return KpiValue.not_measurable(note)
    to_datetime(po, ["promised_date", "delivered_date"])
    to_numeric(po, ["qty_ordered", "qty_delivered"])
    start, end = trailing_window(as_of, 12)
    delivered = po[window_mask(po["delivered_date"], start, end) & po["promised_date"].notna()].copy()
    n = int(len(delivered))
    if n == 0:
        return KpiValue.not_measurable(f"no purchase order delivered between {fmt_window(start, end)}")
    delivered["otif"] = (
        (delivered["delivered_date"] <= delivered["promised_date"])
        & (delivered["qty_delivered"].fillna(0) >= delivered["qty_ordered"].fillna(0))
    ).astype(float)
    numerator = float(delivered["otif"].sum())
    denominator = float(n)
    breakdown = ratio_breakdown(delivered, "supplier", "otif", None)
    return ok(
        ratio(numerator, denominator),
        numerator,
        denominator,
        n,
        note=f"{int(numerator)} of {n} deliveries on time and in full; window {fmt_window(start, end)}",
        breakdown=breakdown,
    )


@register(
    kpi_id="KPI_PROC_PPV",
    name="Purchase price variance (PPV)",
    area="Procurement",
    definition=(
        "Paid unit price against the benchmark price on the purchase order, weighted by delivered quantity, "
        "trailing 12 months. Positive means paid above benchmark. The numerator is the EUR variance. Lines without "
        "a benchmark price are excluded and counted."
    ),
    formula_text=(
        "sum((unit_price - benchmark_price) * qty_delivered) / sum(benchmark_price * qty_delivered), "
        "trailing 12 months, lines without benchmark_price excluded and counted"
    ),
    source_tables=("purchase_orders",),
    measurable_from="a benchmark_price per PO line; on synthetic data it is the generator band midpoint",
    unit="ratio",
    direction="zero",
)
def purchase_price_variance(con: duckdb.DuckDBPyConnection, as_of: date, targets: KpiTargets) -> KpiValue:
    """PPV ratio with EUR variance as numerator, breakdown by supplier."""

    po, note = load_table(con, "purchase_orders", ("unit_price", "qty_delivered", "delivered_date"))
    if po is None:
        return KpiValue.not_measurable(note)
    if "benchmark_price" not in po.columns:
        return KpiValue.not_measurable("purchase_orders lacks column benchmark_price")
    to_datetime(po, ["delivered_date"])
    to_numeric(po, ["unit_price", "qty_delivered", "benchmark_price"])
    start, end = trailing_window(as_of, 12)
    lines = po[window_mask(po["delivered_date"], start, end)].copy()
    if lines.empty:
        return KpiValue.not_measurable(f"no purchase order delivered between {fmt_window(start, end)}")
    without = lines["benchmark_price"].isna() | lines["unit_price"].isna()
    n_without = int(without.sum())
    lines = lines[~without].copy()
    n = int(len(lines))
    if n == 0:
        return KpiValue.not_measurable(f"{n_without} delivered PO line(s) but none carries a benchmark_price")
    qty = lines["qty_delivered"].fillna(0)
    lines["variance_eur"] = (lines["unit_price"] - lines["benchmark_price"]) * qty
    lines["benchmark_eur"] = lines["benchmark_price"] * qty
    numerator = float(lines["variance_eur"].sum())
    denominator = float(lines["benchmark_eur"].sum())
    value = ratio(numerator, denominator)
    if value is None:
        return KpiValue.not_measurable("benchmark value is zero")
    breakdown = ratio_breakdown(lines, "supplier", "variance_eur", "benchmark_eur")
    return ok(
        value,
        numerator,
        denominator,
        n,
        note=f"variance {numerator:,.2f} EUR on {denominator:,.2f} EUR benchmark value; {n_without} line(s) without benchmark excluded; window {fmt_window(start, end)}",
        breakdown=breakdown,
    )
