"""Write paths for the bitemporal store.

One rule governs this module: **rows are appended, never updated**. A correction closes the
previous row on the system axis and inserts a new one, so "what did we believe at 15:00?"
stays answerable forever.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from wnba_domain.identity import SourceName
from wnba_domain.market import PropQuote

__all__ = [
    "enrich_quote_game_ids",
    "normalize_name",
    "quarantine_payload",
    "record_quotes",
    "register_market_game",
    "register_player",
]

_TEAM_ZONE_BY_ABBREVIATION: dict[str, str] = {
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


# Sources spell the same franchise differently: Underdog says LVA, ESPN says LV. Looking a
# team up by raw abbreviation therefore mints a duplicate franchise -- which is how three
# stub teams (LVA/NYL/GSV, each with full_name equal to its abbreviation) came to exist
# alongside the real ones, splitting one club's identity across two UUIDs.
#
# The canonical spelling is ESPN's, because that is where the actual game history lives.
_CANONICAL_ABBREVIATION: dict[str, str] = {
    "LVA": "LV",
    "NYL": "NY",
    "GSV": "GS",
    "WAS": "WSH",
    "LAS": "LA",
    "LAX": "LA",
    "PHO": "PHX",
    "CONN": "CON",
}


def canonical_team_abbreviation(abbreviation: str) -> str:
    """Fold a source's spelling onto the canonical one."""
    code = abbreviation.upper().strip()
    return _CANONICAL_ABBREVIATION.get(code, code)


def _register_market_team(
    conn: psycopg.Connection[Any], *, source: SourceName, abbreviation: str, observed_at: datetime
) -> UUID:
    raw = abbreviation.upper().strip()
    code = canonical_team_abbreviation(raw)
    zone = _TEAM_ZONE_BY_ABBREVIATION.get(code) or _TEAM_ZONE_BY_ABBREVIATION.get(raw)
    if zone is None:
        raise ValueError(f"unknown market team abbreviation {raw!r}")
    with conn.cursor() as cur:
        # Resolve through an existing alias binding first. The alias table was already being
        # written here but never read, so the one structure that could have prevented the
        # duplicate was being maintained and ignored.
        cur.execute(
            """SELECT team_id FROM wnba.team_aliases
               WHERE source=%s AND source_team_id=%s AND system_to IS NULL LIMIT 1""",
            (source.value, raw),
        )
        existing = cur.fetchone()
        if existing is None:
            cur.execute("SELECT team_id FROM wnba.teams WHERE abbreviation=%s", (code,))
            existing = cur.fetchone()
        team_id = (
            UUID(str(existing["team_id"]))
            if existing
            else uuid5(NAMESPACE_URL, f"{source.value}:team:{code}")
        )
        if existing is None:
            cur.execute(
                """INSERT INTO wnba.teams (team_id,abbreviation,full_name,market,time_zone)
                   VALUES (%s,%s,%s,%s,%s)""",
                (team_id, code, code, code, zone),
            )
        # Record the binding under the spelling the SOURCE used, not the canonical one --
        # that is the key the resolver above looks up, and storing the canonical form here
        # would leave the alias permanently unable to match its own source.
        cur.execute(
            """INSERT INTO wnba.team_aliases
               (alias_id,team_id,source,source_team_id,source_display_name,
                valid_from,system_from)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (uuid4(), team_id, source.value, raw, raw, observed_at, observed_at),
        )
    return team_id


def register_market_game(
    conn: psycopg.Connection[Any],
    *,
    source: SourceName,
    source_game_id: str,
    scheduled_at: datetime,
    away_abbreviation: str,
    home_abbreviation: str,
    observed_at: datetime,
) -> UUID:
    """Bind a market game to an existing schedule row, or enroll it before ESPN sees it."""
    home_id = _register_market_team(
        conn, source=source, abbreviation=home_abbreviation, observed_at=observed_at
    )
    away_id = _register_market_team(
        conn, source=source, abbreviation=away_abbreviation, observed_at=observed_at
    )
    with conn.cursor() as cur:
        cur.execute(
            """SELECT game_id FROM wnba.games
               WHERE home_team_id=%s AND away_team_id=%s
                 AND scheduled_tipoff BETWEEN %s - interval '10 minutes'
                                          AND %s + interval '10 minutes'
               ORDER BY abs(extract(epoch FROM scheduled_tipoff-%s)) LIMIT 1""",
            (home_id, away_id, scheduled_at, scheduled_at, scheduled_at),
        )
        existing = cur.fetchone()
        game_id = (
            UUID(str(existing["game_id"]))
            if existing
            else uuid5(NAMESPACE_URL, f"{source.value}:game:{source_game_id}")
        )
        if existing is None:
            cur.execute(
                """INSERT INTO wnba.games
                   (game_id,season_year,scheduled_tipoff,home_team_id,away_team_id,status)
                   VALUES (%s,%s,%s,%s,%s,'scheduled') ON CONFLICT DO NOTHING""",
                (game_id, scheduled_at.year, scheduled_at, home_id, away_id),
            )
        cur.execute(
            """INSERT INTO wnba.game_aliases
               (alias_id,game_id,source,source_game_id,system_from)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (uuid4(), game_id, source.value, source_game_id, observed_at),
        )
    return game_id


def enrich_quote_game_ids(
    conn: psycopg.Connection[Any], mappings: list[tuple[UUID, datetime, str]]
) -> int:
    """Fill identity metadata on older rows without changing their observed market state."""
    changed = 0
    with conn.cursor() as cur:
        for game_id, locks_at, source_quote_id in mappings:
            cur.execute(
                """UPDATE wnba.prop_quotes SET game_id=%s, locks_at=%s
                   WHERE source='underdog' AND source_quote_id=%s
                     AND (game_id IS NULL OR locks_at IS NULL)""",
                (game_id, locks_at, source_quote_id),
            )
            changed += max(0, cur.rowcount)
    return changed


def normalize_name(name: str) -> str:
    """Case-folded, accent-stripped form used for candidate lookup only.

    Never for automatic cross-source binding. Deciding that "A. Wilson" from one feed is the
    same athlete as "A'ja Wilson" from another is a judgement, and a wrong one silently
    fabricates a player's history.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().replace(".", "").replace("'", "").split())


def register_player(
    conn: psycopg.Connection[Any],
    *,
    source: SourceName,
    source_player_id: str,
    display_name: str,
    observed_at: datetime,
) -> UUID:
    """Resolve a source's player to a canonical id, registering on first sight.

    Registering a new canonical player from an **exact source id** is not the fuzzy matching
    the identity invariant forbids -- it is enrolment, and it is safe. What stays forbidden is
    merging two *different* sources' names into one entity on a similarity score. Those
    bindings are left for a human, which is why ``verified_by`` starts NULL.
    """
    player_id = uuid5(NAMESPACE_URL, f"{source.value}:player:{source_player_id}")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT player_id FROM wnba.player_aliases
            WHERE source = %s AND source_player_id = %s AND system_to IS NULL
            """,
            (source.value, source_player_id),
        )
        row = cur.fetchone()
        if row is not None:
            return UUID(str(row["player_id"]))

        cur.execute(
            """
            INSERT INTO wnba.players (player_id, full_name)
            VALUES (%s, %s) ON CONFLICT (player_id) DO NOTHING
            """,
            (player_id, display_name),
        )
        cur.execute(
            """
            INSERT INTO wnba.player_aliases (
                alias_id, player_id, source, source_player_id, source_display_name,
                normalized_name, confidence, verified_by, valid_from, system_from)
            VALUES (%s, %s, %s, %s, %s, %s, 1.0, NULL, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                uuid4(),
                player_id,
                source.value,
                source_player_id,
                display_name,
                normalize_name(display_name),
                observed_at,
                observed_at,
            ),
        )
    return player_id


def record_quotes(
    conn: psycopg.Connection[Any],
    quotes: list[PropQuote],
) -> int:
    """Append quote snapshots. Returns the number actually inserted.

    ``ON CONFLICT DO NOTHING`` is backed by two database constraints. One protects a single
    observation instant; the other identifies the source state using its quote id, source
    update time, line, prices and availability. Therefore an unchanged board re-polled later
    does not masquerade as line movement, while a real source update remains append-only.
    """
    if not quotes:
        return 0

    rows = [
        (
            q.quote_id,
            q.source.value,
            q.source_quote_id,
            q.player_id,
            q.game_id,
            q.prop_type.value,
            q.line,
            q.market_kind.value,
            q.locks_at,
            q.over_american_odds,
            q.under_american_odds,
            q.over_multiplier,
            q.under_multiplier,
            q.is_promotional,
            q.is_available,
            q.valid_from,
            q.system_from,
        )
        for q in quotes
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO wnba.prop_quotes (
                quote_id, source, source_quote_id, player_id, game_id, prop_type, line,
                market_kind, locks_at, over_american_odds, under_american_odds,
                over_multiplier, under_multiplier, is_promotional, is_available,
                valid_from, system_from)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return max(0, cur.rowcount)


def quarantine_payload(
    conn: psycopg.Connection[Any],
    *,
    source: SourceName,
    payload: object,
    errors: list[str],
    validation_level: str = "schema",
) -> UUID:
    """Preserve a rejected payload verbatim.

    Quarantined data is kept, not discarded. What a source gets wrong is a signal in its own
    right, and it is the only way to fix a parser without waiting for the bug to recur.
    """
    raw = json.dumps(payload, default=str, sort_keys=True)[:200_000]
    quarantine_id = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wnba.quarantine (
                quarantine_id, source, raw_payload, payload_sha256,
                validation_level, validation_errors)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                quarantine_id,
                source.value,
                raw,
                hashlib.sha256(raw.encode()).hexdigest(),
                validation_level,
                errors[:50],
            ),
        )
    return quarantine_id
