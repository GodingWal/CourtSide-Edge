BEGIN;

CREATE TABLE IF NOT EXISTS wnba.source_documents (
    document_id       UUID PRIMARY KEY,
    source            TEXT NOT NULL,
    source_uri        TEXT,
    title             TEXT NOT NULL DEFAULT '',
    content_sha256    TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    content_excerpt   TEXT NOT NULL,
    published_at      TIMESTAMPTZ,
    retrieved_at      TIMESTAMPTZ NOT NULL,
    UNIQUE (source,content_sha256)
);

CREATE TABLE IF NOT EXISTS wnba.evidence (
    evidence_id       UUID PRIMARY KEY,
    document_id       UUID NOT NULL REFERENCES wnba.source_documents(document_id),
    subject_id        UUID,
    excerpt           TEXT NOT NULL CHECK (length(excerpt) > 0),
    observed_at       TIMESTAMPTZ NOT NULL,
    reliability       wnba.probability NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wnba.research_runs (
    research_run_id   UUID PRIMARY KEY,
    projection_id     UUID NOT NULL REFERENCES wnba.stat_forecasts(projection_id),
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ,
    status            TEXT NOT NULL CHECK (status IN ('running','complete','failed','blocked')),
    prompt_sha256      TEXT NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    error              TEXT
);

CREATE TABLE IF NOT EXISTS wnba.agent_analyses (
    analysis_id       UUID PRIMARY KEY,
    research_run_id   UUID NOT NULL REFERENCES wnba.research_runs(research_run_id),
    agent_role        TEXT NOT NULL,
    conclusion        TEXT NOT NULL,
    confidence        wnba.probability NOT NULL,
    risk_flags        JSONB NOT NULL DEFAULT '[]',
    evidence_ids      UUID[] NOT NULL,
    response_sha256   TEXT NOT NULL CHECK (response_sha256 ~ '^[0-9a-f]{64}$'),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (research_run_id,agent_role)
);

CREATE TABLE IF NOT EXISTS wnba.research_claims (
    claim_id                    UUID PRIMARY KEY,
    analysis_id                 UUID NOT NULL REFERENCES wnba.agent_analyses(analysis_id),
    subject_id                  UUID NOT NULL,
    predicate                   TEXT NOT NULL,
    value                       TEXT NOT NULL,
    confidence                  wnba.probability NOT NULL,
    evidence_ids                UUID[] NOT NULL CHECK (cardinality(evidence_ids) > 0),
    contradicting_evidence_ids  UUID[] NOT NULL DEFAULT '{}',
    valid_from                  TIMESTAMPTZ NOT NULL,
    expires_at                  TIMESTAMPTZ NOT NULL,
    generated_by                TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'proposed',
    reviewed_by                 TEXT,
    CHECK (expires_at > valid_from)
);
CREATE INDEX IF NOT EXISTS research_claims_subject_idx
    ON wnba.research_claims(subject_id,expires_at DESC);

COMMIT;
