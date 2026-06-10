import time
import logging
from shared.redis_client import RedisPubSub
from shared.context_client import ContextClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent5_RefereeEngine")

context = ContextClient()

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
            "confidence": 0.85,
            "sample_size": 45,
            "decay_seconds": 7200,
            "timestamp": time.time()
        }
        logger.info(f"Publishing referee context: {payload}")
        pubsub.publish("channel_referee_context", payload)
        
        # Write to shared context store so Agent 3 can read it
        context.write_context(
            game_id=assignment["game_id"],
            agent_id="Agent_5",
            context_key="referee_foul_bias",
            context_value=analysis,
            confidence=0.85,
            ttl_seconds=7200
        )
        logger.info(f"  → Wrote referee context to shared store for game {assignment['game_id']}")
        
        # Run daily in reality, simulate 60s for testing
        time.sleep(60)

if __name__ == "__main__":
    main()
