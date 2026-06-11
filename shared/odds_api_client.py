"""The Odds API (the-odds-api.com) client for real WNBA bookmaker odds.

Requires ODDS_API_KEY. Quota-aware: the free tier has ~500 credits/month, so
callers must poll conservatively (see agents/1). Remaining quota is logged
from response headers on every call. All failures degrade to empty results.
"""
import logging
import os

import requests

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_wnba"
REGION = "us"
TIMEOUT = 20

# Player-prop markets requested per event (availability depends on plan/books).
PROP_MARKETS = "player_points,player_rebounds,player_assists,player_threes"
PROP_STAT_MAP = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
}

logger = logging.getLogger("OddsApiClient")


def enabled() -> bool:
    return bool(ODDS_API_KEY)


def _get(path: str, params: dict):
    params = {"apiKey": ODDS_API_KEY, **params}
    try:
        resp = requests.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            logger.info(f"Odds API quota remaining: {remaining}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Odds API request failed ({path}): {e}")
        return None


def get_game_odds() -> list:
    """Featured game markets (h2h/spreads/totals) for upcoming WNBA games.

    Returns: [{event_id, home, away, commence, books: [{book, spread, total, h2h}]}]
    Cost: 1 credit per call (one region, featured markets).
    """
    data = _get(f"/sports/{SPORT}/odds", {
        "regions": REGION,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    })
    if not data:
        return []
    games = []
    for ev in data:
        books = []
        for bm in ev.get("bookmakers", []):
            entry = {"book": bm.get("title"), "spread": None, "total": None, "h2h": None}
            for market in bm.get("markets", []):
                outcomes = market.get("outcomes", [])
                if market.get("key") == "totals" and outcomes:
                    entry["total"] = outcomes[0].get("point")
                elif market.get("key") == "spreads" and outcomes:
                    home_outcome = next((o for o in outcomes if o.get("name") == ev.get("home_team")), outcomes[0])
                    entry["spread"] = home_outcome.get("point")
                elif market.get("key") == "h2h" and outcomes:
                    entry["h2h"] = {o.get("name"): o.get("price") for o in outcomes}
            books.append(entry)
        games.append({
            "event_id": ev.get("id"),
            "home": ev.get("home_team"),
            "away": ev.get("away_team"),
            "commence": ev.get("commence_time"),
            "books": books,
        })
    return games


def get_player_props(event_id: str) -> list:
    """Player props for one event.

    Returns: [{player, stat, line, over_odds, under_odds, book}]
    Cost: scales with markets requested — use sparingly.
    """
    data = _get(f"/sports/{SPORT}/events/{event_id}/odds", {
        "regions": REGION,
        "markets": PROP_MARKETS,
        "oddsFormat": "american",
    })
    if not data:
        return []
    props = {}
    for bm in data.get("bookmakers", []):
        book = bm.get("title")
        for market in bm.get("markets", []):
            stat = PROP_STAT_MAP.get(market.get("key"))
            if not stat:
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description")
                if not player:
                    continue
                key = (player, stat)
                entry = props.setdefault(key, {
                    "player": player, "stat": stat, "line": outcome.get("point"),
                    "over_odds": None, "under_odds": None, "book": book,
                })
                if outcome.get("name") == "Over":
                    entry["over_odds"] = outcome.get("price")
                    entry["line"] = outcome.get("point")
                elif outcome.get("name") == "Under":
                    entry["under_odds"] = outcome.get("price")
    return list(props.values())
