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
  parent_id: integer('parent_id'), // Reference to parent bet if this is a leg
  is_parlay: integer('is_parlay'), // 1 if this is a parlay container, 0 otherwise
  player: text('player'), // Nullable for parlay containers
  stat: text('stat'), // Nullable for parlay containers
  line: real('line'), // Nullable for parlay containers
  over_under: text('over_under'), // Nullable for parlay containers
  book_odds: integer('book_odds').notNull(), // e.g. -110 or +260
  true_odds: real('true_odds'),
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

export const qualitative_events = sqliteTable('qualitative_events', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  channel: text('channel').notNull(),
  payload: text('payload').notNull(), // JSON payload string
  timestamp: integer('timestamp').notNull(),
});

