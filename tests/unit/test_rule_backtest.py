"""Rule backtesting: the evidence stage that made the rule lifecycle possible to complete.

The schema demanded a backtest before activation and nothing could produce one, so the live
database held zero rules. These tests cover what the evidence has to establish before a human is
asked to approve anything -- and, at least as importantly, that nothing here can approve.
"""

from __future__ import annotations

from typing import Any

import pytest
from wnba_rules.dsl import Action, ActionKind, Condition, Operator, Rule, RuleStatus
from wnba_services.learning_loop.rule_backtest import (
    MAXIMUM_COVERAGE,
    MINIMUM_FIRED_MARKETS,
    SettledEpisode,
    backtest_rule,
)


def _rule(
    *,
    kind: ActionKind = ActionKind.SHRINK_TOWARD_EVEN,
    magnitude: float = 0.5,
    field: str = "model_disagreement",
    operator: Operator = Operator.GT,
    value: object = 0.10,
    rule_id: str = "candidate_rule",
) -> Rule:
    return Rule(
        rule_id=rule_id,
        title="A candidate rule",
        rationale="Codifies a judgement that needs measuring before anyone acts on it.",
        conditions=[Condition(field=field, operator=operator, value=value)],
        action=Action(
            kind=kind, magnitude=magnitude if kind is ActionKind.SHRINK_TOWARD_EVEN else 0.0
        ),
        proposed_by="research_director",
    )


def _episode(
    index: int,
    *,
    disagreement: float,
    probability: float,
    hit: bool,
    markets_per_game: int = 5,
) -> SettledEpisode:
    return SettledEpisode(
        player_id=f"player-{index % markets_per_game}",
        game_id=f"game-{index // markets_per_game}",
        prop_type="points",
        facts={
            "model_disagreement": disagreement,
            "predicted_probability": probability,
            "data_quality_score": 0.9,
            "prop_type": "points",
        },
        probability=probability,
        hit=hit,
        forecast_timestamp=index,
    )


def _population(
    count: int, *, disagreement: float, probability: float, hit_pattern: Any
) -> list[SettledEpisode]:
    return [
        _episode(
            i,
            disagreement=disagreement,
            probability=probability,
            hit=hit_pattern(i) if callable(hit_pattern) else hit_pattern,
        )
        for i in range(count)
    ]


# --------------------------------------------------------------------------------------
# Evidence thresholds
# --------------------------------------------------------------------------------------
def test_a_rule_that_never_fires_is_inconclusive_not_harmless() -> None:
    episodes = _population(200, disagreement=0.01, probability=0.65, hit_pattern=True)
    result = backtest_rule(_rule(), episodes)
    assert result.fired == 0
    assert result.verdict == "inconclusive"
    assert "never fired" in result.detail


def test_a_rule_with_too_little_evidence_is_inconclusive() -> None:
    """Sounding sensible is not the same as having been tested."""
    episodes = _population(200, disagreement=0.01, probability=0.65, hit_pattern=True)
    episodes += [
        _episode(1000 + i, disagreement=0.5, probability=0.80, hit=False) for i in range(6)
    ]
    result = backtest_rule(_rule(), episodes)
    assert 0 < result.fired_markets < MINIMUM_FIRED_MARKETS
    assert result.verdict == "inconclusive"


def test_a_rule_that_fires_on_almost_everything_is_a_recalibration() -> None:
    """A global adjustment belongs in the calibration map, where it is fitted rather than argued."""
    episodes = _population(300, disagreement=0.5, probability=0.80, hit_pattern=False)
    result = backtest_rule(_rule(), episodes)
    assert result.coverage > MAXIMUM_COVERAGE
    assert result.verdict == "harmful"
    assert "calibration map" in result.detail


# --------------------------------------------------------------------------------------
# Shrink rules
# --------------------------------------------------------------------------------------
def _mixed(overconfident_hits: bool) -> list[SettledEpisode]:
    """200 quiet episodes plus 150 high-disagreement ones the rule will fire on."""
    episodes = _population(200, disagreement=0.01, probability=0.60, hit_pattern=True)
    episodes += [
        _episode(1000 + i, disagreement=0.5, probability=0.85, hit=overconfident_hits)
        for i in range(150)
    ]
    return episodes


def test_shrinking_forecasts_that_were_wrong_is_helpful() -> None:
    result = backtest_rule(_rule(), _mixed(overconfident_hits=False))
    assert result.verdict == "helpful"
    assert result.log_loss_after is not None
    assert result.log_loss_before is not None
    assert result.log_loss_after < result.log_loss_before
    assert result.log_loss_gain > 0


def test_shrinking_forecasts_that_were_right_is_harmful() -> None:
    """The reasoning may sound wise; the record says it cost accuracy."""
    result = backtest_rule(_rule(), _mixed(overconfident_hits=True))
    assert result.verdict == "harmful"
    assert result.log_loss_gain < 0
    assert "already right" in result.detail


def test_a_backtest_reports_both_proper_scores() -> None:
    result = backtest_rule(_rule(), _mixed(overconfident_hits=False))
    assert result.brier_before is not None
    assert result.brier_after is not None
    assert result.brier_after < result.brier_before


# --------------------------------------------------------------------------------------
# Blocking rules
# --------------------------------------------------------------------------------------
def _blocking_population(blocked_hit_rate: float) -> list[SettledEpisode]:
    episodes = _population(200, disagreement=0.01, probability=0.60, hit_pattern=True)
    blocked = 150
    hits = int(blocked * blocked_hit_rate)
    episodes += [
        _episode(1000 + i, disagreement=0.5, probability=0.62, hit=i < hits) for i in range(blocked)
    ]
    return episodes


def test_blocking_losers_is_helpful() -> None:
    result = backtest_rule(
        _rule(kind=ActionKind.BLOCK), _blocking_population(0.40), breakeven=0.5774
    )
    assert result.verdict == "helpful"
    assert result.blocked_hit_rate == pytest.approx(0.40, abs=0.01)


def test_blocking_winners_is_harmful_even_though_it_looks_prudent() -> None:
    """Blocking winners is expensive and invisible, which is why it needs measuring."""
    result = backtest_rule(
        _rule(kind=ActionKind.BLOCK), _blocking_population(0.75), breakeven=0.5774
    )
    assert result.verdict == "harmful"
    assert "winners" in result.detail


def test_a_blocking_rule_is_judged_against_break_even_not_a_coin_flip() -> None:
    """A 55% hit rate beats a coin flip and still loses money on a 3x two-leg play."""
    population = _blocking_population(0.55)
    against_coin_flip = backtest_rule(_rule(kind=ActionKind.BLOCK), population, breakeven=0.50)
    against_payout = backtest_rule(_rule(kind=ActionKind.BLOCK), population, breakeven=0.5774)
    assert against_coin_flip.verdict == "harmful"
    assert against_payout.verdict == "helpful"


# --------------------------------------------------------------------------------------
# Non-scoring actions
# --------------------------------------------------------------------------------------
def test_annotating_actions_are_not_scored_as_if_they_changed_a_number() -> None:
    for kind in (ActionKind.FLAG_FOR_REVIEW, ActionKind.REQUIRE_EVIDENCE):
        result = backtest_rule(_rule(kind=kind), _mixed(overconfident_hits=False))
        assert result.verdict == "inconclusive"
        assert "procedural" in result.detail


# --------------------------------------------------------------------------------------
# Counting, and governance
# --------------------------------------------------------------------------------------
def test_firings_are_counted_in_markets_not_rows() -> None:
    """Forty snapshots of three markets is three markets of evidence."""
    repeated = [_episode(1000, disagreement=0.5, probability=0.85, hit=False) for _ in range(40)]
    result = backtest_rule(
        _rule(), _population(200, disagreement=0.01, probability=0.6, hit_pattern=True) + repeated
    )
    assert result.fired == 40
    assert result.fired_markets == 1
    assert result.verdict == "inconclusive"


def test_the_backtest_payload_carries_the_thresholds_it_was_judged_against() -> None:
    payload = backtest_rule(_rule(), _mixed(overconfident_hits=False)).to_payload()
    assert payload["minimum_fired_markets"] == MINIMUM_FIRED_MARKETS
    assert payload["maximum_coverage"] == MAXIMUM_COVERAGE
    assert payload["verdict"] == "helpful"
    assert payload["promotion_requires_a_human"] is True


def test_a_helpful_verdict_permits_asking_a_human_and_nothing_more() -> None:
    """`is_promotable` is a question for an approver, never an instruction to the system."""
    helpful = backtest_rule(_rule(), _mixed(overconfident_hits=False))
    harmful = backtest_rule(_rule(), _mixed(overconfident_hits=True))
    assert helpful.is_promotable
    assert not harmful.is_promotable


def test_backtesting_a_rule_does_not_require_forging_an_approval() -> None:
    """A rule needing a backtest is by definition unapproved; that must not block measuring it."""
    candidate = _rule()
    assert candidate.status is RuleStatus.PROPOSED
    assert candidate.approved_by is None
    result = backtest_rule(candidate, _mixed(overconfident_hits=False))
    assert result.fired > 0


def test_an_empty_settled_record_yields_an_honest_nothing() -> None:
    result = backtest_rule(_rule(), [])
    assert result.episodes == 0
    assert result.independent_markets == 0
    assert result.verdict == "inconclusive"
