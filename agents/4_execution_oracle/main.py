import time
import logging
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent4_ExecutionOracle")

app = FastAPI(title="Execution Oracle API")
pubsub = None
execution_log = []
MAX_DRAWDOWN = 0.15
current_drawdown = 0.0

@app.get("/health")
def health_check():
    return {"status": "healthy", "circuit_breaker": current_drawdown >= MAX_DRAWDOWN}

@app.get("/log")
def get_log():
    return {"executions": execution_log[-50:]}

def on_execution_order(message):
    global current_drawdown
    logger.info(f"Received execution order: {message}")
    
    if current_drawdown >= MAX_DRAWDOWN:
        logger.error("CIRCUIT BREAKER ACTIVE: Max drawdown reached. Bet aborted.")
        return
        
    logger.info(f"EXECUTING BET: {message['recommended_bet_amount']} units")
    execution_log.append(message)
    
    # In reality, this would hit sportsbook APIs
    
def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_execution_queue", on_execution_order)
    logger.info("Subscribed to channel_execution_queue")
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f"Redis listener error: {e}")

if __name__ == "__main__":
    logger.info("Agent 4 (P&L Oracle & Execution) started.")
    
    # Start Redis listener in background thread
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    # Start FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8001)
