BEGIN;

ALTER TABLE wnba.projected_roles
    ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'role-baseline-0.1.0',
    ADD COLUMN IF NOT EXISTS feature_summary JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS projected_roles_current_uq
    ON wnba.projected_roles(player_id,game_id) WHERE system_to IS NULL;

COMMIT;
