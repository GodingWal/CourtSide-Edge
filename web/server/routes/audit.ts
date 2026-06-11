import { Router } from 'express';
import { db } from '../db';
import { decision_audit } from '../schema';
import { desc, eq } from 'drizzle-orm';
import { writeLimiter, validateRequest } from '../middleware';
import { createAuditSchema } from '../schemas.validation';

const router = Router();

// ── Decision Audit Trail Endpoints ──────────────────────────────────────────
router.get('/audit/:trace_id', async (req, res) => {
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

router.get('/audit', async (req, res) => {
  try {
    const entries = await db.select().from(decision_audit)
      .orderBy(desc(decision_audit.timestamp))
      .limit(100);
    res.json(entries);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch audit entries' });
  }
});

router.post('/audit', writeLimiter, validateRequest(createAuditSchema), async (req, res) => {
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

export default router;
