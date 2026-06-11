import { createClient } from 'redis';
import { config } from './config';
import { logger } from './logger';

// ── Redis Setup ─────────────────────────────────────────────────────────────
// Reconnect with capped backoff. The previous `() => false` strategy meant a
// single Redis blip permanently disabled every live feature (WS broadcasts,
// agent health, stats) until the server was restarted. node-redis re-issues
// pub/sub subscriptions automatically after a reconnect.
let client: ReturnType<typeof createClient> | null = null;
try {
  client = createClient({
    url: config.REDIS_URL,
    socket: {
      reconnectStrategy: (retries) => Math.min(retries * 200, 5000)
    }
  });
  client.on('error', err => {
    logger.warn({ err }, 'Redis client error');
  });
  client.on('reconnecting', () => {
    logger.warn('Redis reconnecting...');
  });
} catch (err) {
  logger.warn('Redis client instantiation failed.');
}

export const redisClient = client;
