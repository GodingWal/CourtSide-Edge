"""
Referee Crew Analysis v1.0
===========================
Crew-level referee tendency analysis for prop impact.

Integrates into: Agent 5 (Referee Engine), Agent 3 (Projection Engine)
"""
import hashlib
import json
import logging
import os

from shared.base_agent import db_connect
from shared.db import db_available

logger = logging.getLogger("RefereeCrew")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/hoopstats_wnba.db"))

# Prop impact by crew classification
CREW_IMPACT = {
    "tight": {"PTS": -0.8, "AST": -0.3, "REB": -0.5, "3PM": -0.2, "STL": 0.1, "BLK": 0.0},
    "loose": {"PTS": 1.2, "AST": 0.4, "REB": 0.3, "3PM": 0.3, "STL": -0.1, "BLK": -0.1},
    "neutral": {"PTS": 0.0, "AST": 0.0, "REB": 0.0, "3PM": 0.0, "STL": 0.0, "BLK": 0.0},
}


def get_crew_id(referees: list) -> str:
    """Deterministic crew ID from sorted ref names."""
    sorted_refs = sorted(r.lower().strip() for r in referees)
    return hashlib.sha256("|".join(sorted_refs).encode()).hexdigest()[:16]


def classify_crew(avg_total: float, avg_fouls: float, league_avg_total: float,
                  league_avg_fouls: float) -> str:
    """Classify crew as tight, loose, or neutral."""
    total_diff = (avg_total - league_avg_total) / league_avg_total if league_avg_total > 0 else 0
    fouls_diff = (avg_fouls - league_avg_fouls) / league_avg_fouls if league_avg_fouls > 0 else 0

    if fouls_diff > 0.03 and total_diff < 0.02:
        return "tight"
    if fouls_diff < -0.03 and total_diff > 0.02:
        return "loose"
    return "neutral"


def get_crew_adjustment(crew_classification: str, stat: str) -> float:
    """Get prop impact adjustment for a crew classification."""
    impacts = CREW_IMPACT.get(crew_classification, CREW_IMPACT["neutral"])
    return impacts.get(stat, 0.0)


def calculate_crew_adjustment(crew_pace_factor: float, crew_foul_rate: float,
                               stat: str) -> float:
    """Calculate combined crew adjustment."""
    base = 0.0
    if crew_pace_factor > 1.02:
        base += 0.5
    elif crew_pace_factor < 0.98:
        base -= 0.3
    if crew_foul_rate > 1.05 and stat in ("PTS", "AST"):
        base -= 0.3
    return round(base, 2)
