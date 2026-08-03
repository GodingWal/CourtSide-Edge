-- 006_espn_ingestion.sql
-- Canonical ESPN identity, raw lineage, resumable date ingestion, and append-only box-score
-- corrections. Applied while player_game_lines is empty in the Phase 1 deployment.

BEGIN;

CREATE TABLE IF NOT EXISTS wnba.team_aliases (
    alias_id            UUID PRIMARY KEY,
    team_id             UUID NOT NULL REFERENCES wnba.teams(team_id),
    source              wnba.source_name NOT NULL,
    source_team_id      TEXT NOT NULL,
    source_display_name TEXT NOT NULL,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    system_from         TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to           TIMESTAMPTZ,
    CONSTRAINT team_alias_valid_range CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT team_alias_system_range CHECK (system_to IS NULL OR system_to > system_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS team_aliases_current_uq
    ON wnba.team_aliases (source, source_team_id)
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE TABLE IF NOT EXISTS wnba.game_aliases (
    alias_id        UUID PRIMARY KEY,
    game_id         UUID NOT NULL REFERENCES wnba.games(game_id),
    source          wnba.source_name NOT NULL,
    source_game_id  TEXT NOT NULL,
    system_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to       TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS game_aliases_current_uq
    ON wnba.game_aliases (source, source_game_id) WHERE system_to IS NULL;

CREATE TABLE IF NOT EXISTS wnba.source_payloads (
    payload_id       UUID PRIMARY KEY,
    source           wnba.source_name NOT NULL,
    endpoint         TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    payload_sha256   TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload          JSONB NOT NULL,
    UNIQUE (source, endpoint, source_entity_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS wnba.ingested_dates (
    source       wnba.source_name NOT NULL,
    feed         TEXT NOT NULL,
    game_date    DATE NOT NULL,
    games        INTEGER NOT NULL CHECK (games >= 0),
    rows_written INTEGER NOT NULL CHECK (rows_written >= 0),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, feed, game_date)
);

-- Official scoring corrections must not rewrite the outcome we originally observed.
ALTER TABLE wnba.player_game_lines DROP CONSTRAINT player_game_lines_pkey;
ALTER TABLE wnba.player_game_lines
    ADD COLUMN line_id UUID NOT NULL DEFAULT gen_random_uuid(),
    ADD COLUMN source_event_id TEXT NOT NULL DEFAULT '',
    ADD COLUMN system_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN system_to TIMESTAMPTZ,
    ADD CONSTRAINT player_game_lines_pkey PRIMARY KEY (line_id),
    ADD CONSTRAINT player_game_lines_system_range
        CHECK (system_to IS NULL OR system_to > system_from);
CREATE UNIQUE INDEX IF NOT EXISTS player_game_lines_current_uq
    ON wnba.player_game_lines (player_id, game_id) WHERE system_to IS NULL;

COMMIT;
