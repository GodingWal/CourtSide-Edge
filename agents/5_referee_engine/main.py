import time
import logging
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent5_RefereeEngine")

class RefereeTendencyModel:
    def __init__(self):
        self.ref_profiles = {
            "Crew_A": {"fouls_per_40": 38.5, "pace_effect": -1.2, "ou_hit_rate": "Under_Heavy"},
            "Crew_B": {"fouls_per_40": 30.1, "pace_effect": 2.5, "ou_hit_rate": "Over_Heavy"},
        }
        
    def analyze_crew(self, crew_name):
        return self.ref_profiles.get(crew_name, {"fouls_per_40": 34.0, "pace_effect": 0.0, "ou_hit_rate": "Neutral"})

def poll_assignments():
    # Simulate polling referee assignments daily
    return {"game_id": "LVA_NYL", "crew": "Crew_A"}

def main():
    pubsub = RedisPubSub()
    model = RefereeTendencyModel()
    logger.info("Agent 5 (Referee Tendency Engine) started.")
    
    while True:
        assignment = poll_assignments()
        analysis = model.analyze_crew(assignment["crew"])
        
        payload = {
            "source": "Agent 5",
            "game_id": assignment["game_id"],
            "crew": assignment["crew"],
            "tendencies": analysis,
            "timestamp": time.time()
        }
        logger.info(f"Publishing referee context: {payload}")
        pubsub.publish("channel_referee_context", payload)
        
        # Run daily in reality, simulate 60s for testing
        time.sleep(60)

if __name__ == "__main__":
    main()
