"""Shared record types (SPEC.md section 2.5).

``Advisory`` is what a model may say. ``DecisionRecord`` is what deterministic
code decided, with the threshold and its named owner attached. The advisory
never changes the outcome; it rides along for the human reading the queue.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict


class ResolvedThreshold(BaseModel):
    """A threshold looked up from ``config/thresholds.yaml`` with its owner.

    ``resolved_key`` is ``"repair_max_share_of_rv[iphone_like]"`` when the
    per-family ``values`` map was used and equals ``key`` otherwise.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    resolved_key: str
    value: float | int | str | bool
    unit: str
    owner: str
    rule_ids: list[str]
    valid_from: date | None = None


class Advisory(BaseModel):
    """A model-side hint attached to a decision. Never an outcome."""

    kind: Literal[
        "sell_before_launch",
        "forecast_calibration",
        "forecast_confidence",
        "manufacturer_mix",  # v0.2 ADV03, written by restwert.levers.run
        "term_gap",  # v0.2 ADV04, written by restwert.levers.run
    ]
    run_id: str | None = None
    payload: dict[str, float | int | str | None]
    confidence: Literal["low", "medium", "high"]
    note: str = ""


class DecisionRecord(BaseModel):
    """One deterministic decision: rule, threshold, owner, inputs, optional advisory."""

    decision_id: str
    run_id: str
    decided_at: datetime
    as_of: date
    rule_id: str
    rule_version: str
    subject_type: str
    subject_id: str
    outcome: str
    outcome_detail: str
    threshold_key: str
    threshold_value: float | int | str | bool
    threshold_unit: str
    threshold_owner: str
    threshold_valid_from: date | None = None
    inputs: dict[str, Any]
    advisory: Advisory | None = None
    value_at_stake_eur: float | None = None
    due_date: date | None = None
    input_hash: str


def inputs_to_json(d: dict) -> str:
    """Canonical JSON for inputs: sorted keys, ``str()`` for dates and other non-JSON values."""
    return json.dumps(d, sort_keys=True, default=str)


def make_input_hash(rule_id: str, subject_id: str, as_of: date, inputs: dict) -> str:
    """sha256 over ``[rule_id, subject_id, as_of, inputs]`` as canonical JSON.

    Used by the decision log to skip a record whose inputs did not change.
    """
    payload = json.dumps([rule_id, subject_id, as_of, inputs], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_record(
    *,
    run_id: str,
    as_of: date,
    rule_id: str,
    rule_version: str,
    subject_type: str,
    subject_id: str,
    outcome: str,
    outcome_detail: str,
    thr: ResolvedThreshold,
    inputs: dict[str, Any],
    advisory: Advisory | None = None,
    value_at_stake_eur: float | None = None,
    due_date: date | None = None,
) -> DecisionRecord:
    """Assemble a ``DecisionRecord``; fills id, timestamp and input hash.

    ``threshold_key`` is the resolved key (with the family or channel suffix when
    a per-family threshold was used) so the log shows exactly which value fired;
    ``threshold_valid_from`` says which version of that threshold was in force.
    """
    return DecisionRecord(
        decision_id=uuid4().hex,
        run_id=run_id,
        decided_at=datetime.now(timezone.utc),
        as_of=as_of,
        rule_id=rule_id,
        rule_version=rule_version,
        subject_type=subject_type,
        subject_id=subject_id,
        outcome=outcome,
        outcome_detail=outcome_detail,
        threshold_key=thr.resolved_key,
        threshold_value=thr.value,
        threshold_unit=thr.unit,
        threshold_owner=thr.owner,
        threshold_valid_from=thr.valid_from,
        inputs=inputs,
        advisory=advisory,
        value_at_stake_eur=value_at_stake_eur,
        due_date=due_date,
        input_hash=make_input_hash(rule_id, subject_id, as_of, inputs),
    )


@dataclass
class KpiValue:
    """Result of one KPI computation.

    A KPI whose denominator is 0 or whose required column is entirely NULL is
    ``status = 'not_measurable'`` with ``value = None``, never 0.
    """

    value: float | None
    numerator: float | None
    denominator: float | None
    n: int
    status: Literal["ok", "not_measurable"] = "ok"
    note: str = ""
    breakdown: pd.DataFrame | None = field(default=None, repr=False)

    @staticmethod
    def not_measurable(note: str) -> "KpiValue":
        return KpiValue(value=None, numerator=None, denominator=None, n=0, status="not_measurable", note=note)


class RunSummary(BaseModel):
    """What one CLI step did: command, run id, timing, row counts, notes."""

    command: str
    run_id: str
    started_at: datetime
    finished_at: datetime
    seconds: float
    counts: dict[str, int]
    notes: list[str] = []


__all__ = [
    "ResolvedThreshold",
    "Advisory",
    "DecisionRecord",
    "inputs_to_json",
    "make_input_hash",
    "build_record",
    "KpiValue",
    "RunSummary",
]
