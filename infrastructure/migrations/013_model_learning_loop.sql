BEGIN;

CREATE TABLE IF NOT EXISTS wnba.model_evaluations (
    evaluation_id       UUID PRIMARY KEY,
    model_version_id    UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    component_name      TEXT NOT NULL DEFAULT 'ensemble',
    evaluated_at        TIMESTAMPTZ NOT NULL,
    sample_size         INTEGER NOT NULL CHECK (sample_size >= 0),
    brier               DOUBLE PRECISION,
    log_loss            DOUBLE PRECISION,
    calibration_error   DOUBLE PRECISION,
    mean_line_value     DOUBLE PRECISION,
    mean_absolute_error DOUBLE PRECISION,
    status              TEXT NOT NULL CHECK (status IN ('insufficient_data','healthy','degraded')),
    metrics             JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS model_evaluations_latest_idx
    ON wnba.model_evaluations(model_version_id,component_name,evaluated_at DESC);

CREATE TABLE IF NOT EXISTS wnba.calibration_snapshots (
    snapshot_id         UUID PRIMARY KEY,
    evaluation_id       UUID NOT NULL REFERENCES wnba.model_evaluations(evaluation_id),
    prop_type           TEXT,
    probability_bucket  NUMERIC(3,2) NOT NULL,
    forecasts           INTEGER NOT NULL CHECK (forecasts > 0),
    mean_predicted      wnba.probability NOT NULL,
    observed_rate       wnba.probability NOT NULL,
    brier               DOUBLE PRECISION NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.error_attributions (
    attribution_id       UUID PRIMARY KEY,
    episode_id           UUID NOT NULL UNIQUE REFERENCES wnba.decision_episodes(episode_id),
    primary_error        TEXT NOT NULL,
    avoidable            BOOLEAN NOT NULL,
    confidence           wnba.probability NOT NULL,
    minutes_error        DOUBLE PRECISION,
    stat_error           DOUBLE PRECISION NOT NULL,
    line_value           DOUBLE PRECISION,
    detail               JSONB NOT NULL,
    attributed_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.drift_incidents (
    incident_id          UUID PRIMARY KEY,
    model_version_id     UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    component_name       TEXT NOT NULL DEFAULT 'ensemble',
    metric               TEXT NOT NULL,
    observed_value       DOUBLE PRECISION NOT NULL,
    threshold            DOUBLE PRECISION NOT NULL,
    severity             TEXT NOT NULL CHECK (severity IN ('warning','blocking')),
    automatic_response   TEXT NOT NULL,
    opened_at            TIMESTAMPTZ NOT NULL,
    resolved_at          TIMESTAMPTZ,
    UNIQUE (model_version_id,component_name,metric,opened_at)
);
CREATE INDEX IF NOT EXISTS drift_incidents_active_idx
    ON wnba.drift_incidents(opened_at DESC) WHERE resolved_at IS NULL;

COMMIT;
