"""Recency weighting, the two-regime minutes model, and the coupled rate sampler."""

from __future__ import annotations

import math
import random

import pytest
from wnba_services.forecasting.minutes import (
    MAX_MINUTES,
    GameObservation,
    MinutesModel,
    build_joint_sampler,
    build_minutes_model,
    rest_adjustment,
)
from wnba_services.forecasting.recency import (
    fit_half_life,
    half_life_weights,
    weighted_mean,
    weighted_ratio,
)


# --------------------------------------------------------------------------------------
# Recency
# --------------------------------------------------------------------------------------
def test_half_life_weights_halve_at_the_half_life() -> None:
    weights = half_life_weights(11, 5.0)
    assert weights[-1] == pytest.approx(1.0)
    assert weights[-6] == pytest.approx(0.5)
    assert weights[-11] == pytest.approx(0.25)


def test_a_longer_half_life_weights_the_past_more_heavily() -> None:
    oldest_short = half_life_weights(20, 3.0)[0]
    oldest_long = half_life_weights(20, 20.0)[0]
    assert oldest_long > oldest_short


def test_half_life_must_be_positive() -> None:
    with pytest.raises(ValueError, match="half_life must be positive"):
        half_life_weights(5, 0.0)


def test_weighted_mean_leans_toward_recent_form() -> None:
    values = [10.0] * 10 + [20.0] * 3
    assert weighted_mean(values, 3.0) > weighted_mean(values, 30.0)


def test_weighted_ratio_weighs_by_the_denominator_not_by_the_game() -> None:
    """A two-minute cameo must not vote as loudly as a thirty-minute night.

    Averaging per-game ratios gives the cameo an equal vote; taking the ratio of the weighted
    sums gives it two minutes of influence, which is all the evidence it contains.
    """
    stats = [20.0, 4.0]
    minutes = [40.0, 2.0]
    # A very long half-life makes the weights effectively uniform, isolating the ratio-of-sums
    # behaviour from the decay.
    ratio = weighted_ratio(stats, minutes, 1e6)
    assert ratio == pytest.approx(24.0 / 42.0, rel=1e-5)

    # Averaging the per-game ratios gives the two-minute cameo's 2.0 points per minute the same
    # vote as the forty-minute night's 0.5, and lands almost twice as high.
    naive = math.fsum(s / m for s, m in zip(stats, minutes, strict=True)) / 2
    assert ratio < naive


def test_weighted_ratio_is_safe_on_empty_and_zero_denominators() -> None:
    assert weighted_ratio([], [], 5.0) == 0.0
    assert weighted_ratio([3.0], [0.0], 5.0) == 0.0


def test_fit_half_life_recovers_a_fast_moving_series() -> None:
    """A series with a level shift should prefer forgetting; a stationary one should not."""
    shifting = [[5.0] * 10 + [25.0] * 10]
    stationary = [[15.0 + (index % 2) for index in range(20)]]
    assert fit_half_life(shifting) < fit_half_life(stationary)


def test_fit_half_life_falls_back_when_there_is_nothing_to_learn_from() -> None:
    assert fit_half_life([[1.0, 2.0]], default=9.0) == 9.0


# --------------------------------------------------------------------------------------
# Minutes
# --------------------------------------------------------------------------------------
def _mixed_history() -> tuple[list[float], list[bool]]:
    minutes = [31.0, 30.0, 32.0, 29.0, 33.0, 12.0, 11.0, 13.0, 10.0, 14.0]
    started = [True] * 5 + [False] * 5
    return minutes, started


def test_a_rotation_uncertain_player_gets_two_regimes() -> None:
    minutes, started = _mixed_history()
    model = build_minutes_model(minutes, started, start_probability=0.5, half_life=8.0)
    assert model.starter_mean > model.bench_mean + 10.0
    assert model.expected_minutes == pytest.approx(
        0.5 * model.starter_mean + 0.5 * model.bench_mean
    )


def test_the_mixture_is_wider_than_either_regime_alone() -> None:
    """The whole reason for the mixture: 30-or-11 is not 20-give-or-take-two."""
    minutes, started = _mixed_history()
    model = build_minutes_model(minutes, started, start_probability=0.5, half_life=8.0)
    assert model.stddev > max(model.starter_std, model.bench_std)


def test_an_always_starter_collapses_to_one_regime() -> None:
    minutes = [30.0, 31.0, 29.0, 32.0, 28.0, 30.0, 31.0, 29.0]
    model = build_minutes_model(minutes, [True] * 8, start_probability=0.9, half_life=8.0)
    assert model.starter_mean == pytest.approx(model.bench_mean)
    assert model.start_probability == 1.0


def test_a_collapsed_model_ignores_start_probability_entirely() -> None:
    """With one observed regime, start probability carries no minutes information.

    Letting it mix two identical regimes would be harmless; letting it shift the mean would
    not, and that is what an un-guarded implementation does.
    """
    minutes = [30.0, 31.0, 29.0, 32.0, 28.0, 30.0, 31.0, 29.0]
    low = build_minutes_model(minutes, [True] * 8, start_probability=0.1, half_life=8.0)
    high = build_minutes_model(minutes, [True] * 8, start_probability=0.9, half_life=8.0)
    assert low.expected_minutes == pytest.approx(high.expected_minutes)


def test_shifting_preserves_the_gap_between_regimes() -> None:
    minutes, started = _mixed_history()
    model = build_minutes_model(minutes, started, start_probability=0.6, half_life=8.0)
    gap = model.starter_mean - model.bench_mean
    shifted = model.shifted_to(24.0)
    assert shifted.expected_minutes == pytest.approx(24.0)
    assert shifted.starter_mean - shifted.bench_mean == pytest.approx(gap)


def test_expected_minutes_of_zero_is_honoured_rather_than_treated_as_missing() -> None:
    """The `or` bug: a zero projection used to fall through to the historical average."""
    minutes, started = _mixed_history()
    model = build_minutes_model(
        minutes, started, start_probability=0.5, half_life=8.0, expected_minutes=0.0
    )
    assert model.expected_minutes == pytest.approx(0.0)


def test_minutes_model_rejects_mismatched_history() -> None:
    with pytest.raises(ValueError, match="same games"):
        build_minutes_model([30.0], [True, False], start_probability=0.5, half_life=8.0)


def test_sampled_minutes_stay_inside_a_basketball_game() -> None:
    minutes, started = _mixed_history()
    model = build_minutes_model(minutes, started, start_probability=0.5, half_life=8.0)
    observations = [GameObservation(minutes=value, stat=value * 0.5) for value in minutes]
    sampler = build_joint_sampler(observations, half_life=8.0)
    assert sampler is not None
    rng = random.Random(3)
    for _ in range(2000):
        drawn, rate = sampler.sample(rng, model, 1.0)
        assert 0.0 <= drawn <= MAX_MINUTES
        assert rate >= 0.0


# --------------------------------------------------------------------------------------
# Joint sampling
# --------------------------------------------------------------------------------------
def test_short_outings_no_longer_produce_absurd_rates() -> None:
    """Four minutes and two points is 0.5 points per minute -- and 16 points at 32 minutes.

    The shrinkage exists so that four minutes of evidence cannot author a sixteen-point draw.
    """
    observations = [GameObservation(minutes=30.0, stat=15.0) for _ in range(9)]
    observations.append(GameObservation(minutes=4.0, stat=2.0))
    sampler = build_joint_sampler(observations, half_life=8.0)
    assert sampler is not None
    assert max(sampler.rates) < 0.55


def test_the_sampler_keeps_minutes_and_rate_paired() -> None:
    """A night's usage belongs to that night's minutes; independent draws destroy that."""
    observations = [
        GameObservation(minutes=34.0, stat=20.0),
        GameObservation(minutes=33.0, stat=19.0),
        GameObservation(minutes=35.0, stat=21.0),
        GameObservation(minutes=14.0, stat=3.0),
        GameObservation(minutes=13.0, stat=2.0),
        GameObservation(minutes=15.0, stat=4.0),
    ]
    sampler = build_joint_sampler(observations, half_life=100.0)
    assert sampler is not None
    high_minute_rates = [
        rate
        for rate, deviation in zip(sampler.rates, sampler.minute_deviations, strict=True)
        if deviation > 0
    ]
    low_minute_rates = [
        rate
        for rate, deviation in zip(sampler.rates, sampler.minute_deviations, strict=True)
        if deviation < 0
    ]
    assert min(high_minute_rates) > max(low_minute_rates)


def test_a_player_who_never_played_has_no_sampler() -> None:
    assert build_joint_sampler([GameObservation(0.0, 0.0)] * 5, half_life=8.0) is None


# --------------------------------------------------------------------------------------
# Rest
# --------------------------------------------------------------------------------------
def test_back_to_backs_shorten_rotations_and_long_rest_does_not() -> None:
    tired = rest_adjustment(1.0, 3.0)
    normal = rest_adjustment(2.0, 2.0)
    rested = rest_adjustment(4.0, 4.0)
    assert tired.minutes_multiplier < normal.minutes_multiplier <= rested.minutes_multiplier
    assert tired.rate_multiplier < 1.0


def test_a_tired_opponent_concedes_efficiency_without_lengthening_our_rotation() -> None:
    baseline = rest_adjustment(4.0, 4.0)
    against_tired = rest_adjustment(4.0, 1.0)
    assert against_tired.rate_multiplier > baseline.rate_multiplier
    assert against_tired.minutes_multiplier == baseline.minutes_multiplier


def test_missing_rest_information_changes_nothing() -> None:
    neutral = rest_adjustment(None, None)
    assert neutral.minutes_multiplier == 1.0
    assert neutral.rate_multiplier == 1.0


def test_minutes_model_mixture_moments_match_a_direct_computation() -> None:
    model = MinutesModel(
        starter_mean=30.0,
        starter_std=3.0,
        bench_mean=12.0,
        bench_std=2.0,
        start_probability=0.25,
    )
    assert model.expected_minutes == pytest.approx(0.25 * 30.0 + 0.75 * 12.0)
    within = 0.25 * 9.0 + 0.75 * 4.0
    between = 0.25 * (30.0 - 16.5) ** 2 + 0.75 * (12.0 - 16.5) ** 2
    assert model.stddev == pytest.approx(math.sqrt(within + between))
