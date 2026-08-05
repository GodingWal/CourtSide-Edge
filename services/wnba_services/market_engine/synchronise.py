"""Comparing quotes that were observed at comparable moments, and recording what they agreed on.

The consensus layer has existed since migration 015 and computes everything it should: a
weighted-median line, price dispersion, best-price selection per side, source reliability and an
explicit closing designation. It had one source, so it correctly reported every result as
uncorroborated.

With two sources a new failure appears, and it is the one that makes naive multi-source data
worse than single-source data: quotes observed an hour apart are not a disagreement, they are two
different moments. A book that moved to 15.5 at 6pm and a book still showing 14.5 from 4pm look
like a full point of dispersion and are actually a stale row. Every downstream number -- best
line, dispersion, closing-line value -- inherits that error, and it always points the same way,
because the staler source is the one that has not seen the news.

So quotes are bucketed into synchronisation windows before they are compared, and the width of
the window each consensus was built from is stored on the row. A consensus whose sources are
eight minutes apart and one whose sources are eight seconds apart are different evidence, and
after the fact only the stored window can tell them apart.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from wnba_services.market_engine.consensus import (
    SourceQuote,
    SourceReliability,
    build_consensus,
)

__all__ = [
    "SYNCHRONISATION_WINDOW",
    "SynchronisedGroup",
    "designate",
    "record_consensus",
    "synchronise",
]

# Two quotes inside this window are one moment. Wider than a polling jitter and far narrower than
# the time it takes news to move a line, which is the gap the window has to sit in.
SYNCHRONISATION_WINDOW = timedelta(minutes=6)

# Inside this long before lock, a consensus is the closing one. The designation is made before
# the market closes rather than reconstructed afterwards, so it cannot be chosen with hindsight.
_CLOSING_WINDOW = timedelta(minutes=20)


@dataclass(frozen=True)
class SynchronisedGroup:
    """Quotes from different sources that describe the same moment."""

    observed_at: datetime
    spread_seconds: int
    quotes: tuple[SourceQuote, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({quote.source for quote in self.quotes}))

    @property
    def is_corroborated(self) -> bool:
        return len(self.sources) > 1


def synchronise(
    quotes: Sequence[tuple[datetime, SourceQuote]],
    *,
    window: timedelta = SYNCHRONISATION_WINDOW,
) -> list[SynchronisedGroup]:
    """Bucket quotes into comparable moments, keeping one quote per source per bucket.

    The latest quote from each source inside a bucket wins, because a source that reported twice
    inside six minutes has told you its current view twice and only the second one is current.
    Sorting by observation time first is what makes the buckets deterministic regardless of the
    order the rows arrived in.
    """
    ordered = sorted(quotes, key=lambda item: item[0])
    groups: list[SynchronisedGroup] = []
    bucket: list[tuple[datetime, SourceQuote]] = []

    def flush() -> None:
        if not bucket:
            return
        latest: dict[str, tuple[datetime, SourceQuote]] = {}
        for observed, quote in bucket:
            current = latest.get(quote.source)
            if current is None or observed >= current[0]:
                latest[quote.source] = (observed, quote)
        times = [observed for observed, _ in bucket]
        groups.append(
            SynchronisedGroup(
                observed_at=max(times),
                spread_seconds=int((max(times) - min(times)).total_seconds()),
                quotes=tuple(quote for _, quote in sorted(latest.values(), key=lambda i: i[0])),
            )
        )
        bucket.clear()

    for observed, quote in ordered:
        if bucket and observed - bucket[0][0] > window:
            flush()
        bucket.append((observed, quote))
    flush()
    return groups


def designate(observed_at: datetime, locks_at: datetime, *, is_first: bool) -> str:
    """``opening`` / ``intermediate`` / ``closing``, decided before the market closes.

    Deciding afterwards would let the designation be chosen with hindsight -- and "the closing
    line" picked from a settled record is whichever quote makes the result look best.
    """
    if is_first:
        return "opening"
    return "closing" if locks_at - observed_at <= _CLOSING_WINDOW else "intermediate"


def record_consensus(
    cur: Any,
    *,
    player_id: Any,
    game_id: Any,
    prop_type: str,
    group: SynchronisedGroup,
    locks_at: datetime,
    is_first: bool,
    reliability: dict[str, SourceReliability] | None = None,
) -> None:
    """Persist one synchronised consensus so closing-line value has something to read."""
    consensus = build_consensus(list(group.quotes), reliability or {})
    if consensus is None:
        return
    lines = [float(quote.line) for quote in group.quotes]
    mean_line = math.fsum(lines) / len(lines)
    dispersion = (
        math.sqrt(math.fsum((value - mean_line) ** 2 for value in lines) / len(lines))
        if len(lines) > 1
        else 0.0
    )
    cur.execute(
        """INSERT INTO wnba.market_consensus
           (consensus_id,player_id,game_id,prop_type,observed_at,synchronised_within_seconds,
            source_count,sources,consensus_line,line_dispersion,price_dispersion,
            best_over_source,best_under_source,is_corroborated,designation,detail)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (player_id,game_id,prop_type,observed_at) DO NOTHING""",
        (
            uuid4(),
            player_id,
            game_id,
            prop_type,
            group.observed_at,
            group.spread_seconds,
            len(group.sources),
            list(group.sources),
            Decimal(str(consensus.line)),
            dispersion,
            consensus.dispersion,
            consensus.best_over_source,
            consensus.best_under_source,
            group.is_corroborated,
            designate(group.observed_at, locks_at, is_first=is_first),
            Jsonb(
                {
                    **consensus.to_payload(),
                    "synchronisation_window_seconds": int(SYNCHRONISATION_WINDOW.total_seconds()),
                    "observed_spread_seconds": group.spread_seconds,
                }
            ),
        ),
    )


def latest_consensus(
    cur: Any, *, limit: int = 100, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Recent consensus rows for the console, corroborated ones first."""
    _ = now or datetime.now(UTC)
    cur.execute(
        """SELECT c.*,p.full_name
           FROM wnba.market_consensus c
           JOIN wnba.players p ON p.player_id=c.player_id
           ORDER BY c.is_corroborated DESC,c.observed_at DESC LIMIT %s""",
        (max(1, limit),),
    )
    return [dict(row) for row in cur.fetchall()]
