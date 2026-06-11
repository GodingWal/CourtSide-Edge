import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging

logger = setup_logging("Agent17_VelocityAgent")

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
    
    # Agent 1 publishes REAL game lines from ESPN:
    # { "game_id": "IND_CHI", "over_under": 165.5, "spread": -4.5, "timestamp": ... }
    # (player-prop messages, when a props feed is configured, carry player/stat/line/odds)
    if message.get("over_under") is None and message.get("line") is None:
        return
    player = message.get("player") or message.get("game_id", "GAME")
    stat = message.get("stat") or ("TOTAL" if message.get("over_under") is not None else "LINE")
    line = message.get("line", message.get("over_under"))
    odds = message.get("odds")  # may be None for ESPN game lines / odds-less props
    ts = message.get("timestamp", time.time())

    # Per-book history: cross-book differences are not line velocity.
    book = message.get("book") or message.get("provider") or "consensus"
    key = f"{player}:{stat}:{book}"
    if key not in price_histories:
        if len(price_histories) > 2000:
            # Bound total tracked markets; drop the stalest half.
            stale = sorted(price_histories, key=lambda k: price_histories[k][-1][0])
            for k in stale[: len(stale) // 2]:
                price_histories.pop(k, None)
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

            # Odds delta per minute (only when both updates carried a price)
            odds_delta = (odds - prev_odds) if odds is not None and prev_odds is not None else 0
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
                    # Surface on the dashboard (web API reads recent:velocity_alerts)
                    pubsub.push_recent("recent:velocity_alerts", {
                        "player": player,
                        "stat": stat,
                        "direction": direction,
                        "delta": f"{line_delta:+.1f}",
                        "odds_delta": f"{odds_delta:+d}",
                        "duration_seconds": int(time_delta_seconds),
                        "reason": alert_payload["data"]["reason"],
                        "timestamp": int(time.time() * 1000),
                    })

def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_live_odds", process_odds_message)
    logger.info("Subscribed to channel_live_odds (real lines from Agent 1).")

if __name__ == "__main__":
    logger.info("Starting Agent 17 (Line Movement Velocity Agent)...")
    
    # Run Redis listener and simulation loop in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI server on port 8013
    uvicorn.run(app, host="0.0.0.0", port=8013)
