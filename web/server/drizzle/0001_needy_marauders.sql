CREATE INDEX `idx_context_game_agent` ON `agent_context_store` (`game_id`,`agent_id`);--> statement-breakpoint
CREATE INDEX `idx_bets_placed_at` ON `bets` (`placed_at`);--> statement-breakpoint
CREATE INDEX `idx_bets_result` ON `bets` (`result`);--> statement-breakpoint
CREATE INDEX `idx_bets_parent_id` ON `bets` (`parent_id`);--> statement-breakpoint
CREATE INDEX `idx_audit_trace_id` ON `decision_audit` (`trace_id`);--> statement-breakpoint
CREATE INDEX `idx_audit_timestamp` ON `decision_audit` (`timestamp`);--> statement-breakpoint
CREATE INDEX `idx_events_timestamp` ON `qualitative_events` (`timestamp`);