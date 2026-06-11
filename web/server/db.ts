import { drizzle as drizzleSqlite, BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { drizzle as drizzlePg, NodePgDatabase } from 'drizzle-orm/node-postgres';
import Database from 'better-sqlite3';
import { Pool } from 'pg';
import { SQL } from 'drizzle-orm';
import fs from 'fs';
import path from 'path';
import * as sqliteSchema from './schema';
import * as pgSchema from './schema.pg';
import { config } from './config';

// DATABASE_URL set → PostgreSQL (shared with the Python agent tier via the
// same env var). Unset → SQLite at DATABASE_PATH, exactly as before.
export const isPostgres = !!config.DATABASE_URL;

export let sqlite: InstanceType<typeof Database> | null = null;
export let pgPool: Pool | null = null;

let database: unknown;
if (isPostgres) {
  pgPool = new Pool({ connectionString: config.DATABASE_URL });
  database = drizzlePg(pgPool, { schema: pgSchema });
} else {
  // Ensure the database's parent directory exists before opening it.
  // better-sqlite3 does not create missing directories, so a fresh host (or a
  // test pointing at a not-yet-created path) would otherwise throw.
  if (config.DATABASE_PATH !== ':memory:') {
    fs.mkdirSync(path.dirname(path.resolve(config.DATABASE_PATH)), { recursive: true });
  }
  sqlite = new Database(config.DATABASE_PATH);
  // WAL mode for concurrent read/write safety across agents
  sqlite.pragma('journal_mode = WAL');
  // Wait up to 5 seconds before returning SQLITE_BUSY (prevents instant failures)
  sqlite.pragma('busy_timeout = 5000');
  // Enable foreign key enforcement
  sqlite.pragma('foreign_keys = ON');
  database = drizzleSqlite(sqlite, { schema: sqliteSchema });
}

// The drizzle query-builder API used by the routes (select/insert/update/
// transaction/query.findMany) is identical across both dialects, so the
// instance is exported under the SQLite driver's type and routes stay
// dialect-agnostic. Driver-specific calls (raw SQL, sync transactions) must
// branch on `isPostgres`.
export const db = database as BetterSQLite3Database<typeof sqliteSchema>;

// Routes import tables from here so each dialect's column definitions are the
// ones actually bound to queries. Both schemas are column-compatible.
const activeSchema = (isPostgres ? pgSchema : sqliteSchema) as typeof sqliteSchema;
export const {
  players,
  bankroll_history,
  bets,
  settings,
  qualitative_events,
  agent_context_store,
  decision_audit,
  hedging_opportunities,
} = activeSchema;

// Raw SQL across dialects: the sqlite driver exposes .all()/.run(), the pg
// driver .execute(). Rows come back as plain objects either way.
export async function rawQuery<T = Record<string, unknown>>(query: SQL): Promise<T[]> {
  if (isPostgres) {
    const result = await (database as NodePgDatabase).execute(query);
    return result.rows as T[];
  }
  return (database as BetterSQLite3Database<typeof sqliteSchema>).all<T>(query);
}

export async function rawRun(query: SQL): Promise<void> {
  if (isPostgres) {
    await (database as NodePgDatabase).execute(query);
  } else {
    (database as BetterSQLite3Database<typeof sqliteSchema>).run(query);
  }
}

export async function closeDb(): Promise<void> {
  if (sqlite) sqlite.close();
  if (pgPool) await pgPool.end();
}
