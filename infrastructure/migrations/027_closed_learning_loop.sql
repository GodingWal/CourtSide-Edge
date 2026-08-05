-- 027_closed_learning_loop.sql
--
-- Closing the three dead ends in the learning loop, and giving the model a seat at the table
-- without giving it the pen.
--
-- Everything the loop produced was write-forward. A hypothesis was created and never revisited,
-- so `status` sat at 'proposed' and `confidence` at 0.5 for the life of the row. A rule was
-- backtested once on the evidence available the week it was proposed and then ran indefinitely
-- on that frozen verdict. Agent credibility was computed, displayed, and read by nothing. The
-- tables were shaped for a loop that closes; the code only ever went one way round it.
--
-- Four things this migration insists on:
--
--   1. **A hypothesis is answerable.** It gains an evidence tally, a review verdict and a
--      reviewed timestamp, so "does the settled record still support this?" has somewhere to be
--      written down. The tally is counted from `hypothesis_episodes` by the reviewer, never
--      supplied by a model.
--
--   2. **An active rule can be taken back automatically.** `suspended` is a new status reachable
--      without a human, because suspension is a de-escalation: it removes a rule's effect on
--      forecasts. Re-activation is not reachable that way -- it still requires the named-human
--      path that migration 024 established. The asymmetry is the whole point: the system may
--      become more cautious on its own and may never become more confident on its own.
--
--   3. **Model contributions are labelled and auditable.** Anything DeepSeek authored carries
--      `authored_by_model`, and every advisory call lands in `model_advisories` with its prompt
--      and response hashes and whether the loop actually used the answer or fell back. A model
--      proposal is subject to exactly the evidence a template proposal is subject to; the label
--      exists so the difference can be measured, not so it can be trusted more.
--
--   4. **De-escalation leaves a record.** `drift_incidents.automatic_response` has always held a
--      value ('reduce_confidence' or 'require_manual_review') that nothing read. Acting on it
--      writes a `deescalation_events` row, so a forecast that was widened can say why.

BEGIN;

-- --------------------------------------------------------------------------------------
-- Hypotheses: answerable, not just recorded
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.hypotheses
    ADD COLUMN IF NOT EXISTS evaluated_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS supporting_count    INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS contradicting_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS review              JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS authored_by_model   BOOLEAN NOT NULL DEFAULT false,
    -- The `primary_error` value this hypothesis claims to explain. Without it a review has to
    -- reverse-engineer the claim from the hypothesis name, which works only for the templated
    -- ones and breaks the moment a model authors a new hypothesis.
    ADD COLUMN IF NOT EXISTS error_category      TEXT;

ALTER TABLE wnba.hypotheses
    DROP CONSTRAINT IF EXISTS hypotheses_status_is_known;
ALTER TABLE wnba.hypotheses
    ADD CONSTRAINT hypotheses_status_is_known CHECK (
        status IN ('proposed', 'supported', 'refuted', 'inconclusive', 'retired'));

-- A verdict that is not 'proposed' has been reached by a review, and a review has a date. This
-- stops a status being set by hand with no record of what evidence produced it.
ALTER TABLE wnba.hypotheses
    DROP CONSTRAINT IF EXISTS hypothesis_verdicts_are_dated;
ALTER TABLE wnba.hypotheses
    ADD CONSTRAINT hypothesis_verdicts_are_dated CHECK (
        status = 'proposed' OR evaluated_at IS NOT NULL);

COMMENT ON COLUMN wnba.hypotheses.supporting_count IS
    'Settled episodes linked as supporting. Counted from hypothesis_episodes by the reviewer. A '
    'language model may argue about what the tally means; it never supplies the tally.';

-- --------------------------------------------------------------------------------------
-- Analyst rules: reviewable after activation, and withdrawable without a human
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.analyst_rules
    ADD COLUMN IF NOT EXISTS live_review        JSONB,
    ADD COLUMN IF NOT EXISTS last_reviewed_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS suspended_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS suspension_reason  TEXT,
    ADD COLUMN IF NOT EXISTS authored_by_model  BOOLEAN NOT NULL DEFAULT false;

-- Postgres names the inline CHECK from migration 022 `analyst_rules_status_check`. Dropping it
-- IF EXISTS keeps this migration replayable against a database where it has already gone.
ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS analyst_rules_status_check;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT analyst_rules_status_check CHECK (
        status IN ('proposed', 'backtested', 'active', 'suspended', 'rejected', 'retired'));

-- Suspension is automatic, so it has to explain itself. The reason is written by the reviewer
-- from measured firings, not by a model.
ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS suspension_states_its_evidence;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT suspension_states_its_evidence CHECK (
        status <> 'suspended' OR (
            suspended_at IS NOT NULL
            AND length(trim(coalesce(suspension_reason, ''))) > 0
            AND live_review IS NOT NULL));

COMMENT ON COLUMN wnba.analyst_rules.live_review IS
    'Measured behaviour of this rule since activation, from its own recorded firings. Refreshed '
    'weekly. A rule scoring harmful here is suspended automatically; nothing here can activate '
    'a rule or move it back to active, which remains the named-human path from migration 024.';

-- --------------------------------------------------------------------------------------
-- Research proposals: label what a model wrote
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.research_proposals
    ADD COLUMN IF NOT EXISTS authored_by_model BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS model_rationale   TEXT;

-- --------------------------------------------------------------------------------------
-- Error attribution: a second cause, and whether a model reviewed the call
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.error_attributions
    ADD COLUMN IF NOT EXISTS secondary_error TEXT,
    ADD COLUMN IF NOT EXISTS model_reviewed  BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN wnba.error_attributions.secondary_error IS
    'The next-most-likely cause. The deterministic classifier is ordered, so a large minutes '
    'miss used to mask an efficiency miss underneath it; recording both stops the proposal '
    'generator from only ever seeing the first branch of the if-chain.';

-- --------------------------------------------------------------------------------------
-- Every advisory call the model makes, whether or not the loop used it
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.model_advisories (
    advisory_id     UUID PRIMARY KEY,
    task            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL,
    model           TEXT NOT NULL,
    prompt_hash     TEXT,
    response_hash   TEXT,
    -- 'used' means the loop acted on the answer; 'fallback' means the call failed, refused, or
    -- failed validation and the deterministic path ran instead. A learning loop that silently
    -- degrades to templates when its provider is down looks identical to one that is working.
    disposition     TEXT NOT NULL CHECK (disposition IN ('used', 'fallback', 'rejected')),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_reason  TEXT,
    CONSTRAINT advisory_failures_are_explained CHECK (
        disposition = 'used' OR length(trim(coalesce(failure_reason, ''))) > 0)
);
CREATE INDEX IF NOT EXISTS model_advisories_task_idx
    ON wnba.model_advisories (task, requested_at DESC);

COMMENT ON TABLE wnba.model_advisories IS
    'Audit trail for DeepSeek participation in the learning loop. Nothing here is an input to a '
    'forecast. It exists so "how much of this week''s learning was model-authored, and how often '
    'did the provider fail?" is answerable from the database rather than from the logs.';

-- --------------------------------------------------------------------------------------
-- De-escalation: what the system did to itself when it measured its own drift
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.deescalation_events (
    event_id            UUID PRIMARY KEY,
    incident_id         UUID NOT NULL REFERENCES wnba.drift_incidents(incident_id),
    episode_id          UUID REFERENCES wnba.decision_episodes(episode_id),
    applied_at          TIMESTAMPTZ NOT NULL,
    response            TEXT NOT NULL,
    probability_before  DOUBLE PRECISION NOT NULL,
    probability_after   DOUBLE PRECISION NOT NULL,
    detail              JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The same asymmetry the rule engine enforces: an automatic response may only move a
    -- forecast toward even. There is no value here that could sharpen one.
    CONSTRAINT deescalation_never_increases_confidence CHECK (
        abs(probability_after - 0.5) <= abs(probability_before - 0.5) + 1e-9)
);
CREATE INDEX IF NOT EXISTS deescalation_events_episode_idx
    ON wnba.deescalation_events (episode_id);
CREATE INDEX IF NOT EXISTS deescalation_events_incident_idx
    ON wnba.deescalation_events (incident_id, applied_at DESC);

COMMIT;
