import time
import logging
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent7_CorrelationGuard")

class CorrelationGuard:
    def __init__(self):
        self.active_game_exposures = {}
        
    def check_correlation(self, edge_data):
        game_id = edge_data.get("game_id", "UNKNOWN")
        exposure = self.active_game_exposures.get(game_id, 0)
        
        # Prevent >3 bets on the same game to avoid correlated risk
        if exposure >= 3:
            logger.warning(f"Rejecting edge {edge_data} due to high game exposure")
            return False
            
        self.active_game_exposures[game_id] = exposure + 1
        return True

def on_market_intelligence(message, pubsub, guard):
    logger.info(f"Received market edge: {message}")
    if guard.check_correlation(message):
        message["approved_by"] = "Agent 7"
        logger.info(f"Edge approved, publishing to channel_approved_edges")
        pubsub.publish("channel_approved_edges", message)

def main():
    pubsub = RedisPubSub()
    guard = CorrelationGuard()
    logger.info("Agent 7 (Correlation Guard) started.")
    
    pubsub.subscribe("channel_market_intelligence", lambda m: on_market_intelligence(m, pubsub, guard))
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
