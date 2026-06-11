"""Agent 12: Alpha Sandbox — interactive quant analysis chat.

Bridges the dashboard's sandbox chat to the local Nemotron model on this GPU
box. Requests arrive on the Redis list sandbox:requests (pushed by the web
server); replies are written to sandbox:response:<id> with a short TTL.
"""
import json
import time

from shared.base_agent import setup_logging
from shared.espn_client import get_scoreboard
from shared.redis_client import RedisPubSub
from infrastructure.nemotron.client import NemotronClient

logger = setup_logging("Agent12_AlphaSandbox")

SYSTEM_PROMPT = (
    "You are Agent 12, the quantitative signal-discovery analyst of CourtSideEdge, "
    "a WNBA betting analytics terminal. Answer questions about WNBA matchups, player "
    "props, pace, fatigue, referee impact and betting edges. Be concise and concrete: "
    "use short paragraphs or bullet points. When you lack live data for a claim, say so "
    "plainly rather than inventing numbers."
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
            parts.append("Today's WNBA games:\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch scoreboard for context: {e}")
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
    nemotron = NemotronClient()
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
            prompt = f"Live market context:\n{context}\n\nAnalyst question: {message}"
            started = time.time()
            try:
                reply = nemotron.ask(prompt, system=SYSTEM_PROMPT, temperature=0.4)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                reply = "Analysis engine error — the local model did not return a response. Try again."
            elapsed = round(time.time() - started, 2)

            pubsub.client.set(
                f"sandbox:response:{req_id}",
                json.dumps({"reply": reply, "elapsed_seconds": elapsed, "model": "local-nemotron"}),
                ex=180,
            )
            logger.info(f"Replied to {req_id[:8]}… in {elapsed}s")
        except Exception as e:
            logger.error(f"Sandbox loop error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
