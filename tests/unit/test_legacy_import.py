from __future__ import annotations

import sqlite3

import pytest
from wnba_services.ingestion.legacy import validate_legacy_schema


def _table(database: sqlite3.Connection, name: str, columns: list[str]) -> None:
    definitions = ",".join(f'"{column}" TEXT' for column in columns)
    database.execute(f'CREATE TABLE "{name}" ({definitions})')


def test_legacy_schema_refuses_missing_columns() -> None:
    database = sqlite3.connect(":memory:")
    _table(database, "player_box_scores", ["id"])
    _table(database, "team_box_scores", ["id"])
    _table(database, "player_props", ["id"])

    with pytest.raises(ValueError, match=r"player_box_scores.*missing columns"):
        validate_legacy_schema(database)


def test_reviewed_legacy_snapshot_schema_is_accepted() -> None:
    database = sqlite3.connect(":memory:")
    _table(
        database,
        "player_box_scores",
        [
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
    )
    _table(
        database,
        "team_box_scores",
        [
            "id",
            "team",
            "game_id",
            "date",
            "opponent",
            "pace",
            "offensive_efficiency",
            "defensive_efficiency",
        ],
    )
    _table(
        database,
        "player_props",
        [
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
    )

    validate_legacy_schema(database)
