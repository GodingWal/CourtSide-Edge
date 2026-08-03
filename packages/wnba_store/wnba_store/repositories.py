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
    "normalize_name",
    "quarantine_payload",
    "record_quotes",
    "register_player",
]


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
