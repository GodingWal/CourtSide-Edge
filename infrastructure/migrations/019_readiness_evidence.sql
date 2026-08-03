BEGIN;

CREATE TABLE IF NOT EXISTS wnba.readiness_evaluations (
    readiness_evaluation_id UUID PRIMARY KEY,
    evaluated_at            TIMESTAMPTZ NOT NULL,
    overall_ready           BOOLEAN NOT NULL,
    specification_version   TEXT NOT NULL,
    evidence                JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.readiness_gate_results (
    gate_result_id          UUID PRIMARY KEY,
    readiness_evaluation_id UUID NOT NULL
        REFERENCES wnba.readiness_evaluations(readiness_evaluation_id),
    gate_id                 TEXT NOT NULL,
    status                  TEXT NOT NULL
        CHECK (status IN ('pass','provisional_pass','pending','fail')),
    evidence_source         TEXT NOT NULL,
    observed_value          DOUBLE PRECISION,
    threshold_value         DOUBLE PRECISION,
    detail                  TEXT NOT NULL,
    UNIQUE (readiness_evaluation_id,gate_id)
);

COMMIT;
