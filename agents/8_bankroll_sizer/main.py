import time
import logging
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent8_BankrollSizer")

class BankrollSizer:
    def __init__(self):
        self.bankroll = 1000.00
        self.kelly_fraction = 0.25 # 1/4 Kelly
        
    def calculate_sizing(self, edge_data):
        # f = (bp - q) / b
        # Mock values for Kelly calculation
        prob_win = 0.55
        prob_loss = 0.45
        decimal_odds = 1.91 # -110 American
        b = decimal_odds - 1
        
        kelly_f = ((b * prob_win) - prob_loss) / b
        fractional_kelly = kelly_f * self.kelly_fraction
        
        # Max bet cap at 5% of bankroll
        bet_fraction = min(max(fractional_kelly, 0), 0.05)
        if bet_fraction <= 0:
            return 0.0
            
        return round(self.bankroll * bet_fraction, 2)

def on_approved_edge(message, pubsub, sizer):
    logger.info(f"Received approved edge: {message}")
    bet_amount = sizer.calculate_sizing(message)
    
    if bet_amount > 0:
        execution_order = {
            "source": "Agent 8",
            "edge": message,
            "recommended_bet_amount": bet_amount,
            "timestamp": time.time()
        }
        logger.info(f"Publishing execution order: {execution_order}")
        pubsub.publish("channel_execution_queue", execution_order)
    else:
        logger.warning(f"Edge rejected due to negative Kelly sizing: {message}")

def main():
    pubsub = RedisPubSub()
    sizer = BankrollSizer()
    logger.info("Agent 8 (Bankroll & Kelly Sizer) started.")
    
    pubsub.subscribe("channel_approved_edges", lambda m: on_approved_edge(m, pubsub, sizer))
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pubsub.close()

if __name__ == "__main__":
    main()
