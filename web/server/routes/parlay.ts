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
    // Entry size (2-6 picks) chosen in the dashboard; Agent 13 validates too.
    const legs = Math.min(6, Math.max(2, parseInt(req.body?.legs, 10) || 2));
    // Generation includes an LLM rationale (Agent 13 caps it at ~20s); give
    // the proxy enough headroom that a slow local model doesn't read as
    // "Agent 13 offline" while the agent is mid-reply.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);
    const response = await fetch(`${config.AGENT13_URL}/api/parlay/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ legs }),
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
    logger.warn({ err, agent13: config.AGENT13_URL }, 'Agent 13 (parlay generator) unreachable.');
    res.status(503).json({
      error: `Parlay generator (Agent 13) is unreachable at ${config.AGENT13_URL}. ` +
        'Start the agent, or set AGENT13_URL if it runs on another host/container.'
    });
  }
});

export default router;
