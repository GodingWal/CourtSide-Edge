"""Underdog Fantasy adapter: free, two-sided WNBA prop prices.

This is the archive's primary source. It matters more than it looks:

* PrizePicks answers automated requests with a DataDome CAPTCHA, so it is off the table --
  see :class:`~wnba_services.ingestion.http.BotDefenceError`.
* The legacy CourtSide Edge odds key is deactivated, which is exactly why the old archive
  stops on 2026-07-15.

Underdog prices **both directions in American odds**, so the vig can be removed and a fair
probability recovered -- something a pure pick'em feed can never give us. Where only one
direction is offered the row is still archived, because historical odds cannot be refetched
later at any price; it is simply not devigable, which :attr:`PropQuote.is_two_sided` makes
explicit at pricing time.

Payload shape (denormalised JSON:API-ish):

    players[]           id, first_name, last_name, sport_id
    appearances[]       id, player_id, match_id
    games[] / solo_games[]
    over_under_lines[]  stat_value, status, updated_at,
                        over_under.appearance_stat.{appearance_id, display_stat},
                        options[] {choice: higher|lower, american_price}
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from wnba_domain.enums import MarketKind, PropType
from wnba_domain.identity import SourceName, SourceRef
from wnba_domain.market import CONTINUOUS_PROP_TYPES, PropQuote

from wnba_services.ingestion.http import PoliteClient

__all__ = [
    "UNDERDOG_URL",
    "ParsedLine",
    "UnderdogAdapter",
    "map_stat_to_prop_type",
]

UNDERDOG_URL = "https://api.underdogfantasy.com/beta/v6/over_under_lines"

# Only markets we can actually model. First-half and fantasy-point variants are deliberately
# excluded: a market we cannot forecast is a market we should not archive as if we could.
_STAT_MAP: dict[str, PropType] = {
    "Points": PropType.POINTS,
    "Rebounds": PropType.REBOUNDS,
    "Assists": PropType.ASSISTS,
    "3-Pointers Made": PropType.THREE_POINTERS,
    "Steals": PropType.STEALS,
    "Blocks": PropType.BLOCKS,
    "Turnovers": PropType.TURNOVERS,
    "Free Throws Made": PropType.FREE_THROWS_MADE,
    "FG Attempted": PropType.FIELD_GOALS_MADE,
    "Points + Rebounds": PropType.POINTS_REBOUNDS,
    "Points + Assists": PropType.POINTS_ASSISTS,
    "Rebounds + Assists": PropType.REBOUNDS_ASSISTS,
    "Pts + Rebs + Asts": PropType.POINTS_REBOUNDS_ASSISTS,
    "Blocks + Steals": PropType.STEALS_BLOCKS,
    "Fantasy Points": PropType.FANTASY_POINTS,
}


def map_stat_to_prop_type(display_stat: str) -> PropType | None:
    """Map Underdog's label to our vocabulary. ``None`` means "deliberately not modelled"."""
    return _STAT_MAP.get(display_stat.strip())


class ParsedLine:
    """One parsed quote plus the source identity needed to resolve the player."""

    __slots__ = ("quote", "source_player_id", "source_player_name", "team")

    def __init__(
        self,
        quote: PropQuote,
        source_player_id: str,
        source_player_name: str,
        team: str | None,
    ) -> None:
        self.quote = quote
        self.source_player_id = source_player_id
        self.source_player_name = source_player_name
        self.team = team


def _to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_american(value: object) -> int | None:
    if value is None:
        return None
    try:
        odds = int(str(value).replace("+", ""))
    except (TypeError, ValueError):
        return None
    # Underdog occasionally emits values inside (-100, 100), which are not valid American
    # odds. Treat as absent rather than coercing them into something plausible.
    return odds if abs(odds) >= 100 else None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class UnderdogAdapter:
    """Fetch and parse Underdog's WNBA board."""

    source = SourceName.UNDERDOG

    def __init__(self, client: PoliteClient | None = None) -> None:
        self.client = client or PoliteClient(source="underdog")

    def fetch(self) -> Any:
        return self.client.get_json(UNDERDOG_URL)

    def parse(
        self,
        payload: dict[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> tuple[list[ParsedLine], list[str]]:
        """Return parsed WNBA quotes and a list of rejection reasons.

        Rejections are returned rather than logged and forgotten: the shape of what a source
        gets wrong is itself a signal, and it is what a quarantine record is made of.
        """
        system_from = observed_at or datetime.now(UTC)
        rejects: list[str] = []

        players = {p["id"]: p for p in payload.get("players", []) if isinstance(p, dict)}
        appearances = {a["id"]: a for a in payload.get("appearances", []) if isinstance(a, dict)}
        wnba_player_ids = {
            pid for pid, p in players.items() if str(p.get("sport_id", "")).upper() == "WNBA"
        }

        parsed: list[ParsedLine] = []
        for line in payload.get("over_under_lines", []):
            try:
                result = self._parse_line(line, players, appearances, wnba_player_ids, system_from)
            except Exception as exc:
                rejects.append(f"unparseable line {line.get('id', '?')}: {exc}")
                continue
            if isinstance(result, str):
                rejects.append(result)
            elif result is not None:
                parsed.append(result)

        return parsed, rejects

    def _parse_line(
        self,
        line: dict[str, Any],
        players: dict[str, Any],
        appearances: dict[str, Any],
        wnba_player_ids: set[str],
        system_from: datetime,
    ) -> ParsedLine | str | None:
        over_under = line.get("over_under") or {}
        appearance_stat = over_under.get("appearance_stat") or {}
        appearance_id = appearance_stat.get("appearance_id")
        appearance = appearances.get(str(appearance_id)) if appearance_id else None
        if appearance is None:
            return None

        player_id = appearance.get("player_id")
        if player_id not in wnba_player_ids:
            return None  # another sport; not our business

        player = players[player_id]
        display_stat = str(appearance_stat.get("display_stat", ""))
        prop_type = map_stat_to_prop_type(display_stat)
        if prop_type is None:
            return None  # not modelled, deliberately

        stat_value = _to_decimal(line.get("stat_value"))
        if stat_value is None:
            return f"line {line.get('id')}: unparseable stat_value {line.get('stat_value')!r}"
        # Precision discipline mirrors the DB constraint. Countable markets must land on
        # half points; fantasy points are continuous and legitimately quote 37.05, so they are
        # exempt. The PropQuote validator is the authority -- this is only an early reject so
        # the reason lands in quarantine with the row that caused it.
        if prop_type not in CONTINUOUS_PROP_TYPES and (stat_value * 2) % 1 != 0:
            return f"line {line.get('id')}: {stat_value} is not a half-point increment"

        prices: dict[str, int | None] = {"higher": None, "lower": None}
        for option in line.get("options") or []:
            choice = str(option.get("choice", "")).lower()
            if choice in prices:
                prices[choice] = _parse_american(option.get("american_price"))
        if prices["higher"] is None and prices["lower"] is None:
            return f"line {line.get('id')}: no usable American price on either side"

        updated_at = _parse_timestamp(line.get("updated_at")) or system_from
        name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        source_quote_id = str(line.get("id"))

        quote = PropQuote(
            # Deterministic id from (source, line id, observation instant): re-polling an
            # unchanged board cannot create a second row for the same snapshot.
            quote_id=uuid5(NAMESPACE_URL, f"underdog:{source_quote_id}:{system_from.isoformat()}"),
            source=SourceName.UNDERDOG,
            source_quote_id=source_quote_id,
            # Placeholder; the archiver replaces this with the resolved canonical id.
            player_id=uuid5(NAMESPACE_URL, f"underdog:player:{player_id}"),
            game_id=uuid5(NAMESPACE_URL, f"underdog:game:{appearance.get('match_id', 'unknown')}"),
            prop_type=prop_type,
            line=stat_value,
            market_kind=MarketKind.SPORTSBOOK_TWO_SIDED,
            over_american_odds=prices["higher"],
            under_american_odds=prices["lower"],
            is_available=str(line.get("status", "")).lower() == "active",
            source_ref=SourceRef(
                source=SourceName.UNDERDOG,
                source_entity_id=source_quote_id,
                retrieved_at=system_from,
                payload_sha256=hashlib.sha256(
                    json.dumps(line, sort_keys=True, default=str).encode()
                ).hexdigest(),
            ),
            # valid_from is when Underdog last moved the line; system_from is when we saw it.
            # The gap between them is precisely what a leakage-free backtest must respect.
            valid_from=updated_at,
            system_from=system_from,
        )
        return ParsedLine(
            quote=quote,
            source_player_id=str(player_id),
            source_player_name=name,
            team=appearance.get("team_id") or player.get("team_id"),
        )

    def iter_quotes(self) -> Iterator[ParsedLine]:
        payload = self.fetch()
        parsed, _ = self.parse(payload)
        yield from parsed
