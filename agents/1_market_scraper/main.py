import json
import os
import threading
import time

from shared.base_agent import setup_logging
from shared.espn_client import get_scoreboard
from shared import odds_api_client
from shared.redis_client import RedisPubSub

logger = setup_logging('Agent1_MarketScraper')

POLL_SECONDS = 60


# The Odds API quota is tight (free tier ~500 credits/month) — poll slowly.
# 7200s (2h) for game odds + props keeps a single-event props setup within
# the free tier; lower it only on a paid plan.
ODDS_POLL_SECONDS = int(os.getenv("ODDS_POLL_SECONDS", 7200))
MAX_PROP_EVENTS = int(os.getenv("ODDS_MAX_PROP_EVENTS", 2))


def map_events_to_game_ids() -> dict:
    """Full-team-name → ESPN game_id map for today's slate.

    The Odds API names games by full team names ("Las Vegas Aces"); the rest
    of the pipeline (Agent 23's session gate, Agent 4's execution gate) keys
    games by the ESPN scoreboard's game_id. Without this mapping, prop edges
    carry no game_id and the execution gate rejects every one of them.
    """
    mapping = {}
    try:
        for g in get_scoreboard():
            for name in (g.get("home_name"), g.get("away_name")):
                if name:
                    mapping[name.strip().lower()] = g["game_id"]
    except Exception as e:
        logger.warning(f"Could not build team→game_id mapping: {e}")
    return mapping


def odds_api_loop(pubsub: RedisPubSub):
    """Real bookmaker odds + player props from The Odds API (when key set)."""
    last_props: dict = {}
    while True:
        try:
            games = odds_api_client.get_game_odds()
            logger.info(f"Odds API: {len(games)} WNBA games with bookmaker odds.")
            name_to_game_id = map_events_to_game_ids() if games else {}
            for game in games[:MAX_PROP_EVENTS]:
                game_id = (
                    name_to_game_id.get(str(game.get("home", "")).strip().lower())
                    or name_to_game_id.get(str(game.get("away", "")).strip().lower())
                )
                props = odds_api_client.get_player_props(game["event_id"])
                logger.info(f"Odds API: {len(props)} player props for {game['away']} @ {game['home']}.")
                for prop in props:
                    if prop.get("line") is None:
                        continue
                    key = f"{prop['player']}|{prop['stat']}|{prop.get('book', 'DFS')}"
                    payload = {
                        "source": "Agent 1",
                        "feed": "odds_api",
                        "game_id": game_id,
                        "player": prop["player"],
                        "stat": prop["stat"],
                        "line": prop["line"],
                        # Real prices only - None when the book posts no over
                        # price (consumers must skip, not assume -110).
                        "odds": prop.get("over_odds"),
                        "under_odds": prop.get("under_odds"),
                        "book": prop.get("book"),
                        "game": f"{game['away']} @ {game['home']}",
                        "timestamp": time.time(),
                    }
                    prev = last_props.get(key)
                    if prev is not None and prev != prop["line"]:
                        payload["prev_line"] = prev
                        logger.info(f"Prop move: {key} {prev} → {prop['line']}")
                    if prev != prop["line"]:
                        pubsub.publish("channel_live_odds", payload)
                        last_props[key] = prop["line"]
                    # Cache for the parlay generator (real prop lines only).
                    pubsub.client.hset("props:lines", key, json.dumps(payload))
            if games:
                pubsub.client.expire("props:lines", ODDS_POLL_SECONDS * 2)
        except Exception as e:
            logger.error(f"Odds API loop error: {e}")
        time.sleep(ODDS_POLL_SECONDS)


def main():
    pubsub = RedisPubSub()
    logger.info("Agent 1 (Market Scraper) started. Polling real WNBA game lines (ESPN).")

    if odds_api_client.enabled():
        logger.info("ODDS_API_KEY detected — starting bookmaker odds/props feed (The Odds API).")
        threading.Thread(target=odds_api_loop, args=(pubsub,), daemon=True).start()
    else:
        logger.info("No ODDS_API_KEY — bookmaker props feed disabled (ESPN game lines only).")

    last_lines: dict = {}

    while True:
        try:
            poll_game_lines(pubsub, last_lines)
        except Exception as e:
            # One Redis/network hiccup must not kill the scraper process.
            logger.error(f"Game-line poll failed: {e}")
        time.sleep(POLL_SECONDS)


def poll_game_lines(pubsub: RedisPubSub, last_lines: dict):
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
            pubsub.client.expire("live:lines", 6 * 3600)  # don't serve stale days-old lines
        except Exception as e:
            logger.warning(f"Failed to cache live line: {e}")


if __name__ == "__main__":
    main()
