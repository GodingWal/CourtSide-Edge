"""Data-driven projection core for Agent 3.

Implements the v3.1 ensemble layers on top of the real box-score history
that Agent 0 (Historical ETL) fills from ESPN:

  1. Bayesian minutes model — recent-form mean shrunk toward the player's
     season baseline (heavy shrinkage: 34-game WNBA season), with a
     rest-days adjustment (B2B penalty / extended-rest bump).
  2. Usage redistribution — when high-usage teammates are OUT (official
     injury report), a shrunk share of their usage transfers to the player.
  3. Pace adjustment — real market total vs. the league-average total.
  4. Per-stat distributions — Poisson for counting stats (AST/STL/BLK/3PM),
     Gamma-Poisson (negative-binomial) for REB when overdispersed, Normal
     for PTS; parameters from per-minute rates x the minutes distribution.
  5. Correlation matrix — league-wide game-level stat correlations from the
     same box scores, consumed by Agents 7 and 13 for parlay logic.

If a player has too little history to support a projection, run_projection
returns None — it never fabricates numbers. The optional as_of_date cuts
every query to games strictly before that date so the walk-forward backtest
(backtest.py) is free of lookahead bias.
"""
import logging
import os
from datetime import date

import numpy as np

from shared.base_agent import db_connect
from shared.db import db_available
from shared.prop_calibration import calibrate_projection, estimate_hit_rate, assess_line_quality, get_deflation_factor
from shared.context_scoring import calculate_context_score, get_context_multiplier
from shared.recency_bias import check_contrarian_signals, get_line_spike
from shared.team_bias import get_team_bias, get_team_direction, get_star_bias

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

# Stats whose volume scales directly with possession share. REB/STL/BLK are
# opportunity stats — they get half-weight in usage redistribution.
USAGE_DRIVEN_STATS = {"PTS", "AST", "3PM"}

MIN_GAMES = 3
RECENT_GAMES = 5
HISTORY_GAMES = 15
SEASON_GAMES = 40
N_SIMS = 5000

# Layer-1 shrinkage: recent form needs this many games to outweigh the season
# baseline (heavier than NBA practice — the 34-game season demands it).
MINUTES_SHRINK_K = 4.0
# B2B minutes penalty / 3+ days rest bump (documented WNBA effects; the
# direction is from the literature, the baseline minutes are the player's own).
B2B_MINUTES_FACTOR = 0.93
RESTED_MINUTES_FACTOR = 1.02
# Layer-2 redistribution: fraction of vacated usage credited to the player,
# shrunk below the naive 1/(1-U_out) scaling, and capped.
REDIST_ALPHA = 0.6
REDIST_CAP = 1.30


class EnsembleMathCore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _query(self, sql, params=()):
        if not db_available(self.db_path):
            return []
        conn = db_connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _date_filter(as_of_date):
        """SQL fragment + params cutting history to games before as_of_date."""
        if as_of_date:
            return " AND date < ? ", (str(as_of_date),)
        return "", ()

    def _player_history(self, player_name: str, stat_col: str, as_of_date=None, limit=HISTORY_GAMES):
        """Recent (date, minutes, stat) rows for a player, newest first."""
        cut_sql, cut_params = self._date_filter(as_of_date)
        rows = self._query(
            f"""SELECT date, minutes, {stat_col} FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?)
                  AND minutes IS NOT NULL AND {stat_col} IS NOT NULL {cut_sql}
                ORDER BY date DESC LIMIT ?""",
            (player_name, *cut_params, limit),
        )
        return [(d, float(m), float(v)) for d, m, v in rows if m and m > 0]

    def _season_minutes(self, player_name: str, as_of_date=None):
        cut_sql, cut_params = self._date_filter(as_of_date)
        rows = self._query(
            f"""SELECT minutes FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND minutes IS NOT NULL {cut_sql}
                ORDER BY date DESC LIMIT ?""",
            (player_name, *cut_params, SEASON_GAMES),
        )
        return np.array([float(m) for (m,) in rows if m and m > 0])

    def _player_team(self, player_name: str, as_of_date=None):
        cut_sql, cut_params = self._date_filter(as_of_date)
        rows = self._query(
            f"""SELECT team FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) {cut_sql}
                ORDER BY date DESC LIMIT 1""",
            (player_name, *cut_params),
        )
        return rows[0][0] if rows else None

    def _avg_usage(self, player_name: str, as_of_date=None, last_n=10):
        cut_sql, cut_params = self._date_filter(as_of_date)
        rows = self._query(
            f"""SELECT usage_rate FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND usage_rate IS NOT NULL {cut_sql}
                ORDER BY date DESC LIMIT ?""",
            (player_name, *cut_params, last_n),
        )
        vals = [float(u) for (u,) in rows if u is not None]
        return sum(vals) / len(vals) if vals else None

    def _rest_days(self, last_game_date: str, as_of_date=None):
        """Days between the player's most recent game and the projection date."""
        try:
            last = date.fromisoformat(str(last_game_date))
            today = date.fromisoformat(str(as_of_date)) if as_of_date else date.today()
            return max((today - last).days, 0)
        except (TypeError, ValueError):
            return None

    def _league_avg_total(self, as_of_date=None):
        """League-average combined game score from stored box scores."""
        cut_sql, cut_params = self._date_filter(as_of_date)
        rows = self._query(
            f"SELECT AVG(total) FROM (SELECT game_id, SUM(points) AS total "
            f"FROM player_box_scores WHERE 1=1 {cut_sql} GROUP BY game_id) AS game_totals",
            cut_params,
        )
        if rows and rows[0][0]:
            return float(rows[0][0])
        return None

    # ── Layer 2: usage redistribution ─────────────────────────────────────
    def usage_boost(self, player_name: str, out_players, as_of_date=None):
        """Rate multiplier when high-usage teammates are OUT.

        U_out = combined usage share (% of team possessions) of OUT teammates.
        The naive full redistribution multiplies remaining usage by
        1/(1 - U_out); we credit a shrunk fraction (REDIST_ALPHA) of that and
        cap the boost. Returns (multiplier, [teammates that drove it]).
        """
        if not out_players:
            return 1.0, []
        team = self._player_team(player_name, as_of_date)
        if not team:
            return 1.0, []
        out_usage = 0.0
        credited = []
        for name in out_players:
            if name.lower() == player_name.lower():
                continue
            if self._player_team(name, as_of_date) != team:
                continue
            usage = self._avg_usage(name, as_of_date)
            if usage:
                out_usage += usage
                credited.append(name)
        if not credited or out_usage <= 0:
            return 1.0, []
        u = min(out_usage / 100.0, 0.45)
        boost = min(1.0 + REDIST_ALPHA * (u / (1.0 - u)), REDIST_CAP)
        return round(boost, 4), credited

    # ── Layer 5: correlation matrix ───────────────────────────────────────
    def stat_correlations(self, min_games=8, max_players=150, as_of_date=None):
        """League-wide game-level correlations between stat pairs.

        Computed per player from their own game logs, then sample-weighted
        across players — captures e.g. PTS-AST and PTS-3PM co-movement that
        Agents 7/13 use to flag correlated parlay legs. Returns
        {"PTS|AST": corr, ...} for every stat pair with enough sample.
        """
        cut_sql, cut_params = self._date_filter(as_of_date)
        cols = ", ".join(STAT_COLUMNS.values())
        rows = self._query(
            f"""SELECT player_name, {cols} FROM player_box_scores
                WHERE minutes IS NOT NULL AND minutes > 0 {cut_sql}""",
            cut_params,
        )
        if not rows:
            return {}
        by_player: dict = {}
        for row in rows:
            by_player.setdefault(row[0], []).append(row[1:])

        stats = list(STAT_COLUMNS.keys())
        sums = {}
        weights = {}
        n_players = 0
        for player, games in by_player.items():
            if len(games) < min_games:
                continue
            n_players += 1
            if n_players > max_players:
                break
            arr = np.array(games, dtype=float)
            arr = np.nan_to_num(arr)
            for i in range(len(stats)):
                for j in range(i + 1, len(stats)):
                    a, b = arr[:, i], arr[:, j]
                    if a.std() == 0 or b.std() == 0:
                        continue
                    corr = float(np.corrcoef(a, b)[0, 1])
                    if np.isnan(corr):
                        continue
                    key = f"{stats[i]}|{stats[j]}"
                    sums[key] = sums.get(key, 0.0) + corr * len(games)
                    weights[key] = weights.get(key, 0) + len(games)
        return {k: round(sums[k] / weights[k], 3) for k in sums if weights[k] > 0}

    # ── Main entry point ──────────────────────────────────────────────────
    def run_projection(self, player_name: str, stat: str, game_context: dict,
                       as_of_date=None, n_sims: int = N_SIMS):
        """Project one stat for one player from their real game logs.

        game_context (all optional): market_total / over_under (pace input),
        line (enables p_over_line), out_players (official OUT list for usage
        redistribution). as_of_date restricts history for backtesting.

        Returns None when the player lacks history (no fabricated output).
        """
        stat_col = STAT_COLUMNS.get(stat)
        if stat_col is None:
            logger.info(f"Unsupported stat '{stat}' — skipping projection.")
            return None

        history = self._player_history(player_name, stat_col, as_of_date)
        if len(history) < MIN_GAMES:
            logger.info(
                f"Insufficient history for {player_name} ({len(history)} games < {MIN_GAMES}) — skipping."
            )
            return None

        dates = [d for d, _, _ in history]
        minutes = np.array([m for _, m, _ in history])
        values = np.array([v for _, _, v in history])
        per_min_rates = values / minutes

        rng = np.random.default_rng()

        # ── Layer 1: Bayesian minutes (recent form shrunk toward season) ──
        season_min = self._season_minutes(player_name, as_of_date)
        recent = minutes[:RECENT_GAMES]
        season_mean = float(season_min.mean()) if len(season_min) else float(minutes.mean())
        w = len(recent) / (len(recent) + MINUTES_SHRINK_K)
        minutes_mu = w * float(recent.mean()) + (1 - w) * season_mean
        minutes_sd = max(float(season_min.std(ddof=1)) if len(season_min) > 1 else 0.0, 2.0)

        rest_days = self._rest_days(dates[0], as_of_date)
        rest_factor = 1.0
        if rest_days is not None:
            if rest_days <= 1:
                rest_factor = B2B_MINUTES_FACTOR
            elif rest_days >= 3:
                rest_factor = RESTED_MINUTES_FACTOR
        minutes_mu *= rest_factor

        minutes_dist = np.clip(rng.normal(minutes_mu, minutes_sd, size=n_sims), 4, 40)

        # ── Layer 2: usage redistribution from official OUT teammates ─────
        boost, boosted_by = self.usage_boost(
            player_name, game_context.get("out_players") or [], as_of_date
        )
        stat_boost = boost if stat in USAGE_DRIVEN_STATS else 1.0 + (boost - 1.0) * 0.5

        # ── Layer 3: pace from the real market total vs. league average ───
        pace_factor = 1.0
        market_total = game_context.get("market_total") or game_context.get("over_under")
        league_avg = self._league_avg_total(as_of_date)
        if market_total and league_avg:
            pace_factor = float(np.clip(market_total / league_avg, 0.85, 1.15))

        # ── Layer 4: per-stat distribution ─────────────────────────────────
        rate_mu = float(per_min_rates.mean())
        lam = minutes_dist * rate_mu * pace_factor * stat_boost

        if stat == "PTS":
            # Normal with the player's own per-game dispersion.
            value_sd = max(float(values.std(ddof=1)) if len(values) > 1 else 0.0, 1.5)
            stat_dist = np.clip(rng.normal(lam, value_sd), 0, None)
        elif stat == "REB":
            # Gamma-Poisson (negative binomial) when the history is
            # overdispersed; plain Poisson otherwise.
            mean_v, var_v = float(values.mean()), float(values.var(ddof=1)) if len(values) > 1 else 0.0
            if var_v > mean_v > 0:
                shape = mean_v**2 / (var_v - mean_v)
                gamma_rates = rng.gamma(shape, lam / np.maximum(shape, 1e-6))
                stat_dist = rng.poisson(np.clip(gamma_rates, 0.01, None)).astype(float)
            else:
                stat_dist = rng.poisson(np.clip(lam, 0.01, None)).astype(float)
        else:
            # Counting stats: Poisson.
            stat_dist = rng.poisson(np.clip(lam, 0.01, None)).astype(float)

        raw_projected = float(stat_dist.mean())

        # ── Pattern Layer: calibration (deflation factors) ──────────────────
        calibrated = calibrate_projection(raw_projected, stat)
        calibration_delta = round(calibrated - raw_projected, 2)

        # ── Pattern Layer: context scoring (pace + rest + home) ─────────────
        ctx = calculate_context_score(
            opp_pace=game_context.get("opp_pace", 89.5),
            rest_days=rest_days if rest_days is not None else 1,
            is_home=game_context.get("is_home", True),
            role=game_context.get("role", "starter"),
            stat=stat,
        )
        context_adj = ctx["total_adjustment"]
        final_projected = calibrated + context_adj
        final_projected = max(0, round(final_projected, 2))

        # ── Pattern Layer: recency bias signals ─────────────────────────────
        prev_stats = game_context.get("prev_stats", {})
        recency_signals = check_contrarian_signals(
            prev_points=prev_stats.get("points"),
            prev_assists=prev_stats.get("assists"),
            prev_rebounds=prev_stats.get("rebounds"),
            prev_threes=prev_stats.get("threes"),
            consecutive_overs=game_context.get("consecutive_overs", 0),
            consecutive_unders=game_context.get("consecutive_unders", 0),
            is_b2b=(rest_days is not None and rest_days <= 1),
        )

        # ── Pattern Layer: team bias ────────────────────────────────────────
        team = self._player_team(player_name, as_of_date)
        team_bias = get_team_bias(team) if team else 0.0
        star_bias = get_star_bias(player_name)

        result = {
            "player": player_name,
            "stat": stat,
            "projected_minutes": round(float(minutes_dist.mean()), 2),
            "projected_value_raw": round(raw_projected, 2),
            "projected_value_calibrated": calibrated,
            "projected_value": final_projected,
            "calibration_delta": calibration_delta,
            "deflation_factor": get_deflation_factor(stat),
            "context_adjustment": context_adj,
            "context_breakdown": ctx["breakdown"],
            "context_risk_flags": ctx["risk_flags"],
            "recency_signals": [s["action"] for s in recency_signals[:3]],
            "team": team,
            "team_bias": round(team_bias, 1),
            "team_direction": get_team_direction(team) if team else "NEUTRAL",
            "star_bias": round(star_bias, 1),
            "confidence_interval_95": [
                round(float(np.percentile(stat_dist, 2.5)), 2),
                round(float(np.percentile(stat_dist, 97.5)), 2),
            ],
            "pace_factor": round(pace_factor, 3),
            "usage_boost": stat_boost if stat_boost == 1.0 else round(stat_boost, 4),
            "usage_boost_from": boosted_by,
            "rest_days": rest_days,
            "games_sampled": len(history),
        }

        # True over probability vs. the posted line (feeds CLV/Brier tracking).
        line = game_context.get("line")
        if line is not None:
            result["p_over_line_raw"] = round(float(np.mean(stat_dist > float(line))), 4)
            result["p_over_line"] = round(estimate_hit_rate(float(line), calibrated, stat), 4)
            result["line_quality"] = assess_line_quality(float(line), calibrated, stat)

        return result
