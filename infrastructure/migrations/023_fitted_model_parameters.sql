-- 023_fitted_model_parameters.sql
--
-- Somewhere to keep the parameters the system learns about itself.
--
-- Until now every number that governed the forecast was a constant in the source: five
-- component weights, one 0.58 candidate threshold, and no correction at all for the
-- miscalibration the learning loop was already measuring and writing down. The loop could see
-- that a stated 0.62 landed 0.56 of the time, open a drift incident about it, and then go on
-- stating 0.62.
--
-- Three kinds of fitted parameter close that loop:
--
--   calibration_map   isotonic raw -> calibrated probability, per market
--   ensemble_weights  component weights fitted on settled log loss, per market
--   edge_shrinkage    the fraction of an apparent edge that survives regression to the mean
--
-- All three are versioned the same way the rest of the schema versions things: rows are never
-- updated, only superseded. A forecast made last Tuesday must remain explicable using the
-- parameters that were in force last Tuesday, and a parameter set that gets overwritten takes
-- that explanation with it.
--
-- Every one of them is also allowed to be absent. An unfitted market falls back to the identity
-- map, the prior weights, and the stated cold-start shrinkage -- which is the correct output
-- when there is not yet enough settled evidence to justify anything else.

BEGIN;

CREATE TABLE IF NOT EXISTS wnba.fitted_parameters (
    parameter_id   UUID PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (
                       kind IN ('calibration_map','ensemble_weights','edge_shrinkage')),
    market         TEXT NOT NULL,
    fitted_at      TIMESTAMPTZ NOT NULL,
    sample_size    INTEGER NOT NULL CHECK (sample_size >= 0),
    log_loss_gain  DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- FALSE means "we looked and the evidence did not support changing anything". That is a
    -- result worth storing: it is the difference between a market nobody has evaluated and a
    -- market where calibration was tried and honestly failed to help.
    is_fitted      BOOLEAN NOT NULL DEFAULT FALSE,
    payload        JSONB NOT NULL,
    superseded_at  TIMESTAMPTZ,
    CONSTRAINT fitted_parameters_superseded_after_fitting CHECK (
        superseded_at IS NULL OR superseded_at >= fitted_at)
);

-- At most one live parameter set per (kind, market). History stays queryable behind it.
CREATE UNIQUE INDEX IF NOT EXISTS fitted_parameters_live_idx
    ON wnba.fitted_parameters (kind, market) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS fitted_parameters_history_idx
    ON wnba.fitted_parameters (kind, market, fitted_at DESC);

-- --------------------------------------------------------------------------------------
-- Forecast provenance: what the model said before correction, and how wide it thought the
-- outcome was. Both nullable because forecasts written before this migration have neither.
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.stat_forecasts
    ADD COLUMN IF NOT EXISTS probability_over_raw wnba.probability,
    ADD COLUMN IF NOT EXISTS dispersion           DOUBLE PRECISION
        CHECK (dispersion IS NULL OR dispersion >= 1.0),
    ADD COLUMN IF NOT EXISTS scorer_version       TEXT;

COMMENT ON COLUMN wnba.stat_forecasts.probability_over_raw IS
    'P(over) before the calibration map was applied. Kept alongside the calibrated value so an '
    'audit can tell a well-calibrated model from a badly-calibrated one wearing a good map.';
COMMENT ON COLUMN wnba.stat_forecasts.dispersion IS
    'Variance-to-mean ratio used for this market. 1.0 is Poisson; points run above 2.';

-- --------------------------------------------------------------------------------------
-- Decision provenance: the gate used to be `probability >= 0.58` with nothing recording why.
-- --------------------------------------------------------------------------------------
ALTER TABLE wnba.decision_episodes
    ADD COLUMN IF NOT EXISTS shrunk_probability    wnba.probability,
    ADD COLUMN IF NOT EXISTS breakeven_probability wnba.probability,
    ADD COLUMN IF NOT EXISTS decision_reason       TEXT;

COMMENT ON COLUMN wnba.decision_episodes.shrunk_probability IS
    'Predicted probability after edge shrinkage. This, not the raw probability, is what the '
    'candidate gate compared against break-even.';
COMMENT ON COLUMN wnba.decision_episodes.breakeven_probability IS
    'Per-leg hit rate the payout table required at the time of the decision.';

COMMIT;
