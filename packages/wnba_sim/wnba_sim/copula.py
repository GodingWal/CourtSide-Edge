"""Correlated leg outcomes: the joint correct-count distribution.

Everything in :mod:`wnba_marketmath` takes the correct-count PMF as an input and refuses to
guess it. This module is what produces it honestly.

**The one-factor model.** Legs on the same game are not independent, and the reason is
physical rather than statistical: a single latent game state drives all of them at once. A
blowout pulls every starter's minutes down together. A track meet lifts points, rebounds and
assists together. So each leg's latent outcome is

    z_i = sqrt(rho) * f + sqrt(1 - rho) * e_i

with ``f`` the shared game factor and ``e_i`` the player-specific residual, both standard
normal. Leg ``i`` is correct when ``z_i <= Phi^-1(p_i)``, which reproduces the marginal
probability ``p_i`` exactly for any ``rho`` while inducing pairwise correlation ``rho`` between
legs. That last property is what makes the model safe: turning correlation up cannot silently
corrupt the marginals the forecasting models worked so hard to calibrate.

``rho`` is a real parameter to be estimated from settled episodes, not a slider to be nudged
until the EV looks appealing. Until it is estimated, treat any entry whose sign flips across
plausible ``rho`` as unpriced rather than as an opportunity.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import NormalDist

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "DEFAULT_SIMULATIONS",
    "correct_count_pmf",
    "correlation_from_matrix",
    "effective_leg_correlation",
]

DEFAULT_SIMULATIONS = 100_000
_STANDARD_NORMAL = NormalDist()


def _thresholds(leg_probabilities: Sequence[float]) -> NDArray[np.float64]:
    """Latent cut-points reproducing each marginal probability exactly."""
    cuts = []
    for p in leg_probabilities:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"leg probability {p} outside [0, 1]")
        if p <= 0.0:
            cuts.append(-np.inf)
        elif p >= 1.0:
            cuts.append(np.inf)
        else:
            cuts.append(_STANDARD_NORMAL.inv_cdf(p))
    return np.asarray(cuts, dtype=np.float64)


def _pmf_from_counts(correct_per_sim: NDArray[np.int64], leg_count: int) -> tuple[float, ...]:
    counts = np.bincount(correct_per_sim, minlength=leg_count + 1)
    return tuple((counts / counts.sum()).tolist())


def correct_count_pmf(
    leg_probabilities: Sequence[float],
    *,
    correlation: float = 0.0,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = 0,
) -> tuple[float, ...]:
    """PMF over the number of correct legs under a one-factor correlation model.

    ``seed`` is required-by-default rather than optional: an unreproducible simulation cannot
    be audited, and an unauditable forecast cannot be learned from. The seed belongs in the
    ``ModelRun`` record alongside the result.

    At ``correlation=0`` this converges to the analytic Poisson-binomial, which is exactly how
    it is tested -- a simulator that cannot reproduce the closed form where one exists should
    not be trusted where one does not.
    """
    if not leg_probabilities:
        raise ValueError("an entry needs at least one leg")
    if not -1.0 < correlation < 1.0:
        raise ValueError("correlation must lie strictly within (-1, 1)")
    leg_count = len(leg_probabilities)
    if correlation < 0.0 and leg_count > 2:
        # A single shared factor cannot make three or more legs mutually negatively
        # correlated; the implied matrix is not positive semi-definite. Fail rather than
        # silently return a distribution that no joint outcome could produce.
        raise ValueError(
            "a one-factor model cannot represent negative correlation across more than two "
            "legs; supply an explicit correlation matrix instead"
        )

    rng = np.random.default_rng(seed)
    cuts = _thresholds(leg_probabilities)

    shared_weight = np.sqrt(abs(correlation))
    residual_weight = np.sqrt(1.0 - abs(correlation))
    shared = rng.standard_normal((simulations, 1))
    residual = rng.standard_normal((simulations, leg_count))

    if correlation >= 0.0:
        latent = shared_weight * shared + residual_weight * residual
    else:
        # Two legs only: flip the shared factor's sign on the second leg.
        signs = np.array([[1.0, -1.0]])
        latent = shared_weight * shared * signs + residual_weight * residual

    correct = (latent <= cuts).sum(axis=1).astype(np.int64)
    return _pmf_from_counts(correct, leg_count)


def correlation_from_matrix(
    leg_probabilities: Sequence[float],
    correlation_matrix: Sequence[Sequence[float]],
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = 0,
) -> tuple[float, ...]:
    """PMF under an arbitrary latent correlation matrix, via Cholesky.

    Use when legs span several games, or when pair-specific structure is known -- a player's
    points and rebounds correlate far more tightly with each other than either does with a
    teammate's assists.
    """
    leg_count = len(leg_probabilities)
    matrix = np.asarray(correlation_matrix, dtype=np.float64)
    if matrix.shape != (leg_count, leg_count):
        raise ValueError(f"correlation matrix must be {leg_count}x{leg_count}")
    if not np.allclose(matrix, matrix.T):
        raise ValueError("correlation matrix must be symmetric")
    if not np.allclose(np.diag(matrix), 1.0):
        raise ValueError("correlation matrix must have unit diagonal")

    try:
        factor = np.linalg.cholesky(matrix)
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "correlation matrix is not positive semi-definite: it describes a joint "
            "distribution that cannot exist"
        ) from exc

    rng = np.random.default_rng(seed)
    cuts = _thresholds(leg_probabilities)
    latent = rng.standard_normal((simulations, leg_count)) @ factor.T
    correct = (latent <= cuts).sum(axis=1).astype(np.int64)
    return _pmf_from_counts(correct, leg_count)


def effective_leg_correlation(correct_count_pmf_values: Sequence[float]) -> float:
    """Recover the average pairwise correlation of the **binary outcomes**.

    This deliberately does not return the ``correlation`` argument you passed to
    :func:`correct_count_pmf`, and the gap is not an error in either function. That argument is
    the *latent* (tetrachoric) correlation between the underlying normals; this is the *Pearson*
    correlation between the 0/1 leg results. Dichotomising a correlated normal always loses
    dependence, so the outcome correlation is strictly the smaller of the two -- a latent 0.40
    on two 60% legs realises as roughly 0.26.

    Expect them to differ, and compare like with like: use this to check two PMFs against each
    other, never to check a PMF against the parameter that produced it.
    """
    n = len(correct_count_pmf_values) - 1
    if n < 2:
        raise ValueError("pairwise correlation needs at least two legs")
    ks = np.arange(n + 1, dtype=np.float64)
    probabilities = np.asarray(correct_count_pmf_values, dtype=np.float64)

    mean = float((ks * probabilities).sum())
    variance = float((ks**2 * probabilities).sum() - mean**2)
    p_bar = mean / n
    independent_variance = n * p_bar * (1.0 - p_bar)
    if independent_variance <= 0.0:
        return 0.0
    # Var(sum) = n*p*(1-p) * (1 + (n-1)*rho) for exchangeable legs.
    return float((variance / independent_variance - 1.0) / (n - 1))
