"""Market objects: archived prop quotes, payout structures, and cross-source consensus.

Working free means working in a **pick'em** market rather than a two-sided sportsbook market.
That is a different market, not a worse one, and it changes the mathematics:

* There is no pair of prices to strip vig from, so there is no directly recoverable "fair"
  probability to anchor against.
* Value comes from a fixed payout table applied to 2-6 legs at once, so expected value depends
  on the **joint** distribution across correlated legs -- not on any single leg in isolation.

Both shapes are modelled here because the domain should not need rewriting the day a two-sided
source becomes available.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import BitemporalRecord, FrozenModel
from wnba_domain.enums import EntryType, MarketKind, PropType, Side
from wnba_domain.identity import GameId, PlayerId, SourceName, SourceRef

__all__ = [
    "CONTINUOUS_PROP_TYPES",
    "ConsensusLine",
    "PayoutRule",
    "PayoutTable",
    "PropQuote",
    "QuoteKey",
]

MIN_AMERICAN_ODDS_MAGNITUDE = 100

CONTINUOUS_PROP_TYPES: frozenset[PropType] = frozenset({PropType.FANTASY_POINTS})
"""Markets whose outcome is not an integer.

Everything else in this domain resolves to a countable event, which is why the forecast layer
models those with an exact PMF over the non-negative integers. Fantasy points are a weighted
sum and land anywhere, so they get neither the half-point line rule nor the discrete
distribution."""


class QuoteKey(FrozenModel):
    """The natural key of a market. Used to join quotes across sources and over time."""

    player_id: PlayerId
    game_id: GameId
    prop_type: PropType

    def __str__(self) -> str:
        return f"{self.player_id}:{self.game_id}:{self.prop_type.value}"


class PropQuote(BitemporalRecord):
    """One archived observation of one line, from one source, at one moment.

    This is the single most time-critical object in the platform. Historical prop odds are
    paywalled everywhere, so this table *is* our market history. Every hour the archiver is not
    running is an hour of evaluation data that cannot be reconstructed later at any price.

    ``valid_from`` is when the source published the line; ``system_from`` is when we saw it.
    """

    quote_id: UUID
    source: SourceName
    source_quote_id: Annotated[str, Field(min_length=1, max_length=200)]
    player_id: PlayerId
    game_id: GameId
    prop_type: PropType
    line: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("200"))]
    market_kind: MarketKind
    locks_at: AwareDatetime | None = None

    # --- sportsbook two-sided ---------------------------------------------------------
    over_american_odds: Annotated[int, Field(ge=-100000, le=100000)] | None = None
    under_american_odds: Annotated[int, Field(ge=-100000, le=100000)] | None = None

    # --- pick'em ----------------------------------------------------------------------
    over_multiplier: Annotated[Decimal, Field(gt=Decimal("0"), le=Decimal("10"))] | None = None
    under_multiplier: Annotated[Decimal, Field(gt=Decimal("0"), le=Decimal("10"))] | None = None
    """Per-side payout adjustment. PrizePicks "demons" and "goblins" shift the multiplier
    rather than the price, so a naive reader that ignores these mis-prices the entry."""

    is_promotional: bool = False
    is_available: bool = True
    source_ref: SourceRef

    @model_validator(mode="after")
    def _price_shape_matches_market_kind(self) -> Self:
        if self.market_kind is MarketKind.SPORTSBOOK_TWO_SIDED:
            # At least one side must be priced, but not necessarily both. Operators routinely
            # offer a single direction on low lines -- "3-Pointers Made 0.5, higher -186" with
            # no lower at all. Refusing to store those would silently shrink the archive, and
            # an archive with holes cannot be refetched later at any price. They are recorded
            # here and rejected at *pricing* time by `fair_probabilities`, which is where the
            # missing side actually matters.
            priced = [
                ("over", self.over_american_odds),
                ("under", self.under_american_odds),
            ]
            if all(odds is None for _, odds in priced):
                raise ValueError("a two-sided market must carry at least one price")
            for label, odds in priced:
                if odds is None:
                    continue
                if -MIN_AMERICAN_ODDS_MAGNITUDE < odds < MIN_AMERICAN_ODDS_MAGNITUDE:
                    raise ValueError(f"{label} odds {odds} is not valid American odds")
            if self.over_multiplier is not None or self.under_multiplier is not None:
                raise ValueError("pick'em multipliers are meaningless on a two-sided market")
        elif self.over_american_odds is not None or self.under_american_odds is not None:
            raise ValueError("a pick'em market has no American prices")
        return self

    @property
    def is_two_sided(self) -> bool:
        """True only when both prices are present, i.e. when the vig can be removed.

        Pricing code must gate on this rather than on ``market_kind``: a single-sided quote is
        archivable but not devigable, and treating one as the other invents a fair probability
        out of half a market.
        """
        return (
            self.market_kind is MarketKind.SPORTSBOOK_TWO_SIDED
            and self.over_american_odds is not None
            and self.under_american_odds is not None
        )

    @model_validator(mode="after")
    def _line_precision(self) -> Self:
        # Lines on countable stats land on .5 or whole numbers, so anything else signals a
        # parsing bug -- and a silently wrong line is worse than a missing one.
        #
        # Continuous markets are the exception and must not be held to that rule. Fantasy
        # points are genuinely quoted at 37.05 or 39.55; rejecting those as malformed would
        # discard real rows from an archive that cannot be refetched later.
        if self.prop_type in CONTINUOUS_PROP_TYPES:
            if self.line != self.line.quantize(Decimal("0.01")):
                raise ValueError(f"line {self.line} exceeds two decimal places")
        elif (self.line * 2) % 1 != 0:
            raise ValueError(
                f"line {self.line} is not a half-point increment "
                f"(market {self.prop_type.value} has integer outcomes)"
            )
        return self

    def multiplier_for(self, side: Side) -> Decimal:
        """Payout multiplier for one leg. ``1`` means standard, unmodified pick'em pricing."""
        if self.market_kind is not MarketKind.PICKEM:
            raise ValueError("multiplier_for is only meaningful on a pick'em quote")
        chosen = self.over_multiplier if side is Side.OVER else self.under_multiplier
        return Decimal("1") if chosen is None else chosen

    @property
    def key(self) -> QuoteKey:
        return QuoteKey(player_id=self.player_id, game_id=self.game_id, prop_type=self.prop_type)

    def age_seconds_at(self, moment: AwareDatetime) -> float:
        """How stale this quote is. A gate on every recommendation."""
        return (moment - self.system_from).total_seconds()


# --------------------------------------------------------------------------------------
# Payout structures
# --------------------------------------------------------------------------------------
class PayoutRule(FrozenModel):
    """Payout for one entry shape, as a multiple of stake, keyed by number of correct legs.

    Flex entries pay partial credit, which is why the whole distribution over correct-leg
    counts is required. Reducing a flex entry to "probability all legs hit" throws away most of
    its value and produces a systematically pessimistic EV.
    """

    entry_type: EntryType
    leg_count: Annotated[int, Field(ge=2, le=8)]
    payouts_by_correct: dict[int, Annotated[Decimal, Field(ge=Decimal("0"))]]

    @model_validator(mode="after")
    def _payouts_well_formed(self) -> Self:
        for correct in self.payouts_by_correct:
            if not 0 <= correct <= self.leg_count:
                raise ValueError(f"correct-leg count {correct} outside 0..{self.leg_count}")
        if self.leg_count not in self.payouts_by_correct:
            raise ValueError("a perfect entry must have a defined payout")
        if self.entry_type is EntryType.POWER:
            paying = {c for c, m in self.payouts_by_correct.items() if m > 0}
            if paying != {self.leg_count}:
                raise ValueError("a power entry pays only when every leg is correct")
        return self

    def payout_for(self, correct: int) -> Decimal:
        """Stake multiple returned for ``correct`` correct legs. Unlisted counts pay nothing."""
        return self.payouts_by_correct.get(correct, Decimal("0"))


class PayoutTable(FrozenModel):
    """A source's full set of payout rules, effective from a point in time.

    Bitemporal-adjacent on purpose: operators change payouts, and an entry must always be
    evaluated against the table that was in force when it was constructed.
    """

    source: SourceName
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    rules: tuple[PayoutRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules_unique(self) -> Self:
        seen = {(r.entry_type, r.leg_count) for r in self.rules}
        if len(seen) != len(self.rules):
            raise ValueError("duplicate (entry_type, leg_count) rule in payout table")
        return self

    def rule_for(self, entry_type: EntryType, leg_count: int) -> PayoutRule:
        for rule in self.rules:
            if rule.entry_type is entry_type and rule.leg_count == leg_count:
                return rule
        raise LookupError(
            f"{self.source.value} has no {entry_type.value} rule for {leg_count} legs"
        )


# --------------------------------------------------------------------------------------
# Consensus
# --------------------------------------------------------------------------------------
class ConsensusLine(FrozenModel):
    """Cross-source view of one market at one moment.

    Without two-sided prices, dispersion between sources is the closest thing we have to a
    market-uncertainty signal: when PrizePicks and Underdog disagree by a full point, somebody
    has information, and it is probably not us.
    """

    key: QuoteKey
    as_of: AwareDatetime
    source_count: Annotated[int, Field(ge=1, le=20)]
    median_line: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("200"))]
    min_line: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("200"))]
    max_line: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("200"))]
    contributing_quote_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _bounds_ordered(self) -> Self:
        if not self.min_line <= self.median_line <= self.max_line:
            raise ValueError("consensus lines must satisfy min <= median <= max")
        if len(self.contributing_quote_ids) != self.source_count:
            raise ValueError("source_count must match the number of contributing quotes")
        return self

    @property
    def dispersion(self) -> Decimal:
        return self.max_line - self.min_line
