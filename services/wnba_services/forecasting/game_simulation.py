"""Joint game-state simulator for correlated player-stat scenarios.

This is a shadow diagnostic, not a new champion.  Pace, team usage and blowout state are drawn
once per simulated game and shared by every player, so downstream correlations arise from the
same basketball state instead of being painted onto independent leg probabilities afterwards.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

__all__ = ["JointGameResult", "PlayerStatScenario", "simulate_joint_game"]


@dataclass(frozen=True)
class PlayerStatScenario:
    key: str
    team: str
    prop_type: str
    mean: float
    stddev: float
    projected_minutes: float
    minutes_std: float
    line: float | None = None
    side: str = "over"
    starter_probability: float = 1.0
    player_id: str | None = None


@dataclass(frozen=True)
class JointGameResult:
    keys: tuple[str, ...]
    means: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    correlation: tuple[tuple[float, ...], ...]
    hit_probabilities: tuple[float | None, ...]
    scenario_summary: dict[str, float]
    simulations: int
    seed: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "keys": list(self.keys),
            "means": list(self.means),
            "covariance": [list(row) for row in self.covariance],
            "correlation": [list(row) for row in self.correlation],
            "hit_probabilities": list(self.hit_probabilities),
            "scenario_summary": self.scenario_summary,
            "simulations": self.simulations,
            "seed": self.seed,
        }


def _safe_correlation(samples: NDArray[np.float64]) -> NDArray[np.float64]:
    if samples.shape[1] == 1:
        return np.ones((1, 1), dtype=np.float64)
    result = np.corrcoef(samples, rowvar=False)
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def simulate_joint_game(
    players: Sequence[PlayerStatScenario],
    *,
    expected_pace: float = 80.0,
    pace_std: float = 4.0,
    blowout_probability: float = 0.15,
    simulations: int = 20_000,
    seed: int = 0,
) -> JointGameResult:
    if not players:
        raise ValueError("a joint game simulation needs at least one player prop")
    if simulations < 100:
        raise ValueError("at least 100 simulations are required")
    if not 0.0 <= blowout_probability <= 1.0:
        raise ValueError("blowout probability must lie in [0, 1]")

    rng = np.random.default_rng(seed)
    pace = np.clip(rng.normal(expected_pace, pace_std, simulations), 55.0, 115.0)
    pace_factor = pace / max(1.0, expected_pace)
    blowout = rng.random(simulations) < blowout_probability
    teams = sorted({player.team for player in players})
    usage = {team: rng.normal(0.0, 0.055, simulations) for team in teams}
    player_ids = {player.player_id or player.key for player in players}
    start_draw = {player_id: rng.random(simulations) for player_id in player_ids}
    minutes_noise = {player_id: rng.normal(0.0, 1.0, simulations) for player_id in player_ids}
    player_efficiency = {player_id: rng.normal(0.0, 1.0, simulations) for player_id in player_ids}
    output = np.empty((simulations, len(players)), dtype=np.float64)

    for index, player in enumerate(players):
        player_id = player.player_id or player.key
        starts = start_draw[player_id] < player.starter_probability
        minutes = player.projected_minutes + minutes_noise[player_id] * max(0.5, player.minutes_std)
        minutes -= blowout * np.where(starts, 4.0, -1.5)
        minutes = np.clip(minutes, 0.0, 45.0)
        minutes_factor = minutes / max(1.0, player.projected_minutes)
        # Team usage is shared, while efficiency is idiosyncratic. Count-like output is clipped
        # at zero but deliberately remains continuous: the champion's discrete distribution is
        # still authoritative; this simulator estimates dependence and scenarios.
        efficiency_sd = max(0.04, player.stddev / max(1.0, player.mean) * 0.55)
        efficiency = efficiency_sd * (
            0.65 * player_efficiency[player_id]
            + math.sqrt(1.0 - 0.65**2) * rng.normal(0.0, 1.0, simulations)
        )
        raw = player.mean * pace_factor * minutes_factor * np.exp(usage[player.team] + efficiency)
        output[:, index] = np.maximum(0.0, raw)

    covariance = np.atleast_2d(np.cov(output, rowvar=False, ddof=1))
    correlation = _safe_correlation(output)
    hits: list[float | None] = []
    for index, player in enumerate(players):
        if player.line is None:
            hits.append(None)
        elif player.side == "over":
            hits.append(float(np.mean(output[:, index] > player.line)))
        elif player.side == "under":
            hits.append(float(np.mean(output[:, index] < player.line)))
        else:
            raise ValueError("side must be 'over' or 'under'")
    return JointGameResult(
        keys=tuple(player.key for player in players),
        means=tuple(float(value) for value in output.mean(axis=0)),
        covariance=tuple(tuple(float(value) for value in row) for row in covariance),
        correlation=tuple(tuple(float(value) for value in row) for row in correlation),
        hit_probabilities=tuple(hits),
        scenario_summary={
            "mean_pace": float(pace.mean()),
            "pace_p10": float(np.quantile(pace, 0.10)),
            "pace_p90": float(np.quantile(pace, 0.90)),
            "realized_blowout_rate": float(blowout.mean()),
        },
        simulations=simulations,
        seed=seed,
    )
