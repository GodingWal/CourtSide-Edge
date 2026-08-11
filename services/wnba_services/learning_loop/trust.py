"""Reliability artifacts for selective prediction, uncertainty, sources and features.

Every fit in this module consumes observations already ordered in time.  It never randomises
future rows into an earlier training fold, and every result carries its sample size.  These
artifacts can make a forecast wider or withhold it; they cannot promote a model automatically.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from wnba_domain.decision import log_loss

__all__ = [
    "AblationResult",
    "ConformalBand",
    "FeatureObservation",
    "RiskCoveragePoint",
    "SelectivePolicy",
    "SourceReliabilityFit",
    "adaptive_conformal_band",
    "feature_ablation",
    "fit_selective_policy",
    "fit_source_reliability",
    "paired_feature_ablation",
    "risk_coverage_curve",
]


@dataclass(frozen=True)
class FeatureObservation:
    probability: float
    outcome: int
    confidence: float
    occurred_at: datetime
    prop_type: str = "all"
    role_state: str = "unknown"


@dataclass(frozen=True)
class RiskCoveragePoint:
    coverage: float
    selected: int
    log_loss: float
    brier: float
    calibration_error: float
    minimum_confidence: float

    def to_payload(self) -> dict[str, float | int]:
        return {
            "coverage": self.coverage,
            "selected": self.selected,
            "log_loss": self.log_loss,
            "brier": self.brier,
            "calibration_error": self.calibration_error,
            "minimum_confidence": self.minimum_confidence,
        }


@dataclass(frozen=True)
class SelectivePolicy:
    minimum_confidence: float
    coverage: float
    sample_size: int
    validation_log_loss: float
    is_fitted: bool
    reason: str

    def accepts(self, confidence: float) -> bool:
        return self.is_fitted and confidence >= self.minimum_confidence

    def to_payload(self) -> dict[str, Any]:
        return {
            "minimum_confidence": self.minimum_confidence,
            "coverage": self.coverage,
            "sample_size": self.sample_size,
            "validation_log_loss": self.validation_log_loss,
            "is_fitted": self.is_fitted,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConformalBand:
    segment: str
    lower: float
    upper: float
    radius: float
    sample_size: int
    target_coverage: float
    empirical_coverage: float
    used_fallback: bool

    def to_payload(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SourceReliabilityFit:
    source: str
    prop_type: str
    weight: float
    sample_size: int
    mean_absolute_error: float
    median_absolute_error: float
    freshness_rate: float

    def to_payload(self) -> dict[str, float | int | str]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class AblationResult:
    feature_name: str
    sample_size: int
    mean_log_loss_gain: float
    standard_error: float
    confidence_lower: float
    confidence_upper: float
    adjusted_alpha: float
    verdict: str

    def to_payload(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _metrics(rows: Sequence[FeatureObservation]) -> tuple[float, float, float]:
    losses = [log_loss(row.probability, row.outcome) for row in rows]
    briers = [(row.probability - row.outcome) ** 2 for row in rows]
    predicted = math.fsum(row.probability for row in rows) / len(rows)
    observed = math.fsum(row.outcome for row in rows) / len(rows)
    return (
        math.fsum(losses) / len(losses),
        math.fsum(briers) / len(briers),
        abs(predicted - observed),
    )


def risk_coverage_curve(
    observations: Sequence[FeatureObservation],
    *,
    coverages: Sequence[float] = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0),
) -> tuple[RiskCoveragePoint, ...]:
    """Score the most confident fraction at several coverage levels."""
    if not observations:
        return ()
    ranked = sorted(observations, key=lambda row: row.confidence, reverse=True)
    points: list[RiskCoveragePoint] = []
    for coverage in coverages:
        if not 0.0 < coverage <= 1.0:
            raise ValueError("coverage must lie in (0, 1]")
        count = max(1, math.ceil(len(ranked) * coverage))
        selected = ranked[:count]
        loss, brier, error = _metrics(selected)
        points.append(
            RiskCoveragePoint(
                coverage=count / len(ranked),
                selected=count,
                log_loss=loss,
                brier=brier,
                calibration_error=error,
                minimum_confidence=selected[-1].confidence,
            )
        )
    return tuple(points)


def fit_selective_policy(
    observations: Sequence[FeatureObservation],
    *,
    minimum_sample: int = 200,
    maximum_log_loss: float = 0.62,
    maximum_calibration_error: float = 0.05,
    minimum_coverage: float = 0.05,
) -> SelectivePolicy:
    """Choose the widest validation coverage that meets declared risk limits."""
    if len(observations) < minimum_sample:
        return SelectivePolicy(1.0, 0.0, len(observations), math.inf, False, "insufficient data")
    ordered = sorted(observations, key=lambda row: row.occurred_at)
    split = max(1, int(len(ordered) * 0.7))
    training = ordered[:split]
    validation = ordered[split:]
    if len(validation) < 30:
        return SelectivePolicy(1.0, 0.0, len(observations), math.inf, False, "thin validation")
    candidates = [
        point
        for point in risk_coverage_curve(training)
        if point.coverage >= minimum_coverage
        and point.log_loss <= maximum_log_loss
        and point.calibration_error <= maximum_calibration_error
    ]
    if not candidates:
        return SelectivePolicy(
            1.0,
            0.0,
            len(observations),
            _metrics(validation)[0],
            False,
            "no training coverage satisfies risk limits",
        )
    for candidate in sorted(candidates, key=lambda point: point.coverage, reverse=True):
        selected = [row for row in validation if row.confidence >= candidate.minimum_confidence]
        if len(selected) / len(validation) < minimum_coverage:
            continue
        loss, _, calibration_error = _metrics(selected)
        if loss <= maximum_log_loss and calibration_error <= maximum_calibration_error:
            return SelectivePolicy(
                candidate.minimum_confidence,
                len(selected) / len(validation),
                len(observations),
                loss,
                True,
                "training-selected threshold confirmed on future validation",
            )
    return SelectivePolicy(
        1.0,
        0.0,
        len(observations),
        _metrics(validation)[0],
        False,
        "training policy failed future validation",
    )


def _finite_sample_quantile(values: Sequence[float], coverage: float) -> float:
    ordered = sorted(values)
    rank = min(len(ordered) - 1, math.ceil((len(ordered) + 1) * coverage) - 1)
    return ordered[max(0, rank)]


def adaptive_conformal_band(
    prediction: float,
    residuals_by_segment: Mapping[str, Sequence[float]],
    *,
    segment: str,
    target_coverage: float = 0.90,
    minimum_segment_sample: int = 40,
    fallback_segment: str = "all",
) -> ConformalBand:
    """Chronological split-conformal interval with a pooled cold-start fallback.

    Input order is time order. The radius is selected on the earlier 70% and coverage is
    reported only on the later 30%; measuring both on the same residuals would guarantee a
    reassuring-looking coverage number by construction.
    """
    if not 0.0 < target_coverage < 1.0:
        raise ValueError("target coverage must lie in (0, 1)")
    selected = list(residuals_by_segment.get(segment, ()))
    fallback = len(selected) < minimum_segment_sample
    if fallback:
        selected = list(residuals_by_segment.get(fallback_segment, ()))
    if len(selected) < minimum_segment_sample:
        return ConformalBand(segment, prediction, prediction, 0.0, 0, target_coverage, 0.0, True)
    split = max(1, min(len(selected) - 1, int(len(selected) * 0.7)))
    calibration = [abs(value) for value in selected[:split]]
    validation = [abs(value) for value in selected[split:]]
    radius = _finite_sample_quantile(calibration, target_coverage)
    empirical = math.fsum(value <= radius for value in validation) / len(validation)
    return ConformalBand(
        segment,
        max(0.0, prediction - radius),
        prediction + radius,
        radius,
        len(selected),
        target_coverage,
        empirical,
        fallback,
    )


def fit_source_reliability(
    observations: Sequence[tuple[str, str, float, bool]], *, prior_error: float = 0.10
) -> tuple[SourceReliabilityFit, ...]:
    """Fit source/market weights from standardized closing error and freshness.

    Raw final-stat MAE is not comparable across points and steals. Callers provide error
    relative to the designated closing consensus, already standardized by the closing line.
    """
    grouped: dict[tuple[str, str], list[tuple[float, bool]]] = defaultdict(list)
    for source, prop_type, standardized_error, fresh in observations:
        grouped[(source, prop_type)].append((abs(standardized_error), fresh))
    fits: list[SourceReliabilityFit] = []
    for (source, prop_type), values in sorted(grouped.items()):
        errors = sorted(error for error, _ in values)
        mean = math.fsum(errors) / len(errors)
        middle = len(errors) // 2
        median = errors[middle] if len(errors) % 2 else (errors[middle - 1] + errors[middle]) / 2.0
        freshness = math.fsum(fresh for _, fresh in values) / len(values)
        evidence = len(values) / (len(values) + 50.0)
        relative = prior_error / max(prior_error, mean)
        weight = min(1.0, max(0.1, freshness * (evidence * relative + (1.0 - evidence) * 0.6)))
        fits.append(
            SourceReliabilityFit(source, prop_type, weight, len(values), mean, median, freshness)
        )
    return tuple(fits)


def paired_feature_ablation(
    paired_losses: Mapping[str, Sequence[tuple[float, float]]], *, alpha: float = 0.05
) -> tuple[AblationResult, ...]:
    """Compare each removable component on every episode where that component exists."""
    names = [name for name, pairs in paired_losses.items() if pairs]
    if not names:
        return ()
    adjusted = alpha / len(names)
    z = 3.0 if adjusted < 0.01 else 2.576 if adjusted < 0.025 else 1.96
    results: list[AblationResult] = []
    for name in sorted(names):
        pairs = paired_losses[name]
        gains = [ablated - champion for champion, ablated in pairs]
        mean = math.fsum(gains) / len(gains)
        variance = (
            math.fsum((value - mean) ** 2 for value in gains) / (len(gains) - 1)
            if len(gains) > 1
            else 0.0
        )
        standard_error = math.sqrt(variance / len(gains))
        lower, upper = mean - z * standard_error, mean + z * standard_error
        verdict = "helpful" if lower > 0.0 else "harmful" if upper < 0.0 else "inconclusive"
        results.append(
            AblationResult(name, len(gains), mean, standard_error, lower, upper, adjusted, verdict)
        )
    return tuple(results)


def feature_ablation(
    champion_losses: Sequence[float],
    ablated_losses: Mapping[str, Sequence[float]],
    *,
    alpha: float = 0.05,
) -> tuple[AblationResult, ...]:
    """Paired log-loss ablations with Bonferroni-adjusted normal intervals."""
    if not champion_losses or not ablated_losses:
        return ()
    adjusted = alpha / len(ablated_losses)
    # Conservative 99.7% normal bound for adjusted alpha below common feature-family sizes.
    z = 3.0 if adjusted < 0.01 else 2.576 if adjusted < 0.025 else 1.96
    results: list[AblationResult] = []
    for name, losses in sorted(ablated_losses.items()):
        if len(losses) != len(champion_losses):
            raise ValueError(f"ablation {name!r} is not paired with the champion")
        gains = [a - c for a, c in zip(losses, champion_losses, strict=True)]
        mean = math.fsum(gains) / len(gains)
        variance = (
            math.fsum((value - mean) ** 2 for value in gains) / (len(gains) - 1)
            if len(gains) > 1
            else 0.0
        )
        standard_error = math.sqrt(variance / len(gains))
        lower, upper = mean - z * standard_error, mean + z * standard_error
        verdict = "helpful" if lower > 0.0 else "harmful" if upper < 0.0 else "inconclusive"
        results.append(
            AblationResult(name, len(gains), mean, standard_error, lower, upper, adjusted, verdict)
        )
    return tuple(results)
