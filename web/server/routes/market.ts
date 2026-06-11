import { Router } from 'express';
import { randomUUID } from 'crypto';
import { db } from '../db';
import { bankroll_history, bets, agent_context_store, hedging_opportunities, qualitative_events } from '../schema';
import { desc, eq } from 'drizzle-orm';
import { logger } from '../logger';
import { redisClient } from '../redis';
import { writeLimiter, validateRequest } from '../middleware';
import { createHedgeSchema } from '../schemas.validation';

const router = Router();

// ── Standard REST Endpoints ──────────────────────────────────────────────────
router.get('/bankroll/history', async (req, res) => {
  try {
    const history = await db.query.bankroll_history.findMany({
      orderBy: [desc(bankroll_history.timestamp)],
      limit: 100
    });
    res.json(history);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch bankroll history' });
  }
});

// Manually set the current bankroll (inserts a bankroll_history point).
router.post('/bankroll', writeLimiter, async (req, res) => {
  try {
    const balance = parseFloat(req.body?.balance);
    if (!Number.isFinite(balance) || balance < 0 || balance > 100000000) {
      return res.status(400).json({ error: 'balance must be a non-negative number' });
    }
    // drawdown vs historical peak (0 when this is a new peak)
    const hist = await db.select().from(bankroll_history);
    const peak = Math.max(balance, ...hist.map((h) => h.balance), 0);
    const drawdown_pct = peak > 0 ? Math.round(((peak - balance) / peak) * 10000) / 100 : 0;
    await db.insert(bankroll_history).values({ timestamp: Date.now(), balance, drawdown_pct });
    res.status(201).json({ success: true, balance });
  } catch (err) {
    logger.error({ err }, 'Failed to set bankroll');
    res.status(500).json({ error: 'Failed to set bankroll' });
  }
});

router.get('/players/active', async (req, res) => {
  try {
    const activePlayers = await db.query.players.findMany({
      where: (players, { eq }) => eq(players.status, 'ACTIVE')
    });
    res.json(activePlayers);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch players' });
  }
});

router.get('/drift/status', async (req, res) => {
  try {
    const calibration = await db.select()
      .from(agent_context_store)
      .where(eq(agent_context_store.context_key, 'projection_calibration'))
      .orderBy(desc(agent_context_store.created_at))
      .limit(1);

    const allBets = await db.select().from(bets);
    const settledBets = allBets.filter(b => b.is_parlay === 0 && b.result !== null && b.actual_value !== null && b.line !== null);

    let totalError = 0;
    let totalBias = 0;
    const count = settledBets.length;

    settledBets.forEach(b => {
      const error = b.actual_value! - b.line!;
      totalError += Math.abs(error);
      totalBias += error;
    });

    const mae = count > 0 ? Math.round((totalError / count) * 100) / 100 : null;
    const bias = count > 0 ? Math.round((totalBias / count) * 100) / 100 : null;

    res.json({
      calibration: calibration.length > 0 ? JSON.parse(calibration[0].context_value) : null,
      last_checked: calibration.length > 0 ? calibration[0].created_at : Date.now(),
      mae,
      bias,
      settled_bets_analyzed: count
    });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch model drift status');
    res.status(500).json({ error: 'Failed to fetch model drift status' });
  }
});

router.get('/hedges', async (req, res) => {
  try {
    const hedges = await db.select().from(hedging_opportunities).orderBy(desc(hedging_opportunities.created_at)).limit(50);
    res.json(hedges);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch hedging opportunities' });
  }
});

router.post('/hedges', writeLimiter, validateRequest(createHedgeSchema), async (req, res) => {
  try {
    const { bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions } = req.body;
    await db.insert(hedging_opportunities).values({
      bet_id,
      hedged_player,
      original_line: parseFloat(original_line),
      original_odds: parseInt(original_odds, 10),
      live_line: parseFloat(live_line),
      live_odds: parseInt(live_odds, 10),
      potential_profit: parseFloat(potential_profit),
      hedge_instructions,
      created_at: Date.now()
    });
    res.status(201).json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to create hedging opportunity' });
  }
});

// ── Live agent signals (read from Redis; agents publish them) ───────────────
// Each agent maintains a capped Redis list of its most recent signals. These
// endpoints surface whatever is genuinely there — empty array when no live
// signal has been produced, never fabricated data.
async function readRecentList(key: string, limit = 50): Promise<unknown[]> {
  if (!redisClient?.isOpen) return [];
  const raw = await redisClient.lRange(key, 0, limit - 1);
  return raw.map((item) => {
    try { return JSON.parse(item); } catch { return null; }
  }).filter((x) => x !== null);
}

// Recent qualitative events (real agent publications persisted by the server).
router.get('/events/recent', async (req, res) => {
  try {
    const rows = await db.query.qualitative_events.findMany({
      orderBy: [desc(qualitative_events.timestamp)],
      limit: 100,
    });
    const events = rows.map((r) => {
      let payload: unknown = r.payload;
      try { payload = JSON.parse(r.payload as string); } catch { /* keep raw */ }
      return { channel: r.channel, payload, timestamp: r.timestamp };
    });
    res.json(events);
  } catch (err) {
    logger.error({ err }, 'Failed to fetch recent events');
    res.status(500).json({ error: 'Failed to fetch recent events' });
  }
});

// Most recent market-intelligence edges from the Redis stream (agent 11).
router.get('/edges/recent', async (req, res) => {
  try {
    if (!redisClient?.isOpen) return res.json([]);
    const entries = await redisClient.xRevRange('stream_market_intelligence', '+', '-', { COUNT: 25 });
    const edges = entries.map((e: any) => {
      try { return JSON.parse(e.message?.data ?? '{}'); } catch { return null; }
    }).filter((x: any) => x && Object.keys(x).length > 0);
    res.json(edges);
  } catch (err) {
    logger.error({ err }, 'Failed to fetch recent edges');
    res.status(500).json({ error: 'Failed to fetch recent edges' });
  }
});

router.get('/velocity/alerts', async (req, res) => {
  try {
    res.json(await readRecentList('recent:velocity_alerts'));
  } catch (err) {
    logger.error({ err }, 'Failed to fetch line velocity alerts');
    res.status(500).json({ error: 'Failed to fetch line velocity alerts' });
  }
});

router.get('/liquidity/limits', async (req, res) => {
  try {
    res.json(await readRecentList('recent:liquidity_limits'));
  } catch (err) {
    logger.error({ err }, 'Failed to fetch liquidity limits');
    res.status(500).json({ error: 'Failed to fetch liquidity limits' });
  }
});

router.get('/sharp/consensus', async (req, res) => {
  try {
    res.json(await readRecentList('recent:sharp_consensus'));
  } catch (err) {
    logger.error({ err }, 'Failed to fetch sharp consensus');
    res.status(500).json({ error: 'Failed to fetch sharp consensus' });
  }
});

router.get('/live/rotations', async (req, res) => {
  try {
    res.json(await readRecentList('recent:rotations'));
  } catch (err) {
    logger.error({ err }, 'Failed to fetch live rotations');
    res.status(500).json({ error: 'Failed to fetch live rotations' });
  }
});

// ── Alpha Sandbox chat (bridged over Redis to Agent 12 / local Nemotron) ────
router.post('/sandbox/chat', writeLimiter, async (req, res) => {
  try {
    const message = String(req.body?.message ?? '').trim().slice(0, 2000);
    if (!message) return res.status(400).json({ error: 'message is required' });
    if (!redisClient?.isOpen) {
      return res.status(503).json({ error: 'Analysis engine offline (Redis unavailable).' });
    }
    const id = randomUUID();
    await redisClient.lPush('sandbox:requests', JSON.stringify({ id, message, ts: Date.now() }));

    // Poll for Agent 12's reply (local LLM inference can take a while).
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      const raw = await redisClient.get(`sandbox:response:${id}`);
      if (raw) {
        await redisClient.del(`sandbox:response:${id}`);
        return res.json(JSON.parse(raw));
      }
      await new Promise((r) => setTimeout(r, 750));
    }
    res.status(504).json({ error: 'Agent 12 did not respond in time. Is the agent tier running?' });
  } catch (err) {
    logger.error({ err }, 'Sandbox chat failed');
    res.status(500).json({ error: 'Sandbox chat failed' });
  }
});

// ── Agents Health Telemetry ─────────────────────────────────────────────────
// Liveness is real: every Python agent maintains heartbeat:agent:<id> in
// Redis (90s TTL, refreshed every 30s) regardless of which host it runs on.
const AGENTS_LIST = [
  { id: '0', name: 'Historical ETL' },
  { id: '1', name: 'Market Scraper' },
  { id: '2', name: 'News Sentinel' },
  { id: '2.5', name: 'Game Flow Oracle' },
  { id: '3', name: 'Projection Engine' },
  { id: '4', name: 'Execution Oracle' },
  { id: '5', name: 'Referee Engine' },
  { id: '6', name: 'Steam Detector' },
  { id: '7', name: 'Correlation Guard' },
  { id: '8', name: 'Bankroll Sizer' },
  { id: '9', name: 'News Sentiment' },
  { id: '10', name: 'Game Total Projector' },
  { id: '11', name: 'Market Value Detector' },
  { id: '12', name: 'Alpha Sandbox' },
  { id: '13', name: 'Matchup Oracle / Parlay Gen' },
  { id: '14', name: 'CLV Tracker' },
  { id: '15', name: 'Drift Monitor' },
  { id: '16', name: 'Hedge Oracle' },
  { id: '17', name: 'Velocity Agent' },
  { id: '18', name: 'Liquidity Oracle' },
  { id: '19', name: 'Sharp Profiler' },
  { id: '20', name: 'Hedge Executor' },
  { id: '21', name: 'Rotation Tracker' },
  { id: '22', name: 'Data Watchdog' },
  { id: '23', name: 'Game Session Manager' }
];

router.get('/agents/health', async (req, res) => {
  try {
    if (!redisClient?.isOpen) {
      return res.json(AGENTS_LIST.map((a) => ({ ...a, port: null, status: 'offline' as const })));
    }
    const keys = AGENTS_LIST.map((a) => `heartbeat:agent:${a.id}`);
    const beats = await redisClient.mGet(keys);
    const results = AGENTS_LIST.map((agent, i) => ({
      ...agent,
      port: null,
      status: beats[i] ? ('online' as const) : ('offline' as const),
    }));
    res.json(results);
  } catch (err) {
    logger.error({ err }, 'Failed to query agent health');
    res.status(500).json({ error: 'Failed to query agent health' });
  }
});

export default router;
