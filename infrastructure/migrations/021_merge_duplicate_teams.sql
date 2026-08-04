-- 021_merge_duplicate_teams.sql
--
-- Three franchises existed twice.
--
-- Underdog spells them LVA / NYL / GSV; ESPN spells them LV / NY / GS. The market-team
-- resolver looked teams up by raw abbreviation, found no match, and minted a second team row
-- for the same club -- with full_name and market set to the abbreviation, which is the
-- signature of a stub. One franchise, two UUIDs.
--
-- The damage was contained because the stubs were young: one scheduled game each and zero box
-- scores, while the canonical rows hold the entire history (LV: 135 games, 1,271 player lines).
-- Left alone it would have split every team-level aggregate -- pace, opponent defense, rest --
-- across two identities, and quietly starved the matchup features for those fixtures.
--
-- The resolver now folds source spellings onto the canonical abbreviation AND reads
-- team_aliases before creating anything, so this cannot recur. This migration repairs the
-- rows already written.

BEGIN;

CREATE TEMP TABLE team_merge_map ON COMMIT DROP AS
SELECT stub.team_id AS stub_id,
       canonical.team_id AS canonical_id,
       stub.abbreviation AS stub_abbrev,
       canonical.abbreviation AS canonical_abbrev
FROM wnba.teams stub
JOIN (VALUES ('LVA','LV'), ('NYL','NY'), ('GSV','GS'),
             ('WAS','WSH'), ('LAS','LA'), ('LAX','LA')) AS m(bad, good)
  ON m.bad = stub.abbreviation
JOIN wnba.teams canonical ON canonical.abbreviation = m.good
-- Only merge genuine stubs. A row whose full_name differs from its abbreviation is a real
-- team that happens to use a short code, and must be left alone.
WHERE stub.full_name = stub.abbreviation
  AND stub.team_id <> canonical.team_id;

-- Repoint every reference before deleting anything, so a foreign key can never dangle.
UPDATE wnba.games g SET home_team_id = m.canonical_id
FROM team_merge_map m WHERE g.home_team_id = m.stub_id;

UPDATE wnba.games g SET away_team_id = m.canonical_id
FROM team_merge_map m WHERE g.away_team_id = m.stub_id;

UPDATE wnba.player_game_lines l SET team_id = m.canonical_id
FROM team_merge_map m WHERE l.team_id = m.stub_id;

UPDATE wnba.matchup_contexts c SET team_id = m.canonical_id
FROM team_merge_map m WHERE c.team_id = m.stub_id;

UPDATE wnba.matchup_contexts c SET opponent_team_id = m.canonical_id
FROM team_merge_map m WHERE c.opponent_team_id = m.stub_id;

-- Preserve the source spelling as an alias so the market feed keeps resolving, now pointing
-- at the canonical franchise instead of a duplicate.
INSERT INTO wnba.team_aliases
    (alias_id, team_id, source, source_team_id, source_display_name, valid_from, system_from)
SELECT gen_random_uuid(), m.canonical_id, 'underdog', m.stub_abbrev, m.stub_abbrev,
       now(), now()
FROM team_merge_map m
ON CONFLICT DO NOTHING;

UPDATE wnba.team_aliases a SET team_id = m.canonical_id
FROM team_merge_map m WHERE a.team_id = m.stub_id;

DELETE FROM wnba.teams t USING team_merge_map m WHERE t.team_id = m.stub_id;

-- A game must never again reference a team that does not exist in the canonical set, and no
-- two rows may claim the same abbreviation.
CREATE UNIQUE INDEX IF NOT EXISTS teams_abbreviation_uq ON wnba.teams (abbreviation);

COMMIT;
