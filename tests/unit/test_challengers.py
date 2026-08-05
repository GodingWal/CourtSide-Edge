"""What the challenger families have to be, to be worth running at all.

Two of these tests are about statistics and two are about honesty. The statistical ones check
that the models do the thing that distinguishes them from the champion: widen when the evidence
is thin, and move when the role moves. The honest ones check that a challenger is a genuinely
separate answer rather than a rebadged champion, and that it stays a pure function of the inputs
the champion also saw.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from wnba_services.forecasting.challengers import (
    CHALLENGERS,
    HierarchicalBayesChallenger,
    StateSpaceRoleChallenger,
    challenger_by_name,
    challenger_names,
)
from wnba_services.forecasting.scoring import (
    HistoryGame,
    PriorSeasonRate,
    RoleInputs,
    ScoringInputs,
    score_prop,
)


def _game(minutes: float, points: float, *, started: bool = True) -> HistoryGame:
    return HistoryGame(
        minutes=minutes,
        started=started,
        stats={
            "points": points,
            "field_goals_attempted": points * 0.9,
            "rebounds_offensive": 1.0,
            "rebounds_defensive": 3.0,
            "assists": 2.0,
            "three_pointers_made": 1.0,
            "three_pointers_attempted": 3.0,
        },
    )


def _inputs(history: tuple[HistoryGame, ...], **overrides: object) -> ScoringInputs:
    defaults: dict[str, object] = {
        "prop_type": "points",
        "line": Decimal("14.5"),
        "history": history,
        "league_rate_per_minute": 0.45,
        "seed": 7,
    }
    defaults.update(overrides)
    return ScoringInputs(**defaults)  # type: ignore[arg-type]


def _steady(count: int, minutes: float = 30.0, points: float = 15.0) -> tuple[HistoryGame, ...]:
    return tuple(_game(minutes, points) for _ in range(count))


@pytest.mark.parametrize("name", challenger_names())
def test_every_challenger_is_a_pure_function_returning_a_proper_distribution(name: str) -> None:
    inputs = _inputs(_steady(20))
    challenger = challenger_by_name(name)

    first = challenger.predict(inputs)
    second = challenger.predict(inputs)

    assert first.over == second.over
    assert math.isclose(math.fsum(first.pmf), 1.0, abs_tol=1e-9)
    assert 0.0 <= first.over <= 1.0
    assert math.isclose(first.over + first.push + first.under, 1.0, abs_tol=1e-9)
    assert first.name == name


def test_challengers_are_not_the_champion_wearing_a_new_name() -> None:
    """A challenger whose answers match the champion's teaches nothing when it wins."""
    inputs = _inputs(_steady(20))
    champion = score_prop(inputs)

    differences = [
        abs(challenger.predict(inputs).over - champion.over) for challenger in CHALLENGERS.values()
    ]

    assert all(difference > 0.001 for difference in differences)


def test_hierarchical_bayes_is_wider_when_the_same_rate_rests_on_less_evidence() -> None:
    """The whole point of carrying the posterior variance forward.

    Both players average exactly 0.5 points per minute. One has established it over twenty
    games, the other over four. A shrinkage point estimate reports the same distribution for
    both; a posterior does not.
    """
    challenger = HierarchicalBayesChallenger()
    role = RoleInputs(expected_minutes=30.0, minutes_std=3.0, start_probability=1.0)

    veteran = challenger.predict(_inputs(_steady(20), role=role))
    rookie = challenger.predict(
        _inputs(
            _steady(4),
            role=role,
            prior_season=PriorSeasonRate(rate_per_minute=0.5, minutes=120.0),
        )
    )

    assert rookie.stddev > veteran.stddev
    assert rookie.diagnostics["posterior_rate_sd"] > veteran.diagnostics["posterior_rate_sd"]
    # The same thin evidence that widens the interval also fails to move the prior as far, so
    # the rookie's central estimate sits closer to the league rate. Both effects are the pooling
    # doing its job, and a point-estimate model reports neither.
    league = 0.45
    assert abs(rookie.diagnostics["posterior_rate"] - league) < abs(
        veteran.diagnostics["posterior_rate"] - league
    )


def test_hierarchical_bayes_discounts_a_volatile_player_s_evidence() -> None:
    """Over-dispersed history is less information than the same minutes of steady history."""
    challenger = HierarchicalBayesChallenger()
    steady = _steady(16)
    volatile = tuple(
        _game(30.0, 15.0 + (14.0 if index % 2 else -14.0)) for index in range(len(steady))
    )

    steady_result = challenger.predict(_inputs(steady))
    volatile_result = challenger.predict(_inputs(volatile))

    assert volatile_result.diagnostics["overdispersion_phi"] > 1.5
    assert (
        volatile_result.diagnostics["effective_minutes"]
        < steady_result.diagnostics["effective_minutes"]
    )


def test_state_space_tracks_a_role_change_faster_than_the_champion() -> None:
    """Twelve bench games then five starts: the filter should follow, not average.

    The champion's ``player_state`` component decays the past at a fixed half-life. The filter's
    gain rises when the innovations stop looking like noise, so its level lands closer to the new
    role than a fixed decay does.
    """
    bench = [_game(12.0, 5.0, started=False) for _ in range(12)]
    promoted = [_game(32.0, 17.0) for _ in range(5)]
    inputs = _inputs(tuple(bench + promoted))

    result = StateSpaceRoleChallenger().predict(inputs)

    # The new role runs at ~0.53 points per minute, the old at ~0.42.
    assert result.diagnostics["filtered_rate"] > 0.47
    assert result.diagnostics["kalman_gain"] > 0.02
    # Without a projected-minutes row the filter supplies minutes, and it should have followed
    # the promotion rather than sitting near the bench average.
    assert result.diagnostics["used_role_projection"] is False
    assert result.diagnostics["filtered_minutes"] is not None
    assert result.diagnostics["filtered_minutes"] > 20.0


def test_state_space_defers_to_a_projected_minutes_row() -> None:
    """A rotation projection built from injuries and lineups outranks a filtered average."""
    inputs = _inputs(
        _steady(15),
        role=RoleInputs(expected_minutes=18.0, minutes_std=2.0, start_probability=0.0),
    )

    result = StateSpaceRoleChallenger().predict(inputs)

    assert result.diagnostics["used_role_projection"] is True
    assert result.diagnostics["expected_minutes"] == pytest.approx(18.0, abs=0.5)


def test_short_history_does_not_make_the_filter_invent_a_state() -> None:
    inputs = _inputs(_steady(11))

    result = StateSpaceRoleChallenger().predict(inputs)

    assert result.diagnostics["rate_forecast_sd"] >= 0.0
    assert 0.0 <= result.over <= 1.0


def test_unknown_challenger_names_are_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown challenger 'gradient-boosting'"):
        challenger_by_name("gradient-boosting")
