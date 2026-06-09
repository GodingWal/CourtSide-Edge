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

export const bets = sqliteTable('bets', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  player: text('player').notNull(),
  stat: text('stat').notNull(),
  line: real('line').notNull(),
  over_under: text('over_under').notNull(), // 'OVER' or 'UNDER'
  book_odds: integer('book_odds').notNull(), // e.g. -110
  true_odds: real('true_odds'), // our calculated probability
  edge_pct: real('edge_pct'),
  stake: real('stake').notNull(),
  result: text('result'), // 'WIN', 'LOSS', 'PUSH', null=pending
  actual_value: real('actual_value'),
  profit_loss: real('profit_loss'),
  placed_at: integer('placed_at').notNull(),
  settled_at: integer('settled_at'),
  opposing_team: text('opposing_team'),
  notes: text('notes'),
});

export const settings = sqliteTable('settings', {
  key: text('key').primaryKey(),
  value: text('value').notNull(),
});
