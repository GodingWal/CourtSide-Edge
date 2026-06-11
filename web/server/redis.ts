import { createClient } from 'redis';
import { config } from './config';
import { logger } from './logger';

// ── Redis Setup ─────────────────────────────────────────────────────────────
let client: ReturnType<typeof createClient> | null = null;
try {
  client = createClient({
    url: config.REDIS_URL,
    socket: {
      reconnectStrategy: () => false
    }
  });
  client.on('error', err => {
    logger.warn({ err }, 'Redis client error');
  });
} catch (err) {
  logger.warn('Redis client instantiation failed.');
}

export const redisClient = client;
