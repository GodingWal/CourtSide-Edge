import { drizzle } from 'drizzle-orm/better-sqlite3';
import Database from 'better-sqlite3';
import * as schema from './schema';
import { config } from './config';

// Connect to the SQLite database
const sqlite = new Database(config.DATABASE_PATH);
export const db = drizzle(sqlite, { schema });

