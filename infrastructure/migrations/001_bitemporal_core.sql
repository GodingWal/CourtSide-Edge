-- 001_bitemporal_core.sql
--
-- The bitemporal core. Every fact table carries two independent time axes:
--
--   valid_from / valid_to    when the fact was true in the basketball world
--   system_from / system_to  when this platform learned it
--
-- A NULL upper bound means "still open" -- still true, or still our current belief.
-- Correcting a fact NEVER updates a row. It closes the old row on the system axis and
-- inserts a new one. That is what makes "what did we believe at 15:00?" answerable, and it
-- is the difference between a system that learns and one that revises its memories.
--
-- Ranges are [closed, open): a row with valid_to = T was NOT valid at T.

BEGIN;

CREATE SCHEMA IF NOT EXISTS wnba;
SET LOCAL search_path = wnba, public;

CREATE TABLE IF NOT EXISTS wnba.schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);

-- Sources are TEXT with a CHECK rather than a Postgres ENUM: adding a source should be a
-- one-line migration, not an ALTER TYPE dance.
CREATE DOMAIN wnba.source_name AS TEXT
    CHECK (VALUE IN ('espn','stats_wnba','wehoop','pbpstats','prizepicks','underdog',
                     'video','manual','derived','legacy_courtside'));

CREATE DOMAIN wnba.probability AS DOUBLE PRECISION
    CHECK (VALUE >= 0.0 AND VALUE <= 1.0);

-- ---------------------------------------------------------------------------------------
-- Reference entities
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.teams (
    team_id      UUID PRIMARY KEY,
    abbreviation TEXT NOT NULL UNIQUE CHECK (abbreviation ~ '^[A-Z]{2,5}$'),
    full_name    TEXT NOT NULL,
    market       TEXT NOT NULL,
    time_zone    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wnba.players (
    player_id     UUID PRIMARY KEY,
    full_name     TEXT NOT NULL,
    position      TEXT NOT NULL DEFAULT 'UNKNOWN',
    height_inches SMALLINT CHECK (height_inches BETWEEN 54 AND 90),
    birth_date    TIMESTAMPTZ,
    rookie_season SMALLINT
);
CREATE INDEX IF NOT EXISTS players_name_idx ON wnba.players (lower(full_name));

CREATE TABLE IF NOT EXISTS wnba.games (
    game_id          UUID PRIMARY KEY,
    season_year      SMALLINT NOT NULL,
    scheduled_tipoff TIMESTAMPTZ NOT NULL,
    home_team_id     UUID NOT NULL REFERENCES wnba.teams(team_id),
    away_team_id     UUID NOT NULL REFERENCES wnba.teams(team_id),
    status           TEXT NOT NULL DEFAULT 'scheduled',
    overtime_periods SMALLINT NOT NULL DEFAULT 0 CHECK (overtime_periods BETWEEN 0 AND 6),
    home_points      SMALLINT,
    away_points      SMALLINT,
    CONSTRAINT a_team_cannot_play_itself CHECK (home_team_id <> away_team_id)
);
CREATE INDEX IF NOT EXISTS games_tipoff_idx ON wnba.games (scheduled_tipoff);

-- ---------------------------------------------------------------------------------------
-- Identity: alias resolution
--
-- Unresolved names block ingestion rather than being fuzzy-matched in. Every binding is
-- bitemporal because we sometimes discover a binding was wrong, and correcting it must not
-- rewrite what we believed at forecast time.
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.player_aliases (
    alias_id            UUID PRIMARY KEY,
    player_id           UUID NOT NULL REFERENCES wnba.players(player_id),
    source              wnba.source_name NOT NULL,
    source_player_id    TEXT NOT NULL,
    source_display_name TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    confidence          wnba.probability NOT NULL DEFAULT 1.0,
    verified_by         TEXT,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    system_from         TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to           TIMESTAMPTZ,
    CONSTRAINT alias_valid_range  CHECK (valid_to  IS NULL OR valid_to  > valid_from),
    CONSTRAINT alias_system_range CHECK (system_to IS NULL OR system_to > system_from)
);
-- One current belief per (source, source id). Partial unique index is the enforcement.
CREATE UNIQUE INDEX IF NOT EXISTS player_aliases_current_uq
    ON wnba.player_aliases (source, source_player_id)
    WHERE system_to IS NULL AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS player_aliases_lookup_idx
    ON wnba.player_aliases (source, normalized_name);

CREATE TABLE IF NOT EXISTS wnba.entity_resolution_failures (
    failure_id          UUID PRIMARY KEY,
    source              wnba.source_name NOT NULL,
    source_entity_id    TEXT NOT NULL,
    source_display_name TEXT NOT NULL,
    entity_kind         TEXT NOT NULL CHECK (entity_kind IN ('player','team','game')),
    observed_at         TIMESTAMPTZ NOT NULL,
    context             TEXT NOT NULL DEFAULT '',
    resolved_at         TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------------------
-- Availability and role
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.injury_status (
    record_id    UUID PRIMARY KEY,
    player_id    UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id      UUID REFERENCES wnba.games(game_id),
    designation  TEXT NOT NULL,
    body_part    TEXT,
    detail       TEXT,
    source       wnba.source_name NOT NULL,
    source_ref   TEXT NOT NULL,
    valid_from   TIMESTAMPTZ NOT NULL,
    valid_to     TIMESTAMPTZ,
    system_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to    TIMESTAMPTZ,
    CONSTRAINT injury_valid_range  CHECK (valid_to  IS NULL OR valid_to  > valid_from),
    CONSTRAINT injury_system_range CHECK (system_to IS NULL OR system_to > system_from)
);
-- The index that makes point-in-time reads cheap: filter by player, then by both axes.
CREATE INDEX IF NOT EXISTS injury_status_asof_idx
    ON wnba.injury_status (player_id, system_from DESC, valid_from DESC);

-- NOTE: there is deliberately no `is_starter BOOLEAN` column here or anywhere else.
-- The WNBA does not require pre-tip lineup submission, so starter status is not a fact
-- that exists to be stored -- only a probability we estimate.
CREATE TABLE IF NOT EXISTS wnba.projected_roles (
    record_id                        UUID PRIMARY KEY,
    player_id                        UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id                          UUID NOT NULL REFERENCES wnba.games(game_id),
    availability_probability         wnba.probability NOT NULL,
    start_probability                wnba.probability NOT NULL,
    closing_lineup_probability       wnba.probability NOT NULL,
    expected_minutes                 DOUBLE PRECISION NOT NULL CHECK (expected_minutes BETWEEN 0 AND 60),
    minutes_std                      DOUBLE PRECISION NOT NULL CHECK (minutes_std >= 0),
    minutes_restriction_probability  wnba.probability NOT NULL DEFAULT 0.0,
    expected_usage_rate              wnba.probability,
    depth_chart_rank                 SMALLINT,
    valid_from                       TIMESTAMPTZ NOT NULL,
    valid_to                         TIMESTAMPTZ,
    system_from                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to                        TIMESTAMPTZ,
    CONSTRAINT role_valid_range  CHECK (valid_to  IS NULL OR valid_to  > valid_from),
    CONSTRAINT role_system_range CHECK (system_to IS NULL OR system_to > system_from),
    CONSTRAINT unavailable_players_play_no_minutes
        CHECK (availability_probability > 0.0 OR expected_minutes = 0.0)
);
CREATE INDEX IF NOT EXISTS projected_roles_asof_idx
    ON wnba.projected_roles (game_id, player_id, system_from DESC);

-- ---------------------------------------------------------------------------------------
-- Market: the archive
--
-- The most time-critical table in the platform. Historical prop odds are paywalled
-- everywhere, so this IS our market history and it cannot be reconstructed later at any
-- price. Rows are only ever appended.
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.prop_quotes (
    quote_id            UUID PRIMARY KEY,
    source              wnba.source_name NOT NULL,
    source_quote_id     TEXT NOT NULL,
    player_id           UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id             UUID REFERENCES wnba.games(game_id),
    prop_type           TEXT NOT NULL,
    line                NUMERIC(6,2) NOT NULL CHECK (line >= 0 AND line <= 200),
    market_kind         TEXT NOT NULL CHECK (market_kind IN ('sportsbook_two_sided','pickem')),
    locks_at            TIMESTAMPTZ,
    over_american_odds  INTEGER,
    under_american_odds INTEGER,
    over_multiplier     NUMERIC(5,2),
    under_multiplier    NUMERIC(5,2),
    is_promotional      BOOLEAN NOT NULL DEFAULT FALSE,
    is_available        BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    system_from         TIMESTAMPTZ NOT NULL DEFAULT now(),
    system_to           TIMESTAMPTZ,
    CONSTRAINT quote_valid_range  CHECK (valid_to  IS NULL OR valid_to  > valid_from),
    CONSTRAINT quote_system_range CHECK (system_to IS NULL OR system_to > system_from),
    -- Lines land on half-point increments. Anything else is a parsing bug upstream, and a
    -- silently wrong line is far worse than a missing one.
    CONSTRAINT line_is_half_point CHECK ((line * 2) = trunc(line * 2)),
    -- The price shape must match the market kind, mirroring the Pydantic validator.
    CONSTRAINT price_shape_matches_market_kind CHECK (
        (market_kind = 'sportsbook_two_sided'
         AND over_american_odds IS NOT NULL AND under_american_odds IS NOT NULL
         AND abs(over_american_odds) >= 100 AND abs(under_american_odds) >= 100
         AND over_multiplier IS NULL AND under_multiplier IS NULL)
        OR
        (market_kind = 'pickem'
         AND over_american_odds IS NULL AND under_american_odds IS NULL)
    )
);
-- One observation per market per source per instant. Makes the archiver idempotent: a
-- re-poll that sees an unchanged line cannot create a duplicate snapshot.
CREATE UNIQUE INDEX IF NOT EXISTS prop_quotes_snapshot_uq
    ON wnba.prop_quotes (source, player_id, prop_type, system_from);
CREATE INDEX IF NOT EXISTS prop_quotes_asof_idx
    ON wnba.prop_quotes (player_id, prop_type, system_from DESC);
CREATE INDEX IF NOT EXISTS prop_quotes_game_idx ON wnba.prop_quotes (game_id, system_from DESC);

-- ---------------------------------------------------------------------------------------
-- Outcomes: ground truth
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.player_game_lines (
    player_id              UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id                UUID NOT NULL REFERENCES wnba.games(game_id),
    team_id                UUID NOT NULL REFERENCES wnba.teams(team_id),
    minutes                DOUBLE PRECISION NOT NULL CHECK (minutes BETWEEN 0 AND 70),
    -- Legal precisely because it is an OUTCOME, observed after the fact. Feature builders
    -- must never read this for a game they are forecasting.
    started                BOOLEAN NOT NULL,
    points                 SMALLINT NOT NULL CHECK (points >= 0),
    rebounds_offensive     SMALLINT NOT NULL CHECK (rebounds_offensive >= 0),
    rebounds_defensive     SMALLINT NOT NULL CHECK (rebounds_defensive >= 0),
    assists                SMALLINT NOT NULL CHECK (assists >= 0),
    steals                 SMALLINT NOT NULL CHECK (steals >= 0),
    blocks                 SMALLINT NOT NULL CHECK (blocks >= 0),
    turnovers              SMALLINT NOT NULL CHECK (turnovers >= 0),
    personal_fouls         SMALLINT NOT NULL CHECK (personal_fouls >= 0),
    field_goals_made       SMALLINT NOT NULL CHECK (field_goals_made >= 0),
    field_goals_attempted  SMALLINT NOT NULL CHECK (field_goals_attempted >= 0),
    three_pointers_made    SMALLINT NOT NULL CHECK (three_pointers_made >= 0),
    three_pointers_attempted SMALLINT NOT NULL CHECK (three_pointers_attempted >= 0),
    free_throws_made       SMALLINT NOT NULL CHECK (free_throws_made >= 0),
    free_throws_attempted  SMALLINT NOT NULL CHECK (free_throws_attempted >= 0),
    plus_minus             SMALLINT,
    source                 wnba.source_name NOT NULL,
    ingested_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, game_id),
    -- The same internal-consistency checks the Pydantic model enforces. A box score that
    -- disagrees with itself is a parsing bug, and it must not reach a feature.
    CONSTRAINT fg_made_le_attempted   CHECK (field_goals_made      <= field_goals_attempted),
    CONSTRAINT tp_made_le_attempted   CHECK (three_pointers_made   <= three_pointers_attempted),
    CONSTRAINT ft_made_le_attempted   CHECK (free_throws_made      <= free_throws_attempted),
    CONSTRAINT tp_made_le_fg_made     CHECK (three_pointers_made   <= field_goals_made),
    CONSTRAINT tp_att_le_fg_att       CHECK (three_pointers_attempted <= field_goals_attempted),
    CONSTRAINT points_match_shots     CHECK (
        points = 2 * (field_goals_made - three_pointers_made)
               + 3 * three_pointers_made
               + free_throws_made)
);
CREATE INDEX IF NOT EXISTS player_game_lines_player_idx ON wnba.player_game_lines (player_id);

-- ---------------------------------------------------------------------------------------
-- Data quality
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.quarantine (
    quarantine_id    UUID PRIMARY KEY,
    source           wnba.source_name NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_payload      TEXT NOT NULL,
    payload_sha256   TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    validation_level TEXT NOT NULL,
    validation_errors TEXT[] NOT NULL,
    reprocessed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS quarantine_source_idx ON wnba.quarantine (source, received_at DESC);

CREATE TABLE IF NOT EXISTS wnba.dq_incidents (
    incident_id            UUID PRIMARY KEY,
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    level                  TEXT NOT NULL,
    severity               TEXT NOT NULL CHECK (severity IN ('info','warning','critical','blocking')),
    code                   TEXT NOT NULL,
    source                 wnba.source_name NOT NULL,
    entity_id              UUID,
    detail                 TEXT NOT NULL DEFAULT '',
    blocks_recommendations BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at            TIMESTAMPTZ,
    CONSTRAINT blocking_incidents_must_block
        CHECK (severity <> 'blocking' OR blocks_recommendations)
);

-- ---------------------------------------------------------------------------------------
-- Decisions and governance
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wnba.decision_episodes (
    episode_id             UUID PRIMARY KEY,
    forecast_timestamp     TIMESTAMPTZ NOT NULL,
    player_id              UUID NOT NULL REFERENCES wnba.players(player_id),
    game_id                UUID REFERENCES wnba.games(game_id),
    prop_type              TEXT NOT NULL,
    side                   TEXT NOT NULL CHECK (side IN ('over','under')),
    line                   NUMERIC(6,2) NOT NULL,
    source                 wnba.source_name NOT NULL,
    quote_id               UUID NOT NULL REFERENCES wnba.prop_quotes(quote_id),
    multiplier             NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    projected_mean         DOUBLE PRECISION NOT NULL,
    projected_median       DOUBLE PRECISION NOT NULL,
    predicted_probability  wnba.probability NOT NULL,
    projected_minutes      DOUBLE PRECISION NOT NULL,
    confidence             wnba.probability NOT NULL,
    data_quality_score     wnba.probability NOT NULL,
    model_disagreement     DOUBLE PRECISION NOT NULL,
    model_version_ids      UUID[] NOT NULL,
    model_run_id           UUID NOT NULL,
    feature_snapshot_id    UUID NOT NULL,
    system_recommendation  TEXT NOT NULL,
    entry_id               UUID,
    analyst_decision       TEXT,
    analyst_override_reason TEXT,
    -- Defaults to paper, and stays there until all ten pre-real-money criteria are met.
    is_paper               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT override_requires_reason CHECK (
        analyst_decision IS NULL
        OR analyst_decision NOT IN ('override_higher','override_lower')
        OR analyst_override_reason IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS decision_episodes_forecast_idx
    ON wnba.decision_episodes (forecast_timestamp DESC);

-- Appended after settlement. Never merged into the episode.
CREATE TABLE IF NOT EXISTS wnba.episode_outcomes (
    episode_id       UUID PRIMARY KEY REFERENCES wnba.decision_episodes(episode_id),
    settled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actual_stat      DOUBLE PRECISION NOT NULL,
    actual_minutes   DOUBLE PRECISION NOT NULL,
    did_play         BOOLEAN NOT NULL,
    did_start        BOOLEAN NOT NULL,
    hit              BOOLEAN NOT NULL,
    was_push         BOOLEAN NOT NULL DEFAULT FALSE,
    was_voided       BOOLEAN NOT NULL DEFAULT FALSE,
    closing_line     NUMERIC(6,2),
    closing_multiplier NUMERIC(5,2),
    brier            DOUBLE PRECISION,
    log_loss         DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS wnba.ontology_actions (
    action_id      UUID PRIMARY KEY,
    action_type    TEXT NOT NULL,
    actor          TEXT NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject_id     UUID NOT NULL,
    previous_state TEXT NOT NULL,
    new_state      TEXT NOT NULL,
    reason         TEXT NOT NULL CHECK (length(reason) > 0),
    evidence_ids   UUID[] NOT NULL DEFAULT '{}',
    is_automated   BOOLEAN NOT NULL DEFAULT FALSE,
    -- The asymmetric-autonomy invariant, enforced by the database as well as by the code.
    -- Automation may restrict exposure; only a human may expand it.
    CONSTRAINT automation_may_not_expand_exposure CHECK (
        NOT is_automated
        OR action_type NOT IN ('promote_model','approve_candidate','override_minutes'))
);
CREATE INDEX IF NOT EXISTS ontology_actions_subject_idx
    ON wnba.ontology_actions (subject_id, occurred_at DESC);

COMMIT;
