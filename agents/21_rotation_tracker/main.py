import time
import logging
from fastapi import FastAPI
import uvicorn
import threading
from shared.context_client import ContextClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent21_RotationTracker")

app = FastAPI(title="Agent 21: Live Rotation & Foul Trouble Tracker")
context_client = ContextClient()

# Mock player rotation database
ROTATIONS = [
    {"player": "A'ja Wilson", "fouls": 3, "period": "2nd Quarter", "adjustment": "-4.5 min", "status": "FOUL_TROUBLE"},
    {"player": "Angel Reese", "fouls": 4, "period": "3rd Quarter", "adjustment": "-6.0 min", "status": "SEVERE_FOUL_TROUBLE"},
    {"player": "Caitlin Clark", "fouls": 1, "period": "1st Quarter", "adjustment": "0.0 min", "status": "NORMAL"}
]

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/rotations")
def get_rotations():
    return ROTATIONS

def publish_rotation_adjustments():
    """Publishes minutes and points adjustments to shared context store for players in foul trouble."""
    logger.info("Evaluating live game rotations for foul trouble adjustments...")
    
    # Process each active WNBA player in rotation
    for r in ROTATIONS:
        player = r["player"]
        status = r["status"]
        
        if status in ["FOUL_TROUBLE", "SEVERE_FOUL_TROUBLE"]:
            # If in foul trouble, compute negative projection adjustments
            mins_deduction = -4.5 if status == "FOUL_TROUBLE" else -6.0
            
            # Formulate calibration impact (e.g., deduction of points and rebounds due to lost floor time)
            adjustment_context = {
                "player": player,
                "minutes_adjustment": mins_deduction,
                "PTS": round(mins_deduction * 0.65, 2), # e.g. -2.92 points
                "REB": round(mins_deduction * 0.25, 2), # e.g. -1.12 rebounds
                "AST": round(mins_deduction * 0.15, 2)
            }
            
            # Write to context blackboard under game LVA_NYL/IND_CHI (for player A'ja/Angel)
            game_id = "LVA_NYL" if player in ["A'ja Wilson", "Breanna Stewart"] else "IND_CHI"
            
            context_client.write_context(
                game_id=game_id,
                agent_id="Agent_21",
                context_key="live_minutes_adjustment",
                context_value=adjustment_context,
                confidence=0.88,
                ttl_seconds=3600
            )
            logger.info(f"Published live rotation adjustment context for {player} on {game_id}: {adjustment_context}")

def rotation_monitor_loop():
    time.sleep(8)
    while True:
        try:
            publish_rotation_adjustments()
        except Exception as e:
            logger.error(f"Error in rotation monitor: {e}")
        time.sleep(30) # recalculate adjustments every 30s

if __name__ == "__main__":
    logger.info("Starting Agent 21 (Live Rotation & Foul Trouble Tracker)...")
    
    # Run loop in background thread
    monitor_thread = threading.Thread(target=rotation_monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start FastAPI on port 8017
    uvicorn.run(app, host="0.0.0.0", port=8017)
