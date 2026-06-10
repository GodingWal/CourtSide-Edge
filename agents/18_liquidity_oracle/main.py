import time
import logging
from fastapi import FastAPI
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent18_LiquidityOracle")

app = FastAPI(title="Agent 18: Bookmaker Liquidity & Limits Oracle")

# Mock database of sportsbook max limits for player prop markets
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

if __name__ == "__main__":
    logger.info("Starting Agent 18 (Bookmaker Liquidity & Limits Oracle)...")
    uvicorn.run(app, host="0.0.0.0", port=8014)
