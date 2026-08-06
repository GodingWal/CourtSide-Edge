-- 028_pick_settlement_and_agent_measurement.sql
--
-- Four things the platform was doing without a place to write the answer down.
--
--   1. **Owner picks never settled.** `pick_legs.result` has existed since migration 020 with a
--      'pending' default and no writer, so every slip the owner confirmed -- including every
--      DeepSeek-parsed one -- produced exactly no learning signal. Settlement needs two things
--      this adds: a resolved `player_id` (a typed name is not a join key) and a link to the
--      `decision_episodes` row that supplied the truth, so a settled leg can be re-derived
--      rather than trusted.
--
--   2. **Leg correlation was a number the user typed.** The entry pricer takes a scalar `rho`
--      and defaulted it to zero, which prices a same-player points/rebounds pair as if the two
--      were unrelated. `leg_correlations` stores the fitted pairwise estimate per relation and
--      market pair, with its sample size, so the pricer can default to measured structure and
--      say how much of it is measured.
--
--   3. **Agent credibility measured the wrong thing.** `agent_credibility.calibration` held mean
--      absolute error, which rewards hedging: an agent answering 0.5 forever scores 0.5 and
--      lands mid-table above a bold agent that is occasionally wrong. Brier is the proper score,
--      and `skill_vs_model` asks the only question that matters for an advisory seat -- whether
--      the agent beat the number it was advising on. An agent that merely agrees with the model
--      is free to be accurate and still worthless.
--
--   4. **Advisory calls had no cost.** `model_advisories` recorded prompt and response hashes but
--      not tokens or latency, so "what does a research run cost per projection" was unanswerable
--      and could not be traded off against the trigger rate.

BEGIN;

-- --------------------------------------------------------------------------------------
-- Owner pick settlement
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.pick_legs
    -- Resolved at confirmation time where the name matches exactly one player, NULL where it
    -- does not. NULL is a state settlement reports rather than guesses past: a fuzzy match that
    -- settles the wrong player is worse than a leg that stays pending forever.
    ADD COLUMN IF NOT EXISTS player_id UUID REFERENCES wnba.players(player_id),
    -- The episode whose settled outcome decided this leg. Present only for settled legs.
    ADD COLUMN IF NOT EXISTS episode_id UUID REFERENCES wnba.decision_episodes(episode_id),
    ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS actual_stat DOUBLE PRECISION;

ALTER TABLE wnba.pick_legs
    DROP CONSTRAINT IF EXISTS settled_legs_carry_their_evidence;
ALTER TABLE wnba.pick_legs
    ADD CONSTRAINT settled_legs_carry_their_evidence CHECK (
        result = 'pending' OR (settled_at IS NOT NULL AND episode_id IS NOT NULL));

CREATE INDEX IF NOT EXISTS pick_legs_pending_idx
    ON wnba.pick_legs(result) WHERE result = 'pending';
CREATE INDEX IF NOT EXISTS pick_legs_player_idx
    ON wnba.pick_legs(player_id, prop_type) WHERE player_id IS NOT NULL;

ALTER TABLE wnba.pick_slips
    ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legs_won SMALLINT,
    ADD COLUMN IF NOT EXISTS legs_settled SMALLINT,
    -- Realised multiple from the payout table, where the entry type has one. A sportsbook slip
    -- has no bundled table, so this stays NULL rather than inventing a price.
    ADD COLUMN IF NOT EXISTS realised_multiple NUMERIC(10,4);

-- --------------------------------------------------------------------------------------
-- Fitted leg correlation
-- --------------------------------------------------------------------------------------
-- One row per (relation, market pair). `relation` is structural rather than statistical: it
-- names *why* two legs might move together, which is what lets a pair with no settled history
-- inherit a stated prior instead of a zero.
CREATE TABLE IF NOT EXISTS wnba.leg_correlations (
    relation TEXT NOT NULL CHECK (relation IN (
        'same_player',        -- one player, two markets: usage is shared
        'same_team',          -- teammates: usage competes, pace is shared
        'opposing_team',      -- opposite sides of one game: pace is shared, defense is not
        'different_game')),   -- nothing structural; measured to confirm it stays near zero
    prop_a TEXT NOT NULL,
    prop_b TEXT NOT NULL CHECK (prop_b >= prop_a),  -- canonical ordering; the relation is symmetric
    sample_size INTEGER NOT NULL CHECK (sample_size >= 0),
    correlation DOUBLE PRECISION NOT NULL CHECK (correlation BETWEEN -1 AND 1),
    -- Standard error of the estimate. The console shows the band, not just the point: an entry
    -- whose EV changes sign across the band is not an opportunity, it is an unpriced position.
    standard_error DOUBLE PRECISION NOT NULL CHECK (standard_error >= 0),
    is_fitted BOOLEAN NOT NULL DEFAULT false,
    calculated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (relation, prop_a, prop_b)
);

-- --------------------------------------------------------------------------------------
-- Agent measurement that a hedging agent cannot game
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.agent_credibility
    ADD COLUMN IF NOT EXISTS brier DOUBLE PRECISION,
    -- Brier skill against the statistical probability on the same episodes. Positive means the
    -- agent added information; zero means it echoed the model; negative means it subtracted.
    ADD COLUMN IF NOT EXISTS skill_vs_model DOUBLE PRECISION,
    -- Which round the score covers. Round one is independent; round two has seen its peers.
    -- Scoring them together hides whether the debate helps or merely herds. Existing rows were
    -- all round two, which is what the previous query filtered on.
    ADD COLUMN IF NOT EXISTS round SMALLINT NOT NULL DEFAULT 2;

-- The round belongs in the key: one role now produces one score per round per run of the
-- refresh, and the old key would reject the second of them.
ALTER TABLE wnba.agent_credibility DROP CONSTRAINT IF EXISTS agent_credibility_pkey;
ALTER TABLE wnba.agent_credibility
    ADD PRIMARY KEY (agent_role, domain, calculated_at, round);

-- --------------------------------------------------------------------------------------
-- Advisory cost
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.model_advisories
    ADD COLUMN IF NOT EXISTS latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER
        CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    ADD COLUMN IF NOT EXISTS attempts SMALLINT CHECK (attempts IS NULL OR attempts >= 1);

COMMIT;
