from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from wnba_services.feature_engine.matchup import TeamGame, estimate_matchup


def team_games(team_id: UUID, opponent_id: UUID, pace: float, allowed: float) -> list[TeamGame]:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    return [
        TeamGame(
            game_id=uuid4(),
            team_id=team_id,
            opponent_id=opponent_id,
            tipoff=now - timedelta(days=20 - index),
            possessions=pace,
            point_diff=2.0,
            totals={"points": 80.0},
            allowed={"points": allowed},
        )
        for index in range(10)
    ]


def test_matchup_adjustments_are_bounded() -> None:
    team_id, opponent_id = uuid4(), uuid4()
    result = estimate_matchup(
        team_games(team_id, opponent_id, 82.0, 80.0),
        team_games(opponent_id, team_id, 86.0, 95.0),
        league_pace=80.0,
        league_allowed_rate=1.0,
        prop_type="points",
        target_tipoff=datetime(2026, 8, 4, tzinfo=UTC),
        is_home=True,
    )
    assert result is not None
    assert 0.9 <= result.pace_multiplier <= 1.1
    assert 0.85 <= result.defense_multiplier <= 1.15
    assert 0.0 <= result.blowout_probability <= 1.0


def test_matchup_requires_five_games_per_team() -> None:
    team_id, opponent_id = uuid4(), uuid4()
    assert (
        estimate_matchup(
            team_games(team_id, opponent_id, 80.0, 80.0)[:4],
            team_games(opponent_id, team_id, 80.0, 80.0),
            league_pace=80.0,
            league_allowed_rate=1.0,
            prop_type="points",
            target_tipoff=datetime(2026, 8, 4, tzinfo=UTC),
            is_home=False,
        )
        is None
    )
