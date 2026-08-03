BEGIN;

CREATE TABLE IF NOT EXISTS wnba.pick_slips (
    pick_slip_id UUID PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('manual','board','ai_text','screenshot')),
    status TEXT NOT NULL CHECK (status IN ('draft','confirmed','settled','voided')),
    entry_type TEXT NOT NULL DEFAULT 'power' CHECK (entry_type IN ('power','flex','sportsbook')),
    platform TEXT NOT NULL DEFAULT '',
    stake NUMERIC(10,2),
    potential_payout NUMERIC(10,2),
    notes TEXT NOT NULL DEFAULT '',
    is_paper BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (stake IS NULL OR stake >= 0),
    CHECK (potential_payout IS NULL OR potential_payout >= 0)
);

CREATE TABLE IF NOT EXISTS wnba.pick_legs (
    pick_leg_id UUID PRIMARY KEY,
    pick_slip_id UUID NOT NULL REFERENCES wnba.pick_slips(pick_slip_id) ON DELETE CASCADE,
    projection_id UUID REFERENCES wnba.stat_forecasts(projection_id),
    player_name TEXT NOT NULL,
    prop_type TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('over','under')),
    line NUMERIC(7,2) NOT NULL,
    offered_odds INTEGER,
    model_probability wnba.probability,
    result TEXT NOT NULL DEFAULT 'pending'
        CHECK (result IN ('pending','win','loss','push','void')),
    extraction_confidence wnba.probability,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pick_legs_slip_idx ON wnba.pick_legs(pick_slip_id);

CREATE TABLE IF NOT EXISTS wnba.pick_uploads (
    upload_id UUID PRIMARY KEY,
    pick_slip_id UUID REFERENCES wnba.pick_slips(pick_slip_id) ON DELETE SET NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    ocr_text TEXT NOT NULL DEFAULT '',
    parse_status TEXT NOT NULL CHECK (parse_status IN ('uploaded','ocr_complete','parsed','failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
