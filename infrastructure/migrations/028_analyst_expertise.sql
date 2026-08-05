-- 028_analyst_expertise.sql
--
-- Making the analyst side of the loop as measurable as the agent side, and fixing the reason it
-- was not.
--
-- Three things were promised and not delivered:
--
-- 1. `analyst_feedback.evidence_ids_useful` and `.evidence_ids_misleading` have existed since
--    migration 018 and the retrieval ranking added in 027 reads them. Nothing has ever been able
--    to *write* them: the request model has no such fields and the INSERT omits the columns. The
--    ranking's input is not sparse, it is structurally empty. Columns that only one side of a
--    loop can reach look exactly like a working loop until somebody checks.
--
-- 2. `decision_episodes.analyst_decision` and `.analyst_override_reason` have existed since
--    migration 001. An override is the single most informative label an analyst produces -- it is
--    them saying "the model is wrong here, and here is why" *before* the outcome is known -- and
--    nothing has ever scored one against what happened.
--
-- 3. Agent credibility is computed per domain. Analyst expertise is not computed at all, so the
--    one participant whose judgement actually governs promotion has no measured track record.
--
-- WHY THE WEAKEST ASSUMPTION GETS A CLOSED VOCABULARY
-- --------------------------------------------------
-- The free-text `weakest_assumption` column stays, because the sentence an analyst writes at the
-- time is worth more than any taxonomy. But free text cannot be counted, and "which assumption
-- keeps turning out to be the weak one" is a question that only means anything in aggregate. So
-- the label also carries a kind from a closed list, which is what the expertise calculation and
-- the error-attribution comparison read.
--
-- WHAT EXPERTISE IS AND IS NOT USED FOR
-- ------------------------------------
-- It is displayed, and it is the honest answer to "should I trust my own instinct about minutes
-- more than the model's". It does not gate anything, weight anything, or feed a forecast. There
-- is exactly one human here; a score that started silencing them would be a bug with a
-- statistics paper attached.

BEGIN;

-- --------------------------------------------------------------------------------------
-- Feedback: the fields the workflow actually collects
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.analyst_feedback
    ADD COLUMN IF NOT EXISTS weakest_assumption_kind TEXT,
    ADD COLUMN IF NOT EXISTS domain                  TEXT,
    ADD COLUMN IF NOT EXISTS labelled_after_outcome  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS confidence_in_label     wnba.probability;

ALTER TABLE wnba.analyst_feedback
    DROP CONSTRAINT IF EXISTS weakest_assumption_kind_is_known;
ALTER TABLE wnba.analyst_feedback
    ADD CONSTRAINT weakest_assumption_kind_is_known CHECK (
        weakest_assumption_kind IS NULL OR weakest_assumption_kind IN (
            'minutes','role','usage_rate','efficiency','matchup','market_price',
            'availability','data_quality','sample_size','correlation','other'));

COMMENT ON COLUMN wnba.analyst_feedback.labelled_after_outcome IS
    'Whether the analyst had already seen the result when they labelled it. A label written '
    'after the outcome is contaminated by it -- useful for error attribution, useless for '
    'measuring foresight -- and the expertise calculation reads only the ones written before.';

COMMENT ON COLUMN wnba.analyst_feedback.weakest_assumption_kind IS
    'Closed-vocabulary companion to the free-text weakest_assumption. The sentence is worth more '
    'individually; the kind is the only one of the two that can be counted.';

-- --------------------------------------------------------------------------------------
-- Overrides, scored against what actually happened
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.override_evaluations (
    evaluation_id         UUID PRIMARY KEY,
    episode_id            UUID NOT NULL UNIQUE REFERENCES wnba.decision_episodes(episode_id),
    evaluated_at          TIMESTAMPTZ NOT NULL,
    analyst               TEXT NOT NULL,
    analyst_decision      TEXT NOT NULL,
    override_reason       TEXT,
    system_recommendation TEXT NOT NULL,
    system_probability    wnba.probability NOT NULL,
    breakeven             wnba.probability,
    hit                   BOOLEAN NOT NULL,
    system_was_right      BOOLEAN NOT NULL,
    analyst_was_right     BOOLEAN NOT NULL,
    verdict               TEXT NOT NULL CHECK (verdict IN (
                              'override_helped','override_hurt','override_neutral',
                              'agreed_and_right','agreed_and_wrong')),
    detail                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS override_evaluations_analyst_idx
    ON wnba.override_evaluations (analyst, evaluated_at DESC);

COMMENT ON TABLE wnba.override_evaluations IS
    'One row per settled episode the analyst expressed a view on. An override is the most '
    'informative label available -- a claim that the model is wrong, made before the outcome is '
    'known -- and until now nothing scored one. Both directions are recorded: overrides that '
    'helped and overrides that cost, because an analyst who only remembers the good ones is the '
    'default state of every analyst.';

-- --------------------------------------------------------------------------------------
-- Analyst expertise, by domain, pooled like everything else
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.analyst_expertise (
    analyst             TEXT NOT NULL,
    domain              TEXT NOT NULL,
    calculated_at       TIMESTAMPTZ NOT NULL,
    labels              INTEGER NOT NULL DEFAULT 0 CHECK (labels >= 0),
    overrides           INTEGER NOT NULL DEFAULT 0 CHECK (overrides >= 0),
    overrides_helped    INTEGER NOT NULL DEFAULT 0 CHECK (overrides_helped >= 0),
    override_hit_rate   DOUBLE PRECISION,
    label_agreement     DOUBLE PRECISION,
    expertise           wnba.probability NOT NULL,
    sample_size         INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0),
    is_provisional      BOOLEAN NOT NULL DEFAULT true,
    method              TEXT NOT NULL DEFAULT '',
    detail              JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (analyst, domain, calculated_at)
);

COMMENT ON TABLE wnba.analyst_expertise IS
    'Measured, pooled, and displayed only. It gates nothing and weights nothing: there is one '
    'human here and a score that began silencing them would be a bug with a statistics paper '
    'attached. `is_provisional` stays true until the domain has enough labels to mean anything, '
    'and provisional scores are shown as provisional rather than quietly rounded up.';

COMMIT;
