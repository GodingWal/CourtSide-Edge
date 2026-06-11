"""Free, real WNBA data from ESPN's public site API.

No API key required. Used by the data-ingestion agents so the pipeline runs on
genuine schedule / odds / news / boxscore data instead of simulations.
Endpoints are unofficial but long-stable; every call degrades to None/[] on
failure so agents keep running through outages.
"""
import logging
import time

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


def get_scoreboard() -> list[dict]:
    """Today's WNBA games: id, teams, tipoff, live status, score, game odds.

    Returns a list of normalized games:
      {game_id, espn_id, home, away, tipoff (epoch), state (PRE|LIVE|FINAL),
       period, clock, home_score, away_score, odds: {spread, over_under, details} | None}
    """
    data = _get(f"{BASE}/scoreboard")
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
