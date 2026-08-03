BEGIN;

ALTER TABLE wnba.historical_prop_quotes
    ADD COLUMN raw_over_price INTEGER,
    ADD COLUMN raw_under_price INTEGER,
    ADD COLUMN price_encoding TEXT NOT NULL DEFAULT 'unknown_integer';

COMMIT;
