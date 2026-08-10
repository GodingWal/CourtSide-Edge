-- Owner minutes overrides: the recorded action of a named human, never silent edits.
BEGIN;

CREATE TABLE IF NOT EXISTS wnba.minutes_overrides (
    override_id uuid PRIMARY KEY,
    player_id uuid NOT NULL REFERENCES wnba.players(player_id),
    game_id uuid NOT NULL REFERENCES wnba.games(game_id),
    minutes double precision NOT NULL CHECK (minutes > 0 AND minutes <= 48),
    reason text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz
);
CREATE INDEX IF NOT EXISTS minutes_overrides_live_idx
    ON wnba.minutes_overrides (player_id, game_id) WHERE superseded_at IS NULL;

COMMIT;
