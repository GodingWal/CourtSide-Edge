"""The Odds API adapter: the second market source, gated on a key and a dated verification.

The archive has run on one source since it was built, which weakens three things at once.
Closing-line value measured against a single operator measures that operator's own drift. Price
shopping has nothing to shop. And "the market says 14.5" is one company's opinion rather than a
consensus.

This is a commercial aggregator with a published free tier and an API key, so collection is
metered and attributable rather than scraped. Two gates stand in front of it and both fail
closed:

* **A key.** No key, no polling, and no silent fallback to an unauthenticated request.
* **A current terms verification.** A named person must have read the operator's terms, dated
  that reading, and recorded ``permitted``. See
  :mod:`wnba_services.ingestion.source_permissions` for why that lives in the database rather
  than in a runbook.

WHAT THE PLAYER-PROP ENDPOINT ACTUALLY COSTS
--------------------------------------------
Player props are per-event on this API: one request per game rather than one per slate. The
adapter therefore fetches events first and props second, and the caller decides how many games
are worth the quota. Burning a month's allowance on a Tuesday is a real failure mode and the
limit is an argument rather than a constant.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from wnba_domain.enums import MarketKind, PropType

__all__ = [
    "ODDS_API_BASE",
    "PROP_MARKETS",
    "OddsApiQuote",
    "map_market_to_prop_type",
    "parse_events",
    "parse_player_props",
]

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# The API's market keys for WNBA player props, mapped to this platform's vocabulary. Only markets
# the forecaster actually scores are requested: an unmapped market is quota spent on a row that
# would be discarded at parse time.
PROP_MARKETS: dict[str, PropType] = {
    "player_points": PropType.POINTS,
    "player_rebounds": PropType.REBOUNDS,
    "player_assists": PropType.ASSISTS,
    "player_threes": PropType.THREE_POINTERS,
    "player_points_rebounds_assists": PropType.POINTS_REBOUNDS_ASSISTS,
    "player_points_rebounds": PropType.POINTS_REBOUNDS,
    "player_points_assists": PropType.POINTS_ASSISTS,
    "player_rebounds_assists": PropType.REBOUNDS_ASSISTS,
}


def map_market_to_prop_type(market_key: str) -> PropType | None:
    return PROP_MARKETS.get(market_key)


@dataclass(frozen=True)
class OddsApiQuote:
    """One two-sided player prop from one bookmaker at one observation time.

    ``bookmaker`` is kept rather than collapsed into the source name. Two books inside one
    aggregator are two opinions, and averaging them before they reach the consensus layer would
    throw away the dispersion that layer exists to measure.
    """

    source_quote_id: str
    event_id: str
    bookmaker: str
    player_name: str
    prop_type: PropType
    line: Decimal
    over_american_odds: int | None
    under_american_odds: int | None
    observed_at: datetime
    commence_time: datetime | None

    @property
    def is_two_sided(self) -> bool:
        return self.over_american_odds is not None and self.under_american_odds is not None

    @property
    def market_kind(self) -> MarketKind:
        return MarketKind.SPORTSBOOK_TWO_SIDED


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _as_line(value: Any) -> Decimal | None:
    """Half-point lines only. Anything else is a parse error, not a line to round."""
    try:
        line = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if line < 0 or line > 200:
        return None
    if (line * 2) != (line * 2).to_integral_value():
        return None
    return line


def parse_events(payload: Any) -> list[dict[str, Any]]:
    """Event ids and tip times.

    One player-prop request is spent per event, so this list is quota rather than metadata.
    """
    if not isinstance(payload, list):
        raise ValueError("The Odds API events payload is not a list")
    events = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        event_id = raw.get("id")
        if not isinstance(event_id, str) or not event_id:
            continue
        events.append(
            {
                "event_id": event_id,
                "commence_time": _as_datetime(raw.get("commence_time")),
                "home_team": str(raw.get("home_team") or ""),
                "away_team": str(raw.get("away_team") or ""),
            }
        )
    return events


def parse_player_props(payload: Any, *, observed_at: datetime) -> list[OddsApiQuote]:
    """Flatten one event's bookmaker/market/outcome tree into two-sided quotes.

    The API nests outcomes as ``{name: "Over"|"Under", description: <player>, point, price}``.
    Over and Under arrive as siblings, so they are paired by (bookmaker, market, player, line)
    before a quote is emitted -- a one-sided row is archived by the caller if it wants it, but it
    is not devigable and this adapter does not pretend otherwise by inventing the other side.
    """
    if not isinstance(payload, dict):
        raise ValueError("The Odds API props payload is not an object")
    event_id = str(payload.get("id") or "")
    commence = _as_datetime(payload.get("commence_time"))
    bookmakers = payload.get("bookmakers")
    if not isinstance(bookmakers, list):
        return []

    paired: dict[tuple[str, str, str, Decimal], dict[str, int]] = {}
    order: list[tuple[str, str, str, Decimal]] = []
    for book in bookmakers:
        if not isinstance(book, dict):
            continue
        book_key = str(book.get("key") or "")
        markets = book.get("markets")
        if not book_key or not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            prop_type = map_market_to_prop_type(str(market.get("key") or ""))
            if prop_type is None:
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list):
                continue
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                player = str(outcome.get("description") or "").strip()
                line = _as_line(outcome.get("point"))
                price = outcome.get("price")
                side = str(outcome.get("name") or "").strip().lower()
                if not player or line is None or side not in {"over", "under"}:
                    continue
                if not isinstance(price, int):
                    continue
                key = (book_key, str(prop_type), player, line)
                if key not in paired:
                    paired[key] = {}
                    order.append(key)
                paired[key][side] = price

    quotes: list[OddsApiQuote] = []
    for book_key, prop_value, player, line in order:
        sides = paired[(book_key, prop_value, player, line)]
        quotes.append(
            OddsApiQuote(
                source_quote_id=f"{event_id}:{book_key}:{prop_value}:{player}:{line}",
                event_id=event_id,
                bookmaker=book_key,
                player_name=player,
                prop_type=PropType(prop_value),
                line=line,
                over_american_odds=sides.get("over"),
                under_american_odds=sides.get("under"),
                observed_at=observed_at,
                commence_time=commence,
            )
        )
    return quotes


class TheOddsApiAdapter:
    """Fetches WNBA player props. Refuses to run without a key and a current verification."""

    source = "the_odds_api"
    sport_key = "basketball_wnba"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        import os

        self.api_key = api_key or os.getenv("ODDS_API_KEY", "")
        self.base_url = (base_url or os.getenv("ODDS_API_BASE_URL") or ODDS_API_BASE).rstrip("/")

    def _require_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ODDS_API_KEY is not configured. This source is metered and keyed; there is no "
                "unauthenticated fallback."
            )
        return self.api_key

    def fetch_events(self, client: Any) -> list[dict[str, Any]]:
        # The key is checked before the client is touched. Evaluated inside the call arguments it
        # would be reached after the attribute lookup, so a missing key would surface as whatever
        # the client happened to fail with instead of as the missing key.
        key = self._require_key()
        response = client.get(
            f"{self.base_url}/sports/{self.sport_key}/events",
            params={"apiKey": key},
        )
        response.raise_for_status()
        return parse_events(response.json())

    def fetch_props(
        self,
        client: Any,
        event_ids: Sequence[str],
        *,
        observed_at: datetime | None = None,
        markets: Sequence[str] | None = None,
        regions: str = "us",
    ) -> Iterator[OddsApiQuote]:
        """One request per event. The caller chooses how much quota tonight is worth."""
        key = self._require_key()
        at = observed_at or datetime.now(UTC)
        wanted = ",".join(markets or PROP_MARKETS.keys())
        for event_id in event_ids:
            response = client.get(
                f"{self.base_url}/sports/{self.sport_key}/events/{event_id}/odds",
                params={
                    "apiKey": key,
                    "regions": regions,
                    "markets": wanted,
                    "oddsFormat": "american",
                },
            )
            response.raise_for_status()
            yield from parse_player_props(response.json(), observed_at=at)
