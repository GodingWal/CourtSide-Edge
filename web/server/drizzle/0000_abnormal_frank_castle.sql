CREATE TABLE `agent_context_store` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`game_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`context_key` text NOT NULL,
	`context_value` text NOT NULL,
	`confidence` real NOT NULL,
	`ttl_seconds` integer DEFAULT 3600,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `bankroll_history` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`timestamp` integer NOT NULL,
	`balance` real NOT NULL,
	`drawdown_pct` real NOT NULL
);
--> statement-breakpoint
CREATE TABLE `bets` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`parent_id` integer,
	`is_parlay` integer,
	`player` text,
	`stat` text,
	`line` real,
	`over_under` text,
	`book_odds` integer NOT NULL,
	`true_odds` real,
	`edge_pct` real,
	`stake` real NOT NULL,
	`result` text,
	`actual_value` real,
	`profit_loss` real,
	`placed_at` integer NOT NULL,
	`settled_at` integer,
	`opposing_team` text,
	`notes` text,
	`closing_odds` integer,
	`clv_pct` real,
	`is_hedge` integer
);
--> statement-breakpoint
CREATE TABLE `decision_audit` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`trace_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`action` text NOT NULL,
	`reason` text,
	`input_payload` text,
	`output_payload` text,
	`confidence` real,
	`timestamp` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `hedging_opportunities` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`bet_id` integer NOT NULL,
	`hedged_player` text NOT NULL,
	`original_line` real NOT NULL,
	`original_odds` integer NOT NULL,
	`live_line` real NOT NULL,
	`live_odds` integer NOT NULL,
	`potential_profit` real NOT NULL,
	`hedge_instructions` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `players` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`team` text NOT NULL,
	`status` text
);
--> statement-breakpoint
CREATE TABLE `qualitative_events` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`channel` text NOT NULL,
	`payload` text NOT NULL,
	`timestamp` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
