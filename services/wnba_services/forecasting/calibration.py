"""Turning measured miscalibration back into corrected probabilities.

The learning loop already computed expected calibration error per market, opened drift
incidents when it drifted, and wrote ``automatic_response = 'reduce_confidence'`` into the
incident row. Nothing read that column. The system could see that it was, say, systematically
returning 0.62 for events that happen 0.56 of the time -- and then went on returning 0.62.

This module closes that loop. It fits an isotonic map from settled episodes and applies it
between the ensemble and the decision gate, so a measured bias is corrected rather than merely
logged.

**Why isotonic and not Platt.** Miscalibration here is not a single logistic slope: the
components are pooled, clipped and multiplied by bounded adjustments, which distorts different
parts of the probability range differently. Isotonic regression assumes only monotonicity --
that a higher raw probability should never map to a lower calibrated one -- which is the one
property we genuinely believe.

**Why it is shrunk toward the identity.** Isotonic regression is non-parametric and will
happily carve a step function out of two hundred noisy points. :meth:`CalibrationMap.apply`
therefore blends toward the raw probability with a weight that grows with sample size, so a
thin market barely moves and a well-measured one moves most of the way.

**Why it must be cross-fitted before it is believed.** A map fitted on a sample will always
look good on that sample. :func:`cross_validated_improvement` refits per fold and scores on
held-out data, and :func:`fit_calibration_map` refuses to return a map that fails to beat the
identity out of sample. A calibration layer that makes things worse is worse than none.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from wnba_domain.decision import log_loss

__all__ = [
    "IDENTITY",
    "MINIMUM_CALIBRATION_SAMPLE",
    "CalibrationMap",
    "cross_validated_improvement",
    "fit_calibration_map",
    "fit_isotonic",
]

MINIMUM_CALIBRATION_SAMPLE = 150

# Sample size at which the fitted map is trusted halfway. Deliberately of the same order as the
# minimum sample: crossing the threshold should not flip the map on at full strength.
_SHRINK_PRIOR = 200.0
_PROBABILITY_FLOOR = 0.01
_FOLDS = 5

# Log-loss gains below this are indistinguishable from fold-assignment luck on the sample sizes
# this system will realistically see. Roughly a quarter of a percent of a typical loss.
_MINIMUM_GAIN = 0.002


def fit_isotonic(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Pool-adjacent-violators isotonic regression, returned as interpolation knots.

    Input is ``(raw_probability, outcome)`` with outcome in ``{0.0, 1.0}``. Output is a
    non-decreasing sequence of ``(x, y)`` knots at the centroid of each merged block.

    Observations sharing an ``x`` are pooled *before* the merge pass, which is not an
    optimisation. Feeding tied points in one at a time lets PAV emit several blocks at the same
    raw probability, and because the input is 0/1 the last of them is frequently a lone ``1``.
    The map would then claim that a raw 0.70 calibrates to a certainty -- an artefact of
    iteration order rather than anything the data said.
    """
    if not points:
        return ()

    pooled: dict[float, list[float]] = {}
    for x, y in points:
        entry = pooled.setdefault(x, [0.0, 0.0])
        entry[0] += y
        entry[1] += 1.0

    # Each block carries (weighted sum of x, sum of y, weight) so merging is a plain addition.
    blocks: list[list[float]] = []
    for x in sorted(pooled):
        total_y, weight = pooled[x]
        blocks.append([x * weight, total_y, weight])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            last = blocks.pop()
            previous = blocks.pop()
            blocks.append([previous[0] + last[0], previous[1] + last[1], previous[2] + last[2]])
    return tuple((block[0] / block[2], block[1] / block[2]) for block in blocks)


def _interpolate_knots(knots: tuple[tuple[float, float], ...], probability: float) -> float:
    """Piecewise-linear read-off of an isotonic fit, clipped at both ends.

    Interpolating between block centroids rather than returning the step function: the steps
    are an artefact of where the sample happened to fall, and a forecast that jumps 0.04 because
    a raw probability crossed 0.615 is reporting sampling noise as if it were a finding.
    """
    if not knots:
        return probability
    if len(knots) == 1:
        return knots[0][1]
    if probability <= knots[0][0]:
        return knots[0][1]
    if probability >= knots[-1][0]:
        return knots[-1][1]
    for index in range(1, len(knots)):
        left_x, left_y = knots[index - 1]
        right_x, right_y = knots[index]
        if probability <= right_x:
            if right_x - left_x <= 0.0:
                return right_y
            position = (probability - left_x) / (right_x - left_x)
            return left_y + position * (right_y - left_y)
    return knots[-1][1]


@dataclass(frozen=True)
class CalibrationMap:
    """A fitted, sample-shrunk mapping from raw to calibrated probability."""

    market: str
    knots: tuple[tuple[float, float], ...]
    sample_size: int
    fitted_log_loss_gain: float = 0.0
    """Mean log-loss reduction measured out of fold. Zero for the identity map."""

    @property
    def is_identity(self) -> bool:
        return not self.knots

    @property
    def shrinkage(self) -> float:
        """How far toward the fitted map a probability is actually moved, in ``[0, 1]``."""
        if self.is_identity:
            return 0.0
        return self.sample_size / (self.sample_size + _SHRINK_PRIOR)

    def apply(self, probability: float) -> float:
        """Calibrate one probability, shrunk toward the raw value by sample size."""
        raw = min(1.0, max(0.0, probability))
        if self.is_identity:
            return raw
        fitted = _interpolate_knots(self.knots, raw)
        blended = self.shrinkage * fitted + (1.0 - self.shrinkage) * raw
        return min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, blended))

    def to_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "knots": [[x, y] for x, y in self.knots],
            "sample_size": self.sample_size,
            "fitted_log_loss_gain": self.fitted_log_loss_gain,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CalibrationMap:
        raw_knots = payload.get("knots") or []
        return cls(
            market=str(payload.get("market", "unknown")),
            knots=tuple((float(knot[0]), float(knot[1])) for knot in raw_knots),
            sample_size=int(payload.get("sample_size", 0)),
            fitted_log_loss_gain=float(payload.get("fitted_log_loss_gain", 0.0)),
        )


IDENTITY = CalibrationMap(market="identity", knots=(), sample_size=0)


def _apply_knots(
    knots: tuple[tuple[float, float], ...], probability: float, weight: float
) -> float:
    mapped = _interpolate_knots(knots, min(1.0, max(0.0, probability)))
    blended = weight * mapped + (1.0 - weight) * probability
    return min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, blended))


def _fold_gains(points: Sequence[tuple[float, float]], folds: int) -> list[float]:
    """Per-fold out-of-fold log-loss reduction, in nats.

    Folds are interleaved by index rather than contiguous, so a season's worth of drift does
    not end up entirely inside one held-out block.
    """
    if len(points) < folds * 2:
        return []
    weight = len(points) / (len(points) + _SHRINK_PRIOR)
    gains: list[float] = []
    for fold in range(folds):
        holdout = [point for index, point in enumerate(points) if index % folds == fold]
        training = [point for index, point in enumerate(points) if index % folds != fold]
        if not holdout or len(training) < 2:
            continue
        knots = fit_isotonic(training)
        raw = math.fsum(
            log_loss(min(0.999999, max(1e-6, probability)), int(outcome))
            for probability, outcome in holdout
        )
        calibrated = math.fsum(
            log_loss(_apply_knots(knots, probability, weight), int(outcome))
            for probability, outcome in holdout
        )
        gains.append((raw - calibrated) / len(holdout))
    return gains


def cross_validated_improvement(
    points: Sequence[tuple[float, float]], *, folds: int = _FOLDS
) -> float:
    """Mean out-of-fold log-loss reduction from calibrating, in nats. Negative means it hurts."""
    gains = _fold_gains(points, folds)
    return math.fsum(gains) / len(gains) if gains else 0.0


def fit_calibration_map(
    market: str,
    points: Sequence[tuple[float, float]],
    *,
    minimum_sample: int = MINIMUM_CALIBRATION_SAMPLE,
) -> CalibrationMap:
    """Fit a calibration map, or return the identity when the evidence does not support one.

    Three ways to get the identity back, all deliberate: too few settled episodes, a mean gain
    too small to distinguish from sampling noise, or a gain that fails to appear in most folds.
    The last check matters because a single lucky fold can carry a positive average all by
    itself, and adopting a map on that basis is exactly the self-deception this module exists to
    prevent. None of the three is a failure state -- an uncalibrated forecast is the correct
    output when there is nothing trustworthy to correct.
    """
    usable = [
        (min(1.0, max(0.0, probability)), float(outcome))
        for probability, outcome in points
        if 0.0 <= probability <= 1.0 and outcome in (0.0, 1.0, 0, 1, True, False)
    ]
    if len(usable) < minimum_sample:
        return CalibrationMap(market=market, knots=(), sample_size=len(usable))

    gains = _fold_gains(usable, _FOLDS)
    gain = math.fsum(gains) / len(gains) if gains else 0.0
    consistent = sum(1 for value in gains if value > 0.0) >= math.ceil(len(gains) * 0.8)
    if gain <= _MINIMUM_GAIN or not consistent:
        return CalibrationMap(
            market=market, knots=(), sample_size=len(usable), fitted_log_loss_gain=gain
        )
    return CalibrationMap(
        market=market,
        knots=fit_isotonic(usable),
        sample_size=len(usable),
        fitted_log_loss_gain=gain,
    )
