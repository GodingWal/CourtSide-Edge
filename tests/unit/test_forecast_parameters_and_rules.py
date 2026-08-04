"""Fitted-parameter loading and the rules engine's newly-connected wiring.

Both are database-facing, so they are exercised against a recording fake cursor rather than a
live connection. What is being tested is the translation layer -- fallbacks, vocabulary
enforcement, and the promise that every firing gets written down -- not PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from wnba_rules.dsl import RULE_FIELDS, ActionKind, Operator, RuleStatus
from wnba_services.forecasting.calibration import IDENTITY
from wnba_services.forecasting.parameters import (
    DEFAULT_MARKET,
    FittedParameters,
    load_fitted_parameters,
    store_fitted_parameters,
)
from wnba_services.forecasting.rules import (
    active_rule_count,
    build_facts,
    evaluate_rules,
    load_rules,
    record_firings,
)
from wnba_services.forecasting.selection import COLD_START_SHRINK_FACTOR, EdgeShrinkage
from wnba_services.forecasting.weights import DEFAULT_COMPONENT_WEIGHTS


class FakeCursor:
    """Returns queued rows and records every statement it was asked to run."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.statements: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None, /) -> None:
        self.statements.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


# --------------------------------------------------------------------------------------
# Fitted parameters
# --------------------------------------------------------------------------------------
def test_an_empty_table_yields_entirely_default_behaviour() -> None:
    """Every market must be forecastable before anything has been fitted for it."""
    parameters = load_fitted_parameters(FakeCursor([]))
    assert parameters.is_empty
    assert parameters.calibration_for("points") is IDENTITY
    assert parameters.weights_for("points").weights == DEFAULT_COMPONENT_WEIGHTS
    assert parameters.shrinkage_for("points").factor == COLD_START_SHRINK_FACTOR


def test_parameters_are_loaded_and_keyed_by_market() -> None:
    cursor = FakeCursor(
        [
            {
                "kind": "calibration_map",
                "market": "points",
                "payload": {
                    "market": "points",
                    "knots": [[0.2, 0.25], [0.8, 0.6]],
                    "sample_size": 5000,
                },
            },
            {
                "kind": "ensemble_weights",
                "market": "points",
                "payload": {
                    "market": "points",
                    "weights": {"empirical": 1.0},
                    "sample_size": 900,
                    "is_fitted": True,
                },
            },
            {
                "kind": "edge_shrinkage",
                "market": "points",
                "payload": {"factor": 0.4, "sample_size": 800, "is_fitted": True},
            },
        ]
    )
    parameters = load_fitted_parameters(cursor)
    assert parameters.calibration_for("points").apply(0.8) < 0.8
    assert parameters.weights_for("points").is_fitted
    assert parameters.shrinkage_for("points").factor == pytest.approx(0.4)


def test_a_thin_market_falls_back_to_the_pooled_fit() -> None:
    """Fitting per market is right; making a forty-episode market rely on itself is not."""
    cursor = FakeCursor(
        [
            {
                "kind": "edge_shrinkage",
                "market": DEFAULT_MARKET,
                "payload": {"factor": 0.3, "sample_size": 9000, "is_fitted": True},
            }
        ]
    )
    parameters = load_fitted_parameters(cursor)
    assert parameters.shrinkage_for("steals_blocks").factor == pytest.approx(0.3)


def test_an_unrecognised_parameter_kind_is_ignored_rather_than_crashing() -> None:
    cursor = FakeCursor([{"kind": "something_new", "market": "points", "payload": {}}])
    assert load_fitted_parameters(cursor).is_empty


def test_storing_supersedes_the_previous_row_before_inserting() -> None:
    """History is evidence. A parameter set that stopped being trusted must remain visible."""
    cursor = FakeCursor()
    store_fitted_parameters(
        cursor,
        kind="calibration_map",
        market="points",
        at=datetime(2026, 8, 4, tzinfo=UTC),
        payload={"knots": []},
        sample_size=10,
    )
    assert len(cursor.statements) == 2
    assert "UPDATE wnba.fitted_parameters" in cursor.statements[0][0]
    assert "superseded_at" in cursor.statements[0][0]
    assert "INSERT INTO wnba.fitted_parameters" in cursor.statements[1][0]


def test_a_market_specific_fit_outranks_the_pooled_one() -> None:
    parameters = FittedParameters(
        shrinkage={
            DEFAULT_MARKET: EdgeShrinkage(factor=0.2, sample_size=9000, is_fitted=True),
            "points": EdgeShrinkage(factor=0.7, sample_size=4000, is_fitted=True),
        }
    )
    assert parameters.shrinkage_for("points").factor == pytest.approx(0.7)
    assert parameters.shrinkage_for("assists").factor == pytest.approx(0.2)


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------
def _rule_row(
    rule_id: str = "shrink_when_components_disagree",
    status: str = "active",
    approved_by: str | None = "owner",
    conditions: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "title": "Shrink when the components disagree",
        "rationale": "High disagreement has historically meant we are fooling ourselves.",
        "definition": {
            "conditions": conditions
            or [{"field": "model_disagreement", "operator": "gt", "value": 0.08}],
            "combinator": "all",
            "action": action or {"kind": "shrink_toward_even", "magnitude": 0.5},
        },
        "status": status,
        "priority": 60,
        "proposed_by": "analyst",
        "approved_by": approved_by,
    }


def test_active_and_shadow_rules_are_both_loaded() -> None:
    cursor = FakeCursor(
        [
            _rule_row(),
            _rule_row(rule_id="proposed_rule", status="proposed", approved_by=None),
        ]
    )
    rules = load_rules(cursor)
    assert len(rules) == 2
    assert active_rule_count(rules) == 1


def test_shadow_rules_can_be_excluded() -> None:
    cursor = FakeCursor([_rule_row()])
    load_rules(cursor, include_shadow=False)
    assert cursor.statements[0][1] == (["active"],)


def test_a_definition_outside_the_vocabulary_is_skipped_not_guessed_at() -> None:
    """Failing closed: an unparseable rule simply does not fire."""
    cursor = FakeCursor([_rule_row(conditions=[{"field": "vibes", "operator": "gt", "value": 1}])])
    assert load_rules(cursor) == []


def test_a_rule_claiming_active_without_an_approver_is_rejected() -> None:
    cursor = FakeCursor([_rule_row(approved_by=None)])
    assert load_rules(cursor) == []


def test_every_assembled_fact_is_inside_the_closed_vocabulary() -> None:
    """The DSL's leakage guarantee is only worth as much as what the pipeline hands it."""
    facts = build_facts(
        predicted_probability=0.6,
        confidence=0.6,
        model_disagreement=0.02,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=2,
        minutes_std=4.0,
        start_probability=0.8,
        quote_age_seconds=120.0,
        book_count=1,
    )
    assert set(facts) <= set(RULE_FIELDS)
    assert facts["quote_age_seconds"] == 120.0


def test_unobserved_facts_are_omitted_rather_than_defaulted() -> None:
    """A rule written for a field the pipeline did not populate must not fire on a stand-in."""
    facts = build_facts(
        predicted_probability=0.6,
        confidence=0.6,
        model_disagreement=0.02,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    assert "quote_age_seconds" not in facts
    assert "book_count" not in facts
    assert facts["predicted_probability"] == 0.6


def test_an_active_rule_shrinks_the_probability_it_matches() -> None:
    rules = load_rules(FakeCursor([_rule_row()]))
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.20,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    outcome = evaluate_rules(rules, facts, 0.72)
    assert outcome.probability == pytest.approx(0.61)
    assert outcome.was_adjusted


def test_a_shadow_rule_is_scored_but_never_applied() -> None:
    rules = load_rules(
        FakeCursor([_rule_row(rule_id="proposed_rule", status="proposed", approved_by=None)])
    )
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.20,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    outcome = evaluate_rules(rules, facts, 0.72)
    assert outcome.probability == pytest.approx(0.72)
    assert not outcome.was_adjusted
    assert len(outcome.firings) == 1
    assert outcome.firings[0].shadow


def test_no_rule_can_make_a_forecast_more_confident() -> None:
    """The safety asymmetry, checked end to end rather than trusted from the DSL docstring."""
    rules = load_rules(
        FakeCursor(
            [
                _rule_row(),
                _rule_row(
                    rule_id="block_on_thin_data",
                    conditions=[{"field": "data_quality_score", "operator": "lt", "value": 0.5}],
                    action={"kind": "block"},
                ),
            ]
        )
    )
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.20,
        data_quality_score=0.3,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    outcome = evaluate_rules(rules, facts, 0.72)
    assert outcome.blocked
    assert abs(outcome.probability - 0.5) <= abs(0.72 - 0.5)


def test_every_firing_is_written_down_including_shadow_ones() -> None:
    """A silent adjustment is indistinguishable from a bug."""
    rules = load_rules(
        FakeCursor(
            [
                _rule_row(),
                _rule_row(rule_id="proposed_rule", status="proposed", approved_by=None),
            ]
        )
    )
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.20,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    outcome = evaluate_rules(rules, facts, 0.72)
    cursor = FakeCursor()
    written = record_firings(cursor, uuid4(), outcome.firings, at=datetime(2026, 8, 4, tzinfo=UTC))
    assert written == len(outcome.firings) == 2
    assert all("INSERT INTO wnba.rule_firings" in query for query, _ in cursor.statements)


def test_a_rule_that_matches_nothing_leaves_no_trace() -> None:
    rules = load_rules(FakeCursor([_rule_row()]))
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.01,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    outcome = evaluate_rules(rules, facts, 0.72)
    assert outcome.firings == []
    assert outcome.explain() == "no rules fired"


def test_retired_and_rejected_rules_are_never_requested() -> None:
    cursor = FakeCursor([])
    load_rules(cursor)
    requested = cursor.statements[0][1][0]
    assert set(requested) == {"active", "proposed", "backtested"}
    assert RuleStatus.REJECTED.value not in requested
    assert RuleStatus.RETIRED.value not in requested


def test_firings_report_the_action_that_produced_them() -> None:
    rules = load_rules(FakeCursor([_rule_row()]))
    facts = build_facts(
        predicted_probability=0.72,
        confidence=0.72,
        model_disagreement=0.20,
        data_quality_score=0.9,
        projected_minutes=30.0,
        line=15.5,
        prop_type="points",
        source="underdog",
        side="over",
        injury_designation="available",
        teammate_effect_count=0,
    )
    firing = evaluate_rules(rules, facts, 0.72).firings[0]
    assert firing.kind is ActionKind.SHRINK_TOWARD_EVEN
    assert firing.rule_id == "shrink_when_components_disagree"
    assert firing.delta < 0.0


def test_rules_stored_as_json_strings_still_parse() -> None:
    """JSONB holds "gt" and "active"; the strict DSL wants Operator.GT and RuleStatus.ACTIVE.

    Without an explicit coercion at the boundary every stored rule silently fails to validate
    and the engine runs with an empty rule set -- indistinguishable, from the outside, from
    nobody having written any rules.
    """
    rules = load_rules(FakeCursor([_rule_row()]))
    assert len(rules) == 1
    assert rules[0].status is RuleStatus.ACTIVE
    assert rules[0].conditions[0].operator is Operator.GT
    assert rules[0].action.kind is ActionKind.SHRINK_TOWARD_EVEN


def test_an_unrecognised_operator_is_dropped_rather_than_coerced() -> None:
    cursor = FakeCursor(
        [_rule_row(conditions=[{"field": "line", "operator": "approximately", "value": 10}])]
    )
    assert load_rules(cursor) == []


def test_a_definition_missing_its_action_is_dropped() -> None:
    row = _rule_row()
    del row["definition"]["action"]
    assert load_rules(FakeCursor([row])) == []
