import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { db } from './db';
import path from 'path';

export function runMigrations(): void {
  try {
    console.log('⏳ Running database migrations...');
    migrate(db, { migrationsFolder: path.resolve(__dirname, './drizzle') });
    console.log('✅ Database migrations applied successfully.');
  } catch (error) {
    console.error('❌ Failed to run database migrations:', error);
    throw error;
  }
}

if (require.main === module) {
  runMigrations();
}
