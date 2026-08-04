"""Ensemble weights fitted from settled outcomes instead of chosen by hand.

The five component weights were constants in the source, and the model card said so plainly:
"Market prior weights are fixed, not learned from a locked holdout." Worse, the replay harness
used a *different* set of weights over a *different* set of components, so the backtest that
justified the production weights was measuring another model entirely.

This module fits them. The objective is mean log loss on settled episodes, over the simplex --
weights non-negative and summing to one, so the result is still a pooling of opinions rather
than an unconstrained regression that can extrapolate a component to three times its own
confidence.

**What is being fitted, precisely.** Pooling PMFs geometrically and then reading off ``P(over)``
is not identical to pooling the components' ``P(over)`` in logit space. The fit here is the
latter: it is convex, it has a closed-form gradient, and it agrees with the former to first
order around the pooled solution. That approximation is stated rather than hidden, and the
resulting weights are used by the PMF-level pool because a weight vector is a statement about
which components deserve trust -- not about which arithmetic reads them out.

**A fitted weight vector still has to earn its place.** :func:`fit_ensemble_weights` cross-fits
and returns the prior weights unless the fitted ones beat them on held-out log loss. Fitting
five free parameters on a few hundred correlated episodes is exactly the situation where an
in-sample improvement means nothing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wnba_domain.decision import log_loss

__all__ = [
    "DEFAULT_COMPONENT_WEIGHTS",
    "MINIMUM_WEIGHT_SAMPLE",
    "EnsembleWeights",
    "fit_ensemble_weights",
]

# The starting point and the fallback. Unchanged from the hand-set values so that "fitted" and
# "prior" remain distinguishable in the evaluation record.
DEFAULT_COMPONENT_WEIGHTS: dict[str, float] = {
    "empirical": 0.30,
    "hierarchical": 0.20,
    "player_state": 0.12,
    "opportunity_conversion": 0.18,
    "market_prior": 0.20,
}

MINIMUM_WEIGHT_SAMPLE = 300

_LEARNING_RATE = 0.5
_ITERATIONS = 400
_PRIOR_STRENGTH = 0.05
_LOGIT_CLAMP = 6.0
_FOLDS = 5

# Matches the calibration module: gains smaller than this are fold-assignment luck.
_MINIMUM_GAIN = 0.002


def _logit(probability: float) -> float:
    clamped = min(1.0 - 1e-6, max(1e-6, probability))
    return max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, math.log(clamped / (1.0 - clamped))))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _project_to_simplex(values: Sequence[float]) -> tuple[float, ...]:
    """Euclidean projection onto the probability simplex."""
    count = len(values)
    if count == 0:
        return ()
    ordered = sorted(values, reverse=True)
    cumulative = 0.0
    rho, theta = 0, 0.0
    for index, value in enumerate(ordered):
        cumulative += value
        candidate = (cumulative - 1.0) / (index + 1)
        if value - candidate > 0.0:
            rho, theta = index + 1, candidate
    if rho == 0:
        return tuple([1.0 / count] * count)
    return tuple(max(0.0, value - theta) for value in values)


@dataclass(frozen=True)
class EnsembleWeights:
    """A weight vector over named components, with the evidence that produced it."""

    market: str
    weights: dict[str, float]
    sample_size: int
    log_loss_gain: float = 0.0
    """Out-of-fold log-loss reduction against the prior weights. Zero when unfitted."""

    is_fitted: bool = False

    def for_components(self, names: Sequence[str]) -> tuple[float, ...]:
        """Weights aligned to ``names``, renormalised over whichever are present."""
        raw = [max(0.0, self.weights.get(name, 0.0)) for name in names]
        total = math.fsum(raw)
        if total <= 0.0:
            return tuple([1.0 / len(names)] * len(names)) if names else ()
        return tuple(value / total for value in raw)

    def to_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "weights": dict(self.weights),
            "sample_size": self.sample_size,
            "log_loss_gain": self.log_loss_gain,
            "is_fitted": self.is_fitted,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EnsembleWeights:
        raw = payload.get("weights") or {}
        return cls(
            market=str(payload.get("market", "unknown")),
            weights={str(name): float(value) for name, value in dict(raw).items()},
            sample_size=int(payload.get("sample_size", 0)),
            log_loss_gain=float(payload.get("log_loss_gain", 0.0)),
            is_fitted=bool(payload.get("is_fitted", False)),
        )

    @classmethod
    def prior(cls, market: str = "default") -> EnsembleWeights:
        return cls(market=market, weights=dict(DEFAULT_COMPONENT_WEIGHTS), sample_size=0)


def _mean_log_loss(
    names: Sequence[str],
    weights: Sequence[float],
    observations: Sequence[tuple[Mapping[str, float], float]],
) -> float:
    losses: list[float] = []
    for probabilities, outcome in observations:
        stacked = math.fsum(
            weight * _logit(probabilities.get(name, 0.5))
            for name, weight in zip(names, weights, strict=True)
        )
        losses.append(log_loss(_sigmoid(stacked), int(outcome)))
    return math.fsum(losses) / len(losses) if losses else math.inf


def _descend(
    names: Sequence[str],
    observations: Sequence[tuple[Mapping[str, float], float]],
    prior: Sequence[float],
) -> tuple[float, ...]:
    """Projected gradient descent on the simplex, pulled gently toward the prior weights."""
    current = list(prior)
    sample_count = len(observations)
    if sample_count == 0:
        return tuple(current)

    features = [
        [_logit(probabilities.get(name, 0.5)) for name in names]
        for probabilities, _ in observations
    ]
    outcomes = [float(outcome) for _, outcome in observations]

    for _ in range(_ITERATIONS):
        gradient = [0.0] * len(names)
        for row, outcome in zip(features, outcomes, strict=True):
            stacked = math.fsum(weight * value for weight, value in zip(current, row, strict=True))
            residual = _sigmoid(stacked) - outcome
            for index, value in enumerate(row):
                gradient[index] += residual * value / sample_count
        for index in range(len(names)):
            gradient[index] += _PRIOR_STRENGTH * (current[index] - prior[index])
        stepped = [
            weight - _LEARNING_RATE * slope for weight, slope in zip(current, gradient, strict=True)
        ]
        current = list(_project_to_simplex(stepped))
    return tuple(current)


def fit_ensemble_weights(
    market: str,
    observations: Sequence[tuple[Mapping[str, float], float]],
    *,
    component_names: Sequence[str] | None = None,
    prior: EnsembleWeights | None = None,
    minimum_sample: int = MINIMUM_WEIGHT_SAMPLE,
) -> EnsembleWeights:
    """Fit component weights for one market, or return the prior when they do not earn it.

    ``observations`` pairs a mapping of component name to that component's ``P(side)`` with the
    realised 0/1 outcome for the same side. Push and void episodes must already be excluded by
    the caller: they carry no information about forecast quality and including them would fit
    the weights to a coach's rotation decision.
    """
    baseline = prior or EnsembleWeights.prior(market)
    names = list(component_names or DEFAULT_COMPONENT_WEIGHTS)
    if len(observations) < minimum_sample or not names:
        return EnsembleWeights(
            market=market, weights=dict(baseline.weights), sample_size=len(observations)
        )

    prior_vector = list(baseline.for_components(names))
    gains: list[float] = []
    for fold in range(_FOLDS):
        holdout = [row for index, row in enumerate(observations) if index % _FOLDS == fold]
        training = [row for index, row in enumerate(observations) if index % _FOLDS != fold]
        if not holdout or len(training) < len(names) * 10:
            continue
        candidate = _descend(names, training, prior_vector)
        gains.append(
            _mean_log_loss(names, prior_vector, holdout) - _mean_log_loss(names, candidate, holdout)
        )

    if not gains:
        return EnsembleWeights(
            market=market, weights=dict(baseline.weights), sample_size=len(observations)
        )

    # A mean gain alone is not enough. Five free parameters fitted on a few hundred correlated
    # episodes will beat the prior on one fold by luck often enough to carry the average, so the
    # improvement must also show up in nearly every fold before it is believed.
    gain = math.fsum(gains) / len(gains)
    consistent = sum(1 for value in gains if value > 0.0) >= math.ceil(len(gains) * 0.8)
    if gain <= _MINIMUM_GAIN or not consistent:
        return EnsembleWeights(
            market=market,
            weights=dict(baseline.weights),
            sample_size=len(observations),
            log_loss_gain=gain,
        )

    final = _descend(names, observations, prior_vector)
    return EnsembleWeights(
        market=market,
        weights=dict(zip(names, final, strict=True)),
        sample_size=len(observations),
        log_loss_gain=gain,
        is_fitted=True,
    )
