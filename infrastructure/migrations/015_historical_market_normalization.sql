BEGIN;

CREATE TABLE IF NOT EXISTS wnba.legacy_event_mappings (
    source_event_id    TEXT PRIMARY KEY,
    game_id            UUID REFERENCES wnba.games(game_id),
    status             TEXT NOT NULL CHECK (status IN ('resolved','ambiguous','unresolved')),
    confidence         wnba.probability NOT NULL,
    player_overlap     INTEGER NOT NULL CHECK (player_overlap >= 0),
    resolved_players   INTEGER NOT NULL CHECK (resolved_players >= 0),
    method             TEXT NOT NULL,
    reviewed_by        TEXT,
    mapped_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wnba.historical_prop_quotes (
    historical_quote_id UUID PRIMARY KEY,
    legacy_source_row_id BIGINT NOT NULL UNIQUE REFERENCES wnba.legacy_prop_quotes(source_row_id),
    source_event_id      TEXT NOT NULL REFERENCES wnba.legacy_event_mappings(source_event_id),
    game_id              UUID NOT NULL REFERENCES wnba.games(game_id),
    player_id            UUID NOT NULL REFERENCES wnba.players(player_id),
    bookmaker            TEXT NOT NULL,
    prop_type            TEXT NOT NULL,
    line                 NUMERIC(7,2) NOT NULL,
    over_american_odds   INTEGER,
    under_american_odds  INTEGER,
    observed_at          TIMESTAMPTZ NOT NULL,
    scheduled_tipoff     TIMESTAMPTZ NOT NULL,
    mapping_confidence   wnba.probability NOT NULL,
    imported_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (observed_at <= scheduled_tipoff),
    CHECK (over_american_odds IS NULL OR abs(over_american_odds) >= 100),
    CHECK (under_american_odds IS NULL OR abs(under_american_odds) >= 100)
);
CREATE INDEX IF NOT EXISTS historical_quotes_market_idx
    ON wnba.historical_prop_quotes(game_id,player_id,prop_type,bookmaker,observed_at);

CREATE OR REPLACE VIEW wnba.historical_market_open_close AS
WITH ranked AS (
    SELECT q.*,
           row_number() OVER (
             PARTITION BY game_id,player_id,prop_type,bookmaker ORDER BY observed_at
           ) AS opening_rank,
           row_number() OVER (
             PARTITION BY game_id,player_id,prop_type,bookmaker ORDER BY observed_at DESC
           ) AS closing_rank
    FROM wnba.historical_prop_quotes q
)
SELECT game_id,player_id,prop_type,bookmaker,
       max(line) FILTER (WHERE opening_rank=1) AS opening_line,
       max(observed_at) FILTER (WHERE opening_rank=1) AS opening_observed_at,
       max(line) FILTER (WHERE closing_rank=1) AS closing_line,
       max(observed_at) FILTER (WHERE closing_rank=1) AS closing_observed_at,
       count(*) AS snapshots
FROM ranked GROUP BY game_id,player_id,prop_type,bookmaker;

COMMIT;
