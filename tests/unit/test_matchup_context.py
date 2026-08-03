from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from wnba_services.feature_engine.matchup import TeamGame, _load_team_games, estimate_matchup


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


def test_loader_handles_multiple_players_without_synthetic_key_leak() -> None:
    game_id, home_id, away_id = uuid4(), uuid4(), uuid4()
    rows: list[dict[str, object]] = []
    for team_id in (home_id, away_id):
        for _ in range(2):
            rows.append(
                {
                    "game_id": game_id,
                    "team_id": team_id,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "scheduled_tipoff": datetime(2026, 8, 1, tzinfo=UTC),
                    "points": 10,
                    "rebounds_offensive": 1,
                    "rebounds_defensive": 3,
                    "assists": 2,
                    "three_pointers_made": 1,
                    "field_goals_attempted": 8,
                    "free_throws_attempted": 2,
                    "turnovers": 1,
                }
            )
    loaded = _load_team_games(rows)
    assert len(loaded) == 2
    assert all(game.totals["points"] == 20 for game in loaded)
