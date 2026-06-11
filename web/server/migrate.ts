import { migrate as migrateSqlite } from 'drizzle-orm/better-sqlite3/migrator';
import { migrate as migratePg } from 'drizzle-orm/node-postgres/migrator';
import type { NodePgDatabase } from 'drizzle-orm/node-postgres';
import { db, isPostgres } from './db';
import path from 'path';

export async function runMigrations(): Promise<void> {
  try {
    console.log('⏳ Running database migrations...');
    if (isPostgres) {
      await migratePg(db as unknown as NodePgDatabase, {
        migrationsFolder: path.resolve(__dirname, './drizzle-pg'),
      });
    } else {
      migrateSqlite(db, { migrationsFolder: path.resolve(__dirname, './drizzle') });
    }
    console.log('✅ Database migrations applied successfully.');
  } catch (error) {
    console.error('❌ Failed to run database migrations:', error);
    throw error;
  }
}

if (require.main === module) {
  runMigrations().catch(() => process.exit(1));
}
