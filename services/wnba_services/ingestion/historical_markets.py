"""Conservative normalization of rescued historical sportsbook quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from wnba_store.db import connect
from wnba_store.repositories import normalize_name

MARKET_MAP = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "three_pointers",
}


@dataclass(frozen=True)
class GameCandidate:
    game_id: UUID
    overlap: int
    scheduled_tipoff: datetime


@dataclass(frozen=True)
class HistoricalMarketBatch:
    events_resolved: int
    events_ambiguous: int
    events_unresolved: int
    quotes_normalized: int
    players_unresolved: int


def choose_game(
    candidates: list[GameCandidate], resolved_players: int
) -> tuple[UUID | None, float]:
    """Require multiple matching players and a uniquely best game candidate."""
    if resolved_players <= 0 or not candidates:
        return None, 0.0
    ordered = sorted(candidates, key=lambda row: (-row.overlap, row.scheduled_tipoff))
    best = ordered[0]
    if best.overlap < 2:
        return None, best.overlap / resolved_players
    if len(ordered) > 1 and ordered[1].overlap == best.overlap:
        return None, best.overlap / resolved_players
    return best.game_id, min(1.0, best.overlap / resolved_players)


def normalize_historical_markets() -> HistoricalMarketBatch:
    """Map legacy events by unique exact player identity and box-score overlap."""
    resolved = ambiguous = unresolved = quotes = unresolved_players = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT player_id,full_name FROM wnba.players")
        names: dict[str, list[UUID]] = {}
        for player in cur.fetchall():
            names.setdefault(normalize_name(str(player["full_name"])), []).append(
                UUID(str(player["player_id"]))
            )
        cur.execute(
            """SELECT source_event_id,min(observed_at) AS first_seen,max(observed_at) AS last_seen,
                      array_agg(DISTINCT player_name) AS player_names
               FROM wnba.legacy_prop_quotes WHERE source_event_id IS NOT NULL
               GROUP BY source_event_id ORDER BY first_seen"""
        )
        events = cur.fetchall()
        for event in events:
            player_ids: set[UUID] = set()
            player_names = event["player_names"]
            if not isinstance(player_names, list):
                raise ValueError("legacy event player names are not an array")
            for name in player_names:
                matches = names.get(normalize_name(str(name)), [])
                if len(matches) == 1:
                    player_ids.add(matches[0])
                else:
                    unresolved_players += 1
            candidates: list[GameCandidate] = []
            if player_ids:
                cur.execute(
                    """SELECT g.game_id,g.scheduled_tipoff,count(DISTINCT l.player_id) AS overlap
                       FROM wnba.games g
                       JOIN wnba.player_game_lines l ON l.game_id=g.game_id AND l.system_to IS NULL
                       WHERE l.player_id=ANY(%s) AND g.scheduled_tipoff>=%s
                         AND g.scheduled_tipoff<=%s + interval '72 hours'
                       GROUP BY g.game_id,g.scheduled_tipoff""",
                    (list(player_ids), event["last_seen"], event["last_seen"]),
                )
                candidates = [
                    GameCandidate(
                        UUID(str(row["game_id"])),
                        int(str(row["overlap"])),
                        row["scheduled_tipoff"],
                    )
                    for row in cur.fetchall()
                    if isinstance(row["scheduled_tipoff"], datetime)
                ]
            game_id, confidence = choose_game(candidates, len(player_ids))
            status = "resolved" if game_id is not None else "unresolved"
            if game_id is None and candidates and candidates[0].overlap >= 2:
                status = "ambiguous"
            cur.execute(
                """INSERT INTO wnba.legacy_event_mappings
                   (source_event_id,game_id,status,confidence,player_overlap,resolved_players,method)
                   VALUES (%s,%s,%s,%s,%s,%s,'unique-name-boxscore-overlap-v1')
                   ON CONFLICT (source_event_id) DO UPDATE SET
                     game_id=excluded.game_id,status=excluded.status,confidence=excluded.confidence,
                     player_overlap=excluded.player_overlap,resolved_players=excluded.resolved_players,
                     method=excluded.method,mapped_at=now()
                   WHERE wnba.legacy_event_mappings.reviewed_by IS NULL""",
                (
                    event["source_event_id"],
                    game_id,
                    status,
                    confidence,
                    0 if not candidates else max(row.overlap for row in candidates),
                    len(player_ids),
                ),
            )
            if status == "resolved":
                resolved += 1
            elif status == "ambiguous":
                ambiguous += 1
            else:
                unresolved += 1
        cur.execute(
            """SELECT q.*,m.game_id,m.confidence,g.scheduled_tipoff
               FROM wnba.legacy_prop_quotes q
               JOIN wnba.legacy_event_mappings m ON m.source_event_id=q.source_event_id
                 AND m.status='resolved'
               JOIN wnba.games g ON g.game_id=m.game_id
               WHERE q.observed_at<=g.scheduled_tipoff"""
        )
        for row in cur.fetchall():
            matches = names.get(normalize_name(str(row["player_name"])), [])
            prop_type = MARKET_MAP.get(str(row["market"]))
            if len(matches) != 1 or prop_type is None:
                continue
            cur.execute(
                """INSERT INTO wnba.historical_prop_quotes
                   (historical_quote_id,legacy_source_row_id,source_event_id,game_id,player_id,
                    bookmaker,prop_type,line,over_american_odds,under_american_odds,observed_at,
                    scheduled_tipoff,mapping_confidence)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (legacy_source_row_id) DO NOTHING""",
                (
                    uuid4(),
                    row["source_row_id"],
                    row["source_event_id"],
                    row["game_id"],
                    matches[0],
                    row["bookmaker"],
                    prop_type,
                    row["line"],
                    row["over_price"],
                    row["under_price"],
                    row["observed_at"],
                    row["scheduled_tipoff"],
                    row["confidence"],
                ),
            )
            quotes += max(0, cur.rowcount)
    return HistoricalMarketBatch(resolved, ambiguous, unresolved, quotes, unresolved_players)
