import time
import logging
from shared.redis_client import RedisPubSub
from infrastructure.nemotron.client import NemotronClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent9_NewsSentiment")

def poll_longform_content():
    # Simulate fetching coach quote
    return "Coach Hammon mentioned the team is feeling the effects of the recent 3-game road trip across timezones."

def main():
    pubsub = RedisPubSub()
    nemotron = NemotronClient()
    logger.info("Agent 9 (News/Sentiment) started. Analyzing long-form content...")
    
    while True:
        content = poll_longform_content()
        logger.info(f"New content detected: '{content}'")
        
        # Pipe to Nemotron 70B (temp=0.3)
        analysis = nemotron.analyze_sentiment(content)
        logger.info(f"Nemotron analysis: {analysis}")
        
        # Publish downstream
        pubsub.publish("channel_sentiment_context", analysis)
        
        # Poll every 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    main()
