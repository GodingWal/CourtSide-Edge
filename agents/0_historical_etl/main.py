"""Agent 0: Historical ETL — real WNBA history into SQLite.

Backfills final-game player box scores from ESPN's public API for the
configured seasons, then keeps them current with a nightly job. Recomputes
rolling baselines (L5/L10 minutes & usage) that the projection stack reads.
Resumable: ingested dates are tracked, so restarts skip completed work.
"""
import os
import time
from datetime import date, datetime, timedelta

import schedule

from database import get_connection, init_db
from shared.base_agent import setup_logging
from shared.espn_client import get_boxscore_player_stats, get_scoreboard
from stats_publisher import publish_stats_snapshot

logger = setup_logging("Agent0_ETL")

# Seasons to backfill (WNBA regular season + playoffs ≈ May–October).
SEASONS = [int(y) for y in os.getenv("HISTORY_SEASONS", "2024,2025,2026").split(",")]
REQUEST_PAUSE = float(os.getenv("ETL_REQUEST_PAUSE", "0.3"))


def ensure_etl_tables():
    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS etl_ingested_dates (date TEXT PRIMARY KEY, games INTEGER, ingested_at TEXT)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pbs_unique ON player_box_scores(game_id, player_id)"
    )
    conn.commit()
    conn.close()


def season_dates(year: int):
    start = date(year, 5, 1)
    end = min(date(year, 10, 31), date.today() - timedelta(days=1))
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def ingest_date(d: date) -> int | None:
    """Ingest all FINAL games for one calendar date.

    Returns games stored, or None when the scoreboard fetch failed - the
    caller must NOT mark the date as ingested in that case, or a transient
    ESPN outage becomes a permanent hole in the training history.
    """
    datestr = d.strftime("%Y%m%d")
    scoreboard = get_scoreboard(datestr, none_on_error=True)
    if scoreboard is None:
        return None
    games = [g for g in scoreboard if g["state"] == "FINAL" and g.get("espn_id")]
    if not games:
        return 0

    conn = get_connection()
    stored = 0
    for game in games:
        time.sleep(REQUEST_PAUSE)
        rows = get_boxscore_player_stats(game["espn_id"])
        if not rows:
            continue

        # Approximate usage rate per player from team scoring possessions.
        team_poss = {}
        for r in rows:
            poss = (r["fga"] or 0) + 0.44 * (r["fta"] or 0) + (r["turnovers"] or 0)
            team_poss[r["team"]] = team_poss.get(r["team"], 0) + poss

        for r in rows:
            opponent = game["home"] if r["team"] == game["away"] else game["away"]
            poss = (r["fga"] or 0) + 0.44 * (r["fta"] or 0) + (r["turnovers"] or 0)
            usage = round(100 * poss / team_poss[r["team"]], 2) if team_poss.get(r["team"]) else None
            # Upsert (unique on game_id+player_id) so re-ingesting a date
            # heals rows stored by an older parser instead of skipping them.
            conn.execute(
                """INSERT INTO player_box_scores
                   (player_id, player_name, game_id, date, team, opponent, minutes, points,
                    assists, rebounds, steals, blocks, turnovers, field_goals_made,
                    field_goals_attempted, threes_made, threes_attempted, free_throws_made,
                    free_throws_attempted, usage_rate)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(game_id, player_id) DO UPDATE SET
                     player_name=excluded.player_name, date=excluded.date,
                     team=excluded.team, opponent=excluded.opponent,
                     minutes=excluded.minutes, points=excluded.points,
                     assists=excluded.assists, rebounds=excluded.rebounds,
                     steals=excluded.steals, blocks=excluded.blocks,
                     turnovers=excluded.turnovers,
                     field_goals_made=excluded.field_goals_made,
                     field_goals_attempted=excluded.field_goals_attempted,
                     threes_made=excluded.threes_made,
                     threes_attempted=excluded.threes_attempted,
                     free_throws_made=excluded.free_throws_made,
                     free_throws_attempted=excluded.free_throws_attempted,
                     usage_rate=excluded.usage_rate""",
                (r["player_id"], r["player"], game["game_id"] + "_" + datestr, d.isoformat(),
                 r["team"], opponent, r["minutes"], r["points"], r["assists"], r["rebounds"],
                 r["steals"], r["blocks"], r["turnovers"], r["fgm"], r["fga"], r["tpm"],
                 r["tpa"], r["ftm"], r["fta"], usage),
            )
        stored += 1
    conn.commit()
    conn.close()
    return stored


def recompute_baselines():
    """Rolling L5/L10 minutes & usage per player from stored box scores."""
    conn = get_connection()
    cursor = conn.cursor()
    players = cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_box_scores WHERE player_id != ''"
    ).fetchall()
    today = date.today().isoformat()
    for player_id, player_name in players:
        rows = cursor.execute(
            """SELECT minutes, usage_rate FROM player_box_scores
               WHERE player_id = ? ORDER BY date DESC LIMIT 10""",
            (player_id,),
        ).fetchall()
        if not rows:
            continue
        mins = [r[0] for r in rows if r[0] is not None]
        usages = [r[1] for r in rows if r[1] is not None]
        l5_min = round(sum(mins[:5]) / len(mins[:5]), 2) if mins[:5] else None
        l5_usg = round(sum(usages[:5]) / len(usages[:5]), 2) if usages[:5] else None
        l10_usg = round(sum(usages) / len(usages), 2) if usages else None
        cursor.execute(
            """INSERT INTO rolling_baselines (player_id, player_name, last_updated, l5_minutes, l5_usage_rate, l10_usage_rate)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(player_id) DO UPDATE SET
                 player_name=excluded.player_name, last_updated=excluded.last_updated,
                 l5_minutes=excluded.l5_minutes, l5_usage_rate=excluded.l5_usage_rate,
                 l10_usage_rate=excluded.l10_usage_rate""",
            (player_id, player_name, today, l5_min, l5_usg, l10_usg),
        )
    conn.commit()
    conn.close()
    logger.info(f"Rolling baselines recomputed for {len(players)} players.")


def repair_corrupt_history():
    """Force re-ingest when stored rows are missing a stat the feed provides.

    An earlier parser missed ESPN's 'rebounds' key, so every historical row
    has NULL rebounds. When most rows with points lack rebounds, clear the
    ingested-dates markers — backfill() then re-fetches each date and the
    ON CONFLICT upsert heals the rows in place.
    """
    conn = get_connection()
    try:
        total, null_reb = conn.execute(
            """SELECT COUNT(*), SUM(CASE WHEN rebounds IS NULL THEN 1 ELSE 0 END)
               FROM player_box_scores WHERE points IS NOT NULL"""
        ).fetchone()
        if total and null_reb and null_reb / total > 0.5:
            logger.warning(
                f"{null_reb}/{total} stored box-score rows are missing rebounds — "
                "clearing ingest markers to re-fetch and heal history."
            )
            conn.execute("DELETE FROM etl_ingested_dates")
            conn.commit()
    finally:
        conn.close()


def backfill():
    """Resumable multi-season backfill. Skips dates already ingested."""
    ensure_etl_tables()
    repair_corrupt_history()
    conn = get_connection()
    done = {row[0] for row in conn.execute("SELECT date FROM etl_ingested_dates").fetchall()}
    conn.close()

    total_games = 0
    for year in SEASONS:
        season_count = 0
        for d in season_dates(year):
            if d.isoformat() in done:
                continue
            try:
                games = ingest_date(d)
            except Exception as e:
                logger.error(f"Failed to ingest {d}: {e}")
                continue
            if games is None:
                logger.warning(f"Scoreboard fetch failed for {d} - will retry on next run.")
                continue
            conn = get_connection()
            conn.execute(
                """INSERT INTO etl_ingested_dates (date, games, ingested_at) VALUES (?,?,?)
                   ON CONFLICT(date) DO UPDATE SET games=excluded.games, ingested_at=excluded.ingested_at""",
                (d.isoformat(), games, datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()
            season_count += games
            total_games += games
            if games:
                logger.info(f"Ingested {games} final games for {d} (season {year} total: {season_count}).")
            time.sleep(REQUEST_PAUSE)
        logger.info(f"Season {year} backfill complete: {season_count} games.")
    logger.info(f"Backfill finished: {total_games} new games stored.")
    if total_games:
        recompute_baselines()


def nightly_etl_job():
    logger.info("Starting nightly ETL job (yesterday's games + baselines)…")
    yesterday = date.today() - timedelta(days=1)
    try:
        games = ingest_date(yesterday)
        if games is None:
            raise RuntimeError("scoreboard fetch failed (will retry tomorrow's run)")
        conn = get_connection()
        conn.execute(
            """INSERT INTO etl_ingested_dates (date, games, ingested_at) VALUES (?,?,?)
               ON CONFLICT(date) DO UPDATE SET games=excluded.games, ingested_at=excluded.ingested_at""",
            (yesterday.isoformat(), games, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
        logger.info(f"Nightly ingest: {games} games for {yesterday}.")
    except Exception as e:
        logger.error(f"Nightly ingest failed: {e}")
    recompute_baselines()
    try:
        publish_stats_snapshot()
    except Exception as e:
        logger.error(f"Stats snapshot publish failed: {e}")
    logger.info("Nightly ETL job completed.")


def main():
    init_db()
    ensure_etl_tables()
    logger.info(f"Agent 0 (Historical ETL) started. Seasons: {SEASONS}")

    logger.info("Running historical backfill (resumable — skips ingested dates)…")
    backfill()

    # Publish the Stats Center snapshot from whatever history exists (also
    # refreshed by the nightly job after new games are ingested).
    try:
        publish_stats_snapshot()
    except Exception as e:
        logger.error(f"Stats snapshot publish failed: {e}")

    # Nightly refresh at 09:00 UTC (4:00 AM CST)
    schedule.every().day.at("09:00").do(nightly_etl_job)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
