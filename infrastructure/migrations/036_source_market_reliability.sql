-- Source trust is market-specific. Raw error across points and steals is not comparable, and
-- aggregating a source's market mix into one score confounds source quality with prop scale.
BEGIN;

ALTER TABLE wnba.source_reliability_snapshots
    ADD COLUMN IF NOT EXISTS prop_type TEXT NOT NULL DEFAULT 'all';

DROP INDEX IF EXISTS wnba.source_reliability_latest_idx;
CREATE INDEX source_reliability_latest_idx
    ON wnba.source_reliability_snapshots(source,prop_type,calculated_at DESC);

COMMIT;
