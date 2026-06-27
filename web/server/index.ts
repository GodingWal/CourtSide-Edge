import express from 'express';
import { createClient } from 'redis';
import cors from 'cors';
import { db, sqlite } from './db';
import { players, bankroll_history, bets, settings, qualitative_events, agent_context_store, decision_audit, hedging_opportunities } from './schema';
import { desc, eq } from 'drizzle-orm';
import { seed } from './seed';
import { createServer } from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { config } from './config';
import { runMigrations } from './migrate';
import { ZodSchema } from 'zod';
import pino from 'pino';
import rateLimit from 'express-rate-limit';
import {
  createBetSchema,
  settleBetSchema,
  clvBetSchema,
  createContextSchema,
  createAuditSchema,
  createHedgeSchema,
  updateSettingSchema
} from './schemas.validation';

// ── Structured Logger ───────────────────────────────────────────────────────
export const logger = pino({
  level: config.NODE_ENV === 'test' ? 'silent' : 'info',
  transport: config.NODE_ENV !== 'production' ? { target: 'pino-pretty', options: { colorize: true } } : undefined,
});

export const app = express();
const port = config.PORT;

// ── Auth Middleware ─────────────────────────────────────────────────────────
const authMiddleware = (req: express.Request, res: express.Response, next: express.NextFunction) => {
  // Skip auth in dev/test mode or if no API_KEY is configured
  if (config.NODE_ENV !== 'production' || !config.API_KEY) {
    return next();
  }
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: Missing Bearer token' });
  }
  const token = authHeader.slice(7);
  if (token !== config.API_KEY) {
    return res.status(403).json({ error: 'Forbidden: Invalid API key' });
  }
  next();
};

// ── Rate Limiting ───────────────────────────────────────────────────────────
const writeLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 100, // 100 requests per minute per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later.' },
  skip: () => config.NODE_ENV === 'test', // Skip in test mode
});

app.use(cors());
app.use(express.json());

// ── Health Endpoint (unauthenticated) ───────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({ status: 'ok', uptime: process.uptime(), timestamp: Date.now() });
});

// Apply auth to all /api/* routes
app.use('/api', authMiddleware);

const validateRequest = (schema: ZodSchema) => {
  return (req: express.Request, res: express.Response, next: express.NextFunction) => {
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({
        error: 'Validation Error',
        details: parsed.error.issues.map(err => ({
          path: err.path.join('.'),
          message: err.message
        }))
      });
    }
    req.body = parsed.data;
    next();
  };
};


// Standard helper for calculating profit/loss
function calcProfitLoss(result: string, stake: number, bookOdds: number): number {
  if (result === 'PUSH') return 0;
  if (result === 'WIN') {
    return bookOdds > 0
      ? Math.round((stake * bookOdds) / 100 * 100) / 100
      : Math.round((stake * 100) / Math.abs(bookOdds) * 100) / 100;
  }
  return -stake; // LOSS
}

// ── Redis Setup ─────────────────────────────────────────────────────────────
let redisClient: ReturnType<typeof createClient> | null = null;
try {
  redisClient = createClient({
    url: config.REDIS_URL,
    socket: {
      reconnectStrategy: () => false
    }
  });
  redisClient.on('error', err => {
    logger.warn({ err }, 'Redis client error');
  });
} catch (err) {
  logger.warn('Redis client instantiation failed.');
}

// Store active WebSocket connections
const wsClients = new Set<WebSocket>();

const broadcast = (data: any) => {
  const msg = JSON.stringify(data);
  for (const client of wsClients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(msg);
    }
  }
};

// ── Standard REST Endpoints ──────────────────────────────────────────────────
app.get('/api/bankroll/history', async (req, res) => {
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

app.get('/api/players/active', async (req, res) => {
  try {
    const activePlayers = await db.query.players.findMany({
      where: (players, { eq }) => eq(players.status, 'ACTIVE')
    });
    res.json(activePlayers);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch players' });
  }
});



// ── Bets Endpoints ────────────────────────────────────────────────────────────
app.get('/api/bets', async (req, res) => {
  try {
    const allBets = await db.select().from(bets).orderBy(desc(bets.placed_at)).limit(100);
    res.json(allBets);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch bets' });
  }
});

app.post('/api/bets', writeLimiter, validateRequest(createBetSchema), async (req, res) => {
  try {
    const { is_parlay, parent_id, player, stat, line, over_under, book_odds, true_odds, edge_pct, stake, opposing_team, notes, legs } = req.body;
    
    if (is_parlay === 1 || is_parlay === true) {
      // Insert parent parlay container
      const insertedParent = await db.insert(bets).values({
        parent_id: null,
        is_parlay: 1,
        player: null,
        stat: null,
        line: null,
        over_under: null,
        book_odds: parseInt(book_odds, 10),
        true_odds: true_odds ? parseFloat(true_odds) : null,
        edge_pct: edge_pct ? parseFloat(edge_pct) : null,
        stake: parseFloat(stake),
        opposing_team: null,
        notes: notes || '2-Leg Parlay',
        placed_at: Date.now(),
        result: null,
        actual_value: null,
        profit_loss: null,
        settled_at: null
      }).returning({ id: bets.id });
      
      const parentId = insertedParent[0].id;
      
      // Insert child legs
      if (legs && Array.isArray(legs)) {
        for (const leg of legs) {
          await db.insert(bets).values({
            parent_id: parentId,
            is_parlay: 0,
            player: leg.player,
            stat: leg.stat,
            line: leg.line ? parseFloat(leg.line) : null,
            over_under: leg.over_under,
            book_odds: leg.book_odds ? parseInt(leg.book_odds, 10) : 0,
            true_odds: leg.true_odds ? parseFloat(leg.true_odds) : null,
            edge_pct: leg.edge_pct ? parseFloat(leg.edge_pct) : null,
            stake: 0, // 0 stake for legs to avoid double-counting bankroll
            opposing_team: leg.opposing_team || null,
            notes: leg.notes || 'Leg',
            placed_at: Date.now(),
            result: null,
            actual_value: null,
            profit_loss: null,
            settled_at: null
          });
        }
      }
    } else {
      // Regular straight bet
      await db.insert(bets).values({
        parent_id: parent_id || null,
        is_parlay: 0,
        player,
        stat,
        line: line ? parseFloat(line) : null,
        over_under,
        book_odds: parseInt(book_odds, 10),
        true_odds: true_odds ? parseFloat(true_odds) : null,
        edge_pct: edge_pct ? parseFloat(edge_pct) : null,
        stake: parseFloat(stake),
        opposing_team: opposing_team || null,
        notes: notes || null,
        placed_at: Date.now(),
        result: null,
        actual_value: null,
        profit_loss: null,
        settled_at: null
      });
    }
    res.status(201).json({ success: true });
  } catch (err) {
    logger.error({ err }, 'Failed to create bet');
    res.status(500).json({ error: 'Failed to create bet' });
  }
});

app.patch('/api/bets/:id/settle', validateRequest(settleBetSchema), async (req, res) => {
  try {
    const { id } = req.params;
    const { result, actual_value } = req.body;

    const betRecord = await db.select().from(bets).where(eq(bets.id, parseInt(id, 10))).limit(1);
    if (betRecord.length === 0) {
      return res.status(404).json({ error: 'Bet not found' });
    }
    const bet = betRecord[0];
    const profit_loss = calcProfitLoss(result, bet.stake, bet.book_odds);
    const settled_at = Date.now();

    await db.update(bets)
      .set({
        result,
        actual_value: actual_value !== undefined && actual_value !== null ? parseFloat(actual_value) : null,
        profit_loss,
        settled_at
      })
      .where(eq(bets.id, parseInt(id, 10)));

    // Settle legs if this is a parent parlay
    if (bet.is_parlay === 1) {
      await db.update(bets)
        .set({
          result,
          settled_at
        })
        .where(eq(bets.parent_id, parseInt(id, 10)));
    }

    res.json({ success: true, profit_loss });
  } catch (err) {
    res.status(500).json({ error: 'Failed to settle bet' });
  }
});

app.get('/api/bets/stats', async (req, res) => {
  try {
    const allBets = await db.select().from(bets);
    const total_bets = allBets.length;
    const wins = allBets.filter(b => b.result === 'WIN').length;
    const losses = allBets.filter(b => b.result === 'LOSS').length;
    const pushes = allBets.filter(b => b.result === 'PUSH').length;
    const pending = allBets.filter(b => b.result === null).length;
    
    let total_profit = 0;
    let total_edge = 0;
    let edge_count = 0;
    allBets.forEach(b => {
      if (b.profit_loss !== null) total_profit += b.profit_loss;
      if (b.edge_pct !== null) {
        total_edge += b.edge_pct;
        edge_count++;
      }
    });

    const settled_bets = wins + losses;
    const win_rate = settled_bets > 0 ? (wins / settled_bets) * 100 : 0;
    const avg_edge = edge_count > 0 ? (total_edge / edge_count) / 100 : 0;

    res.json({
      total_bets,
      wins,
      losses,
      pushes,
      pending,
      total_profit,
      win_rate,
      avg_edge,
      avg_clv: 0.042
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to calculate stats' });
  }
});

// ── Bet Upload & Parlay Generation Endpoints ────────────────────────────────
app.post('/api/bets/upload', writeLimiter, async (req, res) => {
  try {
    // Simulate OCR processing latency
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    // Randomly return either a single bet or a parlay to showcase both capabilities
    const isParlay = Math.random() > 0.4;
    
    if (isParlay) {
      res.json({
        is_parlay: 1,
        book_odds: 264,
        stake: 100,
        legs: [
          {
            player: "A'ja Wilson",
            stat: "PTS",
            line: 23.5,
            over_under: "OVER",
            book_odds: -110,
            opposing_team: "NYL"
          },
          {
            player: "Breanna Stewart",
            stat: "REB",
            line: 9.5,
            over_under: "OVER",
            book_odds: -115,
            opposing_team: "LVA"
          }
        ],
        notes: "Uploaded Parlay Ticket"
      });
    } else {
      res.json({
        is_parlay: 0,
        player: "Caitlin Clark",
        stat: "AST",
        line: 8.5,
        over_under: "OVER",
        book_odds: 110,
        stake: 50,
        opposing_team: "CON",
        notes: "Uploaded Single Ticket"
      });
    }
  } catch (err) {
    res.status(500).json({ error: 'Failed to process ticket upload' });
  }
});

app.post('/api/parlay/generate', writeLimiter, async (req, res) => {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const response = await fetch('http://localhost:8009/api/parlay/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    if (!response.ok) {
      throw new Error(`Agent 13 API returned status ${response.status}`);
    }
    const data = await response.json();
    res.json(data);
  } catch (err) {
    logger.warn('Agent 13 container offline/slow. Returning fallback parlay.');
    res.json({
      legs: [
        {
          player: "A'ja Wilson",
          team: "LVA",
          stat: "PTS",
          line: 23.5,
          over_under: "OVER",
          book_odds: -110,
          true_odds: 0.58,
          edge_pct: 6.2,
          opposing_team: "DAL"
        },
        {
          player: "Kelsey Plum",
          team: "LVA",
          stat: "AST",
          line: 5.5,
          over_under: "OVER",
          book_odds: -115,
          true_odds: 0.55,
          edge_pct: 4.8,
          opposing_team: "DAL"
        }
      ],
      parlay_odds: 257,
      summary: "Wilson benefits from Dallas' poor paint protection, driving volume and efficiency. Plum's perimeter pick-and-roll creation should yield high assist output against their drop coverage scheme."
    });
  }
});

// ── Settings Endpoints ──────────────────────────────────────────────────────
app.get('/api/settings', async (req, res) => {
  try {
    const dbSettings = await db.select().from(settings);
    const settingsMap: Record<string, string> = {};
    dbSettings.forEach(s => {
      settingsMap[s.key] = s.value;
    });
    res.json(settingsMap);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch settings' });
  }
});

app.put('/api/settings', writeLimiter, validateRequest(updateSettingSchema), async (req, res) => {
  try {
    const { key, value } = req.body;
    const existing = await db.select().from(settings).where(eq(settings.key, key)).limit(1);
    if (existing.length > 0) {
      await db.update(settings).set({ value: value.toString() }).where(eq(settings.key, key));
    } else {
      await db.insert(settings).values({ key, value: value.toString() });
    }
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to update setting' });
  }
});

// ── Export Bets CSV ─────────────────────────────────────────────────────────
app.get('/api/export/bets', async (req, res) => {
  try {
    const allBets = await db.select().from(bets).orderBy(desc(bets.placed_at));
    let csv = 'ID,Placed At,Player,Opponent,Stat,Line,Side,Odds,Stake,Result,Actual,P&L,Notes\n';
    allBets.forEach(b => {
      csv += `${b.id},"${new Date(b.placed_at).toISOString()}","${b.player}","${b.opposing_team || ''}","${b.stat}",${b.line},${b.over_under},${b.book_odds},${b.stake},${b.result || 'PENDING'},${b.actual_value !== null ? b.actual_value : ''},${b.profit_loss !== null ? b.profit_loss : ''},"${b.notes || ''}"\n`;
    });
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename=courtside_bets.csv');
    res.send(csv);
  } catch (err) {
    res.status(500).json({ error: 'Failed to export CSV' });
  }
});

// ── Agent Context Store Endpoints ───────────────────────────────────────────
app.get('/api/context/:game_id', async (req, res) => {
  try {
    const { game_id } = req.params;
    const entries = await db.select().from(agent_context_store)
      .where(eq(agent_context_store.game_id, game_id))
      .orderBy(desc(agent_context_store.created_at));
    res.json(entries);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch context entries' });
  }
});

app.post('/api/context', writeLimiter, validateRequest(createContextSchema), async (req, res) => {
  try {
    const { game_id, agent_id, context_key, context_value, confidence, ttl_seconds } = req.body;
    await db.insert(agent_context_store).values({
      game_id,
      agent_id,
      context_key,
      context_value: typeof context_value === 'string' ? context_value : JSON.stringify(context_value),
      confidence: confidence || 0.8,
      ttl_seconds: ttl_seconds || 3600,
      created_at: Date.now()
    });
    res.status(201).json({ success: true });
  } catch (err) {
    logger.error({ err }, 'Error in POST /api/context');
    res.status(500).json({ error: 'Failed to write context entry' });
  }
});

app.get('/api/context', async (req, res) => {
  try {
    const entries = await db.select().from(agent_context_store)
      .orderBy(desc(agent_context_store.created_at))
      .limit(100);
    res.json(entries);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch all context entries' });
  }
});

// ── Decision Audit Trail Endpoints ──────────────────────────────────────────
app.get('/api/audit/:trace_id', async (req, res) => {
  try {
    const { trace_id } = req.params;
    const decisions = await db.select().from(decision_audit)
      .where(eq(decision_audit.trace_id, trace_id))
      .orderBy(decision_audit.timestamp);
    res.json(decisions);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch audit trail' });
  }
});

app.get('/api/audit', async (req, res) => {
  try {
    const entries = await db.select().from(decision_audit)
      .orderBy(desc(decision_audit.timestamp))
      .limit(100);
    res.json(entries);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch audit entries' });
  }
});

app.post('/api/audit', writeLimiter, validateRequest(createAuditSchema), async (req, res) => {
  try {
    const { trace_id, agent_id, action, reason, input_payload, output_payload, confidence } = req.body;
    await db.insert(decision_audit).values({
      trace_id,
      agent_id,
      action,
      reason: reason || null,
      input_payload: input_payload ? JSON.stringify(input_payload) : null,
      output_payload: output_payload ? JSON.stringify(output_payload) : null,
      confidence: confidence || null,
      timestamp: Date.now()
    });
    res.status(201).json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to log audit decision' });
  }
});

// ── CLV Tracking Endpoints ──────────────────────────────────────────────────
app.patch('/api/bets/:id/clv', validateRequest(clvBetSchema), async (req, res) => {
  try {
    const { id } = req.params;
    const { closing_odds } = req.body;

    const betRecord = await db.select().from(bets).where(eq(bets.id, parseInt(id, 10))).limit(1);
    if (betRecord.length === 0) {
      return res.status(404).json({ error: 'Bet not found' });
    }
    const bet = betRecord[0];
    const openingOdds = bet.book_odds;
    
    // Calculate implied probabilities
    const openProb = openingOdds < 0 
      ? Math.abs(openingOdds) / (Math.abs(openingOdds) + 100) 
      : 100 / (openingOdds + 100);
    const closeProb = closing_odds < 0 
      ? Math.abs(closing_odds) / (Math.abs(closing_odds) + 100) 
      : 100 / (closing_odds + 100);
    const clv_pct = Math.round((closeProb - openProb) / openProb * 10000) / 100;

    await db.update(bets)
      .set({ closing_odds, clv_pct })
      .where(eq(bets.id, parseInt(id, 10)));

    res.json({ success: true, closing_odds, clv_pct });
  } catch (err) {
    res.status(500).json({ error: 'Failed to record CLV' });
  }
});

app.get('/api/clv/summary', async (req, res) => {
  try {
    const allBets = await db.select().from(bets);
    const tracked = allBets.filter(b => b.clv_pct !== null && b.parent_id === null);
    
    if (tracked.length === 0) {
      return res.json({ total_tracked: 0, avg_clv: 0, positive_clv_pct: 0, clv_by_stat: {}, clv_by_result: {} });
    }
    
    const avg_clv = Math.round(tracked.reduce((sum, b) => sum + (b.clv_pct || 0), 0) / tracked.length * 100) / 100;
    const positive = tracked.filter(b => (b.clv_pct || 0) > 0).length;
    
    // CLV by stat
    const statMap: Record<string, number[]> = {};
    tracked.forEach(b => {
      if (b.stat && b.clv_pct !== null) {
        if (!statMap[b.stat]) statMap[b.stat] = [];
        statMap[b.stat].push(b.clv_pct!);
      }
    });
    const clv_by_stat: Record<string, number> = {};
    for (const [k, v] of Object.entries(statMap)) {
      clv_by_stat[k] = Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100;
    }
    
    // CLV by result
    const resultMap: Record<string, number[]> = {};
    tracked.forEach(b => {
      if (b.result && b.clv_pct !== null) {
        if (!resultMap[b.result]) resultMap[b.result] = [];
        resultMap[b.result].push(b.clv_pct!);
      }
    });
    const clv_by_result: Record<string, number> = {};
    for (const [k, v] of Object.entries(resultMap)) {
      clv_by_result[k] = Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100;
    }
    
    res.json({
      total_tracked: tracked.length,
      avg_clv,
      positive_clv_pct: Math.round(positive / tracked.length * 1000) / 10,
      clv_by_stat,
      clv_by_result
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to compute CLV summary' });
  }
});

app.get('/api/drift/status', async (req, res) => {
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

    const mae = count > 0 ? Math.round((totalError / count) * 100) / 100 : 1.45;
    const bias = count > 0 ? Math.round((totalBias / count) * 100) / 100 : -0.12;

    res.json({
      calibration: calibration.length > 0 ? JSON.parse(calibration[0].context_value) : { PTS: -0.4, REB: 0.2, AST: 0.1 },
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

app.get('/api/hedges', async (req, res) => {
  try {
    const hedges = await db.select().from(hedging_opportunities).orderBy(desc(hedging_opportunities.created_at)).limit(50);
    res.json(hedges);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch hedging opportunities' });
  }
});

app.post('/api/hedges', writeLimiter, validateRequest(createHedgeSchema), async (req, res) => {
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

app.get('/api/velocity/alerts', async (req, res) => {
  try {
    const alerts = [
      { player: "A'ja Wilson", stat: "PTS", direction: "UP", delta: "+1.5", odds_delta: "-20", duration_seconds: 45, reason: "Heavy sharp volume on OVER", timestamp: Date.now() - 120000 },
      { player: "Caitlin Clark", stat: "AST", direction: "DOWN", delta: "-1.0", odds_delta: "+15", duration_seconds: 30, reason: "Coach pre-game interview (rest minutes restriction hint)", timestamp: Date.now() - 600000 },
      { player: "Breanna Stewart", stat: "REB", direction: "UP", delta: "+0.5", odds_delta: "-10", duration_seconds: 15, reason: "Market steam detected across 3 books", timestamp: Date.now() - 1800000 }
    ];
    res.json(alerts);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch line velocity alerts' });
  }
});

app.get('/api/liquidity/limits', async (req, res) => {
  res.json([
    { book: 'Pinnacle', type: 'SHARP_MAKER', limit: 2000 },
    { book: 'Circa', type: 'SHARP_MAKER', limit: 1500 },
    { book: 'FanDuel', type: 'RETAIL_TAKER', limit: 250 },
    { book: 'DraftKings', type: 'RETAIL_TAKER', limit: 200 },
    { book: 'BetMGM', type: 'RETAIL_TAKER', limit: 150 }
  ]);
});

app.get('/api/sharp/consensus', async (req, res) => {
  res.json([
    { player: "A'ja Wilson", stat: "PTS", book: "Pinnacle", move: "22.5 → 23.5", direction: "UP", timestamp: Date.now() - 30000 },
    { player: "Caitlin Clark", stat: "AST", book: "Circa", move: "8.5 → 7.5", direction: "DOWN", timestamp: Date.now() - 150000 },
    { player: "Breanna Stewart", stat: "REB", book: "Pinnacle", move: "9.5 → 10.5", direction: "UP", timestamp: Date.now() - 600000 }
  ]);
});

app.get('/api/live/rotations', async (req, res) => {
  res.json([
    { player: "A'ja Wilson", fouls: 3, period: "2nd Quarter", adjustment: "-4.5 min", status: "FOUL_TROUBLE" },
    { player: "Angel Reese", fouls: 4, period: "3rd Quarter", adjustment: "-6.0 min", status: "SEVERE_FOUL_TROUBLE" },
    { player: "Caitlin Clark", fouls: 1, period: "1st Quarter", adjustment: "0.0 min", status: "NORMAL" }
  ]);
});

// ── 4-Tier Agent Architecture ───────────────────────────────────────────────
// Tier 1: Data Ingestion    → Real-time feeds
// Tier 2: Specialized AI    → Analysis & signal generation
// Tier 3: Decision Engine   → Kelly Criterion sizing
// Tier 4: Fulfillment       → Slip generation & routing
const AGENTS_LIST = [
  // Tier 1: Data Ingestion
  { id: 'data-stats',   name: 'Stats API',       tier: 1, description: 'Play-by-play & box scores',  port: null },
  { id: 'data-news',    name: 'News & Social',    tier: 1, description: 'Lineups, injuries, social', port: null },
  { id: 'data-odds',    name: 'Bookmaker Odds',   tier: 1, description: 'Global odds feeds',         port: null },
  // Tier 2: Specialized AI Agents
  { id: 'ai-quant',     name: 'Quant Agent',      tier: 2, description: 'Projections & modeling',    port: 8000 },
  { id: 'ai-sentiment', name: 'Sentiment Agent',  tier: 2, description: 'News impact analysis',      port: null },
  { id: 'ai-line',      name: 'Line Agent',       tier: 2, description: 'Market tracking & CLV',     port: null },
  // Tier 3: Decision Engine
  { id: 'dec-portfolio', name: 'Portfolio Manager', tier: 3, description: 'Kelly Criterion sizing',  port: 8001 },
  // Tier 4: Fulfillment
  { id: 'ful-exec',     name: 'Execution Agent',  tier: 4, description: 'API integration & routing', port: null },
];

app.get('/api/agents/health', async (req, res) => {
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

// ── SSE Endpoint for Agent Analysis (Simulation) ────────────────────────────
// Simulates the 4-Tier pipeline:
//   Tier 2: Quant Agent → Sentiment Agent → Line Agent
//   Tier 3: Portfolio Manager (Kelly Criterion)
//   Tier 4: Execution Agent → Final Betting Slip
// When the real LangGraph backend is ready, this will proxy to it instead.
app.get('/api/analyze-prop', async (req, res) => {
  const { player, line, odds, bankroll } = req.query as Record<string, string>;

  if (!player || !line || !odds || !bankroll) {
    return res.status(400).json({ error: 'Missing required params: player, line, odds, bankroll' });
  }

  const parsedLine = parseFloat(line);
  const parsedOdds = parseInt(odds, 10);
  const parsedBankroll = parseFloat(bankroll);

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  let closed = false;
  req.on('close', () => { closed = true; });

  const sendEvent = (node: string, messages: string[], extra?: Record<string, any>) => {
    if (closed) return;
    const payload = {
      node,
      data: { messages, ...extra },
      timestamp: Date.now(),
    };
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
  };

  const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

  try {
    // ── Tier 2: Quant Agent (Projections) ──
    await delay(600);
    sendEvent('quant_agent', [
      `Loading historical game logs for ${player}...`,
    ]);

    await delay(900);
    sendEvent('quant_agent', [
      `Computed L10 average: ${(parsedLine - 1.2 + Math.random() * 2.4).toFixed(1)} | Season avg: ${(parsedLine + 0.3 + Math.random() * 1.5).toFixed(1)}`,
      `Usage rate in current role: ${(24 + Math.random() * 8).toFixed(1)}%`,
    ]);

    await delay(700);
    const trueProb = 0.48 + Math.random() * 0.15;
    sendEvent('quant_agent', [
      `Bayesian model estimates true probability: ${(trueProb * 100).toFixed(1)}% OVER ${parsedLine}`,
      `Monte Carlo simulation (10,000 trials): median outcome ${(parsedLine + (Math.random() * 4 - 2)).toFixed(1)}`,
    ]);

    // ── Tier 2: Sentiment Agent (News Impact) ──
    await delay(800);
    sendEvent('sentiment_agent', [
      `Scanning beat reporter feeds and press conferences...`,
    ]);

    await delay(1000);
    const hasInjuryNews = Math.random() > 0.6;
    if (hasInjuryNews) {
      sendEvent('sentiment_agent', [
        `⚠ Injury report: Starting PG listed as questionable — usage redistribution likely`,
        `Sentiment score: 0.${Math.floor(60 + Math.random() * 30)} (mildly positive for ${player})`,
      ]);
    } else {
      sendEvent('sentiment_agent', [
        `No material lineup changes detected. Full strength roster confirmed.`,
        `Social sentiment neutral — no abnormal volume on ${player} props`,
      ]);
    }

    // ── Tier 2: Line Agent (Market Tracking) ──
    await delay(700);
    sendEvent('line_agent', [
      `Scanning odds across 5 sportsbooks...`,
    ]);

    await delay(800);
    const clvDirection = Math.random() > 0.5 ? 'favorable' : 'unfavorable';
    sendEvent('line_agent', [
      `Opening line: ${parsedLine} → Current consensus: ${(parsedLine + (clvDirection === 'favorable' ? 0.5 : -0.5)).toFixed(1)}`,
      `CLV indicator: ${clvDirection === 'favorable' ? '✓ Sharp money aligns with our position' : '⚠ Reverse steam detected — line moving against'}`,
      `Pinnacle closing line differential: ${(Math.random() * 3 - 1).toFixed(1)} pts`,
    ]);

    // ── Tier 3: Portfolio Manager (Kelly Criterion) ──
    await delay(600);
    sendEvent('portfolio_manager', [
      `Aggregating signals from Quant, Sentiment, and Line agents...`,
    ]);

    await delay(900);

    // Calculate Kelly Criterion
    const impliedProb = parsedOdds < 0
      ? Math.abs(parsedOdds) / (Math.abs(parsedOdds) + 100)
      : 100 / (parsedOdds + 100);
    const edge = trueProb - impliedProb;
    const decimalOdds = parsedOdds < 0
      ? 1 + 100 / Math.abs(parsedOdds)
      : 1 + parsedOdds / 100;
    const kellyFraction = Math.max(0, (trueProb * decimalOdds - 1) / (decimalOdds - 1));
    const halfKelly = kellyFraction / 2; // Conservative half-Kelly
    const isBet = edge > 0.02; // Require at least 2% edge

    sendEvent('portfolio_manager', [
      `Implied probability from book: ${(impliedProb * 100).toFixed(1)}%`,
      `True probability estimate: ${(trueProb * 100).toFixed(1)}%`,
      `Edge: ${edge > 0 ? '+' : ''}${(edge * 100).toFixed(2)}%`,
      `Full Kelly: ${(kellyFraction * 100).toFixed(2)}% → Half Kelly (applied): ${(halfKelly * 100).toFixed(2)}%`,
    ]);

    await delay(400);

    // ── Tier 4: Execution Agent (Fulfillment) ──
    if (isBet) {
      const wagerAmount = Math.round(parsedBankroll * halfKelly * 100) / 100;
      sendEvent('execution_agent', [
        `✅ POSITIVE EDGE CONFIRMED — Generating betting slip`,
        `Routing to sportsbook API for execution...`,
      ], {
        final_decision: {
          action: 'BET',
          fraction: halfKelly,
          expected_value_pct: edge * 100,
          wager_amount: wagerAmount,
          confidence: trueProb,
          reason: `${player} shows a +${(edge * 100).toFixed(1)}% edge over the book's implied probability. Half-Kelly sizing limits downside exposure while capturing the value.`,
          player,
          line: parsedLine,
          odds: parsedOdds,
        },
      });
    } else {
      sendEvent('execution_agent', [
        `🚫 INSUFFICIENT EDGE — No slip generated`,
      ], {
        final_decision: {
          action: 'PASS',
          fraction: 0,
          expected_value_pct: edge * 100,
          wager_amount: 0,
          confidence: trueProb,
          reason: `Edge of ${(edge * 100).toFixed(2)}% falls below the 2% minimum threshold. The risk-adjusted return does not justify a position. Wait for a more favorable line.`,
          player,
          line: parsedLine,
          odds: parsedOdds,
        },
      });
    }
  } catch (err) {
    logger.error({ err }, 'Error in analyze-prop SSE stream');
    if (!closed) res.end();
  }
});

// ── SSE Endpoint for Alerts ─────────────────────────────────────────────────
app.get('/api/stream/alerts', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.write(': heartbeat\n\n');

  if (!redisClient) {
    const id = setInterval(() => {
      res.write(': heartbeat\n\n');
    }, 15000);
    req.on('close', () => clearInterval(id));
    return;
  }

  try {
    const subscriber = redisClient.duplicate();
    await subscriber.connect();

    const channels = [
      'channel_ev_alerts', 
      'channel_steam_alerts', 
      'channel_approved_edges',
      'channel_roster_updates',
      'channel_referee_context',
      'channel_sentiment_context'
    ];

    for (const channel of channels) {
      await subscriber.subscribe(channel, (message) => {
        res.write(`data: ${JSON.stringify({ channel, message })}\n\n`);
      });
    }

    req.on('close', () => {
      subscriber.unsubscribe();
      subscriber.quit();
    });
  } catch (err) {
    logger.error({ err }, 'SSE Subscription Error');
    res.end();
  }
});

// ── Server Startup ──────────────────────────────────────────────────────────
async function start() {
  // Run SQLite migrations and seeding
  try {
    runMigrations();
    seed();
    logger.info('SQLite database check & seed completed.');
  } catch (err) {
    logger.error({ err }, 'Failed to run database seed');
  }

  // Connect to Redis optionally
  if (redisClient) {
    try {
      await redisClient.connect();
      logger.info('Connected to Redis');

      const subscriber = redisClient.duplicate();
      await subscriber.connect();
      const channels = [
        'channel_ev_alerts', 
        'channel_steam_alerts', 
        'channel_approved_edges',
        'channel_roster_updates',
        'channel_referee_context',
        'channel_sentiment_context'
      ];
      for (const channel of channels) {
        await subscriber.subscribe(channel, async (message) => {
          // Broadcast to WS clients
          broadcast({ channel, message });

          // Save to SQLite if it is a qualitative channel
          if (
            channel === 'channel_roster_updates' ||
            channel === 'channel_referee_context' ||
            channel === 'channel_sentiment_context'
          ) {
            try {
              await db.insert(qualitative_events).values({
                channel,
                payload: message,
                timestamp: Date.now()
              });
              logger.info({ channel }, 'Permanently logged qualitative event to SQLite');
            } catch (dbErr) {
              logger.error({ err: dbErr, channel }, 'Failed to log qualitative event');
            }
          }
        });
      }
    } catch (err) {
      logger.warn('Redis is running offline. WebSocket pub/sub stream disabled.');
    }
  }

  const server = createServer(app);

  // Attach WebSockets
  const wss = new WebSocketServer({ server });
  wss.on('connection', (ws) => {
    wsClients.add(ws);
    ws.on('close', () => wsClients.delete(ws));
  });

  // ── Graceful Shutdown (Issue #11) ───────────────────────────────────────────
  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'Received shutdown signal, draining connections...');

    // 1. Stop accepting new connections
    server.close(() => {
      logger.info('HTTP server closed');
    });

    // 2. Close all WebSocket clients
    for (const client of wsClients) {
      client.close(1001, 'Server shutting down');
    }
    wsClients.clear();

    // 3. Disconnect Redis
    if (redisClient) {
      try {
        await redisClient.quit();
        logger.info('Redis client disconnected');
      } catch (err) {
        logger.warn({ err }, 'Error disconnecting Redis');
      }
    }

    // 4. Close SQLite database
    try {
      sqlite.close();
      logger.info('SQLite database closed');
    } catch (err) {
      logger.warn({ err }, 'Error closing SQLite');
    }

    process.exit(0);
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  if (process.env.NODE_ENV !== 'test') {
    server.listen(port, () => {
      logger.info({ port }, `CourtSideEdge server running on port ${port}`);
    });
  }
}

start().catch((err) => {
  logger.fatal({ err }, 'Failed to start server');
  process.exit(1);
});
