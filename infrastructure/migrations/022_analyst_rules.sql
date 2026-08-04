-- 022_analyst_rules.sql
--
-- Codified analyst knowledge. The PAT-shaped layer: judgements that used to live in a
-- paragraph somebody wrote once now live as structured rows the system executes every time.
--
-- Rules are DATA, never code. The definition column holds a document over a closed
-- vocabulary (see packages/wnba_rules/dsl.py); there is no eval anywhere in the path.
--
-- Two constraints carry the governance:
--   * a rule cannot be ACTIVE without a named human approver
--   * every firing is recorded, including shadow firings, so an adjustment can always be
--     traced back to the rule that made it and the person who approved that rule

BEGIN;

CREATE TABLE IF NOT EXISTS wnba.analyst_rules (
    rule_id      TEXT PRIMARY KEY CHECK (rule_id ~ '^[a-z0-9_]+$'),
    title        TEXT NOT NULL,
    rationale    TEXT NOT NULL CHECK (length(rationale) >= 20),
    definition   JSONB NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('proposed','backtested','active','rejected','retired')),
    priority     SMALLINT NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    proposed_by  TEXT NOT NULL,
    proposed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by  TEXT,
    approved_at  TIMESTAMPTZ,
    retired_at   TIMESTAMPTZ,
    backtest     JSONB,
    CONSTRAINT active_rules_require_a_human CHECK (
        status <> 'active' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    -- A rule may not go live without evidence of what it would have done. This is the same
    -- bar model promotion has to clear, and for the same reason.
    CONSTRAINT active_rules_require_a_backtest CHECK (status <> 'active' OR backtest IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS wnba.rule_firings (
    firing_id           UUID PRIMARY KEY,
    rule_id             TEXT NOT NULL REFERENCES wnba.analyst_rules(rule_id),
    episode_id          UUID,
    fired_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    shadow              BOOLEAN NOT NULL,
    action_kind         TEXT NOT NULL,
    probability_before  DOUBLE PRECISION NOT NULL,
    probability_after   DOUBLE PRECISION NOT NULL,
    -- The safety asymmetry, enforced by the database as well as by the type system: a firing
    -- may never move a forecast further from even.
    CONSTRAINT firings_never_increase_confidence CHECK (
        abs(probability_after - 0.5) <= abs(probability_before - 0.5) + 1e-9)
);
CREATE INDEX IF NOT EXISTS rule_firings_rule_idx ON wnba.rule_firings (rule_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS rule_firings_episode_idx ON wnba.rule_firings (episode_id);

COMMIT;
