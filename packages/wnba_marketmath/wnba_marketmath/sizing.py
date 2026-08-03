"""Stake sizing under a risk policy.

Two properties matter more than the arithmetic:

* **Kelly here is numerical, not the textbook formula.** The closed form ``(bp - q)/b`` assumes
  a binary win/lose payout. A flex entry pays 10x, 2x, 0.4x and 0x depending on how many legs
  land, so the growth-optimal fraction has to be solved for. Applying the binary formula to a
  flex entry produces a confidently wrong number.
* **Full Kelly is not a target.** It is the edge of the cliff, and it assumes the probabilities
  are correct -- which, for a model priced off free data with no historical odds to validate
  against, they are not yet. The policy multiplier defaults to 0.10 and the caps bind hard.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Self

from pydantic import Field, model_validator
from wnba_domain.base import StrictModel
from wnba_domain.market import PayoutRule

__all__ = ["RiskPolicy", "growth_optimal_fraction", "staked_fraction"]

_GOLDEN_RATIO_INV = (math.sqrt(5.0) - 1.0) / 2.0
_SEARCH_TOLERANCE = 1e-9


class RiskPolicy(StrictModel):
    """Hard exposure limits. Deliberately restrictive while the system is unproven."""

    kelly_multiplier: Annotated[float, Field(gt=0.0, le=1.0)] = 0.10
    max_fraction_per_position: Annotated[float, Field(gt=0.0, le=0.05)] = 0.005
    max_fraction_per_game: Annotated[float, Field(gt=0.0, le=0.20)] = 0.02
    max_fraction_per_day: Annotated[float, Field(gt=0.0, le=0.50)] = 0.05
    min_expected_value: Annotated[float, Field(ge=0.0)] = 0.04
    """Below this edge, size is zero regardless of Kelly. Small edges on unvalidated
    probabilities are noise wearing a decimal point."""

    @model_validator(mode="after")
    def _limits_are_nested(self) -> Self:
        if self.max_fraction_per_position > self.max_fraction_per_game:
            raise ValueError("per-position limit cannot exceed the per-game limit")
        if self.max_fraction_per_game > self.max_fraction_per_day:
            raise ValueError("per-game limit cannot exceed the per-day limit")
        return self


def growth_optimal_fraction(
    correct_count_pmf: Sequence[float],
    rule: PayoutRule,
    *,
    max_fraction: float = 0.5,
) -> float:
    """Full-Kelly fraction: the ``f`` maximising expected log wealth.

    Wealth after an entry staking fraction ``f`` at payout multiple ``R`` is ``1 - f + f*R``.
    We maximise ``sum_k p_k * log(1 - f + f*R_k)`` by golden-section search, which is safe here
    because expected log wealth is concave in ``f``.

    Returns ``0.0`` when the entry has no positive edge -- there is no fraction of a losing bet
    worth staking, however small.
    """
    if len(correct_count_pmf) != rule.leg_count + 1:
        raise ValueError("PMF length must be leg_count + 1")

    payouts = [float(rule.payout_for(k)) for k in range(rule.leg_count + 1)]

    def expected_log_wealth(f: float) -> float:
        total = 0.0
        for p, payout in zip(correct_count_pmf, payouts, strict=True):
            if p <= 0.0:
                continue
            wealth = 1.0 - f + f * payout
            if wealth <= 0.0:
                return -math.inf
            total += p * math.log(wealth)
        return total

    # No edge means no stake. Checked at a tiny f rather than analytically so the same code
    # path covers flex rules with partial credit.
    if expected_log_wealth(1e-6) <= expected_log_wealth(0.0):
        return 0.0

    low, high = 0.0, max_fraction
    c = high - _GOLDEN_RATIO_INV * (high - low)
    d = low + _GOLDEN_RATIO_INV * (high - low)
    while high - low > _SEARCH_TOLERANCE:
        if expected_log_wealth(c) > expected_log_wealth(d):
            high = d
        else:
            low = c
        c = high - _GOLDEN_RATIO_INV * (high - low)
        d = low + _GOLDEN_RATIO_INV * (high - low)
    return max(0.0, (low + high) / 2.0)


def staked_fraction(
    correct_count_pmf: Sequence[float],
    rule: PayoutRule,
    policy: RiskPolicy,
    *,
    expected_value: float,
    already_staked_this_game: float = 0.0,
    already_staked_today: float = 0.0,
) -> float:
    """The fraction actually recommended, after edge gate, Kelly fraction and every cap.

    Caps are applied as remaining headroom, so correlated positions on the same game compete
    for one budget rather than each quietly claiming the maximum.
    """
    if expected_value < policy.min_expected_value:
        return 0.0

    full_kelly = growth_optimal_fraction(correct_count_pmf, rule)
    if full_kelly <= 0.0:
        return 0.0

    fraction = full_kelly * policy.kelly_multiplier
    fraction = min(fraction, policy.max_fraction_per_position)
    fraction = min(fraction, max(0.0, policy.max_fraction_per_game - already_staked_this_game))
    fraction = min(fraction, max(0.0, policy.max_fraction_per_day - already_staked_today))
    return max(0.0, fraction)
