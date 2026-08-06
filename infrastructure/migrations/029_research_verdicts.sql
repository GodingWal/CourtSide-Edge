-- 029_research_verdicts.sql
-- Record what a research run concluded, and what it cost to find out.
--
-- The verdict is computed in Python from the stored claims and citations, never by the provider.
-- It deliberately has no probability column: a research run may raise caution, never a forecast.

BEGIN;

ALTER TABLE wnba.research_runs
    ADD COLUMN IF NOT EXISTS agent_calls      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS provider_calls   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS prompt_tokens    INTEGER,
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER;

ALTER TABLE wnba.research_runs
    DROP CONSTRAINT IF EXISTS research_run_counts_are_not_negative;
ALTER TABLE wnba.research_runs
    ADD CONSTRAINT research_run_counts_are_not_negative CHECK (
        agent_calls >= 0
        AND provider_calls >= agent_calls
        AND coalesce(prompt_tokens, 0) >= 0
        AND coalesce(completion_tokens, 0) >= 0);

CREATE TABLE IF NOT EXISTS wnba.research_verdicts (
    verdict_id            UUID PRIMARY KEY,
    research_run_id       UUID NOT NULL UNIQUE REFERENCES wnba.research_runs(research_run_id),
    caution               TEXT NOT NULL CHECK (caution IN ('low','elevated','high')),
    agreement             wnba.probability NOT NULL,
    total_claims          INTEGER NOT NULL CHECK (total_claims >= 0),
    contested_claims      INTEGER NOT NULL CHECK (contested_claims >= 0),
    contested_predicates  JSONB NOT NULL DEFAULT '[]',
    risk_flags            JSONB NOT NULL DEFAULT '[]',
    skeptic_confidence    wnba.probability NOT NULL,
    rationale             TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (contested_claims <= total_claims)
);

COMMIT;
