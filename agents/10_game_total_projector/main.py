import time
import logging
from scipy.stats import poisson
import numpy as np
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent10_GameTotalProjector")

class GameTotalModel:
    def __init__(self):
        # Mocked regression weights
        self.weights = {"intercept": 20, "pace": 1.2, "ortg_drtg_delta": 0.5, "injury": -2.0, "ref": 1.5, "market": 0.8}

    def project_total(self, game_context):
        # Linear Regression OLS
        base_total = (
            self.weights["intercept"] +
            self.weights["pace"] * game_context.get("pace", 80) +
            self.weights["ortg_drtg_delta"] * game_context.get("net_rating", 0) +
            self.weights["injury"] * game_context.get("injury_impact", 0) +
            self.weights["ref"] * game_context.get("ref_bias", 0) +
            self.weights["market"] * game_context.get("steam_movement", 0)
        )
        
        # Poisson simulation for distribution around regression estimate
        sims = poisson.rvs(mu=base_total, size=10000)
        return float(np.mean(sims)), sims

def on_game_context(message, pubsub, model):
    logger.info(f"Received game context: {message}")
    expected_total, sims = model.project_total(message)
    
    response = {
        "source": "Agent 10",
        "projected_total": expected_total,
        "true_over_prob_at_165": float(np.mean(sims > 165.5)),
        "timestamp": time.time()
    }
    logger.info(f"Publishing total projection: {response}")
    pubsub.publish("channel_total_projections", response)

def main():
    pubsub = RedisPubSub()
    model = GameTotalModel()
    logger.info("Agent 10 (Game Total Projector) started.")
    
    pubsub.subscribe("channel_game_context", lambda m: on_game_context(m, pubsub, model))
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
