import { Router } from 'express';
import { config } from '../config';
import { logger } from '../logger';
import { writeLimiter } from '../middleware';

const router = Router();

// ── Parlay Generation Endpoint ──────────────────────────────────────────────
// Proxies Agent 13, which builds entries exclusively from live cached props.
// When Agent 13 is unreachable we surface the failure — no fabricated parlay.
router.post('/parlay/generate', writeLimiter, async (req, res) => {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    const response = await fetch(`${config.AGENT13_URL}/api/parlay/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      // Pass through Agent 13's real refusal (e.g. outside pregame window, no live props)
      return res
        .status(response.status)
        .json({ error: data?.detail ?? `Agent 13 returned status ${response.status}` });
    }
    res.json(data);
  } catch (err) {
    logger.warn({ err }, 'Agent 13 (parlay generator) unreachable.');
    res.status(503).json({
      error: 'Parlay generator (Agent 13) is offline. Start the agent and try again.'
    });
  }
});

export default router;
