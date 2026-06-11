import { Router } from 'express';
import { db, settings } from '../db';
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
    // Atomic upsert: the old select-then-insert raced concurrent writers on
    // the primary key and 500ed on the conflict.
    await db.insert(settings)
      .values({ key, value: String(value) })
      .onConflictDoUpdate({ target: settings.key, set: { value: String(value) } });
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to update setting' });
  }
});

export default router;
