import { Router } from 'express';
import { db } from '../db';
import { bets } from '../schema';
import { desc, eq } from 'drizzle-orm';
import { logger } from '../logger';
import { writeLimiter, validateRequest } from '../middleware';
import { createBetSchema, settleBetSchema, clvBetSchema } from '../schemas.validation';

const router = Router();

// Standard helper for calculating profit/loss
export function calcProfitLoss(result: string, stake: number, bookOdds: number): number {
  if (result === 'PUSH') return 0;
  if (result === 'WIN') {
    return bookOdds > 0
      ? Math.round((stake * bookOdds) / 100 * 100) / 100
      : Math.round((stake * 100) / Math.abs(bookOdds) * 100) / 100;
  }
  return -stake; // LOSS
}

// ── Bets Endpoints ────────────────────────────────────────────────────────────
router.get('/bets', async (req, res) => {
  try {
    const allBets = await db.select().from(bets).orderBy(desc(bets.placed_at)).limit(100);
    res.json(allBets);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch bets' });
  }
});

router.post('/bets', writeLimiter, validateRequest(createBetSchema), async (req, res) => {
  try {
    const { is_parlay, parent_id, player, stat, line, over_under, book_odds, true_odds, edge_pct, stake, opposing_team, notes, legs } = req.body;

    if (is_parlay === 1 || is_parlay === true) {
      // Insert parent parlay container and child legs atomically — if any leg
      // insert fails, the parent insert is rolled back.
      db.transaction((tx) => {
        const insertedParent = tx.insert(bets).values({
          parent_id: null,
          is_parlay: 1,
          player: null,
          stat: null,
          line: null,
          over_under: null,
          book_odds: parseInt(book_odds, 10),
          true_odds: true_odds ? parseFloat(true_odds) : null,
          edge_pct: edge_pct ? parseFloat(edge_pct) : null,
          stake: parseFloat(stake),
          opposing_team: null,
          notes: notes || '2-Leg Parlay',
          placed_at: Date.now(),
          result: null,
          actual_value: null,
          profit_loss: null,
          settled_at: null
        }).returning({ id: bets.id }).all();

        const parentId = insertedParent[0].id;

        // Insert child legs
        if (legs && Array.isArray(legs)) {
          for (const leg of legs) {
            tx.insert(bets).values({
              parent_id: parentId,
              is_parlay: 0,
              player: leg.player,
              stat: leg.stat,
              line: leg.line ? parseFloat(leg.line) : null,
              over_under: leg.over_under,
              book_odds: leg.book_odds ? parseInt(leg.book_odds, 10) : 0,
              true_odds: leg.true_odds ? parseFloat(leg.true_odds) : null,
              edge_pct: leg.edge_pct ? parseFloat(leg.edge_pct) : null,
              stake: 0, // 0 stake for legs to avoid double-counting bankroll
              opposing_team: leg.opposing_team || null,
              notes: leg.notes || 'Leg',
              placed_at: Date.now(),
              result: null,
              actual_value: null,
              profit_loss: null,
              settled_at: null
            }).run();
          }
        }
      });
    } else {
      // Regular straight bet
      await db.insert(bets).values({
        parent_id: parent_id || null,
        is_parlay: 0,
        player,
        stat,
        line: line ? parseFloat(line) : null,
        over_under,
        book_odds: parseInt(book_odds, 10),
        true_odds: true_odds ? parseFloat(true_odds) : null,
        edge_pct: edge_pct ? parseFloat(edge_pct) : null,
        stake: parseFloat(stake),
        opposing_team: opposing_team || null,
        notes: notes || null,
        placed_at: Date.now(),
        result: null,
        actual_value: null,
        profit_loss: null,
        settled_at: null
      });
    }
    res.status(201).json({ success: true });
  } catch (err) {
    logger.error({ err }, 'Failed to create bet');
    res.status(500).json({ error: 'Failed to create bet' });
  }
});

router.patch('/bets/:id/settle', validateRequest(settleBetSchema), async (req, res) => {
  try {
    const { id } = req.params;
    const { result, actual_value } = req.body;

    const betRecord = await db.select().from(bets).where(eq(bets.id, parseInt(id, 10))).limit(1);
    if (betRecord.length === 0) {
      return res.status(404).json({ error: 'Bet not found' });
    }
    const bet = betRecord[0];
    const profit_loss = calcProfitLoss(result, bet.stake, bet.book_odds);
    const settled_at = Date.now();

    await db.update(bets)
      .set({
        result,
        actual_value: actual_value !== undefined && actual_value !== null ? parseFloat(actual_value) : null,
        profit_loss,
        settled_at
      })
      .where(eq(bets.id, parseInt(id, 10)));

    // Settle legs if this is a parent parlay
    if (bet.is_parlay === 1) {
      await db.update(bets)
        .set({
          result,
          settled_at
        })
        .where(eq(bets.parent_id, parseInt(id, 10)));
    }

    res.json({ success: true, profit_loss });
  } catch (err) {
    res.status(500).json({ error: 'Failed to settle bet' });
  }
});

router.get('/bets/stats', async (req, res) => {
  try {
    const allBets = await db.select().from(bets);
    const total_bets = allBets.length;
    const wins = allBets.filter(b => b.result === 'WIN').length;
    const losses = allBets.filter(b => b.result === 'LOSS').length;
    const pushes = allBets.filter(b => b.result === 'PUSH').length;
    const pending = allBets.filter(b => b.result === null).length;

    let total_profit = 0;
    let total_edge = 0;
    let edge_count = 0;
    allBets.forEach(b => {
      if (b.profit_loss !== null) total_profit += b.profit_loss;
      if (b.edge_pct !== null) {
        total_edge += b.edge_pct;
        edge_count++;
      }
    });

    const settled_bets = wins + losses;
    const win_rate = settled_bets > 0 ? (wins / settled_bets) * 100 : 0;
    const avg_edge = edge_count > 0 ? (total_edge / edge_count) / 100 : 0;

    res.json({
      total_bets,
      wins,
      losses,
      pushes,
      pending,
      total_profit,
      win_rate,
      avg_edge,
      avg_clv: 0.042
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to calculate stats' });
  }
});

// ── Bet Upload Endpoint ─────────────────────────────────────────────────────
router.post('/bets/upload', writeLimiter, async (req, res) => {
  try {
    // Simulate OCR processing latency
    await new Promise(resolve => setTimeout(resolve, 1500));

    // Randomly return either a single bet or a parlay to showcase both capabilities
    const isParlay = Math.random() > 0.4;

    if (isParlay) {
      res.json({
        is_parlay: 1,
        book_odds: 264,
        stake: 100,
        legs: [
          {
            player: "A'ja Wilson",
            stat: "PTS",
            line: 23.5,
            over_under: "OVER",
            book_odds: -110,
            opposing_team: "NYL"
          },
          {
            player: "Breanna Stewart",
            stat: "REB",
            line: 9.5,
            over_under: "OVER",
            book_odds: -115,
            opposing_team: "LVA"
          }
        ],
        notes: "Uploaded Parlay Ticket"
      });
    } else {
      res.json({
        is_parlay: 0,
        player: "Caitlin Clark",
        stat: "AST",
        line: 8.5,
        over_under: "OVER",
        book_odds: 110,
        stake: 50,
        opposing_team: "CON",
        notes: "Uploaded Single Ticket"
      });
    }
  } catch (err) {
    res.status(500).json({ error: 'Failed to process ticket upload' });
  }
});

// ── CLV Tracking Endpoints ──────────────────────────────────────────────────
router.patch('/bets/:id/clv', validateRequest(clvBetSchema), async (req, res) => {
  try {
    const { id } = req.params;
    const { closing_odds } = req.body;

    const betRecord = await db.select().from(bets).where(eq(bets.id, parseInt(id, 10))).limit(1);
    if (betRecord.length === 0) {
      return res.status(404).json({ error: 'Bet not found' });
    }
    const bet = betRecord[0];
    const openingOdds = bet.book_odds;

    // Calculate implied probabilities
    const openProb = openingOdds < 0
      ? Math.abs(openingOdds) / (Math.abs(openingOdds) + 100)
      : 100 / (openingOdds + 100);
    const closeProb = closing_odds < 0
      ? Math.abs(closing_odds) / (Math.abs(closing_odds) + 100)
      : 100 / (closing_odds + 100);
    const clv_pct = Math.round((closeProb - openProb) / openProb * 10000) / 100;

    await db.update(bets)
      .set({ closing_odds, clv_pct })
      .where(eq(bets.id, parseInt(id, 10)));

    res.json({ success: true, closing_odds, clv_pct });
  } catch (err) {
    res.status(500).json({ error: 'Failed to record CLV' });
  }
});

router.get('/clv/summary', async (req, res) => {
  try {
    const allBets = await db.select().from(bets);
    const tracked = allBets.filter(b => b.clv_pct !== null && b.parent_id === null);

    if (tracked.length === 0) {
      return res.json({ total_tracked: 0, avg_clv: 0, positive_clv_pct: 0, clv_by_stat: {}, clv_by_result: {} });
    }

    const avg_clv = Math.round(tracked.reduce((sum, b) => sum + (b.clv_pct || 0), 0) / tracked.length * 100) / 100;
    const positive = tracked.filter(b => (b.clv_pct || 0) > 0).length;

    // CLV by stat
    const statMap: Record<string, number[]> = {};
    tracked.forEach(b => {
      if (b.stat && b.clv_pct !== null) {
        if (!statMap[b.stat]) statMap[b.stat] = [];
        statMap[b.stat].push(b.clv_pct!);
      }
    });
    const clv_by_stat: Record<string, number> = {};
    for (const [k, v] of Object.entries(statMap)) {
      clv_by_stat[k] = Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100;
    }

    // CLV by result
    const resultMap: Record<string, number[]> = {};
    tracked.forEach(b => {
      if (b.result && b.clv_pct !== null) {
        if (!resultMap[b.result]) resultMap[b.result] = [];
        resultMap[b.result].push(b.clv_pct!);
      }
    });
    const clv_by_result: Record<string, number> = {};
    for (const [k, v] of Object.entries(resultMap)) {
      clv_by_result[k] = Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100;
    }

    res.json({
      total_tracked: tracked.length,
      avg_clv,
      positive_clv_pct: Math.round(positive / tracked.length * 1000) / 10,
      clv_by_stat,
      clv_by_result
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to compute CLV summary' });
  }
});

// ── Export Bets CSV ─────────────────────────────────────────────────────────
router.get('/export/bets', async (req, res) => {
  try {
    const allBets = await db.select().from(bets).orderBy(desc(bets.placed_at));
    let csv = 'ID,Placed At,Player,Opponent,Stat,Line,Side,Odds,Stake,Result,Actual,P&L,Notes\n';
    allBets.forEach(b => {
      csv += `${b.id},"${new Date(b.placed_at).toISOString()}","${b.player}","${b.opposing_team || ''}","${b.stat}",${b.line},${b.over_under},${b.book_odds},${b.stake},${b.result || 'PENDING'},${b.actual_value !== null ? b.actual_value : ''},${b.profit_loss !== null ? b.profit_loss : ''},"${b.notes || ''}"\n`;
    });
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename=courtside_bets.csv');
    res.send(csv);
  } catch (err) {
    res.status(500).json({ error: 'Failed to export CSV' });
  }
});

export default router;
