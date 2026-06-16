import express from 'express';
import cors from 'cors';
import { db, qualitative_events, closeDb } from './db';
import { seed } from './seed';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import { config } from './config';
import { runMigrations } from './migrate';
import { logger } from './logger';
import { authMiddleware } from './middleware';
import { redisClient } from './redis';
import { wsClients, broadcast, verifyWsClient } from './ws';
import betsRouter from './routes/bets';
import parlayRouter from './routes/parlay';
import settingsRouter from './routes/settings';
import contextRouter from './routes/context';
import auditRouter from './routes/audit';
import marketRouter from './routes/market';
import streamRouter from './routes/stream';
import statsRouter from './routes/stats';
import patternsRouter from './routes/patterns';

export { logger };

export const app = express();
const port = config.PORT;

// Behind the host nginx reverse proxy: trust exactly one hop so req.ip is the
// real client (otherwise every visitor shares 127.0.0.1 and the per-IP rate
// limit becomes one global bucket).
app.set('trust proxy', 1);

app.use(cors({ origin: config.FRONTEND_URL }));
app.use(express.json());

// ── Health Endpoint (unauthenticated) ───────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({ status: 'ok', uptime: process.uptime(), timestamp: Date.now() });
});

// Apply auth to all /api/* routes
app.use('/api', authMiddleware);

// ── API Routes ──────────────────────────────────────────────────────────────
app.use('/api', marketRouter);
app.use('/api', betsRouter);
app.use('/api', parlayRouter);
app.use('/api', settingsRouter);
app.use('/api', contextRouter);
app.use('/api', auditRouter);
app.use('/api', streamRouter);
app.use('/api', statsRouter);
app.use('/api', patternsRouter);

// ── Server Startup ──────────────────────────────────────────────────────────
async function start() {
  // Run database migrations and seeding (SQLite or Postgres per DATABASE_URL)
  try {
    await runMigrations();
    await seed();
    logger.info('Database check & seed completed.');
  } catch (err) {
    logger.error({ err }, 'Failed to run database seed');
  }

  // Connect to Redis in the background: with a reconnect strategy the connect
  // promise only resolves once Redis is reachable, and HTTP startup must not
  // block on that.
  const setupRedis = async () => {
    if (!redisClient) return;
    try {
      await redisClient.connect();
      logger.info('Connected to Redis');

      const subscriber = redisClient.duplicate();
      await subscriber.connect();
      // Every Pub/Sub channel the agents actually publish. (Approved edges and
      // market intelligence travel on Redis Streams — see routes/market.ts —
      // not Pub/Sub.)
      const channels = [
        'channel_live_odds',
        'channel_steam_alerts',
        'channel_sharp_moves',
        'channel_game_active',
        'channel_game_context',
        'channel_true_projections',
        'channel_total_projections',
        'channel_system_health',
        'channel_roster_updates',
        'channel_referee_context',
        'channel_sentiment_context'
      ];
      await Promise.all(
        channels.map((channel) =>
          subscriber.subscribe(channel, async (message) => {
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
                logger.info({ channel }, 'Permanently logged qualitative event to the database');
              } catch (dbErr) {
                logger.error({ err: dbErr, channel }, 'Failed to log qualitative event');
              }
            }
          })
        )
      );
    } catch (err) {
      logger.warn({ err }, 'Redis setup failed; retrying in 10s.');
      setTimeout(setupRedis, 10_000);
    }
  };
  void setupRedis();

  const server = createServer(app);

  // Attach WebSockets — when API_KEY is set, connections must present a valid
  // token (Authorization: Bearer header or ?token= query param) at upgrade time.
  const wss = new WebSocketServer({ server, verifyClient: verifyWsClient });
  wss.on('connection', (ws) => {
    wsClients.add(ws);
    ws.on('close', () => wsClients.delete(ws));
  });

  // ── Graceful Shutdown (Issue #11) ───────────────────────────────────────────
  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'Received shutdown signal, draining connections...');

    // 1. Close all WebSocket clients first so the HTTP server can drain.
    for (const client of wsClients) {
      client.close(1001, 'Server shutting down');
    }
    wsClients.clear();

    // 2. Stop accepting new connections and wait (bounded) for in-flight
    // requests — previously process.exit(0) raced the close callback.
    await Promise.race([
      new Promise<void>((resolve) => server.close(() => {
        logger.info('HTTP server closed');
        resolve();
      })),
      new Promise<void>((resolve) => setTimeout(resolve, 5000))
    ]);

    // 3. Disconnect Redis
    if (redisClient) {
      try {
        await redisClient.quit();
        logger.info('Redis client disconnected');
      } catch (err) {
        logger.warn({ err }, 'Error disconnecting Redis');
      }
    }

    // 4. Close the database
    try {
      await closeDb();
      logger.info('Database closed');
    } catch (err) {
      logger.warn({ err }, 'Error closing database');
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
