import time

from shared.base_agent import setup_logging
from shared.espn_client import get_news
from shared.redis_client import RedisPubSub
from infrastructure.nemotron.client import NemotronClient

logger = setup_logging("Agent2_NewsSentinel")

POLL_SECONDS = 120


def main():
    pubsub = RedisPubSub()
    nemotron = NemotronClient()
    logger.info("Agent 2 (News Sentinel) started. Polling real WNBA news (ESPN).")

    seen = set()

    while True:
        articles = get_news(limit=15)
        fresh = [a for a in articles if a["id"] not in seen]
        if not fresh:
            logger.info("No new WNBA news this cycle.")
        for article in fresh:
            seen.add(article["id"])
            text = f"{article['headline']}. {article['description']}".strip()
            logger.info(f"New article: '{article['headline'][:90]}'")

            # Extract structured injury/roster intel with the local Nemotron model.
            extracted = nemotron.extract_injury_json(text)
            extracted["headline"] = article["headline"]
            extracted["published"] = article.get("published")
            logger.info(f"Nemotron extracted JSON: {extracted}")

            pubsub.publish("channel_roster_updates", extracted)

        # Bound the dedupe set
        if len(seen) > 500:
            seen = set(list(seen)[-250:])

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
