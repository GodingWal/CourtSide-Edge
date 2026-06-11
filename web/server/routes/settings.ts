import { Router } from 'express';
import { db } from '../db';
import { settings } from '../schema';
import { eq } from 'drizzle-orm';
import { writeLimiter, validateRequest } from '../middleware';
import { updateSettingSchema } from '../schemas.validation';

const router = Router();

// ── Settings Endpoints ──────────────────────────────────────────────────────
router.get('/settings', async (req, res) => {
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

router.put('/settings', writeLimiter, validateRequest(updateSettingSchema), async (req, res) => {
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

export default router;
