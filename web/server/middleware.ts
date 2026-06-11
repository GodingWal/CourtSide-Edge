import express from 'express';
import rateLimit from 'express-rate-limit';
import { createHash, timingSafeEqual } from 'crypto';
import { ZodSchema } from 'zod';
import { config } from './config';

// Constant-time token comparison (hashing first equalizes lengths).
export const safeTokenEqual = (a: string, b: string): boolean => {
  const ha = createHash('sha256').update(a).digest();
  const hb = createHash('sha256').update(b).digest();
  return timingSafeEqual(ha, hb);
};

// ── Auth Middleware ─────────────────────────────────────────────────────────
// Bearer-token auth is enforced whenever API_KEY is set, regardless of NODE_ENV.
export const authMiddleware = (req: express.Request, res: express.Response, next: express.NextFunction) => {
  if (!config.API_KEY) {
    return next();
  }
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: Missing Bearer token' });
  }
  const token = authHeader.slice(7);
  if (!safeTokenEqual(token, config.API_KEY)) {
    return res.status(403).json({ error: 'Forbidden: Invalid API key' });
  }
  next();
};

// ── Rate Limiting ───────────────────────────────────────────────────────────
export const writeLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 100, // 100 requests per minute per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later.' },
  skip: () => config.NODE_ENV === 'test', // Skip in test mode
});

export const validateRequest = (schema: ZodSchema) => {
  return (req: express.Request, res: express.Response, next: express.NextFunction) => {
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({
        error: 'Validation Error',
        details: parsed.error.issues.map(err => ({
          path: err.path.join('.'),
          message: err.message
        }))
      });
    }
    req.body = parsed.data;
    next();
  };
};
