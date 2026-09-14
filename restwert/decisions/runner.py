"""Decision runner (spec section 6.4): tables in, decision_log / write_down_ledger / decision_queue out.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``run_all_decisions`` reads the subject frames once (device_pnl, rv_forecast_current, events,
purchase_orders, supplier_contracts, rental_contracts, advisories), calls the pure rules in
``restwert.decisions.rules`` row by row, appends the records to the immutable ``decision_log``
(deduplicated on ``input_hash``), books R03 write-downs once per (serial, rule_id, as_of) in the
immutable ``write_down_ledger``, rebuilds ``decision_queue`` and regenerates
``docs/DECISION_RULES.md``. The only I/O is DuckDB plus that generated markdown file.

Choices where the spec is silent:

* ``build_queue(con, run_id)`` scopes on the ``as_of`` of the given run (falling back to the
  latest ``as_of`` in the log when the run appended nothing) and takes the most recent record per
  (rule_id, subject_type, subject_id) at that ``as_of``. A rerun on unchanged data appends 0 rows
  and rebuilds the identical queue from the earlier records.
* Subjects without the data a rule needs (an open damage quote whose device has no forecast row
  or no As-Is forecast, an in-stock device without a book value) are skipped and counted in
  ``RunSummary.counts`` under ``skipped_<rule>``; they are never decided on guessed inputs.
  R01 values a repaired device at the grade-B marketplace forecast
  (``rv_forecast_current.forecast_rv_grade_b``); that modelling choice is stated on the record
  (``inputs["forecast_rv_after_repair_source"]``) and in ``docs/DECISION_RULES.md``.
* The R06 signature of section 6.2 has no ``category`` parameter, so the supplier contract
  category is not part of the record inputs; the register carries it. Rental contracts get
  ``notice_days=None`` so the rule itself reads the ``rental_notice_days`` threshold and records
  its owner.
* ``run_all_decisions`` returns the rebuilt queue frame together with the run summary.
* Missing tables (for example ``advisories`` before the first forecast run) are treated as empty.
* ``build_queue`` also lists every ``forecast_calibration`` advisory (ADV02) as a priority-3 row
  with ``rule_id = 'ADV02'`` and ``subject_type = 'forecast'``, so the request "a human reviews
  the model" reaches the owner named in the threshold instead of resting in the advisories table.
  v0.2 generalises this to ``_ADVISORY_QUEUE``: ``manufacturer_mix`` (ADV03, subject oem) and
  ``term_gap`` (ADV04, subject cohort) from the levers module are queued the same way.
* R07 (v0.2) reads ``silver.device_ledger`` grouped by PO line when that table exists and
  returns no record otherwise, so a v0.1 database yields the v0.1 counts.
* ``write_docs=False`` skips the regeneration of ``docs/DECISION_RULES.md`` (the CLI passes it
  when the pipeline runs into a non-default directory, so a scratch run never rewrites the repo).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from restwert import db
from restwert.config import Assumptions, Thresholds
from restwert.dates import days_between
from restwert.paths import CONFIG_DIR
from restwert.records import Advisory, DecisionRecord, RunSummary, inputs_to_json

from restwert.decisions import rules
from restwert.decisions.registry import RULES, write_rules_doc
from restwert.decisions.rules import NO_ACTION_OUTCOMES, PRIORITY_1_OUTCOMES, SALE_CHANNELS

#: Column order of the decision_log table (spec section 2.8).
LOG_COLUMNS: tuple[str, ...] = (
    "decision_id",
    "run_id",
    "decided_at",
    "as_of",
    "rule_id",
    "rule_version",
    "subject_type",
    "subject_id",
    "outcome",
    "outcome_detail",
    "threshold_key",
    "threshold_value",
    "threshold_value_num",
    "threshold_unit",
    "threshold_owner",
    "threshold_valid_from",
    "inputs_json",
    "advisory_json",
    "value_at_stake_eur",
    "due_date",
    "input_hash",
    "is_synthetic_input",
)

#: Column order of the decision_queue table.
QUEUE_COLUMNS: tuple[str, ...] = (
    "decision_id",
    "run_id",
    "as_of",
    "priority",
    "rule_id",
    "rule_name",
    "outcome",
    "subject_type",
    "subject_id",
    "explanation",
    "threshold_key",
    "threshold_value",
    "threshold_owner",
    "due_date",
    "value_at_stake_eur",
    "advisory_kind",
    "advisory_confidence",
    "advisory_json",
)

#: Column order of the write_down_ledger table.
LEDGER_COLUMNS: tuple[str, ...] = (
    "write_down_id",
    "serial",
    "as_of",
    "rule_id",
    "decision_id",
    "book_value_before",
    "amount",
    "book_value_after",
    "threshold_owner",
    "run_id",
)


# --------------------------------------------------------------------------------------
# value coercion helpers (DuckDB frames arrive with Timestamps, Decimals, numpy scalars)
# --------------------------------------------------------------------------------------


def _is_null(x: Any) -> bool:
    if x is None:
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _as_date(x: Any) -> date | None:
    if _is_null(x):
        return None
    if isinstance(x, pd.Timestamp):
        return x.date()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return date.fromisoformat(x[:10])
    return pd.Timestamp(x).date()


def _as_float(x: Any) -> float | None:
    if _is_null(x):
        return None
    if isinstance(x, Decimal):
        return float(x)
    return float(x)


def _as_int(x: Any) -> int | None:
    if _is_null(x):
        return None
    return int(x)


def _as_bool(x: Any) -> bool | None:
    if _is_null(x):
        return None
    return bool(x)


def _as_str(x: Any) -> str | None:
    if _is_null(x):
        return None
    return str(x)


def _threshold_value_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _threshold_value_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _read(con, sql: str, tables: tuple[str, ...], params: list | None = None) -> pd.DataFrame:
    """Read a frame, returning an empty frame when one of the tables does not exist yet."""
    for t in tables:
        if not db.table_exists(con, t):
            return pd.DataFrame()
    return db.read_df(con, sql, params)


# --------------------------------------------------------------------------------------
# frames <-> records
# --------------------------------------------------------------------------------------


def records_to_frame(records: list[DecisionRecord], is_synthetic_input: bool) -> pd.DataFrame:
    """Turn records into a decision_log frame (JSON columns via ``records.inputs_to_json``)."""
    rows: list[dict[str, Any]] = []
    for r in records:
        rows.append(
            {
                "decision_id": r.decision_id,
                "run_id": r.run_id,
                "decided_at": _naive_utc(r.decided_at),
                "as_of": r.as_of,
                "rule_id": r.rule_id,
                "rule_version": r.rule_version,
                "subject_type": r.subject_type,
                "subject_id": r.subject_id,
                "outcome": r.outcome,
                "outcome_detail": r.outcome_detail,
                "threshold_key": r.threshold_key,
                "threshold_value": _threshold_value_str(r.threshold_value),
                "threshold_value_num": _threshold_value_num(r.threshold_value),
                "threshold_unit": r.threshold_unit,
                "threshold_owner": r.threshold_owner,
                "threshold_valid_from": r.threshold_valid_from,
                "inputs_json": inputs_to_json(r.inputs),
                "advisory_json": inputs_to_json(r.advisory.model_dump()) if r.advisory is not None else None,
                "value_at_stake_eur": r.value_at_stake_eur,
                "due_date": r.due_date,
                "input_hash": r.input_hash,
                "is_synthetic_input": bool(is_synthetic_input),
            }
        )
    frame = pd.DataFrame(rows, columns=list(LOG_COLUMNS))
    frame["decided_at"] = pd.to_datetime(frame["decided_at"])
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    frame["due_date"] = pd.to_datetime(frame["due_date"])
    frame["threshold_valid_from"] = pd.to_datetime(frame["threshold_valid_from"])
    frame["threshold_value_num"] = frame["threshold_value_num"].astype("float64")
    frame["value_at_stake_eur"] = frame["value_at_stake_eur"].astype("float64")
    frame["is_synthetic_input"] = frame["is_synthetic_input"].astype("bool")
    return frame


def write_log(con, frame: pd.DataFrame) -> int:
    """Append new rows to decision_log, skipping input hashes that already exist."""
    if frame.empty:
        return 0
    frame = frame.drop_duplicates(subset=["input_hash"], keep="first")
    existing: set[str] = set()
    if db.table_exists(con, "decision_log"):
        ex = db.read_df(con, "SELECT input_hash FROM decision_log")
        if not ex.empty:
            existing = set(ex["input_hash"].astype(str))
    new = frame[~frame["input_hash"].isin(existing)]
    if new.empty:
        return 0
    return int(db.append_rows(con, "decision_log", new.reset_index(drop=True)))


def book_write_downs(con, records: list[DecisionRecord], run_id: str) -> int:
    """Append write_down_ledger rows for R03 records with an amount, once per (serial, rule_id, as_of)."""
    candidates = [
        r
        for r in records
        if r.rule_id == "R03"
        and r.outcome != "none"
        and float(r.inputs.get("amount") or 0.0) > 0.0
    ]
    if not candidates:
        return 0

    existing: set[tuple[str, str, date]] = set()
    if db.table_exists(con, "write_down_ledger"):
        ex = db.read_df(con, "SELECT serial, rule_id, as_of FROM write_down_ledger")
        for row in ex.itertuples(index=False):
            existing.add((str(row.serial), str(row.rule_id), _as_date(row.as_of)))

    # the logged decision_id for the same inputs (the log wins over a fresh uuid on rerun)
    logged: dict[str, str] = {}
    if db.table_exists(con, "decision_log"):
        hashes = [r.input_hash for r in candidates]
        placeholders = ", ".join("?" for _ in hashes)
        lg = db.read_df(
            con,
            f"SELECT input_hash, decision_id FROM decision_log WHERE input_hash IN ({placeholders})",
            hashes,
        )
        if not lg.empty:
            logged = dict(zip(lg["input_hash"].astype(str), lg["decision_id"].astype(str)))

    rows: list[dict[str, Any]] = []
    for r in candidates:
        key = (r.subject_id, r.rule_id, r.as_of)
        if key in existing:
            continue
        existing.add(key)
        before = float(r.inputs["book_value_before"])
        amount = round(float(r.inputs["amount"]), 2)
        rows.append(
            {
                "write_down_id": uuid.uuid4().hex,
                "serial": r.subject_id,
                "as_of": r.as_of,
                "rule_id": r.rule_id,
                "decision_id": logged.get(r.input_hash, r.decision_id),
                "book_value_before": round(before, 2),
                "amount": amount,
                "book_value_after": round(before - amount, 2),
                "threshold_owner": r.threshold_owner,
                "run_id": run_id,
            }
        )
    if not rows:
        return 0
    frame = pd.DataFrame(rows, columns=list(LEDGER_COLUMNS))
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    return int(db.append_rows(con, "write_down_ledger", frame))


def _priority(rule_id: str, outcome: str, has_advisory: bool) -> int | None:
    """Queue priority, or None when the record is not queued."""
    if outcome in PRIORITY_1_OUTCOMES:
        return 1
    if outcome not in NO_ACTION_OUTCOMES.get(rule_id, set()):
        return 2
    if has_advisory:
        return 3
    return None


#: Advisory kinds that are queued on their own (priority 3): kind -> (rule_id, rule_name, outcome).
#: ADV02 comes from the forecast module, ADV03 and ADV04 from the levers module (v0.2).
_ADVISORY_QUEUE: dict[str, tuple[str, str, str]] = {
    "forecast_calibration": ("ADV02", "forecast_calibration", "review_model"),
    "manufacturer_mix": ("ADV03", "manufacturer_mix", "review_oem_allocation"),
    "term_gap": ("ADV04", "term_gap", "review_term_policy"),
}

_ADVISORY_DEFAULT_SUBJECT: dict[str, str] = {
    "forecast_calibration": "forecast",
    "manufacturer_mix": "oem",
    "term_gap": "cohort",
}

_ADVISORY_DEFAULT_EXPLANATION: dict[str, str] = {
    "forecast_calibration": "advisory only: review the residual value model",
    "manufacturer_mix": "advisory only: review the manufacturer allocation",
    "term_gap": "advisory only: review the contract term policy",
}


def advisory_queue_rows(con) -> list[dict[str, Any]]:
    """Queue rows (priority 3) for every advisory of a kind in ``_ADVISORY_QUEUE``.

    An advisory asks a named human to review something. It is not a decision and never
    changes one, but it needs a reader: the queue is where the owner looks. The threshold
    value comes from the payload key ``threshold`` (``threshold_bias_pct`` for ADV02).
    """
    kinds = list(_ADVISORY_QUEUE)
    placeholders = ", ".join("?" for _ in kinds)
    frame = _read(
        con,
        f"""
        SELECT advisory_id, as_of, run_id, kind, subject_type, subject_id, confidence,
               payload_json, note, threshold_key, threshold_owner
        FROM advisories
        WHERE kind IN ({placeholders})
        ORDER BY as_of DESC, advisory_id
        """,
        ("advisories",),
        kinds,
    )
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        kind = str(row.kind)
        rule_id, rule_name, outcome = _ADVISORY_QUEUE[kind]
        try:
            payload = json.loads(row.payload_json) if _as_str(row.payload_json) else {}
        except json.JSONDecodeError:
            payload = {}
        threshold_value = payload.get("threshold", payload.get("threshold_bias_pct"))
        adv_json = json.dumps(
            {
                "kind": kind,
                "run_id": _as_str(row.run_id),
                "payload": payload,
                "confidence": _as_str(row.confidence),
                "note": _as_str(row.note) or "",
            },
            sort_keys=True,
            default=str,
        )
        rows.append(
            {
                "decision_id": str(row.advisory_id),
                "run_id": _as_str(row.run_id),
                "as_of": _as_date(row.as_of),
                "priority": 3,
                "rule_id": rule_id,
                "rule_name": rule_name,
                "outcome": outcome,
                "subject_type": _as_str(row.subject_type) or _ADVISORY_DEFAULT_SUBJECT[kind],
                "subject_id": _as_str(row.subject_id) or "",
                "explanation": _as_str(row.note) or _ADVISORY_DEFAULT_EXPLANATION[kind],
                "threshold_key": _as_str(row.threshold_key),
                "threshold_value": _threshold_value_str(threshold_value) if threshold_value is not None else None,
                "threshold_owner": _as_str(row.threshold_owner),
                "due_date": None,
                "value_at_stake_eur": None,
                "advisory_kind": kind,
                "advisory_confidence": _as_str(row.confidence),
                "advisory_json": adv_json,
            }
        )
    return rows


#: v0.1 name kept as an alias (it now returns every queued advisory kind, ADV02 included).
calibration_advisory_rows = advisory_queue_rows


def build_queue(con, run_id: str) -> pd.DataFrame:
    """Rebuild the decision queue from the log: latest record per subject at the run's as_of.

    Advisories of the kinds in ``_ADVISORY_QUEUE`` (ADV02 forecast_calibration, ADV03
    manufacturer_mix, ADV04 term_gap) are appended as priority-3 rows so the review request
    reaches the threshold owner; see :func:`advisory_queue_rows`.
    """
    empty = pd.DataFrame(columns=list(QUEUE_COLUMNS))
    if not db.table_exists(con, "decision_log"):
        return empty
    sql = """
        WITH scope AS (
            SELECT coalesce(
                (SELECT max(as_of) FROM decision_log WHERE run_id = ?),
                (SELECT max(as_of) FROM decision_log)
            ) AS as_of
        )
        SELECT l.*
        FROM decision_log l, scope s
        WHERE l.as_of = s.as_of
        QUALIFY row_number() OVER (
            PARTITION BY l.rule_id, l.subject_type, l.subject_id
            ORDER BY l.decided_at DESC, l.decision_id DESC
        ) = 1
    """
    log = db.read_df(con, sql, [run_id])
    rows: list[dict[str, Any]] = advisory_queue_rows(con)
    if log.empty and not rows:
        return empty

    for row in log.itertuples(index=False):
        adv_json = _as_str(row.advisory_json)
        adv = json.loads(adv_json) if adv_json else None
        prio = _priority(str(row.rule_id), str(row.outcome), adv is not None)
        if prio is None:
            continue
        spec = RULES.get(str(row.rule_id))
        rows.append(
            {
                "decision_id": row.decision_id,
                "run_id": row.run_id,
                "as_of": _as_date(row.as_of),
                "priority": prio,
                "rule_id": row.rule_id,
                "rule_name": spec.name if spec else None,
                "outcome": row.outcome,
                "subject_type": row.subject_type,
                "subject_id": row.subject_id,
                "explanation": row.outcome_detail,
                "threshold_key": row.threshold_key,
                "threshold_value": row.threshold_value,
                "threshold_owner": row.threshold_owner,
                "due_date": _as_date(row.due_date),
                "value_at_stake_eur": _as_float(row.value_at_stake_eur),
                "advisory_kind": adv.get("kind") if adv else None,
                "advisory_confidence": adv.get("confidence") if adv else None,
                "advisory_json": adv_json,
            }
        )
    queue = pd.DataFrame(rows, columns=list(QUEUE_COLUMNS))
    if queue.empty:
        return queue
    queue["_stake"] = queue["value_at_stake_eur"].fillna(0.0)
    queue = (
        queue.sort_values(["priority", "_stake", "rule_id", "subject_id"], ascending=[True, False, True, True])
        .drop(columns=["_stake"])
        .reset_index(drop=True)
    )
    queue["priority"] = queue["priority"].astype("int64")
    queue["as_of"] = pd.to_datetime(queue["as_of"])
    queue["due_date"] = pd.to_datetime(queue["due_date"])
    queue["value_at_stake_eur"] = queue["value_at_stake_eur"].astype("float64")
    return queue


# --------------------------------------------------------------------------------------
# subject readers: one function per rule, each returns records and a skipped count
# --------------------------------------------------------------------------------------


def _advisories_by_device(con) -> dict[str, Advisory]:
    frame = _read(
        con,
        """
        SELECT subject_id, run_id, kind, confidence, payload_json, note
        FROM advisories
        WHERE kind = 'sell_before_launch' AND subject_type = 'device'
        QUALIFY row_number() OVER (PARTITION BY subject_id ORDER BY as_of DESC, advisory_id DESC) = 1
        """,
        ("advisories",),
    )
    out: dict[str, Advisory] = {}
    for row in frame.itertuples(index=False):
        try:
            payload = json.loads(row.payload_json) if _as_str(row.payload_json) else {}
        except json.JSONDecodeError:
            payload = {}
        payload = {k: (v if isinstance(v, (int, float, str)) or v is None else str(v)) for k, v in payload.items()}
        out[str(row.subject_id)] = Advisory(
            kind="sell_before_launch",
            run_id=_as_str(row.run_id),
            payload=payload,
            confidence=str(row.confidence),
            note=_as_str(row.note) or "",
        )
    return out


def _r01(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    frame = _read(
        con,
        """
        SELECT e.event_id, e.serial, e.cost,
               coalesce(f.model_family, d.model_family) AS model_family,
               f.forecast_rv_grade_b, f.forecast_rv_as_is
        FROM events e
        LEFT JOIN rv_forecast_current f ON f.serial = e.serial
        LEFT JOIN devices d ON d.serial = e.serial
        WHERE e.event_type = 'damage' AND e.resolved = false
        ORDER BY e.serial, e.event_id
        """,
        ("events", "rv_forecast_current", "devices"),
    )
    records: list[DecisionRecord] = []
    skipped = 0
    for row in frame.itertuples(index=False):
        family = _as_str(row.model_family)
        rv_b = _as_float(row.forecast_rv_grade_b)
        rv_as_is = _as_float(row.forecast_rv_as_is)
        quote = _as_float(row.cost)
        # a missing As-Is forecast is a missing input, not a zero: skip, never guess
        if family is None or rv_b is None or rv_as_is is None or quote is None:
            skipped += 1
            continue
        records.append(
            rules.decide_repair(
                serial=str(row.serial),
                family=family,
                repair_quote_eur=quote,
                forecast_rv_after_repair_eur=rv_b,
                forecast_rv_as_is_eur=rv_as_is,
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )
    return records, skipped


def _channel_assumptions(a: Assumptions) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    fee_pct: dict[str, float] = {}
    fee_fixed: dict[str, float] = {}
    days: dict[str, float] = {}
    for c in SALE_CHANNELS:
        try:
            block = a.get("channel_fees", c)
        except KeyError:
            continue
        fee_pct[c] = float(block.get("fee_pct", 0.0))
        fee_fixed[c] = float(block.get("fee_fixed_eur", 0.0))
        days[c] = float(block.get("days_to_cash", 0.0))
    return fee_pct, fee_fixed, days


def _r02(con, thr: Thresholds, a: Assumptions, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    frame = _read(
        con,
        """
        SELECT p.serial, p.model_family, p.grade_current,
               f.forecast_rv, f.forecast_rv_employee_buyout, f.forecast_rv_b2b_wholesale, f.forecast_rv_as_is,
               rc.effective_end
        FROM device_pnl p
        LEFT JOIN rv_forecast_current f ON f.serial = p.serial
        LEFT JOIN (
            SELECT serial, coalesce(actual_end_date, end_date) AS effective_end
            FROM rental_contracts
            QUALIFY row_number() OVER (PARTITION BY serial ORDER BY start_date DESC, contract_id DESC) = 1
        ) rc ON rc.serial = p.serial
        WHERE p.lifecycle_status = 'in_stock'
        ORDER BY p.serial
        """,
        ("device_pnl", "rv_forecast_current", "rental_contracts"),
    )
    if frame.empty:
        return [], 0
    fee_pct, fee_fixed, days = _channel_assumptions(a)
    holding = float(a.get("holding_cost_per_day_eur"))
    window = int(thr.get("employee_buyout_window_days", as_of=as_of).value)
    advisories = _advisories_by_device(con)

    records: list[DecisionRecord] = []
    skipped = 0
    for row in frame.itertuples(index=False):
        family = _as_str(row.model_family)
        grade = _as_str(row.grade_current)
        rv = {
            "marketplace": _as_float(row.forecast_rv),
            "employee_buyout": _as_float(row.forecast_rv_employee_buyout),
            "b2b_wholesale": _as_float(row.forecast_rv_b2b_wholesale),
            "as_is": _as_float(row.forecast_rv_as_is),
        }
        rv = {c: v for c, v in rv.items() if v is not None}
        if family is None or grade is None or not rv:
            skipped += 1
            continue
        end = _as_date(row.effective_end)
        buyout_eligible = end is not None and 0 <= days_between(end, as_of) <= window
        serial = str(row.serial)
        try:
            records.append(
                rules.decide_channel(
                    serial=serial,
                    family=family,
                    grade=grade,
                    forecast_rv_by_channel=rv,
                    fee_pct=fee_pct,
                    fee_fixed_eur=fee_fixed,
                    days_to_cash=days,
                    holding_cost_per_day=holding,
                    buyout_eligible=buyout_eligible,
                    thr=thr,
                    as_of=as_of,
                    run_id=run_id,
                    advisory=advisories.get(serial),
                )
            )
        except ValueError:
            skipped += 1
    return records, skipped


def _r03(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    frame = _read(
        con,
        """
        SELECT serial, days_in_stock, book_value, write_down_cum
        FROM device_pnl
        WHERE lifecycle_status = 'in_stock'
        ORDER BY serial
        """,
        ("device_pnl",),
    )
    records: list[DecisionRecord] = []
    skipped = 0
    for row in frame.itertuples(index=False):
        days = _as_int(row.days_in_stock)
        book = _as_float(row.book_value)
        if days is None or book is None:
            skipped += 1
            continue
        records.append(
            rules.decide_aging_write_down(
                serial=str(row.serial),
                days_in_stock=days,
                book_value_before=book,
                already_written_down=_as_float(row.write_down_cum) or 0.0,
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )
    return records, skipped


def _r04(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    frame = _read(
        con,
        """
        SELECT p.serial, p.model_family, p.months_since_launch, p.storage_gb,
               r.grade_inspected, r.wipe_certificate
        FROM device_pnl p
        LEFT JOIN (
            SELECT serial, grade_inspected, wipe_certificate
            FROM events
            WHERE event_type = 'return'
            QUALIFY row_number() OVER (
                PARTITION BY serial ORDER BY return_date DESC NULLS LAST, event_date DESC, event_id DESC
            ) = 1
        ) r ON r.serial = p.serial
        WHERE p.lifecycle_status IN ('wip', 'in_stock')
        ORDER BY p.serial
        """,
        ("device_pnl", "events"),
    )
    records: list[DecisionRecord] = []
    skipped = 0
    for row in frame.itertuples(index=False):
        family = _as_str(row.model_family)
        months = _as_float(row.months_since_launch)
        storage = _as_int(row.storage_gb)
        if family is None or months is None or storage is None:
            skipped += 1
            continue
        records.append(
            rules.decide_grade_a_replacement(
                serial=str(row.serial),
                family=family,
                grade_inspected=_as_str(row.grade_inspected),
                months_since_launch=months,
                storage_gb=storage,
                wipe_certificate=_as_bool(row.wipe_certificate),
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )
    return records, skipped


def _r05(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    frame = _read(
        con,
        """
        SELECT po.po_number, po.supplier, po.qty_delivered, po.price_drop_date, po.price_drop_amount,
               sc.price_protection, sc.price_protection_days
        FROM purchase_orders po
        LEFT JOIN supplier_contracts sc ON sc.supplier_contract_id = po.supplier_contract_id
        ORDER BY po.po_number
        """,
        ("purchase_orders", "supplier_contracts"),
    )
    records: list[DecisionRecord] = []
    for row in frame.itertuples(index=False):
        records.append(
            rules.decide_price_protection_reminder(
                po_number=str(row.po_number),
                supplier=str(row.supplier),
                has_price_protection=bool(_as_bool(row.price_protection) or False),
                price_protection_days=_as_int(row.price_protection_days),
                price_drop_date=_as_date(row.price_drop_date),
                price_drop_amount=_as_float(row.price_drop_amount),
                qty_delivered=_as_int(row.qty_delivered) or 0,
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )
    return records, 0


def _r06(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    records: list[DecisionRecord] = []
    skipped = 0
    sup = _read(
        con,
        """
        SELECT supplier_contract_id, supplier, end_date, notice_days, auto_renewal, spend_under_contract
        FROM supplier_contracts
        ORDER BY supplier_contract_id
        """,
        ("supplier_contracts",),
    )
    for row in sup.itertuples(index=False):
        end = _as_date(row.end_date)
        if end is None:
            skipped += 1
            continue
        records.append(
            rules.decide_renewal_alert(
                contract_type="supplier_contract",
                contract_id=str(row.supplier_contract_id),
                counterparty=str(row.supplier),
                end_date=end,
                notice_days=_as_int(row.notice_days) or 0,
                auto_renewal=bool(_as_bool(row.auto_renewal) or False),
                annual_value_eur=_as_float(row.spend_under_contract) or 0.0,
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )

    ren = _read(
        con,
        """
        SELECT contract_id, customer_id, coalesce(actual_end_date, end_date) AS end_date, monthly_rate
        FROM rental_contracts
        WHERE status = 'active'
        ORDER BY contract_id
        """,
        ("rental_contracts",),
    )
    for row in ren.itertuples(index=False):
        end = _as_date(row.end_date)
        if end is None:
            skipped += 1
            continue
        records.append(
            rules.decide_renewal_alert(
                contract_type="rental_contract",
                contract_id=str(row.contract_id),
                counterparty=str(row.customer_id),
                end_date=end,
                notice_days=None,  # the rule reads rental_notice_days itself and records its owner
                auto_renewal=False,
                annual_value_eur=round((_as_float(row.monthly_rate) or 0.0) * 12, 2),
                thr=thr,
                as_of=as_of,
                run_id=run_id,
            )
        )
    return records, skipped


def _r07(con, thr: Thresholds, as_of: date, run_id: str) -> tuple[list[DecisionRecord], int]:
    """R07 on every PO line of ``silver.device_ledger`` (v0.2); ``([], 0)`` on a v0.1 database.

    One record per (po_number, po_line): qty = serials received on the line, unit price = the
    ledger's purchase price (identical on every serial of the line by construction), net RRP
    from the catalogue. Lines without an oem, a price or an RRP, or whose oem has no floor in
    ``thresholds.yaml``, are skipped and counted, never decided on a guess.
    """
    if not db.table_exists(con, "silver.device_ledger"):
        return [], 0
    frame = db.read_df(
        con,
        """
        SELECT po_number, po_line, oem, supplier_name, supplier_role,
               count(*) AS qty, avg(purchase_price) AS unit_price_eur, max(rrp_net_eur) AS rrp_net_eur
        FROM "silver"."device_ledger"
        WHERE po_number IS NOT NULL AND purchase_price IS NOT NULL
        GROUP BY po_number, po_line, oem, supplier_name, supplier_role
        ORDER BY po_number, po_line
        """,
    )
    records: list[DecisionRecord] = []
    skipped = 0
    for row in frame.itertuples(index=False):
        oem = _as_str(row.oem)
        unit = _as_float(row.unit_price_eur)
        rrp = _as_float(row.rrp_net_eur)
        if oem is None or unit is None or rrp is None or rrp <= 0:
            skipped += 1
            continue
        line = _as_int(row.po_line)
        po_line_id = f"{row.po_number}-{line}" if line is not None else str(row.po_number)
        try:
            records.append(
                rules.decide_purchase_floor(
                    po_line_id=po_line_id,
                    oem=oem,
                    supplier_name=_as_str(row.supplier_name) or "",
                    supplier_role=_as_str(row.supplier_role) or "",
                    unit_price_eur=round(unit, 2),
                    rrp_net_eur=round(rrp, 2),
                    qty=int(row.qty),
                    thr=thr,
                    as_of=as_of,
                    run_id=run_id,
                )
            )
        except (KeyError, ValueError):
            skipped += 1  # no floor for this manufacturer, or the threshold is not in force yet
    return records, skipped


# --------------------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------------------


def _thresholds_sha() -> str | None:
    try:
        from restwert.config import file_sha256

        path = CONFIG_DIR / "thresholds.yaml"
        return file_sha256(path) if path.exists() else None
    except Exception:  # pragma: no cover - hashing is bookkeeping, never a reason to stop
        return None


def run_all_decisions(
    con,
    as_of: date,
    thr: Thresholds,
    a: Assumptions,
    run_id: str | None = None,
    write_docs: bool = True,
) -> tuple[pd.DataFrame, RunSummary]:
    """Run every rule on the current tables and persist log, ledger, queue and rules doc.

    Returns the rebuilt ``decision_queue`` frame and a ``RunSummary`` whose ``counts`` hold the
    number of records per rule, rows appended to the log, write-downs booked, queue size and
    subjects skipped for missing inputs. ``write_docs=False`` leaves ``docs/DECISION_RULES.md``
    untouched.
    """
    started = datetime.now(UTC)
    own_run = run_id is None
    if own_run:
        run_id = db.new_run(con, "decide", None, as_of, _thresholds_sha())
    assert run_id is not None

    notes: list[str] = []
    counts: dict[str, int] = {}
    records: list[DecisionRecord] = []

    for rule_id, fn in (
        ("R01", lambda: _r01(con, thr, as_of, run_id)),
        ("R02", lambda: _r02(con, thr, a, as_of, run_id)),
        ("R03", lambda: _r03(con, thr, as_of, run_id)),
        ("R04", lambda: _r04(con, thr, as_of, run_id)),
        ("R05", lambda: _r05(con, thr, as_of, run_id)),
        ("R06", lambda: _r06(con, thr, as_of, run_id)),
        ("R07", lambda: _r07(con, thr, as_of, run_id)),
    ):
        recs, skipped = fn()
        records.extend(recs)
        counts[f"records_{rule_id}"] = len(recs)
        if skipped:
            counts[f"skipped_{rule_id}"] = skipped
            notes.append(f"{rule_id}: {skipped} subject(s) skipped for missing inputs")

    counts["records_total"] = len(records)
    synthetic = bool(db.is_synthetic(con))
    frame = records_to_frame(records, is_synthetic_input=synthetic)
    counts["log_appended"] = write_log(con, frame)
    counts["write_downs_booked"] = book_write_downs(con, records, run_id)

    queue = build_queue(con, run_id)
    db.write_df(con, "decision_queue", queue, mode="replace")
    counts["queue"] = int(len(queue))
    counts["queue_priority_1"] = int((queue["priority"] == 1).sum()) if not queue.empty else 0

    if write_docs:
        doc_path = write_rules_doc(thr)
        notes.append(f"rules doc written to {doc_path}")
    else:
        notes.append("rules doc not written (write_docs=False)")
    if counts["log_appended"] == 0 and records:
        notes.append("no new decisions: every input hash already existed in decision_log")

    finished = datetime.now(UTC)
    if own_run:
        db.finish_run(con, run_id, counts)
    summary = RunSummary(
        command="decide",
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        seconds=round((finished - started).total_seconds(), 3),
        counts=counts,
        notes=notes,
    )
    return queue, summary


__all__ = [
    "LOG_COLUMNS",
    "QUEUE_COLUMNS",
    "LEDGER_COLUMNS",
    "records_to_frame",
    "write_log",
    "book_write_downs",
    "build_queue",
    "advisory_queue_rows",
    "calibration_advisory_rows",
    "run_all_decisions",
]
