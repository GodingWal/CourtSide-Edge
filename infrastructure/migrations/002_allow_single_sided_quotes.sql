-- 002_allow_single_sided_quotes.sql
--
-- Operators routinely price only one direction on a low line ("3-Pointers Made 0.5,
-- higher -186", with no lower offered at all). The original constraint demanded both sides,
-- which would have silently discarded those rows.
--
-- For an archive that is the whole point of the system, dropping data is the worst available
-- outcome: historical odds cannot be refetched later at any price. So a two-sided market now
-- requires at least one price rather than both, and the missing-side problem is enforced
-- where it actually matters -- at pricing time, where devigging half a market would invent a
-- fair probability out of nothing.

BEGIN;

ALTER TABLE wnba.prop_quotes
    DROP CONSTRAINT IF EXISTS price_shape_matches_market_kind;

ALTER TABLE wnba.prop_quotes
    ADD CONSTRAINT price_shape_matches_market_kind CHECK (
        (market_kind = 'sportsbook_two_sided'
         AND (over_american_odds IS NOT NULL OR under_american_odds IS NOT NULL)
         AND (over_american_odds  IS NULL OR abs(over_american_odds)  >= 100)
         AND (under_american_odds IS NULL OR abs(under_american_odds) >= 100)
         AND over_multiplier IS NULL AND under_multiplier IS NULL)
        OR
        (market_kind = 'pickem'
         AND over_american_odds IS NULL AND under_american_odds IS NULL)
    );

-- Underdog is a real source of two-sided WNBA prices, so it needs to be a first-class value
-- rather than arriving as 'derived'.
ALTER DOMAIN wnba.source_name DROP CONSTRAINT IF EXISTS source_name_check;
ALTER DOMAIN wnba.source_name ADD CONSTRAINT source_name_check
    CHECK (VALUE IN ('espn','stats_wnba','wehoop','pbpstats','prizepicks','underdog',
                     'video','manual','derived','legacy_courtside','the_odds_api'));

COMMIT;
