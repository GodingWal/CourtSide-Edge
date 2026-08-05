"""What an agent may propose, and every way it is stopped from proposing something else.

The pipeline is `finding -> hypothesis -> validated DSL proposal -> leakage inspection ->
backtest -> shadow -> human -> active`. These tests cover the stages this module owns, and they
are written adversarially: each one is an attempt to get something through that should not.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from wnba_rules.dsl import ActionKind, RuleParseError
from wnba_services.learning_loop.rule_authoring import (
    AGENT_AUTHOR,
    compile_proposal,
    inspect_for_leakage,
)
from wnba_services.research_agents.deepseek import RuleProposalDraft

RANGES = {
    "model_disagreement": {"min": 0.0, "p05": 0.01, "median": 0.06, "p95": 0.18, "max": 0.35},
    "data_quality_score": {"min": 0.2, "p05": 0.45, "median": 0.85, "p95": 1.0, "max": 1.0},
    "minutes_std": {"min": 0.0, "p05": 1.5, "median": 4.5, "p95": 9.0, "max": 14.0},
}


def _draft(**overrides: Any) -> RuleProposalDraft:
    payload: dict[str, Any] = {
        "rule_id": "shrink_on_wide_component_spread",
        "title": "Shrink when the components disagree",
        "rationale": (
            "Repeated rate errors alongside wide component spread indicate a forecast whose "
            "confidence comes from pooling rather than from evidence about the player."
        ),
        "mechanism": (
            "Each component conditions on a different slice of history. Wide spread means those "
            "slices tell different stories, which is evidence the player is hard to forecast."
        ),
        "confounders": ["small history samples inflate spread mechanically"],
        "expiry_conditions": (
            "Stops holding if the ensemble weights are refit such that spread no longer tracks "
            "error."
        ),
        "withdrawal_criteria": (
            "Withdraw if the backtest log-loss gain falls below 0.002 over 200 fired markets."
        ),
        "conditions": [{"field": "model_disagreement", "operator": "gt", "value": 0.10}],
        "combinator": "all",
        "action": {"kind": "shrink_toward_even", "magnitude": 0.3, "note": "wide spread"},
        "evidence_ids": ["3f2504e0-4f89-11d3-9a0c-0305e82c3301"],
        "expected_direction": "increase",
        "causal_statement": (
            "Forecasts with wide component disagreement are systematically overconfident."
        ),
    }
    payload.update(overrides)
    # Parsed from JSON rather than from a dict, because JSON is the only way a proposal ever
    # arrives. The schema is strict, so a Python dict would need real UUID objects the provider
    # never produces -- validating the wrong representation would test a path nothing uses.
    return RuleProposalDraft.model_validate_json(json.dumps(payload))


# --------------------------------------------------------------------------------------
# The schema: a document, never code
# --------------------------------------------------------------------------------------
def test_a_valid_draft_compiles_into_a_proposed_rule_owned_by_the_agent() -> None:
    rule = compile_proposal(_draft())

    assert rule.proposed_by == AGENT_AUTHOR
    assert rule.approved_by is None
    assert rule.action.kind is ActionKind.SHRINK_TOWARD_EVEN


@pytest.mark.parametrize(
    "action_kind", ["raise_probability", "increase_stake", "approve", "execute_python"]
)
def test_the_action_vocabulary_cannot_express_anything_expansive(action_kind: str) -> None:
    """There is no verb for widening. A model asking for one fails at the schema."""
    with pytest.raises(ValueError):
        _draft(action={"kind": action_kind, "magnitude": 0.0, "note": ""})


def test_a_draft_carrying_a_python_expression_field_is_rejected() -> None:
    """`extra="forbid"` is what makes "never generates executable Python" structural."""
    with pytest.raises(ValueError):
        _draft(python="probability * 0.5 if disagreement > 0.1 else probability")


def test_a_condition_on_a_field_outside_the_closed_vocabulary_is_refused() -> None:
    with pytest.raises(RuleParseError, match="unknown rule field"):
        compile_proposal(
            _draft(conditions=[{"field": "final_score_margin", "operator": "gt", "value": 10}])
        )


def test_a_condition_on_the_outcome_cannot_be_expressed_at_all() -> None:
    """Leakage prevention starts in the vocabulary rather than in the inspection."""
    with pytest.raises(RuleParseError, match="unknown rule field"):
        compile_proposal(
            _draft(conditions=[{"field": "actual_stat", "operator": "gte", "value": 15}])
        )


@pytest.mark.parametrize(
    "missing",
    ["mechanism", "confounders", "expiry_conditions", "withdrawal_criteria", "evidence_ids"],
)
def test_a_proposal_missing_its_provenance_is_not_a_smaller_proposal(missing: str) -> None:
    payload = _draft().model_dump(mode="json")
    payload.pop(missing)

    with pytest.raises(ValueError):
        RuleProposalDraft.model_validate_json(json.dumps(payload))


def test_an_uncited_proposal_is_refused() -> None:
    with pytest.raises(ValueError):
        _draft(evidence_ids=[])


def test_a_one_word_mechanism_does_not_count_as_a_mechanism() -> None:
    with pytest.raises(ValueError):
        _draft(mechanism="variance")


# --------------------------------------------------------------------------------------
# Leakage inspection
# --------------------------------------------------------------------------------------
def _facts(disagreement: float, quality: float = 0.9) -> dict[str, object]:
    return {
        "model_disagreement": disagreement,
        "data_quality_score": quality,
        "predicted_probability": 0.6,
        "prop_type": "points",
    }


def test_a_sensible_rule_passes_inspection() -> None:
    rule = compile_proposal(_draft())
    settled = [_facts(0.02 + index * 0.01) for index in range(30)]

    inspection = inspect_for_leakage(rule, RANGES, settled_facts=settled)

    assert inspection.passed
    assert inspection.coverage is not None and inspection.coverage > 0


def test_a_threshold_outside_every_observed_value_is_rejected() -> None:
    """A rule that can never fire, or always fires, is not a judgement about anything."""
    rule = compile_proposal(
        _draft(conditions=[{"field": "model_disagreement", "operator": "gt", "value": 0.95}])
    )

    inspection = inspect_for_leakage(rule, RANGES)

    assert not inspection.passed
    assert "threshold_outside_observed_range" in {f["code"] for f in inspection.findings}


def test_a_hair_thin_window_that_fingerprints_its_own_episodes_is_rejected() -> None:
    """The overfit the closed vocabulary cannot catch: a lookup table for four bad nights."""
    rule = compile_proposal(
        _draft(
            conditions=[
                {"field": "model_disagreement", "operator": "gte", "value": 0.114},
                {"field": "model_disagreement", "operator": "lte", "value": 0.119},
            ]
        )
    )

    inspection = inspect_for_leakage(rule, RANGES)

    assert not inspection.passed
    assert "degenerate_window" in {finding["code"] for finding in inspection.findings}


def test_a_rule_that_matches_nothing_in_the_record_is_rejected_as_untestable() -> None:
    rule = compile_proposal(
        _draft(conditions=[{"field": "model_disagreement", "operator": "gt", "value": 0.30}])
    )
    settled = [_facts(0.05) for _ in range(50)]

    inspection = inspect_for_leakage(rule, RANGES, settled_facts=settled)

    assert not inspection.passed
    assert "never_fires" in {finding["code"] for finding in inspection.findings}


def test_a_rare_but_real_rule_is_recorded_and_allowed_through_to_the_backtest() -> None:
    """Rarity is measured properly at the backtest; pre-judging it here would pre-empt that."""
    rule = compile_proposal(
        _draft(conditions=[{"field": "model_disagreement", "operator": "gt", "value": 0.25}])
    )
    settled = [_facts(0.05) for _ in range(499)] + [_facts(0.30)]

    inspection = inspect_for_leakage(rule, RANGES, settled_facts=settled)

    assert inspection.passed
    assert "very_rare" in {finding["code"] for finding in inspection.findings}


def test_a_field_with_no_settled_values_cannot_be_inspected_so_is_refused() -> None:
    rule = compile_proposal(
        _draft(conditions=[{"field": "book_count", "operator": "gte", "value": 2}])
    )

    inspection = inspect_for_leakage(rule, RANGES)

    assert not inspection.passed
    assert "unobserved_field" in {finding["code"] for finding in inspection.findings}


def test_the_inspection_payload_states_what_it_did_and_did_not_check() -> None:
    inspection = inspect_for_leakage(compile_proposal(_draft()), RANGES)

    payload = inspection.to_payload()

    assert payload["vocabulary_is_closed"] is True
    assert payload["outcome_fields_unreachable"] is True
    assert payload["checked_fields"] == ["model_disagreement"]
