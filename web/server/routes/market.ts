import { Router } from 'express';
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

// ── Agents Health Telemetry ─────────────────────────────────────────────────
const AGENTS_LIST = [
  { id: '0', name: 'Historical ETL', port: null },
  { id: '1', name: 'Market Scraper', port: null },
  { id: '2', name: 'News Sentinel', port: null },
  { id: '2.5', name: 'Game Flow Oracle', port: null },
  { id: '3', name: 'Projection Engine', port: 8000 },
  { id: '4', name: 'Execution Oracle', port: 8001 },
  { id: '5', name: 'Referee Engine', port: null },
  { id: '6', name: 'Steam Detector', port: null },
  { id: '7', name: 'Correlation Guard', port: null },
  { id: '8', name: 'Bankroll Sizer', port: null },
  { id: '9', name: 'News Sentiment', port: null },
  { id: '10', name: 'Game Total Projector', port: null },
  { id: '11', name: 'Market Value Detector', port: null },
  { id: '13', name: 'Matchup Oracle / Parlay Gen', port: 8009 },
  { id: '14', name: 'CLV Tracker', port: 8010 },
  { id: '15', name: 'Drift Monitor', port: 8011 },
  { id: '16', name: 'Hedge Oracle', port: 8012 },
  { id: '17', name: 'Velocity Agent', port: 8013 },
  { id: '18', name: 'Liquidity Oracle', port: 8014 },
  { id: '19', name: 'Sharp Profiler', port: 8015 },
  { id: '20', name: 'Hedge Executor', port: 8016 },
  { id: '21', name: 'Rotation Tracker', port: 8017 }
];

router.get('/agents/health', async (req, res) => {
  try {
    const results = await Promise.all(AGENTS_LIST.map(async (agent) => {
      if (agent.port) {
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 2000);
          const response = await fetch(`http://localhost:${agent.port}/health`, { signal: controller.signal });
          clearTimeout(timeoutId);
          if (response.ok) {
            return { ...agent, status: 'online' as const };
          }
        } catch (e) {
          // fetch error or timeout
        }
      }
      return { ...agent, status: agent.port ? 'offline' as const : 'online' as const };
    }));
    res.json(results);
  } catch (err) {
    res.status(500).json({ error: 'Failed to query agent health' });
  }
});

export default router;
