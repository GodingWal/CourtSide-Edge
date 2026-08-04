"""How many independent things have actually happened.

The learning loop reported thousands of settled episodes and then drew conclusions as if it had
thousands of independent observations. It did not, and the gap is not small.

**Repeats.** The forecaster runs whenever the board changes, so one market -- one player, one
game, one prop type -- accumulates a row per run. Every one of those rows resolves against the
*same* outcome. Counting them separately inflates the sample by whatever the refresh rate
happened to be that day, which is a property of the polling schedule rather than of the world.

**Clustering.** Even after collapsing repeats, the surviving markets are not independent of each
other. Every prop in a game shares one game state: a blowout suppresses both teams' starters
together, a track meet lifts everyone's counting stats together. Twenty markets across three
games carry nowhere near twenty markets' worth of evidence.

Both corrections point the same way -- the system knows less than it thought -- which is why they
matter. A calibration error measured across 3,000 correlated rows comes with confidence bands
narrow enough to look conclusive, and a readiness gate reading "500 recommendations" passes on a
count that a handful of nights produced.

:func:`dedupe_latest_per_market` handles the repeats. :func:`design_effect` handles the
clustering, using the standard survey-statistics correction: a cluster sample of size ``n`` in
clusters averaging ``m`` members with intra-cluster correlation ``rho`` carries the information
of roughly ``n / (1 + (m - 1) * rho)`` independent observations.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_INTRA_GAME_CORRELATION",
    "MARKET_KEY_FIELDS",
    "SampleShape",
    "dedupe_latest_per_market",
    "design_effect",
    "effective_sample_size",
    "market_key",
    "summarise_sample",
]

# One market resolves exactly once. Side is deliberately excluded: over and under are two ways of
# describing the same event, not two events.
MARKET_KEY_FIELDS: tuple[str, ...] = ("player_id", "game_id", "prop_type")

# Intra-cluster correlation between markets sharing a game. A stated prior, not a fitted value --
# but a conservative one, and vastly closer to the truth than the implicit zero it replaces.
# Counting stats in a blown-out game move together far more than this; independence is the one
# value we can be certain is wrong.
DEFAULT_INTRA_GAME_CORRELATION = 0.30


def market_key(row: Mapping[str, Any]) -> tuple[Hashable, ...]:
    """The identity of the thing being forecast, independent of how often it was forecast."""
    return tuple(str(row.get(field)) for field in MARKET_KEY_FIELDS)


def dedupe_latest_per_market[T: Mapping[str, Any]](
    rows: Sequence[T], *, order_field: str = "forecast_timestamp"
) -> list[T]:
    """Keep one row per market: the last forecast made before it locked.

    The latest forecast is the right representative rather than the first or an average of them,
    because it is the one an analyst would actually have acted on. Averaging across a market's
    own revisions would also invent a forecast that was never issued, and then score it.

    Rows missing the ordering field keep their input order, which makes the function total: a
    caller with no timestamps still gets one row per market rather than an exception.
    """
    chosen: dict[tuple[Hashable, ...], tuple[int, T]] = {}
    for index, row in enumerate(rows):
        key = market_key(row)
        rank = (row.get(order_field), index)
        current = chosen.get(key)
        if current is None or _sorts_after(rank, (current[1].get(order_field), current[0])):
            chosen[key] = (index, row)
    return [row for _, row in sorted(chosen.values(), key=lambda pair: pair[0])]


def _sorts_after(candidate: tuple[Any, int], incumbent: tuple[Any, int]) -> bool:
    """Later timestamp wins; ties and missing timestamps fall back to input order."""
    left, right = candidate[0], incumbent[0]
    if left is not None and right is not None:
        try:
            if left != right:
                return bool(left > right)
        except TypeError:
            return candidate[1] > incumbent[1]
    elif left is not None or right is not None:
        return left is not None
    return candidate[1] > incumbent[1]


def design_effect(cluster_sizes: Iterable[int], *, correlation: float) -> float:
    """``1 + (mean cluster size - 1) * rho``. Never below 1.

    Uses the mean cluster size rather than a weighted variant: with the small, uneven clusters a
    single night's slate produces, the weighted form is itself noisy, and overstating the
    correction is the safe direction for a system deciding whether it has learned anything yet.
    """
    sizes = [size for size in cluster_sizes if size > 0]
    if not sizes:
        return 1.0
    mean_size = math.fsum(float(size) for size in sizes) / len(sizes)
    return max(1.0, 1.0 + (mean_size - 1.0) * max(0.0, min(1.0, correlation)))


def effective_sample_size(
    observations: int,
    cluster_sizes: Iterable[int],
    *,
    correlation: float = DEFAULT_INTRA_GAME_CORRELATION,
) -> float:
    """Independent-observation equivalent of a clustered sample."""
    if observations <= 0:
        return 0.0
    return observations / design_effect(cluster_sizes, correlation=correlation)


@dataclass(frozen=True)
class SampleShape:
    """What a pile of settled rows is actually worth as evidence."""

    rows: int
    markets: int
    games: int
    effective: float

    @property
    def rows_per_market(self) -> float:
        return self.rows / self.markets if self.markets else 0.0

    @property
    def markets_per_game(self) -> float:
        return self.markets / self.games if self.games else 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "independent_markets": self.markets,
            "independent_games": self.games,
            "effective_sample_size": round(self.effective, 2),
            "rows_per_market": round(self.rows_per_market, 2),
            "markets_per_game": round(self.markets_per_game, 2),
        }


def summarise_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    correlation: float = DEFAULT_INTRA_GAME_CORRELATION,
) -> SampleShape:
    """Describe a settled sample honestly: raw rows, distinct markets, games, effective size.

    Reporting all four rather than one, because they answer different questions and the gaps
    between them are the finding. "3,097 episodes, 214 markets, 3 games, effective 96" is a
    sentence that stops somebody concluding the model is validated.
    """
    deduped = dedupe_latest_per_market(list(rows))
    per_game: dict[Hashable, int] = {}
    for row in deduped:
        game = str(row.get("game_id"))
        per_game[game] = per_game.get(game, 0) + 1
    return SampleShape(
        rows=len(rows),
        markets=len(deduped),
        games=len(per_game),
        effective=effective_sample_size(len(deduped), per_game.values(), correlation=correlation),
    )
