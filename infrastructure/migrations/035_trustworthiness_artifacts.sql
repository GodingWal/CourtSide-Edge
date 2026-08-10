-- Reliability artifacts for source scoring, selective prediction, adaptive uncertainty,
-- feature ablation and joint game-state diagnostics. All are append-only snapshots.
BEGIN;

CREATE TABLE IF NOT EXISTS wnba.source_reliability_snapshots (
    snapshot_id          UUID PRIMARY KEY,
    source               TEXT NOT NULL,
    calculated_at        TIMESTAMPTZ NOT NULL,
    sample_size          INTEGER NOT NULL CHECK (sample_size >= 0),
    reliability_weight   DOUBLE PRECISION NOT NULL CHECK (reliability_weight BETWEEN 0 AND 1),
    mean_absolute_error  DOUBLE PRECISION NOT NULL CHECK (mean_absolute_error >= 0),
    median_absolute_error DOUBLE PRECISION NOT NULL CHECK (median_absolute_error >= 0),
    freshness_rate       wnba.probability NOT NULL,
    detail               JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS source_reliability_latest_idx
    ON wnba.source_reliability_snapshots(source,calculated_at DESC);

CREATE TABLE IF NOT EXISTS wnba.selective_policy_snapshots (
    policy_id             UUID PRIMARY KEY,
    segment               TEXT NOT NULL,
    calculated_at         TIMESTAMPTZ NOT NULL,
    sample_size           INTEGER NOT NULL CHECK (sample_size >= 0),
    minimum_confidence    wnba.probability NOT NULL,
    coverage              wnba.probability NOT NULL,
    validation_log_loss   DOUBLE PRECISION,
    is_fitted             BOOLEAN NOT NULL,
    reason                TEXT NOT NULL,
    risk_coverage         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS selective_policy_latest_idx
    ON wnba.selective_policy_snapshots(segment,calculated_at DESC);

CREATE TABLE IF NOT EXISTS wnba.conformal_interval_snapshots (
    interval_id           UUID PRIMARY KEY,
    segment               TEXT NOT NULL,
    calculated_at         TIMESTAMPTZ NOT NULL,
    sample_size           INTEGER NOT NULL CHECK (sample_size >= 0),
    target_coverage       wnba.probability NOT NULL,
    empirical_coverage    wnba.probability NOT NULL,
    radius                DOUBLE PRECISION NOT NULL CHECK (radius >= 0),
    used_fallback         BOOLEAN NOT NULL,
    detail                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS conformal_interval_latest_idx
    ON wnba.conformal_interval_snapshots(segment,calculated_at DESC);

CREATE TABLE IF NOT EXISTS wnba.feature_ablation_results (
    ablation_id           UUID PRIMARY KEY,
    backtest_run_id       UUID REFERENCES wnba.backtest_runs(backtest_run_id),
    feature_name          TEXT NOT NULL,
    prop_type             TEXT NOT NULL DEFAULT 'all',
    calculated_at         TIMESTAMPTZ NOT NULL,
    sample_size           INTEGER NOT NULL CHECK (sample_size >= 0),
    mean_log_loss_gain    DOUBLE PRECISION NOT NULL,
    standard_error        DOUBLE PRECISION NOT NULL CHECK (standard_error >= 0),
    confidence_lower      DOUBLE PRECISION NOT NULL,
    confidence_upper      DOUBLE PRECISION NOT NULL,
    adjusted_alpha        DOUBLE PRECISION NOT NULL CHECK (adjusted_alpha > 0 AND adjusted_alpha < 1),
    verdict               TEXT NOT NULL CHECK (verdict IN ('helpful','harmful','inconclusive')),
    detail                JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS feature_ablation_latest_idx
    ON wnba.feature_ablation_results(feature_name,prop_type,calculated_at DESC);

CREATE TABLE IF NOT EXISTS wnba.joint_game_simulations (
    simulation_id         UUID PRIMARY KEY,
    game_id               UUID NOT NULL REFERENCES wnba.games(game_id),
    model_run_id          UUID REFERENCES wnba.model_runs(model_run_id),
    simulated_at          TIMESTAMPTZ NOT NULL,
    random_seed           INTEGER NOT NULL,
    simulations           INTEGER NOT NULL CHECK (simulations >= 100),
    player_keys           TEXT[] NOT NULL,
    covariance            JSONB NOT NULL,
    correlation           JSONB NOT NULL,
    scenario_summary      JSONB NOT NULL,
    UNIQUE (game_id,model_run_id)
);

-- Owner-facing line lifecycle. Observations remain immutable in prop_quotes; this view only
-- designates opening/current snapshots and never rewrites history.
CREATE OR REPLACE VIEW wnba.live_market_line_lifecycle AS
WITH ranked AS (
    SELECT q.*,
           row_number() OVER (
             PARTITION BY source,player_id,game_id,prop_type ORDER BY system_from
           ) AS opening_rank,
           row_number() OVER (
             PARTITION BY source,player_id,game_id,prop_type ORDER BY system_from DESC
           ) AS current_rank
    FROM wnba.prop_quotes q
    WHERE game_id IS NOT NULL
)
SELECT source,player_id,game_id,prop_type,
       max(line) FILTER (WHERE opening_rank=1) AS opening_line,
       max(system_from) FILTER (WHERE opening_rank=1) AS opening_observed_at,
       max(line) FILTER (WHERE current_rank=1) AS current_line,
       max(system_from) FILTER (WHERE current_rank=1) AS current_observed_at,
       count(*) AS snapshots
FROM ranked GROUP BY source,player_id,game_id,prop_type;

COMMIT;
