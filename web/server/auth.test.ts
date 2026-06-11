import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import request from 'supertest';
import path from 'path';
import fs from 'fs';

// Set up environment variables before importing the app.
// API_KEY is set, so Bearer auth must be enforced even though NODE_ENV=test.
const testDbPath = path.resolve(__dirname, '../../data/hoopstats_wnba_auth_test.db');
process.env.DATABASE_PATH = testDbPath;
process.env.PORT = '0';
process.env.NODE_ENV = 'test';
process.env.API_KEY = 'test-secret-key';

// Dynamic imports so the env assignments above take effect before config loads
// (static imports are hoisted above the assignments).
let app: typeof import('./index')['app'];
let verifyWsClient: typeof import('./ws')['verifyWsClient'];

describe('Auth enforcement when API_KEY is set (non-production)', () => {
  beforeAll(async () => {
    ({ app } = await import('./index'));
    ({ verifyWsClient } = await import('./ws'));
  });

  afterAll(() => {
    try {
      for (const f of [testDbPath, `${testDbPath}-wal`, `${testDbPath}-shm`]) {
        if (fs.existsSync(f)) fs.unlinkSync(f);
      }
    } catch (err) {
      console.warn('Cleanup warning:', err);
    }
  });

  it('GET /health stays unauthenticated', async () => {
    const res = await request(app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  it('GET /api/bets without a token returns 401', async () => {
    const res = await request(app).get('/api/bets');
    expect(res.status).toBe(401);
  });

  it('GET /api/bets with a wrong token returns 403', async () => {
    const res = await request(app)
      .get('/api/bets')
      .set('Authorization', 'Bearer wrong-key');
    expect(res.status).toBe(403);
  });

  it('GET /api/bets with the correct token returns 200', async () => {
    const res = await request(app)
      .get('/api/bets')
      .set('Authorization', 'Bearer test-secret-key');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('WebSocket upgrade is rejected without a valid token', () => {
    expect(verifyWsClient({ req: { headers: {}, url: '/' } as any })).toBe(false);
    expect(verifyWsClient({ req: { headers: { authorization: 'Bearer wrong' }, url: '/' } as any })).toBe(false);
    expect(verifyWsClient({ req: { headers: {}, url: '/?token=wrong' } as any })).toBe(false);
  });

  it('WebSocket upgrade is accepted with a valid token (header or query param)', () => {
    expect(verifyWsClient({ req: { headers: { authorization: 'Bearer test-secret-key' }, url: '/' } as any })).toBe(true);
    expect(verifyWsClient({ req: { headers: {}, url: '/?token=test-secret-key' } as any })).toBe(true);
  });
});
