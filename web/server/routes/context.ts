import { Router } from 'express';
import { db } from '../db';
import { agent_context_store } from '../schema';
import { desc, eq } from 'drizzle-orm';
import { logger } from '../logger';
import { writeLimiter, validateRequest } from '../middleware';
import { createContextSchema } from '../schemas.validation';

const router = Router();

// ── Agent Context Store Endpoints ───────────────────────────────────────────
router.get('/context/:game_id', async (req, res) => {
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

router.post('/context', writeLimiter, validateRequest(createContextSchema), async (req, res) => {
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

router.get('/context', async (req, res) => {
  try {
    const entries = await db.select().from(agent_context_store)
      .orderBy(desc(agent_context_store.created_at))
      .limit(100);
    res.json(entries);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch all context entries' });
  }
});

export default router;
