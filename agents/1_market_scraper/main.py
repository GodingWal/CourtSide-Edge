import time
from shared.redis_client import RedisPubSub

def main():
    pubsub = RedisPubSub()
    print("Agent 1 (Market Scraper) started.", flush=True)
    
    while True:
        # Dummy data for testing
        odds_data = {
            "source": "Agent 1",
            "message": "Live odds simulated data",
            "timestamp": time.time()
        }
        print(f"Publishing to channel_live_odds: {odds_data}", flush=True)
        pubsub.publish("channel_live_odds", odds_data)
        time.sleep(5)

if __name__ == "__main__":
    main()
