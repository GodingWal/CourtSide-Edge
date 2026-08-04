"""The rule language: safety invariants first, mechanics second.

This layer lets a language model influence live forecasts, so the tests that matter are the
ones proving what it *cannot* do.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from wnba_rules import (
    RULE_FIELDS,
    Action,
    ActionKind,
    Condition,
    Operator,
    Rule,
    RuleParseError,
    RuleStatus,
    apply_rules,
)


def rule(
    rule_id: str = "test_rule",
    *,
    conditions: list[Condition] | None = None,
    action: Action | None = None,
    status: RuleStatus = RuleStatus.ACTIVE,
    approved_by: str | None = "analyst",
    priority: int = 50,
    combinator: str = "all",
) -> Rule:
    return Rule(
        rule_id=rule_id,
        title="test",
        rationale="a rationale long enough to satisfy the minimum length requirement",
        conditions=conditions or [Condition(field="confidence", operator=Operator.LT, value=0.6)],
        combinator=combinator,  # type: ignore[arg-type]
        action=action or Action(kind=ActionKind.FLAG_FOR_REVIEW),
        status=status,
        proposed_by="deepseek",
        approved_by=approved_by,
        priority=priority,
    )


# --------------------------------------------------------------------------------------
# What the language cannot express
# --------------------------------------------------------------------------------------
def test_there_is_no_action_that_increases_a_probability() -> None:
    """The central safety property. If this ever fails, the asymmetry is gone."""
    for kind in ActionKind:
        action = Action(kind=kind, magnitude=1.0 if kind is ActionKind.SHRINK_TOWARD_EVEN else 0.0)
        for p in (0.05, 0.3, 0.5, 0.72, 0.99):
            adjusted = action.apply(p)
            assert abs(adjusted - 0.5) <= abs(p - 0.5) + 1e-12, (
                f"{kind.value} moved {p} further from even -- a rule must never increase confidence"
            )


def test_shrink_cannot_overshoot_past_even() -> None:
    """Magnitude 1.0 lands exactly on 0.5; it cannot flip the forecast to the other side."""
    action = Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=1.0)
    assert action.apply(0.9) == pytest.approx(0.5)
    assert action.apply(0.1) == pytest.approx(0.5)


def test_magnitude_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=1.5)


def test_a_rule_cannot_be_active_without_a_human_approver() -> None:
    with pytest.raises(ValidationError, match="named human approver"):
        rule(status=RuleStatus.ACTIVE, approved_by=None)


def test_a_model_may_propose_but_not_approve() -> None:
    """Proposal is unrestricted; activation is not. That is the whole governance model."""
    proposed = rule(status=RuleStatus.PROPOSED, approved_by=None)
    assert proposed.proposed_by == "deepseek"
    assert proposed.approved_by is None


# --------------------------------------------------------------------------------------
# The closed vocabulary
# --------------------------------------------------------------------------------------
def test_an_unknown_field_is_refused() -> None:
    """A hallucinated field name must fail loudly, not match nothing silently."""
    with pytest.raises(ValidationError, match="unknown rule field"):
        Condition(field="vibes", operator=Operator.GT, value=0.5)


def test_no_outcome_field_is_available_to_rules() -> None:
    """Rules run before tipoff, so nothing derived from the result may be addressable --
    otherwise a rule could 'predict' a game it has already seen."""
    forbidden = {"points", "actual", "hit", "minutes", "started", "rebounds", "assists"}
    assert not (forbidden & set(RULE_FIELDS)), (
        f"outcome-derived fields leaked into the rule vocabulary: {forbidden & set(RULE_FIELDS)}"
    )


def test_numeric_operators_are_refused_on_text_fields() -> None:
    with pytest.raises(ValidationError, match="numeric comparison"):
        Condition(field="prop_type", operator=Operator.GT, value=5)


def test_in_requires_a_list_and_comparisons_do_not() -> None:
    Condition(field="prop_type", operator=Operator.IN, value=["points", "assists"])
    with pytest.raises(ValidationError, match="non-empty list"):
        Condition(field="prop_type", operator=Operator.IN, value="points")
    with pytest.raises(ValidationError, match="does not take a list"):
        Condition(field="confidence", operator=Operator.LT, value=[0.5])


def test_rule_ids_are_constrained_to_a_safe_charset() -> None:
    with pytest.raises(ValidationError):
        rule("Robert'); DROP TABLE wnba.prop_quotes;--")


def test_a_rule_must_explain_itself() -> None:
    with pytest.raises(ValidationError):
        Rule(
            rule_id="terse",
            title="t",
            rationale="too short",
            conditions=[Condition(field="confidence", operator=Operator.LT, value=0.6)],
            action=Action(kind=ActionKind.BLOCK),
            proposed_by="deepseek",
        )


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------
def test_a_missing_fact_fails_closed() -> None:
    """A rule written against a field the pipeline never populated must not behave as though
    the world satisfied it."""
    condition = Condition(field="book_count", operator=Operator.LT, value=3)
    assert condition.evaluate({"book_count": 1}) is True
    assert condition.evaluate({}) is False
    assert condition.evaluate({"book_count": None}) is False


def test_all_versus_any_combinator() -> None:
    conditions = [
        Condition(field="confidence", operator=Operator.LT, value=0.6),
        Condition(field="book_count", operator=Operator.LT, value=2),
    ]
    facts = {"confidence": 0.55, "book_count": 5}
    assert rule(conditions=conditions, combinator="all").matches(facts) is False
    assert rule(conditions=conditions, combinator="any").matches(facts) is True


# --------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------
def test_only_active_rules_change_the_number() -> None:
    facts = {"confidence": 0.5}
    shrink = Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=0.5)

    active = apply_rules([rule("r_active", action=shrink)], facts, 0.80)
    assert active.probability == pytest.approx(0.65)

    proposed = apply_rules(
        [rule("r_proposed", action=shrink, status=RuleStatus.PROPOSED, approved_by=None)],
        facts,
        0.80,
    )
    assert proposed.probability == pytest.approx(0.80), "a shadow rule must not move the number"
    assert proposed.firings[0].shadow is True


def test_shadow_firings_are_still_recorded() -> None:
    """Otherwise there is no way to measure a rule's effect before trusting it."""
    outcome = apply_rules(
        [
            rule(
                "r_shadow",
                action=Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=0.4),
                status=RuleStatus.BACKTESTED,
                approved_by=None,
            )
        ],
        {"confidence": 0.5},
        0.90,
    )
    assert len(outcome.firings) == 1
    assert outcome.firings[0].probability_after == pytest.approx(0.74)
    assert outcome.probability == 0.90


def test_rejected_and_retired_rules_never_run() -> None:
    for status in (RuleStatus.REJECTED, RuleStatus.RETIRED):
        outcome = apply_rules(
            [rule("r_dead", status=status, approved_by=None)], {"confidence": 0.5}, 0.8
        )
        assert outcome.firings == []


def test_block_is_recorded_with_a_reason() -> None:
    outcome = apply_rules(
        [rule("r_block", action=Action(kind=ActionKind.BLOCK))], {"confidence": 0.5}, 0.8
    )
    assert outcome.blocked
    assert "r_block" in outcome.block_reasons[0]


def test_multiple_shrink_rules_compose_and_converge() -> None:
    """Overlapping rules both bite, and because every action moves toward 0.5 they cannot
    oscillate or run away."""
    facts = {"confidence": 0.5}
    rules = [
        rule("r_a", action=Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=0.5), priority=90),
        rule("r_b", action=Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=0.5), priority=10),
    ]
    outcome = apply_rules(rules, facts, 0.90)
    assert outcome.probability == pytest.approx(0.60)
    assert [f.rule_id for f in outcome.firings] == ["r_a", "r_b"]
    assert 0.5 <= outcome.probability <= 0.90


def test_explain_names_the_rule_that_moved_the_number() -> None:
    """A silent adjustment is indistinguishable from a bug."""
    outcome = apply_rules(
        [
            rule(
                "overconfidence_shrink",
                action=Action(kind=ActionKind.SHRINK_TOWARD_EVEN, magnitude=0.3),
            )
        ],
        {"confidence": 0.5},
        0.80,
    )
    assert "overconfidence_shrink" in outcome.explain()
    assert outcome.was_adjusted


def test_no_rules_is_a_clean_passthrough() -> None:
    outcome = apply_rules([], {"confidence": 0.5}, 0.73)
    assert outcome.probability == 0.73
    assert not outcome.blocked
    assert outcome.explain() == "no rules fired"


# --------------------------------------------------------------------------------------
# The rule the calibration data actually justifies
# --------------------------------------------------------------------------------------
def test_the_overconfidence_rule_the_data_calls_for() -> None:
    """First settlement showed the model saying 74.4% and delivering 61.0%, with the gap
    widening as confidence rose. This is that finding expressed as a rule."""
    overconfidence = Rule(
        rule_id="shrink_high_confidence_until_calibrated",
        title="Shrink high-confidence forecasts toward even",
        rationale=(
            "First settled cohort: predicted 74.4% delivered 61.0%, predicted 83.8% delivered "
            "56.0%. Overconfidence grows with confidence, so the model is least reliable "
            "exactly where it would be sized largest."
        ),
        conditions=[Condition(field="predicted_probability", operator=Operator.GTE, value=0.70)],
        action=Action(
            kind=ActionKind.SHRINK_TOWARD_EVEN,
            magnitude=0.5,
            note="withdraw once calibration error is inside tolerance",
        ),
        status=RuleStatus.PROPOSED,
        proposed_by="deepseek",
    )
    assert overconfidence.status is RuleStatus.PROPOSED
    assert overconfidence.approved_by is None

    high = apply_rules([overconfidence], {"predicted_probability": 0.84}, 0.84)
    assert high.firings[0].shadow is True
    assert high.firings[0].probability_after == pytest.approx(0.67)

    low = apply_rules([overconfidence], {"predicted_probability": 0.55}, 0.55)
    assert low.firings == []


def test_rule_parse_error_is_a_value_error() -> None:
    assert issubclass(RuleParseError, ValueError)
