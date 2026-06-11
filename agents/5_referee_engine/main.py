"""Agent 5: Referee Tendency Engine — real crews, data-derived tendencies.

Builds a per-referee game log (total points & fouls of every final game each
official worked) from ESPN's public API, then publishes tendencies for today's
real crews relative to league averages. Games whose officials ESPN hasn't
posted yet, and referees without enough sample, produce no output — the agent
never invents crews or tendencies.
"""
import os
import time
from datetime import date, timedelta

from shared.base_agent import db_connect, db_transaction, setup_logging, run_polling_loop
from shared.context_client import ContextClient
from shared.espn_client import get_boxscore_fouls, get_game_officials, get_scoreboard
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent5_RefereeEngine")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

BACKFILL_DAYS = int(os.getenv("REF_BACKFILL_DAYS", "60"))
POLL_SECONDS = int(os.getenv("REF_POLL_SECONDS", "1800"))
REQUEST_PAUSE = float(os.getenv("REF_REQUEST_PAUSE", "0.3"))
MIN_REF_GAMES = int(os.getenv("REF_MIN_GAMES", "3"))

context = ContextClient()


def ensure_tables():
    with db_transaction(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS referee_game_log (
                 espn_id TEXT, referee TEXT, game_id TEXT, date TEXT,
                 total_points INTEGER, total_fouls INTEGER,
                 PRIMARY KEY (espn_id, referee))"""
        )


def ingest_final_game(game) -> int:
    """Store one (referee, game) row per official of a FINAL game."""
    espn_id = game.get("espn_id")
    if not espn_id:
        return 0
    officials = get_game_officials(espn_id)
    if not officials:
        return 0
    try:
        total_points = int(game.get("home_score") or 0) + int(game.get("away_score") or 0)
    except (TypeError, ValueError):
        return 0
    if total_points <= 0:
        return 0
    fouls_rows = get_boxscore_fouls(espn_id)
    total_fouls = sum(r["fouls"] for r in fouls_rows) if fouls_rows else None

    with db_transaction(DB_PATH) as conn:
        for ref in officials:
            conn.execute(
                """INSERT INTO referee_game_log
                   (espn_id, referee, game_id, date, total_points, total_fouls)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(espn_id, referee) DO NOTHING""",
                (espn_id, ref, game["game_id"], date.today().isoformat(), total_points, total_fouls),
            )
    return len(officials)


def backfill_ref_logs():
    """Build the referee log from recent final games (skips known games)."""
    conn = db_connect(DB_PATH)
    known = {row[0] for row in conn.execute("SELECT DISTINCT espn_id FROM referee_game_log").fetchall()}
    conn.close()

    ingested = 0
    for offset in range(BACKFILL_DAYS, 0, -1):
        d = date.today() - timedelta(days=offset)
        games = [g for g in get_scoreboard(d.strftime("%Y%m%d"))
                 if g["state"] == "FINAL" and g.get("espn_id") and g["espn_id"] not in known]
        for game in games:
            time.sleep(REQUEST_PAUSE)
            if ingest_final_game(game):
                ingested += 1
        time.sleep(REQUEST_PAUSE)
    logger.info(f"Referee log backfill complete: {ingested} new officiated games stored.")


def referee_profiles():
    """Per-referee tendencies vs. league average, from the real game log."""
    conn = db_connect(DB_PATH)
    try:
        league = conn.execute(
            "SELECT AVG(total_points), AVG(total_fouls) FROM "
            "(SELECT DISTINCT espn_id, total_points, total_fouls FROM referee_game_log) AS games"
        ).fetchone()
        if not league or league[0] is None:
            return {}, None
        league_points = float(league[0])

        profiles = {}
        for ref, games, avg_points, avg_fouls in conn.execute(
            """SELECT referee, COUNT(*), AVG(total_points), AVG(total_fouls)
               FROM referee_game_log GROUP BY referee"""
        ).fetchall():
            if games < MIN_REF_GAMES:
                continue
            pace_effect = round(float(avg_points) - league_points, 2)
            profiles[ref] = {
                "games": games,
                "fouls_per_40": round(float(avg_fouls), 1) if avg_fouls is not None else None,
                "pace_effect": pace_effect,
                "ou_tendency": "Over_Lean" if pace_effect > 2 else "Under_Lean" if pace_effect < -2 else "Neutral",
            }
        return profiles, league_points
    finally:
        conn.close()


def crew_tendencies(officials, profiles):
    """Sample-weighted crew aggregate; None when no official has enough data."""
    known = [profiles[o] for o in officials if o in profiles]
    if not known:
        return None, 0
    total_games = sum(p["games"] for p in known)
    pace = sum(p["pace_effect"] * p["games"] for p in known) / total_games
    fouls = [p for p in known if p["fouls_per_40"] is not None]
    fouls_per_40 = (
        round(sum(p["fouls_per_40"] * p["games"] for p in fouls) / sum(p["games"] for p in fouls), 1)
        if fouls else None
    )
    pace = round(pace, 2)
    return {
        "fouls_per_40": fouls_per_40,
        "pace_effect": pace,
        "ou_tendency": "Over_Lean" if pace > 2 else "Under_Lean" if pace < -2 else "Neutral",
        "refs_profiled": len(known),
        "refs_total": len(officials),
    }, total_games


def ingest_recent_finals():
    """Keep the log current: yesterday's and today's finished games."""
    for d in (date.today() - timedelta(days=1), date.today()):
        for game in get_scoreboard(d.strftime("%Y%m%d")):
            if game["state"] == "FINAL" and game.get("espn_id"):
                time.sleep(REQUEST_PAUSE)
                ingest_final_game(game)


def publish_today(pubsub):
    profiles, league_points = referee_profiles()
    if not profiles:
        logger.info("No referee history with sufficient sample yet — publishing nothing.")
        return

    for game in get_scoreboard():
        if game["state"] == "FINAL" or not game.get("espn_id"):
            continue
        time.sleep(REQUEST_PAUSE)
        officials = get_game_officials(game["espn_id"])
        if not officials:
            logger.info(f"Officials not yet posted for {game['game_id']} — skipping.")
            continue

        tendencies, sample = crew_tendencies(officials, profiles)
        if tendencies is None:
            logger.info(f"No profiled officials for {game['game_id']} crew {officials} — skipping.")
            continue

        confidence = round(min(0.5 + 0.01 * sample, 0.9), 2)
        payload = {
            "source": "Agent 5",
            "game_id": game["game_id"],
            "crew": officials,
            "tendencies": tendencies,
            "league_avg_total": round(league_points, 1),
            "confidence": confidence,
            "sample_size": sample,
            "decay_seconds": 7200,
            "timestamp": time.time(),
        }
        logger.info(f"Publishing referee context for {game['game_id']}: {tendencies} (crew: {officials})")
        pubsub.publish("channel_referee_context", payload)

        context.write_context(
            game_id=game["game_id"],
            agent_id="Agent_5",
            context_key="referee_foul_bias",
            context_value=tendencies,
            confidence=confidence,
            ttl_seconds=7200,
        )
        logger.info(f"  → Wrote referee context to shared store for game {game['game_id']}")


def main():
    pubsub = RedisPubSub()
    ensure_tables()
    logger.info("Agent 5 (Referee Tendency Engine) started. Backfilling real officiating history…")
    backfill_ref_logs()

    def cycle():
        ingest_recent_finals()
        publish_today(pubsub)

    run_polling_loop(task=cycle, interval=POLL_SECONDS, logger=logger)


if __name__ == "__main__":
    main()
