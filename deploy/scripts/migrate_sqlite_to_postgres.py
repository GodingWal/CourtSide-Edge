#!/usr/bin/env python3
"""One-time copy of the SQLite ledger into PostgreSQL.

Usage (from the repo root, with the postgres service up and the web server
having run its migrations once so the web-owned tables exist):

    DATABASE_URL=postgresql://courtside:...@localhost:5432/courtside \
        python3 deploy/scripts/migrate_sqlite_to_postgres.py [path/to/hoopstats_wnba.db]

Idempotent: rows are inserted with ON CONFLICT DO NOTHING on the primary key,
so re-running after a partial migration only fills the gaps. Sequences for
serial ids are bumped past the copied maximum at the end.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from shared.db import AUTO_PK, IS_POSTGRES, connect  # noqa: E402

# (table, pk columns for conflict target, serial id to bump or None)
TABLES = [
    ("players", "id", None),
    ("settings", "key", None),
    ("bankroll_history", "id", "id"),
    ("bets", "id", "id"),
    ("qualitative_events", "id", "id"),
    ("agent_context_store", "id", "id"),
    ("decision_audit", "id", "id"),
    ("hedging_opportunities", "id", "id"),
    ("player_box_scores", "id", "id"),
    ("team_box_scores", "id", "id"),
    ("rolling_baselines", "player_id", None),
    ("etl_ingested_dates", "date", None),
    ("referee_game_log", "espn_id, referee", None),
]

# Agent-owned tables: created here when absent (the agents would create them
# on their next cycle anyway; doing it now lets their history copy over).
AGENT_DDL = [
    f"""CREATE TABLE IF NOT EXISTS player_box_scores (
        id {AUTO_PK}, player_id TEXT, player_name TEXT, game_id TEXT, date TEXT,
        team TEXT, opponent TEXT, minutes REAL, points INTEGER, assists INTEGER,
        rebounds INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER,
        field_goals_made INTEGER, field_goals_attempted INTEGER,
        threes_made INTEGER, threes_attempted INTEGER,
        free_throws_made INTEGER, free_throws_attempted INTEGER,
        usage_rate REAL, offensive_rating REAL, defensive_rating REAL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pbs_unique ON player_box_scores(game_id, player_id)",
    f"""CREATE TABLE IF NOT EXISTS team_box_scores (
        id {AUTO_PK}, team TEXT, game_id TEXT, date TEXT, opponent TEXT,
        pace REAL, offensive_efficiency REAL, defensive_efficiency REAL)""",
    """CREATE TABLE IF NOT EXISTS rolling_baselines (
        player_id TEXT PRIMARY KEY, player_name TEXT, last_updated TEXT,
        l5_minutes REAL, l5_usage_rate REAL, l10_usage_rate REAL,
        season_offensive_rating REAL, season_defensive_rating REAL)""",
    "CREATE TABLE IF NOT EXISTS etl_ingested_dates (date TEXT PRIMARY KEY, games INTEGER, ingested_at TEXT)",
    """CREATE TABLE IF NOT EXISTS referee_game_log (
        espn_id TEXT, referee TEXT, game_id TEXT, date TEXT,
        total_points INTEGER, total_fouls INTEGER, PRIMARY KEY (espn_id, referee))""",
]


def main() -> int:
    if not IS_POSTGRES:
        print("ERROR: set DATABASE_URL=postgresql://... before running.")
        return 1
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "data/hoopstats_wnba.db"
    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite database not found at {sqlite_path}")
        return 1

    src = sqlite3.connect(sqlite_path)
    dst = connect("ignored-in-postgres-mode")

    for ddl in AGENT_DDL:
        dst.execute(ddl)
    dst.commit()

    for table, conflict_cols, serial_col in TABLES:
        exists = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            print(f"— {table}: not in SQLite, skipping")
            continue

        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall()]
        col_list = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        rows = src.execute(f"SELECT {col_list} FROM {table}").fetchall()
        if not rows:
            print(f"— {table}: empty")
            continue

        dst.executemany(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_cols}) DO NOTHING",
            rows,
        )
        dst.commit()
        if serial_col:
            dst.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{serial_col}'), "
                f"(SELECT COALESCE(MAX({serial_col}), 0) + 1 FROM {table}), false)"
            )
            dst.commit()
        print(f"✓ {table}: {len(rows)} rows copied")

    src.close()
    dst.close()
    print("✅ Migration complete. Point DATABASE_URL at Postgres everywhere and restart the stack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
