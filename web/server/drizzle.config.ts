import { defineConfig } from 'drizzle-kit';
import path from 'path';

export default defineConfig({
  schema: './schema.ts',
  out: './drizzle',
  dialect: 'sqlite',
  dbCredentials: {
    url: path.resolve(__dirname, '../../data/hoopstats_wnba.db'),
  },
});
