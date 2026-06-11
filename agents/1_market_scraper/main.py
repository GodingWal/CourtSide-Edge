import time

from shared.base_agent import setup_logging
from shared.espn_client import get_scoreboard
from shared.redis_client import RedisPubSub

logger = setup_logging('Agent1_MarketScraper')

POLL_SECONDS = 60


def main():
    pubsub = RedisPubSub()
    logger.info("Agent 1 (Market Scraper) started. Polling real WNBA game lines (ESPN).")

    last_lines: dict = {}

    while True:
        games = get_scoreboard()
        if not games:
            logger.info("No WNBA games on today's scoreboard (or feed unavailable).")
        for game in games:
            odds = game.get("odds")
            if not odds or odds.get("over_under") is None:
                continue

            payload = {
                "source": "Agent 1",
                "game_id": game["game_id"],
                "espn_id": game["espn_id"],
                "home": game["home"],
                "away": game["away"],
                "state": game["state"],
                "provider": odds.get("provider"),
                "details": odds.get("details"),
                "spread": odds.get("spread"),
                "over_under": odds.get("over_under"),
                "timestamp": time.time(),
            }

            # Only publish when the line actually changed (real movement).
            prev = last_lines.get(game["game_id"])
            changed = prev is None or (
                prev.get("spread") != payload["spread"] or prev.get("over_under") != payload["over_under"]
            )
            if changed:
                if prev is not None:
                    payload["prev_spread"] = prev.get("spread")
                    payload["prev_over_under"] = prev.get("over_under")
                    logger.info(
                        f"Line move {game['game_id']}: spread {prev.get('spread')}→{payload['spread']}, "
                        f"O/U {prev.get('over_under')}→{payload['over_under']}"
                    )
                else:
                    logger.info(f"Opening line {game['game_id']}: {payload['details']} O/U {payload['over_under']}")
                pubsub.publish("channel_live_odds", payload)
                last_lines[game["game_id"]] = {
                    "spread": payload["spread"],
                    "over_under": payload["over_under"],
                }

            # Keep the latest snapshot queryable by other agents (hedge oracle).
            try:
                pubsub.client.hset("live:lines", game["game_id"], str(payload["over_under"]))
            except Exception as e:
                logger.warning(f"Failed to cache live line: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
