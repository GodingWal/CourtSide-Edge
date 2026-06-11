import time
import os
from fastapi import FastAPI
import uvicorn
import threading
from shared.context_client import ContextClient

from shared.base_agent import setup_logging, db_connect

logger = setup_logging("Agent15_DriftMonitor")

app = FastAPI(title="Agent 15: Drift Monitor & Calibration Oracle")
context_client = ContextClient()

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

@app.get("/health")
def health():
    return {"status": "healthy"}

def analyze_drift():
    """Queries SQLite for settled wagers, calculates MAE and bias, and updates shared memory context."""
    logger.info("Starting projection drift analysis cycle...")
    
    if not os.path.exists(DB_PATH):
        logger.warning(f"Database not found at {DB_PATH}. Aborting drift analysis.")
        return
        
    try:
        conn = db_connect(DB_PATH)
        cursor = conn.cursor()
        
        # Select settled straight wagers that have lines and actual outcomes
        cursor.execute("""
            SELECT stat, line, actual_value 
            FROM bets 
            WHERE is_parlay = 0 
              AND result IS NOT NULL 
              AND actual_value IS NOT NULL 
              AND line IS NOT NULL
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            logger.info("No settled wagers found to analyze. Writing baseline calibration.")
            calibration = {"PTS": -0.4, "REB": 0.2, "AST": 0.1}
            # Write global calibration factors to shared blackboard
            context_client.write_context(
                game_id="GLOBAL",
                agent_id="Agent_15",
                context_key="projection_calibration",
                context_value=calibration,
                confidence=0.90,
                ttl_seconds=86400
            )
            return

        # Calculate errors by stat category
        stats_data = {}
        for stat, line, actual in rows:
            if stat not in stats_data:
                stats_data[stat] = []
            stats_data[stat].append(actual - line)

        calibration = {}
        for stat, errors in stats_data.items():
            bias = sum(errors) / len(errors)
            # Calibration factor is the negative of bias to offset it
            calibration[stat] = round(-bias * 0.5, 2) # dampening multiplier
            logger.info(f"Stat: {stat} | Sample Count: {len(errors)} | Bias detected: {bias:.2f} | Calibration Factor: {calibration[stat]:.2f}")

        # Ensure defaults exist
        for default_stat, default_val in [("PTS", -0.4), ("REB", 0.2), ("AST", 0.1)]:
            if default_stat not in calibration:
                calibration[default_stat] = default_val

        # Save to context store
        context_client.write_context(
            game_id="GLOBAL",
            agent_id="Agent_15",
            context_key="projection_calibration",
            context_value=calibration,
            confidence=min(0.95, 0.5 + (len(rows) * 0.01)), # scale confidence with sample size
            ttl_seconds=86400
        )
        logger.info(f"Published projection calibration context: {calibration}")
        
    except Exception as e:
        logger.error(f"Error during drift analysis: {e}")

def drift_monitoring_loop():
    # Let things startup before running analysis
    time.sleep(5)
    while True:
        try:
            analyze_drift()
        except Exception as e:
            logger.error(f"Error in drift monitoring loop: {e}")
        time.sleep(30) # run drift analysis every 30s in this active sandbox

if __name__ == "__main__":
    logger.info("Starting Agent 15 (Drift Monitor & Calibration Swarm)...")
    
    # Run analysis loop in background thread
    monitor_thread = threading.Thread(target=drift_monitoring_loop, daemon=True)
    monitor_thread.start()
    
    # Start FastAPI server on port 8011
    uvicorn.run(app, host="0.0.0.0", port=8011)
