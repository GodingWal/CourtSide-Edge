import time
import threading
from fastapi import FastAPI, Query, HTTPException
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging

logger = setup_logging('Agent23_GameSessionManager')

app = FastAPI(title="Agent 23: WNBA Game Session Manager")

# Mock database of games in memory
games_db = {
    "LVA_NYL": {
        "gameId": "LVA_NYL",
        "teams": ["LVA", "NYL"],
        "tipoff": time.time() + 900,  # 15 minutes from now (inside the 30-min parlay window)
        "status": "PRE"
    },
    "IND_CHI": {
        "gameId": "IND_CHI",
        "teams": ["IND", "CHI"],
        "tipoff": time.time() + 1800,  # 30 minutes from now (on the boundary)
        "status": "PRE"
    }
}

# Lock for thread safety
db_lock = threading.Lock()


@app.get('/health')
def health_check():
    return {"status": "healthy"}


@app.get('/schedule')
def get_schedule():
    with db_lock:
        return list(games_db.values())


@app.get('/game/{game_id}/status')
def get_game_status(game_id: str):
    with db_lock:
        if game_id not in games_db:
            raise HTTPException(status_code=404, detail="Game not found")
        return games_db[game_id]


@app.get('/game/{game_id}/set_status')
def set_game_status(game_id: str, status: str = Query(..., regex="^(PRE|LIVE|FINAL)$")):
    with db_lock:
        if game_id not in games_db:
            # Create dynamic entry if game doesn't exist, to support arbitrary game IDs
            games_db[game_id] = {
                "gameId": game_id,
                "teams": game_id.split('_') if '_' in game_id else ["UNK", "UNK"],
                "tipoff": time.time() + 600,
                "status": status
            }
        else:
            games_db[game_id]["status"] = status
        
        updated_game = games_db[game_id]
        
    logger.info(f"Forced status of game {game_id} to {status}")
    
    # Immediately publish the update to Redis
    try:
        pubsub = RedisPubSub()
        pubsub.publish("channel_game_active", updated_game)
        pubsub.close()
    except Exception as e:
        logger.error(f"Failed to publish status update to Redis: {e}")
        
    return updated_game


def run_heartbeat():
    pubsub = None
    logger.info("Starting heartbeat loop...")
    while True:
        try:
            if pubsub is None:
                pubsub = RedisPubSub()
            
            with db_lock:
                current_games = list(games_db.values())
            
            for game in current_games:
                logger.info(f"Publishing heartbeat for game {game['gameId']} (status: {game['status']})")
                pubsub.publish("channel_game_active", game)
                
        except Exception as e:
            logger.error(f"Error in heartbeat thread: {e}")
            pubsub = None  # Force reconnection next loop
            
        time.sleep(30)


if __name__ == '__main__':
    # Start heartbeat in background thread
    heartbeat_thread = threading.Thread(target=run_heartbeat, daemon=True)
    heartbeat_thread.start()
    
    logger.info("Agent 23 (WNBA Game Session Manager) started.")
    uvicorn.run(app, host="0.0.0.0", port=8019)
