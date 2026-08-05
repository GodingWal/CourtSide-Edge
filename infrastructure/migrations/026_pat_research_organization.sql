-- PAT-style research organization: plans, blocking audits, independent debate, synthesis,
-- evidence usefulness, source reliability, and governed agent rule proposals.
BEGIN;

CREATE TABLE IF NOT EXISTS wnba.research_plans (
    plan_id UUID PRIMARY KEY,
    research_run_id UUID REFERENCES wnba.research_runs(research_run_id),
    projection_id UUID NOT NULL REFERENCES wnba.stat_forecasts(projection_id),
    created_at TIMESTAMPTZ NOT NULL,
    trigger_codes TEXT[] NOT NULL,
    questions JSONB NOT NULL,
    required_evidence_groups TEXT[] NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned','running','blocked','complete'))
);

CREATE TABLE IF NOT EXISTS wnba.research_audits (
    audit_id UUID PRIMARY KEY,
    research_run_id UUID REFERENCES wnba.research_runs(research_run_id),
    projection_id UUID NOT NULL REFERENCES wnba.stat_forecasts(projection_id),
    audited_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass','warn','blocked')),
    issues JSONB NOT NULL,
    freshness_seconds INTEGER NOT NULL CHECK (freshness_seconds >= 0)
);

CREATE TABLE IF NOT EXISTS wnba.agent_forecasts (
    agent_forecast_id UUID PRIMARY KEY,
    research_run_id UUID NOT NULL REFERENCES wnba.research_runs(research_run_id),
    agent_role TEXT NOT NULL,
    round SMALLINT NOT NULL CHECK (round IN (1,2)),
    advisory_probability wnba.probability NOT NULL,
    rationale TEXT NOT NULL,
    evidence_ids UUID[] NOT NULL CHECK (cardinality(evidence_ids) > 0),
    peer_forecast_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (research_run_id,agent_role,round)
);

CREATE TABLE IF NOT EXISTS wnba.decision_syntheses (
    synthesis_id UUID PRIMARY KEY,
    research_run_id UUID NOT NULL UNIQUE REFERENCES wnba.research_runs(research_run_id),
    created_at TIMESTAMPTZ NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('support','caution','block','insufficient')),
    model_probability wnba.probability NOT NULL,
    advisory_probability wnba.probability,
    disagreement DOUBLE PRECISION NOT NULL CHECK (disagreement >= 0),
    summary TEXT NOT NULL,
    risk_flags JSONB NOT NULL,
    policy_reasons JSONB NOT NULL,
    CONSTRAINT synthesis_never_rewrites_model CHECK (
        model_probability >= 0 AND model_probability <= 1)
);

CREATE TABLE IF NOT EXISTS wnba.research_precedents (
    research_run_id UUID NOT NULL REFERENCES wnba.research_runs(research_run_id),
    episode_id UUID NOT NULL REFERENCES wnba.decision_episodes(episode_id),
    similarity DOUBLE PRECISION NOT NULL CHECK (similarity BETWEEN 0 AND 1),
    rank SMALLINT NOT NULL CHECK (rank > 0),
    PRIMARY KEY (research_run_id,episode_id)
);

CREATE TABLE IF NOT EXISTS wnba.evidence_interactions (
    interaction_id UUID PRIMARY KEY,
    evidence_id UUID NOT NULL REFERENCES wnba.evidence(evidence_id),
    episode_id UUID REFERENCES wnba.decision_episodes(episode_id),
    analyst TEXT NOT NULL,
    interaction TEXT NOT NULL CHECK (
        interaction IN ('viewed','useful','decision_changing','irrelevant','misleading')),
    weight SMALLINT NOT NULL CHECK (weight BETWEEN -3 AND 3),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.evidence_rankings (
    evidence_id UUID PRIMARY KEY REFERENCES wnba.evidence(evidence_id),
    score DOUBLE PRECISION NOT NULL,
    interactions INTEGER NOT NULL CHECK (interactions >= 0),
    calculated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.source_reliability (
    source TEXT NOT NULL,
    domain TEXT NOT NULL,
    sample_size INTEGER NOT NULL CHECK (sample_size >= 0),
    agreement_rate wnba.probability,
    timeliness_score wnba.probability,
    reliability wnba.probability NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source,domain,calculated_at)
);

ALTER TABLE wnba.analyst_rules
    ADD COLUMN IF NOT EXISTS evidence_ids UUID[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS mechanism TEXT,
    ADD COLUMN IF NOT EXISTS confounders TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS withdrawal_criteria TEXT;

ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS agent_rules_require_governance_metadata;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT agent_rules_require_governance_metadata CHECK (
        proposed_by <> 'deepseek' OR (
            cardinality(evidence_ids) > 0
            AND length(trim(coalesce(mechanism,''))) > 0
            AND cardinality(confounders) > 0
            AND expires_at IS NOT NULL
            AND length(trim(coalesce(withdrawal_criteria,''))) > 0));

CREATE INDEX IF NOT EXISTS research_plans_projection_idx
    ON wnba.research_plans(projection_id,created_at DESC);
CREATE INDEX IF NOT EXISTS agent_forecasts_run_idx
    ON wnba.agent_forecasts(research_run_id,round,agent_role);
CREATE INDEX IF NOT EXISTS evidence_interactions_evidence_idx
    ON wnba.evidence_interactions(evidence_id,occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS evidence_interactions_once_idx
    ON wnba.evidence_interactions(evidence_id,episode_id,analyst,interaction)
    WHERE episode_id IS NOT NULL;

COMMIT;
