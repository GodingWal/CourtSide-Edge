from __future__ import annotations

import pytest
from wnba_services.forecasting.game_simulation import PlayerStatScenario, simulate_joint_game


def _player(key: str, team: str, *, line: float = 15.5) -> PlayerStatScenario:
    return PlayerStatScenario(key, team, "points", 18.0, 5.0, 32.0, 3.0, line)


def test_joint_game_simulation_is_seeded_and_preserves_shape() -> None:
    players = [_player("a", "NYL"), _player("b", "NYL"), _player("c", "MIN")]
    first = simulate_joint_game(players, simulations=2000, seed=91)
    second = simulate_joint_game(players, simulations=2000, seed=91)
    assert first == second
    assert len(first.covariance) == 3
    assert len(first.correlation[0]) == 3
    assert all(value is not None and 0.0 <= value <= 1.0 for value in first.hit_probabilities)


def test_shared_team_usage_creates_more_within_team_dependence() -> None:
    players = [_player("a", "NYL"), _player("b", "NYL"), _player("c", "MIN")]
    result = simulate_joint_game(players, simulations=20_000, seed=19)
    assert result.correlation[0][1] > result.correlation[0][2]


def test_same_player_markets_share_minutes_and_efficiency() -> None:
    points = PlayerStatScenario("points", "NYL", "points", 18, 5, 32, 4, 15.5, player_id="p1")
    combo = PlayerStatScenario(
        "pra", "NYL", "points_rebounds_assists", 28, 7, 32, 4, 25.5, player_id="p1"
    )
    teammate = PlayerStatScenario("teammate", "NYL", "points", 14, 4, 28, 3, 13.5, player_id="p2")
    result = simulate_joint_game([points, combo, teammate], simulations=20_000, seed=31)
    assert result.correlation[0][1] > result.correlation[0][2]


def test_blowouts_jointly_reduce_starter_output() -> None:
    players = [_player("a", "NYL"), _player("b", "MIN")]
    normal = simulate_joint_game(players, blowout_probability=0.0, simulations=5000, seed=7)
    blowout = simulate_joint_game(players, blowout_probability=1.0, simulations=5000, seed=7)
    assert blowout.means[0] < normal.means[0]
    assert blowout.means[1] < normal.means[1]


def test_joint_game_simulation_validates_inputs() -> None:
    with pytest.raises(ValueError, match="at least one"):
        simulate_joint_game([])
    with pytest.raises(ValueError, match="side"):
        simulate_joint_game(
            [PlayerStatScenario("a", "NYL", "points", 10, 2, 20, 2, 10, "middle")],
            simulations=100,
        )
