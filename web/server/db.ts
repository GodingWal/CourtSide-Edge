import { drizzle } from 'drizzle-orm/better-sqlite3';
import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import * as schema from './schema';
import { config } from './config';

// Ensure the database's parent directory exists before opening it. better-sqlite3
// does not create missing directories, so a fresh host (or a test pointing at a
// not-yet-created path) would otherwise throw "directory does not exist".
if (config.DATABASE_PATH !== ':memory:') {
  fs.mkdirSync(path.dirname(path.resolve(config.DATABASE_PATH)), { recursive: true });
}

// Connect to the SQLite database
export const sqlite = new Database(config.DATABASE_PATH);

// Issue #3: Enable WAL mode for concurrent read/write safety across agents
sqlite.pragma('journal_mode = WAL');
// Wait up to 5 seconds before returning SQLITE_BUSY (prevents instant failures)
sqlite.pragma('busy_timeout = 5000');
// Enable foreign key enforcement
sqlite.pragma('foreign_keys = ON');

export const db = drizzle(sqlite, { schema });

