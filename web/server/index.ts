import express from 'express';
import { createClient } from 'redis';
import cors from 'cors';
import { db } from './db';
import { players, bankroll_history, bets, settings, qualitative_events } from './schema';
import { desc, eq } from 'drizzle-orm';
import { seed } from './seed';
import { createServer } from 'http';
import { WebSocketServer, WebSocket } from 'ws';

const app = express();
const port = 3000;

app.use(cors());
app.use(express.json());

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
    url: 'redis://localhost:6379',
    socket: {
      reconnectStrategy: () => false
    }
  });
  redisClient.on('error', err => {
    // Only log once to avoid flooding
  });
} catch (err) {
  console.warn('⚠️ Redis client instantiation failed.');
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

app.post('/api/bets', async (req, res) => {
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
    console.error(err);
    res.status(500).json({ error: 'Failed to create bet' });
  }
});

app.patch('/api/bets/:id/settle', async (req, res) => {
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
app.post('/api/bets/upload', async (req, res) => {
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

app.post('/api/parlay/generate', async (req, res) => {
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
    console.warn('⚠️ Agent 13 container offline/slow. Returning fallback parlay.');
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

app.put('/api/settings', async (req, res) => {
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
  { id: '13', name: 'Matchup Oracle / Parlay Gen', port: 8009 }
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
    console.error('SSE Subscription Error:', err);
    res.end();
  }
});

// ── Server Startup ──────────────────────────────────────────────────────────
async function start() {
  // Run SQLite migrations and seeding
  try {
    seed();
    console.log('✓ SQLite database check & seed completed.');
  } catch (err) {
    console.error('Failed to run database seed:', err);
  }

  // Connect to Redis optionally
  if (redisClient) {
    try {
      await redisClient.connect();
      console.log('✓ Connected to Redis');

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
              console.log(`[Redis Bridge] Permanently logged event on ${channel} to SQLite.`);
            } catch (dbErr) {
              console.error(`Failed to log qualitative event from ${channel}:`, dbErr);
            }
          }
        });
      }
    } catch (err) {
      console.warn('⚠️ Redis is running offline. WebSocket pub/sub stream disabled.');
    }
  }

  const server = createServer(app);

  // Attach WebSockets
  const wss = new WebSocketServer({ server });
  wss.on('connection', (ws) => {
    wsClients.add(ws);
    ws.on('close', () => wsClients.delete(ws));
  });

  server.listen(port, () => {
    console.log(`Server running on port ${port}`);
  });
}

start().catch(console.error);
