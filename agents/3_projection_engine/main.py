import json
import os
import time
import threading
from fastapi import FastAPI, HTTPException
import uvicorn
from shared.redis_client import RedisPubSub
from shared.context_client import ContextClient
from ensemble import EnsembleMathCore, STAT_COLUMNS

from shared.base_agent import setup_logging, run_polling_loop, db_connect
from shared.db import db_available

logger = setup_logging("Agent3_ProjectionEngine")

app = FastAPI(title="Projection Engine API")
ensemble = EnsembleMathCore()
pubsub = None
context = ContextClient()

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))


def _player_name_for_id(player_id: str):
    if not db_available(DB_PATH):
        return None
    conn = db_connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT player_name FROM player_box_scores WHERE player_id = ? LIMIT 1",
            (player_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/project/{player_id}")
def get_projection(player_id: str, stat: str = "PTS"):
    """On-demand projection for a real player from stored game logs."""
    player_name = _player_name_for_id(player_id)
    if player_name is None:
        raise HTTPException(status_code=404, detail=f"No game logs for player_id {player_id}")
    proj = ensemble.run_projection(player_name, stat, {})
    if proj is None:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough history to project {player_name} {stat}",
        )
    return proj


def _read_enrichments(game_id: str) -> dict:
    """Pull real cross-agent context (referee, fatigue, roster, live rotation)."""
    enrichments = {}
    for entry in context.read_context(game_id):
        agent = entry.get("agent_id", "")
        key = entry.get("context_key", "")
        value = entry.get("value", {})
        confidence = entry.get("confidence", 0.5)

        if key == "referee_foul_bias" and confidence > 0.6 and isinstance(value, dict):
            enrichments["ref_pace_effect"] = value.get("pace_effect", 0)
            logger.info(f"  → Context from {agent}: Ref pace effect {enrichments['ref_pace_effect']}")
        elif key == "coach_fatigue_score" and confidence > 0.5 and isinstance(value, dict):
            enrichments["fatigue_score"] = value.get("fatigue", 0)
            logger.info(f"  → Context from {agent}: Fatigue score {enrichments['fatigue_score']}")
        elif key == "roster_alert" and confidence > 0.5 and isinstance(value, dict):
            enrichments["roster_impact"] = value.get("impact", "")
            logger.info(f"  → Context from {agent}: Roster alert - {enrichments['roster_impact']}")
        elif key == "live_minutes_adjustment" and confidence > 0.5 and isinstance(value, dict):
            enrichments["live_minutes_adj"] = value.get("minutes_adjustment", 0)
            enrichments["live_pts_adj"] = value.get("PTS", 0)
            logger.info(
                f"  → Context from {agent}: Live minutes adjust: {enrichments['live_minutes_adj']} mins, "
                f"PTS: {enrichments['live_pts_adj']}"
            )
    return enrichments


def _official_out_players() -> list:
    """Players listed OUT on the official report (cached by Agent 2)."""
    if not pubsub:
        return []
    try:
        raw = pubsub.client.get("injuries:report")
        if not raw:
            return []
        return [r["player"] for r in json.loads(raw) if str(r.get("status", "")).upper() == "OUT"]
    except Exception as e:
        logger.warning(f"Could not read injury report for usage redistribution: {e}")
        return []


def on_live_odds(message):
    # Only player-prop messages name a projectable target. ESPN game-line
    # messages (game totals/spreads) carry no player and are handled by
    # Agent 10 (game totals) instead.
    player = message.get("player")
    stat = message.get("stat")
    if not player or stat not in STAT_COLUMNS:
        return

    line = message.get("line")
    logger.info(f"Agent 3 projecting {player} {stat} (market line {line})...")

    # "LVA @ NYL" → context-store key "LVA_NYL"
    game = message.get("game") or ""
    game_id = game.replace(" @ ", "_") if " @ " in game else message.get("game_id", "UNKNOWN")
    enrichments = _read_enrichments(game_id)

    # Usage redistribution input: official OUT list from the injury report.
    game_context = {**message, "out_players": _official_out_players()}

    proj = ensemble.run_projection(player, stat, game_context)
    if proj is None:
        return  # no real history — publish nothing

    if proj.get("usage_boost") and proj["usage_boost"] != 1.0:
        logger.info(
            f"  → Usage redistribution x{proj['usage_boost']} (OUT: {proj['usage_boost_from']})"
        )

    context_used = list(enrichments.keys())

    # Global calibration from Agent 15 (derived from settled bet outcomes).
    calib_offset = 0.0
    global_calib = context.read_context_key("GLOBAL", "Agent_15", "projection_calibration")
    if global_calib and isinstance(global_calib, dict) and "value" in global_calib:
        calib_offset = global_calib["value"].get(stat, 0.0)
    if calib_offset:
        proj["projected_value"] = round(proj["projected_value"] + calib_offset, 2)
        context_used.append("global_calibration")
        logger.info(f"  → Applied Agent 15 calibration offset for {stat}: {calib_offset}")

    # Live rotation adjustments from Agent 21 (real boxscore-driven).
    if stat == "PTS" and enrichments.get("live_pts_adj"):
        proj["projected_value"] = round(proj["projected_value"] + enrichments["live_pts_adj"], 2)
        proj["projected_minutes"] = round(
            proj["projected_minutes"] + enrichments.get("live_minutes_adj", 0), 2
        )
        context_used.append("live_rotation_adjustment")

    edge = round(proj["projected_value"] - line, 2) if line is not None else None

    # Cache the latest projection per market so other agents can rank live
    # props by real edge (Agent 13 parlay legs, Agent 11 true-line context).
    if pubsub and line is not None:
        try:
            pubsub.client.hset("props:projections", f"{player}|{stat}", json.dumps({
                "player": player,
                "stat": stat,
                "projected_value": proj["projected_value"],
                "market_line": line,
                "edge_vs_line": edge,
                "p_over_line": proj.get("p_over_line"),
                "book": message.get("book"),
                "games_sampled": proj["games_sampled"],
                "timestamp": time.time(),
            }))
            pubsub.client.expire("props:projections", 4 * 3600)
        except Exception as e:
            logger.warning(f"Failed to cache projection: {e}")

    response = {
        "source": "Agent 3",
        "type": "true_projection",
        "game_id": game_id,
        "data": {**proj, "market_line": line, "edge_vs_line": edge, "book": message.get("book")},
        "context_used": context_used,
        "confidence": round(min(0.6 + 0.02 * proj["games_sampled"] + 0.05 * len(enrichments), 0.95), 2),
        "sample_size": proj["games_sampled"],
        "decay_seconds": 600,
        "timestamp": time.time(),
    }
    logger.info(
        f"Publishing projection: {player} {stat} {proj['projected_value']} vs line {line} "
        f"(edge {edge}, {proj['games_sampled']} games)"
    )
    if pubsub:
        pubsub.publish("channel_true_projections", response)


def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", on_live_odds)
    logger.info("Subscribed to channel_live_odds")
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except Exception as e:
        logger.error(f"Redis listener error: {e}")


def weekly_model_maintenance():
    """Publish the stat correlation matrix and run walk-forward validation.

    Both are derived purely from stored box scores; the correlations feed
    Agents 7/13 (parlay correlation flags) and the validation summary feeds
    the dashboard. Runs shortly after startup, then weekly.
    """
    from backtest import publish_validation, run_backtest

    time.sleep(120)  # let the listener connect and Agent 0's ETL settle
    while True:
        try:
            correlations = ensemble.stat_correlations()
            if correlations and pubsub:
                pubsub.client.set("stats:correlations", json.dumps(correlations), ex=8 * 24 * 3600)
                logger.info(f"Published stat correlation matrix ({len(correlations)} pairs).")
        except Exception as e:
            logger.error(f"Correlation matrix publish failed: {e}")
        try:
            result = run_backtest(ensemble)
            if result["per_stat"]:
                publish_validation(result)
        except Exception as e:
            logger.error(f"Walk-forward validation failed: {e}")
        time.sleep(7 * 24 * 3600)


if __name__ == "__main__":
    logger.info("Agent 3 (Projection Engine) started.")

    # Start Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()

    # Weekly correlation matrix + walk-forward validation (plan §9).
    threading.Thread(target=weekly_model_maintenance, daemon=True).start()

    # Start FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)
