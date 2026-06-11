"""Stats snapshot publisher — aggregates real box scores into Redis.

The web tier runs on a different host than this agent's SQLite, so the
dashboard's Stats Center reads pre-aggregated snapshots from Redis:

  stats:teams     JSON map  team -> season aggregates + last-10 form
  stats:players   JSON list per-player season averages (+ last-5 scoring)
  stats:games     JSON list per-game team scores (head-to-head lookups)
  stats:gamelogs  Redis hash  player_name -> JSON list of game rows

Everything is derived from player_box_scores (Agent 0's ESPN ETL); nothing
is fabricated — empty tables publish nothing.
"""
import json
import time

from database import get_connection
from shared.base_agent import setup_logging
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent0_StatsPublisher")

SNAPSHOT_TTL = 26 * 3600  # refreshed nightly; survive a missed run


def _pct(made, attempted):
    return round(100 * made / attempted, 1) if attempted else None


def compute_team_stats(conn):
    """Per-team season aggregates from team-summed box scores."""
    games = {}
    for team, game_id, date, opponent, pts, reb, ast, stl, blk, tov, fgm, fga, tpm, tpa, ftm, fta in conn.execute(
        """SELECT team, game_id, date, opponent,
                  SUM(points), SUM(rebounds), SUM(assists), SUM(steals), SUM(blocks), SUM(turnovers),
                  SUM(field_goals_made), SUM(field_goals_attempted),
                  SUM(threes_made), SUM(threes_attempted),
                  SUM(free_throws_made), SUM(free_throws_attempted)
           FROM player_box_scores GROUP BY game_id, team"""
    ).fetchall():
        games.setdefault(game_id, {})[team] = {
            "date": date, "opponent": opponent, "pts": pts or 0, "reb": reb or 0,
            "ast": ast or 0, "stl": stl or 0, "blk": blk or 0, "tov": tov or 0,
            "fgm": fgm or 0, "fga": fga or 0, "tpm": tpm or 0, "tpa": tpa or 0,
            "ftm": ftm or 0, "fta": fta or 0,
        }

    teams: dict = {}
    game_rows = []
    for game_id, sides in games.items():
        if len(sides) != 2:
            continue  # partial boxscore — skip rather than fabricate a result
        (team_a, a), (team_b, b) = sides.items()
        game_rows.append({
            "game_id": game_id, "date": a["date"],
            "teams": {team_a: a["pts"], team_b: b["pts"]},
        })
        for team, own, opp in ((team_a, a, b), (team_b, b, a)):
            t = teams.setdefault(team, {
                "games": 0, "wins": 0, "losses": 0, "pts": 0, "opp_pts": 0,
                "reb": 0, "ast": 0, "stl": 0, "blk": 0, "tov": 0,
                "fgm": 0, "fga": 0, "tpm": 0, "tpa": 0, "ftm": 0, "fta": 0,
                "results": [],
            })
            t["games"] += 1
            t["wins" if own["pts"] > opp["pts"] else "losses"] += 1
            t["pts"] += own["pts"]
            t["opp_pts"] += opp["pts"]
            for k in ("reb", "ast", "stl", "blk", "tov", "fgm", "fga", "tpm", "tpa", "ftm", "fta"):
                t[k] += own[k]
            t["results"].append((own["date"], "W" if own["pts"] > opp["pts"] else "L"))

    snapshot = {}
    for team, t in teams.items():
        g = t["games"]
        last10 = [r for _, r in sorted(t["results"])[-10:]]
        snapshot[team] = {
            "team": team, "games": g, "wins": t["wins"], "losses": t["losses"],
            "ppg": round(t["pts"] / g, 1), "opp_ppg": round(t["opp_pts"] / g, 1),
            "net_ppg": round((t["pts"] - t["opp_pts"]) / g, 1),
            "rpg": round(t["reb"] / g, 1), "apg": round(t["ast"] / g, 1),
            "spg": round(t["stl"] / g, 1), "bpg": round(t["blk"] / g, 1),
            "topg": round(t["tov"] / g, 1),
            "fg_pct": _pct(t["fgm"], t["fga"]), "fg3_pct": _pct(t["tpm"], t["tpa"]),
            "ft_pct": _pct(t["ftm"], t["fta"]),
            "last10": f"{last10.count('W')}-{last10.count('L')}",
        }
    return snapshot, sorted(game_rows, key=lambda r: r["date"], reverse=True)


def compute_player_stats(conn):
    """Per-player season averages, latest team, and last-5 scoring."""
    players = []
    for row in conn.execute(
        """SELECT player_id, player_name, COUNT(DISTINCT game_id),
                  AVG(minutes), AVG(points), AVG(rebounds), AVG(assists),
                  AVG(steals), AVG(blocks), AVG(turnovers),
                  SUM(field_goals_made), SUM(field_goals_attempted),
                  SUM(threes_made), SUM(threes_attempted),
                  SUM(free_throws_made), SUM(free_throws_attempted),
                  AVG(usage_rate), MAX(date)
           FROM player_box_scores WHERE player_name IS NOT NULL
           GROUP BY player_id, player_name"""
    ).fetchall():
        (pid, name, gp, mpg, ppg, rpg, apg, spg, bpg, topg,
         fgm, fga, tpm, tpa, ftm, fta, usage, last_date) = row
        team_row = conn.execute(
            "SELECT team FROM player_box_scores WHERE player_id = ? ORDER BY date DESC LIMIT 1",
            (pid,),
        ).fetchone()
        last5 = [r[0] or 0 for r in conn.execute(
            "SELECT points FROM player_box_scores WHERE player_id = ? ORDER BY date DESC LIMIT 5",
            (pid,),
        ).fetchall()]
        players.append({
            "player_id": pid, "player": name,
            "team": team_row[0] if team_row else None,
            "gp": gp,
            "mpg": round(mpg or 0, 1), "ppg": round(ppg or 0, 1),
            "rpg": round(rpg or 0, 1), "apg": round(apg or 0, 1),
            "spg": round(spg or 0, 1), "bpg": round(bpg or 0, 1),
            "topg": round(topg or 0, 1),
            "fg_pct": _pct(fgm or 0, fga or 0), "fg3_pct": _pct(tpm or 0, tpa or 0),
            "ft_pct": _pct(ftm or 0, fta or 0),
            "usage": round(usage, 1) if usage is not None else None,
            "l5_ppg": round(sum(last5) / len(last5), 1) if last5 else None,
            "last_game": last_date,
        })
    players.sort(key=lambda p: p["ppg"], reverse=True)
    return players


def compute_gamelogs(conn):
    """Compact per-player game log (newest first) for matchup queries."""
    logs: dict = {}
    for row in conn.execute(
        """SELECT player_name, date, team, opponent, minutes, points, rebounds,
                  assists, steals, blocks, turnovers,
                  field_goals_made, field_goals_attempted,
                  threes_made, threes_attempted
           FROM player_box_scores WHERE player_name IS NOT NULL
           ORDER BY date DESC"""
    ).fetchall():
        (name, date, team, opp, minutes, pts, reb, ast, stl, blk, tov, fgm, fga, tpm, tpa) = row
        logs.setdefault(name, []).append({
            "date": date, "team": team, "opp": opp,
            "min": minutes, "pts": pts, "reb": reb, "ast": ast,
            "stl": stl, "blk": blk, "tov": tov,
            "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa,
        })
    return logs


def publish_stats_snapshot():
    """Aggregate the box-score history and publish snapshots to Redis."""
    conn = get_connection()
    try:
        teams, game_rows = compute_team_stats(conn)
        players = compute_player_stats(conn)
        logs = compute_gamelogs(conn)
    finally:
        conn.close()

    if not teams and not players:
        logger.info("No box-score history yet — publishing no stats snapshot.")
        return

    pubsub = RedisPubSub()
    try:
        updated = time.time()
        pubsub.client.set("stats:teams", json.dumps({"updated": updated, "teams": teams}), ex=SNAPSHOT_TTL)
        pubsub.client.set("stats:players", json.dumps({"updated": updated, "players": players}), ex=SNAPSHOT_TTL)
        pubsub.client.set("stats:games", json.dumps({"updated": updated, "games": game_rows}), ex=SNAPSHOT_TTL)
        if logs:
            pubsub.client.delete("stats:gamelogs")
            mapping = {name: json.dumps(rows) for name, rows in logs.items()}
            pubsub.client.hset("stats:gamelogs", mapping=mapping)
            pubsub.client.expire("stats:gamelogs", SNAPSHOT_TTL)
        logger.info(
            f"Published stats snapshot: {len(teams)} teams, {len(players)} players, "
            f"{len(game_rows)} games, {len(logs)} game logs."
        )
    finally:
        pubsub.close()
