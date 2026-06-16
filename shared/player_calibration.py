"""
Player-Specific Calibration v1.0
=================================
Per-player deflation factors based on historical hit rate vs market lines.
Replaces league-wide averages with player-specific calibration.

Integrates into: Agent 3 (Projection Engine)
"""
import json
import logging
import os
from datetime import datetime

from shared.base_agent import db_connect, db_transaction
from shared.db import db_available

logger = logging.getLogger("PlayerCalibration")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/hoopstats_wnba.db"))

# Seeded calibrations from 10,774 matched prop bets
SEEDED_CALIBRATIONS = {
    "Dearica Hamby": {"PTS": 0.73, "AST": 0.80, "REB": 0.85},
    "A'ja Wilson": {"PTS": 1.05, "AST": 0.95, "REB": 1.02},
    "Caitlin Clark": {"PTS": 0.98, "AST": 0.92, "REB": 0.90},
    "Angel Reese": {"PTS": 0.88, "AST": 0.75, "REB": 0.95},
    "Napheesa Collier": {"PTS": 0.90, "AST": 0.85, "REB": 0.92},
    "Kelsey Mitchell": {"PTS": 0.92, "AST": 0.88, "REB": 0.90},
}

LEAGUE_FALLBACK = {"PTS": 0.93, "AST": 0.82, "REB": 0.88, "3PM": 0.90, "STL": 0.85, "BLK": 0.87}

STAT_COL_MAP = {"PTS": "points", "AST": "assists", "REB": "rebounds", "3PM": "threes_made"}


def _ensure_cache_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS player_calibration_cache (
        player_name TEXT, stat TEXT, factor REAL, games_used INTEGER,
        computed_at TEXT, PRIMARY KEY (player_name, stat))""")


def _compute_factor(player_name: str, stat: str) -> tuple:
    """Compute deflation factor from actual mean vs 10-game rolling mean."""
    stat_col = STAT_COL_MAP.get(stat, stat.lower())
    if not db_available(DB_PATH):
        return None, 0
    conn = db_connect(DB_PATH)
    try:
        rows = conn.execute(
            f"""SELECT date, {stat_col} FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND minutes > 0
                  AND {stat_col} IS NOT NULL
                ORDER BY date DESC LIMIT 100""", (player_name,)
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 10:
        return None, len(rows)

    values = [float(v) for _, v in rows if v is not None]
    if not values:
        return None, 0

    actual_mean = sum(values) / len(values)
    recent_10 = values[:10]
    rolling_mean = sum(recent_10) / len(recent_10)

    if rolling_mean <= 0:
        return None, len(rows)

    factor = max(0.50, min(1.50, actual_mean / rolling_mean))
    return round(factor, 3), len(rows)


def refresh_cache(player_name: str = None):
    """Recompute and cache deflation factors. If player_name given, only that player."""
    if not db_available(DB_PATH):
        return
    conn = db_connect(DB_PATH)
    try:
        _ensure_cache_table(conn)
        if player_name:
            players = [(player_name,)]
        else:
            players = conn.execute(
                "SELECT DISTINCT player_name FROM player_box_scores WHERE minutes > 0"
            ).fetchall()

        for (name,) in players:
            for stat in STAT_COL_MAP:
                factor, games = _compute_factor(name, stat)
                if factor is not None:
                    conn.execute(
                        """INSERT OR REPLACE INTO player_calibration_cache
                           (player_name, stat, factor, games_used, computed_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (name, stat, factor, games, datetime.now().isoformat()),
                    )
        conn.commit()
    finally:
        conn.close()


def get_player_deflation(player_name: str, stat: str) -> float:
    """Return player-specific deflation factor with 4-tier fallback."""
    # Tier 1: seeded calibrations
    if player_name in SEEDED_CALIBRATIONS and stat in SEEDED_CALIBRATIONS[player_name]:
        return SEEDED_CALIBRATIONS[player_name][stat]

    # Tier 2: cached computed factor
    if db_available(DB_PATH):
        conn = db_connect(DB_PATH)
        try:
            _ensure_cache_table(conn)
            row = conn.execute(
                "SELECT factor FROM player_calibration_cache WHERE LOWER(player_name) = LOWER(?) AND stat = ?",
                (player_name, stat),
            ).fetchone()
            if row:
                return float(row[0])
        finally:
            conn.close()

    # Tier 3: fresh computation
    factor, games = _compute_factor(player_name, stat)
    if factor is not None and games >= 10:
        return factor

    # Tier 4: league fallback
    return LEAGUE_FALLBACK.get(stat, 0.90)


def get_all_calibrations() -> dict:
    """Return all cached calibrations."""
    if not db_available(DB_PATH):
        return {}
    conn = db_connect(DB_PATH)
    try:
        _ensure_cache_table(conn)
        rows = conn.execute("SELECT player_name, stat, factor FROM player_calibration_cache").fetchall()
    finally:
        conn.close()

    result = {}
    for name, stat, factor in rows:
        result.setdefault(name, {})[stat] = float(factor)
    return result


def detect_drift(player_name: str, stat: str, window: int = 20) -> dict:
    """Detect if player's calibration is shifting."""
    stat_col = STAT_COL_MAP.get(stat, stat.lower())
    if not db_available(DB_PATH):
        return {"drift_ratio": 1.0, "is_drifting": False, "direction": "UNKNOWN"}

    conn = db_connect(DB_PATH)
    try:
        rows = conn.execute(
            f"""SELECT {stat_col} FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND minutes > 0
                  AND {stat_col} IS NOT NULL
                ORDER BY date DESC LIMIT ?""",
            (player_name, window + 15),
        ).fetchall()
    finally:
        conn.close()

    values = [float(v) for (v,) in rows if v is not None]
    if len(values) < window:
        return {"drift_ratio": 1.0, "is_drifting": False, "direction": "INSUFFICIENT_DATA"}

    recent = values[:5]
    previous = values[5:window]
    if not previous:
        return {"drift_ratio": 1.0, "is_drifting": False, "direction": "STABLE"}

    recent_mean = sum(recent) / len(recent)
    prev_mean = sum(previous) / len(previous)
    if prev_mean <= 0:
        return {"drift_ratio": 1.0, "is_drifting": False, "direction": "STABLE"}

    drift_ratio = recent_mean / prev_mean
    is_drifting = abs(drift_ratio - 1.0) > 0.10
    direction = "UP" if drift_ratio > 1.0 else "DOWN" if drift_ratio < 1.0 else "STABLE"

    return {
        "drift_ratio": round(drift_ratio, 3),
        "is_drifting": is_drifting,
        "direction": direction,
        "recent_mean": round(recent_mean, 2),
        "previous_mean": round(prev_mean, 2),
    }
