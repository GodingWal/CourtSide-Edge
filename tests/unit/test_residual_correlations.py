"""Residual correlations: what the box scores say about how legs move together.

The episode fit measures correlation on settled bets, which is sparse and entangled with
where lines sat. The residual fit measures it on every played game against each player's own
rolling baseline. These tests pin the leakage rule (a baseline never contains its own game),
the sample gate, and the merge precedence.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from wnba_services.market_engine.correlation import (
    CorrelationEstimate,
    ResidualPair,
    fit_residual_correlations,
    merge_correlation_estimates,
    residual_pair_observations,
)

_BASE = datetime(2026, 6, 1, tzinfo=UTC)


def _game(player: str, game: str, team: str, day: int, **stats: float) -> dict:
    row = {
        "player_id": player,
        "game_id": game,
        "team_id": team,
        "tipoff": _BASE + timedelta(days=day),
        "minutes": 30.0,
        "points": 10.0,
        "rebounds": 5.0,
        "assists": 3.0,
    }
    row.update(stats)
    return row


def _history(player: str, team: str, days: range, *, game_prefix: str) -> list[dict]:
    return [
        _game(player, f"{game_prefix}{day}", team, day, points=float(day), rebounds=float(day % 7))
        for day in days
    ]


def test_a_baseline_never_contains_its_own_game() -> None:
    # Nine flat games then one spike: the spike's residual is measured against the flat
    # baseline, so it must be the full deviation, not a diluted one.
    rows = _history("p1", "A", range(1, 10), game_prefix="g")
    rows.append(_game("p1", "g10", "A", 10, points=38.0, rebounds=5.0))
    rows.append(_game("p2", "g10", "B", 10, points=0.0))
    pairs = [
        pair
        for pair in residual_pair_observations(rows)
        if pair.relation == "same_player" and pair.prop_a == "points" and pair.prop_b == "rebounds"
    ]
    # Days 6-10 all have five-plus priors, so several pairs exist; the spike game is the
    # largest residual and the one whose baseline must exclude it.
    spike = max(pairs, key=lambda pair: pair.residual_a)
    baseline = sum(range(1, 10)) / 9  # 5.0
    assert spike.residual_a == 38.0 - baseline


def test_thin_histories_earn_no_residual() -> None:
    rows = _history("p1", "A", range(1, 5), game_prefix="g")
    rows.append(_game("p1", "g5", "A", 5, points=30.0))
    assert residual_pair_observations(rows) == []


def test_teammates_and_opponents_are_classified() -> None:
    rows = []
    for player, team in (("p1", "A"), ("p2", "A"), ("p3", "B")):
        rows.extend(_history(player, team, range(1, 7), game_prefix=f"{player}-"))
        rows.append(_game(player, "shared", team, 7, points=20.0))
    pairs = residual_pair_observations(rows)
    relations = {pair.relation for pair in pairs}
    assert "same_team" in relations
    assert "opposing_team" in relations
    same_team = {
        (pair.prop_a, pair.prop_b) for pair in pairs if pair.relation == "same_team"
    }
    # Teammates pair across all nine stat combinations; same-player pairs are cross-stat only.
    assert ("points", "points") in same_team
    same_player = {
        (pair.prop_a, pair.prop_b) for pair in pairs if pair.relation == "same_player"
    }
    assert ("points", "points") not in same_player


def test_fit_recovers_a_planted_correlation() -> None:
    pairs = [
        ResidualPair("same_player", "assists", "points", math.sin(i / 3.0), 2.0 * math.sin(i / 3.0))
        for i in range(200)
    ]
    (estimate,) = fit_residual_correlations(pairs)
    assert estimate.is_fitted
    # A planted r of 1.0 saturates the simulator clamp at _MAX_ABS_CORRELATION = 0.95.
    assert estimate.correlation >= 0.95
    assert estimate.standard_error < 0.01


def test_fit_respects_the_sample_gate() -> None:
    pairs = [ResidualPair("same_player", "assists", "points", 1.0, 1.0) for _ in range(29)]
    assert fit_residual_correlations(pairs) == []


def test_residual_measurement_wins_the_merge() -> None:
    episode = CorrelationEstimate("same_player", "assists", "points", 0.10, 0.20, 40, True)
    residual = CorrelationEstimate("same_player", "assists", "points", 0.13, 0.02, 4000, True)
    (merged,) = merge_correlation_estimates([episode], [residual])
    assert merged.correlation == 0.13
    assert merged.sample_size == 4000


def test_episode_estimate_stands_where_box_scores_are_silent() -> None:
    episode = CorrelationEstimate("same_team", "points", "points", 0.05, 0.20, 12, False)
    merged = merge_correlation_estimates([episode], [])
    assert merged == [episode]


def test_unfitted_residual_does_not_override() -> None:
    episode = CorrelationEstimate("same_player", "assists", "points", 0.30, 0.20, 0, False)
    residual = CorrelationEstimate("same_player", "assists", "points", 0.99, 0.01, 5, False)
    (merged,) = merge_correlation_estimates([episode], [residual])
    assert merged.correlation == 0.30
