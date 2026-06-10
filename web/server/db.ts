import { drizzle } from 'drizzle-orm/better-sqlite3';
import Database from 'better-sqlite3';
import * as schema from './schema';
import { config } from './config';

// Connect to the SQLite database
export const sqlite = new Database(config.DATABASE_PATH);

// Issue #3: Enable WAL mode for concurrent read/write safety across agents
sqlite.pragma('journal_mode = WAL');
// Wait up to 5 seconds before returning SQLITE_BUSY (prevents instant failures)
sqlite.pragma('busy_timeout = 5000');
// Enable foreign key enforcement
sqlite.pragma('foreign_keys = ON');

export const db = drizzle(sqlite, { schema });

