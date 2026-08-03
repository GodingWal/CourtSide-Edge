BEGIN;

CREATE TABLE IF NOT EXISTS wnba.forecast_components (
    component_id       UUID PRIMARY KEY,
    projection_id      UUID NOT NULL REFERENCES wnba.stat_forecasts(projection_id),
    component_name     TEXT NOT NULL,
    component_version  TEXT NOT NULL,
    weight             DOUBLE PRECISION NOT NULL CHECK (weight >= 0 AND weight <= 1),
    mean               DOUBLE PRECISION NOT NULL CHECK (mean >= 0),
    probability_over   wnba.probability NOT NULL,
    probability_push   wnba.probability NOT NULL,
    probability_under  wnba.probability NOT NULL,
    distribution       JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (projection_id,component_name)
);
CREATE INDEX IF NOT EXISTS forecast_components_projection_idx
    ON wnba.forecast_components(projection_id);

COMMIT;
