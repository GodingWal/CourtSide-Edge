import os
import random
import time
import threading
from fastapi import FastAPI, HTTPException
import uvicorn
from shared.redis_client import RedisPubSub

from shared.base_agent import run_polling_loop, setup_logging, db_connect

logger = setup_logging("Agent13_ParlayGenerator")

app = FastAPI(title="Agent 13: Matchup Oracle & Parlay Generator")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

DEFAULT_PLAYERS = [
    {"name": "A'ja Wilson", "team": "LVA"},
    {"name": "Breanna Stewart", "team": "NYL"},
    {"name": "Caitlin Clark", "team": "IND"},
    {"name": "Sabrina Ionescu", "team": "NYL"},
    {"name": "Alyssa Thomas", "team": "CON"},
    {"name": "Kelsey Plum", "team": "LVA"},
    {"name": "Angel Reese", "team": "CHI"},
    {"name": "Arike Ogunbowale", "team": "DAL"},
]

STATS = [
    {"stat": "PTS", "line_min": 15.5, "line_max": 26.5},
    {"stat": "REB", "line_min": 6.5, "line_max": 11.5},
    {"stat": "AST", "line_min": 4.5, "line_max": 9.5},
]

TEAMS = ["LVA", "NYL", "SEA", "CON", "PHX", "IND", "CHI", "MIN", "DAL", "WSH", "ATL", "LAX"]

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


def get_active_players():
    if not os.path.exists(DB_PATH):
        logger.warning(f"Database not found at {DB_PATH}. Using default players.")
        return DEFAULT_PLAYERS
    
    try:
        conn = db_connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name, team FROM players WHERE status = 'ACTIVE'")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return DEFAULT_PLAYERS
        
        return [{"name": row[0], "team": row[1]} for row in rows]
    except Exception as e:
        logger.error(f"Error reading database: {e}")
        return DEFAULT_PLAYERS


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


def generate_nemotron_summary(leg1, leg2):
    templates = [
        "{p1}'s interior matchup against {team1}'s weak paint defense will drive heavy volume and high-efficiency looks. Concurrently, {p2} is positioned to exploit {team2}'s drop coverage scheme, leading to increased output that makes this two-leg combination highly positive EV.",
        "{p1} projects to see high usage against {team1}'s fast-paced transition play, magnifying the over value. Simultaneously, {p2}'s elite secondary playmaking will punish {team2}'s aggressive blitz packages, creating a strong correlated correlation between both legs.",
        "With {team1} struggling against perimeter pick-and-rolls, {p1} is in a prime spot to exceed lines in high-leverage possessions. Meanwhile, {p2} benefits from massive rebounding advantages against {team2}'s small-ball frontcourt, sealing a highly edge-rich pairing."
    ]
    template = random.choice(templates)
    return template.format(
        p1=leg1["player"], team1=leg1["opposing_team"],
        p2=leg2["player"], team2=leg2["opposing_team"]
    )


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "pregame_window_min": PREGAME_WINDOW_MIN,
        "tracked_games": list(active_games.values())
    }


@app.post("/api/parlay/generate")
def generate_parlay():
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

    players = get_active_players()
    if len(players) < 2:
        players = DEFAULT_PLAYERS
        
    p1, p2 = random.sample(players, 2)
    
    # Generate Leg 1
    s1 = random.choice(STATS)
    line1 = round(random.uniform(s1["line_min"], s1["line_max"]) * 2) / 2
    opp1 = random.choice([t for t in TEAMS if t != p1["team"]])
    odds1 = random.choice([-115, -110, -105, +100])
    edge1 = round(random.uniform(2.5, 9.5), 2)
    true_odds1 = round(1.0 / (to_decimal(odds1) - (edge1 / 100.0)), 2) if odds1 < 0 else round(1.0 / (to_decimal(odds1) - (edge1 / 100.0)), 2)
    # Ensure true odds is bounded
    true_odds1 = max(0.40, min(0.70, true_odds1))

    leg1 = {
        "player": p1["name"],
        "team": p1["team"],
        "stat": s1["stat"],
        "line": line1,
        "over_under": "OVER",
        "book_odds": odds1,
        "true_odds": true_odds1,
        "edge_pct": edge1,
        "opposing_team": opp1
    }
    
    # Generate Leg 2
    s2 = random.choice(STATS)
    line2 = round(random.uniform(s2["line_min"], s2["line_max"]) * 2) / 2
    opp2 = random.choice([t for t in TEAMS if t != p2["team"]])
    odds2 = random.choice([-115, -110, -105, +100])
    edge2 = round(random.uniform(2.5, 9.5), 2)
    true_odds2 = max(0.40, min(0.70, round(1.0 / (to_decimal(odds2) - (edge2 / 100.0)), 2)))

    leg2 = {
        "player": p2["name"],
        "team": p2["team"],
        "stat": s2["stat"],
        "line": line2,
        "over_under": "OVER",
        "book_odds": odds2,
        "true_odds": true_odds2,
        "edge_pct": edge2,
        "opposing_team": opp2
    }
    
    # Calculate Parlay Odds
    dec_odds1 = to_decimal(odds1)
    dec_odds2 = to_decimal(odds2)
    combined_dec = dec_odds1 * dec_odds2
    parlay_odds = to_american(combined_dec)
    
    summary = generate_nemotron_summary(leg1, leg2)
    
    return {
        "legs": [leg1, leg2],
        "parlay_odds": parlay_odds,
        "summary": summary
    }

if __name__ == "__main__":
    logger.info("Agent 13 (Parlay Generator) starting...")
    uvicorn.run(app, host="0.0.0.0", port=8009)
