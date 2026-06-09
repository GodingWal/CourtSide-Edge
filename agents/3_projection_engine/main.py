import time
import logging
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub
from ensemble import EnsembleMathCore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent3_ProjectionEngine")

app = FastAPI(title="Projection Engine API")
ensemble = EnsembleMathCore()
pubsub = None

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
    logger.info(f"Agent 3 received live odds trigger. Running ensemble...")
    
    # Run full ensemble
    proj = ensemble.run_projection("player_mock_id", "LVA", {})
    
    response = {
        "source": "Agent 3",
        "type": "true_projection",
        "data": proj,
        "timestamp": time.time()
    }
    logger.info(f"Publishing to channel_true_projections...")
    if pubsub:
        pubsub.publish("channel_true_projections", response)

def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", on_live_odds)
    logger.info("Subscribed to channel_live_odds")
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f"Redis listener error: {e}")

if __name__ == "__main__":
    logger.info("Agent 3 (Projection Engine) started.")
    
    # Start Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)
