CREATE INDEX `idx_bets_settled_at` ON `bets` (`settled_at`);--> statement-breakpoint
CREATE INDEX `idx_bets_is_parlay` ON `bets` (`is_parlay`);