import time
import logging
from shared.redis_client import RedisPubSub
from infrastructure.nemotron.client import NemotronClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent2_NewsSentinel")

def poll_twitter():
    # Simulate fetching a tweet
    return "Breanna Stewart is probable for tonight's game against the Aces. - NYL Beat Writer"

def main():
    pubsub = RedisPubSub()
    nemotron = NemotronClient()
    logger.info("Agent 2 (News Sentinel) started. Polling X...")
    
    while True:
        tweet = poll_twitter()
        logger.info(f"New tweet detected: '{tweet}'")
        
        # Pipe to Nemotron 70B
        extracted_data = nemotron.extract_injury_json(tweet)
        logger.info(f"Nemotron extracted JSON: {extracted_data}")
        
        # Publish downstream
        pubsub.publish("channel_roster_updates", extracted_data)
        
        # Poll every 30 seconds
        time.sleep(30)

if __name__ == "__main__":
    main()
