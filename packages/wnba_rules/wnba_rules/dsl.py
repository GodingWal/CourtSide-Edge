"""A declarative rule language for codified analyst knowledge.

This is the PAT-shaped part of the platform: the place where a judgement like "when the
market has barely moved and our disagreement is high, we are usually fooling ourselves" stops
being a paragraph somebody wrote once and becomes a thing the system actually does, every
time, visibly.

WHY THIS IS DATA AND NOT CODE
-----------------------------
The obvious implementation is to let the model emit Python and ``exec`` it. That would be a
remote code execution hole with a language model holding the keyboard, and it would make
every rule unreviewable, untestable and unloggable. So a rule is a **structured object over a
closed vocabulary**: a fixed set of fields, a fixed set of operators, a fixed set of actions.
Anything outside that vocabulary fails to parse. There is no eval, no import, no arbitrary
expression anywhere in this module.

THE ASYMMETRY THAT MAKES IT SAFE
--------------------------------
Every available action either **restricts** or **annotates**. A rule can block a
recommendation, pull a probability toward 50/50, or demand human review. No rule can raise a
probability, increase a stake, or unblock anything -- those verbs do not exist in the
language, so no proposal can contain them and no reviewer can approve them by accident.

That is deliberate. A system that can automatically talk itself into a bigger position is one
bad inference away from an expensive night; a system that can only ever talk itself out of one
fails safe. Expansion stays a human decision, which is the same rule the ontology applies to
model promotion.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator
from wnba_domain.base import StrictModel

__all__ = [
    "RULE_FIELDS",
    "Action",
    "ActionKind",
    "Condition",
    "Operator",
    "Rule",
    "RuleParseError",
    "RuleStatus",
]


class RuleParseError(ValueError):
    """Raised when a proposal uses a field, operator or action outside the vocabulary."""


class Operator(StrEnum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"


class ActionKind(StrEnum):
    """The complete set of things a rule may do.

    Note what is absent: there is no ``raise_probability``, no ``increase_stake``, no
    ``approve``. The language cannot express them.
    """

    BLOCK = "block"
    """Refuse to recommend. The strongest action, and still only a restriction."""

    SHRINK_TOWARD_EVEN = "shrink_toward_even"
    """Pull the probability toward 0.5 by a bounded fraction. The honest response to
    'we are systematically overconfident in this situation'."""

    FLAG_FOR_REVIEW = "flag_for_review"
    """Surface it to a human without changing the number."""

    REQUIRE_EVIDENCE = "require_evidence"
    """Block unless a live, cited research claim supports it."""


class RuleStatus(StrEnum):
    PROPOSED = "proposed"
    BACKTESTED = "backtested"
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


# The closed vocabulary. A field is listed here only if it is knowable strictly before tipoff
# -- anything derived from the outcome would let a rule "predict" a game it has already seen,
# which is the leakage this platform exists to prevent.
RULE_FIELDS: dict[str, type] = {
    "predicted_probability": float,
    "confidence": float,
    "model_disagreement": float,
    "data_quality_score": float,
    "projected_minutes": float,
    "availability_probability": float,
    "start_probability": float,
    "closing_lineup_probability": float,
    "minutes_std": float,
    "quote_age_seconds": float,
    "book_count": int,
    "line": float,
    "prop_type": str,
    "source": str,
    "side": str,
    "injury_designation": str,
    "teammate_effect_count": int,
}

_NUMERIC_OPERATORS = {Operator.LT, Operator.LTE, Operator.GT, Operator.GTE}


class Condition(StrictModel):
    """One test against one whitelisted field."""

    field: str
    operator: Operator
    value: Any

    @model_validator(mode="after")
    def _within_vocabulary(self) -> Self:
        expected = RULE_FIELDS.get(self.field)
        if expected is None:
            raise RuleParseError(
                f"unknown rule field {self.field!r}; the vocabulary is closed so that every "
                f"rule is reviewable and leakage-free. Known fields: "
                f"{', '.join(sorted(RULE_FIELDS))}"
            )
        if self.operator in (Operator.IN, Operator.NOT_IN):
            if not isinstance(self.value, list) or not self.value:
                raise RuleParseError(f"{self.operator.value} requires a non-empty list")
            return self
        if isinstance(self.value, list):
            raise RuleParseError(f"{self.operator.value} does not take a list")
        if expected is str and self.operator in _NUMERIC_OPERATORS:
            raise RuleParseError(
                f"{self.field!r} is textual; {self.operator.value} is a numeric comparison"
            )
        if expected is not str and not isinstance(self.value, int | float | Decimal):
            raise RuleParseError(
                f"{self.field!r} expects a number, got {type(self.value).__name__}"
            )
        return self

    def evaluate(self, facts: Mapping[str, object]) -> bool:
        """Test this condition. A missing fact is False, never a crash and never a pass.

        Failing closed matters: a rule written for a field the pipeline did not populate must
        not silently behave as though the world satisfied it.
        """
        if self.field not in facts:
            return False
        actual = facts[self.field]
        if actual is None:
            return False

        if self.operator is Operator.IN:
            return actual in self.value
        if self.operator is Operator.NOT_IN:
            return actual not in self.value
        if self.operator is Operator.EQ:
            return bool(actual == self.value)
        if self.operator is Operator.NEQ:
            return bool(actual != self.value)

        if not isinstance(actual, int | float | Decimal):
            return False
        left, right = float(actual), float(self.value)
        match self.operator:
            case Operator.LT:
                return left < right
            case Operator.LTE:
                return left <= right
            case Operator.GT:
                return left > right
            case Operator.GTE:
                return left >= right
        # Every remaining operator is handled above; mypy proves this is exhaustive.
        raise AssertionError(f"unhandled operator {self.operator!r}")


class Action(StrictModel):
    """What a rule does when it fires. Restriction or annotation only."""

    kind: ActionKind
    magnitude: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """Only meaningful for ``shrink_toward_even``: the fraction of the distance to 0.5 to
    close. Capped at 1.0, which lands exactly on 0.5 -- a rule can reduce a forecast to a
    coin flip but can never push it past one and out the other side."""

    note: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def _magnitude_matches_kind(self) -> Self:
        if self.kind is ActionKind.SHRINK_TOWARD_EVEN:
            if not 0.0 < self.magnitude <= 1.0:
                raise RuleParseError("shrink_toward_even needs a magnitude in (0, 1]")
        elif self.magnitude:
            raise RuleParseError(f"{self.kind.value} takes no magnitude")
        return self

    def apply(self, probability: float) -> float:
        """Adjust a probability. Structurally incapable of increasing distance from 0.5."""
        if self.kind is not ActionKind.SHRINK_TOWARD_EVEN:
            return probability
        return probability + (0.5 - probability) * self.magnitude


class Rule(StrictModel):
    """A codified analyst judgement: conditions, an action, and its provenance."""

    rule_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=20, max_length=2000)
    """Why this rule should exist, in words. A rule nobody can explain is a rule nobody can
    review, and it will outlive everyone's memory of why it was added."""

    conditions: list[Condition] = Field(min_length=1, max_length=10)
    combinator: Literal["all", "any"] = "all"
    action: Action
    status: RuleStatus = RuleStatus.PROPOSED
    proposed_by: str = Field(min_length=1, max_length=100)
    approved_by: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    priority: Annotated[int, Field(ge=0, le=100)] = 50

    @model_validator(mode="after")
    def _active_rules_need_a_human(self) -> Self:
        """The asymmetric-autonomy invariant, enforced at construction.

        A rule may be proposed by anything -- a model, a script, an analyst at midnight. It
        cannot become ACTIVE without a named human, because 'active' is what makes it affect
        real output.
        """
        if self.status is RuleStatus.ACTIVE and not self.approved_by:
            raise RuleParseError(
                f"rule {self.rule_id!r} cannot be active without a named human approver"
            )
        return self

    def matches(self, facts: Mapping[str, object]) -> bool:
        results = (condition.evaluate(facts) for condition in self.conditions)
        return all(results) if self.combinator == "all" else any(results)
