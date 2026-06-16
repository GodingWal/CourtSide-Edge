"""Agent 33: In-Game Prop Analyzer
Identifies live in-game prop betting opportunities based on current stats
vs prorated projections.
"""
import json
import logging
import os
from datetime import datetime

from fastapi import FastAPI
import uvicorn

from shared.base_agent import db_connect, setup_logging, run_polling_loop
from shared.db import db_available
from shared.redis_client import RedisPubSub, StreamProducer

logger = setup_logging("Agent33_InGamePropAnalyzer")

app = FastAPI(title="Agent 33: In-Game Prop Analyzer")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))
pubsub = None
stream = None

# In-memory cache of active opportunities
active_opportunities = []


def _ensure_table():
    if not db_available(DB_PATH):
        return
    conn = db_connect(DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS in_game_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, player_name TEXT, stat TEXT,
            quarter INTEGER, recommendation TEXT, confidence REAL,
            current_stat REAL, projected_final REAL, line REAL,
            result TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ig_game ON in_game_opportunities(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ig_player ON in_game_opportunities(player_name, stat)")
    finally:
        conn.close()


def _get_season_per_min(player_name: str, stat_col: str):
    if not db_available(DB_PATH):
        return 0.5
    conn = db_connect(DB_PATH)
    try:
        row = conn.execute(
            f"""SELECT SUM({stat_col}), SUM(minutes) FROM player_box_scores
                WHERE LOWER(player_name) = LOWER(?) AND minutes > 0
                  AND {stat_col} IS NOT NULL""",
            (player_name,),
        ).fetchone()
    finally:
        conn.close()
    if row and row[0] and row[1] and row[1] > 0:
        return float(row[0]) / float(row[1])
    return 0.5


def _analyze_player(player: dict, full_game_line: float, quarter: int,
                    time_remaining: str, score_diff: float) -> dict:
    """Analyze a single player for in-game prop edges."""
    name = player["name"]
    current_pts = player.get("pts", 0)
    current_ast = player.get("ast", 0)
    current_reb = player.get("reb", 0)
    mins_played = player.get("mins", 0)

    # Parse time remaining
    try:
        parts = time_remaining.split(":")
        minutes_left_in_q = int(parts[0]) + int(parts[1]) / 60
    except (ValueError, IndexError):
        minutes_left_in_q = 8.0

    quarters_left = max(0, 4 - quarter)
    remaining_minutes = minutes_left_in_q + quarters_left * 10

    if remaining_minutes <= 0 or mins_played <= 0:
        return None

    opportunities = []

    for stat, current, stat_col in [("PTS", current_pts, "points"),
                                     ("AST", current_ast, "assists"),
                                     ("REB", current_reb, "rebounds")]:
        per_min = _get_season_per_min(name, stat_col)
        if per_min <= 0:
            continue

        current_rate = current / mins_played if mins_played > 0 else 0
        deviation = (current_rate - per_min) / per_min if per_min > 0 else 0

        # Blowout adjustment
        is_starter = mins_played > 15
        blowout_factor = 1.0
        if score_diff > 15 and quarter >= 3:
            blowout_factor = 0.7 if is_starter else 1.4
        elif score_diff > 10 and quarter >= 4:
            blowout_factor = 0.6 if is_starter else 1.5

        projected = current + (remaining_minutes * per_min * blowout_factor)

        # Determine recommendation
        if deviation < -0.25 and blowout_factor >= 1.0:
            rec = "OVER"
            confidence = min(0.85, 0.55 + abs(deviation) * 1.5)
            reason = f"{abs(deviation)*100:.0f}% below per-minute pace. Positive regression expected."
        elif deviation > 0.30 and blowout_factor <= 1.0:
            rec = "UNDER"
            confidence = min(0.80, 0.55 + deviation * 1.5)
            reason = f"{deviation*100:.0f}% above per-minute pace. Negative regression likely."
        elif blowout_factor < 1.0 and is_starter:
            rec = "UNDER"
            confidence = 0.65
            reason = "Blowout risk — starter may sit late."
        elif blowout_factor > 1.0 and not is_starter:
            rec = "OVER"
            confidence = 0.60
            reason = "Blowout opportunity — bench gets extra minutes."
        else:
            continue

        sh_line = max(0.5, full_game_line - current) if stat == "PTS" else full_game_line * 0.4

        opportunities.append({
            "player": name,
            "stat": stat,
            "current": current,
            "full_game_line": full_game_line,
            "projected_final": round(projected, 1),
            "second_half_line": round(sh_line, 1),
            "recommendation": rec,
            "confidence": round(confidence, 3),
            "reason": reason,
        })

    return opportunities[0] if opportunities else None


@app.get("/health")
def health():
    return {"status": "healthy", "agent": 33}


@app.post("/api/in-game/analyze")
def analyze_in_game(data: dict):
    """Main analysis endpoint for live game state."""
    _ensure_table()
    game_id = data.get("game_id", "")
    quarter = data.get("quarter", 1)
    time_remaining = data.get("time_remaining", "10:00")
    score_diff = abs(data.get("score_diff", 0))
    players = data.get("players", [])
    lines = data.get("lines", {})

    opportunities = []
    for player in players:
        name = player.get("name", "")
        line = lines.get(name, {}).get("PTS", 15.5)
        opp = _analyze_player(player, line, quarter, time_remaining, score_diff)
        if opp:
            opportunities.append(opp)
            # Store in DB
            if db_available(DB_PATH):
                conn = db_connect(DB_PATH)
                try:
                    conn.execute(
                        """INSERT INTO in_game_opportunities
                           (game_id, player_name, stat, quarter, recommendation,
                            confidence, current_stat, projected_final, line)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (game_id, opp["player"], opp["stat"], quarter,
                         opp["recommendation"], opp["confidence"],
                         opp["current"], opp["projected_final"], opp["full_game_line"]),
                    )
                    conn.commit()
                finally:
                    conn.close()

    global active_opportunities
    active_opportunities = opportunities

    # Publish to Redis stream
    if stream:
        for opp in opportunities:
            stream.produce("stream_in_game_opportunities", {
                "game_id": game_id,
                **opp,
                "timestamp": datetime.now().isoformat(),
            })

    return {"opportunities": opportunities, "game_id": game_id, "quarter": quarter}


@app.get("/api/in-game/active")
def get_active():
    return {"opportunities": active_opportunities}


@app.get("/api/in-game/history")
def get_history(game_id: str = None, limit: int = 50):
    if not db_available(DB_PATH):
        return {"history": []}
    conn = db_connect(DB_PATH)
    try:
        if game_id:
            rows = conn.execute(
                "SELECT * FROM in_game_opportunities WHERE game_id = ? ORDER BY created_at DESC LIMIT ?",
                (game_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM in_game_opportunities ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return {"history": [dict(r) for r in rows]}


def main():
    global pubsub, stream
    pubsub = RedisPubSub()
    stream = StreamProducer()
    _ensure_table()
    uvicorn.run(app, host="0.0.0.0", port=8033)


if __name__ == "__main__":
    main()
