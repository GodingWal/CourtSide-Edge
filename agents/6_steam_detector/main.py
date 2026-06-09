import time
import logging
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent6_SteamDetector")

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
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
