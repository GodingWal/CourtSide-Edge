import threading
import time

import uvicorn
from fastapi import FastAPI

from shared.base_agent import setup_logging
from shared.context_client import ContextClient
from shared.espn_client import get_boxscore_fouls, get_scoreboard
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent21_RotationTracker")

app = FastAPI(title="Agent 21: Live Rotation & Foul Trouble Tracker")
context_client = ContextClient()

# Latest real rotation snapshot (from live game boxscores).
ROTATIONS = []
_rotations_lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/rotations")
def get_rotations():
    with _rotations_lock:
        return list(ROTATIONS)


def foul_status(fouls: int, period) -> str:
    """Classify foul trouble relative to game progress (WNBA: 6 to foul out)."""
    p = period or 1
    if fouls >= 5:
        return "SEVERE_FOUL_TROUBLE"
    if (p <= 2 and fouls >= 3) or (p == 3 and fouls >= 4):
        return "FOUL_TROUBLE"
    return "NORMAL"


def evaluate_live_games(pubsub: RedisPubSub):
    """Pull real boxscores for live games and publish foul-trouble adjustments."""
    games = [g for g in get_scoreboard() if g["state"] == "LIVE"]
    if not games:
        logger.info("No live WNBA games right now.")
        with _rotations_lock:
            ROTATIONS.clear()
        return

    snapshot = []
    for game in games:
        if not game.get("espn_id"):
            continue  # no event id — boxscore fetch can only fail
        fouls = get_boxscore_fouls(game["espn_id"])
        for row in fouls:
            status = foul_status(row["fouls"], game.get("period"))
            entry = {
                "player": row["player"],
                "team": row["team"],
                "fouls": row["fouls"],
                "period": f"Q{game.get('period')}" if game.get("period") else "—",
                "adjustment": "-4.5 min" if status == "FOUL_TROUBLE" else ("-6.0 min" if status == "SEVERE_FOUL_TROUBLE" else "0.0 min"),
                "status": status,
                "game_id": game["game_id"],
                "timestamp": time.time(),
            }
            snapshot.append(entry)

            if status in ("FOUL_TROUBLE", "SEVERE_FOUL_TROUBLE"):
                mins_deduction = -4.5 if status == "FOUL_TROUBLE" else -6.0
                adjustment_context = {
                    "player": row["player"],
                    "minutes_adjustment": mins_deduction,
                    "PTS": round(mins_deduction * 0.65, 2),
                    "REB": round(mins_deduction * 0.25, 2),
                    "AST": round(mins_deduction * 0.15, 2),
                }
                context_client.write_context(
                    game_id=game["game_id"],
                    agent_id="Agent_21",
                    context_key="live_minutes_adjustment",
                    context_value=adjustment_context,
                    confidence=0.88,
                    ttl_seconds=3600,
                )
                logger.info(f"Foul trouble: {row['player']} ({row['fouls']} fouls, {game['game_id']}) → {adjustment_context}")

    with _rotations_lock:
        ROTATIONS.clear()
        ROTATIONS.extend(snapshot)

    # Surface foul-trouble rows for the dashboard (web API reads recent:rotations).
    try:
        flagged = [e for e in snapshot if e["status"] != "NORMAL"]
        for e in flagged[:20]:
            pubsub.push_recent("recent:rotations", e, cap=50)
    except Exception as e:
        logger.warning(f"Failed to push rotations to Redis: {e}")


def rotation_monitor_loop():
    time.sleep(8)
    pubsub = RedisPubSub()
    while True:
        try:
            evaluate_live_games(pubsub)
        except Exception as e:
            logger.error(f"Error in rotation monitor: {e}")
        time.sleep(60)


if __name__ == "__main__":
    logger.info("Starting Agent 21 (Live Rotation & Foul Trouble Tracker)...")
    monitor_thread = threading.Thread(target=rotation_monitor_loop, daemon=True)
    monitor_thread.start()
    uvicorn.run(app, host="0.0.0.0", port=8017)
