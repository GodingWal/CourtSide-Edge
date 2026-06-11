"""Agent 22: Data Watchdog — real upstream liveness for the execution gate.

Every agent maintains a heartbeat:agent:<id> key in Redis (shared/base_agent),
refreshed every 30s with a 90s TTL. The watchdog derives the health of each
critical feed from its *producer's* heartbeat — not from message recency,
because feeds like live odds and projections legitimately go quiet when lines
aren't moving. Message timestamps are still tracked and reported as extra
telemetry.
"""
import os
import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop, HEARTBEAT_INTERVAL, HEARTBEAT_TTL

logger = setup_logging('Agent22_DataWatchdog')

app = FastAPI(title="Agent 22: CourtSideEdge Data Watchdog")

HEARTBEAT_STALE = float(os.getenv('WATCHDOG_HEARTBEAT_STALE_SEC', str(HEARTBEAT_INTERVAL * 2)))
HEARTBEAT_DEAD = float(os.getenv('WATCHDOG_HEARTBEAT_DEAD_SEC', str(HEARTBEAT_TTL)))

# Critical feed -> agent that produces it.
CHANNEL_PRODUCERS = {
    "channel_live_odds": "1",
    "channel_true_projections": "3",
}

# Last message seen per channel (telemetry only; quiet markets are normal).
startup_time = time.time()
last_publish_times = {channel: startup_time for channel in CHANNEL_PRODUCERS}

lock = threading.Lock()


def producer_status(client, agent_id: str) -> tuple[str, float]:
    """Health of one producing agent from its real Redis heartbeat."""
    try:
        raw = client.get(f"heartbeat:agent:{agent_id}")
    except Exception as e:
        logger.error(f"Failed to read heartbeat for agent {agent_id}: {e}")
        return "DEAD", float("inf")
    if raw is None:
        return "DEAD", float("inf")  # TTL expired: producer stopped beating
    elapsed = time.time() - int(raw)
    if elapsed <= HEARTBEAT_STALE:
        return "OK", elapsed
    if elapsed <= HEARTBEAT_DEAD:
        return "STALE", elapsed
    return "DEAD", elapsed


def build_status(client) -> dict:
    with lock:
        message_times = dict(last_publish_times)
    now = time.time()
    report = {}
    for channel, agent_id in CHANNEL_PRODUCERS.items():
        status, heartbeat_age = producer_status(client, agent_id)
        report[channel] = {
            "producer_agent": agent_id,
            "status": status,
            "heartbeat_age_sec": round(heartbeat_age, 2) if heartbeat_age != float("inf") else None,
            "last_message_sec": round(now - message_times[channel], 2),
        }
    return report


@app.get('/health')
def health_check():
    return {"status": "healthy"}


@app.get('/status')
def get_status():
    client = RedisPubSub()
    try:
        return build_status(client.client)
    finally:
        client.close()


def process_channel_message(channel: str, message: dict):
    with lock:
        last_publish_times[channel] = time.time()
    logger.info(f"Received message on {channel}, updated last publish time.")


def start_subscriptions():
    pubsub = RedisPubSub()
    logger.info("Subscribing to critical channels...")

    for channel in CHANNEL_PRODUCERS:
        pubsub.subscribe(channel, lambda m, c=channel: process_channel_message(c, m))

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

            report = build_status(pubsub.client)
            now = time.time()
            for channel, state in report.items():
                health_msg = {
                    "channel": channel,
                    "producer_agent": state["producer_agent"],
                    "heartbeat_age_sec": state["heartbeat_age_sec"],
                    "status": state["status"],
                    "timestamp": now,
                }
                logger.info(
                    f"Publishing health state for {channel}: {state['status']} "
                    f"(producer heartbeat {state['heartbeat_age_sec']}s old)"
                )
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
