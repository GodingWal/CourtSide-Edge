"""Which candidates to combine, and how many of them.

Everything upstream of this module answers "is this one leg worth anything". Nothing answered
"what should the entry be", which is the question actually being asked at the moment somebody
builds a slip. The gap mattered because the answer is not "the best legs": a payout table
rewards leg count non-linearly, correlation moves the joint distribution the payout is applied
to, and a fifth leg that is individually the best available can still lower the entry's expected
value.

The search is deliberately small and deliberately two-staged.

**Stage one screens on the independent PMF**, which is analytic and exact. It costs nothing, it
ranks combinations almost identically to the correlated answer for the sizes that matter, and it
means the expensive simulator runs tens of times instead of thousands.

**Stage two prices the finalists across the correlation band** -- at the estimate, and at one
standard error either side. An entry whose expected value changes sign inside that band is not
an opportunity, it is an unpriced position, and it is reported as such rather than ranked on its
midpoint. This is the same argument the pricing endpoint already makes about showing correlated
and independent answers together; it just applies it to a number we now estimate instead of one
the user types.

The construction is a suggestion for a human to accept, reject, or ignore. It sizes with the
same :class:`RiskPolicy` the pricer uses, it inherits every gate that policy imposes, and like
everything else here it can only decline -- there is no path where the search promotes an entry
the arithmetic did not support.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from wnba_domain.enums import EntryType
from wnba_domain.market import PayoutRule, PayoutTable
from wnba_marketmath import (
    RiskPolicy,
    entry_expected_value,
    independent_correct_count_pmf,
    staked_fraction,
)
from wnba_sim import correct_count_pmf

from wnba_services.market_engine.correlation import (
    CorrelationEstimate,
    EntryCorrelation,
    LegKey,
    entry_correlation,
)

__all__ = [
    "DEFAULT_MAX_LEGS_PER_GAME",
    "DEFAULT_SEARCH_POOL",
    "CandidateLeg",
    "ConstructedEntry",
    "construct_entries",
]

# How many of the ranked candidates the search considers. The combination count grows fast and
# the tail of the ranking is not where good entries come from; twelve keeps the exhaustive
# search honest (C(12,5) = 792 screens) without pretending the 40th-best leg belongs in a slip.
DEFAULT_SEARCH_POOL = 12

# Legs from one game per entry. The cap is structural rather than statistical: same-game legs are
# where correlation is largest and least well measured, so this limits exposure to the number we
# trust least. It is also the lever a user is most likely to want to move, so it is an argument.
DEFAULT_MAX_LEGS_PER_GAME = 2

# Simulations for the confirmation pass. Lower than the pricing endpoint's default because this
# runs once per finalist rather than once per user request, and the ranking it feeds is already
# insensitive at this resolution.
_CONFIRMATION_SIMULATIONS = 40_000


@dataclass(frozen=True)
class CandidateLeg:
    """One forecast the gate already called a candidate, with what pricing needs."""

    projection_id: str
    player_name: str
    prop_type: str
    side: str
    line: float
    probability: float
    """The **shrunk** probability. Building entries out of unshrunk edges would compound the
    selection bias `forecasting.selection` exists to remove, once per leg."""

    player_id: str | None = None
    team: str | None = None
    game_id: str | None = None
    breakeven: float | None = None

    @property
    def key(self) -> LegKey:
        return LegKey(
            prop_type=self.prop_type,
            side=self.side,
            player_id=self.player_id,
            team=self.team,
            game_id=self.game_id,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "player_name": self.player_name,
            "prop_type": self.prop_type,
            "side": self.side,
            "line": self.line,
            "probability": self.probability,
            "game_id": self.game_id,
            "team": self.team,
        }


@dataclass(frozen=True)
class ConstructedEntry:
    """One suggested entry, priced at the correlation estimate and across its band."""

    entry_type: str
    legs: tuple[CandidateLeg, ...]
    correlation: EntryCorrelation
    expected_value: float
    independent_expected_value: float
    worst_case_expected_value: float
    """Expected value at the least favourable end of the correlation band. The number the
    verdict is taken on, because it is the one that survives being wrong about correlation."""

    stake_fraction: float
    gates: tuple[str, ...]
    verdict: str

    @property
    def leg_count(self) -> int:
        return len(self.legs)

    def to_payload(self) -> dict[str, Any]:
        return {
            "entry_type": self.entry_type,
            "leg_count": self.leg_count,
            "legs": [leg.to_payload() for leg in self.legs],
            "correlation": self.correlation.to_payload(),
            "expected_value": self.expected_value,
            "independent_expected_value": self.independent_expected_value,
            "worst_case_expected_value": self.worst_case_expected_value,
            "correlation_swing": self.expected_value - self.independent_expected_value,
            "stake_fraction": self.stake_fraction,
            "gates": list(self.gates),
            "verdict": self.verdict,
        }


def _respects_game_cap(legs: Sequence[CandidateLeg], *, max_per_game: int) -> bool:
    counts: dict[str, int] = {}
    for leg in legs:
        if leg.game_id is None:
            continue
        counts[leg.game_id] = counts.get(leg.game_id, 0) + 1
        if counts[leg.game_id] > max_per_game:
            return False
    return True


def _has_duplicate_market(legs: Sequence[CandidateLeg]) -> bool:
    """Two legs on the same player and market are one position entered twice.

    Whatever the sides, this is not a two-leg entry: taking both sides is a guaranteed loss on a
    product that requires every leg, and taking the same side twice is a single leg the payout
    table is being told to treat as two independent ones.
    """
    seen = {(leg.player_id or leg.player_name, leg.prop_type) for leg in legs}
    return len(seen) != len(legs)


def _representable(correlation: float, leg_count: int) -> float:
    """Clip a correlation to what the one-factor model can express at this leg count.

    Negative correlation across three or more legs describes a joint distribution that does not
    exist, and the simulator rightly refuses it. A band that reaches into that region is not an
    error to raise on -- it is an estimate whose lower end is simply not representable, so it is
    priced at the nearest value that is.
    """
    floor = 0.0 if leg_count > 2 else -0.99
    return max(floor, min(0.99, correlation))


def _pmf_for(
    legs: Sequence[CandidateLeg], correlation: float, *, simulations: int, seed: int
) -> tuple[float, ...]:
    return correct_count_pmf(
        [leg.probability for leg in legs],
        correlation=_representable(correlation, len(legs)),
        simulations=simulations,
        seed=seed,
    )


def _price(
    legs: Sequence[CandidateLeg],
    rule: PayoutRule,
    correlation: float,
    *,
    simulations: int,
    seed: int,
) -> float:
    return entry_expected_value(
        _pmf_for(legs, correlation, simulations=simulations, seed=seed), rule
    )


def construct_entries(
    candidates: Sequence[CandidateLeg],
    table: PayoutTable,
    *,
    fitted: dict[tuple[str, str, str], CorrelationEstimate] | None = None,
    policy: RiskPolicy | None = None,
    entry_types: Sequence[EntryType] = (EntryType.POWER, EntryType.FLEX),
    max_legs: int = 5,
    pool: int = DEFAULT_SEARCH_POOL,
    max_per_game: int = DEFAULT_MAX_LEGS_PER_GAME,
    limit: int = 5,
    simulations: int = _CONFIRMATION_SIMULATIONS,
    seed: int = 0,
) -> list[ConstructedEntry]:
    """Rank entries by the expected value that survives the correlation band.

    Returns at most ``limit`` entries, best first, and returns an empty list rather than a weak
    suggestion when nothing clears the policy. "No entry tonight" is a legitimate and frequent
    answer; a search that always produces a slip is a search that has stopped being a filter.
    """
    risk = policy or RiskPolicy()
    ranked = sorted(candidates, key=lambda leg: leg.probability, reverse=True)[: max(0, pool)]
    if len(ranked) < 2:
        return []

    screened: list[tuple[float, tuple[CandidateLeg, ...], PayoutRule, EntryType]] = []
    for entry_type in entry_types:
        for size in range(2, max_legs + 1):
            try:
                rule = table.rule_for(entry_type, size)
            except LookupError:
                # The bundled table has no rule at this size for this product. Not an error --
                # products differ in which sizes they sell.
                continue
            for combination in combinations(ranked, size):
                if _has_duplicate_market(combination):
                    continue
                if not _respects_game_cap(combination, max_per_game=max_per_game):
                    continue
                independent = independent_correct_count_pmf(
                    [leg.probability for leg in combination]
                )
                screened.append(
                    (entry_expected_value(independent, rule), combination, rule, entry_type)
                )

    screened.sort(key=lambda item: item[0], reverse=True)
    finalists = screened[: max(limit * 4, limit)]

    constructed: list[ConstructedEntry] = []
    for independent_ev, combination, rule, entry_type in finalists:
        correlation = entry_correlation([leg.key for leg in combination], fitted)
        expected = _price(combination, rule, correlation.mean, simulations=simulations, seed=seed)
        low = _price(combination, rule, correlation.low, simulations=simulations, seed=seed)
        high = _price(combination, rule, correlation.high, simulations=simulations, seed=seed)
        worst = min(expected, low, high)

        gates: list[str] = []
        if worst < risk.min_expected_value:
            gates.append(
                f"worst-case edge {worst:.1%} across the correlation band is below the "
                f"{risk.min_expected_value:.0%} minimum"
            )
        if min(expected, low, high) < 0 <= max(expected, low, high):
            gates.append(
                "expected value changes sign inside the correlation band -- the entry is "
                "unpriced rather than positive"
            )
        if not correlation.is_fitted:
            gates.append(
                f"{len(correlation.pairs) - correlation.fitted_pairs} of "
                f"{len(correlation.pairs)} leg pairs use a stated prior, not a measurement"
            )

        pmf = _pmf_for(combination, correlation.mean, simulations=simulations, seed=seed)
        stake = staked_fraction(pmf, rule, risk, expected_value=worst)
        constructed.append(
            ConstructedEntry(
                entry_type=entry_type.value,
                legs=tuple(combination),
                correlation=correlation,
                expected_value=expected,
                independent_expected_value=independent_ev,
                worst_case_expected_value=worst,
                stake_fraction=stake,
                gates=tuple(gates),
                # A suggestion is only a suggestion when nothing blocks it. Any gate at all makes
                # it a decline that shows its reasoning, which is the more useful of the two.
                verdict="suggest" if not gates else "decline",
            )
        )

    constructed.sort(key=lambda entry: entry.worst_case_expected_value, reverse=True)
    return constructed[:limit]
