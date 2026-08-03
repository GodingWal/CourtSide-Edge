"""Resumable ESPN schedule and complete box-score ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from wnba_domain.basketball import PlayerGameLine
from wnba_domain.identity import SourceName
from wnba_store.db import connect
from wnba_store.repositories import quarantine_payload, register_player

from wnba_services.ingestion.adapters.espn import EspnGame, EspnTeam, EspnWnbaAdapter

__all__ = ["EspnIngestResult", "backfill_espn", "ingest_espn_date"]

_TEAM_ZONES: dict[str, str] = {
    "ATL": "America/New_York",
    "CHI": "America/Chicago",
    "CON": "America/New_York",
    "DAL": "America/Chicago",
    "GS": "America/Los_Angeles",
    "GSV": "America/Los_Angeles",
    "IND": "America/Indiana/Indianapolis",
    "LA": "America/Los_Angeles",
    "LAS": "America/Los_Angeles",
    "LAX": "America/Los_Angeles",
    "LV": "America/Los_Angeles",
    "LVA": "America/Los_Angeles",
    "MIN": "America/Chicago",
    "NY": "America/New_York",
    "NYL": "America/New_York",
    "PHX": "America/Phoenix",
    "POR": "America/Los_Angeles",
    "SEA": "America/Los_Angeles",
    "TOR": "America/Toronto",
    "WAS": "America/New_York",
    "WSH": "America/New_York",
    "CLE": "America/New_York",
}


@dataclass(frozen=True)
class EspnIngestResult:
    game_date: date
    games: int
    player_lines: int
    skipped: bool = False


def _team_id(source_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"espn:team:{source_id}")


def _game_id(source_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"espn:game:{source_id}")


def _register_team(conn: psycopg.Connection[Any], team: EspnTeam, observed_at: datetime) -> UUID:
    team_id = _team_id(team.source_id)
    zone = _TEAM_ZONES.get(team.abbreviation)
    if zone is None:
        raise ValueError(
            f"unknown WNBA team abbreviation {team.abbreviation!r}; add its verified IANA zone"
        )
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.teams (team_id,abbreviation,full_name,market,time_zone)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (team_id) DO UPDATE SET full_name=excluded.full_name,
                   market=excluded.market, time_zone=excluded.time_zone""",
            (team_id, team.abbreviation, team.full_name, team.market, zone),
        )
        cur.execute(
            """INSERT INTO wnba.team_aliases
               (alias_id,team_id,source,source_team_id,source_display_name,
                valid_from,system_from)
               VALUES (%s,%s,'espn',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (uuid4(), team_id, team.source_id, team.full_name, observed_at, observed_at),
        )
    return team_id


def _register_game(
    conn: psycopg.Connection[Any], game: EspnGame, observed_at: datetime
) -> tuple[UUID, dict[str, UUID]]:
    home_id = _register_team(conn, game.home, observed_at)
    away_id = _register_team(conn, game.away, observed_at)
    game_id = _game_id(game.source_id)
    overtime_periods = max(0, game.period - 4) if game.status == "final" else 0
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.games
               (game_id,season_year,scheduled_tipoff,home_team_id,away_team_id,status,
                overtime_periods,home_points,away_points)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (game_id) DO UPDATE SET
                 scheduled_tipoff=excluded.scheduled_tipoff,status=excluded.status,
                 overtime_periods=excluded.overtime_periods,home_points=excluded.home_points,
                 away_points=excluded.away_points""",
            (
                game_id,
                game.scheduled_tipoff.year,
                game.scheduled_tipoff,
                home_id,
                away_id,
                game.status,
                overtime_periods,
                game.home_points,
                game.away_points,
            ),
        )
        cur.execute(
            """INSERT INTO wnba.game_aliases
               (alias_id,game_id,source,source_game_id,system_from)
               VALUES (%s,%s,'espn',%s,%s) ON CONFLICT DO NOTHING""",
            (uuid4(), game_id, game.source_id, observed_at),
        )
    return game_id, {game.home.source_id: home_id, game.away.source_id: away_id}


def _store_payload(
    conn: psycopg.Connection[Any],
    *,
    endpoint: str,
    entity_id: str,
    payload: dict[str, Any],
    observed_at: datetime,
) -> None:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.source_payloads
               (payload_id,source,endpoint,source_entity_id,retrieved_at,payload_sha256,payload)
               VALUES (%s,'espn',%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
            (uuid4(), endpoint, entity_id, observed_at, digest, serialized),
        )


def _write_line(
    conn: psycopg.Connection[Any],
    line: PlayerGameLine,
    *,
    source_event_id: str,
    observed_at: datetime,
) -> bool:
    values = (
        line.player_id,
        line.game_id,
        line.team_id,
        line.minutes,
        line.started,
        line.points,
        line.rebounds_offensive,
        line.rebounds_defensive,
        line.assists,
        line.steals,
        line.blocks,
        line.turnovers,
        line.personal_fouls,
        line.field_goals_made,
        line.field_goals_attempted,
        line.three_pointers_made,
        line.three_pointers_attempted,
        line.free_throws_made,
        line.free_throws_attempted,
        line.plus_minus,
    )
    with conn.cursor() as cur:
        cur.execute(
            """SELECT team_id,minutes,started,points,rebounds_offensive,rebounds_defensive,
                      assists,steals,blocks,turnovers,personal_fouls,field_goals_made,
                      field_goals_attempted,three_pointers_made,three_pointers_attempted,
                      free_throws_made,free_throws_attempted,plus_minus
               FROM wnba.player_game_lines
               WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
            (line.player_id, line.game_id),
        )
        current = cur.fetchone()
        comparable = values[2:]
        if current is not None and tuple(current.values()) == comparable:
            return False
        if current is not None:
            cur.execute(
                """UPDATE wnba.player_game_lines SET system_to=%s
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                (observed_at, line.player_id, line.game_id),
            )
        cur.execute(
            """INSERT INTO wnba.player_game_lines
               (player_id,game_id,team_id,minutes,started,points,rebounds_offensive,
                rebounds_defensive,assists,steals,blocks,turnovers,personal_fouls,
                field_goals_made,field_goals_attempted,three_pointers_made,
                three_pointers_attempted,free_throws_made,free_throws_attempted,plus_minus,
                source,ingested_at,source_event_id,system_from)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       'espn',%s,%s,%s)""",
            (*values, observed_at, source_event_id, observed_at),
        )
    return True


def ingest_espn_date(
    game_date: date,
    database_url: str | None = None,
    *,
    force: bool = False,
    adapter: EspnWnbaAdapter | None = None,
) -> EspnIngestResult:
    feed = adapter or EspnWnbaAdapter()
    with connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM wnba.ingested_dates
               WHERE source='espn' AND feed='boxscore' AND game_date=%s""",
            (game_date,),
        )
        if cur.fetchone() and not force:
            return EspnIngestResult(game_date, 0, 0, skipped=True)

    observed_at = datetime.now(UTC)
    scoreboard = feed.fetch_scoreboard(game_date)
    # ESPN includes national-team exhibitions and All-Star events in the WNBA scoreboard.
    # They do not belong in franchise player-prop training and often use pseudo-team codes
    # such as PUERTORICO or WNBASTARS, so require both teams to have a verified home zone.
    games = [
        game
        for game in feed.parse_scoreboard(scoreboard)
        if game.status == "final"
        and game.home.abbreviation in _TEAM_ZONES
        and game.away.abbreviation in _TEAM_ZONES
    ]
    written = 0

    with connect(database_url) as conn:
        try:
            _store_payload(
                conn,
                endpoint="scoreboard",
                entity_id=game_date.isoformat(),
                payload=scoreboard,
                observed_at=observed_at,
            )
            for game in games:
                summary = feed.fetch_summary(game.source_id)
                _store_payload(
                    conn,
                    endpoint="summary",
                    entity_id=game.source_id,
                    payload=summary,
                    observed_at=observed_at,
                )
                game_id, team_ids = _register_game(conn, game, observed_at)
                for raw in feed.parse_summary(summary):
                    player_id = register_player(
                        conn,
                        source=SourceName.ESPN,
                        source_player_id=raw.source_player_id,
                        display_name=raw.player_name,
                        observed_at=observed_at,
                    )
                    team_id = team_ids.get(raw.team_source_id)
                    if team_id is None:
                        raise ValueError(
                            f"summary player {raw.player_name!r} references unknown team "
                            f"{raw.team_source_id!r}"
                        )
                    line = PlayerGameLine(
                        player_id=player_id,
                        game_id=game_id,
                        team_id=team_id,
                        minutes=raw.minutes,
                        started=raw.started,
                        points=raw.points,
                        rebounds_offensive=raw.rebounds_offensive,
                        rebounds_defensive=raw.rebounds_defensive,
                        assists=raw.assists,
                        steals=raw.steals,
                        blocks=raw.blocks,
                        turnovers=raw.turnovers,
                        personal_fouls=raw.personal_fouls,
                        field_goals_made=raw.field_goals_made,
                        field_goals_attempted=raw.field_goals_attempted,
                        three_pointers_made=raw.three_pointers_made,
                        three_pointers_attempted=raw.three_pointers_attempted,
                        free_throws_made=raw.free_throws_made,
                        free_throws_attempted=raw.free_throws_attempted,
                        plus_minus=raw.plus_minus,
                    )
                    written += _write_line(
                        conn, line, source_event_id=game.source_id, observed_at=observed_at
                    )
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO wnba.ingested_dates
                       (source,feed,game_date,games,rows_written,completed_at)
                       VALUES ('espn','boxscore',%s,%s,%s,%s)
                       ON CONFLICT (source,feed,game_date) DO UPDATE SET
                         games=excluded.games,rows_written=excluded.rows_written,
                         completed_at=excluded.completed_at""",
                    (game_date, len(games), written, observed_at),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            quarantine_payload(
                conn,
                source=SourceName.ESPN,
                payload={"date": game_date.isoformat(), "scoreboard": scoreboard},
                errors=[str(exc)],
                validation_level="cross_source",
            )
            conn.commit()
            raise

    return EspnIngestResult(game_date, len(games), written)


def backfill_espn(
    start: date, end: date, database_url: str | None = None, *, force: bool = False
) -> list[EspnIngestResult]:
    if end < start:
        raise ValueError("end date precedes start date")
    adapter = EspnWnbaAdapter()
    results: list[EspnIngestResult] = []
    current = start
    while current <= end:
        results.append(ingest_espn_date(current, database_url, force=force, adapter=adapter))
        current += timedelta(days=1)
    return results
