"""Two-sided sportsbook odds arithmetic.

Unused by the free pick'em pipeline today, and deliberately kept anyway: the day a two-sided
source becomes available, the domain and the market engine should already speak the language.
It also provides the honest reference point for what pick'em pricing costs us -- a 3x two-leg
power play is roughly a -137 per-leg parlay, which is worth knowing before admiring the payout.
"""

from __future__ import annotations

__all__ = [
    "american_to_decimal",
    "american_to_probability",
    "decimal_to_american",
    "expected_value",
    "remove_vig",
]

MIN_MAGNITUDE = 100


def _validate(american: int) -> None:
    if -MIN_MAGNITUDE < american < MIN_MAGNITUDE:
        raise ValueError(f"{american} is not valid American odds")


def american_to_probability(american: int) -> float:
    """Implied probability **including** the book's margin. Not a fair probability."""
    _validate(american)
    if american < 0:
        return -american / (-american + 100)
    return 100 / (american + 100)


def american_to_decimal(american: int) -> float:
    """Total return per unit staked, stake included."""
    _validate(american)
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / -american


def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds <= 1:
        raise ValueError("decimal odds must exceed 1")
    if decimal_odds >= 2:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def remove_vig(over_american: int, under_american: int) -> tuple[float, float]:
    """Fair over/under probabilities by proportional (multiplicative) devigging.

    Proportional devigging assumes the margin is spread evenly across both sides. That is an
    assumption, not a fact -- books shade favourites -- but it is the standard baseline and it
    is stated here rather than buried.
    """
    raw_over = american_to_probability(over_american)
    raw_under = american_to_probability(under_american)
    total = raw_over + raw_under
    if total <= 0:
        raise ValueError("degenerate market: implied probabilities sum to zero")
    return raw_over / total, raw_under / total


def expected_value(probability: float, american: int) -> float:
    """Expected profit per unit staked. ``0.13`` means +13%."""
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must lie in [0, 1]")
    return probability * american_to_decimal(american) - 1.0
