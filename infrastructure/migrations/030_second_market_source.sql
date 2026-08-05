-- 030_second_market_source.sql
--
-- The second market source, and the record that has to exist before it may be polled.
--
-- The consensus layer has been built and tested since migration 015: weighted-median line, price
-- dispersion, best-price selection, source reliability and an explicit pre-lock closing
-- designation. It runs today against one source and correctly reports itself as uncorroborated,
-- because one source agreeing with itself is not agreement.
--
-- WHY A TERMS VERIFICATION IS A DATABASE CONSTRAINT AND NOT A README LINE
-- ----------------------------------------------------------------------
-- The roadmap has said for months that what remained here "is not code": choosing a second
-- source means reading that operator's terms, confirming the collection is permitted, and dating
-- that verification. That is still true, and it is precisely why the check belongs in the
-- schema. A note in a runbook saying "check the terms first" is advice. A row that must exist,
-- name a human, carry the terms URL and a hash of what they said on the day, and be re-verified
-- when it expires, is a control.
--
-- So: an adapter may not write quotes for a source with no current verification. The ingestion
-- path reads this table and refuses, which means enabling a source is a deliberate act with a
-- name attached rather than the side effect of setting an environment variable.
--
-- The verification is *not* legal advice and does not claim the collection is lawful. It records
-- that a named person read the terms on a date and what they concluded, which is the thing that
-- can be audited later. Nothing here can determine whether that conclusion was right.
--
-- WHY PAYOUT TABLES GET THE SAME TREATMENT
-- ----------------------------------------
-- The candidate gate compares a shrunk probability against the break-even implied by a payout
-- table, and the bundled tables are unverified constants. A break-even that is wrong by two
-- points turns a losing filter into a passing one, silently, on every decision. Verified tables
-- are dated, attributed and hashed for the same reason.

BEGIN;

-- The Odds API: a commercial aggregator with a published free tier, an API key, and terms that
-- contemplate programmatic access. Added to the domain so quotes can be attributed to it; adding
-- the name does not enable it, because the verification row below is what enables it.
ALTER DOMAIN wnba.source_name DROP CONSTRAINT IF EXISTS source_name_check;
ALTER DOMAIN wnba.source_name ADD CONSTRAINT source_name_check
    CHECK (VALUE IN ('espn','stats_wnba','wehoop','pbpstats','prizepicks','underdog',
                     'the_odds_api','video','manual','derived','legacy_courtside'));

CREATE TABLE IF NOT EXISTS wnba.source_terms_verifications (
    verification_id  UUID PRIMARY KEY,
    source           wnba.source_name NOT NULL,
    verified_by      TEXT NOT NULL CHECK (length(trim(verified_by)) >= 2),
    verified_at      TIMESTAMPTZ NOT NULL,
    -- Terms change without announcement. A verification that never expires is a verification of
    -- whatever the terms said on some forgotten afternoon.
    expires_at       TIMESTAMPTZ NOT NULL,
    terms_url        TEXT NOT NULL CHECK (terms_url ~ '^https?://'),
    terms_sha256     TEXT NOT NULL CHECK (terms_sha256 ~ '^[0-9a-f]{64}$'),
    permitted_use    TEXT NOT NULL CHECK (length(trim(permitted_use)) >= 20),
    rate_limit       TEXT NOT NULL DEFAULT '',
    attribution_required BOOLEAN NOT NULL DEFAULT false,
    redistribution_permitted BOOLEAN NOT NULL DEFAULT false,
    conclusion       TEXT NOT NULL CHECK (conclusion IN ('permitted','prohibited','unclear')),
    notes            TEXT NOT NULL DEFAULT '',
    revoked_at       TIMESTAMPTZ,
    revoked_reason   TEXT,
    CONSTRAINT verification_expires_after_it_was_made CHECK (expires_at > verified_at),
    CONSTRAINT revocation_carries_a_reason CHECK (
        revoked_at IS NULL OR length(trim(coalesce(revoked_reason, ''))) > 0)
);

CREATE INDEX IF NOT EXISTS source_terms_current_idx
    ON wnba.source_terms_verifications (source, expires_at DESC)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE wnba.source_terms_verifications IS
    'A named person read this operator''s terms on this date and reached this conclusion. Not '
    'legal advice and not a claim that collection is lawful -- a record of who decided, when, '
    'and on the basis of what text, which is the part that can be audited afterwards.';

COMMENT ON COLUMN wnba.source_terms_verifications.conclusion IS
    'Only ''permitted'' enables ingestion. ''unclear'' is a real and useful outcome: it records '
    'that somebody looked and could not tell, which is different from nobody having looked.';

-- --------------------------------------------------------------------------------------
-- Payout tables, verified rather than assumed
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.payout_verifications (
    verification_id UUID PRIMARY KEY,
    source          wnba.source_name NOT NULL,
    entry_type      TEXT NOT NULL,
    legs            SMALLINT NOT NULL CHECK (legs BETWEEN 2 AND 12),
    verified_by     TEXT NOT NULL CHECK (length(trim(verified_by)) >= 2),
    verified_at     TIMESTAMPTZ NOT NULL,
    observed_payouts JSONB NOT NULL,
    breakeven_probability wnba.probability NOT NULL,
    settlement_rules TEXT NOT NULL CHECK (length(trim(settlement_rules)) >= 20),
    screenshot_sha256 TEXT CHECK (screenshot_sha256 IS NULL OR screenshot_sha256 ~ '^[0-9a-f]{64}$'),
    superseded_at   TIMESTAMPTZ,
    UNIQUE (source, entry_type, legs, verified_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS payout_verifications_live_idx
    ON wnba.payout_verifications (source, entry_type, legs) WHERE superseded_at IS NULL;

COMMENT ON TABLE wnba.payout_verifications IS
    'What the product actually paid, observed and dated. The candidate gate compares a shrunk '
    'probability against a break-even derived from a payout table; a table that is wrong by two '
    'points silently turns a losing filter into a passing one on every decision.';

COMMENT ON COLUMN wnba.payout_verifications.settlement_rules IS
    'How the operator settles voids, scratches, DNPs and stat corrections. The rules decide what '
    'a settled record even means, and they differ between products in ways that change results.';

-- --------------------------------------------------------------------------------------
-- Cross-source consensus, persisted so closing-line value has something to read
-- --------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.market_consensus (
    consensus_id      UUID PRIMARY KEY,
    player_id         UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id           UUID NOT NULL REFERENCES wnba.games(game_id),
    prop_type         TEXT NOT NULL,
    observed_at       TIMESTAMPTZ NOT NULL,
    -- Quotes are only comparable when they were observed close together. A line from one source
    -- an hour after another's is not a disagreement, it is two different moments.
    synchronised_within_seconds INTEGER NOT NULL CHECK (synchronised_within_seconds >= 0),
    source_count      SMALLINT NOT NULL CHECK (source_count >= 1),
    sources           TEXT[] NOT NULL,
    consensus_line    NUMERIC(7,2) NOT NULL,
    line_dispersion   DOUBLE PRECISION NOT NULL DEFAULT 0,
    price_dispersion  DOUBLE PRECISION,
    best_over_source  TEXT,
    best_under_source TEXT,
    is_corroborated   BOOLEAN NOT NULL DEFAULT false,
    designation       TEXT NOT NULL CHECK (designation IN ('opening','intermediate','closing')),
    detail            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (player_id, game_id, prop_type, observed_at)
);
CREATE INDEX IF NOT EXISTS market_consensus_market_idx
    ON wnba.market_consensus (player_id, game_id, prop_type, observed_at DESC);
CREATE INDEX IF NOT EXISTS market_consensus_designation_idx
    ON wnba.market_consensus (designation, observed_at DESC);

COMMENT ON COLUMN wnba.market_consensus.is_corroborated IS
    'False whenever one source produced the row. A single operator agreeing with itself is not a '
    'consensus, and closing-line value measured against it is measuring that operator''s own '
    'drift.';

COMMIT;
