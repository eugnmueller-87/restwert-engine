"""Levers runner (spec v0.2 sections 7.5 and 7.6): silver in, gold out, ADV03 and ADV04.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``run_levers`` reads ``silver.device_ledger``, ``silver.ledger_lines``, ``silver.serial_timeline``,
``rv_forecast_of_record``, ``rv_forecast_grid`` (and ``bronze.rc_orders`` for the grade at
sale when present), attributes the seven levers, checks the additive identity, writes
``gold.levers_per_device``, ``gold.levers_by_cohort`` and ``gold.levers_summary`` in
``mode="replace"`` and re-appends the ``manufacturer_mix`` (ADV03) and ``term_gap`` (ADV04)
rows of ``advisories``. It refuses to run without the ledger (``ValueError("run ledger first")``).

The only I/O is DuckDB plus ``docs/LEVERS.md``, which is written only when the connection
is on the default database file (the CLI's ``_docs_wanted`` rule; ``write_docs`` overrides).

Choices where the spec is silent:

* ``lever_reference_min_n`` missing from ``assumptions.yaml`` falls back to 10 (the spec's
  value) and the run notes say so; the value is a placeholder either way.
* ADV03 needs at least ``lever_reference_min_n`` attributed L06 sales of the oem in the
  trailing 12 months before it fires, so one outlier device cannot raise a fleet advisory;
  the payload ``family`` is the catalogue family with the most such sales.
* ADV04 fires once per (model_family, purchase half-year) cohort; the payload carries the n
  of every term in the cohort as ``n_<term>``.
* The three gold tables are created when missing (``restwert.lake.schema_lake`` when that
  module is importable, else the same DDL kept here), so ``run_levers`` works on a database
  where the ledger was written by hand in a test.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from restwert import db, schema
from restwert.config import TERM_MONTHS, Assumptions, Thresholds
from restwert.forecast.advisory import ADVISORY_COLUMNS
from restwert.levers.attribution import DEFAULT_MIN_N, attribute_all, check_additivity
from restwert.levers.references import GridIndex, to_float
from restwert.levers.summary import levers_by_cohort, levers_summary, render_levers_md
from restwert.paths import DEFAULT_DB, DOCS_DIR
from restwert.records import Advisory, RunSummary

LEVERS_MD_PATH: Path = DOCS_DIR / "LEVERS.md"

ADVISORY_KINDS: tuple[str, ...] = ("manufacturer_mix", "term_gap")

#: DDL of the three gold tables (byte-identical to ``restwert.lake.schema_lake.GOLD_DDL``),
#: used only when that module is not importable.
_GOLD_LEVERS_DDL: dict[str, str] = {
    "gold.levers_per_device": """CREATE TABLE IF NOT EXISTS gold.levers_per_device (
  serial VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, delta_eur DECIMAL(12,2), actual_value DOUBLE, reference_value DOUBLE,
  reference_source VARCHAR NOT NULL, n_reference INTEGER NOT NULL, is_attributed BOOLEAN NOT NULL, additive BOOLEAN NOT NULL,
  basis VARCHAR NOT NULL, event_date DATE, counterfactual_json VARCHAR NOT NULL, as_of DATE NOT NULL, PRIMARY KEY (serial, lever_id));""",
    "gold.levers_by_cohort": """CREATE TABLE IF NOT EXISTS gold.levers_by_cohort (
  cohort_kind VARCHAR NOT NULL, cohort_value VARCHAR NOT NULL, lever_id VARCHAR NOT NULL, n_attributed INTEGER NOT NULL,
  sum_delta_eur DECIMAL(14,2), mean_delta_eur DECIMAL(12,2), as_of DATE NOT NULL, PRIMARY KEY (cohort_kind, cohort_value, lever_id));""",
    "gold.levers_summary": """CREATE TABLE IF NOT EXISTS gold.levers_summary (
  lever_id VARCHAR PRIMARY KEY, lever_name VARCHAR NOT NULL, component VARCHAR NOT NULL, basis VARCHAR NOT NULL, additive BOOLEAN NOT NULL,
  n_eligible INTEGER NOT NULL, n_attributed INTEGER NOT NULL, eur_per_device DECIMAL(12,2), eur_per_device_p90 DECIMAL(12,2),
  eur_fleet_per_year DECIMAL(14,2), share_of_lever_basis DOUBLE, lever_basis_eur DECIMAL(14,2), lever_basis VARCHAR NOT NULL,
  threshold_key VARCHAR NOT NULL, threshold_value VARCHAR NOT NULL,
  threshold_unit VARCHAR NOT NULL, threshold_owner VARCHAR NOT NULL, rule_id VARCHAR NOT NULL, reference_key VARCHAR NOT NULL,
  reference_owner VARCHAR NOT NULL, reference_sentence VARCHAR NOT NULL,
  rank INTEGER NOT NULL, as_of DATE NOT NULL);""",
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _read(con, sql: str, tables: tuple[str, ...]) -> pd.DataFrame:
    for t in tables:
        if not db.table_exists(con, t):
            return pd.DataFrame()
    return db.read_df(con, sql)


def ensure_gold_lever_tables(con) -> None:
    """Create the three gold lever tables when missing (schema_lake when importable, else the local DDL)."""
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    try:
        from restwert.lake.schema_lake import create_lake_schema  # type: ignore

        create_lake_schema(con, drop_layers=())
        if all(db.table_exists(con, t) for t in _GOLD_LEVERS_DDL):
            return
    except Exception:  # module 1 not there yet, or a signature drift: fall back to the frozen DDL
        pass
    for ddl in _GOLD_LEVERS_DDL.values():
        con.execute(ddl)


def _ensure_advisories_table(con) -> None:
    if not db.table_exists(con, "advisories"):
        con.execute(schema.DDL["advisories"])


def _on_default_db(con) -> bool:
    """True when the connection's main database file is ``paths.DEFAULT_DB``."""
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
    except Exception:
        return False
    for row in rows:
        file = str(row[2]) if len(row) > 2 else ""
        try:
            if file and Path(file).resolve() == Path(DEFAULT_DB).resolve():
                return True
        except OSError:
            continue
    return False


def _min_n(a: Assumptions, notes: list[str]) -> int:
    try:
        return int(a.get("lever_reference_min_n"))
    except KeyError:
        notes.append(f"lever_reference_min_n missing in assumptions.yaml; placeholder {DEFAULT_MIN_N} used")
        return DEFAULT_MIN_N


def _confidence(n: int) -> str:
    return "low" if n < 30 else "medium" if n < 100 else "high"


def _advisory_row(
    *,
    advisory_id: str,
    as_of: date,
    run_id: str,
    adv: Advisory,
    subject_type: str,
    subject_id: str,
    threshold_key: str,
    threshold_owner: str,
) -> dict[str, Any]:
    return {
        "advisory_id": advisory_id,
        "as_of": pd.Timestamp(as_of),
        "run_id": run_id,
        "kind": adv.kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "confidence": adv.confidence,
        "payload_json": json.dumps(adv.payload, sort_keys=True, default=str),
        "note": adv.note,
        "threshold_key": threshold_key,
        "threshold_owner": threshold_owner,
    }


# --------------------------------------------------------------------------------------
# ADV03 manufacturer mix, ADV04 term gap
# --------------------------------------------------------------------------------------


def manufacturer_mix_advisories(
    per_device: pd.DataFrame, dl: pd.DataFrame, thr: Thresholds, as_of: date, run_id: str, min_n: int
) -> pd.DataFrame:
    """ADV03: one row per oem whose mean L06 gap ratio over trailing-12-month sales exceeds ``oem_realisation_gap_pct``."""
    empty = pd.DataFrame(columns=ADVISORY_COLUMNS)
    if per_device is None or len(per_device) == 0:
        return empty
    t = thr.get("oem_realisation_gap_pct", as_of=as_of)
    l06 = per_device[(per_device["lever_id"] == "L06") & per_device["is_attributed"].astype(bool)].copy()
    if len(l06) == 0:
        return empty
    ev = pd.to_datetime(l06["event_date"], errors="coerce")
    end = pd.Timestamp(as_of)
    l06 = l06[(ev > end - pd.Timedelta(days=365)) & (ev <= end)]
    if len(l06) == 0:
        return empty
    keys = dl[["serial", "oem", "catalogue_family"]].copy()
    keys["serial"] = keys["serial"].astype(str)
    l06["serial"] = l06["serial"].astype(str)
    m = l06.merge(keys, on="serial", how="left")
    m = m[m["oem"].notna()]
    m["gap"] = pd.to_numeric(m["reference_value"], errors="coerce") - pd.to_numeric(m["actual_value"], errors="coerce")
    m["delta_eur"] = pd.to_numeric(m["delta_eur"], errors="coerce").astype(float)
    rows: list[dict[str, Any]] = []
    for oem, g in m.groupby(m["oem"].astype(str), sort=True):
        n = int(len(g))
        if n < int(min_n):
            continue
        mean_gap = float(g["gap"].mean())
        if mean_gap <= float(t.value):
            continue
        family = str(g["catalogue_family"].astype(str).mode().iloc[0]) if g["catalogue_family"].notna().any() else ""
        eur_year = round(float(g["delta_eur"].sum()), 2)
        adv = Advisory(
            kind="manufacturer_mix",
            run_id=run_id,
            payload={
                "oem": str(oem),
                "family": family,
                "n": n,
                "mean_gap_pct": round(mean_gap, 4),
                "threshold": t.value,
                "eur_fleet_per_year": eur_year,
            },
            confidence=_confidence(n),
            note=(
                f"advisory only: {oem} realised {mean_gap * 100:.1f} % of net RRP less than its catalogue "
                f"family's median over {n} sales in the trailing 12 months, beyond the "
                f"{float(t.value) * 100:.0f} % gap; {eur_year:.2f} EUR per year on lever L06. "
                f"A human reviews the manufacturer allocation. Owner: {t.owner}"
            ),
        )
        rows.append(
            _advisory_row(
                advisory_id=f"ADV03-{oem}-{as_of.isoformat()}",
                as_of=as_of,
                run_id=run_id,
                adv=adv,
                subject_type="oem",
                subject_id=str(oem),
                threshold_key=t.key,
                threshold_owner=t.owner,
            )
        )
    return pd.DataFrame(rows, columns=ADVISORY_COLUMNS) if rows else empty


def term_gap_advisories(per_device: pd.DataFrame, thr: Thresholds, as_of: date, run_id: str) -> pd.DataFrame:
    """ADV04: one row per (model_family, purchase half-year) whose L07 gap reaches ``term_result_gap_alert_eur``.

    ``gap_eur`` is the WIDEST L07 gap in the cohort: L07 scores every term against the best
    other term per month of term, times this term's months, so the maximum sits on the term
    with the widest scaled gap (per-month gap times its months), which is not always the worst
    term per month. The payload names that term as ``vs_term`` and its months as
    ``gap_horizon_months`` (the months the gap is scaled to); ``better_term`` is the term it
    lost against (the best other term of 12, 24, 36, 48 by median result per month).
    """
    empty = pd.DataFrame(columns=ADVISORY_COLUMNS)
    if per_device is None or len(per_device) == 0:
        return empty
    t = thr.get("term_result_gap_alert_eur", as_of=as_of)
    l07 = per_device[(per_device["lever_id"] == "L07") & per_device["is_attributed"].astype(bool)]
    if len(l07) == 0:
        return empty
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for r in l07.to_dict("records"):
        try:
            cf = json.loads(r.get("counterfactual_json") or "{}")
        except json.JSONDecodeError:
            continue
        key = (str(cf.get("model_family")), str(cf.get("purchase_half_year")))
        gap = to_float(cf.get("gap_signed_eur"))
        if gap is None:
            continue
        entry = seen.setdefault(key, {"gap": gap, "better_term": None, "vs_term": None, "n": {}})
        this_term, other_term = cf.get("this_term"), cf.get("other_term")
        if this_term is not None:
            entry["n"][int(this_term)] = int(cf.get("n_this_term") or 0)
        if other_term is not None:
            entry["n"][int(other_term)] = int(cf.get("n_other_term") or 0)
        if gap >= entry["gap"]:
            entry["gap"] = gap
            if gap > 0 and other_term is not None:
                entry["better_term"] = int(other_term)
                entry["vs_term"] = int(this_term) if this_term is not None else None
            else:
                entry["better_term"] = int(this_term) if this_term is not None else None
                entry["vs_term"] = int(other_term) if other_term is not None else None
            # the gap is (per-month gap) x this term's months, whatever its sign
            entry["gap_horizon_months"] = int(this_term) if this_term is not None else None
    rows: list[dict[str, Any]] = []
    for (family, hy), entry in sorted(seen.items()):
        gap = float(entry["gap"])
        if gap < float(t.value):
            continue
        n_total = int(sum(entry["n"].values()))
        payload: dict[str, Any] = {
            "family": family,
            "half_year": hy,
            "better_term": entry["better_term"],
            "vs_term": entry["vs_term"],
            "gap_eur": round(gap, 2),
            "gap_horizon_months": entry.get("gap_horizon_months"),
            "threshold": t.value,
        }
        # n per term: every term of config.TERM_MONTHS (0 when absent from the cohort), plus any other term seen
        for term in TERM_MONTHS:
            payload[f"n_{term}"] = int(entry["n"].get(term, 0))
        for term, n in sorted(entry["n"].items()):
            payload[f"n_{term}"] = int(n)
        adv = Advisory(
            kind="term_gap",
            run_id=run_id,
            payload=payload,
            confidence=_confidence(n_total),
            note=(
                f"advisory only: in {family} bought in {hy} the {entry['better_term']}-month term closed "
                f"{gap:.2f} EUR per device better than the {entry['vs_term']}-month term (the widest gap in the "
                f"cohort, per {entry.get('gap_horizon_months')} months of term; n {n_total}), at or above the "
                f"{float(t.value):.0f} EUR alert. A human reviews the term policy. Owner: {t.owner}"
            ),
        )
        rows.append(
            _advisory_row(
                advisory_id=f"ADV04-{family}-{hy}-{as_of.isoformat()}",
                as_of=as_of,
                run_id=run_id,
                adv=adv,
                subject_type="cohort",
                subject_id=f"{family}:{hy}",
                threshold_key=t.key,
                threshold_owner=t.owner,
            )
        )
    return pd.DataFrame(rows, columns=ADVISORY_COLUMNS) if rows else empty


def write_lever_advisories(con, frame: pd.DataFrame) -> int:
    """Delete the ADV03 / ADV04 rows of ``advisories`` and append the new ones."""
    _ensure_advisories_table(con)
    placeholders = ", ".join("?" for _ in ADVISORY_KINDS)
    con.execute(f"DELETE FROM advisories WHERE kind IN ({placeholders})", list(ADVISORY_KINDS))
    if frame is None or len(frame) == 0:
        return 0
    return int(db.append_rows(con, "advisories", frame))


# --------------------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------------------


def _read_inputs(con, notes: list[str]) -> dict[str, pd.DataFrame]:
    dl = db.read_df(con, 'SELECT * FROM "silver"."device_ledger" ORDER BY serial')
    if db.table_exists(con, "bronze.rc_orders") and "grade_at_sale" not in dl.columns:
        orders = db.read_df(
            con,
            """
            SELECT serial, grade_at_sale FROM "bronze"."rc_orders"
            QUALIFY row_number() OVER (PARTITION BY serial ORDER BY sold_at DESC, order_id DESC) = 1
            """,
        )
        if len(orders):
            orders["serial"] = orders["serial"].astype(str)
            dl["serial"] = dl["serial"].astype(str)
            dl = dl.merge(orders, on="serial", how="left")
    lines = _read(
        con,
        'SELECT serial, line_type, amount_eur, event_date, source_ref FROM "silver"."ledger_lines" WHERE line_type = \'repair\'',
        ("silver.ledger_lines",),
    )
    all_lines = _read(
        con,
        'SELECT serial, line_type, amount_eur FROM "silver"."ledger_lines" WHERE serial IN (SELECT serial FROM "silver"."device_ledger" WHERE is_closed)',
        ("silver.ledger_lines",),
    )
    if not db.table_exists(con, "silver.ledger_lines"):
        notes.append("silver.ledger_lines missing: L04 repair part reads no lines; additivity checked on the device ledger only")
    timeline = _read(con, 'SELECT * FROM "silver"."serial_timeline"', ("silver.serial_timeline",))
    record = _read(
        con,
        "SELECT serial, run_id, forecast_rv, channel_factor_employee_buyout, channel_factor_b2b_wholesale, is_missing "
        "FROM rv_forecast_of_record",
        ("rv_forecast_of_record",),
    )
    if len(record) == 0:
        notes.append("rv_forecast_of_record missing or empty: L03 not attributed")
    grid = _read(con, "SELECT model, grade, months_since_launch, forecast_rv_ratio, fit_quality FROM rv_forecast_grid", ("rv_forecast_grid",))
    if len(grid) == 0:
        notes.append("rv_forecast_grid missing or empty: L04 and L05 not attributed")
    return {"dl": dl, "lines": lines, "all_lines": all_lines, "timeline": timeline, "record": record, "grid": grid}


def run_levers(
    con,
    as_of: date,
    thr: Thresholds,
    a: Assumptions,
    write_docs: bool | None = None,
) -> RunSummary:
    """Attribute the levers, write the three gold tables and the ADV03 / ADV04 advisories.

    Counts: ``levers_per_device``, ``levers_attributed``, ``levers_summary``, ``levers_by_cohort``,
    ``adv03``, ``adv04``, ``additivity_violations`` (0, or the run raises). ``write_docs=None``
    writes ``docs/LEVERS.md`` only on the default database file.
    """
    if not db.table_exists(con, "silver.device_ledger"):
        raise ValueError("run ledger first: silver.device_ledger does not exist")
    started = datetime.now(UTC)
    run_id = db.new_run(con, "levers", None, as_of, None) if db.table_exists(con, "runs") else f"levers-{as_of.isoformat()}"
    notes: list[str] = []
    counts: dict[str, int] = {}

    inputs = _read_inputs(con, notes)
    dl = inputs["dl"]
    min_n = _min_n(a, notes)
    grid = GridIndex(inputs["grid"])

    per_device = attribute_all(dl, inputs["lines"], inputs["timeline"], inputs["record"], grid, thr, a, as_of)
    violations = check_additivity(dl, per_device, inputs["all_lines"] if len(inputs["all_lines"]) else None)
    counts["additivity_violations"] = int(len(violations))
    notes.append(
        "additivity checked against silver.ledger_lines" if violations.attrs.get("lines_used")
        else "additivity checked on the device ledger only (no lines): a formula consistency test"
    )
    if len(violations):
        sample = violations.head(5).to_dict("records")
        notes.append(f"additivity violations: {sample}")

    by_cohort = levers_by_cohort(per_device, dl, as_of=as_of)
    summary = levers_summary(per_device, dl, thr, as_of, a)

    ensure_gold_lever_tables(con)
    counts["levers_per_device"] = db.write_df(con, "gold.levers_per_device", per_device, mode="replace")
    counts["levers_attributed"] = int(per_device["is_attributed"].astype(bool).sum()) if len(per_device) else 0
    counts["levers_by_cohort"] = db.write_df(con, "gold.levers_by_cohort", by_cohort, mode="replace")
    counts["levers_summary"] = db.write_df(con, "gold.levers_summary", summary, mode="replace")

    adv03 = manufacturer_mix_advisories(per_device, dl, thr, as_of, run_id, min_n)
    adv04 = term_gap_advisories(per_device, thr, as_of, run_id)
    counts["adv03"] = int(len(adv03))
    counts["adv04"] = int(len(adv04))
    frames = [f for f in (adv03, adv04) if len(f)]
    write_lever_advisories(con, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=ADVISORY_COLUMNS))
    counts["serials"] = int(len(dl))

    docs = _on_default_db(con) if write_docs is None else bool(write_docs)
    if docs:
        LEVERS_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEVERS_MD_PATH, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_levers_md(thr))
        notes.append(f"levers doc written to {LEVERS_MD_PATH}")
    else:
        notes.append("levers doc not written (not the default database)")

    if len(violations):
        raise ValueError(
            f"levers: {len(violations)} additivity violation(s); first serial {violations.iloc[0]['serial']}"
        )

    finished = datetime.now(UTC)
    if db.table_exists(con, "runs"):
        db.finish_run(con, run_id, counts)
    return RunSummary(
        command="levers",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=round((finished - started).total_seconds(), 3),
        counts=counts,
        notes=notes,
    )


__all__ = [
    "LEVERS_MD_PATH",
    "ADVISORY_KINDS",
    "ensure_gold_lever_tables",
    "manufacturer_mix_advisories",
    "term_gap_advisories",
    "write_lever_advisories",
    "run_levers",
]
