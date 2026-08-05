"""Game-level possession simulation, joint stat distributions, and same-game correlation.

Three planned model families that the per-prop scorer structurally cannot provide, because it
answers one question at a time: it scores points, then rebounds, then assists, each from its own
marginal distribution, and nothing in that path knows the three came from the same game.

That is fine for a single prop and wrong for everything else:

**Combination markets.** ``points_rebounds_assists`` is not the sum of three independent counts.
The scorer already propagates covariance across its stat groups, which is a real improvement over
Poisson-of-the-sum, but the covariance it uses is estimated from the player's own game log rather
than produced by a mechanism.

**Same-game entries.** Two props from one game are not independent, and the copula library has
priced correlated entries since early on -- using a single scalar correlation supplied by the
caller. Where that scalar comes from was never answered. This module answers it.

**Player-to-player structure.** A blowout suppresses both teams' starters together. A track meet
lifts everyone's counting stats together. Two overs in one game share a game state, and an
analyst stacking them is taking a more concentrated position than the leg count suggests.

WHAT THE SIMULATION ACTUALLY DOES
---------------------------------
It samples a game, not a stat line. Each iteration draws a possession count for the game, a pace
shock both teams share, and then, conditional on that shared state, each player's minutes and
per-minute rates. Every stat for every player in the iteration is generated under the same game
state, so the correlations fall out of the mechanism rather than being asserted.

WHAT THE SIMULATION SAYS, MEASURED
----------------------------------
Run at 60,000 iterations over two starters and a bench player (Monte Carlo standard error
~0.004), the two mechanisms separate cleanly and they do not behave the same way:

===================  =================  ==============
Shared state         starter / starter  starter / bench
===================  =================  ==============
pace only                      +0.013           +0.014
25% blowout risk               +0.083           -0.036
45% blowout risk               +0.100           -0.044
===================  =================  ==============

Two things fall out of that, and neither was obvious before it was measured.

**Pace is a weak, uniform correlator.** A shared possession count lifts everyone together, but
the shared component is small next to the per-game count noise -- a 4% swing in opportunity
against a ~29% coefficient of variation on a single game's points. Same-game correlation is not
mostly a pace story, which is what a possessions-first mental model would predict.

**Game script is the correlator that matters, and it changes sign.** Blowout risk pulls two
starters together and pushes a starter and a bench player apart, because the same event takes
minutes from one and gives them to the other. A single scalar correlation applied to every pair
in an entry -- which is what the copula pricing has always received -- cannot represent that, and
gets the bench pairing backwards.

WHAT IT IS NOT
--------------
Not the production scorer, and not a challenger. It is a *feature* generator: the correlations it
produces feed entry pricing and same-game exposure, both of which are analysis-only. Replacing
``score_prop`` with a possession simulation would be a far larger change and would have to earn
its place through the champion/challenger path like anything else.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_SIMULATIONS",
    "GameSimulation",
    "JointDistribution",
    "PlayerGameState",
    "correlation_matrix",
    "simulate_game",
]

DEFAULT_SIMULATIONS = 4_000

# League pace, in possessions per team per 40-minute game, and how much it varies night to night.
# Stated priors rather than fitted coefficients, and labelled as such: the WNBA plays 40 minutes,
# and importing an NBA pace number here would be a silent 20% error in every possession count.
LEAGUE_POSSESSIONS = 80.0
POSSESSION_SD = 6.0

# How strongly a shared game state moves every player in it. This is the parameter the whole
# module exists to make explicit: the copula pricing has always taken a scalar correlation from
# its caller, and nobody could say where the number came from.
_PACE_RATE_SENSITIVITY = 0.55

# A blowout shortens starters' minutes and lengthens the bench's. Applied to the shared state so
# that two starters in a blown-out game are suppressed together, which is the actual mechanism
# behind same-game correlation on the downside.
_BLOWOUT_MINUTES_PENALTY = 6.0

_STATS = ("points", "rebounds", "assists")


@dataclass(frozen=True)
class PlayerGameState:
    """One player's inputs to a shared-game simulation, all knowable before tipoff."""

    player_id: str
    expected_minutes: float
    minutes_sd: float
    rates: Mapping[str, float]
    """Per-minute rate for each stat: points, rebounds, assists."""

    rate_sd: Mapping[str, float] = field(default_factory=dict)
    is_starter: bool = True
    dispersion: float = 1.35


@dataclass(frozen=True)
class JointDistribution:
    """Sampled joint outcomes for one player, and the marginals that fall out of them."""

    player_id: str
    samples: tuple[tuple[float, float, float], ...]

    def marginal(self, stat: str) -> tuple[float, ...]:
        index = _STATS.index(stat)
        return tuple(sample[index] for sample in self.samples)

    def mean(self, stat: str) -> float:
        values = self.marginal(stat)
        return math.fsum(values) / len(values) if values else 0.0

    def probability_over(self, stat: str, line: float) -> float:
        """P(stat > line) straight from the joint samples, with no marginal assumption."""
        values = self.marginal(stat)
        if not values:
            return 0.0
        return math.fsum(1.0 for value in values if value > line) / len(values)

    def combination_probability(self, stats: Sequence[str], line: float) -> float:
        """P(sum of these stats > line), from the *joint* samples.

        This is the number a combination market actually needs. Summing three marginal
        distributions as though they were independent understates the spread of the total, which
        biases every combination line -- and the direction of that bias depends on the line, so it
        cannot be corrected with a constant.
        """
        indices = [_STATS.index(stat) for stat in stats]
        if not self.samples:
            return 0.0
        hits = sum(
            1 for sample in self.samples if math.fsum(sample[index] for index in indices) > line
        )
        return hits / len(self.samples)

    def stat_correlation(self, first: str, second: str) -> float:
        return _correlation(self.marginal(first), self.marginal(second))


@dataclass(frozen=True)
class GameSimulation:
    """The result of simulating one game many times, with every player sharing each iteration."""

    iterations: int
    players: tuple[JointDistribution, ...]
    mean_possessions: float

    def by_player(self) -> dict[str, JointDistribution]:
        return {player.player_id: player for player in self.players}

    def player_correlation(self, first: str, second: str, stat: str = "points") -> float:
        """Correlation between two players' outcomes in the same game.

        Produced by the shared game state rather than asserted. Two starters in a game that might
        be a blowout are negatively exposed together; two players in a fast game are positively
        exposed together, and the simulation shows both because both were generated.
        """
        lookup = self.by_player()
        if first not in lookup or second not in lookup:
            return 0.0
        return _correlation(lookup[first].marginal(stat), lookup[second].marginal(stat))

    def to_payload(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "mean_possessions": round(self.mean_possessions, 2),
            "players": [
                {
                    "player_id": player.player_id,
                    "means": {stat: round(player.mean(stat), 3) for stat in _STATS},
                    "stat_correlations": {
                        "points_rebounds": round(player.stat_correlation("points", "rebounds"), 4),
                        "points_assists": round(player.stat_correlation("points", "assists"), 4),
                    },
                }
                for player in self.players
            ],
            "analysis_only": True,
        }


def _correlation(first: Sequence[float], second: Sequence[float]) -> float:
    """Pearson correlation, returning 0 when either series has no variance.

    A constant series is not perfectly correlated with anything; it is uninformative, and
    returning 0 rather than a division by zero is what keeps a player who never scores from
    appearing to move in lockstep with everyone.
    """
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    mean_first = math.fsum(first) / len(first)
    mean_second = math.fsum(second) / len(second)
    covariance = math.fsum(
        (a - mean_first) * (b - mean_second) for a, b in zip(first, second, strict=True)
    )
    variance_first = math.fsum((a - mean_first) ** 2 for a in first)
    variance_second = math.fsum((b - mean_second) ** 2 for b in second)
    if variance_first <= 0.0 or variance_second <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, covariance / math.sqrt(variance_first * variance_second)))


def _draw_count(rng: random.Random, mean: float, dispersion: float) -> float:
    """One over-dispersed count draw, as a gamma-mixed Poisson."""
    if mean <= 0.0:
        return 0.0
    if dispersion <= 1.0:
        return float(_poisson(rng, mean))
    shape = mean / (dispersion - 1.0)
    scale = dispersion - 1.0
    return float(_poisson(rng, rng.gammavariate(shape, scale)))


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth for small means, a normal approximation above the point it stops being cheap."""
    if mean <= 0.0:
        return 0
    if mean > 30.0:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    target = math.exp(-mean)
    product = 1.0
    count = 0
    while True:
        product *= rng.random()
        if product <= target:
            return count
        count += 1
        if count > 200:
            return count


def simulate_game(
    players: Sequence[PlayerGameState],
    *,
    iterations: int = DEFAULT_SIMULATIONS,
    seed: int = 20260805,
    expected_possessions: float | None = None,
    blowout_probability: float = 0.0,
) -> GameSimulation:
    """Simulate a whole game repeatedly, generating every player under one shared game state.

    The shared state is what makes the output correlations real. Each iteration draws the game's
    possession count and whether it turns into a blowout *once*, and every player in that
    iteration is then generated conditional on those draws. Nothing is correlated by assertion.
    """
    rng = random.Random(seed)
    base_possessions = expected_possessions or LEAGUE_POSSESSIONS
    samples: dict[str, list[tuple[float, float, float]]] = {
        player.player_id: [] for player in players
    }
    possession_total = 0.0

    for _ in range(max(1, iterations)):
        # ---- the shared game state, drawn once per iteration -------------------------
        possessions = max(50.0, rng.gauss(base_possessions, POSSESSION_SD))
        pace_factor = possessions / base_possessions
        is_blowout = rng.random() < blowout_probability
        possession_total += possessions

        for player in players:
            minutes = max(0.0, rng.gauss(player.expected_minutes, max(0.1, player.minutes_sd)))
            if is_blowout:
                # Starters lose minutes in a blowout and the bench gains them. Applying this
                # inside the shared iteration is what makes two starters covary downward.
                minutes = max(
                    0.0,
                    minutes - _BLOWOUT_MINUTES_PENALTY
                    if player.is_starter
                    else minutes + _BLOWOUT_MINUTES_PENALTY * 0.5,
                )

            # Pace lifts or suppresses every player's opportunity together, damped by how much
            # of a stat is actually pace-driven rather than role-driven.
            shared = 1.0 + (pace_factor - 1.0) * _PACE_RATE_SENSITIVITY

            drawn: list[float] = []
            for stat in _STATS:
                rate = float(player.rates.get(stat, 0.0))
                spread = float(player.rate_sd.get(stat, 0.0))
                if spread > 0.0:
                    rate = max(0.0, rng.gauss(rate, spread))
                drawn.append(_draw_count(rng, minutes * rate * shared, player.dispersion))
            samples[player.player_id].append((drawn[0], drawn[1], drawn[2]))

    return GameSimulation(
        iterations=max(1, iterations),
        players=tuple(
            JointDistribution(player_id=player.player_id, samples=tuple(samples[player.player_id]))
            for player in players
        ),
        mean_possessions=possession_total / max(1, iterations),
    )


def correlation_matrix(
    simulation: GameSimulation, *, stat: str = "points"
) -> dict[str, dict[str, float]]:
    """Player-to-player correlation for one stat, as a full symmetric matrix.

    The input entry pricing has always wanted and never had. ``wnba_sim.copula`` takes a scalar
    correlation from its caller; this is where a defensible number for that scalar comes from, and
    it is per pair rather than one value applied to every pair -- two starters on the same team
    are not correlated to the same degree as a starter and an opposing bench player.
    """
    ids = [player.player_id for player in simulation.players]
    lookup = simulation.by_player()
    matrix: dict[str, dict[str, float]] = {}
    for first in ids:
        row: dict[str, float] = {}
        for second in ids:
            row[second] = (
                1.0
                if first == second
                else _correlation(lookup[first].marginal(stat), lookup[second].marginal(stat))
            )
        matrix[first] = row
    return matrix
