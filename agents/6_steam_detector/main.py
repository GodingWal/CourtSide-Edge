import time
from shared.redis_client import RedisPubSub

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging("Agent6_SteamDetector")

# Steam = the line moving STEAM_THRESHOLD+ units within STEAM_WINDOW_SECONDS.
STEAM_THRESHOLD = 0.5
STEAM_WINDOW_SECONDS = 120
HISTORY_CAP = 50


class SteamDetector:
    def __init__(self):
        self.line_history = {}

    def detect_steam(self, odds_data):
        """Track real line updates per market; flag fast aggregate movement.

        Returns a dict describing the move when line movement exceeds
        STEAM_THRESHOLD units within STEAM_WINDOW_SECONDS, else None.
        """
        line = odds_data.get("line", odds_data.get("over_under"))
        if line is None:
            return None
        market = odds_data.get("player") or odds_data.get("game_id")
        if not market:
            return None
        stat = odds_data.get("stat") or ("TOTAL" if odds_data.get("over_under") is not None else "LINE")
        # Per-book history: different books post different numbers for the
        # same market, and mixing them reads as phantom steam.
        book = odds_data.get("book") or odds_data.get("provider") or "consensus"
        key = f"{market}:{stat}:{book}"
        now = odds_data.get("timestamp", time.time())

        if key not in self.line_history and len(self.line_history) > 2000:
            cutoff = now - 24 * 3600
            self.line_history = {
                k: h for k, h in self.line_history.items() if h and h[-1][0] >= cutoff
            }
        history = self.line_history.setdefault(key, [])
        history.append((now, float(line)))
        del history[:-HISTORY_CAP]

        window = [(ts, ln) for ts, ln in history if now - ts <= STEAM_WINDOW_SECONDS]
        if len(window) < 2:
            return None
        first_line, last_line = window[0][1], window[-1][1]
        movement = last_line - first_line
        if abs(movement) < STEAM_THRESHOLD:
            return None
        return {
            "market": market,
            "stat": stat,
            "movement": round(movement, 2),
            "from_line": first_line,
            "to_line": last_line,
            "window_seconds": int(now - window[0][0]),
            "updates_in_window": len(window),
        }


def on_live_odds(message, pubsub, detector):
    steam = detector.detect_steam(message)
    if steam is None:
        return
    alert = {
        "source": "Agent 6",
        "type": "Steam_Move",
        "direction": "UP" if steam["movement"] > 0 else "DOWN",
        "market": steam["market"],
        "stat": steam["stat"],
        "movement": steam["movement"],
        "from_line": steam["from_line"],
        "to_line": steam["to_line"],
        "window_seconds": steam["window_seconds"],
        "sample_size": steam["updates_in_window"],
        "timestamp": time.time(),
    }
    logger.info(f"Steam detected! Publishing: {alert}")
    pubsub.publish("channel_steam_alerts", alert)


def main():
    pubsub = RedisPubSub()
    detector = SteamDetector()
    logger.info("Agent 6 (Line Steam Detector) started.")

    pubsub.subscribe("channel_live_odds", lambda m: on_live_odds(m, pubsub, detector))

    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
