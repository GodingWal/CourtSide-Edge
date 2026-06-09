import Database from 'better-sqlite3';
import path from 'path';

const dbPath = path.resolve(__dirname, '../../data/hoopstats_wnba.db');

// ── WNBA Players ──────────────────────────────────────────────────────────────
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
  { id: 'p012', name: 'Brionna Jones', team: 'CON', status: 'INJURED' },
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
  { id: 'p024', name: 'Courtney Williams', team: 'MIN', status: 'INJURED' },
  { id: 'p025', name: 'Arike Ogunbowale', team: 'DAL', status: 'ACTIVE' },
  { id: 'p026', name: 'Satou Sabally', team: 'DAL', status: 'ACTIVE' },
  { id: 'p027', name: 'Natasha Howard', team: 'DAL', status: 'ACTIVE' },
  { id: 'p028', name: 'Ariel Atkins', team: 'WSH', status: 'ACTIVE' },
  { id: 'p029', name: 'Rhyne Howard', team: 'ATL', status: 'ACTIVE' },
  { id: 'p030', name: 'Dearica Hamby', team: 'LAX', status: 'ACTIVE' },
];

const STATS = ['PTS', 'REB', 'AST', 'STL', 'BLK', 'PTS+REB', 'PTS+AST', '3PM'];
const TEAMS = ['LVA', 'NYL', 'SEA', 'CON', 'PHX', 'IND', 'CHI', 'MIN', 'DAL', 'WSH', 'ATL', 'LAX'];

function randomFloat(min: number, max: number): number {
  return Math.round((Math.random() * (max - min) + min) * 100) / 100;
}

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function calcProfitLoss(result: string, stake: number, bookOdds: number): number {
  if (result === 'PUSH') return 0;
  if (result === 'WIN') {
    return bookOdds > 0
      ? Math.round((stake * bookOdds) / 100 * 100) / 100
      : Math.round((stake * 100) / Math.abs(bookOdds) * 100) / 100;
  }
  return -stake; // LOSS
}

export function seed(): void {
  const sqlite = new Database(dbPath);
  sqlite.pragma('journal_mode = WAL');

  // ── Create tables ──────────────────────────────────────────────────────────
  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS players (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      team TEXT NOT NULL,
      status TEXT
    );
  `);

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS bankroll_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp INTEGER NOT NULL,
      balance REAL NOT NULL,
      drawdown_pct REAL NOT NULL
    );
  `);

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS bets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      player TEXT NOT NULL,
      stat TEXT NOT NULL,
      line REAL NOT NULL,
      over_under TEXT NOT NULL,
      book_odds INTEGER NOT NULL,
      true_odds REAL,
      edge_pct REAL,
      stake REAL NOT NULL,
      result TEXT,
      actual_value REAL,
      profit_loss REAL,
      placed_at INTEGER NOT NULL,
      settled_at INTEGER,
      opposing_team TEXT,
      notes TEXT
    );
  `);

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
  `);

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS qualitative_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      channel TEXT NOT NULL,
      payload TEXT NOT NULL,
      timestamp INTEGER NOT NULL
    );
  `);

  // ── Seed players ───────────────────────────────────────────────────────────
  const playerCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM players').get() as { cnt: number };
  if (playerCount.cnt === 0) {
    const insert = sqlite.prepare('INSERT INTO players (id, name, team, status) VALUES (?, ?, ?, ?)');
    const tx = sqlite.transaction(() => {
      for (const p of PLAYERS) {
        insert.run(p.id, p.name, p.team, p.status);
      }
    });
    tx();
    console.log(`✓ Seeded ${PLAYERS.length} players`);
  } else {
    console.log(`⏭ Players already seeded (${playerCount.cnt} rows)`);
  }

  // ── Seed bankroll history (90 days) ────────────────────────────────────────
  const bankrollCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM bankroll_history').get() as { cnt: number };
  if (bankrollCount.cnt === 0) {
    const insert = sqlite.prepare(
      'INSERT INTO bankroll_history (timestamp, balance, drawdown_pct) VALUES (?, ?, ?)'
    );
    const startingBalance = 10000;
    let balance = startingBalance;
    let peak = startingBalance;
    const now = Date.now();
    const dayMs = 86400000;

    const tx = sqlite.transaction(() => {
      for (let i = 89; i >= 0; i--) {
        const ts = now - i * dayMs;
        const dailyPL = randomFloat(-400, 600);
        balance = Math.round((balance + dailyPL) * 100) / 100;
        if (balance < 500) balance = 500; // floor so bankroll doesn't go negative
        if (balance > peak) peak = balance;
        const drawdown = Math.round(((peak - balance) / peak) * 10000) / 100;
        insert.run(ts, balance, drawdown);
      }
    });
    tx();
    console.log('✓ Seeded 90 days of bankroll history');
  } else {
    console.log(`⏭ Bankroll history already seeded (${bankrollCount.cnt} rows)`);
  }

  // ── Seed bets (25 samples) ────────────────────────────────────────────────
  const betCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM bets').get() as { cnt: number };
  if (betCount.cnt === 0) {
    const insert = sqlite.prepare(`
      INSERT INTO bets (player, stat, line, over_under, book_odds, true_odds, edge_pct, stake, result, actual_value, profit_loss, placed_at, settled_at, opposing_team, notes)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const now = Date.now();
    const dayMs = 86400000;

    const results: (string | null)[] = [
      'WIN', 'WIN', 'WIN', 'WIN', 'WIN', 'WIN', 'WIN', 'WIN', 'WIN', 'WIN',
      'LOSS', 'LOSS', 'LOSS', 'LOSS', 'LOSS', 'LOSS', 'LOSS',
      'PUSH', 'PUSH', 'PUSH',
      null, null, null, null, null,
    ];

    const tx = sqlite.transaction(() => {
      for (let i = 0; i < 25; i++) {
        const player = pick(PLAYERS);
        const stat = pick(STATS);
        const line = randomFloat(5, 30);
        const overUnder = Math.random() > 0.5 ? 'OVER' : 'UNDER';
        const bookOdds = pick([-110, -115, -120, -105, +100, +105, +110, -125, -130, +120]);
        const trueOdds = randomFloat(0.4, 0.7);
        const edgePct = randomFloat(1, 12);
        const stake = pick([25, 50, 75, 100, 150, 200]);
        const result = results[i];
        const placedAt = now - randomInt(1, 30) * dayMs;

        let actualValue: number | null = null;
        let profitLoss: number | null = null;
        let settledAt: number | null = null;

        if (result !== null) {
          actualValue = randomFloat(0, 40);
          profitLoss = calcProfitLoss(result, stake, bookOdds);
          settledAt = placedAt + randomInt(2, 8) * 3600000; // 2-8 hours later
        }

        const opposingTeam = pick(TEAMS.filter((t) => t !== player.team));
        const notes = result === 'WIN' ? 'Sharp line, good CLV' : result === 'LOSS' ? 'Variance hit' : null;

        insert.run(
          player.name, stat, line, overUnder, bookOdds, trueOdds, edgePct,
          stake, result, actualValue, profitLoss, placedAt, settledAt,
          opposingTeam, notes
        );
      }
    });
    tx();
    console.log('✓ Seeded 25 sample bets');
  } else {
    console.log(`⏭ Bets already seeded (${betCount.cnt} rows)`);
  }

  // ── Seed settings ─────────────────────────────────────────────────────────
  const settingsCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM settings').get() as { cnt: number };
  if (settingsCount.cnt === 0) {
    const insert = sqlite.prepare('INSERT INTO settings (key, value) VALUES (?, ?)');
    const defaults: Record<string, string> = {
      bankroll_starting: '10000',
      kelly_fraction: '0.25',
      notifications_enabled: 'true',
      auto_halt_drawdown: '15',
    };
    const tx = sqlite.transaction(() => {
      for (const [k, v] of Object.entries(defaults)) {
        insert.run(k, v);
      }
    });
    tx();
    console.log('✓ Seeded default settings');
  } else {
    console.log(`⏭ Settings already seeded (${settingsCount.cnt} rows)`);
  }

  // ── Seed qualitative events ──────────────────────────────────────────────
  const qualitativeCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM qualitative_events').get() as { cnt: number };
  if (qualitativeCount.cnt === 0) {
    const insert = sqlite.prepare(`
      INSERT INTO qualitative_events (channel, payload, timestamp)
      VALUES (?, ?, ?)
    `);
    const now = Date.now();
    const dayMs = 86400000;

    const sampleEvents = [
      {
        channel: 'channel_roster_updates',
        payload: JSON.stringify({
          player: "A'ja Wilson",
          team: 'LVA',
          status: 'INJURED',
          injury: 'Right ankle sprain',
          source: 'Twitter/X Beat Writer',
          details: 'Seen in walking boot at morning shootaround.'
        }),
        timestamp: now - 5 * dayMs
      },
      {
        channel: 'channel_referee_context',
        payload: JSON.stringify({
          source: "Agent 5",
          game_id: "LVA_NYL",
          crew: "Crew_A",
          tendencies: { fouls_per_40: 38.5, pace_effect: -1.2, ou_hit_rate: "Under_Heavy" }
        }),
        timestamp: now - 3 * dayMs
      },
      {
        channel: 'channel_sentiment_context',
        payload: JSON.stringify({
          team: 'LVA',
          sentiment_score: -0.6,
          summary: 'Coach Hammon expressed severe frustration with travel density and fatigue after 3 road games in 5 days.'
        }),
        timestamp: now - 2 * dayMs
      },
      {
        channel: 'channel_roster_updates',
        payload: JSON.stringify({
          player: 'Alyssa Thomas',
          team: 'CON',
          status: 'ACTIVE',
          injury: 'Knee soreness cleared',
          source: 'Team Practice Report',
          details: 'Fully participated in contact drills, starting tonight.'
        }),
        timestamp: now - dayMs
      },
      {
        channel: 'channel_referee_context',
        payload: JSON.stringify({
          source: "Agent 5",
          game_id: "IND_CHI",
          crew: "Crew_B",
          tendencies: { fouls_per_40: 30.1, pace_effect: 2.5, ou_hit_rate: "Over_Heavy" }
        }),
        timestamp: now - 12 * 3600000
      }
    ];

    const tx = sqlite.transaction(() => {
      for (const e of sampleEvents) {
        insert.run(e.channel, e.payload, e.timestamp);
      }
    });
    tx();
    console.log(`✓ Seeded ${sampleEvents.length} sample qualitative events`);
  } else {
    console.log(`⏭ Qualitative events already seeded (${qualitativeCount.cnt} rows)`);
  }

  sqlite.close();
  console.log('✅ Seed complete');
}

// Run if executed directly
if (require.main === module) {
  seed();
}
