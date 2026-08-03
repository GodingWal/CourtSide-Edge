BEGIN;

CREATE TABLE IF NOT EXISTS wnba.analyst_feedback (
    feedback_id                 UUID PRIMARY KEY,
    episode_id                  UUID NOT NULL REFERENCES wnba.decision_episodes(episode_id),
    analyst                     TEXT NOT NULL,
    submitted_at                TIMESTAMPTZ NOT NULL,
    feedback_type               TEXT NOT NULL,
    projection_useful           wnba.probability NOT NULL,
    evidence_relevant           wnba.probability NOT NULL,
    confidence_appropriate      wnba.probability NOT NULL,
    weakest_assumption          TEXT,
    missing_context             TEXT,
    would_repeat                BOOLEAN NOT NULL,
    evidence_ids_useful         UUID[] NOT NULL DEFAULT '{}',
    evidence_ids_misleading     UUID[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS analyst_feedback_episode_idx
    ON wnba.analyst_feedback(episode_id,submitted_at DESC);

CREATE TABLE IF NOT EXISTS wnba.hypotheses (
    hypothesis_id       UUID PRIMARY KEY,
    name                TEXT NOT NULL,
    causal_statement    TEXT NOT NULL,
    mechanism           TEXT NOT NULL,
    target_markets      TEXT[] NOT NULL,
    expected_direction  TEXT NOT NULL CHECK (expected_direction IN ('increase','decrease','conditional')),
    required_features   TEXT[] NOT NULL DEFAULT '{}',
    confounders         TEXT[] NOT NULL,
    status              TEXT NOT NULL DEFAULT 'proposed',
    confidence          wnba.probability NOT NULL DEFAULT 0.5,
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    reviewed_by         TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS wnba.hypothesis_episodes (
    hypothesis_id UUID NOT NULL REFERENCES wnba.hypotheses(hypothesis_id),
    episode_id    UUID NOT NULL REFERENCES wnba.decision_episodes(episode_id),
    relationship  TEXT NOT NULL CHECK (relationship IN ('supporting','contradicting')),
    PRIMARY KEY (hypothesis_id,episode_id)
);

CREATE TABLE IF NOT EXISTS wnba.research_proposals (
    proposal_id                 UUID PRIMARY KEY,
    title                       TEXT NOT NULL,
    proposed_at                 TIMESTAMPTZ NOT NULL,
    proposed_by                 TEXT NOT NULL,
    motivating_episode_ids      UUID[] NOT NULL CHECK (cardinality(motivating_episode_ids) > 0),
    hypothesis_id               UUID REFERENCES wnba.hypotheses(hypothesis_id),
    estimated_value             wnba.probability NOT NULL,
    estimated_failure_reduction wnba.probability NOT NULL,
    implementation_cost         TEXT NOT NULL CHECK (implementation_cost IN ('low','medium','high')),
    affected_markets            TEXT[] NOT NULL,
    experiment_design           TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'proposed',
    approved_by                 TEXT,
    error_category              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.experiments (
    experiment_id               UUID PRIMARY KEY,
    proposal_id                 UUID REFERENCES wnba.research_proposals(proposal_id),
    champion_model_version_id   UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    challenger_model_version_id UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    started_at                  TIMESTAMPTZ NOT NULL,
    completed_at                TIMESTAMPTZ,
    primary_metric              TEXT NOT NULL CHECK (primary_metric IN ('log_loss','brier','mae','line_value')),
    evaluation                  TEXT NOT NULL CHECK (evaluation IN ('rolling_origin','walk_forward','shadow')),
    champion_score              DOUBLE PRECISION,
    challenger_score            DOUBLE PRECISION,
    promoted                    BOOLEAN NOT NULL DEFAULT false,
    notes                       TEXT NOT NULL DEFAULT '',
    CHECK (champion_model_version_id <> challenger_model_version_id),
    CHECK (NOT promoted OR completed_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS wnba.agent_credibility (
    agent_role       TEXT NOT NULL,
    domain           TEXT NOT NULL,
    sample_size      INTEGER NOT NULL CHECK (sample_size >= 0),
    calibration      DOUBLE PRECISION,
    evidence_accuracy DOUBLE PRECISION,
    credibility      wnba.probability NOT NULL,
    calculated_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (agent_role,domain,calculated_at)
);

COMMIT;
