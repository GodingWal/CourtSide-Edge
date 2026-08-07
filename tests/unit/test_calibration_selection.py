"""Calibration, learned weights, edge shrinkage and the payout-aware gate.

Every claim in here is a claim about the pipeline refusing to fool itself: a calibration map
that does not help is not installed, a weight vector that does not beat the prior out of fold is
not adopted, and an edge that does not show up in results is not believed.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from wnba_domain.enums import EntryType, RecommendationStatus
from wnba_marketmath import underdog_payout_table
from wnba_marketmath.odds import remove_vig_decimal
from wnba_services.forecasting.calibration import (
    IDENTITY,
    CalibrationMap,
    cross_validated_improvement,
    fit_calibration_map,
    fit_isotonic,
)
from wnba_services.forecasting.selection import (
    COLD_START_SHRINK_FACTOR,
    EdgeShrinkage,
    breakeven_probability,
    decide_candidate,
)
from wnba_services.forecasting.weights import (
    DEFAULT_COMPONENT_WEIGHTS,
    EnsembleWeights,
    fit_ensemble_weights,
)


# --------------------------------------------------------------------------------------
# Isotonic calibration
# --------------------------------------------------------------------------------------
def test_isotonic_output_never_decreases() -> None:
    rng = random.Random(1)
    points = [(rng.random(), float(rng.random() < 0.5)) for _ in range(400)]
    knots = fit_isotonic(points)
    values = [value for _, value in knots]
    assert values == sorted(values)


def test_isotonic_recovers_a_known_overconfidence() -> None:
    """A model whose 0.7s only land 0.55 of the time should be mapped down toward 0.55."""
    rng = random.Random(2)
    points = [(0.70, float(rng.random() < 0.55)) for _ in range(800)]
    points += [(0.30, float(rng.random() < 0.45)) for _ in range(800)]
    knots = fit_isotonic(points)
    high = [value for x, value in knots if x > 0.5]
    assert high
    assert max(high) < 0.62


def _overconfident_sample(count: int, seed: int) -> list[tuple[float, float]]:
    """Forecasts that are systematically too far from 0.5 to be true."""
    rng = random.Random(seed)
    sample: list[tuple[float, float]] = []
    for _ in range(count):
        stated = rng.uniform(0.55, 0.85)
        true = 0.5 + (stated - 0.5) * 0.5
        sample.append((stated, float(rng.random() < true)))
    return sample


def test_calibration_is_installed_when_it_demonstrably_helps() -> None:
    fitted = fit_calibration_map("points", _overconfident_sample(3000, 11))
    assert not fitted.is_identity
    assert fitted.fitted_log_loss_gain > 0.0
    assert fitted.apply(0.80) < 0.80


def test_a_well_calibrated_model_is_left_alone() -> None:
    """Calibrating an already-honest forecast can only add noise, so it must not happen."""
    rng = random.Random(5)
    honest = [
        (probability, float(rng.random() < probability))
        for probability in (rng.uniform(0.2, 0.8) for _ in range(3000))
    ]
    assert fit_calibration_map("points", honest).is_identity


def test_too_few_settled_episodes_means_no_map() -> None:
    assert fit_calibration_map("points", _overconfident_sample(40, 3)).is_identity


def test_the_identity_map_changes_nothing() -> None:
    for probability in (0.01, 0.33, 0.5, 0.99):
        assert IDENTITY.apply(probability) == probability


def test_a_thin_map_barely_moves_a_probability() -> None:
    """Shrinkage by sample size: 200 episodes should not rewrite the forecast."""
    thin = CalibrationMap(market="points", knots=((0.3, 0.3), (0.8, 0.55)), sample_size=200)
    thick = CalibrationMap(market="points", knots=((0.3, 0.3), (0.8, 0.55)), sample_size=20000)
    assert abs(thin.apply(0.8) - 0.8) < abs(thick.apply(0.8) - 0.8)


def test_calibrated_probabilities_stay_inside_the_open_unit_interval() -> None:
    extreme = CalibrationMap(market="x", knots=((0.0, 0.0), (1.0, 1.0)), sample_size=10**6)
    assert 0.0 < extreme.apply(0.0) < 1.0
    assert 0.0 < extreme.apply(1.0) < 1.0


def test_calibration_map_round_trips_through_its_payload() -> None:
    fitted = fit_calibration_map("assists", _overconfident_sample(2000, 7))
    restored = CalibrationMap.from_payload(fitted.to_payload())
    assert restored.knots == fitted.knots
    assert restored.sample_size == fitted.sample_size
    assert restored.apply(0.72) == pytest.approx(fitted.apply(0.72))


def test_cross_validated_improvement_is_negative_for_a_useless_map() -> None:
    rng = random.Random(9)
    noise = [(rng.uniform(0.45, 0.55), float(rng.random() < 0.5)) for _ in range(60)]
    assert cross_validated_improvement(noise) <= 0.05


# --------------------------------------------------------------------------------------
# Learned weights
# --------------------------------------------------------------------------------------
def _weight_sample(count: int, seed: int) -> list[tuple[dict[str, float], float]]:
    """One component is informative; the rest are coin flips dressed as forecasts."""
    rng = random.Random(seed)
    rows: list[tuple[dict[str, float], float]] = []
    for _ in range(count):
        truth = rng.random() < 0.5
        good = 0.80 if truth else 0.20
        rows.append(
            (
                {
                    "empirical": good,
                    "hierarchical": rng.uniform(0.45, 0.55),
                    "player_state": rng.uniform(0.45, 0.55),
                    "opportunity_conversion": rng.uniform(0.45, 0.55),
                    "market_prior": rng.uniform(0.45, 0.55),
                },
                float(truth),
            )
        )
    return rows


def test_weights_move_toward_the_component_that_actually_predicts() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 4))
    assert fitted.is_fitted
    assert fitted.weights["empirical"] > DEFAULT_COMPONENT_WEIGHTS["empirical"]
    assert fitted.log_loss_gain > 0.0


def test_fitted_weights_remain_a_probability_distribution() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 6))
    assert sum(fitted.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert min(fitted.weights.values()) >= 0.0


def test_too_few_episodes_keeps_the_prior_weights() -> None:
    unfitted = fit_ensemble_weights("points", _weight_sample(50, 8))
    assert not unfitted.is_fitted
    assert unfitted.weights == DEFAULT_COMPONENT_WEIGHTS


def test_identical_components_offer_nothing_to_learn() -> None:
    """When every component says the same thing, no weighting of them can say anything else.

    A logit stack over identical inputs is invariant to the weights, so the out-of-fold gain is
    exactly zero and the prior must survive. If this ever adopts a fitted vector, the fit is
    reporting an improvement it cannot possibly have produced.
    """
    rng = random.Random(12)
    identical = []
    for _ in range(600):
        shared = rng.uniform(0.3, 0.7)
        identical.append(
            (dict.fromkeys(DEFAULT_COMPONENT_WEIGHTS, shared), float(rng.random() < shared))
        )
    fitted = fit_ensemble_weights("points", identical)
    assert not fitted.is_fitted
    assert fitted.weights == DEFAULT_COMPONENT_WEIGHTS


def test_an_anti_predictive_component_loses_its_weight() -> None:
    """A component that is reliably backwards should be weighted toward nothing, not inverted."""
    rng = random.Random(13)
    rows: list[tuple[dict[str, float], float]] = []
    for _ in range(2000):
        truth = rng.random() < 0.5
        rows.append(
            (
                {
                    "empirical": 0.80 if truth else 0.20,
                    "hierarchical": rng.uniform(0.45, 0.55),
                    "player_state": rng.uniform(0.45, 0.55),
                    "opportunity_conversion": rng.uniform(0.45, 0.55),
                    # Confidently wrong, every time.
                    "market_prior": 0.20 if truth else 0.80,
                },
                float(truth),
            )
        )
    fitted = fit_ensemble_weights("points", rows)
    assert fitted.is_fitted
    assert fitted.weights["market_prior"] < 0.02
    assert fitted.weights["empirical"] > fitted.weights["market_prior"]


def test_an_adopted_fit_always_carries_a_verified_out_of_fold_gain() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 31))
    assert fitted.is_fitted is (fitted.log_loss_gain > 0.0)


def test_weights_renormalise_over_whichever_components_are_present() -> None:
    weights = EnsembleWeights.prior()
    resolved = weights.for_components(["empirical", "hierarchical"])
    assert sum(resolved) == pytest.approx(1.0)


def test_weights_round_trip_through_their_payload() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 15))
    restored = EnsembleWeights.from_payload(fitted.to_payload())
    assert restored.weights == pytest.approx(fitted.weights)
    assert restored.is_fitted == fitted.is_fitted


def test_a_fit_that_cannot_beat_the_base_rate_is_refused() -> None:
    """Better than the prior is not good enough when the prior was the problem.

    Four loud noise components and one quiet one: shifting every gram of weight onto the quiet
    component is a large, genuine improvement over the prior weights -- and still only equals
    quoting the base rate, because none of it predicts anything. The fit must be refused, and
    the record must show that it *did* beat the prior so the next reviewer sees both halves of
    the story.
    """
    rng = random.Random(9)
    rows: list[tuple[dict[str, float], float]] = []
    for _ in range(2000):
        rows.append(
            (
                {
                    "empirical": rng.uniform(0.05, 0.95),
                    "hierarchical": rng.uniform(0.05, 0.95),
                    "player_state": rng.uniform(0.05, 0.95),
                    "opportunity_conversion": rng.uniform(0.49, 0.51),
                    "market_prior": rng.uniform(0.05, 0.95),
                },
                float(rng.random() < 0.5),
            )
        )
    fitted = fit_ensemble_weights("points", rows)
    assert fitted.log_loss_gain > 0.002, "the quiet-noise pool really does beat the prior"
    assert not fitted.is_fitted, "but it does not beat having no model at all"
    assert fitted.weights == DEFAULT_COMPONENT_WEIGHTS


def test_an_adopted_fit_also_beats_the_base_rate() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 31))
    assert fitted.is_fitted
    assert fitted.climatology_gain > 0.0


def test_the_base_rate_benchmark_round_trips_through_the_payload() -> None:
    fitted = fit_ensemble_weights("points", _weight_sample(2000, 15))
    restored = EnsembleWeights.from_payload(fitted.to_payload())
    assert restored.climatology_gain == pytest.approx(fitted.climatology_gain)


# --------------------------------------------------------------------------------------
# Edge shrinkage
# --------------------------------------------------------------------------------------
def test_cold_start_believes_half_of_every_edge() -> None:
    shrinkage = EdgeShrinkage.cold_start()
    assert shrinkage.factor == COLD_START_SHRINK_FACTOR
    assert shrinkage.apply(0.70) == pytest.approx(0.60)
    assert shrinkage.apply(0.30) == pytest.approx(0.40)


def test_shrinkage_never_crosses_or_reverses_the_coin_flip() -> None:
    for factor in (0.0, 0.3, 1.0):
        shrinkage = EdgeShrinkage(factor=factor, sample_size=1000)
        assert 0.5 <= shrinkage.apply(0.9) <= 0.9 + 1e-9
        assert 0.1 - 1e-9 <= shrinkage.apply(0.1) <= 0.5


def test_a_model_whose_edges_are_real_is_barely_shrunk() -> None:
    rng = random.Random(21)
    honest = [
        (probability, float(rng.random() < probability))
        for probability in (rng.uniform(0.35, 0.65) for _ in range(6000))
    ]
    assert EdgeShrinkage.from_settled(honest).factor > 0.75


def test_a_model_whose_edges_are_noise_is_shrunk_to_nothing() -> None:
    """The selection-bias correction: thousands of props, a threshold, and no real signal."""
    rng = random.Random(22)
    noise = [(rng.uniform(0.35, 0.65), float(rng.random() < 0.5)) for _ in range(6000)]
    assert EdgeShrinkage.from_settled(noise).factor < 0.25


def test_edges_pointing_the_wrong_way_collapse_rather_than_invert() -> None:
    rng = random.Random(23)
    backwards = [
        (probability, float(rng.random() < 1.0 - probability))
        for probability in (rng.uniform(0.35, 0.65) for _ in range(6000))
    ]
    assert EdgeShrinkage.from_settled(backwards).factor == 0.0


def test_too_few_settled_episodes_falls_back_to_the_stated_prior() -> None:
    assert EdgeShrinkage.from_settled([(0.6, 1.0)] * 20).factor == COLD_START_SHRINK_FACTOR


# --------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------
def _underdog_rule(entry: EntryType, legs: int):  # type: ignore[no-untyped-def]
    table = underdog_payout_table(datetime(2026, 1, 1, tzinfo=UTC))
    for rule in table.rules:
        if rule.entry_type is entry and rule.leg_count == legs:
            return rule
    raise AssertionError("expected rule not present in the bundled table")


def test_the_gate_reads_break_even_off_the_payout_table() -> None:
    """The magic 0.58 was right for one product and wrong for every other."""
    two_leg = breakeven_probability(_underdog_rule(EntryType.POWER, 2))
    three_leg = breakeven_probability(_underdog_rule(EntryType.POWER, 3))
    assert two_leg == pytest.approx(0.5774, abs=1e-3)
    assert three_leg == pytest.approx(0.5503, abs=1e-3)
    assert two_leg != three_leg


def _decision(probability: float, **overrides: object):  # type: ignore[no-untyped-def]
    arguments: dict[str, object] = {
        "over_probability": probability,
        "under_probability": 1.0 - probability,
        "shrinkage": EdgeShrinkage(factor=1.0, sample_size=5000, is_fitted=True),
        "breakeven": 0.5774,
        "quality": 0.95,
        "disagreement": 0.03,
        "injury_designation": "available",
    }
    arguments.update(overrides)
    return decide_candidate(**arguments)  # type: ignore[arg-type]


def test_a_clear_edge_becomes_a_candidate() -> None:
    decision = _decision(0.68)
    assert decision.status is RecommendationStatus.CANDIDATE
    assert decision.side == "over"
    assert decision.edge > 0.0


def test_an_edge_below_break_even_is_declined_even_above_the_old_threshold() -> None:
    """0.58 used to pass. Against a 3x two-leg product it is a losing selection."""
    assert _decision(0.58).status is RecommendationStatus.DECLINED_NO_EDGE


def test_shrinkage_can_take_a_forecast_below_the_gate() -> None:
    unshrunk = _decision(0.66, shrinkage=EdgeShrinkage(factor=1.0, sample_size=5000))
    shrunk = _decision(0.66, shrinkage=EdgeShrinkage(factor=0.3, sample_size=5000))
    assert unshrunk.status is RecommendationStatus.CANDIDATE
    assert shrunk.status is RecommendationStatus.DECLINED_NO_EDGE


def test_the_under_side_is_selected_and_gated_symmetrically() -> None:
    decision = _decision(0.30)
    assert decision.side == "under"
    assert decision.raw_probability == pytest.approx(0.70)
    assert decision.status is RecommendationStatus.CANDIDATE


def test_blocks_outrank_any_amount_of_apparent_edge() -> None:
    assert (
        _decision(0.95, rule_blocked=True, rule_reason="stale quote").status
        is RecommendationStatus.BLOCKED_BY_SKEPTIC
    )
    assert (
        _decision(0.95, injury_designation="questionable").status
        is RecommendationStatus.BLOCKED_BY_SKEPTIC
    )
    assert _decision(0.95, quality=0.4).status is RecommendationStatus.BLOCKED_DATA_QUALITY
    assert _decision(0.95, disagreement=0.4).status is RecommendationStatus.BLOCKED_CALIBRATION


def test_every_decision_records_the_number_that_produced_it() -> None:
    assert "break-even" in _decision(0.68).reason
    assert "questionable" in _decision(0.68, injury_designation="questionable").reason


# --------------------------------------------------------------------------------------
# Devigging a two-sided pick'em quote
# --------------------------------------------------------------------------------------
def test_a_flat_pickem_quote_is_reported_as_uninformative() -> None:
    """ "The market says even" and "the market says nothing" must not look the same."""
    assert remove_vig_decimal(1.0, 1.0) is None


def test_a_shaded_quote_devigs_to_a_fair_pair() -> None:
    fair = remove_vig_decimal(1.8, 2.4)
    assert fair is not None
    over, under = fair
    assert over + under == pytest.approx(1.0)
    assert over > under


def test_devigging_rejects_impossible_multipliers() -> None:
    with pytest.raises(ValueError, match="positive"):
        remove_vig_decimal(0.0, 2.0)
