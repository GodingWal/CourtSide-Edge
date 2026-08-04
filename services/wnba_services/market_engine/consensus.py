"""Cross-source consensus, price dispersion, and an explicit closing line.

The live market pipeline reads one source. That is a real constraint on what the platform can
claim -- there is no consensus to compute, no dispersion to measure, and "the closing line" is
just the last thing one operator said -- and the readiness gate for closing-line value is
correctly still pending because of it.

This module is the layer a second source plugs into. Everything here works with one source and
says so rather than pretending: :attr:`MarketConsensus.is_corroborated` is ``False`` at
``source_count == 1``, dispersion is ``None`` rather than zero, and the closing line is labelled
with how many sources agreed on it. A single quote dressed as a consensus is worse than no
consensus at all, because the number looks like corroboration and is not.

**Choosing the second source is not a code decision.** It requires checking that source's terms,
confirming the collection is lawful and permitted, and dating that verification -- none of which
this file can do. What it can do is make sure that when the decision is made, the work is
plugging in an adapter rather than building this.

**Closing-line value is the one measure available without trustworthy prices.** In a pick'em
market there is no two-sided price to compare against, so the honest substitute is whether the
line moved toward the side we took between forecast and lock. It is weaker than true CLV and is
labelled as such wherever it is reported.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

__all__ = [
    "DEFAULT_RELIABILITY",
    "MarketConsensus",
    "SourceQuote",
    "SourceReliability",
    "build_consensus",
    "closing_quote",
    "line_value",
]

# What an unmeasured source is worth relative to a measured one. Deliberately below 1.0: a source
# nobody has scored yet should not outvote one with a track record simply by showing up.
DEFAULT_RELIABILITY = 0.6

_DISPERSION_WARNING = 1.0


@dataclass(frozen=True)
class SourceQuote:
    """One operator's view of one market at one moment."""

    source: str
    line: Decimal
    observed_at: datetime
    over_multiplier: Decimal | None = None
    under_multiplier: Decimal | None = None
    is_available: bool = True

    @property
    def is_priced(self) -> bool:
        """Whether this quote says anything about which side the operator prefers."""
        return (
            self.over_multiplier is not None
            and self.under_multiplier is not None
            and self.over_multiplier != self.under_multiplier
        )


@dataclass(frozen=True)
class SourceReliability:
    """How much a source's line should count, and the evidence behind that.

    Fitted rather than assigned: a source earns weight by being close to outcomes, not by being
    the one somebody integrated first.
    """

    source: str
    weight: float = DEFAULT_RELIABILITY
    sample_size: int = 0
    mean_absolute_error: float | None = None

    @property
    def is_measured(self) -> bool:
        return self.sample_size > 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "weight": self.weight,
            "sample_size": self.sample_size,
            "mean_absolute_error": self.mean_absolute_error,
            "is_measured": self.is_measured,
        }


@dataclass(frozen=True)
class MarketConsensus:
    """What the market as a whole is saying, and how much it agrees with itself."""

    line: Decimal
    source_count: int
    sources: tuple[str, ...]
    dispersion: float | None
    best_over_source: str | None
    best_under_source: str | None
    observed_at: datetime

    @property
    def is_corroborated(self) -> bool:
        """Whether more than one operator saw this market.

        The distinction this whole module exists to preserve: one source is an observation, two
        are a comparison, and only the second supports the word "consensus".
        """
        return self.source_count > 1

    @property
    def sources_disagree(self) -> bool:
        """Wide spread means the operators are pricing different information."""
        return self.dispersion is not None and self.dispersion >= _DISPERSION_WARNING

    def to_payload(self) -> dict[str, Any]:
        return {
            "line": str(self.line),
            "source_count": self.source_count,
            "sources": list(self.sources),
            "dispersion": self.dispersion,
            "is_corroborated": self.is_corroborated,
            "sources_disagree": self.sources_disagree,
            "best_over_source": self.best_over_source,
            "best_under_source": self.best_under_source,
        }


def build_consensus(
    quotes: Sequence[SourceQuote],
    reliability: Mapping[str, SourceReliability] | None = None,
) -> MarketConsensus | None:
    """Combine each source's latest quote into one view of the market.

    One quote per source: an operator that moved its line four times has one opinion, the last
    one, and counting the revisions would let the busiest source outvote the rest.

    The consensus line is a reliability-weighted median rather than a mean. A median cannot be
    dragged by one operator posting an outlier, which is exactly the failure a thin market
    produces, and it stays on the half-point grid that real lines live on.
    """
    available = [quote for quote in quotes if quote.is_available]
    if not available:
        return None

    latest: dict[str, SourceQuote] = {}
    for quote in available:
        current = latest.get(quote.source)
        if current is None or quote.observed_at > current.observed_at:
            latest[quote.source] = quote

    ordered = sorted(latest.values(), key=lambda quote: quote.source)
    weights = {
        quote.source: (
            reliability[quote.source].weight
            if reliability and quote.source in reliability
            else DEFAULT_RELIABILITY
        )
        for quote in ordered
    }

    line = _weighted_median([(quote.line, weights[quote.source]) for quote in ordered])
    values = [float(quote.line) for quote in ordered]
    dispersion = statistics.pstdev(values) if len(values) > 1 else None

    return MarketConsensus(
        line=line,
        source_count=len(ordered),
        sources=tuple(quote.source for quote in ordered),
        dispersion=dispersion,
        best_over_source=_best(ordered, side="over"),
        best_under_source=_best(ordered, side="under"),
        observed_at=max(quote.observed_at for quote in ordered),
    )


def _weighted_median(pairs: Sequence[tuple[Decimal, float]]) -> Decimal:
    """Lowest line whose cumulative weight reaches half the total."""
    ordered = sorted(pairs, key=lambda pair: pair[0])
    total = math.fsum(weight for _, weight in ordered)
    if total <= 0.0:
        return ordered[len(ordered) // 2][0]
    cumulative = 0.0
    for line, weight in ordered:
        cumulative += weight
        if cumulative >= total / 2:
            return line
    return ordered[-1][0]


def _best(quotes: Sequence[SourceQuote], *, side: str) -> str | None:
    """Which source offers the most attractive terms on this side.

    Line first, payout second. A better number is worth more than a better multiplier because it
    changes the probability of winning rather than the size of the win, and on a pick'em board
    the multipliers are usually identical anyway.
    """
    priced = list(quotes)
    if not priced:
        return None
    if side == "over":
        # A lower line is easier to go over.
        best_line = min(quote.line for quote in priced)
        contenders = [quote for quote in priced if quote.line == best_line]
        return max(contenders, key=lambda quote: quote.over_multiplier or Decimal("0")).source
    best_line = max(quote.line for quote in priced)
    contenders = [quote for quote in priced if quote.line == best_line]
    return max(contenders, key=lambda quote: quote.under_multiplier or Decimal("0")).source


def closing_quote(quotes: Sequence[SourceQuote], locks_at: datetime) -> MarketConsensus | None:
    """The consensus as it stood at lock -- an explicit designation, not the newest row.

    Observations after the lock are excluded rather than merely deprioritised. A line that moved
    once the market was closed cannot have been available to act on, and letting it in would turn
    the closing-line comparison into a measure of hindsight.
    """
    eligible = [quote for quote in quotes if quote.observed_at <= locks_at]
    return build_consensus(eligible)


def line_value(*, forecast_line: Decimal, closing_line: Decimal, side: str) -> float:
    """How far the line moved toward the side we took, in points.

    Positive means the market came to us. This is not closing-line value in the sportsbook sense
    -- there is no two-sided price to compare against on a pick'em board -- and it should never be
    reported as though it were. It is the weaker measure that the available data supports.
    """
    if side not in {"over", "under"}:
        raise ValueError(f"side must be 'over' or 'under', got {side!r}")
    direction = 1.0 if side == "over" else -1.0
    return direction * float(closing_line - forecast_line)
