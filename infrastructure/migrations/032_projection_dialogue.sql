-- 032_projection_dialogue.sql
-- Owner dialogue with the analyst model, persisted per projection.
--
-- The audit drawer used to offer four fixed feedback buttons and nothing else: a label, never
-- a conversation. This starts the conversation. Each projection carries at most one dialogue
-- per analyst; messages keep their exact provider payload so a later turn replays the
-- transcript verbatim rather than a lossy reconstruction.
--
-- Analysis-only: nothing here writes to any market, book, or account.

BEGIN;

CREATE TABLE wnba.projection_dialogues (
    dialogue_id   uuid        NOT NULL,
    projection_id uuid        NOT NULL REFERENCES wnba.stat_forecasts (projection_id),
    analyst       text        NOT NULL DEFAULT 'owner',
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    PRIMARY KEY (dialogue_id)
);

CREATE UNIQUE INDEX projection_dialogues_projection_idx
    ON wnba.projection_dialogues (projection_id, analyst);

CREATE TABLE wnba.dialogue_messages (
    message_id  uuid        NOT NULL,
    dialogue_id uuid        NOT NULL REFERENCES wnba.projection_dialogues (dialogue_id),
    role        text        NOT NULL,
    content     text        NOT NULL,
    payload     jsonb,
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (message_id)
);

CREATE INDEX dialogue_messages_dialogue_idx
    ON wnba.dialogue_messages (dialogue_id, created_at);

COMMIT;
