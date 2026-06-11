import json
import os
import random
import time
import threading
from fastapi import Body, FastAPI, HTTPException
import uvicorn
from shared.redis_client import RedisPubSub
from infrastructure.hermes.client import HermesClient

from shared.base_agent import run_polling_loop, setup_logging

logger = setup_logging("Agent13_ParlayGenerator")

app = FastAPI(title="Agent 13: Matchup Oracle & Parlay Generator")

hermes = HermesClient()

# ── Pregame Window & Game Session Tracking ────────────────────────────────────
PREGAME_WINDOW_MIN = int(os.environ.get('PARLAY_PREGAME_WINDOW_MINUTES', '30'))

active_games = {}
state_lock = threading.Lock()


def process_game_active(msg):
    game_id = msg.get("gameId")
    tipoff = msg.get("tipoff")
    status = msg.get("status")
    if game_id:
        with state_lock:
            active_games[game_id] = {
                "gameId": game_id,
                "tipoff": tipoff,
                "status": status
            }
        logger.info(f"Stored status update: {game_id} -> {status} (tipoff: {tipoff})")


def start_subscriptions():
    pubsub = RedisPubSub()
    logger.info("Agent 13 subscribing to channel_game_active...")
    pubsub.subscribe("channel_game_active", process_game_active)
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except Exception as e:
        logger.error(f"Subscription loop failed: {e}")
        pubsub.close()


@app.on_event("startup")
def startup_event():
    # Start subscription listener in background thread
    threading.Thread(target=start_subscriptions, daemon=True).start()


def to_decimal(american):
    if american > 0:
        return (american / 100.0) + 1.0
    else:
        return (100.0 / abs(american)) + 1.0


def to_american(decimal):
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    else:
        return int(round(-100.0 / (decimal - 1.0)))


def generate_hermes_summary(legs, platform, multiplier):
    """Rationale for the entry. Uses the real local Hermes model when
    available; otherwise a purely factual description of the picks — never
    fabricated matchup analysis."""
    picks = " + ".join(
        f"{leg['player']} {leg['over_under']} {leg['line']} {leg['stat']} ({leg['opposing_team']})"
        for leg in legs
    )
    factual = f"{len(legs)}-pick power play on {platform}: {picks}; pays {multiplier}x."
    if hermes.simulated:
        return factual
    try:
        return hermes.ask(
            question=(
                f"Entry: {factual}\n"
                "Write a 2-3 sentence rationale for this WNBA pick'em entry. Base it ONLY on the "
                "lines given — do not invent injuries, matchup stats, or scheme details."
            ),
            system="You are the parlay analyst of a WNBA betting terminal. Be concise and concrete.",
            temperature=0.4,
        )
    except Exception as e:
        logger.error(f"Hermes summary failed, returning factual description: {e}")
        return factual


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "pregame_window_min": PREGAME_WINDOW_MIN,
        "tracked_games": list(active_games.values())
    }


# Published pick'em payout multipliers per platform, by number of picks
# (power-play style: every pick must hit). Update when the platforms change
# their payout tables.
PICKEM_MULTIPLIERS = {
    "PRIZEPICKS": {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 37.5},
    "UNDERDOG": {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 35.0},
}
DEFAULT_MULTIPLIERS = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 35.0}
MIN_LEGS, MAX_LEGS = 2, 6


def payout_multiplier(platform: str, num_legs: int) -> float:
    normalized = (platform or "").upper().replace(" ", "")
    table = next(
        (t for key, t in PICKEM_MULTIPLIERS.items() if normalized.startswith(key)),
        DEFAULT_MULTIPLIERS,
    )
    return table.get(num_legs, DEFAULT_MULTIPLIERS[num_legs])


@app.post("/api/parlay/generate")
def generate_parlay(payload: dict | None = Body(default=None)):
    # ── Requested entry size (2-6 picks) ──────────────────────────────────────
    if not isinstance(payload, dict):
        payload = {}
    try:
        num_legs = int(payload.get("legs", 2))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="legs must be an integer between 2 and 6.")
    if not MIN_LEGS <= num_legs <= MAX_LEGS:
        raise HTTPException(
            status_code=400,
            detail=f"legs must be between {MIN_LEGS} and {MAX_LEGS} (got {num_legs})."
        )

    # ── Pregame Window Gate Check ─────────────────────────────────────────────
    now = time.time()
    window_sec = PREGAME_WINDOW_MIN * 60
    
    with state_lock:
        games_in_window = []
        for g_id, game in active_games.items():
            if game["status"] == "PRE":
                time_to_tipoff = game["tipoff"] - now
                if 0 <= time_to_tipoff <= window_sec:
                    games_in_window.append(game)
                    
    # If no upcoming games are in the tipoff window, refuse parlay synthesis
    if not games_in_window:
        logger.warning("⛔ PARLAY SYNTHESIS BLOCKED: Outside pre-game window (no games scheduled in next 30 minutes)")
        raise HTTPException(
            status_code=400,
            detail=f"Parlay synthesis blocked: Outside of pre-game window. No games starting in the next {PREGAME_WINDOW_MIN} minutes."
        )

    logger.info(f"Generating parlay. Games in window: {[g['gameId'] for g in games_in_window]}")

    # Build legs from REAL player props cached by Agent 1 (The Odds API).
    # We never fabricate lines: if no live props exist, refuse with 503.
    try:
        pubsub = RedisPubSub()
        raw_props = pubsub.client.hgetall("props:lines")
        raw_projections = pubsub.client.hgetall("props:projections")
        pubsub.close()
    except Exception as e:
        logger.error(f"Failed to read live props from Redis: {e}")
        raw_props = {}
        raw_projections = {}

    props = []
    for raw in raw_props.values():
        try:
            props.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    # One prop per player; require a line and odds.
    by_player = {}
    for prop in props:
        if prop.get("line") is not None and prop.get("odds") is not None:
            by_player.setdefault(prop["player"], prop)
    candidates = list(by_player.values())

    if len(candidates) < num_legs:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Only {len(candidates)} live player props cached (need {num_legs}). "
                "The Odds API feed may be empty — try closer to game time or a smaller entry."
            )
        )

    # Entries can't mix platforms — build on the single pick'em platform
    # (PrizePicks or Underdog) with the deepest prop pool for this size.
    by_book = {}
    for c in candidates:
        by_book.setdefault(c.get("book") or "DFS", []).append(c)
    platform, pool = max(by_book.items(), key=lambda kv: len(kv[1]))
    if len(pool) < num_legs:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Largest single-platform pool ({platform}) has only {len(pool)} props; "
                f"a {num_legs}-pick entry can't mix platforms. Try a smaller entry."
            )
        )

    # Rank the pool by real edge where Agent 3 has cached a projection for the
    # market; props without a projection rank last (zero assumed edge).
    projections = {}
    for k, raw in raw_projections.items():
        try:
            projections[k] = json.loads(raw)
        except (TypeError, ValueError):
            continue

    def edge_for(prop):
        proj = projections.get(f"{prop['player']}|{prop['stat']}")
        if not proj or not prop.get("line"):
            return None
        return round((proj["projected_value"] - prop["line"]) / prop["line"] * 100, 2)

    ranked = sorted(pool, key=lambda p: abs(edge_for(p) or 0.0), reverse=True)
    have_projections = any(edge_for(p) is not None for p in pool)
    chosen = ranked[:num_legs] if have_projections else random.sample(pool, num_legs)

    multiplier = payout_multiplier(platform, num_legs)

    def to_leg(prop):
        edge = edge_for(prop)
        proj = projections.get(f"{prop['player']}|{prop['stat']}")
        return {
            "player": prop["player"],
            "team": prop.get("team", ""),
            "stat": prop["stat"],
            "line": prop["line"],
            # Side follows the projection when one exists; OVER otherwise.
            "over_under": "UNDER" if edge is not None and edge < 0 else "OVER",
            # Pick'em has no per-leg juice; represent as even-odds picks.
            "book_odds": 100,
            "true_odds": 0.5,
            "edge_pct": abs(edge) if edge is not None else 0.0,
            "projected_value": proj["projected_value"] if proj else None,
            "opposing_team": prop.get("game", ""),
            "book": prop.get("book"),
        }

    legs = [to_leg(prop) for prop in chosen]

    # Pick'em entry payout: fixed multiplier (e.g. 3x for a 2-pick power play),
    # expressed also as equivalent American odds for the UI.
    parlay_odds = to_american(multiplier)

    summary = generate_hermes_summary(legs, platform, multiplier)

    return {
        "legs": legs,
        "platform": platform,
        "payout_multiplier": multiplier,
        "parlay_odds": parlay_odds,
        "summary": summary
    }

if __name__ == "__main__":
    logger.info("Agent 13 (Parlay Generator) starting...")
    uvicorn.run(app, host="0.0.0.0", port=8009)
