"""
Usage Redistribution v1.0
==========================
When a star player sits, teammates get more usage.
Builds redistribution matrices from historical star-out games.

Integrates into: Agent 3 (Projection Engine)
"""
import logging
import os

from shared.base_agent import db_connect
from shared.db import db_available

logger = logging.getLogger("UsageRedistribution")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/hoopstats_wnba.db"))

SHRINKAGE = 0.6
BOOST_CAP = 0.50


def _get_team_players(team: str):
    if not db_available(DB_PATH):
        return []
    conn = db_connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT DISTINCT player_name FROM player_box_scores WHERE team = ?",
            (team,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _get_season_avg(player_name: str, stat_col: str):
    if not db_available(DB_PATH):
        return None
    conn = db_connect(DB_PATH)
    try:
        row = conn.execute(
            f"""SELECT AVG({stat_col}), COUNT(*) FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND minutes > 0
                  AND {stat_col} IS NOT NULL""",
            (player_name,),
        ).fetchone()
    finally:
        conn.close()
    if row and row[0] and row[1] >= 5:
        return float(row[0])
    return None


def _get_avg_when_star_out(player_name: str, out_player: str, stat_col: str):
    if not db_available(DB_PATH):
        return None
    conn = db_connect(DB_PATH)
    try:
        # Find games where out_player had 0 minutes
        rows = conn.execute(
            """SELECT DISTINCT p2.game_id FROM player_box_scores p1
               JOIN player_box_scores p2 ON p1.game_id = p2.game_id
               WHERE LOWER(p1.player_name) = LOWER(?) AND p1.minutes = 0
                 AND LOWER(p2.player_name) = LOWER(?) AND p2.minutes > 0
                 AND p2.{stat} IS NOT NULL""".replace("{stat}", stat_col),
            (out_player, player_name),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 3:
        return None

    game_ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(game_ids))
    conn = db_connect(DB_PATH)
    try:
        row = conn.execute(
            f"SELECT AVG({stat_col}) FROM player_box_scores
             WHERE LOWER(player_name) = LOWER(?) AND game_id IN ({placeholders})",
            (player_name, *game_ids),
        ).fetchone()
    finally:
        conn.close()

    return float(row[0]) if row and row[0] else None


def get_redistribution_matrix(out_player_name: str) -> dict:
    """Return {teammate: {stat: delta}} for when out_player sits."""
    if not db_available(DB_PATH):
        return {}

    conn = db_connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT DISTINCT team FROM player_box_scores WHERE LOWER(player_name) = LOWER(?)",
            (out_player_name,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {}
    team = row[0]
    teammates = [p for p in _get_team_players(team) if p.lower() != out_player_name.lower()]

    matrix = {}
    for teammate in teammates:
        deltas = {}
        for stat_code, stat_col in {"PTS": "points", "AST": "assists", "REB": "rebounds"}.items():
            season_avg = _get_season_avg(teammate, stat_col)
            out_avg = _get_avg_when_star_out(teammate, out_player_name, stat_col)
            if season_avg and out_avg and season_avg > 0:
                raw_delta = (out_avg - season_avg) / season_avg
                delta = max(-BOOST_CAP, min(BOOST_CAP, raw_delta * SHRINKAGE))
                deltas[stat_code] = round(delta, 3)
        if deltas:
            matrix[teammate] = deltas

    return matrix


def adjust_projection_for_star_out(player_name: str, out_players: list,
                                   base_projection: dict) -> dict:
    """Apply usage redistribution to a base projection."""
    if not out_players:
        return {**base_projection, "redistribution_applied": False}

    total_boost = 0.0
    breakdown = {}
    stat = base_projection.get("stat", "PTS")

    for out_player in out_players:
        matrix = get_redistribution_matrix(out_player)
        if player_name in matrix and stat in matrix[player_name]:
            boost = matrix[player_name][stat]
            total_boost += boost
            breakdown[out_player] = boost

    if total_boost == 0:
        return {**base_projection, "redistribution_applied": False}

    adjusted_value = base_projection.get("projected_value", 0) * (1 + total_boost)
    return {
        **base_projection,
        "projected_value": round(adjusted_value, 2),
        "projected_value_pre_redist": base_projection.get("projected_value"),
        "redistribution_applied": True,
        "redistribution_boost": round(total_boost, 3),
        "redistribution_detail": breakdown,
    }
