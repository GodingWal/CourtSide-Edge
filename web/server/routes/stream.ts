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
        subscriber.subscribe(channel, (message) => {
          res.write(`data: ${JSON.stringify({ channel, message })}\n\n`);
        })
      )
    );

    req.on('close', () => {
      subscriber.unsubscribe();
      subscriber.quit();
    });
  } catch (err) {
    logger.error({ err }, 'SSE Subscription Error');
    res.end();
  }
});

export default router;
