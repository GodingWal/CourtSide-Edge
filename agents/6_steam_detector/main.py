import time
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging("Agent6_SteamDetector")

class SteamDetector:
    def __init__(self):
        self.line_history = {}
        
    def detect_steam(self, odds_data):
        # Tracking line movement > 0.5 units in under 2 minutes
        # Mocking detection
        return True

def on_live_odds(message, pubsub, detector):
    if detector.detect_steam(message):
        alert = {
            "source": "Agent 6",
            "type": "Steam_Move",
            "direction": "Sharp_Money",
            "timestamp": time.time()
        }
        logger.info(f"Steam detected! Publishing: {alert}")
        pubsub.publish("channel_steam_alerts", alert)

def main():
    pubsub = RedisPubSub()
    detector = SteamDetector()
    logger.info("Agent 6 (Line Steam Detector) started.")
    
    pubsub.subscribe("channel_live_odds", lambda m: on_live_odds(m, pubsub, detector))
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
