"""The unified scorer: the one code path the live board and the replay harness share."""

from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest
from wnba_services.forecasting.calibration import CalibrationMap
from wnba_services.forecasting.scoring import (
    MINIMUM_HISTORY_GAMES,
    HistoryGame,
    MarketInputs,
    MatchupInputs,
    PriorSeasonRate,
    RoleInputs,
    ScoringInputs,
    TeammateInputs,
    resolve_adjustments,
    resolve_dispersion,
    score_prop,
)
from wnba_services.forecasting.weights import EnsembleWeights

SIMULATIONS = 3000


def _history(count: int = 20, seed: int = 5, *, bench_every: int = 5) -> tuple[HistoryGame, ...]:
    rng = random.Random(seed)
    games: list[HistoryGame] = []
    for index in range(count):
        starts = index % bench_every != 0
        minutes = max(6.0, min(38.0, rng.gauss(30.0 if starts else 14.0, 4.0)))
        games.append(
            HistoryGame(
                minutes=minutes,
                started=starts,
                stats={
                    "points": float(max(0, round(rng.gauss(minutes * 0.55, 4.0)))),
                    "field_goals_attempted": float(max(1, round(minutes * 0.42))),
                    "rebounds_offensive": float(rng.randint(0, 3)),
                    "rebounds_defensive": float(rng.randint(1, 7)),
                    "assists": float(rng.randint(0, 7)),
                    "three_pointers_made": float(rng.randint(0, 4)),
                    "three_pointers_attempted": float(rng.randint(1, 8)),
                },
            )
        )
    return tuple(games)


def _inputs(**overrides: object) -> ScoringInputs:
    arguments: dict[str, object] = {
        "prop_type": "points",
        "line": Decimal("15.5"),
        "history": _history(),
        "league_rate_per_minute": 0.45,
        "seed": 17,
        "simulations": SIMULATIONS,
    }
    arguments.update(overrides)
    return ScoringInputs(**arguments)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------------------
def test_scoring_is_deterministic() -> None:
    """An unreproducible forecast cannot be audited, nor learned from."""
    first, second = score_prop(_inputs()), score_prop(_inputs())
    assert first.pmf == second.pmf
    assert first.over == second.over


def test_the_distribution_is_a_distribution() -> None:
    forecast = score_prop(_inputs())
    assert math.fsum(forecast.pmf) == pytest.approx(1.0, abs=1e-9)
    assert forecast.over + forecast.push + forecast.under == pytest.approx(1.0, abs=1e-9)
    assert all(value >= 0.0 for value in forecast.pmf)


def test_an_unsupported_market_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="unsupported market"):
        score_prop(_inputs(prop_type="double_doubles"))


def test_thin_history_is_refused() -> None:
    with pytest.raises(ValueError, match="games of history"):
        score_prop(_inputs(history=_history(count=MINIMUM_HISTORY_GAMES - 1)))


def test_a_prior_season_lowers_the_history_requirement() -> None:
    """Skipping every player in April is a choice, and it was the wrong one."""
    thin = _history(count=5)
    with pytest.raises(ValueError, match="games of history"):
        score_prop(_inputs(history=thin))

    forecast = score_prop(
        _inputs(history=thin, prior_season=PriorSeasonRate(rate_per_minute=0.52, minutes=700.0))
    )
    assert forecast.mean > 0.0


def test_a_prior_season_pulls_the_rate_toward_itself() -> None:
    thin = _history(count=8)
    low = score_prop(
        _inputs(history=thin, prior_season=PriorSeasonRate(rate_per_minute=0.20, minutes=700.0))
    )
    high = score_prop(
        _inputs(history=thin, prior_season=PriorSeasonRate(rate_per_minute=0.90, minutes=700.0))
    )
    assert high.mean > low.mean


# --------------------------------------------------------------------------------------
# Minutes: the two bugs that motivated the rewrite
# --------------------------------------------------------------------------------------
def test_a_zero_minute_projection_is_not_treated_as_a_missing_one() -> None:
    """``role_minutes or weighted_mean(history)`` substituted the average for a projected zero."""
    adjustments = resolve_adjustments(_inputs(role=RoleInputs(expected_minutes=0.0)))
    assert adjustments.expected_minutes == pytest.approx(0.0)


def test_a_zero_minute_projection_produces_a_forecast_of_essentially_nothing() -> None:
    forecast = score_prop(_inputs(role=RoleInputs(expected_minutes=0.0)))
    assert forecast.mean < 1.0


def test_every_component_reads_the_same_expected_minutes() -> None:
    """The simulator and the parametric components used to disagree whenever role was absent.

    Each had its own fallback, and one filtered out zero-minute games while the other did not.
    With minutes resolved once, doubling the projection must move the empirical component and
    the hierarchical component in the same direction and by a comparable amount.
    """
    low = score_prop(_inputs(role=RoleInputs(expected_minutes=15.0)))
    high = score_prop(_inputs(role=RoleInputs(expected_minutes=30.0)))

    by_name_low = {component.name: component.mean for component in low.components}
    by_name_high = {component.name: component.mean for component in high.components}
    for name in ("empirical", "hierarchical", "player_state", "opportunity_conversion"):
        assert by_name_high[name] > by_name_low[name]
    empirical_ratio = by_name_high["empirical"] / by_name_low["empirical"]
    hierarchical_ratio = by_name_high["hierarchical"] / by_name_low["hierarchical"]
    assert empirical_ratio == pytest.approx(hierarchical_ratio, rel=0.15)


def test_no_role_projection_still_produces_coherent_minutes() -> None:
    adjustments = resolve_adjustments(_inputs())
    assert 0.0 < adjustments.expected_minutes <= 45.0
    assert adjustments.minutes_std > 0.0


def test_minutes_scenarios_preserve_the_mixture_behind_the_headline() -> None:
    adjustments = resolve_adjustments(
        _inputs(role=RoleInputs(expected_minutes=27.0, start_probability=0.60))
    )

    mixed = (
        adjustments.start_probability * adjustments.starter_minutes
        + (1.0 - adjustments.start_probability) * adjustments.bench_minutes
    )
    assert mixed == pytest.approx(adjustments.expected_minutes)
    assert adjustments.starter_minutes_std > 0.0
    assert adjustments.bench_minutes_std > 0.0


# --------------------------------------------------------------------------------------
# Dispersion and combination markets
# --------------------------------------------------------------------------------------
def test_a_combo_market_disperses_more_than_its_parts() -> None:
    """Points+rebounds+assists is a sum of correlated counts, not a count with the summed rate."""
    history = _history()
    points = resolve_dispersion(_inputs(history=history, prop_type="points"))
    rebounds = resolve_dispersion(
        _inputs(history=history, prop_type="rebounds", line=Decimal("5.5"))
    )
    combo = resolve_dispersion(
        _inputs(history=history, prop_type="points_rebounds_assists", line=Decimal("24.5"))
    )
    assert combo > min(points, rebounds)


def test_dispersion_exceeds_one_for_a_points_market() -> None:
    assert resolve_dispersion(_inputs()) > 1.2


def test_the_forecast_is_wider_than_a_poisson_of_the_same_mean() -> None:
    """The single change that most affects tail lines."""
    forecast = score_prop(_inputs())
    assert forecast.stddev > math.sqrt(forecast.mean) * 1.15


# --------------------------------------------------------------------------------------
# Features that used to be computed and discarded
# --------------------------------------------------------------------------------------
def test_rest_days_no_longer_move_the_forecast() -> None:
    """Measured against settled outcomes, rest was not a signal; the scorer stops pretending."""
    rested = score_prop(
        _inputs(
            matchup=MatchupInputs(team_rest_days=4.0, opponent_rest_days=4.0),
            role=RoleInputs(expected_minutes=30.0),
        )
    )
    back_to_back = score_prop(
        _inputs(
            matchup=MatchupInputs(team_rest_days=1.0, opponent_rest_days=4.0),
            role=RoleInputs(expected_minutes=30.0),
        )
    )
    assert back_to_back.mean == pytest.approx(rested.mean)


def test_start_probability_moves_a_rotation_uncertain_player() -> None:
    likely = score_prop(_inputs(role=RoleInputs(start_probability=0.95)))
    unlikely = score_prop(_inputs(role=RoleInputs(start_probability=0.05)))
    assert likely.mean > unlikely.mean


def test_a_shaded_market_price_moves_the_market_component() -> None:
    """The old prior read only the line and threw the multipliers away."""
    flat = score_prop(_inputs())
    leaning_over = score_prop(
        _inputs(market=MarketInputs(over_probability=0.75, under_probability=0.25))
    )
    flat_market = {c.name: c.over for c in flat.components}["market_prior"]
    shaded_market = {c.name: c.over for c in leaning_over.components}["market_prior"]
    assert shaded_market > flat_market
    assert shaded_market == pytest.approx(0.75, abs=0.05)
    assert leaning_over.over > flat.over


def test_an_uninformative_price_is_not_treated_as_evidence() -> None:
    assert not MarketInputs().is_informative
    assert not MarketInputs(over_probability=0.6).is_informative


def test_teammate_and_matchup_multipliers_are_bounded() -> None:
    """A feature may nudge a forecast; it may not author one."""
    extreme = resolve_adjustments(
        _inputs(
            teammate=TeammateInputs(rate_multiplier=9.0, effect_count=3),
            matchup=MatchupInputs(pace_multiplier=3.0, defense_multiplier=3.0),
        )
    )
    assert extreme.rate_multiplier <= 1.35
    assert extreme.matchup_multiplier <= 1.25


def test_a_blowout_no_longer_shortens_projected_minutes() -> None:
    """Gated to zero: the probability is quantized and confounded with pace (r = -0.80, #68)."""
    calm = resolve_adjustments(
        _inputs(
            role=RoleInputs(expected_minutes=30.0),
            matchup=MatchupInputs(blowout_probability=0.0),
        )
    )
    rout = resolve_adjustments(
        _inputs(
            role=RoleInputs(expected_minutes=30.0),
            matchup=MatchupInputs(blowout_probability=1.0),
        )
    )
    assert rout.expected_minutes == pytest.approx(calm.expected_minutes)


# --------------------------------------------------------------------------------------
# Pooling, weights, calibration
# --------------------------------------------------------------------------------------
def test_the_log_pool_is_sharper_than_the_mixture() -> None:
    pooled = score_prop(_inputs(pool="log"))
    mixed = score_prop(_inputs(pool="linear"))
    assert pooled.stddev <= mixed.stddev


def test_weights_change_the_answer() -> None:
    market_heavy = EnsembleWeights(
        market="points",
        weights={
            "empirical": 0.0,
            "hierarchical": 0.0,
            "player_state": 0.0,
            "opportunity_conversion": 0.0,
            "market_prior": 1.0,
        },
        sample_size=1000,
        is_fitted=True,
    )
    forecast = score_prop(_inputs(weights=market_heavy))
    assert forecast.mean == pytest.approx(float(Decimal("15.5")) + 0.5, abs=1.0)


def test_disagreement_is_weighted_by_the_trust_each_component_is_given() -> None:
    """A component at 2% weight wandering off is not evidence that the ensemble is confused."""
    concentrated = EnsembleWeights(
        market="points",
        weights={
            "empirical": 1.0,
            "hierarchical": 0.0,
            "player_state": 0.0,
            "opportunity_conversion": 0.0,
            "market_prior": 0.0,
        },
        sample_size=1000,
    )
    assert score_prop(_inputs(weights=concentrated)).disagreement == pytest.approx(0.0, abs=1e-9)


def test_calibration_is_actually_applied_to_the_output() -> None:
    """The loop the learning stage measured and then failed to close.

    Raw and calibrated probabilities are both reported rather than the raw one being
    overwritten: an audit that cannot see what the model said before the correction cannot tell
    a well-calibrated model from a badly-calibrated one wearing a good map.
    """
    raw = score_prop(_inputs())
    squashing = CalibrationMap(market="points", knots=((0.0, 0.45), (1.0, 0.55)), sample_size=10**6)
    calibrated = score_prop(_inputs(calibration=squashing))

    assert calibrated.over == pytest.approx(raw.over)
    assert calibrated.calibrated_over != pytest.approx(raw.calibrated_over)
    assert abs(calibrated.calibrated_over - 0.5) < abs(raw.over - 0.5)


def test_calibrated_sides_remain_coherent_with_the_push() -> None:
    forecast = score_prop(_inputs(line=Decimal("16")))
    total = forecast.calibrated_over + forecast.calibrated_under + forecast.push
    assert total == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------
def test_data_quality_can_actually_reach_the_top_of_its_range() -> None:
    """The old score capped at 0.9 against a 0.85 gate, making it a game count in disguise."""
    complete = score_prop(
        _inputs(
            role=RoleInputs(expected_minutes=30.0, start_probability=0.8),
            teammate=TeammateInputs(rate_multiplier=1.05, effect_count=1),
            matchup=MatchupInputs(pace_multiplier=1.01, team_rest_days=2.0),
            prior_season=PriorSeasonRate(rate_per_minute=0.5, minutes=600.0),
        )
    )
    bare = score_prop(_inputs())
    assert complete.data_quality_score == pytest.approx(1.0)
    assert bare.data_quality_score < complete.data_quality_score


def test_diagnostics_record_what_produced_the_number() -> None:
    forecast = score_prop(
        _inputs(matchup=MatchupInputs(team_rest_days=1.0, opponent_rest_days=2.0))
    )
    diagnostics = forecast.diagnostics
    assert diagnostics["history_games"] == 20
    # Rest is a measured non-signal, so the diagnostics must show the identity adjustment.
    assert diagnostics["rest_minutes_multiplier"] == 1.0
    assert set(diagnostics["weights"]) == {component.name for component in forecast.components}
    assert diagnostics["dispersion"] == forecast.dispersion


def test_every_component_is_reported_with_its_own_distribution() -> None:
    forecast = score_prop(_inputs())
    assert len(forecast.components) == 5
    for component in forecast.components:
        assert math.fsum(component.pmf) == pytest.approx(1.0, abs=1e-9)
        assert component.over + component.push + component.under == pytest.approx(1.0, abs=1e-9)
