"""The correlated simulator.

The contract this suite defends: turning correlation up must change the *joint* distribution
without disturbing the *marginals*. If correlation could quietly shift a leg's own probability,
every calibration effort upstream would be silently undone by a parameter nobody was watching.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from wnba_domain.enums import EntryType
from wnba_domain.market import PayoutRule
from wnba_marketmath import entry_expected_value, independent_correct_count_pmf
from wnba_sim import correct_count_pmf, correlation_from_matrix, effective_leg_correlation

SIMS = 200_000


def power(legs: int, multiplier: str) -> PayoutRule:
    return PayoutRule(
        entry_type=EntryType.POWER,
        leg_count=legs,
        payouts_by_correct={legs: Decimal(multiplier)},
    )


def marginal(pmf: tuple[float, ...]) -> float:
    """Average per-leg hit probability implied by a correct-count PMF."""
    n = len(pmf) - 1
    return sum(k * p for k, p in enumerate(pmf)) / n


# --------------------------------------------------------------------------------------
# Agreement with the closed form
# --------------------------------------------------------------------------------------
def test_zero_correlation_reproduces_the_poisson_binomial() -> None:
    """A simulator that cannot match the analytic answer where one exists is not trustworthy
    where one does not."""
    probs = [0.55, 0.62, 0.48, 0.71]
    analytic = independent_correct_count_pmf(probs)
    simulated = correct_count_pmf(probs, correlation=0.0, simulations=SIMS, seed=11)

    assert simulated == pytest.approx(analytic, abs=5e-3)


def test_simulation_is_reproducible() -> None:
    """An unreproducible forecast cannot be audited, and an unauditable one cannot be learned
    from. The seed belongs in the ModelRun record."""
    a = correct_count_pmf([0.6, 0.55], correlation=0.3, simulations=20_000, seed=42)
    b = correct_count_pmf([0.6, 0.55], correlation=0.3, simulations=20_000, seed=42)
    c = correct_count_pmf([0.6, 0.55], correlation=0.3, simulations=20_000, seed=43)

    assert a == b
    assert a != c


# --------------------------------------------------------------------------------------
# The central invariant
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("rho", [0.0, 0.2, 0.5, 0.8])
def test_correlation_never_disturbs_the_marginals(rho: float) -> None:
    probs = [0.60, 0.60, 0.60]
    pmf = correct_count_pmf(probs, correlation=rho, simulations=SIMS, seed=5)

    assert sum(pmf) == pytest.approx(1.0)
    assert marginal(pmf) == pytest.approx(0.60, abs=5e-3), (
        "correlation changed a leg's own probability; the copula construction is broken and "
        "every upstream calibration is being silently undone"
    )


@pytest.mark.parametrize("rho", [0.0, 0.3, 0.6])
def test_marginals_hold_for_unequal_legs(rho: float) -> None:
    probs = [0.35, 0.60, 0.85]
    pmf = correct_count_pmf(probs, correlation=rho, simulations=SIMS, seed=9)
    assert marginal(pmf) == pytest.approx(sum(probs) / len(probs), abs=5e-3)


# --------------------------------------------------------------------------------------
# Direction of the effect -- the part that is easy to get backwards
# --------------------------------------------------------------------------------------
def test_positive_correlation_helps_all_or_nothing_power_plays() -> None:
    """Deliberate same-game stacking is an edge on power plays, not a rookie error.

    You are paid only when every leg lands, so bunching the outcomes raises the probability of
    the only event that pays.
    """
    rule = power(2, "3")
    probs = [0.60, 0.60]

    independent = correct_count_pmf(probs, correlation=0.0, simulations=SIMS, seed=3)
    correlated = correct_count_pmf(probs, correlation=0.4, simulations=SIMS, seed=3)

    assert correlated[2] > independent[2]
    assert entry_expected_value(correlated, rule) > entry_expected_value(independent, rule)


def test_correlation_is_a_mean_preserving_spread() -> None:
    """The mechanism behind every other correlation result here.

    Correlation leaves the expected number of correct legs untouched and moves mass from the
    middle to the tails. Everything else follows from Jensen: whether that spread helps depends
    entirely on the curvature of the payout in the number of correct legs.
    """
    probs = [0.60] * 5
    independent = correct_count_pmf(probs, correlation=0.0, simulations=SIMS, seed=17)
    correlated = correct_count_pmf(probs, correlation=0.5, simulations=SIMS, seed=17)

    def moments(pmf: tuple[float, ...]) -> tuple[float, float]:
        mean = sum(k * p for k, p in enumerate(pmf))
        var = sum(p * (k - mean) ** 2 for k, p in enumerate(pmf))
        return mean, var

    mean_ind, var_ind = moments(independent)
    mean_cor, var_cor = moments(correlated)

    assert mean_cor == pytest.approx(mean_ind, abs=0.02)
    assert var_cor > var_ind * 1.5
    assert correlated[3] < independent[3], "mass leaves the middle"
    assert correlated[5] > independent[5], "and piles into the tails"


def test_convex_payouts_benefit_from_correlation() -> None:
    """Real pick'em tables are strongly convex, so correlation helps almost everywhere.

    PrizePicks 5-leg flex pays 0.4x / 2x / 10x for 3 / 4 / 5 correct -- successive steps of
    0.4, 1.6 and 8.0. Against a payout curving upward that hard, the tail mass correlation
    creates at k=5 dwarfs the middle it costs. Same-game stacking is a genuine edge on these
    products, not a beginner's mistake.
    """
    flex = PayoutRule(
        entry_type=EntryType.FLEX,
        leg_count=5,
        payouts_by_correct={5: Decimal("10"), 4: Decimal("2"), 3: Decimal("0.4")},
    )
    probs = [0.60] * 5

    independent = correct_count_pmf(probs, correlation=0.0, simulations=SIMS, seed=17)
    correlated = correct_count_pmf(probs, correlation=0.5, simulations=SIMS, seed=17)

    assert correlated[4] < independent[4], "the middle does thin out"
    assert entry_expected_value(correlated, flex) > entry_expected_value(independent, flex), (
        "but the 10x tail more than repays it"
    )


def test_concave_payouts_are_harmed_by_correlation() -> None:
    """The other side of Jensen, on a synthetic rule.

    No operator currently offers a payout this flat -- it is constructed to isolate the
    mechanism. Steps of 1.0, 0.4, 0.3, 0.2, 0.1 curve downward, so spreading the distribution
    destroys value. The lesson is that the sign of the correlation effect is a property of the
    payout table, not a universal fact, and it must be recomputed whenever an operator changes
    its multipliers.
    """
    concave = PayoutRule(
        entry_type=EntryType.FLEX,
        leg_count=5,
        payouts_by_correct={
            5: Decimal("2.0"),
            4: Decimal("1.9"),
            3: Decimal("1.7"),
            2: Decimal("1.4"),
            1: Decimal("1.0"),
        },
    )
    probs = [0.60] * 5

    independent = correct_count_pmf(probs, correlation=0.0, simulations=SIMS, seed=17)
    correlated = correct_count_pmf(probs, correlation=0.5, simulations=SIMS, seed=17)

    assert entry_expected_value(correlated, concave) < entry_expected_value(independent, concave)


def test_higher_correlation_monotonically_raises_the_all_correct_probability() -> None:
    probs = [0.55, 0.55, 0.55]
    all_correct = [
        correct_count_pmf(probs, correlation=rho, simulations=SIMS, seed=21)[3]
        for rho in (0.0, 0.25, 0.5, 0.75)
    ]
    assert all_correct == sorted(all_correct)


# --------------------------------------------------------------------------------------
# Guards against impossible joint distributions
# --------------------------------------------------------------------------------------
def test_negative_correlation_across_three_legs_is_refused() -> None:
    """Three legs cannot be mutually negatively correlated through one shared factor. Returning
    a plausible-looking PMF for an impossible joint distribution would be worse than failing."""
    with pytest.raises(ValueError, match="cannot represent negative correlation"):
        correct_count_pmf([0.5, 0.5, 0.5], correlation=-0.3)


def test_negative_correlation_works_for_two_legs() -> None:
    rule = power(2, "3")
    probs = [0.60, 0.60]
    anti = correct_count_pmf(probs, correlation=-0.5, simulations=SIMS, seed=13)

    assert marginal(anti) == pytest.approx(0.60, abs=5e-3)
    assert anti[2] < 0.36
    assert entry_expected_value(anti, rule) < 0.0, (
        "two 60% legs clear the 57.7% bar under independence but must fail it when they rarely "
        "land together"
    )


@pytest.mark.parametrize("rho", [-1.0, 1.0, 1.5])
def test_degenerate_correlation_is_rejected(rho: float) -> None:
    with pytest.raises(ValueError, match="strictly within"):
        correct_count_pmf([0.5, 0.5], correlation=rho)


def test_non_psd_correlation_matrix_is_rejected() -> None:
    """A matrix that is symmetric with a unit diagonal can still describe a joint distribution
    that cannot exist. Cholesky is the cheapest honest check."""
    impossible = [[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]]
    with pytest.raises(ValueError, match="cannot exist"):
        correlation_from_matrix([0.5, 0.5, 0.5], impossible)


def test_matrix_form_matches_the_one_factor_form() -> None:
    """Equicorrelation supplied as a matrix must agree with the one-factor shortcut."""
    probs = [0.6, 0.55, 0.65]
    rho = 0.4
    matrix = [[1.0 if i == j else rho for j in range(3)] for i in range(3)]

    one_factor = correct_count_pmf(probs, correlation=rho, simulations=SIMS, seed=8)
    explicit = correlation_from_matrix(probs, matrix, simulations=SIMS, seed=8)

    assert explicit == pytest.approx(one_factor, abs=6e-3)


# --------------------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------------------
def test_outcome_correlation_is_below_latent_correlation() -> None:
    """Dichotomising a correlated normal always loses dependence.

    A latent 0.4 realises as roughly 0.26 between the binary outcomes. This is expected
    behaviour, not a miscalibration, and the diagnostic exists partly to stop someone
    "fixing" it.
    """
    pmf = correct_count_pmf([0.6, 0.6], correlation=0.4, simulations=SIMS, seed=7)
    recovered = effective_leg_correlation(pmf)

    assert 0.0 < recovered < 0.4
    assert recovered == pytest.approx(0.26, abs=0.03)


def test_effective_correlation_is_zero_for_independent_legs() -> None:
    pmf = correct_count_pmf([0.6, 0.6, 0.6], correlation=0.0, simulations=SIMS, seed=4)
    assert effective_leg_correlation(pmf) == pytest.approx(0.0, abs=0.01)
