import pino from 'pino';
import { config } from './config';

// ── Structured Logger ───────────────────────────────────────────────────────
export const logger = pino({
  level: config.NODE_ENV === 'test' ? 'silent' : 'info',
  transport: config.NODE_ENV !== 'production' ? { target: 'pino-pretty', options: { colorize: true } } : undefined,
});
