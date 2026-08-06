"""Bitemporal ingestion of the latest official WNBA injury report."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from wnba_store.db import connect
from wnba_store.repositories import normalize_name

from wnba_services.ingestion.adapters.wnba_injuries import (
    INDEX_URL,
    OfficialInjury,
    latest_report,
    parse_report,
)
from wnba_services.ingestion.http import PoliteClient

STATUS_MAP = {
    "out": "out",
    "questionable": "questionable",
    "doubtful": "doubtful",
    "probable": "probable",
    "available": "available",
}

# The official report prints its own team abbreviations, which differ from the ones the
# schedule feed uses for the same franchises. Every report row resolved to zero games until
# this map existed -- LVA@IND never matches the LV@IND row in wnba.games.
TEAM_ABBR_MAP = {
    "LVA": "LV",
    "LAS": "LA",
    "PHO": "PHX",
    "NYL": "NY",
    "WAS": "WSH",
    "GSV": "GS",
    "PDX": "POR",
}


@dataclass(frozen=True)
class InjuryIngestResult:
    report_url: str
    parsed: int
    inserted: int
    unchanged: int
    unresolved: int


def _resolve_player(cur: psycopg.Cursor[dict[str, Any]], injury: OfficialInjury) -> UUID | None:
    cur.execute(
        """SELECT player_id FROM wnba.player_aliases
           WHERE source='espn' AND normalized_name=%s AND system_to IS NULL""",
        (normalize_name(injury.player_name),),
    )
    rows = cur.fetchall()
    return UUID(str(rows[0]["player_id"])) if len(rows) == 1 else None


def _resolve_game(cur: psycopg.Cursor[dict[str, Any]], injury: OfficialInjury) -> UUID | None:
    teams = injury.matchup.split("@", maxsplit=1)
    # The league PDF occasionally yields an empty game date (see the adapter's `_context`,
    # which returns "" when a row falls outside the expected word columns). Binding "" into
    # the date parameter below raised InvalidDatetimeFormat and aborted the whole batch --
    # one malformed row cost every good row in the report. A row without a date is
    # unresolvable, and unresolved rows already have a home: entity_resolution_failures.
    if len(teams) != 2 or not injury.game_date.strip():
        return None
    away, home = (TEAM_ABBR_MAP.get(team.strip(), team.strip()) for team in teams)
    cur.execute(
        """SELECT g.game_id FROM wnba.games g
           JOIN wnba.teams a ON a.team_id=g.away_team_id
           JOIN wnba.teams h ON h.team_id=g.home_team_id
           WHERE (g.scheduled_tipoff AT TIME ZONE 'America/New_York')::date=%s::date
             AND a.abbreviation=%s AND h.abbreviation=%s
           ORDER BY g.scheduled_tipoff LIMIT 2""",
        (injury.game_date, away, home),
    )
    rows = cur.fetchall()
    return UUID(str(rows[0]["game_id"])) if len(rows) == 1 else None


def ingest_official_injuries(*, client: PoliteClient | None = None) -> InjuryIngestResult:
    feed = client or PoliteClient("wnba-official-injuries", min_interval_seconds=1)
    index = feed.get_json(INDEX_URL)
    if not isinstance(index, dict):
        raise ValueError("official injury index must be an object")
    report_url, label = latest_report(index)
    document = feed.get_bytes(report_url, accept="application/pdf")
    digest = hashlib.sha256(document).hexdigest()
    observed = datetime.now(UTC)
    injuries = parse_report(document)
    inserted = unchanged = unresolved = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.official_injury_reports
               (report_id,source_url,report_label,report_sha256,retrieved_at,document_bytes)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (report_sha256) DO NOTHING""",
            (uuid4(), report_url, label, digest, observed, document),
        )
        for injury in injuries:
            player_id = _resolve_player(cur, injury)
            game_id = _resolve_game(cur, injury)
            designation = STATUS_MAP.get(injury.status.casefold(), "unknown")
            if player_id is None or game_id is None:
                unresolved += 1
                cur.execute(
                    """INSERT INTO wnba.entity_resolution_failures
                       (failure_id,source,source_entity_id,source_display_name,entity_kind,
                        observed_at,context)
                       VALUES (%s,'wnba_official',%s,%s,'player',%s,%s)""",
                    (
                        uuid4(),
                        f"{injury.game_date}:{injury.matchup}:{injury.player_name}",
                        injury.player_name,
                        observed,
                        f"game={injury.matchup}; team={injury.team_name}; status={injury.status}",
                    ),
                )
                continue
            cur.execute(
                """SELECT designation,detail FROM wnba.injury_status
                   WHERE player_id=%s AND game_id=%s AND source='wnba_official'
                     AND system_to IS NULL ORDER BY system_from DESC LIMIT 1""",
                (player_id, game_id),
            )
            current = cur.fetchone()
            if (
                current
                and current["designation"] == designation
                and current["detail"] == injury.reason
            ):
                unchanged += 1
                continue
            if current:
                cur.execute(
                    """UPDATE wnba.injury_status SET system_to=%s
                       WHERE player_id=%s AND game_id=%s AND source='wnba_official'
                       AND system_to IS NULL""",
                    (observed, player_id, game_id),
                )
            cur.execute(
                """INSERT INTO wnba.injury_status
                   (record_id,player_id,game_id,designation,detail,source,source_ref,
                    valid_from,system_from)
                   VALUES (%s,%s,%s,%s,%s,'wnba_official',%s,%s,%s)""",
                (
                    uuid4(),
                    player_id,
                    game_id,
                    designation,
                    injury.reason,
                    report_url,
                    observed,
                    observed,
                ),
            )
            inserted += 1
    return InjuryIngestResult(report_url, len(injuries), inserted, unchanged, unresolved)
