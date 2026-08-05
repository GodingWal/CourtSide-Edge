-- 025_champion_challenger.sql
--
-- Somewhere to keep a second opinion, and the record of whether it was any good.
--
-- The experiments table has existed since migration 018 and has never held a row. The stated
-- reason was honest and correct: an experiment compares two model versions and only one existed,
-- so filling the table would have been decoration. Two genuinely different model families now
-- exist (a conjugate hierarchical-Bayes rate model and a local-level state-space role model), so
-- the comparison has something to compare.
--
-- Three things this migration insists on:
--
--   1. A challenger prediction is stored against the *same episode* the champion decided, at the
--      same moment, from the same inputs. A challenger scored on a different board, or later, is
--      measuring the board or the delay. The unique index enforces one prediction per
--      (experiment, episode, model version) and nothing rewrites it afterwards.
--
--   2. Failure is data. A challenger that raised, timed out, or refused for want of history gets
--      a row with `failed = true` and a reason, not a missing row. Silent omission is how a model
--      that only answers the easy props comes to look better than one that answers all of them.
--
--   3. Promotion is a named human act, with the same asymmetry the rule lifecycle already has:
--      no scheduled job and no agent can write `promoted`, because `promoted` is unsatisfiable
--      without an `approved_by` the database checks for. Rollback is equally named, and leaves
--      the promotion in place as history rather than erasing it.

BEGIN;

-- --------------------------------------------------------------------------------------
-- Experiment record: what was compared, on how much, and who decided
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.experiments
    ADD COLUMN IF NOT EXISTS challenger_name         TEXT,
    ADD COLUMN IF NOT EXISTS status                  TEXT NOT NULL DEFAULT 'running',
    ADD COLUMN IF NOT EXISTS opened_by               TEXT,
    ADD COLUMN IF NOT EXISTS minimum_sample          INTEGER NOT NULL DEFAULT 200,
    ADD COLUMN IF NOT EXISTS sample_size             INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS independent_sample_size INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS evaluated_at            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS metrics                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS subgroups               JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS verdict                 TEXT,
    ADD COLUMN IF NOT EXISTS approved_by             TEXT,
    ADD COLUMN IF NOT EXISTS approved_at             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS promotion_reason        TEXT,
    ADD COLUMN IF NOT EXISTS rolled_back_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rolled_back_by          TEXT,
    ADD COLUMN IF NOT EXISTS rollback_reason         TEXT;

ALTER TABLE wnba.experiments
    DROP CONSTRAINT IF EXISTS experiments_status_is_known;
ALTER TABLE wnba.experiments
    ADD CONSTRAINT experiments_status_is_known CHECK (
        status IN ('running', 'evaluated', 'promoted', 'rejected', 'rolled_back', 'abandoned'));

ALTER TABLE wnba.experiments
    DROP CONSTRAINT IF EXISTS experiments_verdict_is_known;
ALTER TABLE wnba.experiments
    ADD CONSTRAINT experiments_verdict_is_known CHECK (
        verdict IS NULL OR verdict IN (
            'insufficient_sample', 'challenger_better', 'no_difference', 'challenger_worse',
            'subgroup_degradation'));

-- The promotion lock. `promoted` cannot be true without a named human and a stated reason, so
-- there is no value a job could write that would activate a challenger on its own.
ALTER TABLE wnba.experiments
    DROP CONSTRAINT IF EXISTS promotion_requires_a_named_human;
ALTER TABLE wnba.experiments
    ADD CONSTRAINT promotion_requires_a_named_human CHECK (
        NOT promoted OR (
            approved_at IS NOT NULL
            AND length(trim(coalesce(approved_by, ''))) > 0
            AND length(trim(coalesce(promotion_reason, ''))) > 0));

ALTER TABLE wnba.experiments
    DROP CONSTRAINT IF EXISTS rollback_requires_a_named_human;
ALTER TABLE wnba.experiments
    ADD CONSTRAINT rollback_requires_a_named_human CHECK (
        rolled_back_at IS NULL OR (
            length(trim(coalesce(rolled_back_by, ''))) > 0
            AND length(trim(coalesce(rollback_reason, ''))) > 0));

-- One live experiment per challenger version. Two overlapping experiments on the same model
-- would split the sample and let whichever finished first claim the evidence.
CREATE UNIQUE INDEX IF NOT EXISTS experiments_one_running_per_challenger_idx
    ON wnba.experiments (challenger_model_version_id)
    WHERE status = 'running';

COMMENT ON COLUMN wnba.experiments.independent_sample_size IS
    'Distinct (player, game, market) decisions behind sample_size. Re-scoring the same prop at '
    'five snapshots produces five rows and one independent observation; the promotion gate reads '
    'this column, not sample_size.';

-- --------------------------------------------------------------------------------------
-- Shadow predictions: the challenger''s answer to the question the champion already answered
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.challenger_predictions (
    prediction_id       UUID PRIMARY KEY,
    experiment_id       UUID NOT NULL REFERENCES wnba.experiments(experiment_id),
    model_version_id    UUID NOT NULL REFERENCES wnba.model_versions(model_version_id),
    episode_id          UUID NOT NULL REFERENCES wnba.decision_episodes(episode_id),
    generated_at        TIMESTAMPTZ NOT NULL,
    prop_type           TEXT NOT NULL,
    line                NUMERIC(7,2) NOT NULL,
    side                TEXT NOT NULL CHECK (side IN ('over', 'under')),
    projected_mean      DOUBLE PRECISION,
    probability_over    wnba.probability,
    probability_side    wnba.probability,
    latency_ms          DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    failed              BOOLEAN NOT NULL DEFAULT false,
    failure_reason      TEXT,
    diagnostics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- A successful prediction carries probabilities; a failed one carries a reason. Neither is
    -- allowed to be half-present, because a null probability with no stated failure is the shape
    -- a quietly-skipped prop takes.
    CONSTRAINT challenger_prediction_is_complete_or_explained CHECK (
        (NOT failed AND probability_over IS NOT NULL AND probability_side IS NOT NULL)
        OR (failed AND length(trim(coalesce(failure_reason, ''))) > 0)),
    UNIQUE (experiment_id, episode_id, model_version_id)
);

CREATE INDEX IF NOT EXISTS challenger_predictions_experiment_idx
    ON wnba.challenger_predictions (experiment_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS challenger_predictions_episode_idx
    ON wnba.challenger_predictions (episode_id);

COMMENT ON TABLE wnba.challenger_predictions IS
    'Live shadow forecasts. Nothing here reaches a recommendation, a rule, or the board: a '
    'challenger prediction exists to be scored against the same settled outcome the champion is '
    'scored against, and for no other purpose.';

COMMIT;
