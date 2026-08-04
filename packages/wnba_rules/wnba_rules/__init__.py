"""Codified analyst knowledge: declarative rules a human approves and the machine executes.

The PAT-shaped layer of the platform. A rule is structured data over a closed vocabulary --
never code -- so it can be parsed, reviewed, backtested and logged. Every available action
restricts or annotates; the language has no verb for raising a probability or a stake, so no
proposal can contain one and no reviewer can approve one by accident.
"""

from __future__ import annotations

from wnba_rules.dsl import (
    RULE_FIELDS,
    Action,
    ActionKind,
    Condition,
    Operator,
    Rule,
    RuleParseError,
    RuleStatus,
)
from wnba_rules.engine import Firing, RuleOutcome, apply_rules

__all__ = [
    "RULE_FIELDS",
    "Action",
    "ActionKind",
    "Condition",
    "Firing",
    "Operator",
    "Rule",
    "RuleOutcome",
    "RuleParseError",
    "RuleStatus",
    "apply_rules",
]

__version__ = "0.1.0"
