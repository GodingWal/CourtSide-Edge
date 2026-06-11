import time

from shared.base_agent import setup_logging
from shared.espn_client import get_injuries, get_news
from shared.redis_client import RedisPubSub
from infrastructure.hermes.client import HermesClient

logger = setup_logging("Agent2_NewsSentinel")

POLL_SECONDS = 120

# ESPN's official report statuses → the pipeline's injury_status vocabulary.
STATUS_MAP = {
    "OUT": "OUT",
    "DOUBTFUL": "DOUBTFUL",
    "QUESTIONABLE": "QUESTIONABLE",
    "DAY-TO-DAY": "QUESTIONABLE",
    "PROBABLE": "PROBABLE",
    "ACTIVE": "ACTIVE",
}


def publish_injury_report(pubsub, last_statuses: dict):
    """Publish ESPN's official league injury report (deterministic, no LLM).

    Only new players or status changes are published, so the qualitative
    event log isn't flooded with repeats every poll cycle.
    """
    published = 0
    for row in get_injuries():
        status = STATUS_MAP.get(row["status"].upper(), "QUESTIONABLE")
        key = row["player"]
        if last_statuses.get(key) == status:
            continue
        last_statuses[key] = status
        payload = {
            "source": "Agent 2",
            "feed": "espn_injury_report",
            "player_name": row["player"],
            "team": row["team"],
            "injury_status": status,
            "confidence_score": 0.95,
            "source_credibility": "OFFICIAL",
            "game_impact": "MAJOR" if status in ("OUT", "DOUBTFUL") else "MINOR" if status == "QUESTIONABLE" else "NONE",
            "motivation_flag": "NONE",
            "sentiment_score": 0.0,
            "headline": row["detail"] or f"{row['player']} listed {status} on official report",
            "published": row.get("date"),
            "timestamp": time.time(),
        }
        pubsub.publish("channel_roster_updates", payload)
        published += 1
    if published:
        logger.info(f"Published {published} official injury report updates.")


def main():
    pubsub = RedisPubSub()
    hermes = HermesClient()
    logger.info("Agent 2 (News Sentinel) started. Polling real WNBA news + injury report (ESPN).")

    seen = set()
    injury_statuses: dict = {}

    while True:
        # Official ESPN injury report: always available, no LLM required.
        try:
            publish_injury_report(pubsub, injury_statuses)
        except Exception as e:
            logger.warning(f"Injury report poll failed: {e}")

        articles = get_news(limit=15)
        fresh = [a for a in articles if a["id"] not in seen]
        if not fresh:
            logger.info("No new WNBA news this cycle.")
        for article in fresh:
            seen.add(article["id"])
            text = f"{article['headline']}. {article['description']}".strip()
            logger.info(f"New article: '{article['headline'][:90]}'")

            # Extract structured injury/roster intel with the local Hermes model.
            extracted = hermes.extract_injury_json(text)
            if extracted is None:
                logger.warning("LLM unavailable/failed for this article — skipping (no fabricated intel).")
                continue
            extracted["headline"] = article["headline"]
            extracted["published"] = article.get("published")
            logger.info(f"Hermes extracted JSON: {extracted}")

            pubsub.publish("channel_roster_updates", extracted)

        # Bound the dedupe set
        if len(seen) > 500:
            seen = set(list(seen)[-250:])

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
