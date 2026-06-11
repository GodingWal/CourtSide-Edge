// PostgreSQL mirror of schema.ts (used when DATABASE_URL is set).
//
// Kept value-compatible with the SQLite schema so the Python agents' SQL works
// unchanged on both dialects:
// - millisecond-epoch timestamps are bigint (they overflow int4),
// - boolean-ish flags (is_parlay, is_hedge) stay integers (agents compare = 1),
// - floating point columns are double precision (SQLite REAL is 8-byte).
import { pgTable, text, integer, bigint, doublePrecision, serial, index } from 'drizzle-orm/pg-core';

const epochMs = (name: string) => bigint(name, { mode: 'number' });

export const players = pgTable('players', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  team: text('team').notNull(),
  status: text('status'), // ACTIVE, INJURED
});

export const bankroll_history = pgTable('bankroll_history', {
  id: serial('id').primaryKey(),
  timestamp: epochMs('timestamp').notNull(),
  balance: doublePrecision('balance').notNull(),
  drawdown_pct: doublePrecision('drawdown_pct').notNull(),
});

export const bets = pgTable('bets', {
  id: serial('id').primaryKey(),
  parent_id: integer('parent_id'), // Reference to parent bet if this is a leg
  is_parlay: integer('is_parlay'), // 1 if this is a parlay container, 0 otherwise
  player: text('player'), // Nullable for parlay containers
  stat: text('stat'), // Nullable for parlay containers
  line: doublePrecision('line'), // Nullable for parlay containers
  over_under: text('over_under'), // Nullable for parlay containers
  book_odds: integer('book_odds').notNull(), // e.g. -110 or +260
  true_odds: doublePrecision('true_odds'),
  edge_pct: doublePrecision('edge_pct'),
  stake: doublePrecision('stake').notNull(),
  result: text('result'), // 'WIN', 'LOSS', 'PUSH', null=pending
  actual_value: doublePrecision('actual_value'),
  profit_loss: doublePrecision('profit_loss'),
  placed_at: epochMs('placed_at').notNull(),
  settled_at: epochMs('settled_at'),
  opposing_team: text('opposing_team'),
  notes: text('notes'),
  closing_odds: integer('closing_odds'), // Closing line odds at game time (Agent 14 CLV Tracker)
  clv_pct: doublePrecision('clv_pct'), // Closing Line Value percentage (positive = sharp)
  is_hedge: integer('is_hedge'), // 1 if this bet is an automated hedge placed by Agent 20
}, (table) => ([
  index('idx_bets_placed_at').on(table.placed_at),
  index('idx_bets_result').on(table.result),
  index('idx_bets_parent_id').on(table.parent_id),
  index('idx_bets_settled_at').on(table.settled_at),
  index('idx_bets_is_parlay').on(table.is_parlay),
]));

export const settings = pgTable('settings', {
  key: text('key').primaryKey(),
  value: text('value').notNull(),
});

export const qualitative_events = pgTable('qualitative_events', {
  id: serial('id').primaryKey(),
  channel: text('channel').notNull(),
  payload: text('payload').notNull(), // JSON payload string
  timestamp: epochMs('timestamp').notNull(),
}, (table) => ([
  index('idx_events_timestamp').on(table.timestamp),
]));

export const agent_context_store = pgTable('agent_context_store', {
  id: serial('id').primaryKey(),
  game_id: text('game_id').notNull(),
  agent_id: text('agent_id').notNull(),
  context_key: text('context_key').notNull(),
  context_value: text('context_value').notNull(), // JSON payload
  confidence: doublePrecision('confidence').notNull(), // 0.0 - 1.0
  ttl_seconds: integer('ttl_seconds').default(3600),
  created_at: epochMs('created_at').notNull(),
}, (table) => ([
  index('idx_context_game_agent').on(table.game_id, table.agent_id),
]));

export const decision_audit = pgTable('decision_audit', {
  id: serial('id').primaryKey(),
  trace_id: text('trace_id').notNull(), // UUID linking all decisions for one edge
  agent_id: text('agent_id').notNull(),
  action: text('action').notNull(), // 'APPROVE', 'REJECT', 'ABSTAIN', 'SIZE', 'EXECUTE', 'HALT'
  reason: text('reason'),
  input_payload: text('input_payload'), // JSON: what the agent received
  output_payload: text('output_payload'), // JSON: what the agent emitted
  confidence: doublePrecision('confidence'),
  timestamp: epochMs('timestamp').notNull(),
}, (table) => ([
  index('idx_audit_trace_id').on(table.trace_id),
  index('idx_audit_timestamp').on(table.timestamp),
]));

export const hedging_opportunities = pgTable('hedging_opportunities', {
  id: serial('id').primaryKey(),
  bet_id: integer('bet_id').notNull(),
  hedged_player: text('hedged_player').notNull(),
  original_line: doublePrecision('original_line').notNull(),
  original_odds: integer('original_odds').notNull(),
  live_line: doublePrecision('live_line').notNull(),
  live_odds: integer('live_odds').notNull(),
  potential_profit: doublePrecision('potential_profit').notNull(),
  hedge_instructions: text('hedge_instructions').notNull(),
  created_at: epochMs('created_at').notNull(),
});
