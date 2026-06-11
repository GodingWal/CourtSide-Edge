import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging("Agent19_SharpProfiler")

app = FastAPI(title="Agent 19: Sharp vs. Retail Market Profiler")
pubsub = None

@app.get("/health")
def health():
    return {"status": "healthy"}

def on_raw_odds(message):
    """Monitors real incoming odds (Agent 1 / ESPN consensus) for genuine line moves."""
    # Only react to REAL movement: Agent 1 includes prev_* fields when a line changed.
    prev = message.get("prev_over_under") if message.get("prev_over_under") is not None else message.get("prev_line")
    curr = message.get("over_under") if message.get("over_under") is not None else message.get("line")
    if prev is None or curr is None or prev == curr:
        return

    book = message.get("provider") or message.get("book") or "Consensus"
    subject = message.get("player") or message.get("game_id", "GAME")
    stat = message.get("stat") or ("TOTAL" if message.get("over_under") is not None else "LINE")
    logger.info(f"Real line movement on {book}: {subject} {stat} {prev} → {curr}")

    # ESPN consensus movement is a real signal but NOT proof of sharp action;
    # a fixed 0.95 confidence overstated it badly and cascaded straight into
    # sizing. 0.6 keeps it above Agent 11's noise gate without dominating.
    sharp_event = {
        "source": "Agent 19",
        "type": "sharp_move",
        "data": {
            "player": subject,
            "stat": stat,
            "book": book,
            "move": f"{prev} → {curr}",
            "direction": "UP" if curr > prev else "DOWN",
            "timestamp": time.time()
        },
        "confidence": 0.6,
        "timestamp": time.time()
    }
    if pubsub:
        pubsub.publish("channel_sharp_moves", sharp_event)
        # Surface on the dashboard (web API reads recent:sharp_consensus)
        pubsub.push_recent("recent:sharp_consensus", {
            "player": subject,
            "stat": stat,
            "book": book,
            "move": f"{prev} → {curr}",
            "direction": "UP" if curr > prev else "DOWN",
            "timestamp": int(time.time() * 1000),
        })

def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", on_raw_odds)
    logger.info("Subscribed to channel_live_odds (real lines only — no simulation).")
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except Exception as e:
        logger.error(f"Redis listener failed: {e}")

if __name__ == "__main__":
    logger.info("Starting Agent 19 (Sharp vs. Retail Market Profiler)...")
    
    # Run Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI on port 8015
    uvicorn.run(app, host="0.0.0.0", port=8015)
