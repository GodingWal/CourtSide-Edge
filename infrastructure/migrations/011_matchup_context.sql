BEGIN;

CREATE TABLE IF NOT EXISTS wnba.matchup_contexts (
    context_id          UUID PRIMARY KEY,
    game_id             UUID NOT NULL REFERENCES wnba.games(game_id),
    team_id             UUID NOT NULL REFERENCES wnba.teams(team_id),
    opponent_team_id    UUID NOT NULL REFERENCES wnba.teams(team_id),
    prop_type           TEXT NOT NULL,
    expected_possessions DOUBLE PRECISION NOT NULL CHECK (expected_possessions BETWEEN 50 AND 130),
    pace_multiplier     DOUBLE PRECISION NOT NULL CHECK (pace_multiplier BETWEEN 0.90 AND 1.10),
    defense_multiplier  DOUBLE PRECISION NOT NULL CHECK (defense_multiplier BETWEEN 0.85 AND 1.15),
    expected_margin     DOUBLE PRECISION NOT NULL CHECK (expected_margin BETWEEN -50 AND 50),
    blowout_probability wnba.probability NOT NULL,
    team_rest_days      DOUBLE PRECISION NOT NULL CHECK (team_rest_days BETWEEN 0 AND 30),
    opponent_rest_days  DOUBLE PRECISION NOT NULL CHECK (opponent_rest_days BETWEEN 0 AND 30),
    opponent_sample     INTEGER NOT NULL CHECK (opponent_sample >= 0),
    confidence          wnba.probability NOT NULL,
    method_version      TEXT NOT NULL,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    system_from         TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to           TIMESTAMPTZ,
    CHECK (team_id <> opponent_team_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (system_to IS NULL OR system_to > system_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS matchup_contexts_current_uq
    ON wnba.matchup_contexts(game_id,team_id,prop_type) WHERE system_to IS NULL;

COMMIT;
