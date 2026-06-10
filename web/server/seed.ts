import Database from 'better-sqlite3';
import { config } from './config';
import { runMigrations } from './migrate';

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

export function seed(options?: { forceReset?: boolean }): void {
  let sqlite = new Database(config.DATABASE_PATH);
  sqlite.pragma('journal_mode = WAL');

  if (options?.forceReset) {
    console.log('⚠️ Force reset requested. Dropping all tables...');
    sqlite.exec(`DROP TABLE IF EXISTS bets;`);
    sqlite.exec(`DROP TABLE IF EXISTS players;`);
    sqlite.exec(`DROP TABLE IF EXISTS bankroll_history;`);
    sqlite.exec(`DROP TABLE IF EXISTS settings;`);
    sqlite.exec(`DROP TABLE IF EXISTS qualitative_events;`);
    sqlite.exec(`DROP TABLE IF EXISTS agent_context_store;`);
    sqlite.exec(`DROP TABLE IF EXISTS decision_audit;`);
    sqlite.exec(`DROP TABLE IF EXISTS hedging_opportunities;`);
    sqlite.close();

    // Rerun migrations to recreate empty tables
    runMigrations();

    sqlite = new Database(config.DATABASE_PATH);
    sqlite.pragma('journal_mode = WAL');
  }

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
      INSERT INTO bets (parent_id, is_parlay, player, stat, line, over_under, book_odds, true_odds, edge_pct, stake, result, actual_value, profit_loss, placed_at, settled_at, opposing_team, notes, closing_odds, clv_pct, is_hedge)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        // Generate CLV data for settled bets
        let closingOdds: number | null = null;
        let clvPct: number | null = null;
        if (result !== null) {
          // Simulate closing odds movement (line moved 5-15 points in our favor ~60% of the time)
          const favorableMove = Math.random() > 0.4;
          const movement = randomInt(3, 15);
          closingOdds = favorableMove ? bookOdds - movement : bookOdds + movement;
          // CLV = (closing_implied - opening_implied) / opening_implied * 100
          const openProb = bookOdds < 0 ? Math.abs(bookOdds) / (Math.abs(bookOdds) + 100) : 100 / (bookOdds + 100);
          const closeProb = closingOdds < 0 ? Math.abs(closingOdds) / (Math.abs(closingOdds) + 100) : 100 / (closingOdds + 100);
          clvPct = Math.round((closeProb - openProb) / openProb * 10000) / 100;
        }

        insert.run(
          null, 0, player.name, stat, line, overUnder, bookOdds, trueOdds, edgePct,
          stake, result, actualValue, profitLoss, placedAt, settledAt,
          opposingTeam, notes, closingOdds, clvPct, 0
        );
      }

      // Seed Parlay 1: Settled, Won
      const parlay1Placed = now - 5 * dayMs;
      const parlay1Settled = parlay1Placed + 4 * 3600000;
      const parent1Res = insert.run(
        null, 1, null, null, null, null, 260, 0.55, 8.5, 100, 'WIN', null, 260.00, parlay1Placed, parlay1Settled, null, '2-Leg Parlay (Stewart + Wilson)', 280, 3.5, 0
      );
      const parent1Id = parent1Res.lastInsertRowid as number;

      // Leg 1 of Parlay 1
      insert.run(
        parent1Id, 0, 'Breanna Stewart', 'PTS', 22.5, 'OVER', -110, 0.58, 6.2, 0, 'WIN', 25, 0, parlay1Placed, parlay1Settled, 'LVA', 'Leg 1', -118, 4.1, 0
      );
      // Leg 2 of Parlay 1
      insert.run(
        parent1Id, 0, "A'ja Wilson", 'REB', 9.5, 'OVER', -115, 0.60, 9.1, 0, 'WIN', 12, 0, parlay1Placed, parlay1Settled, 'NYL', 'Leg 2', -125, 5.2, 0
      );

      // Seed Parlay 2: Pending
      const parlay2Placed = now - 2 * 3600000; // 2 hours ago
      const parent2Res = insert.run(
        null, 1, null, null, null, null, 320, 0.48, 11.2, 50, null, null, null, parlay2Placed, null, null, 'Active 2-Leg Parlay (Clark + Ionescu)', null, null, 0
      );
      const parent2Id = parent2Res.lastInsertRowid as number;

      // Leg 1 of Parlay 2
      insert.run(
        parent2Id, 0, 'Caitlin Clark', 'AST', 8.5, 'OVER', -110, 0.52, 8.5, 0, null, null, null, parlay2Placed, null, 'CON', 'Leg 1', null, null, 0
      );
      // Leg 2 of Parlay 2
      insert.run(
        parent2Id, 0, 'Sabrina Ionescu', 'PTS', 18.5, 'OVER', -115, 0.55, 12.0, 0, null, null, null, parlay2Placed, null, 'PHX', 'Leg 2', null, null, 0
      );

      // Seed a sample hedge wager (placed by Agent 20 for Bet ID 25/Clark)
      const clarkHedgePlaced = now - 1 * 3600000;
      insert.run(
        25, // parent_id: reference to original Clark bet
        0,
        'Caitlin Clark',
        'AST',
        9.5,
        'UNDER',
        110,
        0.45,
        5.2,
        85.00, // stake
        null, // result: pending
        null,
        null,
        clarkHedgePlaced,
        null,
        'CON',
        'Agent 20: Auto-Hedge placed to lock in middle opportunity',
        null,
        null,
        1 // is_hedge
      );
    });
    tx();
    console.log('✓ Seeded 25 sample bets and 2 parlays');
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

  // ── Seed agent context store ──────────────────────────────────────────────
  const contextCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM agent_context_store').get() as { cnt: number };
  if (contextCount.cnt === 0) {
    const insert = sqlite.prepare(`
      INSERT INTO agent_context_store (game_id, agent_id, context_key, context_value, confidence, ttl_seconds, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `);
    const now = Date.now();
    const dayMs = 86400000;

    const sampleContexts = [
      { game_id: 'LVA_NYL', agent_id: 'Agent_5', context_key: 'referee_foul_bias', context_value: JSON.stringify({ crew: 'Crew_A', fouls_per_40: 38.5, pace_effect: -1.2, ou_tendency: 'Under' }), confidence: 0.85, ttl: 7200, ts: now - 3 * 3600000 },
      { game_id: 'LVA_NYL', agent_id: 'Agent_9', context_key: 'coach_fatigue_score', context_value: JSON.stringify({ team: 'LVA', fatigue: -0.6, travel_density: '3 road in 5 days', summary: 'Heavy travel fatigue detected' }), confidence: 0.72, ttl: 3600, ts: now - 2 * 3600000 },
      { game_id: 'IND_CHI', agent_id: 'Agent_5', context_key: 'referee_foul_bias', context_value: JSON.stringify({ crew: 'Crew_B', fouls_per_40: 30.1, pace_effect: 2.5, ou_tendency: 'Over' }), confidence: 0.88, ttl: 7200, ts: now - 1 * 3600000 },
      { game_id: 'IND_CHI', agent_id: 'Agent_9', context_key: 'travel_fatigue', context_value: JSON.stringify({ team: 'IND', fatigue: -0.2, travel_density: 'Home stand', summary: 'Minimal fatigue, rested squad' }), confidence: 0.91, ttl: 3600, ts: now - 30 * 60000 },
      { game_id: 'LVA_NYL', agent_id: 'Agent_2', context_key: 'roster_alert', context_value: JSON.stringify({ player: "A'ja Wilson", status: 'Questionable', injury: 'Right ankle', impact: 'If out, LVA usage redistribution massive' }), confidence: 0.65, ttl: 1800, ts: now - 45 * 60000 },
    ];

    const tx = sqlite.transaction(() => {
      for (const c of sampleContexts) {
        insert.run(c.game_id, c.agent_id, c.context_key, c.context_value, c.confidence, c.ttl, c.ts);
      }
    });
    tx();
    console.log(`✓ Seeded ${sampleContexts.length} agent context entries`);
  } else {
    console.log(`⏭ Agent context store already seeded (${contextCount.cnt} rows)`);
  }

  // ── Seed decision audit trail ────────────────────────────────────────────
  const auditCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM decision_audit').get() as { cnt: number };
  if (auditCount.cnt === 0) {
    const insert = sqlite.prepare(`
      INSERT INTO decision_audit (trace_id, agent_id, action, reason, input_payload, output_payload, confidence, timestamp)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const now = Date.now();
    const traceId1 = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
    const traceId2 = 'f9e8d7c6-b5a4-3210-fedc-ba0987654321';

    const sampleAudits = [
      // Trace 1: Full pipeline — approved
      { trace_id: traceId1, agent_id: 'Agent_11', action: 'APPROVE', reason: 'Detected sharp_money with 82% confidence, divergence 6.5%', confidence: 0.82, ts: now - 5 * 60000 },
      { trace_id: traceId1, agent_id: 'Agent_7', action: 'APPROVE', reason: 'Game exposure 1/3, within limits', confidence: 0.82, ts: now - 4 * 60000 + 30000 },
      { trace_id: traceId1, agent_id: 'Agent_8', action: 'SIZE', reason: 'Sized at $42.50 (NORMAL regime, confidence-adjusted Kelly)', confidence: 0.82, ts: now - 4 * 60000 },
      { trace_id: traceId1, agent_id: 'Agent_4', action: 'EXECUTE', reason: 'Executed $42.50 bet. Drawdown: 3.2%, Confidence: 0.82', confidence: 0.82, ts: now - 3 * 60000 + 45000 },
      // Trace 2: Rejected at Agent 7
      { trace_id: traceId2, agent_id: 'Agent_11', action: 'APPROVE', reason: 'Detected public_drift with 60% confidence', confidence: 0.60, ts: now - 10 * 60000 },
      { trace_id: traceId2, agent_id: 'Agent_7', action: 'REJECT', reason: 'Game exposure 3 exceeds max 3 for confidence < 0.85', confidence: 0.60, ts: now - 9 * 60000 + 30000 },
    ];

    const tx = sqlite.transaction(() => {
      for (const a of sampleAudits) {
        insert.run(a.trace_id, a.agent_id, a.action, a.reason, null, null, a.confidence, a.ts);
      }
    });
    tx();
    console.log(`✓ Seeded ${sampleAudits.length} decision audit entries`);
  } else {
    console.log(`⏭ Decision audit already seeded (${auditCount.cnt} rows)`);
  }

  // ── Seed hedging opportunities ───────────────────────────────────────────
  const hedgeCount = sqlite.prepare('SELECT COUNT(*) as cnt FROM hedging_opportunities').get() as { cnt: number };
  if (hedgeCount.cnt === 0) {
    const insertHedge = sqlite.prepare(`
      INSERT INTO hedging_opportunities (bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const tx = sqlite.transaction(() => {
      insertHedge.run(
        27, // Bet ID
        "Caitlin Clark",
        8.5,
        -110,
        9.5,
        +110,
        14.50,
        "Bet UNDER 9.5 AST @ +110 to lock in a middle opportunity between 8.5 and 9.5 AST.",
        Date.now() - 3600000
      );
      insertHedge.run(
        1, // Bet ID
        "A'ja Wilson",
        22.5,
        -110,
        20.5,
        +200,
        28.00,
        "Bet UNDER 20.5 PTS @ +200 to establish an arbitrage middle with +28.00 EV.",
        Date.now() - 1800000
      );
    });
    tx();
    console.log(`✓ Seeded 2 hedging opportunities`);
  } else {
    console.log(`⏭ Hedging opportunities already seeded (${hedgeCount.cnt} rows)`);
  }

  sqlite.close();
  console.log('✅ Seed complete');
}

// Run if executed directly
if (require.main === module) {
  const forceReset = process.argv.includes('--reset');
  seed({ forceReset });
}
