"""The distributional claims the forecast rests on, checked against closed forms."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from wnba_services.forecasting.distributions import (
    correlated_sum_moments,
    count_pmf,
    estimate_correlation,
    estimate_dispersion,
    line_probabilities,
    linear_pool,
    log_pool,
    negative_binomial_pmf,
    pmf_mean,
    pmf_median,
    pmf_stddev,
    poisson_pmf,
)

LENGTH = 160


def test_poisson_matches_its_own_moments() -> None:
    pmf = poisson_pmf(9.0, LENGTH)
    assert pmf_mean(pmf) == pytest.approx(9.0, abs=1e-6)
    assert pmf_stddev(pmf) == pytest.approx(3.0, abs=1e-6)


def test_negative_binomial_reproduces_requested_mean_and_variance() -> None:
    for mean in (2.0, 8.5, 22.0):
        for dispersion in (1.2, 2.3, 4.0):
            pmf = negative_binomial_pmf(mean, dispersion, LENGTH)
            assert pmf_mean(pmf) == pytest.approx(mean, rel=1e-3)
            assert pmf_stddev(pmf) == pytest.approx(math.sqrt(dispersion * mean), rel=2e-2)


def test_negative_binomial_degenerates_to_poisson_at_unit_dispersion() -> None:
    poisson = poisson_pmf(11.0, LENGTH)
    negative_binomial = negative_binomial_pmf(11.0, 1.0, LENGTH)
    assert negative_binomial == pytest.approx(poisson, abs=1e-12)


def test_dispersion_is_the_whole_point_for_points() -> None:
    """A points prop modelled as Poisson understates its own spread by a third or more.

    This is the defect that motivated the module: the same mean, priced at the same line,
    produces materially different over/under probabilities depending on whether the extra
    variance is admitted.
    """
    mean, line = 16.0, Decimal("18.5")
    poisson_over, _, _ = line_probabilities(poisson_pmf(mean, LENGTH), line)
    dispersed_over, _, _ = line_probabilities(count_pmf(mean, 2.3, LENGTH), line)
    assert dispersed_over > poisson_over + 0.05


def test_estimate_dispersion_shrinks_small_samples_toward_the_prior() -> None:
    tight = [10.0, 10.0, 10.0, 10.0]
    assert estimate_dispersion(tight, prior=2.3) > 1.5
    assert estimate_dispersion([], prior=2.3) == 2.3


def test_estimate_dispersion_never_reports_under_dispersion() -> None:
    """A short run of near-identical games is a small sample, not a discovery."""
    assert estimate_dispersion([12.0] * 12, prior=1.05) >= 1.0


def test_estimate_dispersion_follows_a_genuinely_wild_sample() -> None:
    wild = [0.0, 30.0, 2.0, 28.0, 1.0, 33.0, 4.0, 26.0, 0.0, 31.0, 3.0, 29.0]
    calm = [14.0, 15.0, 16.0, 15.0, 14.0, 16.0, 15.0, 15.0, 14.0, 16.0, 15.0, 15.0]
    assert estimate_dispersion(wild, prior=2.0) > estimate_dispersion(calm, prior=2.0)


def test_correlation_recovers_a_known_relationship() -> None:
    rising = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert estimate_correlation(rising, rising) == pytest.approx(1.0)
    assert estimate_correlation(rising, list(reversed(rising))) == pytest.approx(-1.0)
    assert estimate_correlation(rising, [3.0] * 5) == 0.0


def test_correlated_sum_exceeds_the_independent_sum() -> None:
    means = [16.0, 7.0, 4.0]
    variances = [36.0, 9.0, 4.0]
    independent = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    correlated = [[1.0 if i == j else 0.35 for j in range(3)] for i in range(3)]

    independent_mean, independent_variance = correlated_sum_moments(means, variances, independent)
    _, correlated_variance = correlated_sum_moments(means, variances, correlated)

    assert independent_mean == pytest.approx(27.0)
    assert independent_variance == pytest.approx(49.0)
    assert correlated_variance > independent_variance


def test_log_pool_is_sharper_than_the_mixture_it_replaced() -> None:
    """The reason production switched pools: a mixture is wider than any of its members."""
    low = count_pmf(12.0, 1.5, LENGTH)
    high = count_pmf(20.0, 1.5, LENGTH)
    mixture = linear_pool([low, high], [0.5, 0.5], LENGTH)
    pooled = log_pool([low, high], [0.5, 0.5], LENGTH)

    assert pmf_stddev(mixture) > pmf_stddev(low)
    assert pmf_stddev(pooled) < pmf_stddev(mixture)


def test_both_pools_are_normalised_and_agree_when_components_agree() -> None:
    single = count_pmf(10.0, 1.6, LENGTH)
    for pool in (linear_pool, log_pool):
        blended = pool([single, single], [0.4, 0.6], LENGTH)
        assert math.fsum(blended) == pytest.approx(1.0, abs=1e-9)
        assert blended == pytest.approx(single, abs=1e-9)


def test_log_pool_survives_a_component_that_assigns_zero() -> None:
    """Without the probability floor, one confident component could veto every outcome."""
    narrow = tuple([0.0] * 10 + [1.0] + [0.0] * (LENGTH - 11))
    wide = count_pmf(20.0, 2.0, LENGTH)
    blended = log_pool([narrow, wide], [0.5, 0.5], LENGTH)
    assert math.fsum(blended) == pytest.approx(1.0, abs=1e-9)
    assert all(value >= 0.0 for value in blended)


def test_line_probabilities_partition_the_distribution() -> None:
    pmf = count_pmf(9.0, 1.4, LENGTH)
    for line in (Decimal("8.5"), Decimal("9"), Decimal("0.5")):
        over, push, under = line_probabilities(pmf, line)
        assert over + push + under == pytest.approx(1.0, abs=1e-9)
        assert min(over, push, under) >= 0.0


def test_only_an_integer_line_can_push() -> None:
    pmf = count_pmf(9.0, 1.4, LENGTH)
    assert line_probabilities(pmf, Decimal("9.5"))[1] == 0.0
    assert line_probabilities(pmf, Decimal("9"))[1] > 0.0


def test_median_is_the_first_outcome_reaching_half_the_mass() -> None:
    pmf = count_pmf(9.0, 1.0, LENGTH)
    median = pmf_median(pmf)
    assert math.fsum(pmf[: median + 1]) >= 0.5
    assert math.fsum(pmf[:median]) < 0.5
