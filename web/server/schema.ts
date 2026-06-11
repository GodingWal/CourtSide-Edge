import { sqliteTable, text, integer, real, index } from 'drizzle-orm/sqlite-core';

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
  closing_odds: integer('closing_odds'), // Closing line odds at game time (Agent 14 CLV Tracker)
  clv_pct: real('clv_pct'), // Closing Line Value percentage (positive = sharp)
  is_hedge: integer('is_hedge'), // 1 if this bet is an automated hedge placed by Agent 20
}, (table) => ([
  index('idx_bets_placed_at').on(table.placed_at),
  index('idx_bets_result').on(table.result),
  index('idx_bets_parent_id').on(table.parent_id),
  index('idx_bets_settled_at').on(table.settled_at),
  index('idx_bets_is_parlay').on(table.is_parlay),
]));

export const settings = sqliteTable('settings', {
  key: text('key').primaryKey(),
  value: text('value').notNull(),
});

export const qualitative_events = sqliteTable('qualitative_events', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  channel: text('channel').notNull(),
  payload: text('payload').notNull(), // JSON payload string
  timestamp: integer('timestamp').notNull(),
}, (table) => ([
  index('idx_events_timestamp').on(table.timestamp),
]));

// ── Agent Context Store (Shared Memory Layer) ─────────────────────────────────
// Enables agents to read each other's enrichments instead of operating blind.
// Agent 3 can read Agent 5's referee profiles, Agent 9's fatigue scores, etc.
export const agent_context_store = sqliteTable('agent_context_store', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  game_id: text('game_id').notNull(),
  agent_id: text('agent_id').notNull(),
  context_key: text('context_key').notNull(), // e.g. 'referee_foul_bias', 'coach_fatigue_score'
  context_value: text('context_value').notNull(), // JSON payload
  confidence: real('confidence').notNull(), // 0.0 - 1.0
  ttl_seconds: integer('ttl_seconds').default(3600),
  created_at: integer('created_at').notNull(),
}, (table) => ([
  index('idx_context_game_agent').on(table.game_id, table.agent_id),
]));

// ── Decision Audit Trail ──────────────────────────────────────────────────────
// Logs every agent approve/reject/abstain decision for full traceability.
// trace_id links all decisions for a single edge through the pipeline.
export const decision_audit = sqliteTable('decision_audit', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  trace_id: text('trace_id').notNull(), // UUID linking all decisions for one edge
  agent_id: text('agent_id').notNull(),
  action: text('action').notNull(), // 'APPROVE', 'REJECT', 'ABSTAIN', 'SIZE', 'EXECUTE', 'HALT'
  reason: text('reason'),
  input_payload: text('input_payload'), // JSON: what the agent received
  output_payload: text('output_payload'), // JSON: what the agent emitted
  confidence: real('confidence'),
  timestamp: integer('timestamp').notNull(),
}, (table) => ([
  index('idx_audit_trace_id').on(table.trace_id),
  index('idx_audit_timestamp').on(table.timestamp),
]));

// ── Hedging Opportunities (Agent 16 Dynamic Hedging Oracle) ───────────────────
export const hedging_opportunities = sqliteTable('hedging_opportunities', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  bet_id: integer('bet_id').notNull(),
  hedged_player: text('hedged_player').notNull(),
  original_line: real('original_line').notNull(),
  original_odds: integer('original_odds').notNull(),
  live_line: real('live_line').notNull(),
  live_odds: integer('live_odds').notNull(),
  potential_profit: real('potential_profit').notNull(),
  hedge_instructions: text('hedge_instructions').notNull(),
  created_at: integer('created_at').notNull(),
});


