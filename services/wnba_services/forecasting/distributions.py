"""Count distributions that admit basketball is not a Poisson process.

Three modelling mistakes were baked into the original ensemble, and all three pushed
probabilities the same direction -- toward false confidence on exactly the tail lines a
forecast would want to act on. This module fixes them in one place so every component
inherits the fix.

**Points do not arrive one at a time.** A Poisson variable has variance equal to its mean.
Points come in ones, twos and threes, so a scorer averaging 16 has a realised variance nearer
35 than 16. Modelling that with a Poisson understates the spread by more than half, which
inflates ``P(over)`` for a line below the mean and deflates it above. :func:`count_pmf` takes
an explicit dispersion -- variance divided by mean -- and uses a negative binomial whenever
that exceeds one. :func:`estimate_dispersion` measures it from the player's own history and
shrinks toward a market-level prior, so it is data rather than a constant someone guessed.

**Averaging PMFs is not the same as averaging forecasts.** A weighted arithmetic mean of
component PMFs is a *mixture*: its variance is the mean of the component variances plus the
variance of the component means. Components that disagree therefore produce a blend more
dispersed than any of them individually, and the old pipeline then charged that dispersion
against its own confidence score -- paying twice for one disagreement. :func:`log_pool` takes
the weighted geometric mean instead, which keeps the blend as sharp as its members and is the
standard external-Bayesian answer. :func:`linear_pool` is kept for comparison, because a claim
that one pools better than the other should be measured rather than asserted.

**A sum of correlated counts is not a count with the summed rate.** Points, rebounds and
assists all rise when a player stays on the floor, so a points+rebounds+assists prop has a
variance strictly greater than the sum of its parts. Convolving independent marginals -- or
worse, Poisson-ising the summed rate, which is what the pipeline used to do -- understates the
tails a second time on precisely the markets with the widest lines.
:func:`correlated_sum_moments` propagates the covariance explicitly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

__all__ = [
    "LEAGUE_DISPERSION",
    "MAX_OUTCOME",
    "correlated_sum_moments",
    "count_pmf",
    "estimate_correlation",
    "estimate_dispersion",
    "line_probabilities",
    "linear_pool",
    "log_pool",
    "negative_binomial_pmf",
    "pmf_mean",
    "pmf_median",
    "pmf_stddev",
    "poisson_pmf",
]

MAX_OUTCOME = 200

# Variance-to-mean ratios observed for WNBA box-score counts. Points disperse hardest because
# a possession yields 1, 2 or 3; rebounds and assists arrive in units of one and sit close to
# Poisson. These are *priors* -- `estimate_dispersion` shrinks the player's own measurement
# toward them rather than adopting them outright.
LEAGUE_DISPERSION: dict[str, float] = {
    "points": 2.30,
    "rebounds": 1.25,
    "assists": 1.20,
    "three_pointers": 1.15,
    "steals": 1.10,
    "blocks": 1.15,
    "turnovers": 1.10,
    "points_rebounds_assists": 2.20,
    "points_rebounds": 2.20,
    "points_assists": 2.20,
    "rebounds_assists": 1.30,
    "steals_blocks": 1.15,
}
DEFAULT_DISPERSION = 1.35

# Games of the player's own history required before their measured dispersion outweighs the
# market prior. Dispersion is a second-moment estimate, so it converges far more slowly than a
# mean and deserves a heavier prior than a rate would.
_DISPERSION_PRIOR_GAMES = 15.0
_MAX_DISPERSION = 6.0
_LOG_FLOOR = 1e-12


def poisson_pmf(mean: float, length: int) -> tuple[float, ...]:
    """Poisson PMF truncated to ``length`` outcomes and renormalised."""
    mean = max(0.01, min(150.0, mean))
    values = [math.exp(-mean)]
    for outcome in range(1, length):
        values.append(values[-1] * mean / outcome)
    total = math.fsum(values)
    return tuple(value / total for value in values)


def negative_binomial_pmf(mean: float, dispersion: float, length: int) -> tuple[float, ...]:
    """Negative binomial with the given mean and variance ``dispersion * mean``.

    Parameterised by the variance-to-mean ratio rather than by ``(r, p)`` because that ratio is
    the thing we can actually measure from box scores, and because it degrades gracefully: as
    dispersion approaches one the distribution approaches the Poisson, so a market with no
    excess variance costs nothing to model this way.
    """
    mean = max(0.01, min(150.0, mean))
    dispersion = max(1.0, min(_MAX_DISPERSION, dispersion))
    if dispersion - 1.0 < 1e-6:
        return poisson_pmf(mean, length)

    # var = dispersion * mean  =>  p = 1 / dispersion, r = mean / (dispersion - 1)
    success = 1.0 / dispersion
    shape = mean / (dispersion - 1.0)
    values = [success**shape]
    for outcome in range(1, length):
        values.append(values[-1] * (outcome + shape - 1.0) / outcome * (1.0 - success))
    total = math.fsum(values)
    if total <= 0.0:
        return poisson_pmf(mean, length)
    return tuple(value / total for value in values)


def count_pmf(mean: float, dispersion: float, length: int) -> tuple[float, ...]:
    """The count distribution for a stat with this mean and this variance-to-mean ratio."""
    return negative_binomial_pmf(mean, dispersion, length)


def estimate_dispersion(
    values: Sequence[float],
    *,
    prior: float,
    prior_games: float = _DISPERSION_PRIOR_GAMES,
) -> float:
    """Variance-to-mean ratio from a player's own outcomes, shrunk toward a market prior.

    Under-dispersion is clamped away rather than trusted. A player whose last twelve games
    happened to land within a two-point band has not discovered a way to make basketball less
    random; they have a small sample. Believing them would make the forecast sharper than the
    evidence supports, which is the one error this whole module exists to stop.
    """
    usable = [value for value in values if value >= 0.0]
    if len(usable) < 3:
        return prior
    mean = math.fsum(usable) / len(usable)
    if mean <= 0.0:
        return prior
    variance = math.fsum((value - mean) ** 2 for value in usable) / (len(usable) - 1)
    observed = max(1.0, variance / mean)
    weight = len(usable) / (len(usable) + prior_games)
    return max(1.0, min(_MAX_DISPERSION, weight * observed + (1.0 - weight) * prior))


def estimate_correlation(first: Sequence[float], second: Sequence[float]) -> float:
    """Pearson correlation between two per-game stat series, or 0.0 when undefined."""
    if len(first) != len(second) or len(first) < 3:
        return 0.0
    mean_first = math.fsum(first) / len(first)
    mean_second = math.fsum(second) / len(second)
    covariance = math.fsum(
        (a - mean_first) * (b - mean_second) for a, b in zip(first, second, strict=True)
    )
    var_first = math.fsum((a - mean_first) ** 2 for a in first)
    var_second = math.fsum((b - mean_second) ** 2 for b in second)
    if var_first <= 0.0 or var_second <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, covariance / math.sqrt(var_first * var_second)))


def correlated_sum_moments(
    means: Sequence[float],
    variances: Sequence[float],
    correlations: Sequence[Sequence[float]],
) -> tuple[float, float]:
    """Mean and variance of a sum of correlated counts.

    Returned as moments rather than as a distribution so the caller can moment-match whatever
    family it likes. Matching a negative binomial to these two moments is exact in mean and
    variance and wrong only in the third moment onward -- a far smaller error than pretending
    a combo prop is Poisson with the summed rate, which is wrong in the second.
    """
    if len(means) != len(variances):
        raise ValueError("means and variances must describe the same components")
    total_mean = math.fsum(means)
    total_variance = math.fsum(variances)
    for i in range(len(means)):
        for j in range(i + 1, len(means)):
            total_variance += (
                2.0 * correlations[i][j] * math.sqrt(max(0.0, variances[i] * variances[j]))
            )
    return total_mean, max(0.0, total_variance)


def linear_pool(
    pmfs: Sequence[Sequence[float]], weights: Sequence[float], length: int
) -> tuple[float, ...]:
    """Weighted arithmetic mean of PMFs -- a mixture. Kept as the comparison baseline."""
    blended = [
        math.fsum(
            weight * (pmf[value] if value < len(pmf) else 0.0)
            for pmf, weight in zip(pmfs, weights, strict=True)
        )
        for value in range(length)
    ]
    total = math.fsum(blended)
    if total <= 0.0:
        return tuple([1.0 / length] * length)
    return tuple(value / total for value in blended)


def log_pool(
    pmfs: Sequence[Sequence[float]], weights: Sequence[float], length: int
) -> tuple[float, ...]:
    """Weighted geometric mean of PMFs, renormalised -- the log-linear opinion pool.

    Sharper than the mixture and externally Bayesian: pooling then conditioning gives the same
    answer as conditioning then pooling, which the linear pool does not guarantee. The cost is
    that a component assigning near-zero probability to an outcome can veto it, so the floor
    below is load-bearing rather than defensive tidying.
    """
    weight_total = math.fsum(weights)
    if weight_total <= 0.0:
        return tuple([1.0 / length] * length)
    normalised = [weight / weight_total for weight in weights]

    log_values: list[float] = []
    for value in range(length):
        log_values.append(
            math.fsum(
                weight * math.log(max(_LOG_FLOOR, pmf[value] if value < len(pmf) else 0.0))
                for pmf, weight in zip(pmfs, normalised, strict=True)
            )
        )
    peak = max(log_values)
    unnormalised = [math.exp(value - peak) for value in log_values]
    total = math.fsum(unnormalised)
    if total <= 0.0:
        return tuple([1.0 / length] * length)
    return tuple(value / total for value in unnormalised)


def line_probabilities(pmf: Sequence[float], line: Decimal) -> tuple[float, float, float]:
    """``(over, push, under)`` for a countable prop at this line."""
    over = math.fsum(probability for value, probability in enumerate(pmf) if Decimal(value) > line)
    push = pmf[int(line)] if line == line.to_integral_value() and 0 <= int(line) < len(pmf) else 0.0
    return over, push, max(0.0, 1.0 - over - push)


def pmf_mean(pmf: Sequence[float]) -> float:
    return math.fsum(value * probability for value, probability in enumerate(pmf))


def pmf_stddev(pmf: Sequence[float]) -> float:
    mean = pmf_mean(pmf)
    variance = math.fsum(probability * (value - mean) ** 2 for value, probability in enumerate(pmf))
    return math.sqrt(max(0.0, variance))


def pmf_median(pmf: Sequence[float]) -> int:
    cumulative = 0.0
    for value, probability in enumerate(pmf):
        cumulative += probability
        if cumulative >= 0.5:
            return value
    return len(pmf) - 1
