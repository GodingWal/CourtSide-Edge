"""Pick'em entry pricing.

The whole free-data pivot lives in this module. Without two-sided prices there is no vig to
strip and no fair probability handed to us; value has to be computed from a fixed payout table
applied to several legs at once.

That has one consequence worth stating plainly: **entry EV depends on the joint distribution
across legs, not on the individual legs.** Two 58% legs on the same game are not a 33.6%
parlay. A single latent game state -- pace, blowout, foul trouble -- moves them together.

Correlation is a **mean-preserving spread** on the number of correct legs: it leaves the
expected count alone and moves mass from the middle to the tails. By Jensen, whether that helps
is decided by the curvature of the payout in the correct-leg count:

* **Convex payout -> correlation helps.** Every real pick'em table is strongly convex. A 5-leg
  flex paying 0.4x / 2x / 10x for 3 / 4 / 5 correct has successive steps of 0.4, 1.6 and 8.0;
  the tail mass correlation creates at k=5 dwarfs the middle it costs. Power plays are the
  extreme case -- all payout sits on the top outcome. Deliberate same-game stacking is a
  genuine edge on these products, not a beginner's error.
* **Concave payout -> correlation hurts.** Flat, insurance-like ladders lose value when the
  distribution is spread.

So the sign is a property of the operator's payout table, not a universal fact, and it must be
recomputed whenever those multipliers change. Nothing in this module computes its own joint
distribution: only the correlated simulator in ``wnba_sim`` is entitled to model that structure.
:func:`independent_correct_count_pmf` exists purely as the naive baseline to measure against.

"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from wnba_domain.enums import EntryType
from wnba_domain.identity import SourceName
from wnba_domain.market import PayoutRule, PayoutTable

from wnba_marketmath.odds import decimal_to_american

__all__ = [
    "PAYOUT_TABLES_ARE_UNVERIFIED",
    "breakeven_uniform_leg_probability",
    "entry_expected_value",
    "implied_break_even_american",
    "independent_correct_count_pmf",
    "prizepicks_payout_table",
    "underdog_payout_table",
]

PAYOUT_TABLES_ARE_UNVERIFIED = (
    "The bundled payout tables are a STARTING POINT, not ground truth. Operators change "
    "multipliers, vary them by region, and run promotions. A payout table that is wrong by one "
    "step silently mis-prices every entry built on it, and the resulting EV will look "
    "plausible -- which is precisely what makes it dangerous. Verify against the live source "
    "and record the verification date before trusting any number out of this module."
)


# --------------------------------------------------------------------------------------
# Distributions over correct-leg counts
# --------------------------------------------------------------------------------------
def independent_correct_count_pmf(leg_probabilities: Sequence[float]) -> tuple[float, ...]:
    """Poisson-binomial PMF over the number of correct legs, assuming independence.

    This is the **baseline**, never the answer. Its only legitimate uses are as a sanity check
    and as the comparison that quantifies how much correlation is moving the entry.
    """
    if not leg_probabilities:
        raise ValueError("an entry needs at least one leg")
    for p in leg_probabilities:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"leg probability {p} outside [0, 1]")

    pmf = [1.0]
    for p in leg_probabilities:
        nxt = [0.0] * (len(pmf) + 1)
        for k, acc in enumerate(pmf):
            nxt[k] += acc * (1.0 - p)
            nxt[k + 1] += acc * p
        pmf = nxt
    return tuple(pmf)


def _uniform_pmf(probability: float, leg_count: int) -> tuple[float, ...]:
    return tuple(
        math.comb(leg_count, k) * probability**k * (1.0 - probability) ** (leg_count - k)
        for k in range(leg_count + 1)
    )


# --------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------
def entry_expected_value(
    correct_count_pmf: Sequence[float],
    rule: PayoutRule,
) -> float:
    """Expected profit per unit staked, given the joint correct-count distribution.

    Pass the **simulated** PMF from ``wnba_sim``. Passing the independent one and calling the
    result an edge is the single most efficient way to lose money slowly while feeling rigorous.
    """
    if len(correct_count_pmf) != rule.leg_count + 1:
        raise ValueError(
            f"a {rule.leg_count}-leg rule needs a PMF over 0..{rule.leg_count} "
            f"({rule.leg_count + 1} entries), got {len(correct_count_pmf)}"
        )
    total = math.fsum(correct_count_pmf)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"correct-count PMF must sum to 1, got {total}")

    gross = math.fsum(p * float(rule.payout_for(k)) for k, p in enumerate(correct_count_pmf))
    return gross - 1.0


def breakeven_uniform_leg_probability(rule: PayoutRule) -> float:
    """Per-leg hit rate needed to break even, assuming independent, equally-likely legs.

    The number that should be printed above every pick'em dashboard. A 2-pick power play at 3x
    needs **57.7%** per leg, not 50% -- so a "coin flip you like" is a losing bet, and a
    dashboard showing 54% win probability is showing a loss.

    Solved by bisection so flex rules with partial credit are handled exactly, rather than by
    the closed form that only works for all-or-nothing power plays.
    """
    if entry_expected_value(_uniform_pmf(1.0, rule.leg_count), rule) < 0.0:
        raise ValueError("this rule cannot break even even with certain legs")

    low, high = 0.0, 1.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if entry_expected_value(_uniform_pmf(mid, rule.leg_count), rule) < 0.0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def implied_break_even_american(rule: PayoutRule) -> int:
    """The break-even per-leg probability expressed as American odds.

    Useful for arguing about a pick'em product in sportsbook terms: a 3x two-leg power play is
    roughly a -137 price per leg, which is a considerably less attractive number than "3x".
    """
    p = breakeven_uniform_leg_probability(rule)
    if p <= 0.0 or p >= 1.0:
        raise ValueError("break-even probability is degenerate")
    return decimal_to_american(1.0 / p)


# --------------------------------------------------------------------------------------
# Bundled tables -- unverified defaults
# --------------------------------------------------------------------------------------
def _rule(entry: EntryType, legs: int, payouts: dict[int, str]) -> PayoutRule:
    return PayoutRule(
        entry_type=entry,
        leg_count=legs,
        payouts_by_correct={k: Decimal(v) for k, v in payouts.items()},
    )


def prizepicks_payout_table(effective_from: datetime) -> PayoutTable:
    """A PrizePicks-shaped payout table. See :data:`PAYOUT_TABLES_ARE_UNVERIFIED`.

    ``effective_from`` must be the moment you verified these numbers against the live product,
    not the moment you imported this module.
    """
    return PayoutTable(
        source=SourceName.PRIZEPICKS,
        effective_from=effective_from,
        rules=(
            _rule(EntryType.POWER, 2, {2: "3"}),
            _rule(EntryType.POWER, 3, {3: "5"}),
            _rule(EntryType.POWER, 4, {4: "10"}),
            _rule(EntryType.POWER, 5, {5: "20"}),
            _rule(EntryType.POWER, 6, {6: "37.5"}),
            _rule(EntryType.FLEX, 3, {3: "2.25", 2: "1.25"}),
            _rule(EntryType.FLEX, 4, {4: "5", 3: "1.5"}),
            _rule(EntryType.FLEX, 5, {5: "10", 4: "2", 3: "0.4"}),
            _rule(EntryType.FLEX, 6, {6: "25", 5: "2", 4: "0.4"}),
        ),
    )


def underdog_payout_table(effective_from: datetime) -> PayoutTable:
    """An Underdog-shaped payout table. See :data:`PAYOUT_TABLES_ARE_UNVERIFIED`."""
    return PayoutTable(
        source=SourceName.UNDERDOG,
        effective_from=effective_from,
        rules=(
            _rule(EntryType.POWER, 2, {2: "3"}),
            _rule(EntryType.POWER, 3, {3: "6"}),
            _rule(EntryType.POWER, 4, {4: "10"}),
            _rule(EntryType.POWER, 5, {5: "20"}),
            _rule(EntryType.FLEX, 3, {3: "3", 2: "1.25"}),
            _rule(EntryType.FLEX, 4, {4: "6", 3: "1.5"}),
            _rule(EntryType.FLEX, 5, {5: "10", 4: "2.5", 3: "0.4"}),
        ),
    )
