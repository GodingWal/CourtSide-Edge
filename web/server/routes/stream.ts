import { Router } from 'express';
import { logger } from '../logger';
import { redisClient } from '../redis';

const router = Router();

// ── SSE Endpoint for Alerts ─────────────────────────────────────────────────
router.get('/stream/alerts', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.write(': heartbeat\n\n');

  // Periodic heartbeat on every path so idle proxies don't drop the stream.
  const heartbeat = setInterval(() => {
    res.write(': heartbeat\n\n');
  }, 15000);

  let subscriber: NonNullable<typeof redisClient> | null = null;
  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    clearInterval(heartbeat);
    if (subscriber) {
      const sub = subscriber;
      void sub.unsubscribe().catch(() => {}).finally(() => {
        void sub.quit().catch(() => {});
      });
      subscriber = null;
    }
  };
  // Register BEFORE any await: a client that disconnects while the Redis
  // duplicate is still connecting must not leak that connection.
  req.on('close', cleanup);

  if (!redisClient) return;

  try {
    subscriber = redisClient.duplicate();
    await subscriber.connect();
    if (closed) {
      // Client went away during connect — tear the duplicate down now.
      const sub = subscriber;
      subscriber = null;
      await sub.quit().catch(() => {});
      return;
    }

    // Channels the agents actually publish (critical-path edges travel on
    // Redis Streams, not Pub/Sub).
    const channels = [
      'channel_steam_alerts',
      'channel_sharp_moves',
      'channel_true_projections',
      'channel_total_projections',
      'channel_roster_updates',
      'channel_referee_context',
      'channel_sentiment_context'
    ];

    await Promise.all(
      channels.map((channel) =>
        subscriber!.subscribe(channel, (message) => {
          res.write(`data: ${JSON.stringify({ channel, message })}\n\n`);
        })
      )
    );
  } catch (err) {
    logger.error({ err }, 'SSE Subscription Error');
    cleanup();
    res.end();
  }
});

export default router;
