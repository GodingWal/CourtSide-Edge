import time
import logging
import threading
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent2.5_GameFlowOracle")

class GameFlowOracle:
    def __init__(self):
        self.current_roster_updates = {}
        self.current_referee_context = {}
        self.current_sentiment = {}
        
    def calculate_game_context(self):
        # Applies modifiers: Blowout Risk, Pace Factor, Matchup History, Fatigue State
        return {
            "source": "Agent 2.5",
            "blowout_risk": "Low",
            "pace_modifier": 1.05, # Fast pace expected
            "fatigue_state": self.current_sentiment.get("fatigue_penalty", 0),
            "ref_bias": self.current_referee_context.get("tendencies", {}).get("pace_effect", 0),
            "timestamp": time.time()
        }

oracle = GameFlowOracle()
pubsub = None

def on_roster(message):
    oracle.current_roster_updates = message
    publish_context()

def on_referee(message):
    oracle.current_referee_context = message
    publish_context()

def on_sentiment(message):
    oracle.current_sentiment = message
    publish_context()

def publish_context():
    context = oracle.calculate_game_context()
    logger.info(f"Publishing aggregated game context: {context}")
    if pubsub:
        pubsub.publish("channel_game_context", context)

def main():
    global pubsub
    pubsub = RedisPubSub()
    logger.info("Agent 2.5 (Game Flow Oracle) started.")
    
    pubsub.subscribe("channel_roster_updates", on_roster)
    pubsub.subscribe("channel_referee_context", on_referee)
    pubsub.subscribe("channel_sentiment_context", on_sentiment)
    
    try:
        while True:
            # Publish heartbeat context every 30 seconds
            publish_context()
            time.sleep(30)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
