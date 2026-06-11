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
    "You are Agent 12, the senior quantitative analyst of CourtSideEdge, a WNBA "
    "betting analytics terminal focused on DFS pick'em props (PrizePicks/Underdog). "
    "The user message begins with a LIVE MARKET CONTEXT block — your real-time data "
    "feed: today's slate, live prop lines, the model's projections with edges, and "
    "the official injury report. Treat it as ground truth and cite specific numbers "
    "from it. Never say you lack data access if the context block contains data.\n\n"
    "How to answer:\n"
    "- Lead with your conclusion or pick, then the supporting numbers.\n"
    "- Quantify: cite the line, the projection, and the edge whenever they exist "
    "in the context; flag injury statuses that affect usage and minutes.\n"
    "- When asked for plays or angles, rank them by edge and explain the reasoning "
    "(usage redistribution, pace, matchup) in concrete terms.\n"
    "- Distinguish clearly between what the data shows and what is your inference.\n"
    "- Use short paragraphs or bullets; depth over filler. Only when the context "
    "block is empty AND the question needs live numbers should you say live data "
    "is unavailable."
)

MAX_PROP_LINES = 40
MAX_INJURY_ROWS = 25


def build_context(pubsub: RedisPubSub) -> str:
    """Real context for the model: today's slate, live props, the model's
    projections with edges, and the official injury report."""
    parts = []
    try:
        games = get_scoreboard()
        if games:
            lines = [
                f"- {g['away']} @ {g['home']} [{g['state']}]"
                + (f" O/U {g['odds']['over_under']}" if g.get("odds") and g["odds"].get("over_under") is not None else "")
                + (f" — {g['away_score']}-{g['home_score']}" if g["state"] != "PRE" and g.get("home_score") is not None else "")
                for g in games
            ]
            parts.append(f"Today's WNBA games ({len(games)} total, US/Eastern date):\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch scoreboard for context: {e}")

    # Model projections vs market lines (Agent 3) — the terminal's own edges.
    projections = {}
    try:
        raw = pubsub.client.hgetall("props:projections")
        for key, val in raw.items():
            try:
                projections[key] = json.loads(val)
            except (TypeError, ValueError):
                continue
        if projections:
            ranked = sorted(projections.values(), key=lambda p: abs(p.get("edge_vs_line") or 0), reverse=True)
            lines = [
                f"- {p['player']} {p['stat']}: line {p['market_line']} vs projection {p['projected_value']} "
                f"(edge {p['edge_vs_line']:+}, {p.get('games_sampled', '?')} games sampled)"
                for p in ranked[:MAX_PROP_LINES]
            ]
            parts.append("Model projections vs market lines (sorted by edge):\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch projections for context: {e}")

    # Live prop board (markets without a projection above, to avoid repeats).
    try:
        props = pubsub.client.hgetall("props:lines")
        lines = []
        for key, raw in props.items():
            try:
                prop = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if f"{prop.get('player')}|{prop.get('stat')}" in projections:
                continue
            lines.append(f"- {prop['player']} {prop['stat']} {prop['line']} @ {prop.get('book')} ({prop.get('game')})")
            if len(lines) >= MAX_PROP_LINES:
                break
        if lines:
            parts.append("Other live player prop lines (no model projection yet):\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch props for context: {e}")

    # Official injury report (cached by Agent 2 from ESPN).
    try:
        raw = pubsub.client.get("injuries:report")
        if raw:
            report = json.loads(raw)
            lines = [
                f"- {r['player']} ({r['team']}): {r['status']}"
                + (f" — {r['detail']}" if r.get("detail") else "")
                for r in report[:MAX_INJURY_ROWS]
            ]
            parts.append("Official injury report:\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not fetch injury report for context: {e}")

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
