import time
from scipy.stats import poisson
import numpy as np
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging("Agent10_GameTotalProjector")


class GameTotalModel:
    """Market-anchored total projection.

    Starts from the real bookmaker total (channel_game_context carries it from
    Agent 1's live feed) and adjusts by the referee crew's data-derived pace
    effect (Agent 5's per-ref scoring delta vs league average, in points).
    Without a real market total there is no projection.
    """

    def project_total(self, game_context):
        market_total = game_context.get("market_total")
        if market_total is None:
            return None, None
        ref_bias = game_context.get("ref_bias") or 0.0
        expected = float(market_total) + float(ref_bias)

        # Poisson simulation for the distribution around the point estimate.
        sims = poisson.rvs(mu=expected, size=10000)
        return float(np.mean(sims)), sims


def on_game_context(message, pubsub, model):
    logger.info(f"Received game context: {message}")
    expected_total, sims = model.project_total(message)
    if expected_total is None:
        logger.info("No real market total in context — skipping projection.")
        return

    market_total = float(message["market_total"])
    response = {
        "source": "Agent 10",
        "game_id": message.get("game_id"),
        "market_total": market_total,
        "ref_adjustment": message.get("ref_bias") or 0.0,
        "projected_total": round(expected_total, 1),
        "true_over_prob": float(np.mean(sims > market_total)),
        "timestamp": time.time(),
    }
    logger.info(f"Publishing total projection: {response}")
    pubsub.publish("channel_total_projections", response)


def main():
    pubsub = RedisPubSub()
    model = GameTotalModel()
    logger.info("Agent 10 (Game Total Projector) started.")

    pubsub.subscribe("channel_game_context", lambda m: on_game_context(m, pubsub, model))

    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
