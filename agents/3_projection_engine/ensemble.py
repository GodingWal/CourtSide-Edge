"""Data-driven projection core for Agent 3.

Every layer reads real history from the SQLite database that Agent 0
(Historical ETL) fills with ESPN box scores. If a player has too little
history to support a projection, run_projection returns None — it never
fabricates numbers.
"""
import logging
import os

import numpy as np

from shared.base_agent import db_connect

logger = logging.getLogger("EnsembleMathCore")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

# Odds API / dashboard stat codes -> player_box_scores columns
STAT_COLUMNS = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "3PM": "threes_made",
    "STL": "steals",
    "BLK": "blocks",
}

MIN_GAMES = 3
HISTORY_GAMES = 15
N_SIMS = 5000


class EnsembleMathCore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _query(self, sql, params=()):
        if not os.path.exists(self.db_path):
            return []
        conn = db_connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _player_history(self, player_name: str, stat_col: str):
        """Recent (minutes, stat) rows for a player, newest first."""
        rows = self._query(
            f"""SELECT minutes, {stat_col} FROM player_box_scores
                WHERE player_name = ? COLLATE NOCASE
                  AND minutes IS NOT NULL AND {stat_col} IS NOT NULL
                ORDER BY date DESC LIMIT ?""",
            (player_name, HISTORY_GAMES),
        )
        return [(float(m), float(v)) for m, v in rows if m and m > 0]

    def _league_avg_total(self):
        """League-average combined game score from stored box scores."""
        rows = self._query(
            "SELECT AVG(total) FROM (SELECT game_id, SUM(points) AS total "
            "FROM player_box_scores GROUP BY game_id)"
        )
        if rows and rows[0][0]:
            return float(rows[0][0])
        return None

    def run_projection(self, player_name: str, stat: str, game_context: dict):
        """Project one stat for one player from their real game logs.

        Layers:
          1. Minutes distribution — empirical mean/std of recent minutes.
          2. Production rate — empirical per-minute rates of the target stat.
          3. Pace adjustment — market total vs. league-average total (both real).
          4. Monte Carlo — sampled minutes × sampled per-minute rates.

        Returns None when the player lacks history (no fabricated output).
        """
        stat_col = STAT_COLUMNS.get(stat)
        if stat_col is None:
            logger.info(f"Unsupported stat '{stat}' — skipping projection.")
            return None

        history = self._player_history(player_name, stat_col)
        if len(history) < MIN_GAMES:
            logger.info(
                f"Insufficient history for {player_name} ({len(history)} games < {MIN_GAMES}) — skipping."
            )
            return None

        minutes = np.array([m for m, _ in history])
        per_min_rates = np.array([v / m for m, v in history])

        rng = np.random.default_rng()

        # Layer 1: minutes distribution from the player's own recent games.
        minutes_sd = max(float(minutes.std(ddof=1)) if len(minutes) > 1 else 0.0, 1.0)
        minutes_dist = np.clip(
            rng.normal(loc=float(minutes.mean()), scale=minutes_sd, size=N_SIMS), 0, 40
        )

        # Layer 2: per-minute production sampled from observed rates (bootstrap).
        rate_dist = rng.choice(per_min_rates, size=N_SIMS, replace=True)

        # Layer 3: pace adjustment from the real market total vs. real league average.
        pace_factor = 1.0
        market_total = game_context.get("market_total") or game_context.get("over_under")
        league_avg = self._league_avg_total()
        if market_total and league_avg:
            pace_factor = float(np.clip(market_total / league_avg, 0.85, 1.15))

        # Layer 4: Monte Carlo stat distribution.
        stat_dist = minutes_dist * rate_dist * pace_factor

        return {
            "player": player_name,
            "stat": stat,
            "projected_minutes": round(float(minutes_dist.mean()), 2),
            "projected_value": round(float(stat_dist.mean()), 2),
            "confidence_interval_95": [
                round(float(np.percentile(stat_dist, 2.5)), 2),
                round(float(np.percentile(stat_dist, 97.5)), 2),
            ],
            "pace_factor": round(pace_factor, 3),
            "games_sampled": len(history),
        }
