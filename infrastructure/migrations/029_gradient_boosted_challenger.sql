-- 029_gradient_boosted_challenger.sql
--
-- A training set and a place to keep a fitted model, both built so the tree challenger cannot
-- repeat the defect that made the first walk-forward harness worthless.
--
-- THE TRAINING SET IS THE REPLAY'S OWN INPUTS
-- -------------------------------------------
-- The obvious way to build a training set is a second query that reconstructs what was knowable
-- at each historical moment. That is a second implementation of the point-in-time logic, and
-- this codebase has already paid for having two implementations of the forecast: the harness
-- scored a model that did not exist for months, and the model card cited its Brier score.
--
-- So there is no second implementation. The walk-forward replay already constructs a
-- `ScoringInputs` for every historical quote at every snapshot, using only rows written before
-- that moment. As it runs, it now also writes the feature vector it derived from those inputs.
-- The training set is therefore *definitionally* point-in-time: if the replay could not see a
-- fact, no training row contains it.
--
-- WHY THE MODEL IS STORED AS TEXT AND NOT A PICKLE
-- ------------------------------------------------
-- LightGBM boosters serialise to a plain text description of the trees. Loading one executes
-- nothing. A pickle would be shorter to write and would make model loading an arbitrary-code
-- execution path fed from a database column -- which is the same class of hole the rule DSL
-- exists to avoid, and it would be strange to refuse a language model the ability to emit Python
-- while happily unpickling whatever is in a table.
--
-- WHAT `is_active` DOES NOT MEAN
-- ------------------------------
-- It means "this is the artifact the challenger loads". It does not mean promoted, approved or
-- trusted. A fitted challenger is still a challenger: it reaches no recommendation, and moving
-- it to champion still requires the named-human promotion path in migration 025.

BEGIN;

CREATE TABLE IF NOT EXISTS wnba.training_examples (
    example_id          UUID PRIMARY KEY,
    backtest_run_id     UUID NOT NULL REFERENCES wnba.backtest_runs(backtest_run_id),
    historical_quote_id UUID NOT NULL REFERENCES wnba.historical_prop_quotes(historical_quote_id),
    snapshot_label      TEXT NOT NULL,
    as_of               TIMESTAMPTZ NOT NULL,
    game_id             UUID NOT NULL REFERENCES wnba.games(game_id),
    player_id           UUID NOT NULL REFERENCES wnba.players(player_id),
    prop_type           TEXT NOT NULL,
    line                NUMERIC(7,2) NOT NULL,
    features            JSONB NOT NULL,
    -- The label is whether the over landed. Stored rather than recomputed so a training set is
    -- reproducible from its own rows.
    label               BOOLEAN NOT NULL,
    actual_stat         DOUBLE PRECISION NOT NULL,
    UNIQUE (backtest_run_id, historical_quote_id, snapshot_label)
);
CREATE INDEX IF NOT EXISTS training_examples_time_idx
    ON wnba.training_examples (as_of);
CREATE INDEX IF NOT EXISTS training_examples_run_idx
    ON wnba.training_examples (backtest_run_id, prop_type);

COMMENT ON TABLE wnba.training_examples IS
    'Feature vectors derived from the walk-forward replay''s own point-in-time ScoringInputs. '
    'Not a second reconstruction of history: if the replay could not see a fact, no row here '
    'contains it.';

CREATE TABLE IF NOT EXISTS wnba.model_artifacts (
    artifact_id     UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    semver          TEXT NOT NULL,
    fitted_at       TIMESTAMPTZ NOT NULL,
    framework       TEXT NOT NULL,
    -- Plain text, never a pickle. Loading this executes nothing.
    payload         TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    feature_names   TEXT[] NOT NULL CHECK (cardinality(feature_names) > 0),
    training_rows   INTEGER NOT NULL CHECK (training_rows >= 0),
    training_markets INTEGER NOT NULL DEFAULT 0 CHECK (training_markets >= 0),
    trained_through TIMESTAMPTZ,
    hyperparameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    fold_metrics    JSONB NOT NULL DEFAULT '[]'::jsonb,
    holdout_log_loss DOUBLE PRECISION,
    holdout_brier    DOUBLE PRECISION,
    baseline_log_loss DOUBLE PRECISION,
    -- "This is the artifact the challenger loads." Not promoted, not approved, not trusted.
    is_active       BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT NOT NULL DEFAULT '',
    UNIQUE (name, semver, fitted_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS model_artifacts_one_active_per_name_idx
    ON wnba.model_artifacts (name) WHERE is_active;

COMMENT ON COLUMN wnba.model_artifacts.is_active IS
    'Which artifact the challenger loads. A fitted challenger is still a challenger: it reaches '
    'no recommendation, and moving it to champion requires the named-human promotion path.';

COMMENT ON COLUMN wnba.model_artifacts.baseline_log_loss IS
    'Held-out log loss of the market prior over the same rows. A tree model that cannot beat the '
    'price it was shown has learned the price.';

COMMIT;
