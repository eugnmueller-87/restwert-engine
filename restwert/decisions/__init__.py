"""Decisions package: spec section 6 (rules R01..R06, registry, runner).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

* ``rules``    pure decision functions, one record per call, advisories never change outcomes
* ``registry`` rule metadata and the generator of docs/DECISION_RULES.md
* ``runner``   reads tables, calls the rules, appends decision_log and write_down_ledger,
               rebuilds decision_queue

Thresholds live in ``config/thresholds.yaml`` with a named owner per key. Nothing here orders,
lists, mails or files anything.
"""

from restwert.decisions.rules import (  # noqa: F401
    NO_ACTION_OUTCOMES,
    PRIORITY_1_OUTCOMES,
    RULE_VERSION,
    decide_aging_write_down,
    decide_channel,
    decide_grade_a_replacement,
    decide_price_protection_reminder,
    decide_renewal_alert,
    decide_repair,
)
from restwert.decisions.registry import (  # noqa: F401
    ADVISORY_SPECS,
    RULES,
    RuleSpec,
    render_rules_doc,
    write_rules_doc,
)
from restwert.decisions.runner import (  # noqa: F401
    book_write_downs,
    build_queue,
    records_to_frame,
    run_all_decisions,
    write_log,
)

__all__ = [
    "NO_ACTION_OUTCOMES",
    "PRIORITY_1_OUTCOMES",
    "RULE_VERSION",
    "decide_repair",
    "decide_channel",
    "decide_aging_write_down",
    "decide_grade_a_replacement",
    "decide_price_protection_reminder",
    "decide_renewal_alert",
    "ADVISORY_SPECS",
    "RULES",
    "RuleSpec",
    "render_rules_doc",
    "write_rules_doc",
    "records_to_frame",
    "write_log",
    "book_write_downs",
    "build_queue",
    "run_all_decisions",
]
