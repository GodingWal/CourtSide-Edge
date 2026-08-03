"""Market math: odds arithmetic, pick'em pricing, and stake sizing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from wnba_domain.enums import EntryType
from wnba_domain.market import PayoutRule
from wnba_marketmath import (
    RiskPolicy,
    american_to_decimal,
    american_to_probability,
    breakeven_uniform_leg_probability,
    decimal_to_american,
    entry_expected_value,
    expected_value,
    growth_optimal_fraction,
    implied_break_even_american,
    independent_correct_count_pmf,
    prizepicks_payout_table,
    remove_vig,
    staked_fraction,
)

VERIFIED_AT = datetime(2026, 8, 3, tzinfo=UTC)


def power(legs: int, multiplier: str) -> PayoutRule:
    return PayoutRule(
        entry_type=EntryType.POWER,
        leg_count=legs,
        payouts_by_correct={legs: Decimal(multiplier)},
    )


# --------------------------------------------------------------------------------------
# Odds arithmetic
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("american", "probability"),
    [(-110, 110 / 210), (100, 0.5), (-200, 2 / 3), (150, 0.4)],
)
def test_american_to_probability(american: int, probability: float) -> None:
    assert american_to_probability(american) == pytest.approx(probability)


@pytest.mark.parametrize("american", [-99, 0, 99])
def test_invalid_american_odds_are_rejected(american: int) -> None:
    with pytest.raises(ValueError, match="not valid American odds"):
        american_to_probability(american)


@pytest.mark.parametrize("american", [-500, -110, 100, 250])
def test_american_decimal_roundtrip(american: int) -> None:
    assert decimal_to_american(american_to_decimal(american)) == american


def test_removing_vig_produces_probabilities_that_sum_to_one() -> None:
    over, under = remove_vig(-110, -110)
    assert over == pytest.approx(0.5)
    assert over + under == pytest.approx(1.0)

    over, under = remove_vig(-140, 120)
    assert over + under == pytest.approx(1.0)
    assert over > under


def test_expected_value_of_a_fair_bet_is_zero() -> None:
    assert expected_value(0.5, 100) == pytest.approx(0.0)
    assert expected_value(0.55, -110) > 0.0
    assert expected_value(0.50, -110) < 0.0


# --------------------------------------------------------------------------------------
# The number that decides whether pick'em is playable at all
# --------------------------------------------------------------------------------------
def test_two_leg_power_play_breakeven_is_57_7_percent() -> None:
    """A 3x two-leg power play requires 57.74% per leg, not 50%.

    This is the single most important constant in the free-data build. Beating it demands a
    genuine ~8-point edge on both legs simultaneously, which is a far higher bar than beating a
    -110 sportsbook line, and no amount of dashboard styling changes that.
    """
    breakeven = breakeven_uniform_leg_probability(power(2, "3"))
    assert breakeven == pytest.approx((1 / 3) ** 0.5, abs=1e-6)
    assert breakeven == pytest.approx(0.5774, abs=1e-4)


@pytest.mark.parametrize(
    ("legs", "multiplier"),
    [(2, "3"), (3, "5"), (4, "10"), (5, "20"), (6, "37.5")],
)
def test_power_play_breakeven_matches_the_closed_form(legs: int, multiplier: str) -> None:
    """Bisection must agree with ``(1/M)^(1/n)`` on all-or-nothing rules.

    The bisection exists for flex rules, where no closed form applies; agreeing with the
    analytic answer where one does exist is what makes it trustworthy where one does not.
    """
    rule = power(legs, multiplier)
    expected = (1.0 / float(multiplier)) ** (1.0 / legs)
    assert breakeven_uniform_leg_probability(rule) == pytest.approx(expected, abs=1e-6)


def test_pickem_pricing_expressed_as_american_odds() -> None:
    """3x on two legs is roughly -137 per leg. Considerably less charming than '3x'."""
    assert implied_break_even_american(power(2, "3")) == pytest.approx(-137, abs=1)


def test_flex_breakeven_is_lower_than_power_because_partial_credit_pays() -> None:
    table = prizepicks_payout_table(VERIFIED_AT)
    flex = breakeven_uniform_leg_probability(table.rule_for(EntryType.FLEX, 5))
    pwr = breakeven_uniform_leg_probability(table.rule_for(EntryType.POWER, 5))
    assert flex < pwr


# --------------------------------------------------------------------------------------
# Correct-count distributions
# --------------------------------------------------------------------------------------
def test_independent_pmf_matches_hand_computation() -> None:
    pmf = independent_correct_count_pmf([0.6, 0.5])
    assert pmf == pytest.approx((0.4 * 0.5, 0.6 * 0.5 + 0.4 * 0.5, 0.6 * 0.5))
    assert sum(pmf) == pytest.approx(1.0)


def test_independent_pmf_sums_to_one_for_many_legs() -> None:
    pmf = independent_correct_count_pmf([0.55, 0.61, 0.48, 0.72, 0.5, 0.66])
    assert len(pmf) == 7
    assert sum(pmf) == pytest.approx(1.0)


def test_entry_ev_is_zero_at_the_breakeven_probability() -> None:
    rule = power(2, "3")
    p = breakeven_uniform_leg_probability(rule)
    pmf = independent_correct_count_pmf([p, p])
    assert entry_expected_value(pmf, rule) == pytest.approx(0.0, abs=1e-6)


def test_entry_ev_rejects_a_mismatched_pmf() -> None:
    with pytest.raises(ValueError, match=r"needs a PMF over 0\.\.2"):
        entry_expected_value([0.5, 0.5], power(2, "3"))


def test_correlation_can_flip_a_positive_edge_negative() -> None:
    """Why the independent PMF is a baseline and never the answer.

    Two 60% legs on the same game clear the 57.7% bar comfortably under independence. Make them
    correlated -- as legs on one game genuinely are, because a blowout sinks both at once -- and
    the same entry becomes a losing bet. Nothing about the legs changed. Only the joint
    distribution did, which is exactly the quantity a naive model never computes.
    """
    rule = power(2, "3")

    independent = independent_correct_count_pmf([0.60, 0.60])
    assert entry_expected_value(independent, rule) > 0.0

    # Same 60% marginals, strong positive correlation: they mostly both hit or both miss.
    correlated = (0.38, 0.04, 0.58)
    assert sum(correlated) == pytest.approx(1.0)
    assert entry_expected_value(correlated, rule) > 0.0

    # Strong negative correlation: same marginals, but they rarely land together.
    anti_correlated = (0.10, 0.60, 0.30)
    assert sum(anti_correlated) == pytest.approx(1.0)
    assert entry_expected_value(anti_correlated, rule) < 0.0


# --------------------------------------------------------------------------------------
# Sizing
# --------------------------------------------------------------------------------------
def test_no_edge_means_no_stake() -> None:
    rule = power(2, "3")
    p = breakeven_uniform_leg_probability(rule) - 0.02
    pmf = independent_correct_count_pmf([p, p])
    assert growth_optimal_fraction(pmf, rule) == 0.0


def test_kelly_grows_with_edge() -> None:
    rule = power(2, "3")
    small = independent_correct_count_pmf([0.60, 0.60])
    large = independent_correct_count_pmf([0.75, 0.75])
    assert 0.0 < growth_optimal_fraction(small, rule) < growth_optimal_fraction(large, rule)


def test_binary_kelly_agrees_with_the_closed_form() -> None:
    """On an all-or-nothing rule the numerical search must reproduce ``(bp - q)/b``."""
    rule = power(2, "3")
    p_all = 0.65 * 0.65
    pmf = independent_correct_count_pmf([0.65, 0.65])
    b = 3.0 - 1.0
    closed_form = (b * p_all - (1 - p_all)) / b
    assert growth_optimal_fraction(pmf, rule) == pytest.approx(closed_form, abs=1e-6)


def test_policy_caps_bind_before_kelly_does() -> None:
    policy = RiskPolicy()
    rule = power(2, "3")
    pmf = independent_correct_count_pmf([0.85, 0.85])
    ev = entry_expected_value(pmf, rule)

    assert growth_optimal_fraction(pmf, rule) > policy.max_fraction_per_position
    sized = staked_fraction(pmf, rule, policy, expected_value=ev)
    assert sized == pytest.approx(policy.max_fraction_per_position)


def test_exposure_headroom_is_shared_not_duplicated() -> None:
    policy = RiskPolicy()
    rule = power(2, "3")
    pmf = independent_correct_count_pmf([0.85, 0.85])
    ev = entry_expected_value(pmf, rule)

    sized = staked_fraction(
        pmf,
        rule,
        policy,
        expected_value=ev,
        already_staked_this_game=policy.max_fraction_per_game,
    )
    assert sized == 0.0, "a game already at its exposure cap must not accept another position"


def test_thin_edges_are_refused_regardless_of_kelly() -> None:
    policy = RiskPolicy(min_expected_value=0.04)
    rule = power(2, "3")
    # 58.5% per leg clears the 57.74% breakeven, but only just: a 2.7% edge.
    pmf = independent_correct_count_pmf([0.585, 0.585])
    ev = entry_expected_value(pmf, rule)

    assert 0.0 < ev < 0.04
    assert growth_optimal_fraction(pmf, rule) > 0.0
    assert staked_fraction(pmf, rule, policy, expected_value=ev) == 0.0


def test_risk_policy_limits_must_nest() -> None:
    with pytest.raises(ValueError, match="per-position limit cannot exceed"):
        RiskPolicy(max_fraction_per_position=0.03, max_fraction_per_game=0.01)


# --------------------------------------------------------------------------------------
# Payout tables
# --------------------------------------------------------------------------------------
def test_bundled_tables_are_well_formed_and_flagged_unverified() -> None:
    from wnba_marketmath import PAYOUT_TABLES_ARE_UNVERIFIED

    assert "STARTING POINT" in PAYOUT_TABLES_ARE_UNVERIFIED

    table = prizepicks_payout_table(VERIFIED_AT)
    assert table.rule_for(EntryType.POWER, 2).payout_for(2) == Decimal("3")
    assert table.rule_for(EntryType.POWER, 2).payout_for(1) == Decimal("0")
    assert table.rule_for(EntryType.FLEX, 5).payout_for(3) == Decimal("0.4")

    with pytest.raises(LookupError, match="no power rule for 7 legs"):
        table.rule_for(EntryType.POWER, 7)


def test_a_power_rule_that_pays_on_partial_credit_is_rejected() -> None:
    with pytest.raises(ValueError, match="pays only when every leg is correct"):
        PayoutRule(
            entry_type=EntryType.POWER,
            leg_count=3,
            payouts_by_correct={3: Decimal("5"), 2: Decimal("1.25")},
        )
