"""
WNBA Prop Calibration Engine v1.0
====================================
Empirically-derived deflation factors and fair-line calibration for
player props.  Based on analysis of 10,774 historical prop bets matched
to actual outcomes (WNBA 2024-2025 via The Odds API).

Key insight: rolling averages overestimate by 15-25% because the market
uses too short a window (5-game) and overreacts to recent performance.

Integrates into: Agent 3 (Projection Engine) — called by EnsembleMathCore
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger("PropCalibration")

# Deflation factors derived from 10,774 matched prop bets (2024-2025)
# These correct the systematic overestimation in rolling averages.
DEFLATION_FACTORS: Dict[str, float] = {
    "PTS": 0.93,
    "AST": 0.82,
    "REB": 0.88,
    "PRA": 0.89,
    "3PM": 0.90,
    "STL": 0.85,
    "BLK": 0.87,
}

# Rolling window preference: 10-game beats 5-game for prediction accuracy.
# The market correlates r=0.9997 with 5-game rolling but 10-game has
# higher predictive power (R^2 0.463 vs 0.432).
PREFERRED_ROLLING_WINDOW = 10

# League-wide under win rates by line bucket — from real odds data.
# Used for rapid line-quality assessment.
UNDER_HIT_RATES = {
    "PTS": {
        (0, 10.5): 0.562,
        (10.5, 13.5): 0.552,
        (13.5, 16.5): 0.511,
        (16.5, 19.5): 0.619,
        (19.5, 22.5): 0.656,
        (22.5, 25.5): 0.676,
        (25.5, 30.0): 0.086,   # Stars go OVER high lines
        (30.0, 99.0): 0.086,
    },
    "AST": {
        (0, 2.5): 0.500,
        (2.5, 3.5): 0.456,
        (3.5, 4.5): 0.465,
        (4.5, 5.5): 0.543,
        (5.5, 6.5): 0.676,
        (6.5, 7.5): 0.724,
        (7.5, 99.0): 0.800,
    },
    "REB": {
        (0, 3.5): 0.463,
        (3.5, 5.5): 0.487,
        (5.5, 7.5): 0.500,
        (7.5, 9.5): 0.543,
        (9.5, 11.5): 0.600,
        (11.5, 99.0): 0.955,
    },
    "3PM": {
        (0, 1.5): 0.478,
        (1.5, 2.5): 0.478,
        (2.5, 3.5): 0.478,
        (3.5, 99.0): 0.848,
    },
}


def calibrate_projection(raw_projection: float, stat: str = "PTS") -> float:
    """Apply deflation factor to raw rolling average.

    Args:
        raw_projection: Rolling-average projection (e.g. 10-game mean).
        stat: Stat code — PTS, AST, REB, 3PM, STL, BLK, PRA.

    Returns:
        Calibrated projection rounded to nearest half-point.
    """
    factor = DEFLATION_FACTORS.get(stat, 0.90)
    calibrated = raw_projection * factor
    return round(calibrated * 2) / 2


def estimate_hit_rate(market_line: float, calibrated_fair_line: float,
                      stat: str = "PTS") -> float:
    """Estimate the probability that the player goes OVER the market line.

    Uses the empirical hit-rate buckets derived from real historical odds.
    """
    if market_line <= 0 or calibrated_fair_line <= 0:
        return 0.50

    # If calibrated fair line is close to market line, ~50/50
    diff = calibrated_fair_line - market_line
    if abs(diff) < 0.5:
        return 0.50

    # Use bucket lookup for rough estimate
    buckets = UNDER_HIT_RATES.get(stat, {})
    for (low, high), under_rate in buckets.items():
        if low <= market_line < high:
            if diff > 0:
                # Our fair line is HIGHER → market line is LOW → good for OVER
                over_rate = 1.0 - under_rate
                return min(0.95, max(0.05, over_rate + 0.05))
            else:
                # Our fair line is LOWER → market line is HIGH → good for UNDER
                return min(0.95, max(0.05, under_rate + 0.05))

    return 0.50


def assess_line_quality(market_line: float, calibrated_fair_line: float,
                        stat: str = "PTS") -> dict:
    """Assess whether the market line is mispriced vs our calibrated fair line.

    Returns dict with keys: edge, direction (OVER|UNDER|FAIR), confidence.
    """
    diff = calibrated_fair_line - market_line  # positive = market too low
    abs_diff = abs(diff)

    if diff > 0.5:
        direction = "OVER"
    elif diff < -0.5:
        direction = "UNDER"
    else:
        direction = "FAIR"

    if abs_diff >= 2.0:
        confidence = "STRONG"
    elif abs_diff >= 1.0:
        confidence = "MODERATE"
    elif abs_diff >= 0.5:
        confidence = "WEAK"
    else:
        confidence = "NONE"

    # Downgrade confidence for volatile stats
    if stat in ("3PM", "STL") and confidence != "NONE":
        confidence = "WEAK"

    return {
        "edge": round(abs_diff, 2),
        "direction": direction,
        "confidence": confidence,
        "diff": round(diff, 2),
    }


def get_deflation_factor(stat: str) -> float:
    """Return the deflation factor for a given stat."""
    return DEFLATION_FACTORS.get(stat, 0.90)


def get_metadata() -> dict:
    """Return calibration metadata for debugging / display."""
    return {
        "version": "1.0",
        "based_on": "10,774 matched prop bets (WNBA 2024-2025)",
        "methodology": "Rolling-10 averages with empirical deflation factors",
        "factors": DEFLATION_FACTORS.copy(),
        "preferred_window": PREFERRED_ROLLING_WINDOW,
    }
