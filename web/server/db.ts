import { drizzle } from 'drizzle-orm/better-sqlite3';
import Database from 'better-sqlite3';
import * as schema from './schema';
import path from 'path';

// Connect to the SQLite database
const dbPath = path.resolve(__dirname, '../../data/hoopstats_wnba.db');
const sqlite = new Database(dbPath);
export const db = drizzle(sqlite, { schema });
