import { Router } from 'express';
import { logger } from '../logger';
import { redisClient } from '../redis';

const router = Router();

// ── Stats Center endpoints ───────────────────────────────────────────────────
// Agent 0 aggregates real ESPN box-score history into Redis snapshots
// (stats:teams / stats:players / stats:games / stats:gamelogs). These routes
// surface whatever is genuinely there — empty payloads until the agent has
// published, never fabricated numbers.

async function readJsonKey(key: string): Promise<any | null> {
  if (!redisClient?.isOpen) return null;
  const raw = await redisClient.get(key);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

router.get('/stats/teams', async (req, res) => {
  try {
    const snapshot = await readJsonKey('stats:teams');
    res.json(snapshot ?? { updated: null, teams: {} });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch team stats');
    res.status(500).json({ error: 'Failed to fetch team stats' });
  }
});

router.get('/stats/players', async (req, res) => {
  try {
    const snapshot = await readJsonKey('stats:players');
    res.json(snapshot ?? { updated: null, players: [] });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch player stats');
    res.status(500).json({ error: 'Failed to fetch player stats' });
  }
});

// Head-to-head: every stored game between two teams (newest first).
router.get('/stats/h2h', async (req, res) => {
  try {
    const a = String(req.query.a ?? '').toUpperCase();
    const b = String(req.query.b ?? '').toUpperCase();
    if (!a || !b) return res.status(400).json({ error: 'query params a and b are required' });
    const snapshot = await readJsonKey('stats:games');
    const games = (snapshot?.games ?? []).filter(
      (g: any) => g.teams && g.teams[a] !== undefined && g.teams[b] !== undefined
    );
    res.json({ updated: snapshot?.updated ?? null, games });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch head-to-head games');
    res.status(500).json({ error: 'Failed to fetch head-to-head games' });
  }
});

// Walk-forward model validation summary (Agent 3 backtest, refreshed weekly).
router.get('/stats/model', async (req, res) => {
  try {
    const snapshot = await readJsonKey('stats:model_validation');
    res.json(snapshot ?? { updated: null, per_stat: {} });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch model validation summary');
    res.status(500).json({ error: 'Failed to fetch model validation summary' });
  }
});

// Full game log for one player (used for player-vs-team and matchup views).
router.get('/stats/gamelog', async (req, res) => {
  try {
    const player = String(req.query.player ?? '').trim();
    if (!player) return res.status(400).json({ error: 'query param player is required' });
    if (!redisClient?.isOpen) return res.json({ player, games: [] });
    const raw = await redisClient.hGet('stats:gamelogs', player);
    let games: unknown[] = [];
    if (raw) {
      try { games = JSON.parse(raw); } catch { games = []; }
    }
    res.json({ player, games });
  } catch (err) {
    logger.error({ err }, 'Failed to fetch player game log');
    res.status(500).json({ error: 'Failed to fetch player game log' });
  }
});

export default router;
