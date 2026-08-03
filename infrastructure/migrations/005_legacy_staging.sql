-- 005_legacy_staging.sql
-- Preserve the rescued CourtSide Edge SQLite archive exactly as received. These are staging
-- tables, not canonical facts: legacy box scores lack starter, offensive/defensive rebound,
-- foul, and plus-minus fields required by wnba.player_game_lines.

BEGIN;

CREATE TABLE IF NOT EXISTS wnba.legacy_import_runs (
    import_id       UUID PRIMARY KEY,
    source_path     TEXT NOT NULL,
    source_sha256   TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    player_rows     INTEGER NOT NULL DEFAULT 0,
    team_rows       INTEGER NOT NULL DEFAULT 0,
    quote_rows      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
    error           TEXT
);

CREATE TABLE IF NOT EXISTS wnba.legacy_player_box_scores (
    source_row_id          BIGINT PRIMARY KEY,
    source_player_id       TEXT NOT NULL,
    player_name            TEXT NOT NULL,
    source_game_id         TEXT NOT NULL,
    game_date              DATE NOT NULL,
    team_code              TEXT NOT NULL,
    opponent_code          TEXT NOT NULL,
    minutes                DOUBLE PRECISION,
    points                 INTEGER,
    assists                INTEGER,
    rebounds               INTEGER,
    steals                 INTEGER,
    blocks                 INTEGER,
    turnovers              INTEGER,
    field_goals_made       INTEGER,
    field_goals_attempted  INTEGER,
    threes_made            INTEGER,
    threes_attempted       INTEGER,
    free_throws_made       INTEGER,
    free_throws_attempted  INTEGER,
    usage_rate             DOUBLE PRECISION,
    offensive_rating       DOUBLE PRECISION,
    defensive_rating       DOUBLE PRECISION,
    imported_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT legacy_player_attempts CHECK (
        (field_goals_made IS NULL OR field_goals_attempted IS NULL
         OR field_goals_made <= field_goals_attempted)
        AND (threes_made IS NULL OR threes_attempted IS NULL OR threes_made <= threes_attempted)
        AND (free_throws_made IS NULL OR free_throws_attempted IS NULL
             OR free_throws_made <= free_throws_attempted)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS legacy_player_game_uq
    ON wnba.legacy_player_box_scores (source_game_id, source_player_id);
CREATE INDEX IF NOT EXISTS legacy_player_date_idx
    ON wnba.legacy_player_box_scores (game_date, source_player_id);

CREATE TABLE IF NOT EXISTS wnba.legacy_team_box_scores (
    source_row_id          BIGINT PRIMARY KEY,
    team_code              TEXT NOT NULL,
    source_game_id         TEXT NOT NULL,
    game_date              DATE NOT NULL,
    opponent_code          TEXT NOT NULL,
    pace                   DOUBLE PRECISION,
    offensive_efficiency   DOUBLE PRECISION,
    defensive_efficiency   DOUBLE PRECISION,
    imported_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS legacy_team_game_uq
    ON wnba.legacy_team_box_scores (source_game_id, team_code);

CREATE TABLE IF NOT EXISTS wnba.legacy_prop_quotes (
    source_row_id          BIGINT PRIMARY KEY,
    source_event_id        TEXT,
    bookmaker              TEXT NOT NULL,
    player_name            TEXT NOT NULL,
    market                 TEXT NOT NULL,
    line                   NUMERIC(7,2) NOT NULL,
    over_price             INTEGER,
    under_price            INTEGER,
    observed_at            TIMESTAMPTZ NOT NULL,
    imported_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS legacy_quotes_lookup_idx
    ON wnba.legacy_prop_quotes (player_name, market, observed_at);

COMMIT;
