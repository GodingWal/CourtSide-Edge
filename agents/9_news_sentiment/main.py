import time
from shared.redis_client import RedisPubSub
from shared.context_client import ContextClient
from infrastructure.nemotron.client import NemotronClient

from shared.base_agent import setup_logging

logger = setup_logging("Agent9_NewsSentiment")

context = ContextClient()

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
        analysis["confidence"] = 0.72
        analysis["sample_size"] = 1
        analysis["decay_seconds"] = 3600
        pubsub.publish("channel_sentiment_context", analysis)
        
        # Write to shared context store so Agent 3 can factor in fatigue/sentiment
        team = analysis.get("team", "UNKNOWN")
        game_id = analysis.get("game_id", f"{team}_UNKNOWN")
        
        context.write_context(
            game_id=game_id,
            agent_id="Agent_9",
            context_key="coach_fatigue_score",
            context_value={
                "team": team,
                "sentiment_score": analysis.get("sentiment_score", 0),
                "fatigue": analysis.get("sentiment_score", 0),
                "summary": analysis.get("summary", content[:100])
            },
            confidence=0.72,
            ttl_seconds=3600
        )
        logger.info(f"  → Wrote sentiment context to shared store for {team}")
        
        # Poll every 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    main()
