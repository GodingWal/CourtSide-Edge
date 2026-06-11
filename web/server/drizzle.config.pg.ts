import { defineConfig } from 'drizzle-kit';

// PostgreSQL variant: `npx drizzle-kit generate --config=drizzle.config.pg.ts`
export default defineConfig({
  schema: './schema.pg.ts',
  out: './drizzle-pg',
  dialect: 'postgresql',
  dbCredentials: {
    url: process.env.DATABASE_URL ?? 'postgresql://localhost:5432/courtside',
  },
});
