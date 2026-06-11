import time

from shared.base_agent import setup_logging
from shared.context_client import ContextClient
from shared.espn_client import get_news
from shared.redis_client import RedisPubSub
from infrastructure.hermes.client import HermesClient

logger = setup_logging("Agent9_NewsSentiment")

context = ContextClient()

POLL_SECONDS = 180


def main():
    pubsub = RedisPubSub()
    hermes = HermesClient()
    logger.info("Agent 9 (News/Sentiment) started. Analyzing real long-form WNBA coverage (ESPN).")

    seen = set()

    while True:
        articles = get_news(limit=15)
        # Long-form: prefer pieces with substantive descriptions.
        fresh = [a for a in articles if a["id"] not in seen and len(a.get("description") or "") > 40]
        if not fresh:
            logger.info("No new long-form content this cycle.")
        for article in fresh:
            seen.add(article["id"])
            content = f"{article['headline']}. {article['description']}".strip()
            logger.info(f"New content: '{article['headline'][:90]}'")

            # Score with the local Hermes model (temp=0.3).
            analysis = hermes.analyze_sentiment(content)
            if analysis is None:
                logger.warning("LLM unavailable/failed for this article — skipping (no fabricated scores).")
                continue
            logger.info(f"Hermes analysis: {analysis}")

            analysis["headline"] = article["headline"]
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
                    "fatigue": analysis.get("fatigue_penalty", 0),
                    "summary": content[:100],
                },
                confidence=0.72,
                ttl_seconds=3600,
            )
            logger.info(f"  → Wrote sentiment context to shared store for {team}")

        if len(seen) > 500:
            seen = set(list(seen)[-250:])

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
