CREATE TABLE "agent_context_store" (
	"id" serial PRIMARY KEY NOT NULL,
	"game_id" text NOT NULL,
	"agent_id" text NOT NULL,
	"context_key" text NOT NULL,
	"context_value" text NOT NULL,
	"confidence" double precision NOT NULL,
	"ttl_seconds" integer DEFAULT 3600,
	"created_at" bigint NOT NULL
);
--> statement-breakpoint
CREATE TABLE "bankroll_history" (
	"id" serial PRIMARY KEY NOT NULL,
	"timestamp" bigint NOT NULL,
	"balance" double precision NOT NULL,
	"drawdown_pct" double precision NOT NULL
);
--> statement-breakpoint
CREATE TABLE "bets" (
	"id" serial PRIMARY KEY NOT NULL,
	"parent_id" integer,
	"is_parlay" integer,
	"player" text,
	"stat" text,
	"line" double precision,
	"over_under" text,
	"book_odds" integer NOT NULL,
	"true_odds" double precision,
	"edge_pct" double precision,
	"stake" double precision NOT NULL,
	"result" text,
	"actual_value" double precision,
	"profit_loss" double precision,
	"placed_at" bigint NOT NULL,
	"settled_at" bigint,
	"opposing_team" text,
	"notes" text,
	"closing_odds" integer,
	"clv_pct" double precision,
	"is_hedge" integer
);
--> statement-breakpoint
CREATE TABLE "decision_audit" (
	"id" serial PRIMARY KEY NOT NULL,
	"trace_id" text NOT NULL,
	"agent_id" text NOT NULL,
	"action" text NOT NULL,
	"reason" text,
	"input_payload" text,
	"output_payload" text,
	"confidence" double precision,
	"timestamp" bigint NOT NULL
);
--> statement-breakpoint
CREATE TABLE "hedging_opportunities" (
	"id" serial PRIMARY KEY NOT NULL,
	"bet_id" integer NOT NULL,
	"hedged_player" text NOT NULL,
	"original_line" double precision NOT NULL,
	"original_odds" integer NOT NULL,
	"live_line" double precision NOT NULL,
	"live_odds" integer NOT NULL,
	"potential_profit" double precision NOT NULL,
	"hedge_instructions" text NOT NULL,
	"created_at" bigint NOT NULL
);
--> statement-breakpoint
CREATE TABLE "players" (
	"id" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"team" text NOT NULL,
	"status" text
);
--> statement-breakpoint
CREATE TABLE "qualitative_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"channel" text NOT NULL,
	"payload" text NOT NULL,
	"timestamp" bigint NOT NULL
);
--> statement-breakpoint
CREATE TABLE "settings" (
	"key" text PRIMARY KEY NOT NULL,
	"value" text NOT NULL
);
--> statement-breakpoint
CREATE INDEX "idx_context_game_agent" ON "agent_context_store" USING btree ("game_id","agent_id");--> statement-breakpoint
CREATE INDEX "idx_bets_placed_at" ON "bets" USING btree ("placed_at");--> statement-breakpoint
CREATE INDEX "idx_bets_result" ON "bets" USING btree ("result");--> statement-breakpoint
CREATE INDEX "idx_bets_parent_id" ON "bets" USING btree ("parent_id");--> statement-breakpoint
CREATE INDEX "idx_bets_settled_at" ON "bets" USING btree ("settled_at");--> statement-breakpoint
CREATE INDEX "idx_bets_is_parlay" ON "bets" USING btree ("is_parlay");--> statement-breakpoint
CREATE INDEX "idx_audit_trace_id" ON "decision_audit" USING btree ("trace_id");--> statement-breakpoint
CREATE INDEX "idx_audit_timestamp" ON "decision_audit" USING btree ("timestamp");--> statement-breakpoint
CREATE INDEX "idx_events_timestamp" ON "qualitative_events" USING btree ("timestamp");