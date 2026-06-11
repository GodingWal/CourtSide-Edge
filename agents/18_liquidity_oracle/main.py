import threading
import time

from fastapi import FastAPI
import uvicorn

from shared.base_agent import setup_logging
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent18_LiquidityOracle")

app = FastAPI(title="Agent 18: Bookmaker Liquidity & Limits Oracle")

# Configured sportsbook max limits for player prop markets (operator-maintained
# reference data based on each book's published/observed limits — update as
# limits change; this is configuration, not a live feed).
BOOK_LIMITS = {
    "Pinnacle": {"type": "SHARP_MAKER", "max_limit": 2000.0, "currency": "USD"},
    "Circa": {"type": "SHARP_MAKER", "max_limit": 1500.0, "currency": "USD"},
    "FanDuel": {"type": "RETAIL_TAKER", "max_limit": 250.0, "currency": "USD"},
    "DraftKings": {"type": "RETAIL_TAKER", "max_limit": 200.0, "currency": "USD"},
    "BetMGM": {"type": "RETAIL_TAKER", "max_limit": 150.0, "currency": "USD"}
}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/limits")
def get_limits():
    """Exposes current limits for external agent sizing queries."""
    return BOOK_LIMITS

@app.get("/limits/{book}")
def get_book_limit(book: str):
    if book in BOOK_LIMITS:
        return BOOK_LIMITS[book]
    return {"error": "Bookmaker not found", "default_limit": 100.0}

def publish_limits_loop():
    """Keep the dashboard's recent:liquidity_limits list in sync with config."""
    while True:
        try:
            pubsub = RedisPubSub()
            rows = [
                {"book": book, "type": cfg["type"], "limit": cfg["max_limit"]}
                for book, cfg in BOOK_LIMITS.items()
            ]
            pubsub.client.delete("recent:liquidity_limits")
            for row in reversed(rows):
                pubsub.push_recent("recent:liquidity_limits", row, cap=20)
            pubsub.close()
            logger.info(f"Published {len(rows)} configured book limits to Redis.")
        except Exception as e:
            logger.warning(f"Failed to publish limits to Redis: {e}")
        time.sleep(300)


if __name__ == "__main__":
    logger.info("Starting Agent 18 (Bookmaker Liquidity & Limits Oracle)...")
    threading.Thread(target=publish_limits_loop, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8014)
