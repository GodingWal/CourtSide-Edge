-- 027_evidence_feedback_loop.sql
--
-- Closing a loop that was measured but never read, and fixing the reason it could not have been.
--
-- Migration 026 added `evidence_rankings`, keyed by `evidence_id`, fed from the analyst usefulness
-- labels that `analyst_feedback` has carried since migration 018. The scoring worked. The loop
-- still could not close, for a reason that only shows up when you ask what the key means:
--
--   evidence_id = uuid5(content_hash + group_name)
--
-- Evidence is derived per prop, so its content hash is different for every prop, so every
-- evidence_id is seen exactly once and its ranking is computed from a sample of one label at
-- best. Ranking a population whose members never recur is not a feedback loop; it is a very
-- elaborate way of storing the prior.
--
-- What *does* recur is the evidence **kind** -- availability, role_effects, matchup, market,
-- forecast_audit, data_audit, historical_precedents. "Analysts keep marking the matchup block
-- misleading and the availability block useful" is a statement about the retrieval order that
-- accumulates across every prop on every slate, and it is the statement the feedback was
-- supposed to produce.
--
-- So: evidence rows learn their kind, and rankings are kept per kind alongside the per-id rows.
-- The per-id table stays because a specific piece of evidence can still be individually wrong and
-- that is worth recording; it is simply not the thing retrieval can learn from.

BEGIN;

ALTER TABLE wnba.evidence
    ADD COLUMN IF NOT EXISTS kind TEXT;

CREATE INDEX IF NOT EXISTS evidence_kind_idx ON wnba.evidence (kind) WHERE kind IS NOT NULL;

COMMENT ON COLUMN wnba.evidence.kind IS
    'Which slice of the frozen snapshot this evidence is: availability, role_effects, matchup, '
    'market, forecast_audit, data_audit, historical_precedents. The recurring unit -- evidence_id '
    'is content-hashed per prop and is seen exactly once, so it can never accumulate feedback.';

CREATE TABLE IF NOT EXISTS wnba.evidence_kind_rankings (
    kind              TEXT PRIMARY KEY,
    calculated_at     TIMESTAMPTZ NOT NULL,
    useful_labels     INTEGER NOT NULL DEFAULT 0 CHECK (useful_labels >= 0),
    misleading_labels INTEGER NOT NULL DEFAULT 0 CHECK (misleading_labels >= 0),
    citation_count    INTEGER NOT NULL DEFAULT 0 CHECK (citation_count >= 0),
    usefulness        wnba.probability NOT NULL,
    sample_size       INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0)
);

COMMENT ON TABLE wnba.evidence_kind_rankings IS
    'Retrieval ranking that can actually be learned. Ordering the bundle by this is the only '
    'thing analyst usefulness labels are allowed to change -- no kind is ever withheld from an '
    'analyst on the strength of its score, because evidence an analyst dislikes is still '
    'evidence and hiding it would make the next investigation worse, not better.';

COMMIT;
