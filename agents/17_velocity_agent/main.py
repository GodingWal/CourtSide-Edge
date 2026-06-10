import time
import logging
import json
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent17_VelocityAgent")

app = FastAPI(title="Agent 17: Line Movement Velocity Agent")
pubsub = None

# Store price histories in memory: { "player:stat": [(timestamp, line, odds), ...] }
price_histories = {}

@app.get("/health")
def health():
    return {"status": "healthy"}

def process_odds_message(message):
    """Processes incoming live odds, calculates velocity of changes, and alerts on anomalies."""
    logger.info("Agent 17 received live odds stream data.")
    
    # We parse the incoming mock data
    # Mock data format published by Agent 1:
    # { "player": "Caitlin Clark", "stat": "AST", "line": 8.5, "odds": -110, "timestamp": ... }
    player = message.get("player", "A'ja Wilson")
    stat = message.get("stat", "PTS")
    line = message.get("line", 22.5)
    odds = message.get("odds", -110)
    ts = message.get("timestamp", time.time())
    
    key = f"{player}:{stat}"
    if key not in price_histories:
        price_histories[key] = []
        
    price_histories[key].append((ts, line, odds))
    
    # Limit history size per key
    price_histories[key] = price_histories[key][-20:]
    
    # Calculate velocity if we have at least 2 data points
    if len(price_histories[key]) >= 2:
        prev_ts, prev_line, prev_odds = price_histories[key][-2]
        time_delta_seconds = ts - prev_ts
        
        if time_delta_seconds > 0:
            # Line delta per minute
            line_delta = line - prev_line
            line_velocity_per_min = (line_delta / time_delta_seconds) * 60.0
            
            # Odds delta per minute
            odds_delta = odds - prev_odds
            odds_velocity_per_min = (odds_delta / time_delta_seconds) * 60.0
            
            logger.info(f"Line Velocity for {key}: {line_velocity_per_min:.2f} line/min, {odds_velocity_per_min:.2f} odds/min")
            
            # Anomaly check: if line changes by >= 1.0 or odds change by >= 15 cents in a short window
            if abs(line_delta) >= 1.0 or abs(odds_delta) >= 15:
                direction = "UP" if (line_delta > 0 or odds_delta < 0) else "DOWN"
                
                alert_payload = {
                    "source": "Agent 17",
                    "type": "velocity_alert",
                    "data": {
                        "player": player,
                        "stat": stat,
                        "direction": direction,
                        "line_delta": f"{line_delta:+.1f}",
                        "odds_delta": f"{odds_delta:+d}",
                        "duration_seconds": int(time_delta_seconds),
                        "reason": f"Rapid {direction} line move detected: {line_delta:+.1f} line, {odds_delta:+d} odds in {int(time_delta_seconds)}s"
                    },
                    "confidence": 0.88,
                    "sample_size": len(price_histories[key]),
                    "decay_seconds": 300,
                    "timestamp": time.time()
                }
                
                logger.info(f"🚨 VELOCITY ANOMALY DETECTED: {alert_payload['data']['reason']}")
                if pubsub:
                    # Publish as a steam alert so correlation engine & execution monitor pick it up
                    pubsub.publish("channel_steam_alerts", alert_payload)

def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", process_odds_message)
    logger.info("Subscribed to channel_live_odds")
    
    # We will also simulate receiving updates periodically to keep the dashboard active when Redis is run offline
    # This simulates line shifts every 15 seconds
    while True:
        try:
            # Generate mock live odds update to process
            mock_players = ["Caitlin Clark", "A'ja Wilson", "Breanna Stewart"]
            mock_stats = ["PTS", "AST", "REB"]
            p = mock_players[int(time.time()) % 3]
            s = mock_stats[int(time.time()) % 3]
            l = 20.5 + (int(time.time()) % 5) * 0.5
            o = -110 - (int(time.time()) % 4) * 5
            
            process_odds_message({
                "player": p,
                "stat": s,
                "line": l,
                "odds": o,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(f"Error in simulated odds generator: {e}")
        time.sleep(15)

if __name__ == "__main__":
    logger.info("Starting Agent 17 (Line Movement Velocity Agent)...")
    
    # Run Redis listener and simulation loop in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI server on port 8013
    uvicorn.run(app, host="0.0.0.0", port=8013)
