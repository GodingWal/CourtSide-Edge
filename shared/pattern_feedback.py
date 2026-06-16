"""
Pattern Feedback Loop v1.0
===========================
Self-correcting system that tracks pattern module accuracy and auto-adjusts.

Integrates into: Agent 15 (Drift Monitor), Agent 3 (Projection Engine)
"""
import json
import logging
import os
from datetime import datetime, timedelta

from shared.base_agent import db_connect, db_transaction
from shared.db import db_available

logger = logging.getLogger("PatternFeedback")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/hoopstats_wnba.db"))

DRIFT_THRESHOLD = 0.10  # 10% win rate drop
WARNING_THRESHOLD = 0.50
CRITICAL_THRESHOLD = 0.45
PLAYER_CALIB_MIN_GAMES = 10


def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pattern_pick_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pick_id TEXT, pattern_tags TEXT, player_name TEXT, stat TEXT,
        projected_value REAL, actual_value REAL, line REAL,
        result TEXT, profit REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pattern_drift_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_tag TEXT, alert_type TEXT,
        recent_win_rate REAL, baseline_win_rate REAL,
        suggested_action TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS player_deflation_factors (
        player_name TEXT PRIMARY KEY, factor REAL, stat TEXT,
        accuracy REAL, games_tracked INTEGER, last_updated TEXT)""")


def record_pick_result(pick_id: str, pattern_tags: list, player_name: str,
                       stat: str, projected_value: float, actual_value: float,
                       line: float, result: str):
    """Record the outcome of a pick for pattern analysis."""
    if not db_available(DB_PATH):
        return
    profit = 100.0 if result == "WIN" else -110.0 if result == "LOSS" else 0.0
    conn = db_connect(DB_PATH)
    try:
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO pattern_pick_results
               (pick_id, pattern_tags, player_name, stat, projected_value,
                actual_value, line, result, profit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pick_id, json.dumps(pattern_tags), player_name, stat,
             projected_value, actual_value, line, result, profit),
        )
        conn.commit()
    finally:
        conn.close()


def get_pattern_accuracy(pattern_tag: str, min_samples: int = 20) -> dict:
    """Return win rate and ROI for a specific pattern tag."""
    if not db_available(DB_PATH):
        return {"win_rate": 0.0, "roi": 0.0, "n": 0}
    conn = db_connect(DB_PATH)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT result, profit FROM pattern_pick_results
               WHERE pattern_tags LIKE ? AND result IN ('WIN', 'LOSS')
               ORDER BY created_at DESC LIMIT 200""",
            (f'%"{pattern_tag}"%',),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < min_samples:
        return {"win_rate": 0.0, "roi": 0.0, "n": len(rows)}

    wins = sum(1 for r, _ in rows if r == "WIN")
    total_profit = sum(p for _, p in rows)
    total_bet = len(rows) * 110
    return {
        "win_rate": round(wins / len(rows), 3),
        "roi": round(total_profit / total_bet, 4) if total_bet > 0 else 0,
        "n": len(rows),
    }


def detect_drift(pattern_tag: str, window: int = 20) -> dict:
    """Detect if a pattern's accuracy is declining."""
    if not db_available(DB_PATH):
        return {"is_drifting": False, "recent_wr": 0, "baseline_wr": 0}
    conn = db_connect(DB_PATH)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT result FROM pattern_pick_results
               WHERE pattern_tags LIKE ? AND result IN ('WIN', 'LOSS')
               ORDER BY created_at DESC LIMIT 70""",
            (f'%"{pattern_tag}"%',),
        ).fetchall()
    finally:
        conn.close()

    results = [r[0] for r in rows]
    if len(results) < window:
        return {"is_drifting": False, "recent_wr": 0, "baseline_wr": 0, "n": len(results)}

    recent = results[:window]
    baseline = results[window:window + 50]
    recent_wr = sum(1 for r in recent if r == "WIN") / len(recent)
    baseline_wr = sum(1 for r in baseline if r == "WIN") / len(baseline) if baseline else recent_wr

    return {
        "is_drifting": (baseline_wr - recent_wr) > DRIFT_THRESHOLD and len(baseline) >= 20,
        "recent_wr": round(recent_wr, 3),
        "baseline_wr": round(baseline_wr, 3),
        "drop": round(baseline_wr - recent_wr, 3),
        "n": len(results),
    }


def suggest_adjustment(pattern_tag: str) -> str:
    """Suggest calibration adjustment based on recent performance."""
    accuracy = get_pattern_accuracy(pattern_tag)
    drift = detect_drift(pattern_tag)

    if accuracy["n"] < 20:
        return "INSUFFICIENT_DATA"
    if accuracy["win_rate"] < CRITICAL_THRESHOLD and accuracy["n"] >= 30:
        return "CRITICAL_REDUCE_WEIGHT"
    if drift["is_drifting"]:
        return "REDUCE_WEIGHT"
    if accuracy["win_rate"] > 0.58:
        return "INCREASE_WEIGHT"
    return "MAINTAIN"


def get_module_health_report() -> dict:
    """Full health report for all pattern modules."""
    patterns = ["team_bias_under", "team_bias_over", "recency_fade", "contrarian",
                "calibration", "context_scoring", "b2b_adjustment"]
    report = {}
    for p in patterns:
        acc = get_pattern_accuracy(p, min_samples=10)
        drift_info = detect_drift(p)
        report[p] = {
            **acc,
            "drift": drift_info,
            "suggestion": suggest_adjustment(p),
        }
    return report


def run_player_auto_calibration():
    """Auto-adjust per-player deflation factors based on projection accuracy."""
    if not db_available(DB_PATH):
        return {}
    conn = db_connect(DB_PATH)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT player_name, stat, projected_value, actual_value, result
               FROM pattern_pick_results
               WHERE created_at > datetime('now', '-30 days')
               ORDER BY player_name, stat, created_at DESC""",
        ).fetchall()
    finally:
        conn.close()

    from collections import defaultdict
    by_player = defaultdict(list)
    for player_name, stat, proj, actual, result in rows:
        by_player[(player_name, stat)].append((proj, actual, result))

    adjustments = {}
    for (player, stat), picks in by_player.items():
        if len(picks) < PLAYER_CALIB_MIN_GAMES:
            continue
        bias_ratio = sum(a / p for p, a, _ in picks if p > 0) / len(picks)
        if abs(bias_ratio - 1.0) > 0.05:
            current = 0.93  # fallback
            conn = db_connect(DB_PATH)
            try:
                row = conn.execute(
                    "SELECT factor FROM player_deflation_factors WHERE player_name = ?",
                    (player,),
                ).fetchone()
                if row:
                    current = float(row[0])
            finally:
                conn.close()
            new_factor = max(0.50, min(1.50, current * (2 - bias_ratio)))
            adjustments[player] = round(new_factor, 3)
            conn = db_connect(DB_PATH)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO player_deflation_factors
                       (player_name, factor, stat, accuracy, games_tracked, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (player, new_factor, stat, round(bias_ratio, 3), len(picks),
                     datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

    return adjustments
