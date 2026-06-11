import dotenv from 'dotenv';
import path from 'path';
import { z } from 'zod';

// Load environment variables from .env file
dotenv.config();

const configSchema = z.object({
  PORT: z.coerce.number().default(3000),
  REDIS_URL: z.string().default('redis://localhost:6379'),
  // When set (postgresql://user:pass@host:5432/db), the server uses
  // PostgreSQL and DATABASE_PATH is ignored. Empty string = unset, so an
  // uncommented-but-blank env line doesn't kill startup.
  DATABASE_URL: z.preprocess(
    (v) => (v === '' ? undefined : v),
    z.string().startsWith('postgres').optional()
  ),
  DATABASE_PATH: z.string().default(
    path.resolve(__dirname, '../../data/hoopstats_wnba.db')
  ),
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  API_KEY: z.string().optional(), // Required in production, optional in dev/test
  FRONTEND_URL: z.string().default('http://localhost:5173'),
  AGENT13_URL: z.string().default('http://localhost:8009'),
});

const parsed = configSchema.safeParse(process.env);

if (!parsed.success) {
  console.error('❌ Invalid environment configuration:');
  console.error(JSON.stringify(parsed.error.format(), null, 2));
  process.exit(1);
}

// Warn if running production without an API key
if (parsed.data.NODE_ENV === 'production' && !parsed.data.API_KEY) {
  console.error('❌ API_KEY is required in production mode.');
  process.exit(1);
}

export const config = parsed.data;
export type Config = z.infer<typeof configSchema>;

