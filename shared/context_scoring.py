"""
Context Scoring Module v1.0
============================
Combines pace + rest + home/away into a single situational adjustment.

Key findings from 10,774 matched prop bets:
  - Opponent pace is the #1 situational factor (+2.0 pts Q4 vs Q1 pace).
  - Home court advantage is NEGLIGIBLE (Cohen's d = 0.012).
  - Rest advantage is effectively ZERO (+0.006 pts/day).
  - B2B effects are context-dependent (see b2b_context module).

Integrates into: Agent 3 (Projection Engine), Agent 24 (Validation Gate)
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("ContextScoring")

# Pace quartile effects (derived from real odds data)
PACE_QUARTILE_EFFECTS = {
    "Q1_slowest": -1.0,
    "Q2_slow": -0.4,
    "Q3_fast": +0.4,
    "Q4_fastest": +1.0,
}

# Pace thresholds (WNBA 2024-2025 possessions per game)
PACE_THRESHOLDS = {
    "Q1": 87.5,
    "Q2": 89.0,
    "Q3": 91.0,
}

# Rest category effects
REST_EFFECTS = {
    "b2b": -0.20,
    "1_day": +0.05,
    "2_days": +0.10,
    "3plus_days": +0.15,
}

# Home court (negligible per analysis)
HOME_COURT_EFFECT = {
    "home": +0.10,
    "away": -0.10,
}

# Stat-specific multipliers for pace effect
PACE_STAT_MULTIPLIERS = {
    "PTS": 1.00,
    "AST": 0.60,
    "REB": -0.15,
    "PRA": 0.70,
    "3PM": 0.80,
    "STL": 0.40,
    "BLK": 0.20,
}


def get_pace_quartile(opp_pace: float) -> str:
    """Classify opponent pace into quartile."""
    if opp_pace < PACE_THRESHOLDS["Q1"]:
        return "Q1_slowest"
    if opp_pace < PACE_THRESHOLDS["Q2"]:
        return "Q2_slow"
    if opp_pace < PACE_THRESHOLDS["Q3"]:
        return "Q3_fast"
    return "Q4_fastest"


def calculate_pace_adjustment(opp_pace: float, stat: str = "PTS") -> float:
    """Calculate pace-based adjustment for a stat projection."""
    quartile = get_pace_quartile(opp_pace)
    base_effect = PACE_QUARTILE_EFFECTS[quartile]
    multiplier = PACE_STAT_MULTIPLIERS.get(stat, 1.0)
    return base_effect * multiplier


def calculate_rest_adjustment(rest_days: int, is_home: bool = True,
                               role: str = "starter", stat: str = "PTS") -> float:
    """Calculate rest-day adjustment."""
    if rest_days == 0:
        category = "b2b"
    elif rest_days == 1:
        category = "1_day"
    elif rest_days == 2:
        category = "2_days"
    else:
        category = "3plus_days"

    base_effect = REST_EFFECTS[category]

    # B2B is highly context-dependent
    if rest_days == 0:
        if is_home:
            base_effect = +0.90
        else:
            if role == "starter":
                base_effect = -0.40
            elif role == "bench":
                base_effect = +0.80
            else:
                base_effect = -0.15

    return base_effect


def calculate_context_score(opp_pace: float, rest_days: int,
                            is_home: bool, role: str = "starter",
                            stat: str = "PTS") -> dict:
    """Calculate composite context score for a player-game.

    Returns dict with keys: total_adjustment, breakdown, risk_flags.
    """
    pace_adj = calculate_pace_adjustment(opp_pace, stat)
    rest_adj = calculate_rest_adjustment(rest_days, is_home, role, stat)
    home_adj = HOME_COURT_EFFECT["home"] if is_home else HOME_COURT_EFFECT["away"]
    if stat == "REB":
        home_adj *= 1.5

    total = pace_adj + rest_adj + home_adj

    flags: List[str] = []
    if get_pace_quartile(opp_pace) == "Q1_slowest":
        flags.append("SLOW_PACE")
    if get_pace_quartile(opp_pace) == "Q4_fastest":
        flags.append("FAST_PACE")
    if rest_days == 0 and not is_home and role == "starter":
        flags.append("ROAD_B2B_STARTER")
    if rest_days == 0 and is_home:
        flags.append("HOME_B2B")

    return {
        "total_adjustment": round(total, 2),
        "breakdown": {
            "pace": round(pace_adj, 2),
            "rest": round(rest_adj, 2),
            "home": round(home_adj, 2),
        },
        "risk_flags": flags,
    }


def get_context_multiplier(opp_pace: float, rest_days: int,
                           is_home: bool, role: str = "starter",
                           stat: str = "PTS") -> float:
    """Return a simple multiplier (1.0 = neutral, >1.0 = favorable for overs)."""
    score = calculate_context_score(opp_pace, rest_days, is_home, role, stat)
    total = score["total_adjustment"]
    base = 15.0
    multiplier = (base + total) / base
    return max(0.85, min(1.15, multiplier))
