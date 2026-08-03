BEGIN;

CREATE TABLE IF NOT EXISTS wnba.teammate_role_effects (
    effect_id          UUID PRIMARY KEY,
    player_id          UUID NOT NULL REFERENCES wnba.players(player_id),
    teammate_id        UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id            UUID NOT NULL REFERENCES wnba.games(game_id),
    prop_type          TEXT NOT NULL,
    rate_multiplier    DOUBLE PRECISION NOT NULL CHECK (rate_multiplier BETWEEN 0.70 AND 1.30),
    minutes_delta      DOUBLE PRECISION NOT NULL CHECK (minutes_delta BETWEEN -5.0 AND 5.0),
    games_with         INTEGER NOT NULL CHECK (games_with >= 0),
    games_without      INTEGER NOT NULL CHECK (games_without >= 0),
    confidence         wnba.probability NOT NULL,
    method_version     TEXT NOT NULL,
    valid_from         TIMESTAMPTZ NOT NULL,
    valid_to           TIMESTAMPTZ,
    system_from        TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to          TIMESTAMPTZ,
    CHECK (player_id <> teammate_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (system_to IS NULL OR system_to > system_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS teammate_effects_current_uq
    ON wnba.teammate_role_effects(player_id,teammate_id,game_id,prop_type)
    WHERE system_to IS NULL;
CREATE INDEX IF NOT EXISTS teammate_effects_game_idx
    ON wnba.teammate_role_effects(game_id,player_id) WHERE system_to IS NULL;

COMMIT;
