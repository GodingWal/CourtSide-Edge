from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from wnba_services.learning_loop.trust import (
    FeatureObservation,
    adaptive_conformal_band,
    feature_ablation,
    fit_selective_policy,
    fit_source_reliability,
    paired_feature_ablation,
    risk_coverage_curve,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _observations(count: int = 400) -> list[FeatureObservation]:
    rows = []
    for index in range(count):
        outcome = index % 2
        strong = index % 4 != 0
        probability = 0.82 if outcome and strong else 0.18 if not outcome and strong else 0.5
        confidence = 0.9 if strong else 0.2
        rows.append(
            FeatureObservation(probability, outcome, confidence, NOW + timedelta(days=index))
        )
    return rows


def test_risk_coverage_scores_high_confidence_subset_first() -> None:
    curve = risk_coverage_curve(_observations(), coverages=(0.25, 1.0))
    assert curve[0].coverage == 0.25
    assert curve[0].log_loss < curve[1].log_loss
    assert curve[0].minimum_confidence == 0.9


def test_selective_policy_uses_future_validation_and_finds_safe_coverage() -> None:
    policy = fit_selective_policy(
        _observations(), maximum_log_loss=0.7, maximum_calibration_error=0.1
    )
    assert policy.is_fitted
    assert policy.coverage >= 0.5
    assert policy.accepts(0.95)
    assert not policy.accepts(0.1)


def test_selective_policy_fails_closed_when_thin() -> None:
    policy = fit_selective_policy(_observations(20))
    assert not policy.is_fitted
    assert policy.coverage == 0.0


def test_adaptive_conformal_uses_segment_when_supported() -> None:
    residuals = {"all": [5.0] * 100, "prop:points": [1.0] * 50}
    band = adaptive_conformal_band(20.0, residuals, segment="prop:points")
    assert not band.used_fallback
    assert band.lower == 19.0
    assert band.upper == 21.0
    assert band.empirical_coverage == 1.0


def test_adaptive_conformal_marks_pooled_fallback() -> None:
    band = adaptive_conformal_band(20.0, {"all": [2.0] * 80, "thin": [0.1]}, segment="thin")
    assert band.used_fallback
    assert band.radius == 2.0


def test_conformal_coverage_is_measured_on_later_residuals() -> None:
    band = adaptive_conformal_band(
        20.0,
        {"all": [1.0] * 70 + [8.0] * 30},
        segment="all",
    )
    assert band.radius == 1.0
    assert band.empirical_coverage == 0.0


def test_source_reliability_rewards_fresh_accurate_sources() -> None:
    fits = fit_source_reliability(
        [("accurate", "points", 0.02, True)] * 100 + [("stale", "points", 0.40, False)] * 100
    )
    by_source = {fit.source: fit for fit in fits}
    assert by_source["accurate"].weight > by_source["stale"].weight
    assert by_source["accurate"].median_absolute_error == 0.02


def test_source_reliability_is_separate_by_market() -> None:
    fits = fit_source_reliability(
        [("operator", "points", 0.02, True)] * 60 + [("operator", "steals", 0.30, True)] * 60
    )
    by_market = {fit.prop_type: fit for fit in fits}
    assert by_market["points"].weight > by_market["steals"].weight


def test_feature_ablation_is_paired_and_multiple_comparison_adjusted() -> None:
    results = feature_ablation(
        [0.3] * 100,
        {"minutes": [0.5] * 100, "market": [0.3] * 100},
    )
    by_name = {result.feature_name: result for result in results}
    assert by_name["minutes"].verdict == "helpful"
    assert by_name["market"].verdict == "inconclusive"
    assert by_name["minutes"].adjusted_alpha == pytest.approx(0.025)


def test_ablation_refuses_unpaired_comparison() -> None:
    with pytest.raises(ValueError, match="not paired"):
        feature_ablation([0.3, 0.4], {"minutes": [0.5]})


def test_paired_ablation_uses_each_components_available_episodes() -> None:
    results = paired_feature_ablation(
        {
            "always": [(0.3, 0.5), (0.4, 0.6)],
            "sometimes": [(0.3, 0.7)],
        }
    )
    by_name = {result.feature_name: result for result in results}
    assert by_name["always"].sample_size == 2
    assert by_name["sometimes"].sample_size == 1
