-- 026_research_organization.sql
--
-- Turning a panel of analysts into a research organization.
--
-- The DeepSeek workflow already ran five evidence-grounded roles and stored cited, expiring
-- claims. What it did not have was any of the structure that makes a research organization
-- different from five people answering the same question at once: nobody decided what was worth
-- investigating, nothing could refuse to analyse bad inputs, the agents all saw the same prompt
-- simultaneously so their "independent" opinions were only independent by accident, and their
-- output was left as five paragraphs beside a forecast rather than combined into anything.
--
-- Seven structures land here, and every one of them is a restriction or a record. None of them
-- gives an agent a new power:
--
--   research_plans        what to investigate, why, and under whose plan
--   research_audits       a deterministic pre-flight that can BLOCK analysis on bad inputs
--   agent_analyses.round  first-round independence, then adversarial revision
--   decision_syntheses    model + research + market + policy, combined and *stored beside* the
--                         forecast -- never written back into it
--   claim refresh         claims expire on a schedule and say when they must be looked at again
--   evidence_rankings     which evidence analysts actually found useful
--   source_reliability    which sources have earned trust, by domain
--
-- Plus the provenance an agent-authored rule needs before a human can responsibly read it.
--
-- THE BOUNDARY THIS MIGRATION DEFENDS
-- -----------------------------------
-- A research agent may describe, cite, doubt, and propose. It may not change a number and it may
-- not activate anything. `decision_syntheses` stores the model's probability as a *copy* for
-- context and has no column an agent could write a different one into. Agent-authored rules land
-- as `proposed` with a `proposed_by` that names the agent, and the database refuses an approval
-- whose approver is that same agent.

BEGIN;

-- --------------------------------------------------------------------------------------
-- The coordinator's output: a plan, with a reason
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.research_plans (
    plan_id          UUID PRIMARY KEY,
    projection_id    UUID NOT NULL REFERENCES wnba.stat_forecasts(projection_id),
    episode_id       UUID REFERENCES wnba.decision_episodes(episode_id),
    planned_at       TIMESTAMPTZ NOT NULL,
    planned_by       TEXT NOT NULL,
    -- Why this projection and not the other four hundred. A plan with no trigger is a plan
    -- nobody can audit for bias toward the props that happened to be interesting.
    trigger_kind     TEXT NOT NULL CHECK (trigger_kind IN (
                         'injury_change','role_change','model_disagreement','claim_expiry',
                         'line_move','manual','candidate_recommendation')),
    trigger_detail   JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority         SMALLINT NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    roles            TEXT[] NOT NULL CHECK (cardinality(roles) > 0),
    questions        JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason           TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    locks_at         TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
                         'planned','auditing','blocked','running','complete','expired','failed')),
    research_run_id  UUID REFERENCES wnba.research_runs(research_run_id),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    detail           TEXT NOT NULL DEFAULT ''
);

-- One open plan per projection. Without this, a trigger that fires on every poll queues the same
-- investigation every fifteen minutes and the queue becomes a measure of the polling schedule.
CREATE UNIQUE INDEX IF NOT EXISTS research_plans_one_open_per_projection_idx
    ON wnba.research_plans (projection_id)
    WHERE status IN ('planned','auditing','running');
CREATE INDEX IF NOT EXISTS research_plans_queue_idx
    ON wnba.research_plans (status, priority DESC, planned_at);

-- --------------------------------------------------------------------------------------
-- The data auditor: a gate in front of the analysts
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.research_audits (
    audit_id       UUID PRIMARY KEY,
    plan_id        UUID NOT NULL REFERENCES wnba.research_plans(plan_id),
    audited_at     TIMESTAMPTZ NOT NULL,
    verdict        TEXT NOT NULL CHECK (verdict IN ('clear','degraded','blocked')),
    findings       JSONB NOT NULL DEFAULT '[]'::jsonb,
    blocking_count INTEGER NOT NULL DEFAULT 0 CHECK (blocking_count >= 0),
    auditor        TEXT NOT NULL,
    CONSTRAINT blocked_audits_have_a_blocking_finding CHECK (
        (verdict = 'blocked') = (blocking_count > 0))
);
CREATE INDEX IF NOT EXISTS research_audits_plan_idx ON wnba.research_audits (plan_id, audited_at DESC);

COMMENT ON TABLE wnba.research_audits IS
    'Deterministic pre-flight over the inputs an investigation would read. Runs before any '
    'model is called, so a run on stale or self-contradictory inputs costs nothing and produces '
    'no claims to later explain away.';

-- --------------------------------------------------------------------------------------
-- Two rounds: independent, then adversarial
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.agent_analyses
    ADD COLUMN IF NOT EXISTS round            SMALLINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS revised_from     UUID REFERENCES wnba.agent_analyses(analysis_id),
    ADD COLUMN IF NOT EXISTS prior_confidence wnba.probability,
    ADD COLUMN IF NOT EXISTS revision_reason  TEXT,
    ADD COLUMN IF NOT EXISTS saw_peers        BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE wnba.agent_analyses
    DROP CONSTRAINT IF EXISTS agent_analyses_research_run_id_agent_role_key;
ALTER TABLE wnba.agent_analyses
    DROP CONSTRAINT IF EXISTS agent_analyses_run_role_round_key;
ALTER TABLE wnba.agent_analyses
    ADD CONSTRAINT agent_analyses_run_role_round_key UNIQUE (research_run_id, agent_role, round);

ALTER TABLE wnba.agent_analyses
    DROP CONSTRAINT IF EXISTS agent_analyses_round_is_one_or_two;
ALTER TABLE wnba.agent_analyses
    ADD CONSTRAINT agent_analyses_round_is_one_or_two CHECK (round IN (1, 2));

-- The independence invariant, in the schema rather than only in the prompt: a first-round
-- analysis by definition did not see its peers, and a revision by definition did.
ALTER TABLE wnba.agent_analyses
    DROP CONSTRAINT IF EXISTS first_round_analyses_are_independent;
ALTER TABLE wnba.agent_analyses
    ADD CONSTRAINT first_round_analyses_are_independent CHECK (
        (round = 1 AND NOT saw_peers AND revised_from IS NULL)
        OR (round = 2 AND saw_peers AND revised_from IS NOT NULL));

COMMENT ON COLUMN wnba.agent_analyses.saw_peers IS
    'False for every first-round analysis. Five agents given the same prompt at the same moment '
    'are independent only in the sense that they did not talk; five agents given each other''s '
    'conclusions are doing something different and the record has to say which happened.';

-- --------------------------------------------------------------------------------------
-- The synthesizer: everything combined, nothing overwritten
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.decision_syntheses (
    synthesis_id            UUID PRIMARY KEY,
    research_run_id         UUID NOT NULL REFERENCES wnba.research_runs(research_run_id),
    episode_id              UUID NOT NULL REFERENCES wnba.decision_episodes(episode_id),
    created_at              TIMESTAMPTZ NOT NULL,
    synthesized_by          TEXT NOT NULL,
    posture                 TEXT NOT NULL CHECK (posture IN (
                                'supported','qualified','contested','insufficient_evidence',
                                'policy_blocked')),
    -- A COPY of what the model said, for context. There is deliberately no column here that
    -- an agent could write a different probability into: synthesis annotates a decision, it
    -- does not make one.
    model_probability       wnba.probability NOT NULL,
    policy_blocked          BOOLEAN NOT NULL DEFAULT false,
    policy_reasons          JSONB NOT NULL DEFAULT '[]'::jsonb,
    supporting_claim_ids    UUID[] NOT NULL DEFAULT '{}',
    contradicting_claim_ids UUID[] NOT NULL DEFAULT '{}',
    unresolved_conflicts    INTEGER NOT NULL DEFAULT 0 CHECK (unresolved_conflicts >= 0),
    agent_agreement         DOUBLE PRECISION,
    weighted_confidence     wnba.probability,
    concerns                JSONB NOT NULL DEFAULT '[]'::jsonb,
    precedent_episode_ids   UUID[] NOT NULL DEFAULT '{}',
    precedent_hit_rate      wnba.probability,
    narrative               TEXT NOT NULL DEFAULT '',
    UNIQUE (research_run_id)
);
CREATE INDEX IF NOT EXISTS decision_syntheses_episode_idx
    ON wnba.decision_syntheses (episode_id, created_at DESC);

COMMENT ON TABLE wnba.decision_syntheses IS
    'The research organization''s combined read on one decision: what the model said, what the '
    'agents concluded, what the market looked like, what policy did. Analysis-only. The forecast '
    'it describes is already frozen and this row cannot change it.';

-- --------------------------------------------------------------------------------------
-- Claim lifecycle: expiry was stored, refresh was not scheduled
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.research_claims
    ADD COLUMN IF NOT EXISTS refresh_after  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS superseded_by  UUID REFERENCES wnba.research_claims(claim_id),
    ADD COLUMN IF NOT EXISTS superseded_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS domain         TEXT;

CREATE INDEX IF NOT EXISTS research_claims_refresh_idx
    ON wnba.research_claims (refresh_after)
    WHERE superseded_at IS NULL;

COMMENT ON COLUMN wnba.research_claims.refresh_after IS
    'When this claim stops being safe to rely on without re-checking. Distinct from expires_at: '
    'a claim about a questionable tag is stale in an hour even though it formally expires at '
    'lock, and the difference is what the refresh scheduler reads.';

-- --------------------------------------------------------------------------------------
-- What evidence was worth having, and which sources have earned trust
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.evidence_rankings (
    evidence_id       UUID PRIMARY KEY REFERENCES wnba.evidence(evidence_id),
    calculated_at     TIMESTAMPTZ NOT NULL,
    useful_labels     INTEGER NOT NULL DEFAULT 0 CHECK (useful_labels >= 0),
    misleading_labels INTEGER NOT NULL DEFAULT 0 CHECK (misleading_labels >= 0),
    citation_count    INTEGER NOT NULL DEFAULT 0 CHECK (citation_count >= 0),
    usefulness        wnba.probability NOT NULL,
    sample_size       INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0)
);

COMMENT ON TABLE wnba.evidence_rankings IS
    'Retrieval ranking learned from analyst usage. `usefulness` is pooled toward 0.5 by sample '
    'size, so one enthusiastic label cannot promote a piece of evidence above one that has been '
    'useful thirty times.';

CREATE TABLE IF NOT EXISTS wnba.source_reliability (
    source          TEXT NOT NULL,
    domain          TEXT NOT NULL,
    calculated_at   TIMESTAMPTZ NOT NULL,
    documents       INTEGER NOT NULL DEFAULT 0 CHECK (documents >= 0),
    citations       INTEGER NOT NULL DEFAULT 0 CHECK (citations >= 0),
    useful_labels   INTEGER NOT NULL DEFAULT 0 CHECK (useful_labels >= 0),
    misleading_labels INTEGER NOT NULL DEFAULT 0 CHECK (misleading_labels >= 0),
    contradiction_rate DOUBLE PRECISION,
    reliability     wnba.probability NOT NULL,
    sample_size     INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0),
    PRIMARY KEY (source, domain, calculated_at)
);

-- Agent credibility already exists (migration 018) keyed by (agent_role, domain, calculated_at).
-- What it lacked was any writer and any record of how the number was reached.
ALTER TABLE wnba.agent_credibility
    ADD COLUMN IF NOT EXISTS claims_scored     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS method            TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS detail            JSONB NOT NULL DEFAULT '{}'::jsonb;

-- --------------------------------------------------------------------------------------
-- Agent-authored rules: the provenance a reviewer needs before reading one
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.analyst_rules
    ADD COLUMN IF NOT EXISTS authored_by_agent  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS hypothesis_id      UUID REFERENCES wnba.hypotheses(hypothesis_id),
    ADD COLUMN IF NOT EXISTS research_run_id    UUID REFERENCES wnba.research_runs(research_run_id),
    ADD COLUMN IF NOT EXISTS evidence_ids       UUID[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS mechanism          TEXT,
    ADD COLUMN IF NOT EXISTS confounders        TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS expiry_conditions  TEXT,
    ADD COLUMN IF NOT EXISTS withdrawal_criteria TEXT,
    ADD COLUMN IF NOT EXISTS leakage_inspection JSONB;

-- An agent may propose. What it may not do is propose something a reviewer cannot interrogate:
-- a rule with no cited evidence, no stated mechanism, no named confounders, no conditions under
-- which it stops being true, and no criteria for withdrawing it is not a proposal, it is an
-- assertion. Template-authored rules are exempt because their provenance is the catalogue.
ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS agent_rules_carry_their_provenance;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT agent_rules_carry_their_provenance CHECK (
        NOT authored_by_agent OR (
            cardinality(evidence_ids) > 0
            AND length(trim(coalesce(mechanism, ''))) >= 40
            AND cardinality(confounders) > 0
            AND length(trim(coalesce(expiry_conditions, ''))) >= 20
            AND length(trim(coalesce(withdrawal_criteria, ''))) >= 20
            AND leakage_inspection IS NOT NULL));

-- No agent activates its own rule. The rule lifecycle already required a named human approver;
-- this closes the remaining gap, which is an approver string that happens to be the proposer.
ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS approver_is_not_the_proposer;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT approver_is_not_the_proposer CHECK (
        approved_by IS NULL OR lower(trim(approved_by)) <> lower(trim(proposed_by)));

COMMENT ON COLUMN wnba.analyst_rules.leakage_inspection IS
    'The result of inspecting a proposed rule for conditions that could only be known after the '
    'game. The closed vocabulary already excludes outcome fields, so this catches the subtler '
    'case: thresholds so narrow they fingerprint the specific episodes that motivated the rule.';

COMMIT;
