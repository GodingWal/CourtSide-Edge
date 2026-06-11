import os
import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging('Agent22_DataWatchdog')

app = FastAPI(title="Agent 22: CourtSideEdge Data Watchdog")

# Configurable thresholds via environment variables (in seconds)
LIVE_ODDS_STALE = float(os.getenv('WATCHDOG_LIVE_ODDS_STALE_SEC', '30.0'))
LIVE_ODDS_DEAD = float(os.getenv('WATCHDOG_LIVE_ODDS_DEAD_SEC', '90.0'))

TRUE_PROJECTIONS_STALE = float(os.getenv('WATCHDOG_TRUE_PROJECTIONS_STALE_SEC', '60.0'))
TRUE_PROJECTIONS_DEAD = float(os.getenv('WATCHDOG_TRUE_PROJECTIONS_DEAD_SEC', '180.0'))

EV_ALERTS_STALE = float(os.getenv('WATCHDOG_EV_ALERTS_STALE_SEC', '120.0'))
EV_ALERTS_DEAD = float(os.getenv('WATCHDOG_EV_ALERTS_DEAD_SEC', '360.0'))

THRESHOLDS = {
    "channel_live_odds": {"stale": LIVE_ODDS_STALE, "dead": LIVE_ODDS_DEAD},
    "channel_true_projections": {"stale": TRUE_PROJECTIONS_STALE, "dead": TRUE_PROJECTIONS_DEAD},
    "channel_ev_alerts": {"stale": EV_ALERTS_STALE, "dead": EV_ALERTS_DEAD}
}

# In-memory map to store the last publish timestamp (initially set to startup time)
startup_time = time.time()
last_publish_times = {
    "channel_live_odds": startup_time,
    "channel_true_projections": startup_time,
    "channel_ev_alerts": startup_time
}

lock = threading.Lock()


@app.get('/health')
def health_check():
    return {"status": "healthy"}


@app.get('/status')
def get_status():
    with lock:
        now = time.time()
        status_report = {}
        for channel, config in THRESHOLDS.items():
            last_time = last_publish_times[channel]
            elapsed = now - last_time
            
            if elapsed <= config["stale"]:
                status = "OK"
            elif elapsed <= config["dead"]:
                status = "STALE"
            else:
                status = "DEAD"
                
            status_report[channel] = {
                "last_publish_time": last_time,
                "staleness_sec": round(elapsed, 2),
                "status": status,
                "thresholds": config
            }
        return status_report


def process_channel_message(channel: str, message: dict):
    with lock:
        last_publish_times[channel] = time.time()
    logger.info(f"Received message on {channel}, updated last publish time.")


def start_subscriptions():
    pubsub = RedisPubSub()
    logger.info("Subscribing to critical channels...")
    
    # Subscribe to each critical channel
    pubsub.subscribe("channel_live_odds", lambda m: process_channel_message("channel_live_odds", m))
    pubsub.subscribe("channel_true_projections", lambda m: process_channel_message("channel_true_projections", m))
    pubsub.subscribe("channel_ev_alerts", lambda m: process_channel_message("channel_ev_alerts", m))
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except Exception as e:
        logger.error(f"Subscription loop encountered error: {e}")
        pubsub.close()


def run_heartbeat():
    pubsub = None
    logger.info("Starting health watchdog heartbeat loop...")
    while True:
        try:
            if pubsub is None:
                pubsub = RedisPubSub()
                
            now = time.time()
            with lock:
                current_pub_times = dict(last_publish_times)
                
            for channel, config in THRESHOLDS.items():
                last_time = current_pub_times[channel]
                elapsed = now - last_time
                
                if elapsed <= config["stale"]:
                    status = "OK"
                elif elapsed <= config["dead"]:
                    status = "STALE"
                else:
                    status = "DEAD"
                
                health_msg = {
                    "channel": channel,
                    "staleness_ms": int(elapsed * 1000),
                    "status": status,
                    "timestamp": now
                }
                
                logger.info(f"Publishing health state for {channel}: {status} ({round(elapsed, 1)}s stale)")
                pubsub.publish("channel_system_health", health_msg)
                
        except Exception as e:
            logger.error(f"Error in watchdog heartbeat thread: {e}")
            pubsub = None  # Force reconnection next loop
            
        time.sleep(30)


if __name__ == '__main__':
    # Start subscription listener thread
    sub_thread = threading.Thread(target=start_subscriptions, daemon=True)
    sub_thread.start()
    
    # Start heartbeat publisher thread
    heartbeat_thread = threading.Thread(target=run_heartbeat, daemon=True)
    heartbeat_thread.start()
    
    logger.info("Agent 22 (Data Watchdog) started.")
    uvicorn.run(app, host="0.0.0.0", port=8018)
