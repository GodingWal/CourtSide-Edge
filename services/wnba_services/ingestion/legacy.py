"""One-way import of the rescued legacy SQLite archive into immutable staging tables.

The import deliberately does not manufacture canonical facts. The legacy player rows have
total rebounds but no offensive/defensive split, no starter flag, no fouls, and no plus-minus.
Filling those fields with zero would make the strict schema pass while poisoning training data.
Instead, every source value is preserved here and ESPN backfill supplies complete canonical
box scores separately.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from wnba_store.db import connect

__all__ = ["LegacyImportResult", "import_legacy_sqlite", "validate_legacy_schema"]

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "player_box_scores": {
        "id",
        "player_id",
        "player_name",
        "game_id",
        "date",
        "team",
        "opponent",
        "minutes",
        "points",
        "assists",
        "rebounds",
        "steals",
        "blocks",
        "turnovers",
        "field_goals_made",
        "field_goals_attempted",
        "threes_made",
        "threes_attempted",
        "free_throws_made",
        "free_throws_attempted",
        "usage_rate",
        "offensive_rating",
        "defensive_rating",
    },
    "team_box_scores": {
        "id",
        "team",
        "game_id",
        "date",
        "opponent",
        "pace",
        "offensive_efficiency",
        "defensive_efficiency",
    },
    "player_props": {
        "id",
        "event_id",
        "bookmaker",
        "player",
        "market",
        "line",
        "over_price",
        "under_price",
        "updated_at",
    },
}


@dataclass(frozen=True)
class LegacyImportResult:
    import_id: str
    source_sha256: str
    player_rows: int
    team_rows: int
    quote_rows: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_legacy_schema(database: sqlite3.Connection) -> None:
    """Refuse an archive whose shape differs from the one we reviewed."""
    for table, required in _REQUIRED_COLUMNS.items():
        actual = {str(row[1]) for row in database.execute(f'PRAGMA table_info("{table}")')}
        missing = required - actual
        if missing:
            raise ValueError(f"legacy table {table!r} is missing columns: {sorted(missing)}")


def _rows(database: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    if table not in _REQUIRED_COLUMNS:
        raise ValueError(f"unapproved legacy table {table!r}")
    columns = list(_REQUIRED_COLUMNS[table])
    # Stable explicit order makes the SQL below independent of set ordering.
    ordered = {
        "player_box_scores": [
            "id",
            "player_id",
            "player_name",
            "game_id",
            "date",
            "team",
            "opponent",
            "minutes",
            "points",
            "assists",
            "rebounds",
            "steals",
            "blocks",
            "turnovers",
            "field_goals_made",
            "field_goals_attempted",
            "threes_made",
            "threes_attempted",
            "free_throws_made",
            "free_throws_attempted",
            "usage_rate",
            "offensive_rating",
            "defensive_rating",
        ],
        "team_box_scores": [
            "id",
            "team",
            "game_id",
            "date",
            "opponent",
            "pace",
            "offensive_efficiency",
            "defensive_efficiency",
        ],
        "player_props": [
            "id",
            "event_id",
            "bookmaker",
            "player",
            "market",
            "line",
            "over_price",
            "under_price",
            "updated_at",
        ],
    }[table]
    if set(columns) != set(ordered):  # defensive check if the declarations drift
        raise AssertionError(f"legacy column declaration drift for {table}")
    selected = ",".join(f'"{column}"' for column in ordered)
    return [tuple(row) for row in database.execute(f'SELECT {selected} FROM "{table}"')]


def import_legacy_sqlite(path: Path, database_url: str | None = None) -> LegacyImportResult:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    source_hash = _sha256(path)
    import_id = uuid4()
    started = datetime.now(UTC)

    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as legacy:
        validate_legacy_schema(legacy)
        players = _rows(legacy, "player_box_scores")
        teams = _rows(legacy, "team_box_scores")
        quotes = _rows(legacy, "player_props")

    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO wnba.legacy_import_runs
                   (import_id,source_path,source_sha256,started_at,status)
                   VALUES (%s,%s,%s,%s,'running')""",
                (import_id, str(path), source_hash, started),
            )
            cur.executemany(
                """INSERT INTO wnba.legacy_player_box_scores VALUES
                   (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (source_row_id) DO NOTHING""",
                players,
            )
            cur.executemany(
                """INSERT INTO wnba.legacy_team_box_scores VALUES
                   (%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (source_row_id) DO NOTHING""",
                teams,
            )
            cur.executemany(
                """INSERT INTO wnba.legacy_prop_quotes VALUES
                   (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (source_row_id) DO NOTHING""",
                quotes,
            )
            cur.execute(
                """UPDATE wnba.legacy_import_runs SET completed_at=now(), status='complete',
                       player_rows=%s, team_rows=%s, quote_rows=%s
                   WHERE import_id=%s""",
                (len(players), len(teams), len(quotes), import_id),
            )
        conn.commit()

    return LegacyImportResult(
        import_id=str(import_id),
        source_sha256=source_hash,
        player_rows=len(players),
        team_rows=len(teams),
        quote_rows=len(quotes),
    )
