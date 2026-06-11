"""Agent 2.5: Game Flow Oracle — per-game context from real upstream signals.

Aggregates the live market line (Agent 1), referee tendencies (Agent 5),
coach/player sentiment (Agent 9) and roster intel (Agent 2) into one
channel_game_context payload per game. Publishes only when a real market line
exists for the game — no synthetic heartbeat context.
"""
import threading
import time

from shared.base_agent import setup_logging, run_polling_loop
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent2.5_GameFlowOracle")


class GameFlowOracle:
    def __init__(self):
        self.lock = threading.Lock()
        self.market = {}            # game_id -> latest ESPN game-line message
        self.referee = {}           # game_id -> latest referee context
        self.sentiment = None       # latest league-wide sentiment analysis
        self.roster_alerts = []     # recent MAJOR-impact roster updates

    def on_market(self, message):
        game_id = message.get("game_id")
        if not game_id or message.get("over_under") is None:
            return None
        with self.lock:
            self.market[game_id] = message
        return game_id

    def on_referee(self, message):
        game_id = message.get("game_id")
        if not game_id:
            return None
        with self.lock:
            self.referee[game_id] = message
        return game_id

    def on_sentiment(self, message):
        with self.lock:
            self.sentiment = message
        return None  # league-wide: re-publish all known games

    def on_roster(self, message):
        if message.get("game_impact") not in ("MINOR", "MAJOR"):
            return None
        with self.lock:
            self.roster_alerts.append(message)
            self.roster_alerts = self.roster_alerts[-20:]
        return None

    def build_context(self, game_id):
        """Game context derived entirely from observed data; None without a
        real market line for the game."""
        with self.lock:
            market = self.market.get(game_id)
            if not market:
                return None
            referee = self.referee.get(game_id)
            sentiment = self.sentiment
            roster = list(self.roster_alerts)

        total = market.get("over_under")
        spread = market.get("spread")

        # WNBA league scoring runs ≈1 point per possession, so the market
        # total implies ~total/2 possessions per team (pace).
        pace_estimate = round(total / 2.0, 1) if total else None

        blowout_risk = None
        if spread is not None:
            abs_spread = abs(spread)
            blowout_risk = "High" if abs_spread >= 12 else "Medium" if abs_spread >= 7 else "Low"

        context = {
            "source": "Agent 2.5",
            "game_id": game_id,
            "home": market.get("home"),
            "away": market.get("away"),
            "state": market.get("state"),
            "market_total": total,
            "market_spread": spread,
            "pace_estimate": pace_estimate,
            "blowout_risk": blowout_risk,
            "timestamp": time.time(),
        }
        if referee:
            context["ref_bias"] = (referee.get("tendencies") or {}).get("pace_effect")
            context["ref_crew"] = referee.get("crew")
        if sentiment:
            context["fatigue_state"] = sentiment.get("fatigue_penalty")
        game_teams = {market.get("home"), market.get("away")}
        relevant_roster = [r for r in roster if r.get("team") in game_teams]
        if relevant_roster:
            context["roster_alerts"] = [
                {
                    "player": r.get("player_name"),
                    "team": r.get("team"),
                    "status": r.get("injury_status"),
                    "impact": r.get("game_impact"),
                }
                for r in relevant_roster
            ]
        return context


oracle = GameFlowOracle()
pubsub = None


def publish_context(game_id):
    context = oracle.build_context(game_id)
    if context is None:
        return
    logger.info(f"Publishing game context for {game_id}: total={context['market_total']}, "
                f"spread={context['market_spread']}, blowout_risk={context['blowout_risk']}")
    if pubsub:
        pubsub.publish("channel_game_context", context)


def publish_all_known():
    with oracle.lock:
        game_ids = list(oracle.market.keys())
    for game_id in game_ids:
        publish_context(game_id)


def on_live_odds(message):
    game_id = oracle.on_market(message)
    if game_id:
        publish_context(game_id)


def on_referee(message):
    game_id = oracle.on_referee(message)
    if game_id:
        publish_context(game_id)


def on_sentiment(message):
    oracle.on_sentiment(message)
    publish_all_known()


def on_roster(message):
    oracle.on_roster(message)
    publish_all_known()


def main():
    global pubsub
    pubsub = RedisPubSub()
    logger.info("Agent 2.5 (Game Flow Oracle) started. Waiting for real upstream signals…")

    pubsub.subscribe("channel_live_odds", on_live_odds)
    pubsub.subscribe("channel_roster_updates", on_roster)
    pubsub.subscribe("channel_referee_context", on_referee)
    pubsub.subscribe("channel_sentiment_context", on_sentiment)

    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        run_polling_loop(interval=30.0)
    finally:
        pubsub.close()


if __name__ == "__main__":
    main()
