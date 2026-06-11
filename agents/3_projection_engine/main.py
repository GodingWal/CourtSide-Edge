import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub
from shared.context_client import ContextClient
from ensemble import EnsembleMathCore

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging("Agent3_ProjectionEngine")

app = FastAPI(title="Projection Engine API")
ensemble = EnsembleMathCore()
pubsub = None
context = ContextClient()

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.get("/project/{player_id}")
def get_projection(player_id: str):
    # API trigger for projection
    game_context = {"missing_players": [], "pace": 80.5}
    proj = ensemble.run_projection(player_id, "LVA", game_context)
    return proj

def on_live_odds(message):
    logger.info("Agent 3 received live odds trigger. Running ensemble...")
    
    # Read shared context from other agents before running projection
    game_id = message.get("game_id", "UNKNOWN")
    shared_context = context.read_context(game_id)
    
    # Extract enrichments from other agents
    enrichments = {}
    for entry in shared_context:
        agent = entry.get("agent_id", "")
        key = entry.get("context_key", "")
        value = entry.get("value", {})
        confidence = entry.get("confidence", 0.5)
        
        if key == "referee_foul_bias" and confidence > 0.6:
            enrichments["ref_pace_effect"] = value.get("pace_effect", 0) if isinstance(value, dict) else 0
            enrichments["ref_ou_tendency"] = value.get("ou_tendency", "Neutral") if isinstance(value, dict) else "Neutral"
            logger.info(f"  → Context from {agent}: Ref pace effect {enrichments['ref_pace_effect']}")
            
        elif key == "coach_fatigue_score" and confidence > 0.5:
            enrichments["fatigue_score"] = value.get("fatigue", 0) if isinstance(value, dict) else 0
            logger.info(f"  → Context from {agent}: Fatigue score {enrichments['fatigue_score']}")
            
        elif key == "roster_alert" and confidence > 0.5:
            enrichments["roster_impact"] = value.get("impact", "") if isinstance(value, dict) else ""
            logger.info(f"  → Context from {agent}: Roster alert - {enrichments['roster_impact']}")
            
        elif key == "live_minutes_adjustment" and confidence > 0.5:
            enrichments["live_minutes_adj"] = value.get("minutes_adjustment", 0) if isinstance(value, dict) else 0
            enrichments["live_pts_adj"] = value.get("PTS", 0) if isinstance(value, dict) else 0
            logger.info(f"  → Context from {agent}: Live minutes adjust: {enrichments['live_minutes_adj']} mins, PTS: {enrichments['live_pts_adj']}")
    
    # Read global calibration from Agent 15 (Drift Monitor)
    global_calib = context.read_context_key("GLOBAL", "Agent_15", "projection_calibration")
    calib_offset = 0.0
    if global_calib and isinstance(global_calib, dict) and "value" in global_calib:
        calib_offset = global_calib["value"].get("PTS", 0.0)
        logger.info(f"  → Applied Agent 15 global calibration offset for PTS: {calib_offset}")

    # Run full ensemble with enriched context
    game_context = {**message, **enrichments}
    proj = ensemble.run_projection("player_mock_id", "LVA", game_context)
    
    if calib_offset != 0.0:
        proj["projected_points"] = round(proj["projected_points"] + calib_offset, 2)

    # Apply live rotation adjustments from Agent 21 if present
    live_pts_adj = enrichments.get("live_pts_adj", 0.0)
    if live_pts_adj != 0.0:
        proj["projected_points"] = round(proj["projected_points"] + live_pts_adj, 2)
        proj["projected_minutes"] = round(proj["projected_minutes"] + enrichments.get("live_minutes_adj", 0), 2)
    
    response = {
        "source": "Agent 3",
        "type": "true_projection",
        "data": proj,
        "context_used": list(enrichments.keys()) + (["global_calibration"] if calib_offset != 0.0 else []) + (["live_rotation_adjustment"] if live_pts_adj != 0.0 else []),
        "confidence": 0.85 if len(enrichments) > 0 else 0.70,
        "sample_size": len(shared_context),
        "decay_seconds": 600,
        "timestamp": time.time()
    }
    logger.info(f"Publishing to channel_true_projections (used {len(enrichments)} context enrichments + calibration={calib_offset})...")
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

if __name__ == "__main__":
    logger.info("Agent 3 (Projection Engine) started.")
    
    # Start Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)
