import { z } from 'zod';

export const legSchema = z.object({
  player: z.string().min(1, 'Player name is required'),
  stat: z.string().min(1, 'Stat category is required'),
  line: z.coerce.number().positive('Line must be a positive number'),
  over_under: z.enum(['OVER', 'UNDER']),
  book_odds: z.coerce.number().int('Odds must be an integer'),
  true_odds: z.coerce.number().optional().nullable(),
  edge_pct: z.coerce.number().optional().nullable(),
  opposing_team: z.string().optional().nullable(),
  notes: z.string().optional().nullable(),
});

export const straightBetSchema = z.object({
  is_parlay: z.union([z.literal(0), z.literal(false)]),
  parent_id: z.coerce.number().int().optional().nullable(),
  player: z.string().min(1, 'Player name is required'),
  stat: z.string().min(1, 'Stat category is required'),
  line: z.coerce.number().positive('Line must be a positive number'),
  over_under: z.enum(['OVER', 'UNDER']),
  book_odds: z.coerce.number().int('Odds must be an integer'),
  true_odds: z.coerce.number().optional().nullable(),
  edge_pct: z.coerce.number().optional().nullable(),
  stake: z.coerce.number().nonnegative('Stake must be greater than or equal to 0'),
  opposing_team: z.string().optional().nullable(),
  notes: z.string().optional().nullable(),
});

export const parlayBetSchema = z.object({
  is_parlay: z.union([z.literal(1), z.literal(true)]),
  book_odds: z.coerce.number().int('Odds must be an integer'),
  true_odds: z.coerce.number().optional().nullable(),
  edge_pct: z.coerce.number().optional().nullable(),
  stake: z.coerce.number().nonnegative('Stake must be greater than or equal to 0'),
  notes: z.string().optional().nullable(),
  legs: z.array(legSchema).min(1, 'Parlay must have at least one leg'),
});

export const createBetSchema = z.discriminatedUnion('is_parlay', [
  straightBetSchema.extend({ is_parlay: z.literal(0) }),
  straightBetSchema.extend({ is_parlay: z.literal(false) }),
  parlayBetSchema.extend({ is_parlay: z.literal(1) }),
  parlayBetSchema.extend({ is_parlay: z.literal(true) }),
]);

export const settleBetSchema = z.object({
  result: z.enum(['WIN', 'LOSS', 'PUSH']),
  actual_value: z.coerce.number().optional().nullable(),
});

export const clvBetSchema = z.object({
  closing_odds: z.coerce.number().int('Closing odds must be an integer'),
});

export const createContextSchema = z.object({
  game_id: z.string().min(1, 'Game ID is required'),
  agent_id: z.string().min(1, 'Agent ID is required'),
  context_key: z.string().min(1, 'Context key is required'),
  context_value: z.any(),
  confidence: z.coerce.number().min(0).max(1).default(0.8),
  ttl_seconds: z.coerce.number().int().positive().optional().nullable(),
});

export const createAuditSchema = z.object({
  // Agents fall back to non-UUID ids like 'unknown' for messages that
  // arrived without one; rejecting those would drop the audit entry.
  trace_id: z.string().min(1, 'Trace ID is required'),
  agent_id: z.string().min(1, 'Agent ID is required'),
  action: z.string().min(1, 'Action is required'),
  reason: z.string().optional().nullable(),
  input_payload: z.any().optional().nullable(),
  output_payload: z.any().optional().nullable(),
  confidence: z.coerce.number().min(0).max(1).optional().nullable(),
});

export const createHedgeSchema = z.object({
  bet_id: z.coerce.number().int().positive(),
  hedged_player: z.string().min(1, 'Hedged player is required'),
  original_line: z.coerce.number().positive(),
  original_odds: z.coerce.number().int(),
  live_line: z.coerce.number().positive(),
  live_odds: z.coerce.number().int(),
  potential_profit: z.coerce.number().nonnegative(),
  hedge_instructions: z.string().min(1, 'Hedge instructions are required'),
});

export const updateSettingSchema = z.object({
  key: z.string().min(1, 'Setting key is required'),
  // Scalars only: objects would be stored as "[object Object]".
  value: z.union([z.string(), z.number(), z.boolean()]),
});
