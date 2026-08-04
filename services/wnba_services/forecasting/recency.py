"""One recency scheme, expressed as a half-life, fitted rather than guessed.

The pipeline previously carried three different decay schemes that nobody had reconciled:
linear ``1..n`` weights in the simulator, ``0.8 ** age`` in the player-state component, and two
separate smoothing constants inside the opportunity/conversion decomposition. None of them was
tuned, and because they disagreed, the same twenty games meant four different things depending
on which component was reading them.

A half-life is the honest way to say this. "Games from three weeks ago count half as much" is a
statement an analyst can argue with and a backtest can settle; ``alpha=0.25`` is neither.
:func:`fit_half_life` settles it, by scoring candidate half-lives on one-step-ahead prediction
over real history and returning the one that actually forecasts best.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "CANDIDATE_HALF_LIVES",
    "DEFAULT_HALF_LIFE",
    "MARKET_HALF_LIFE",
    "fit_half_life",
    "half_life_weights",
    "weighted_mean",
    "weighted_ratio",
]

# Measured in games, not days: a player's form travels with their appearances, and a team's
# schedule density is already handled by the rest adjustment rather than by decay.
DEFAULT_HALF_LIFE = 8.0

# Volume-driven markets carry more signal from further back, because minutes and role change
# more slowly than shooting touch does. Efficiency-driven markets forget faster.
MARKET_HALF_LIFE: dict[str, float] = {
    "points": 7.0,
    "rebounds": 10.0,
    "assists": 10.0,
    "three_pointers": 6.0,
    "steals": 12.0,
    "blocks": 12.0,
    "turnovers": 10.0,
    "points_rebounds_assists": 9.0,
    "points_rebounds": 9.0,
    "points_assists": 8.0,
    "rebounds_assists": 10.0,
    "steals_blocks": 12.0,
}

CANDIDATE_HALF_LIVES: tuple[float, ...] = (3.0, 5.0, 7.0, 10.0, 14.0, 20.0, 30.0)


def half_life_weights(count: int, half_life: float) -> tuple[float, ...]:
    """Weights for ``count`` observations ordered oldest first; the newest weighs 1.0.

    A non-positive half-life would mean "only the last game exists", which is a degenerate
    model rather than an aggressive one, so it is rejected instead of clamped.
    """
    if count <= 0:
        return ()
    if half_life <= 0.0:
        raise ValueError("half_life must be positive")
    decay = 0.5 ** (1.0 / half_life)
    return tuple(decay ** (count - 1 - index) for index in range(count))


def weighted_mean(values: Sequence[float], half_life: float) -> float:
    """Recency-weighted mean of a series ordered oldest first."""
    if not values:
        raise ValueError("cannot take a weighted mean of an empty series")
    weights = half_life_weights(len(values), half_life)
    total = math.fsum(weights)
    return math.fsum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def weighted_ratio(
    numerators: Sequence[float], denominators: Sequence[float], half_life: float
) -> float:
    """Recency-weighted ratio of two series ordered oldest first.

    Weighting the ratio of the sums rather than the mean of the ratios, because a game with two
    minutes should contribute two minutes of evidence about a per-minute rate -- not the same
    vote as a thirty-six-minute game with a hundredth of the precision.
    """
    if len(numerators) != len(denominators):
        raise ValueError("numerator and denominator series must be the same length")
    if not numerators:
        return 0.0
    weights = half_life_weights(len(numerators), half_life)
    weighted_numerator = math.fsum(
        value * weight for value, weight in zip(numerators, weights, strict=True)
    )
    weighted_denominator = math.fsum(
        value * weight for value, weight in zip(denominators, weights, strict=True)
    )
    if weighted_denominator <= 0.0:
        return 0.0
    return weighted_numerator / weighted_denominator


def fit_half_life(
    series: Sequence[Sequence[float]],
    *,
    candidates: Sequence[float] = CANDIDATE_HALF_LIVES,
    minimum_history: int = 5,
    default: float = DEFAULT_HALF_LIFE,
) -> float:
    """Pick the half-life that best predicts the next observation, across many series.

    Strictly one-step-ahead: for each prefix, the weighted mean of games ``0..k-1`` is scored
    against game ``k``, which never sees its own outcome. Ties and empty evidence fall back to
    the supplied default rather than to whichever candidate happens to sort first.
    """
    if not candidates:
        raise ValueError("need at least one candidate half-life")
    best_half_life = default
    best_error = math.inf
    for half_life in candidates:
        errors: list[float] = []
        for values in series:
            for split in range(minimum_history, len(values)):
                prediction = weighted_mean(values[:split], half_life)
                errors.append((prediction - values[split]) ** 2)
        if not errors:
            continue
        mean_error = math.fsum(errors) / len(errors)
        if mean_error < best_error:
            best_error, best_half_life = mean_error, half_life
    return best_half_life
