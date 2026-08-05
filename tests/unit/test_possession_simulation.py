"""What the shared game state actually does, measured rather than asserted.

Every correlation here is produced by the mechanism: each iteration draws one possession count
and one blowout outcome, and every player in that iteration is generated under them. Nothing in
the module sets a correlation, so these tests are checking physics rather than a constant.

Iteration counts are chosen against Monte Carlo error. At 20,000 iterations the standard error on
a correlation is about 0.007, so the tolerances below are set well outside it -- a test that
asserts a correlation to three decimals is testing the seed.
"""

from __future__ import annotations

import pytest
from wnba_services.forecasting.possession import (
    LEAGUE_POSSESSIONS,
    PlayerGameState,
    correlation_matrix,
    simulate_game,
)

ITERATIONS = 20_000


def _players() -> list[PlayerGameState]:
    return [
        PlayerGameState(
            "starter_a", 32.0, 3.0, {"points": 0.50, "rebounds": 0.22, "assists": 0.15}
        ),
        PlayerGameState(
            "starter_b", 30.0, 3.0, {"points": 0.45, "rebounds": 0.30, "assists": 0.10}
        ),
        PlayerGameState(
            "bench_c",
            14.0,
            4.0,
            {"points": 0.35, "rebounds": 0.18, "assists": 0.08},
            is_starter=False,
        ),
    ]


def test_a_shared_blowout_pulls_two_starters_together() -> None:
    """The same event takes minutes from both, so both fall together."""
    calm = simulate_game(_players(), iterations=ITERATIONS, blowout_probability=0.0, seed=5)
    risky = simulate_game(_players(), iterations=ITERATIONS, blowout_probability=0.45, seed=5)

    assert risky.player_correlation("starter_a", "starter_b") > (
        calm.player_correlation("starter_a", "starter_b") + 0.04
    )


def test_the_same_blowout_pushes_a_starter_and_a_bench_player_apart() -> None:
    """The sign flip a single scalar correlation cannot represent.

    A blowout takes minutes from the starter and gives them to the bench. An entry priced with
    one correlation applied to every pair gets this pairing backwards.
    """
    risky = simulate_game(_players(), iterations=ITERATIONS, blowout_probability=0.45, seed=5)

    starters = risky.player_correlation("starter_a", "starter_b")
    mixed = risky.player_correlation("starter_a", "bench_c")

    assert starters > 0
    assert mixed < 0
    assert starters > mixed


def test_pace_alone_is_a_weak_and_uniform_correlator() -> None:
    """Measured, and not what a possessions-first mental model predicts.

    The shared pace component is small next to a single game's count noise, so it moves everyone
    together only slightly -- and it moves the bench player as much as the starters, because
    nothing about pace distinguishes them.
    """
    calm = simulate_game(_players(), iterations=ITERATIONS, blowout_probability=0.0, seed=5)

    starters = calm.player_correlation("starter_a", "starter_b")
    mixed = calm.player_correlation("starter_a", "bench_c")

    assert 0.0 <= starters < 0.05
    assert abs(starters - mixed) < 0.03


def test_a_player_is_perfectly_correlated_with_themselves_and_the_matrix_is_symmetric() -> None:
    simulation = simulate_game(_players(), iterations=2000, seed=5)

    matrix = correlation_matrix(simulation)

    for player in matrix:
        assert matrix[player][player] == 1.0
        for other in matrix:
            assert abs(matrix[player][other] - matrix[other][player]) < 1e-9


def test_the_combination_probability_comes_from_joint_samples() -> None:
    """Summing independent marginals understates a combination's spread; this does not."""
    simulation = simulate_game(_players(), iterations=ITERATIONS, seed=5)
    player = simulation.by_player()["starter_a"]

    total_mean = sum(player.mean(stat) for stat in ("points", "rebounds", "assists"))
    at_the_mean = player.combination_probability(["points", "rebounds", "assists"], total_mean)

    # A line at the mean of a right-skewed count total sits just above the median.
    assert 0.35 < at_the_mean < 0.55


def test_points_and_rebounds_covary_within_a_player_through_shared_minutes() -> None:
    simulation = simulate_game(_players(), iterations=ITERATIONS, seed=5)
    player = simulation.by_player()["starter_a"]

    assert player.stat_correlation("points", "rebounds") > 0


def test_pace_is_a_wnba_number_not_an_imported_one() -> None:
    """The league plays 40 minutes. An NBA pace constant would be a silent 20% error."""
    assert 70.0 <= LEAGUE_POSSESSIONS <= 90.0


def test_a_player_who_never_scores_is_not_correlated_with_everyone() -> None:
    """A constant series is uninformative, not perfectly correlated."""
    players = [
        *_players(),
        PlayerGameState("dnp", 0.0, 0.0, {"points": 0.0, "rebounds": 0.0, "assists": 0.0}),
    ]
    simulation = simulate_game(players, iterations=2000, seed=5)

    assert simulation.player_correlation("dnp", "starter_a") == 0.0


def test_an_unknown_player_correlates_with_nothing_rather_than_raising() -> None:
    simulation = simulate_game(_players(), iterations=500, seed=5)

    assert simulation.player_correlation("starter_a", "not_in_this_game") == 0.0


@pytest.mark.parametrize("possessions", [72.0, 80.0, 88.0])
def test_expected_possessions_moves_the_simulated_pace(possessions: float) -> None:
    simulation = simulate_game(
        _players(), iterations=2000, seed=5, expected_possessions=possessions
    )

    assert abs(simulation.mean_possessions - possessions) < 1.0
