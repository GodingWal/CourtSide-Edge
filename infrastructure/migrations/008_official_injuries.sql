BEGIN;

ALTER DOMAIN wnba.source_name DROP CONSTRAINT source_name_check;
ALTER DOMAIN wnba.source_name ADD CONSTRAINT source_name_check
    CHECK (VALUE IN ('espn','stats_wnba','wehoop','pbpstats','prizepicks','underdog',
                     'video','manual','derived','legacy_courtside','wnba_official'));

CREATE TABLE IF NOT EXISTS wnba.official_injury_reports (
    report_id       UUID PRIMARY KEY,
    source_url      TEXT NOT NULL,
    report_label    TEXT NOT NULL,
    report_sha256   TEXT NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at    TIMESTAMPTZ NOT NULL,
    document_bytes  BYTEA NOT NULL,
    UNIQUE (report_sha256)
);

CREATE INDEX IF NOT EXISTS injury_status_current_idx
    ON wnba.injury_status(player_id, system_from DESC) WHERE system_to IS NULL;

COMMIT;
