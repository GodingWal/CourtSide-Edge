-- 031_game_market_lines.sql
-- Start a game-spread and game-total history.
--
-- The blowout minutes haircut was gated to zero (PR #71) because the in-house
-- blowout_probability is quantized and confounded with pace (r = -0.80). A refit worth the
-- name needs the market's own closing spreads, and those cannot be bought retroactively --
-- so the platform records them from the ESPN scoreboard odds block, the only game-market
-- feed it can reach today, on the 5-minute CLV timer. Same archiver discipline as
-- prop_quotes: record-only, change-defined rows, nothing forecasts off this table yet.

BEGIN;

CREATE TABLE wnba.game_market_lines (
    line_id     uuid         NOT NULL,
    game_id     uuid         NOT NULL REFERENCES wnba.games (game_id),
    source      text         NOT NULL,
    provider    text         NOT NULL,
    home_spread numeric(5,2),
    over_under  numeric(5,2),
    details     text,
    observed_at timestamptz  NOT NULL,
    PRIMARY KEY (line_id)
);

CREATE INDEX game_market_lines_game_idx
    ON wnba.game_market_lines (game_id, observed_at DESC);

COMMIT;
