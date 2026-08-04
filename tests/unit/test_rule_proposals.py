"""Proposed rules and hypotheses, and the lifecycle that carries them toward a human.

The single most important test here is that every catalogued candidate parses against the closed
vocabulary. A proposal that cannot become a `Rule` is a row that will be silently skipped at load
time, and the symptom -- an engine running with no rules -- is indistinguishable from nobody
having proposed any.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from wnba_rules.dsl import RULE_FIELDS, ActionKind, Rule, RuleStatus
from wnba_services.learning_loop import rule_lifecycle, rule_proposals
from wnba_services.learning_loop.rule_proposals import (
    CANDIDATE_RULES,
    HYPOTHESIS_TEMPLATES,
    RuleCandidate,
    propose_from_measured_errors,
)

from tests.fixtures.fake_db import FakeDatabase


def _as_rule(candidate: RuleCandidate) -> Rule:
    from wnba_services.forecasting.rules import _coerce_enums

    return Rule.model_validate(
        _coerce_enums(
            {
                **candidate.definition(),
                "rule_id": candidate.rule_id,
                "title": candidate.title,
                "rationale": candidate.rationale,
                "status": "proposed",
                "priority": candidate.priority,
                "proposed_by": "research_director",
                "approved_by": None,
            }
        )
    )


# --------------------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------------------
def test_every_candidate_rule_parses_against_the_closed_vocabulary() -> None:
    """A proposal that cannot become a Rule is a row that will be silently dropped at load."""
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            rule = _as_rule(candidate)
            assert rule.rule_id == candidate.rule_id
            assert rule.status is RuleStatus.PROPOSED


def test_every_candidate_uses_only_fields_the_pipeline_populates() -> None:
    """A rule conditioned on a fact the forecaster never supplies can never fire."""
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            for condition in candidate.conditions:
                assert condition["field"] in RULE_FIELDS, candidate.rule_id


def test_no_candidate_can_make_a_forecast_more_confident() -> None:
    """The language has no such verb, so this is a check that nothing smuggled one in."""
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            rule = _as_rule(candidate)
            for probability in (0.2, 0.5, 0.9):
                adjusted = rule.action.apply(probability)
                assert abs(adjusted - 0.5) <= abs(probability - 0.5) + 1e-9


def test_no_candidate_arrives_pre_approved() -> None:
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            assert _as_rule(candidate).approved_by is None


def test_candidate_rule_ids_are_unique_across_the_catalogue() -> None:
    ids = [c.rule_id for candidates in CANDIDATE_RULES.values() for c in candidates]
    assert len(ids) == len(set(ids))


def test_every_candidate_carries_a_reviewable_rationale() -> None:
    """A rule nobody can explain is a rule nobody can review."""
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            assert len(candidate.rationale) >= 20


def test_shrink_candidates_are_partial_rather_than_collapsing() -> None:
    for candidates in CANDIDATE_RULES.values():
        for candidate in candidates:
            if candidate.action["kind"] == ActionKind.SHRINK_TOWARD_EVEN.value:
                assert 0.0 < candidate.action["magnitude"] < 1.0


def test_every_hypothesis_template_states_a_mechanism_and_its_confounders() -> None:
    for template in HYPOTHESIS_TEMPLATES.values():
        assert len(template.mechanism) > 40
        assert template.confounders
        assert template.expected_direction in {"increase", "decrease", "conditional"}


def test_hypothesis_and_rule_catalogues_address_the_same_error_categories() -> None:
    """A category the system can diagnose but propose nothing about is a dead end."""
    assert set(HYPOTHESIS_TEMPLATES) <= set(CANDIDATE_RULES)


# --------------------------------------------------------------------------------------
# Proposing
# --------------------------------------------------------------------------------------
def _error_category(name: str = "rate_or_efficiency") -> list[dict[str, object]]:
    return [
        {
            "primary_error": name,
            "failures": 40,
            "markets": 30,
            "episodes": ["11111111-1111-1111-1111-111111111111"],
            "markets_affected": ["points", "assists"],
        }
    ]


def test_a_repeated_error_category_proposes_a_rule_and_a_hypothesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase().when("FROM wnba.error_attributions", _error_category())
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    batch = propose_from_measured_errors(now=datetime(2026, 8, 4, tzinfo=UTC))

    assert batch.categories_reviewed == 1
    assert batch.rules_proposed == 1
    assert batch.hypotheses_created == 1
    assert database.ran("INSERT INTO wnba.analyst_rules")
    assert database.ran("INSERT INTO wnba.hypotheses")


def test_proposed_rules_are_inserted_as_proposed_and_unapproved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schema forbids activation without an approver and a backtest; so must the writer."""
    database = FakeDatabase().when("FROM wnba.error_attributions", _error_category())
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    propose_from_measured_errors()

    statement, _ = database.executed("INSERT INTO wnba.analyst_rules")[0]
    assert "'proposed'" in statement
    assert "approved_by" not in statement
    assert "active" not in statement


def test_an_already_known_rule_is_not_proposed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase().when("FROM wnba.error_attributions", _error_category())
    database.when(
        "SELECT rule_id FROM wnba.analyst_rules",
        [{"rule_id": "shrink_when_components_disagree"}],
    )
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    batch = propose_from_measured_errors()

    assert batch.rules_proposed == 0
    assert not database.ran("INSERT INTO wnba.analyst_rules")


def test_a_thin_error_category_is_filtered_by_the_query_not_by_hope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The minimum is expressed in independent markets, in SQL, so it cannot be forgotten."""
    database = FakeDatabase().when("FROM wnba.error_attributions", [])
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    batch = propose_from_measured_errors()

    assert batch.categories_reviewed == 0
    statement, params = database.executed("FROM wnba.error_attributions")[0]
    assert "count(DISTINCT (d.player_id,d.game_id,d.prop_type))>=" in statement
    assert params == (rule_proposals.MINIMUM_FAILURES,)


def test_motivating_episodes_are_linked_to_the_hypothesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase().when("FROM wnba.error_attributions", _error_category())
    database.when("INSERT INTO wnba.hypothesis_episodes", rowcount=1)
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    batch = propose_from_measured_errors()

    assert batch.episodes_linked == 1
    statement, params = database.executed("INSERT INTO wnba.hypothesis_episodes")[0]
    assert "'supporting'" in statement
    assert len(params) == 2


def test_malformed_aggregates_are_refused_rather_than_iterated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = _error_category()
    broken[0]["episodes"] = "not-an-array"
    database = FakeDatabase().when("FROM wnba.error_attributions", broken)
    monkeypatch.setattr(rule_proposals, "connect", database.connect)

    with pytest.raises(ValueError, match="not arrays"):
        propose_from_measured_errors()


# --------------------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------------------
def test_the_lifecycle_runner_can_never_write_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """The governance invariant, checked against the statements actually issued."""
    database = FakeDatabase()
    monkeypatch.setattr(rule_lifecycle, "connect", database.connect)

    rule_lifecycle.run_rule_backtests()

    for statement, params in database.statements:
        if "analyst_rules" in statement and "UPDATE" in statement:
            assert "'active'" not in statement
            assert "active" not in [str(value) for value in (params or ())]


def test_the_lifecycle_runner_only_considers_proposed_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(rule_lifecycle, "connect", database.connect)

    batch = rule_lifecycle.run_rule_backtests()

    assert batch.rules_considered == 0
    assert batch.awaiting_approval == 0
