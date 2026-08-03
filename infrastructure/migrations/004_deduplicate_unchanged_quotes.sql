-- 004_deduplicate_unchanged_quotes.sql
--
-- A poll is not a market move. Underdog keeps the same source_quote_id and updated_at while
-- a line is unchanged, so inserting the board every 15 minutes fabricates apparent history.
-- Keep the earliest observation of each source state, remove already-created duplicates,
-- and make subsequent polls idempotent at the database boundary.

BEGIN;

WITH ranked AS (
    SELECT quote_id,
           row_number() OVER (
               PARTITION BY source, source_quote_id, valid_from, line,
                            COALESCE(over_american_odds, 0),
                            COALESCE(under_american_odds, 0),
                            COALESCE(over_multiplier, 0),
                            COALESCE(under_multiplier, 0),
                            is_promotional, is_available
               ORDER BY system_from, quote_id
           ) AS duplicate_number
    FROM wnba.prop_quotes
)
DELETE FROM wnba.prop_quotes q
USING ranked r
WHERE q.quote_id = r.quote_id
  AND r.duplicate_number > 1;

CREATE UNIQUE INDEX IF NOT EXISTS prop_quotes_source_state_uq
    ON wnba.prop_quotes (
        source,
        source_quote_id,
        valid_from,
        line,
        COALESCE(over_american_odds, 0),
        COALESCE(under_american_odds, 0),
        COALESCE(over_multiplier, 0),
        COALESCE(under_multiplier, 0),
        is_promotional,
        is_available
    );

COMMIT;
