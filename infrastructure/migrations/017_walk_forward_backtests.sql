BEGIN;

CREATE TABLE IF NOT EXISTS wnba.backtest_runs (
    backtest_run_id UUID PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
    specification   JSONB NOT NULL,
    result_count    INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS wnba.backtest_results (
    result_id             UUID PRIMARY KEY,
    backtest_run_id       UUID NOT NULL REFERENCES wnba.backtest_runs(backtest_run_id),
    historical_quote_id   UUID NOT NULL REFERENCES wnba.historical_prop_quotes(historical_quote_id),
    snapshot_label        TEXT NOT NULL,
    forecast_as_of        TIMESTAMPTZ NOT NULL,
    model_name            TEXT NOT NULL,
    prop_type             TEXT NOT NULL,
    line                  NUMERIC(7,2) NOT NULL,
    projected_mean        DOUBLE PRECISION NOT NULL,
    predicted_probability wnba.probability NOT NULL,
    actual_stat           DOUBLE PRECISION NOT NULL,
    hit                   BOOLEAN NOT NULL,
    brier                 DOUBLE PRECISION NOT NULL,
    log_loss              DOUBLE PRECISION NOT NULL,
    history_games         INTEGER NOT NULL,
    quote_age_seconds     INTEGER NOT NULL CHECK (quote_age_seconds >= 0),
    UNIQUE (backtest_run_id,historical_quote_id,snapshot_label,model_name)
);
CREATE INDEX IF NOT EXISTS backtest_results_compare_idx
    ON wnba.backtest_results(backtest_run_id,model_name,snapshot_label,prop_type);

COMMIT;
