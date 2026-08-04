from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from wnba_services.feature_engine.matchup import (
    TeamGame,
    _load_team_games,
    estimate_matchup,
    opponent_adjusted_rates,
)


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


def _game(
    team_id: UUID,
    opponent_id: UUID,
    scored: float,
    allowed: float,
    index: int,
) -> TeamGame:
    return TeamGame(
        game_id=uuid4(),
        team_id=team_id,
        opponent_id=opponent_id,
        tipoff=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=index),
        possessions=100.0,
        point_diff=scored - allowed,
        totals={"points": scored},
        allowed={"points": allowed},
    )


def test_opponent_adjustment_separates_defence_from_schedule() -> None:
    """The defect: a team that only faced weak offences read as an elite defence.

    Two defences concede exactly the same raw rate. One did it against the league's best
    offence, the other against its worst. The raw feature calls them identical; the adjusted
    one must credit the first and penalise the second.
    """
    strong_offence, weak_offence = uuid4(), uuid4()
    flattered, genuine = uuid4(), uuid4()
    filler = uuid4()

    games: list[TeamGame] = []
    index = 0
    for _ in range(8):
        # The league's baseline: the strong offence scores 120, the weak one scores 80.
        games.append(_game(strong_offence, filler, 120.0, 100.0, index))
        games.append(_game(filler, strong_offence, 100.0, 120.0, index))
        games.append(_game(weak_offence, filler, 80.0, 100.0, index + 1))
        games.append(_game(filler, weak_offence, 100.0, 80.0, index + 1))
        # Both defences concede 100 -- but to very different offences.
        games.append(_game(flattered, strong_offence, 100.0, 100.0, index + 2))
        games.append(_game(strong_offence, flattered, 100.0, 100.0, index + 2))
        games.append(_game(genuine, weak_offence, 100.0, 100.0, index + 3))
        games.append(_game(weak_offence, genuine, 100.0, 100.0, index + 3))
        index += 4

    rates = opponent_adjusted_rates(games, ("points",))
    assert rates.defense_rate(flattered) < rates.defense_rate(genuine)


def test_adjusted_effects_are_centred_and_therefore_identified() -> None:
    team_a, team_b, team_c = uuid4(), uuid4(), uuid4()
    games: list[TeamGame] = []
    for index in range(6):
        games.append(_game(team_a, team_b, 105.0, 95.0, index))
        games.append(_game(team_b, team_a, 95.0, 105.0, index))
        games.append(_game(team_b, team_c, 100.0, 100.0, index + 10))
        games.append(_game(team_c, team_b, 100.0, 100.0, index + 10))

    rates = opponent_adjusted_rates(games, ("points",))
    assert sum(rates.offense.values()) == pytest.approx(0.0, abs=1e-9)
    assert sum(rates.defense.values()) == pytest.approx(0.0, abs=1e-9)
    assert rates.league_rate > 0.0


def test_a_thinly_observed_defence_is_pulled_toward_the_league() -> None:
    """One catastrophic night is not a defensive identity."""
    established_a, established_b, newcomer = uuid4(), uuid4(), uuid4()
    games: list[TeamGame] = []
    for index in range(10):
        games.append(_game(established_a, established_b, 100.0, 100.0, index))
        games.append(_game(established_b, established_a, 100.0, 100.0, index))
    # The newcomer appears once and is taken apart.
    games.append(_game(newcomer, established_a, 90.0, 140.0, 20))
    games.append(_game(established_a, newcomer, 140.0, 90.0, 20))

    rates = opponent_adjusted_rates(games, ("points",))
    raw_gap = abs(rates.defense_rate(newcomer) - rates.league_rate)
    shrunk_gap = abs(rates.shrunk_defense_rate(newcomer) - rates.league_rate)
    assert raw_gap > 0.0
    assert shrunk_gap < raw_gap


def test_adjusted_rates_survive_an_empty_league() -> None:
    rates = opponent_adjusted_rates([], ("points",))
    assert rates.league_rate == 0.0
    assert rates.defense_rate(uuid4()) == 0.0
