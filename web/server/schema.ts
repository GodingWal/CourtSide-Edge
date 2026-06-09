import { sqliteTable, text, integer, real } from 'drizzle-orm/sqlite-core';

export const players = sqliteTable('players', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  team: text('team').notNull(),
  status: text('status'), // ACTIVE, INJURED
});

export const bankroll_history = sqliteTable('bankroll_history', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  timestamp: integer('timestamp').notNull(),
  balance: real('balance').notNull(),
  drawdown_pct: real('drawdown_pct').notNull(),
});
