-- Audited cross-source identity and reproducible baseline forecast lineage.
BEGIN;

CREATE TABLE IF NOT EXISTS wnba.player_merges (
    merge_id       UUID PRIMARY KEY,
    from_player_id UUID NOT NULL REFERENCES wnba.players(player_id),
    to_player_id   UUID NOT NULL REFERENCES wnba.players(player_id),
    reason         TEXT NOT NULL CHECK (length(reason) > 0),
    verified_by    TEXT NOT NULL CHECK (length(verified_by) > 0),
    system_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to      TIMESTAMPTZ,
    CHECK (from_player_id <> to_player_id),
    CHECK (system_to IS NULL OR system_to > system_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS player_merges_current_uq
    ON wnba.player_merges(from_player_id) WHERE system_to IS NULL;

CREATE TABLE IF NOT EXISTS wnba.model_versions (
    model_version_id UUID PRIMARY KEY,
    name             TEXT NOT NULL,
    semver           TEXT NOT NULL,
    target           TEXT NOT NULL,
    stage            TEXT NOT NULL CHECK (stage IN ('champion','challenger','shadow','deprecated')),
    specification    JSONB NOT NULL,
    git_sha          TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, semver, target)
);

CREATE TABLE IF NOT EXISTS wnba.feature_snapshots (
    feature_snapshot_id UUID PRIMARY KEY,
    player_id           UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id             UUID NOT NULL REFERENCES wnba.games(game_id),
    as_of               TIMESTAMPTZ NOT NULL,
    features            JSONB NOT NULL,
    source_line_ids     UUID[] NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wnba.model_runs (
    model_run_id     UUID PRIMARY KEY,
    model_version_id UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    started_at       TIMESTAMPTZ NOT NULL,
    completed_at     TIMESTAMPTZ NOT NULL,
    random_seed      INTEGER NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
    detail           TEXT NOT NULL DEFAULT '',
    CHECK (completed_at >= started_at)
);

CREATE TABLE IF NOT EXISTS wnba.stat_forecasts (
    projection_id       UUID PRIMARY KEY,
    model_run_id        UUID NOT NULL REFERENCES wnba.model_runs(model_run_id),
    feature_snapshot_id UUID NOT NULL REFERENCES wnba.feature_snapshots(feature_snapshot_id),
    quote_id            UUID NOT NULL REFERENCES wnba.prop_quotes(quote_id),
    player_id           UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id             UUID NOT NULL REFERENCES wnba.games(game_id),
    prop_type           TEXT NOT NULL,
    line                NUMERIC(6,2) NOT NULL,
    mean                DOUBLE PRECISION NOT NULL,
    median              DOUBLE PRECISION NOT NULL,
    stddev              DOUBLE PRECISION NOT NULL,
    probability_over    wnba.probability NOT NULL,
    probability_push    wnba.probability NOT NULL,
    probability_under   wnba.probability NOT NULL,
    projected_minutes   DOUBLE PRECISION NOT NULL,
    sample_size         INTEGER NOT NULL,
    data_quality_score  wnba.probability NOT NULL,
    confidence          wnba.probability NOT NULL,
    distribution        JSONB NOT NULL,
    generated_at        TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    is_shadow           BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (expires_at > generated_at),
    UNIQUE (model_run_id, quote_id)
);
CREATE INDEX IF NOT EXISTS stat_forecasts_recent_idx
    ON wnba.stat_forecasts(generated_at DESC);

COMMIT;
