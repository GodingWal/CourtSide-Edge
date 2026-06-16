/**
 * Patterns API Routes
 * ===================
 * Exposes WNBA prop pattern analysis endpoints for the dashboard.
 *
 * GET  /api/patterns/calibration           — Deflation factors & config
 * GET  /api/patterns/team-bias             — All team bias scores
 * GET  /api/patterns/contrarian-rules      — All contrarian rules with ROI
 * GET  /api/patterns/context-score         — Quick context multiplier
 * GET  /api/patterns/player/:name          — Player-specific signals
 */
import { Router } from 'express';
import * as calibration from '../../../shared/prop_calibration';
import * as teamBias from '../../../shared/team_bias';
import * as recencyBias from '../../../shared/recency_bias';
import * as contextScoring from '../../../shared/context_scoring';

const router = Router();

// ── Calibration Config ──────────────────────────────────────────────────────
router.get('/patterns/calibration', (_req, res) => {
  res.json({
    success: true,
    data: calibration.get_metadata(),
  });
});

// ── Team Bias ───────────────────────────────────────────────────────────────
router.get('/patterns/team-bias', (_req, res) => {
  const rankings = teamBias.get_all_team_rankings();
  res.json({
    success: true,
    data: {
      rankings: rankings.map(([team, bias]) => ({
        team,
        bias,
        direction: teamBias.get_team_direction(team),
        sweet_spot: teamBias.get_team_sweet_spot(team),
      })),
    },
  });
});

// ── Contrarian Rules ────────────────────────────────────────────────────────
router.get('/patterns/contrarian-rules', (_req, res) => {
  const rules = recencyBias.get_all_rules();
  res.json({
    success: true,
    data: rules,
  });
});

// ── Context Score ───────────────────────────────────────────────────────────
router.get('/patterns/context-score', (req, res) => {
  const opp_pace = parseFloat(req.query.opp_pace as string) || 89.5;
  const rest_days = parseInt(req.query.rest_days as string) || 1;
  const is_home = req.query.is_home !== 'false';
  const role = (req.query.role as string) || 'starter';
  const stat = (req.query.stat as string) || 'PTS';

  const score = contextScoring.calculate_context_score(
    opp_pace, rest_days, is_home, role, stat
  );
  const multiplier = contextScoring.get_context_multiplier(
    opp_pace, rest_days, is_home, role, stat
  );

  res.json({
    success: true,
    data: {
      ...score,
      multiplier: Math.round(multiplier * 1000) / 1000,
    },
  });
});

// ── Player-Specific Signals ─────────────────────────────────────────────────
router.get('/patterns/player/:name', (req, res) => {
  const playerName = decodeURIComponent(req.params.name);

  const starBias = teamBias.get_star_bias(playerName);
  const contrarian = recencyBias.check_contrarian_signals();

  res.json({
    success: true,
    data: {
      player: playerName,
      star_bias: starBias,
      is_inflated: starBias < -10,
      team_direction: null,
      contrarian_signals: contrarian,
    },
  });
});

// ── Health ──────────────────────────────────────────────────────────────────
router.get('/patterns/health', (_req, res) => {
  res.json({
    success: true,
    service: 'patterns-api',
    version: '1.0',
    endpoints: [
      'GET /api/patterns/calibration',
      'GET /api/patterns/team-bias',
      'GET /api/patterns/contrarian-rules',
      'GET /api/patterns/context-score',
      'GET /api/patterns/player/:name',
    ],
  });
});

export default router;
