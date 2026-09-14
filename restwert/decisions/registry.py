"""Rule registry and generator of docs/DECISION_RULES.md (spec section 6.3).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``RULES`` is the single list of deterministic rules the runner knows. ``ADVISORY_SPECS``
documents the two advisories the forecast module produces; they are attached to records and
never become outcomes. ``render_rules_doc`` turns registry plus ``config/thresholds.yaml`` into
markdown, so the documentation can never drift from the code: every threshold key, its value,
unit, owner, rationale and placeholder flag come from the loaded yaml at render time.

The "Inputs" line of every rule section is read from the function signature with ``inspect``,
so adding a parameter to a rule changes the document on the next run.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from restwert.config import Thresholds
from restwert.paths import DOCS_DIR
from restwert.records import DecisionRecord

from restwert.decisions import rules as _rules
from restwert.decisions.rules import (
    NO_ACTION_OUTCOMES,
    PRIORITY_1_OUTCOMES,
    RULE_VERSION,
    SALE_CHANNELS,
)

try:  # the foundation package constant; fall back to the same sentence when module 1 is absent
    from restwert import GOVERNANCE_PRINCIPLE
except ImportError:  # pragma: no cover - only during the parallel build window
    GOVERNANCE_PRINCIPLE = (
        "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD."
    )

#: Parameters every rule takes that are not business inputs.
_CONTEXT_PARAMS: frozenset[str] = frozenset({"thr", "as_of", "run_id", "advisory"})


@dataclass(frozen=True)
class RuleSpec:
    """Metadata of one deterministic rule."""

    rule_id: str
    name: str
    version: str
    subject_type: str
    threshold_keys: tuple[str, ...]
    outcomes: tuple[str, ...]
    description: str
    fn: Callable[..., DecisionRecord]

    @property
    def input_names(self) -> tuple[str, ...]:
        """Business inputs of the rule, read from the function signature."""
        params = inspect.signature(self.fn).parameters
        return tuple(p for p in params if p not in _CONTEXT_PARAMS)

    @property
    def accepts_advisory(self) -> bool:
        """True when the rule can carry a forecast advisory on its record."""
        return "advisory" in inspect.signature(self.fn).parameters


RULES: dict[str, RuleSpec] = {
    "R01": RuleSpec(
        rule_id="R01",
        name="repair_or_not",
        version=RULE_VERSION,
        subject_type="device",
        threshold_keys=("repair_max_share_of_rv", "repair_min_rv_eur"),
        outcomes=("repair", "no_repair_quote_above_share", "no_repair_below_min_rv"),
        description=(
            "Repair a damaged device only when the forecast residual value after repair is at "
            "least the family minimum and the repair quote is at most the family share of that "
            "value (equality repairs). Value at stake: forecast RV after repair minus quote minus "
            "the As Is value of the broken device. Modelling choice: a repaired device is valued "
            "at the grade-B marketplace forecast (rv_forecast_current.forecast_rv_grade_b); the "
            "As Is value is the family As-Is ratio times purchase price. A device without either "
            "forecast is skipped, never decided on a zero."
        ),
        fn=_rules.decide_repair,
    ),
    "R02": RuleSpec(
        rule_id="R02",
        name="channel_choice",
        version=RULE_VERSION,
        subject_type="device",
        threshold_keys=(
            "channel_min_net_uplift_eur",
            "channel_max_days_to_cash",
            "as_is_only_grade",
            "employee_buyout_window_days",
        ),
        outcomes=SALE_CHANNELS,
        description=(
            "Grade D goes As Is only, other grades never As Is; employee buyout only inside the "
            "buyout window after the rental ended; channels slower than the maximum days to cash "
            "are dropped, never the last one. Net per channel = forecast RV x (1 - fee share) - "
            "fixed fee - holding cost per day x days to cash. The best net wins over the fastest "
            "channel only when it beats it by at least the uplift threshold (equality: the slower, "
            "better channel wins). The outcome is the chosen channel. The record names the "
            "threshold that decided it (as_is_only_grade for a grade-D device, "
            "employee_buyout_window_days when the closed window removed the better channel, "
            "channel_max_days_to_cash when that filter did, else channel_min_net_uplift_eur) and "
            "lists all four with their owners in inputs.thresholds_consulted."
        ),
        fn=_rules.decide_channel,
    ),
    "R03": RuleSpec(
        rule_id="R03",
        name="aging_write_down",
        version=RULE_VERSION,
        subject_type="device",
        threshold_keys=(
            "aging_days_90",
            "aging_days_180",
            "age_write_down_pct_90",
            "age_write_down_pct_180",
        ),
        outcomes=("none", "write_down_90", "write_down_180"),
        description=(
            "Sellable stock strictly older than the 180-day bucket is written down to the 180 "
            "percentage of its gross book value, older than the 90-day bucket to the 90 percentage. "
            "Gross book value = book value before + already written down; the amount booked is the "
            "cumulative target minus what was already written down, never negative. The runner "
            "books the amount in write_down_ledger once per serial, rule and as_of."
        ),
        fn=_rules.decide_aging_write_down,
    ),
    "R04": RuleSpec(
        rule_id="R04",
        name="grade_a_replacement",
        version=RULE_VERSION,
        subject_type="device",
        threshold_keys=(
            "replacement_max_months_since_launch",
            "replacement_min_storage_gb",
            "replacement_requires_wipe",
        ),
        outcomes=("eligible_as_replacement", "not_eligible"),
        description=(
            "A returned device may serve as a replacement when, in this order, its inspected grade "
            "is A, its months since launch are at most the family maximum (equality eligible), its "
            "storage is at least the family minimum and, when required, a wipe certificate is on "
            "file. The record names the first failing condition and carries its threshold."
        ),
        fn=_rules.decide_grade_a_replacement,
    ),
    "R05": RuleSpec(
        rule_id="R05",
        name="price_protection_reminder",
        version=RULE_VERSION,
        subject_type="purchase_order",
        threshold_keys=("price_protection_reminder_days", "price_protection_min_claim_eur"),
        outcomes=(
            "no_action",
            "below_min_claim",
            "claim_window_expired",
            "claim_reminder",
            "claim_open",
        ),
        description=(
            "For every PO under a contract with price protection and a recorded price drop: claim "
            "value = drop amount x quantity delivered, deadline = drop date + protection days. Below "
            "the minimum claim nothing happens; a passed deadline is logged as expired; a deadline "
            "within the reminder lead raises the reminder (equality reminds); otherwise the claim is "
            "open. The tool reminds, a human files the claim."
        ),
        fn=_rules.decide_price_protection_reminder,
    ),
    "R06": RuleSpec(
        rule_id="R06",
        name="renewal_alert",
        version=RULE_VERSION,
        subject_type="supplier_contract | rental_contract",
        threshold_keys=("renewal_alert_lead_days", "renewal_high_value_eur", "rental_notice_days"),
        outcomes=(
            "expired",
            "notice_missed_auto_renews",
            "expiring_no_notice_possible",
            "renewal_alert_high_value",
            "renewal_alert",
            "no_action",
        ),
        description=(
            "Notice deadline = end date - notice days. An ended contract is expired; a passed "
            "deadline with auto-renewal means the contract renews itself; a passed deadline without "
            "means it runs out; a deadline within the alert lead raises the alert (equality alerts), "
            "escalated when the annual value reaches the high-value threshold. Rental contracts have "
            "no notice field: their notice period is the threshold rental_notice_days (owner Head of "
            "Customer Success), which the rule reads itself and writes into the record inputs and "
            "inputs.thresholds_consulted."
        ),
        fn=_rules.decide_renewal_alert,
    ),
    "R07": RuleSpec(
        rule_id="R07",
        name="purchase_discount_floor",
        version=RULE_VERSION,
        subject_type="purchase_order",
        threshold_keys=("purchase_discount_floor_pct",),
        outcomes=("at_or_above_floor", "below_floor_discount"),
        description=(
            "v0.2, one record per PO line of the device ledger: discount = 1 - unit price / net "
            "launch RRP (catalogue RRP net of VAT). At or above the manufacturer's floor nothing "
            "happens (equality passes, logged, not queued); below it the line is queued for the Head "
            "of Procurement with value at stake = (unit price - floor price) x quantity, floor price = "
            "net RRP x (1 - floor), positive when the rule fires. The tool queues; a human negotiates. Runs only when "
            "silver.device_ledger exists (0 records on a v0.1 database)."
        ),
        fn=_rules.decide_purchase_floor,
    ),
}

#: Advisories produced by the forecast module (spec section 5.6) and attached to records here.
ADVISORY_SPECS: dict[str, dict] = {
    "ADV01": {
        "kind": "sell_before_launch",
        "name": "sell_before_launch",
        "subject_type": "device",
        "threshold_keys": ("sell_before_launch_lookahead_days", "sell_before_launch_min_drop_pct"),
        "attached_to": ("R02",),
        "description": (
            "Raised by the forecast module for in-stock or WIP devices when the next launch of the "
            "family falls inside the lookahead window and the forecast residual value after that "
            "launch, net of holding cost, is lower by at least the minimum drop. It rides along on "
            "the R02 channel decision so the human sees it; it never changes the chosen channel."
        ),
        "produced_by": "restwert.forecast.advisory.sell_before_launch",
    },
    "ADV02": {
        "kind": "forecast_calibration",
        "name": "forecast_calibration",
        "subject_type": "forecast",
        "threshold_keys": ("forecast_recalibration_bias_pct",),
        "attached_to": (),
        "description": (
            "Raised by the forecast module when the trailing three-month channel-adjusted forecast "
            "bias (realised price against the forecast of record multiplied by the run's factor for "
            "the channel actually used) exceeds the threshold. It asks a named human to review the "
            "model; nothing in the tool refits or changes a decision because of it. The runner lists "
            "it in decision_queue as a priority-3 row (rule_id ADV02, subject_type forecast) and the "
            "dashboard shows it under the forecast error chart."
        ),
        "produced_by": "restwert.forecast.advisory.calibration_advisory",
    },
    "ADV03": {
        "kind": "manufacturer_mix",
        "name": "manufacturer_mix",
        "subject_type": "oem",
        "threshold_keys": ("oem_realisation_gap_pct",),
        "attached_to": (),
        "description": (
            "v0.2, raised by the levers module for every manufacturer whose mean lever L06 gap "
            "(the fleet's family median realised share of net RRP minus the manufacturer's own, over "
            "sales in the trailing 12 months) exceeds the threshold. It asks the Category Manager "
            "Hardware to review the manufacturer allocation; nothing in the tool changes the mix. "
            "The runner lists it in decision_queue as a priority-3 row (rule_id ADV03, subject_type oem)."
        ),
        "produced_by": "restwert.levers.run.manufacturer_mix_advisories",
    },
    "ADV04": {
        "kind": "term_gap",
        "name": "term_gap",
        "subject_type": "cohort",
        "threshold_keys": ("term_result_gap_alert_eur",),
        "attached_to": (),
        "description": (
            "v0.2, raised by the levers module for every (segment, purchase half-year) cohort in "
            "which a contract term closed worse than the best other term of 12, 24, 36, 48 by at "
            "least the threshold per device (lever L07: median closed result per month of term, "
            "times this term's months; this term and at least one other term above the minimum "
            "group size). The advisory names the widest gap in the cohort, the better term and the "
            "term it beat. It asks the CFO to review the term policy; nothing in the tool changes a "
            "contract. The runner lists it in decision_queue as a priority-3 row (rule_id ADV04, "
            "subject_type cohort)."
        ),
        "produced_by": "restwert.levers.run.term_gap_advisories",
    },
}


# --------------------------------------------------------------------------------------
# markdown rendering
# --------------------------------------------------------------------------------------


def _fmt_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _threshold_rows(thr: Thresholds, key: str) -> list[str]:
    """Markdown table rows for one yaml key (one row per family/channel when `values` is set)."""
    spec = thr.thresholds.get(key)
    if spec is None:
        return [f"| `{key}` | MISSING IN YAML | | | | | |"]
    flag = "placeholder" if spec.placeholder_default else "signed"
    rationale = spec.rationale.replace("|", "/")
    valid_from = spec.valid_from.isoformat() if isinstance(spec.valid_from, date) else str(spec.valid_from)
    rows: list[str] = []
    if spec.values is not None:
        for sub, v in spec.values.items():
            rows.append(
                f"| `{key}[{sub}]` | {_fmt_value(v)} | {spec.unit} | {spec.owner} | {flag} | "
                f"{valid_from} | {rationale} |"
            )
    else:
        rows.append(
            f"| `{key}` | {_fmt_value(spec.value)} | {spec.unit} | {spec.owner} | {flag} | "
            f"{valid_from} | {rationale} |"
        )
    return rows


_TABLE_HEADER = [
    "| threshold | value | unit | owner | status | valid from | rationale |",
    "|---|---|---|---|---|---|---|",
]


def render_rules_doc(thr: Thresholds) -> str:
    """Render the decision rules document from ``RULES``, ``ADVISORY_SPECS`` and the thresholds."""
    lines: list[str] = []
    lines.append("# Decision rules")
    lines.append("")
    lines.append(f"> {GOVERNANCE_PRINCIPLE}")
    lines.append("")
    lines.append(
        "GENERATED by `restwert.decisions.registry.write_rules_doc` from the rule registry and "
        f"`config/thresholds.yaml` (version {thr.version}). Do not edit by hand; rerun "
        "`python -m restwert decide` to refresh."
    )
    lines.append("")
    lines.append(
        "Every rule is a pure function of plain inputs and the thresholds file. It returns a "
        "decision record with the rule id and version, the subject, the outcome, one human "
        "sentence explaining it, the threshold that fired (key, value, unit, owner), every input "
        "it read, an optional forecast advisory and a hash of the inputs. Records are appended to "
        "the immutable `decision_log` and deduplicated on that hash. Nothing in the tool orders, "
        "lists, mails or files anything; a named human acts on the queue."
    )
    lines.append("")
    lines.append(
        "Status `placeholder` means the value is a synthetic design parameter without an "
        "external source; `signed` means a named owner has confirmed it."
    )
    lines.append("")

    for rule_id in sorted(RULES):
        r = RULES[rule_id]
        lines.append(f"## {r.rule_id} {r.name} (version {r.version})")
        lines.append("")
        lines.append(f"- Subject: `{r.subject_type}`")
        lines.append("- Inputs: " + ", ".join(f"`{p}`" for p in r.input_names))
        lines.append(f"- Logic: {r.description}")
        lines.append("- Outcomes: " + ", ".join(f"`{o}`" for o in r.outcomes))
        no_action = sorted(NO_ACTION_OUTCOMES.get(rule_id, set()))
        lines.append(
            "- Logged but not queued: "
            + (", ".join(f"`{o}`" for o in no_action) if no_action else "none (every outcome is queued)")
        )
        p1 = [o for o in r.outcomes if o in PRIORITY_1_OUTCOMES]
        if p1:
            lines.append("- Queue priority 1: " + ", ".join(f"`{o}`" for o in p1))
        lines.append(f"- Carries advisory: {'yes' if r.accepts_advisory else 'no'}")
        lines.append("")
        lines.append("Thresholds:")
        lines.append("")
        lines.extend(_TABLE_HEADER)
        for key in r.threshold_keys:
            lines.extend(_threshold_rows(thr, key))
        lines.append("")

    lines.append("## Advisories (never outcomes)")
    lines.append("")
    for adv_id in sorted(ADVISORY_SPECS):
        a = ADVISORY_SPECS[adv_id]
        lines.append(f"### {adv_id} {a['name']}")
        lines.append("")
        lines.append(f"- Kind: `{a['kind']}`, subject `{a['subject_type']}`, produced by `{a['produced_by']}`")
        attached = ", ".join(a["attached_to"]) if a["attached_to"] else "no rule (queued on its own)"
        lines.append(f"- Attached to: {attached}")
        lines.append(f"- {a['description']}")
        lines.append("")
        lines.extend(_TABLE_HEADER)
        for key in a["threshold_keys"]:
            lines.extend(_threshold_rows(thr, key))
        lines.append("")

    lines.append("## Queue priority")
    lines.append("")
    lines.append("- 1: " + ", ".join(f"`{o}`" for o in sorted(PRIORITY_1_OUTCOMES)))
    lines.append("- 2: every other queued outcome")
    lines.append("- 3: records whose outcome is a no-action outcome but which carry an advisory")
    lines.append("")

    lines.append("## All thresholds in config/thresholds.yaml")
    lines.append("")
    lines.extend(_TABLE_HEADER)
    for key in thr.thresholds:
        lines.extend(_threshold_rows(thr, key))
    lines.append("")
    lines.append("Rule ids per threshold: " + "; ".join(
        f"`{key}` -> {', '.join(spec.rule_ids)}" for key, spec in thr.thresholds.items()
    ))
    lines.append("")
    return "\n".join(lines)


def write_rules_doc(thr: Thresholds, path: Path = DOCS_DIR / "DECISION_RULES.md") -> Path:
    """Write the rendered document as UTF-8 without BOM and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_rules_doc(thr))
    return path


__all__ = [
    "RuleSpec",
    "RULES",
    "ADVISORY_SPECS",
    "NO_ACTION_OUTCOMES",
    "PRIORITY_1_OUTCOMES",
    "render_rules_doc",
    "write_rules_doc",
]
