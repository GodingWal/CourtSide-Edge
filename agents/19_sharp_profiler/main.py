import time
import logging
import json
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent19_SharpProfiler")

app = FastAPI(title="Agent 19: Sharp vs. Retail Market Profiler")
pubsub = None

@app.get("/health")
def health():
    return {"status": "healthy"}

def on_raw_odds(message):
    """Monitors incoming odds and detects sharp movements from maker books (Pinnacle/Circa)."""
    # If the source is Pinnacle/Circa, we publish to sharp moves channel
    book = message.get("book", "Pinnacle")
    if book in ["Pinnacle", "Circa"]:
        logger.info(f"Detected odds movement on sharp maker book: {book}")
        sharp_event = {
            "source": "Agent 19",
            "type": "sharp_move",
            "data": {
                "player": message.get("player", "A'ja Wilson"),
                "stat": message.get("stat", "PTS"),
                "book": book,
                "move": f"{message.get('prev_line', 22.5)} → {message.get('line', 23.5)}",
                "direction": "UP" if message.get("line', 23.5) > message.get("prev_line", 22.5) else "DOWN",
                "timestamp": time.time()
            },
            "confidence": 0.95,
            "timestamp": time.time()
        }
        if pubsub:
            pubsub.publish("channel_sharp_moves", sharp_event)

def simulation_loop():
    # Simulate a sharp move event every 15 seconds to drive retail-lag checks and dashboard alerts
    while True:
        try:
            time.sleep(15)
            mock_players = ["A'ja Wilson", "Caitlin Clark", "Breanna Stewart"]
            mock_stats = ["PTS", "AST", "REB"]
            p = mock_players[int(time.time()) % 3]
            s = mock_stats[int(time.time()) % 3]
            prev = 18.5 + (int(time.time()) % 3) * 0.5
            curr = prev + (0.5 if random_dir() else -0.5)
            
            sharp_event = {
                "source": "Agent 19",
                "type": "sharp_move",
                "data": {
                    "player": p,
                    "stat": s,
                    "book": "Pinnacle" if int(time.time()) % 2 == 0 else "Circa",
                    "move": f"{prev:.1f} → {curr:.1f}",
                    "direction": "UP" if curr > prev else "DOWN",
                    "timestamp": time.time()
                },
                "confidence": 0.95,
                "timestamp": time.time()
            }
            logger.info(f"Simulating sharp maker move: {sharp_event['data']['player']} {sharp_event['data']['move']} on {sharp_event['data']['book']}")
            if pubsub:
                pubsub.publish("channel_sharp_moves", sharp_event)
        except Exception as e:
            logger.error(f"Error in sharp simulation: {e}")

def random_dir():
    import random
    return random.choice([True, False])

def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", on_raw_odds)
    logger.info("Subscribed to channel_live_odds")
    
    # Start simulation loop in background
    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f"Redis listener failed: {e}")

if __name__ == "__main__":
    logger.info("Starting Agent 19 (Sharp vs. Retail Market Profiler)...")
    
    # Run Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI on port 8015
    uvicorn.run(app, host="0.0.0.0", port=8015)
