"""Free, real WNBA data from ESPN's public site API.

No API key required. Used by the data-ingestion agents so the pipeline runs on
genuine schedule / odds / news / boxscore data instead of simulations.
Endpoints are unofficial but long-stable; every call degrades to None/[] on
failure so agents keep running through outages.
"""
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}
TIMEOUT = 15

logger = logging.getLogger("EspnClient")


def _get(url: str, params: dict | None = None):
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"ESPN request failed ({url}): {e}")
        return None


def get_scoreboard(date: str | None = None) -> list[dict]:
    """Today's WNBA games: id, teams, tipoff, live status, score, game odds.

    Returns a list of normalized games:
      {game_id, espn_id, home, away, tipoff (epoch), state (PRE|LIVE|FINAL),
       period, clock, home_score, away_score, odds: {spread, over_under, details} | None}
    """
    # WNBA schedule days are US/Eastern. Without an explicit date, pin the
    # request to today-in-ET so late-night UTC doesn't show the wrong slate.
    if date is None:
        date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    data = _get(f"{BASE}/scoreboard", params={"dates": date})
    if not data:
        return []
    games = []
    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            home_abbr = home.get("team", {}).get("abbreviation", "UNK")
            away_abbr = away.get("team", {}).get("abbreviation", "UNK")

            state = event.get("status", {}).get("type", {}).get("state", "pre")
            status = {"pre": "PRE", "in": "LIVE", "post": "FINAL"}.get(state, "PRE")

            odds = None
            for o in comp.get("odds", []) or []:
                odds = {
                    "provider": o.get("provider", {}).get("name"),
                    "details": o.get("details"),
                    "spread": o.get("spread"),
                    "over_under": o.get("overUnder"),
                }
                break

            tipoff = None
            try:
                tipoff = time.mktime(time.strptime(event.get("date", ""), "%Y-%m-%dT%H:%MZ")) - time.timezone
            except Exception:
                pass

            games.append({
                "game_id": f"{away_abbr}_{home_abbr}",
                "espn_id": event.get("id"),
                "home": home_abbr,
                "away": away_abbr,
                "tipoff": tipoff,
                "state": status,
                "period": event.get("status", {}).get("period"),
                "clock": event.get("status", {}).get("displayClock"),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "odds": odds,
            })
        except Exception as e:
            logger.warning(f"Failed to parse scoreboard event: {e}")
    return games


def get_news(limit: int = 20) -> list[dict]:
    """Latest WNBA news articles: {id, headline, description, published}."""
    data = _get(f"{BASE}/news", params={"limit": limit})
    if not data:
        return []
    articles = []
    for a in data.get("articles", []):
        articles.append({
            "id": a.get("dataSourceIdentifier") or a.get("guid") or a.get("links", {}).get("web", {}).get("href", a.get("headline")),
            "headline": a.get("headline", ""),
            "description": a.get("description", ""),
            "published": a.get("published"),
        })
    return articles


# ESPN's injuries feed names teams in full; the pipeline uses 3-letter codes.
TEAM_ABBR = {
    "Atlanta Dream": "ATL", "Chicago Sky": "CHI", "Connecticut Sun": "CON",
    "Dallas Wings": "DAL", "Golden State Valkyries": "GSV", "Indiana Fever": "IND",
    "Las Vegas Aces": "LVA", "Los Angeles Sparks": "LAX", "Minnesota Lynx": "MIN",
    "New York Liberty": "NYL", "Phoenix Mercury": "PHX", "Portland Fire": "POR",
    "Seattle Storm": "SEA", "Toronto Tempo": "TOR", "Washington Mystics": "WSH",
}


def get_injuries() -> list[dict]:
    """League-wide injury report: {player, team, status, detail, date}.

    Deterministic official data (no LLM extraction needed) — the source for
    the dashboard's live injury intel when no fresh news items exist.
    """
    data = _get(f"{BASE}/injuries")
    if not data:
        return []
    rows = []
    for team_entry in data.get("injuries", []) or []:
        team = (
            team_entry.get("team", {}).get("abbreviation")
            or team_entry.get("abbreviation")
            or team_entry.get("displayName", "UNK")
        )
        team = TEAM_ABBR.get(team, team)
        for injury in team_entry.get("injuries", []) or []:
            athlete = injury.get("athlete", {}) or {}
            player = athlete.get("displayName")
            if not player:
                continue
            status = injury.get("status") or injury.get("type", {}).get("description") or "Unknown"
            details = injury.get("details", {}) or {}
            detail_parts = [p for p in (details.get("type"), details.get("detail")) if p]
            rows.append({
                "player": player,
                "team": team,
                "status": str(status),
                "detail": " — ".join(detail_parts) or injury.get("shortComment") or "",
                "date": injury.get("date"),
            })
    return rows


def get_game_officials(espn_event_id: str) -> list[str]:
    """Real referee crew for a game (ESPN posts officials near tipoff)."""
    data = _get(f"{BASE}/summary", params={"event": espn_event_id})
    if not data:
        return []
    names = []
    for official in data.get("gameInfo", {}).get("officials", []) or []:
        name = official.get("displayName") or official.get("fullName")
        if name:
            names.append(name)
    return names


def get_boxscore_fouls(espn_event_id: str) -> list[dict]:
    """Per-player fouls for a live/final game: {player, team, fouls, minutes}."""
    data = _get(f"{BASE}/summary", params={"event": espn_event_id})
    if not data:
        return []
    out = []
    try:
        for team in data.get("boxscore", {}).get("players", []):
            team_abbr = team.get("team", {}).get("abbreviation", "UNK")
            for stat_group in team.get("statistics", []):
                keys = stat_group.get("keys", []) or stat_group.get("names", [])
                try:
                    fouls_idx = next(i for i, k in enumerate(keys) if "foul" in str(k).lower())
                except StopIteration:
                    continue
                try:
                    min_idx = next((i for i, k in enumerate(keys) if str(k).lower() in ("minutes", "min")), None)
                except StopIteration:
                    min_idx = None
                for athlete in stat_group.get("athletes", []):
                    stats = athlete.get("stats", [])
                    if not stats or len(stats) <= fouls_idx:
                        continue
                    try:
                        fouls = int(stats[fouls_idx])
                    except (ValueError, TypeError):
                        continue
                    out.append({
                        "player": athlete.get("athlete", {}).get("displayName", "Unknown"),
                        "team": team_abbr,
                        "fouls": fouls,
                        "minutes": stats[min_idx] if min_idx is not None and len(stats) > min_idx else None,
                    })
    except Exception as e:
        logger.warning(f"Failed to parse boxscore: {e}")
    return out


def get_boxscore_player_stats(espn_event_id: str) -> list[dict]:
    """Full per-player stat lines for a (final) game.

    Returns rows with: player_id, player, team, minutes, points, rebounds,
    assists, steals, blocks, turnovers, fgm, fga, tpm, tpa, ftm, fta, fouls.
    """
    data = _get(f"{BASE}/summary", params={"event": espn_event_id})
    if not data:
        return []

    def split_made_att(value):
        try:
            made, att = str(value).split("-")
            return int(made), int(att)
        except (ValueError, AttributeError):
            return None, None

    rows = []
    try:
        for team in data.get("boxscore", {}).get("players", []):
            team_abbr = team.get("team", {}).get("abbreviation", "UNK")
            for stat_group in team.get("statistics", []):
                keys = [str(k).upper() for k in (stat_group.get("keys") or stat_group.get("names") or [])]

                def idx(*names):
                    for n in names:
                        if n in keys:
                            return keys.index(n)
                    return None

                i_min, i_pts = idx("MIN", "MINUTES"), idx("PTS", "POINTS")
                i_reb, i_ast = idx("REB", "REBOUNDS", "TOTALREBOUNDS"), idx("AST", "ASSISTS")
                i_stl, i_blk = idx("STL", "STEALS"), idx("BLK", "BLOCKS")
                i_to, i_pf = idx("TO", "TURNOVERS"), idx("PF", "FOULS", "PERSONALFOULS")
                i_fg, i_3pt, i_ft = idx("FG", "FIELDGOALSMADE-FIELDGOALSATTEMPTED"), idx("3PT", "THREEPOINTFIELDGOALSMADE-THREEPOINTFIELDGOALSATTEMPTED"), idx("FT", "FREETHROWSMADE-FREETHROWSATTEMPTED")
                if i_pts is None:
                    continue

                for athlete in stat_group.get("athletes", []):
                    stats = athlete.get("stats", [])
                    if not stats or len(stats) <= i_pts:
                        continue  # DNP rows have empty stats

                    def num(i, cast=int):
                        if i is None or i >= len(stats):
                            return None
                        try:
                            return cast(str(stats[i]).replace("+", ""))
                        except (ValueError, TypeError):
                            return None

                    fgm, fga = split_made_att(stats[i_fg]) if i_fg is not None and i_fg < len(stats) else (None, None)
                    tpm, tpa = split_made_att(stats[i_3pt]) if i_3pt is not None and i_3pt < len(stats) else (None, None)
                    ftm, fta = split_made_att(stats[i_ft]) if i_ft is not None and i_ft < len(stats) else (None, None)

                    info = athlete.get("athlete", {})
                    rows.append({
                        "player_id": str(info.get("id", "")),
                        "player": info.get("displayName", "Unknown"),
                        "team": team_abbr,
                        "minutes": num(i_min, float),
                        "points": num(i_pts),
                        "rebounds": num(i_reb),
                        "assists": num(i_ast),
                        "steals": num(i_stl),
                        "blocks": num(i_blk),
                        "turnovers": num(i_to),
                        "fouls": num(i_pf),
                        "fgm": fgm, "fga": fga,
                        "tpm": tpm, "tpa": tpa,
                        "ftm": ftm, "fta": fta,
                    })
    except Exception as e:
        logger.warning(f"Failed to parse full boxscore: {e}")
    return rows
