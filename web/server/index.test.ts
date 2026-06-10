import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import request from 'supertest';
import path from 'path';
import fs from 'fs';

// Set up environment variables before importing the app
const testDbPath = path.resolve(__dirname, '../../data/hoopstats_wnba_test.db');
process.env.DATABASE_PATH = testDbPath;
process.env.PORT = '0'; // Run on random port to avoid port collision
process.env.NODE_ENV = 'test';

// Import app after env is configured
import { app } from './index';

describe('CourtSideEdge API Integration Tests', () => {
  beforeAll(() => {
    // Ensure data directory exists
    const dataDir = path.dirname(testDbPath);
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }
  });

  afterAll(() => {
    // Clean up test database
    try {
      if (fs.existsSync(testDbPath)) {
        fs.unlinkSync(testDbPath);
      }
      // Also clean up any WAL/SHM files left by SQLite
      if (fs.existsSync(`${testDbPath}-wal`)) {
        fs.unlinkSync(`${testDbPath}-wal`);
      }
      if (fs.existsSync(`${testDbPath}-shm`)) {
        fs.unlinkSync(`${testDbPath}-shm`);
      }
    } catch (err) {
      console.warn('⚠️ Cleanup warning:', err);
    }
  });

  it('GET /api/bets returns a list of bets', async () => {
    const res = await request(app).get('/api/bets');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /api/players/active returns active players list', async () => {
    const res = await request(app).get('/api/players/active');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    expect(res.body.length).toBeGreaterThan(0);
    expect(res.body[0]).toHaveProperty('id');
    expect(res.body[0]).toHaveProperty('name');
  });

  it('POST /api/bets creates a straight bet successfully', async () => {
    const betPayload = {
      is_parlay: 0,
      player: "A'ja Wilson",
      stat: 'PTS',
      line: 23.5,
      over_under: 'OVER',
      book_odds: -110,
      true_odds: 0.58,
      edge_pct: 6.2,
      stake: 100,
      opposing_team: 'NYL',
      notes: 'Test straight bet'
    };

    const res = await request(app)
      .post('/api/bets')
      .send(betPayload);

    expect(res.status).toBe(201);
    expect(res.body).toEqual({ success: true });

    // Verify it exists in database
    const betsRes = await request(app).get('/api/bets');
    const created = betsRes.body.find((b: any) => b.player === "A'ja Wilson" && b.notes === 'Test straight bet');
    expect(created).toBeDefined();
    expect(created.stake).toBe(100);
    expect(created.book_odds).toBe(-110);
  });

  it('POST /api/bets fails validation with negative stake', async () => {
    const invalidPayload = {
      is_parlay: 0,
      player: "A'ja Wilson",
      stat: 'PTS',
      line: 23.5,
      over_under: 'OVER',
      book_odds: -110,
      stake: -50, // Invalid negative stake
      opposing_team: 'NYL'
    };

    const res = await request(app)
      .post('/api/bets')
      .send(invalidPayload);


    expect(res.status).toBe(400);
    expect(res.body).toHaveProperty('error', 'Validation Error');
    expect(res.body.details[0].path).toBe('stake');
  });

  it('POST /api/bets creates a parlay bet successfully', async () => {
    const parlayPayload = {
      is_parlay: 1,
      book_odds: 260,
      stake: 50,
      notes: 'Test Parlay',
      legs: [
        {
          player: 'Caitlin Clark',
          stat: 'AST',
          line: 8.5,
          over_under: 'OVER',
          book_odds: -110,
          opposing_team: 'CON'
        },
        {
          player: 'Angel Reese',
          stat: 'REB',
          line: 11.5,
          over_under: 'OVER',
          book_odds: -115,
          opposing_team: 'CHI'
        }
      ]
    };

    const res = await request(app)
      .post('/api/bets')
      .send(parlayPayload);

    expect(res.status).toBe(201);
    expect(res.body).toEqual({ success: true });
  });

  it('PATCH /api/bets/:id/settle settles a bet successfully', async () => {
    // 1. Create a bet to settle
    const betPayload = {
      is_parlay: 0,
      player: 'Kelsey Plum',
      stat: 'PTS',
      line: 18.5,
      over_under: 'OVER',
      book_odds: 100, // Even money (+100)
      stake: 100,
      opposing_team: 'SEA',
      notes: 'Plum test'
    };

    await request(app).post('/api/bets').send(betPayload);
    const betsRes = await request(app).get('/api/bets');
    const targetBet = betsRes.body.find((b: any) => b.player === 'Kelsey Plum');
    expect(targetBet).toBeDefined();

    // 2. Settle the bet
    const settlePayload = {
      result: 'WIN',
      actual_value: 20
    };

    const res = await request(app)
      .patch(`/api/bets/${targetBet.id}/settle`)
      .send(settlePayload);

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.profit_loss).toBe(100); // 100 stake * (+100 odds) = 100 profit
  });

  it('PATCH /api/bets/:id/settle fails validation with invalid result option', async () => {
    const res = await request(app)
      .patch('/api/bets/1/settle')
      .send({ result: 'WINNER' }); // Invalid option ('WINNER' vs 'WIN' | 'LOSS' | 'PUSH')


    expect(res.status).toBe(400);
    expect(res.body).toHaveProperty('error', 'Validation Error');
    expect(res.body.details[0].path).toBe('result');
  });

  it('POST /api/context writes and retrieves context successfully', async () => {
    const contextPayload = {
      game_id: 'TEST_GAME_100',
      agent_id: 'Agent_5',
      context_key: 'referee_foul_bias',
      context_value: { crew: 'Crew_Z', fouls_per_40: 34.2 },
      confidence: 0.9,
      ttl_seconds: 3600
    };

    const res = await request(app)
      .post('/api/context')
      .send(contextPayload);

    expect(res.status).toBe(201);
    expect(res.body).toEqual({ success: true });

    // Retrieve context
    const getRes = await request(app).get('/api/context/TEST_GAME_100');
    expect(getRes.status).toBe(200);
    expect(getRes.body.length).toBeGreaterThan(0);
    expect(getRes.body[0].agent_id).toBe('Agent_5');
  });

  it('POST /api/audit writes to decision audit trail', async () => {
    const auditPayload = {
      trace_id: '9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d',
      agent_id: 'Agent_11',
      action: 'APPROVE',
      reason: 'Validation check',
      confidence: 0.85
    };

    const res = await request(app)
      .post('/api/audit')
      .send(auditPayload);


    expect(res.status).toBe(201);
    expect(res.body).toEqual({ success: true });
  });

  it('PUT /api/settings updates a system setting', async () => {
    const res = await request(app)
      .put('/api/settings')
      .send({ key: 'test_setting_key', value: 'test_value' });

    expect(res.status).toBe(200);
    expect(res.body).toEqual({ success: true });
  });
});
