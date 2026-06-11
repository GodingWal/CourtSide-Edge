import Database from 'better-sqlite3';
import { sql } from 'drizzle-orm';
import { config } from './config';
import { runMigrations } from './migrate';
import { db, isPostgres, players, settings, rawRun } from './db';

// ── WNBA Players (reference roster for manual bet entry / settle UI) ─────────
// Status starts ACTIVE for everyone; real injury intel comes from Agent 2's
// live feed, never from seed data.
const PLAYERS: { id: string; name: string; team: string; status: string }[] = [
  { id: 'p001', name: "A'ja Wilson", team: 'LVA', status: 'ACTIVE' },
  { id: 'p002', name: 'Kelsey Plum', team: 'LVA', status: 'ACTIVE' },
  { id: 'p003', name: 'Jackie Young', team: 'LVA', status: 'ACTIVE' },
  { id: 'p004', name: 'Breanna Stewart', team: 'NYL', status: 'ACTIVE' },
  { id: 'p005', name: 'Sabrina Ionescu', team: 'NYL', status: 'ACTIVE' },
  { id: 'p006', name: 'Jonquel Jones', team: 'NYL', status: 'ACTIVE' },
  { id: 'p007', name: 'Jewell Loyd', team: 'SEA', status: 'ACTIVE' },
  { id: 'p008', name: 'Nneka Ogwumike', team: 'SEA', status: 'ACTIVE' },
  { id: 'p009', name: 'Skylar Diggins-Smith', team: 'SEA', status: 'ACTIVE' },
  { id: 'p010', name: 'Alyssa Thomas', team: 'CON', status: 'ACTIVE' },
  { id: 'p011', name: 'DeWanna Bonner', team: 'CON', status: 'ACTIVE' },
  { id: 'p012', name: 'Brionna Jones', team: 'CON', status: 'ACTIVE' },
  { id: 'p013', name: 'Diana Taurasi', team: 'PHX', status: 'ACTIVE' },
  { id: 'p014', name: 'Brittney Griner', team: 'PHX', status: 'ACTIVE' },
  { id: 'p015', name: 'Kahleah Copper', team: 'PHX', status: 'ACTIVE' },
  { id: 'p016', name: 'Aliyah Boston', team: 'IND', status: 'ACTIVE' },
  { id: 'p017', name: 'Caitlin Clark', team: 'IND', status: 'ACTIVE' },
  { id: 'p018', name: 'Kelsey Mitchell', team: 'IND', status: 'ACTIVE' },
  { id: 'p019', name: 'Angel Reese', team: 'CHI', status: 'ACTIVE' },
  { id: 'p020', name: 'Chennedy Carter', team: 'CHI', status: 'ACTIVE' },
  { id: 'p021', name: 'Marina Mabrey', team: 'CHI', status: 'ACTIVE' },
  { id: 'p022', name: 'Napheesa Collier', team: 'MIN', status: 'ACTIVE' },
  { id: 'p023', name: 'Kayla McBride', team: 'MIN', status: 'ACTIVE' },
  { id: 'p024', name: 'Courtney Williams', team: 'MIN', status: 'ACTIVE' },
  { id: 'p025', name: 'Arike Ogunbowale', team: 'DAL', status: 'ACTIVE' },
  { id: 'p026', name: 'Satou Sabally', team: 'DAL', status: 'ACTIVE' },
  { id: 'p027', name: 'Natasha Howard', team: 'DAL', status: 'ACTIVE' },
  { id: 'p028', name: 'Ariel Atkins', team: 'WSH', status: 'ACTIVE' },
  { id: 'p029', name: 'Rhyne Howard', team: 'ATL', status: 'ACTIVE' },
  { id: 'p030', name: 'Dearica Hamby', team: 'LAX', status: 'ACTIVE' },
];

const DEFAULT_SETTINGS: Record<string, string> = {
  bankroll_starting: '10000',
  kelly_fraction: '0.25',
  notifications_enabled: 'true',
  auto_halt_drawdown: '15',
};

const SEED_TABLES = [
  'bets', 'players', 'bankroll_history', 'settings', 'qualitative_events',
  'agent_context_store', 'decision_audit', 'hedging_opportunities',
];

// ── Legacy demo-data purge (SQLite only) ─────────────────────────────────────
// Older builds seeded mock bets, bankroll history, hedges, audits and sample
// qualitative events into SQLite databases. Detect the old seed's unmistakable
// fingerprints and delete exactly those cohorts; databases without the
// fingerprints are untouched. Postgres databases postdate those builds, so
// the purge never applies there.
function purgeLegacyDemoRows(sqlite: InstanceType<typeof Database>): void {
  const tableExists = (name: string): boolean =>
    !!sqlite.prepare(`SELECT 1 FROM sqlite_master WHERE type='table' AND name=?`).get(name);

  let purged = 0;

  // Demo bets were inserted in one transaction on an empty table, so the
  // cohort is contiguous from id 1 up to the last fingerprinted row. Real
  // bets always have higher ids than the demo batch.
  if (tableExists('bets')) {
    const betFingerprints = [
      'Sharp line, good CLV',
      'Variance hit',
      '2-Leg Parlay (Stewart + Wilson)',
      'Active 2-Leg Parlay (Clark + Ionescu)',
      'Agent 20: Auto-Hedge placed to lock in middle opportunity',
    ];
    const row = sqlite.prepare(
      `SELECT MAX(id) as maxId FROM bets WHERE notes IN (${betFingerprints.map(() => '?').join(',')})`
    ).get(...betFingerprints) as { maxId: number | null };
    if (row?.maxId) {
      const res = sqlite.prepare('DELETE FROM bets WHERE id <= ? OR parent_id <= ?').run(row.maxId, row.maxId);
      purged += res.changes;

      // The demo bankroll walk (90 daily points from $10k) was seeded into
      // the same legacy databases — only ever as rows 1-90 of a fresh table.
      if (tableExists('bankroll_history')) {
        purged += sqlite.prepare('DELETE FROM bankroll_history WHERE id <= 90').run().changes;
      }
    }
  }

  if (tableExists('qualitative_events')) {
    const eventFingerprints = [
      '%Seen in walking boot at morning shootaround%',
      '%"crew":"Crew_A"%',
      '%"crew":"Crew_B"%',
      '%travel density and fatigue after 3 road games in 5 days%',
      '%Fully participated in contact drills, starting tonight%',
    ];
    for (const fp of eventFingerprints) {
      purged += sqlite.prepare('DELETE FROM qualitative_events WHERE payload LIKE ?').run(fp).changes;
    }
  }

  if (tableExists('agent_context_store')) {
    const contextFingerprints = [
      '%"crew":"Crew_A"%',
      '%"crew":"Crew_B"%',
      '%Heavy travel fatigue detected%',
      '%Minimal fatigue, rested squad%',
      '%LVA usage redistribution massive%',
    ];
    for (const fp of contextFingerprints) {
      purged += sqlite.prepare('DELETE FROM agent_context_store WHERE context_value LIKE ?').run(fp).changes;
    }
  }

  if (tableExists('decision_audit')) {
    purged += sqlite.prepare(
      `DELETE FROM decision_audit WHERE trace_id IN ('a1b2c3d4-e5f6-7890-abcd-ef1234567890','f9e8d7c6-b5a4-3210-fedc-ba0987654321')`
    ).run().changes;
  }

  if (tableExists('hedging_opportunities')) {
    purged += sqlite.prepare(
      `DELETE FROM hedging_opportunities WHERE hedge_instructions LIKE '%middle opportunity between 8.5 and 9.5%'
         OR hedge_instructions LIKE '%arbitrage middle with +28.00 EV%'`
    ).run().changes;
  }

  if (purged > 0) {
    console.log(`🧹 Purged ${purged} legacy demo rows left over from old mock seeds`);
  }
}

// Reference data (players roster, default settings) through the dialect-
// agnostic drizzle API — identical for SQLite and Postgres.
async function seedReferenceData(): Promise<void> {
  const playerRows = await db.select().from(players);
  if (playerRows.length === 0) {
    await db.insert(players).values(PLAYERS);
    console.log(`✓ Seeded ${PLAYERS.length} players`);
  } else {
    console.log(`⏭ Players already seeded (${playerRows.length} rows)`);
  }

  const settingRows = await db.select().from(settings);
  if (settingRows.length === 0) {
    await db.insert(settings).values(
      Object.entries(DEFAULT_SETTINGS).map(([key, value]) => ({ key, value }))
    );
    console.log('✓ Seeded default settings');
  } else {
    console.log(`⏭ Settings already seeded (${settingRows.length} rows)`);
  }
}

export async function seed(options?: { forceReset?: boolean }): Promise<void> {
  // Only reference data (players roster, default settings) is ever seeded.
  // Bets, bankroll history, events, contexts and audits come exclusively
  // from real usage and live agents — no demo/mock rows.
  if (options?.forceReset) {
    console.log('⚠️ Force reset requested. Dropping all tables...');
    if (isPostgres) {
      for (const table of SEED_TABLES) {
        await rawRun(sql.raw(`DROP TABLE IF EXISTS "${table}" CASCADE`));
      }
      // The drizzle migration journal must go too or migrations won't rerun.
      await rawRun(sql.raw('DROP SCHEMA IF EXISTS drizzle CASCADE'));
    } else {
      const raw = new Database(config.DATABASE_PATH);
      for (const table of SEED_TABLES) {
        raw.exec(`DROP TABLE IF EXISTS ${table};`);
      }
      raw.close();
    }
    // Rerun migrations to recreate empty tables
    await runMigrations();
  }

  // One-time cleanup of mock rows left behind by old SQLite seed versions.
  if (!isPostgres) {
    const raw = new Database(config.DATABASE_PATH);
    raw.pragma('journal_mode = WAL');
    try {
      purgeLegacyDemoRows(raw);
    } catch (err) {
      console.error('Failed to purge legacy demo rows:', err);
    } finally {
      raw.close();
    }
  }

  await seedReferenceData();
  console.log('✅ Seed complete');
}

// Run if executed directly
if (require.main === module) {
  const forceReset = process.argv.includes('--reset');
  seed({ forceReset }).catch((err) => {
    console.error('Seed failed:', err);
    process.exit(1);
  });
}
