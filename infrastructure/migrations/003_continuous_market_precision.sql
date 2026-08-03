-- 003_continuous_market_precision.sql
--
-- Fantasy points are a weighted sum, not a countable event, and Underdog genuinely quotes
-- them at 37.05 and 39.55. The half-point constraint from 001 was rejecting those as
-- malformed -- discarding real rows from an archive that cannot be refetched later.
--
-- Countable markets keep the half-point rule, because for them a line off the half really is
-- a parsing bug and a silently wrong line is worse than a missing one.

BEGIN;

ALTER TABLE wnba.prop_quotes DROP CONSTRAINT IF EXISTS line_is_half_point;

ALTER TABLE wnba.prop_quotes ADD CONSTRAINT line_precision_matches_market CHECK (
    CASE WHEN prop_type = 'fantasy_points'
         THEN line = round(line, 2)
         ELSE (line * 2) = trunc(line * 2)
    END
);

COMMIT;
