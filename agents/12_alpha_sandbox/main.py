"""Agent 12: Alpha Sandbox — interactive quant analysis chat.

Bridges the dashboard's sandbox chat to the local Hermes model on this GPU
box. Requests arrive on the Redis list sandbox:requests (pushed by the web
server); replies are written to sandbox:response:<id> with a short TTL.
"""
import json
import time

from shared.base_agent import setup_logging
from shared.espn_client import get_scoreboard
from shared.redis_client import RedisPubSub
from infrastructure.hermes.client import HermesClient

logger = setup_logging("Agent12_AlphaSandbox")

SYSTEM_PROMPT = (
    "You are Agent 12, the quantitative signal-discovery analyst of CourtSideEdge, "
    "a WNBA betting analytics terminal. The user message begins with a LIVE MARKET "
    "CONTEXT block — that is your real-time data feed (today's games, scores, totals "
    "and player prop lines). Treat it as ground truth and cite it when answering. "
    "Never say you lack data access if the context block contains data. Be concise "
    "and concrete: short paragraphs or bullets. Only when the context block is empty "
    "AND the question needs live numbers should you say live data is unavailable."
)


def build_context(pubsub: RedisPubSub) -> str:
    """Real context for the model: today's games and any cached prop lines."""
    parts = []
    try:
        games = get_scoreboard()
        if games:
            lines = [
                f"- {g['away']} @ {g['home']} [{g['state']}]"
                + (f" O/U {g['odds']['over_under']}" if g.get("odds") and g["odds"].get("over_under") is not None else "")
                for g in games
            ]
            parts.append(f"Today's WNBA games ({len(games)} total, US/Eastern date):\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch scoreboard for context: {e}")

    # Bookmaker slate: distinct upcoming games that have live betting markets.
    try:
        raw = pubsub.client.hgetall("props:lines")
        market_games = sorted({json.loads(v).get("game") for v in raw.values() if v} - {None})
        if market_games:
            parts.append(
                f"Upcoming games with live betting markets ({len(market_games)}):\n"
                + "\n".join(f"- {g}" for g in market_games)
            )
    except Exception as e:
        logger.warning(f"Could not fetch market games for context: {e}")
    try:
        props = pubsub.client.hgetall("props:lines")
        if props:
            sample = list(props.items())[:12]
            lines = []
            for key, raw in sample:
                try:
                    prop = json.loads(raw)
                    lines.append(f"- {prop['player']} {prop['stat']} {prop['line']} ({prop.get('odds')}) @ {prop.get('book')}")
                except (TypeError, ValueError):
                    continue
            if lines:
                parts.append("Live player prop lines:\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch props for context: {e}")
    return "\n\n".join(parts) if parts else "No live games or prop lines available right now."


def main():
    pubsub = RedisPubSub()
    hermes = HermesClient()
    logger.info("Agent 12 (Alpha Sandbox) started. Waiting for chat requests…")

    while True:
        try:
            item = pubsub.client.blpop("sandbox:requests", timeout=10)
            if not item:
                continue
            _, raw = item
            request = json.loads(raw)
            req_id = request.get("id")
            message = (request.get("message") or "").strip()
            if not req_id or not message:
                continue
            logger.info(f"Sandbox question ({req_id[:8]}…): {message[:120]}")

            context = build_context(pubsub)
            prompt = (
                "=== LIVE MARKET CONTEXT (your real-time data feed) ===\n"
                f"{context}\n"
                "=== END CONTEXT ===\n\n"
                f"Analyst question: {message}"
            )
            started = time.time()
            try:
                reply = hermes.ask(prompt, system=SYSTEM_PROMPT, temperature=0.4)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                reply = "Analysis engine error — the local model did not return a response. Try again."
            elapsed = round(time.time() - started, 2)

            pubsub.client.set(
                f"sandbox:response:{req_id}",
                json.dumps({"reply": reply, "elapsed_seconds": elapsed, "model": "local-hermes"}),
                ex=180,
            )
            logger.info(f"Replied to {req_id[:8]}… in {elapsed}s")
        except Exception as e:
            logger.error(f"Sandbox loop error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
