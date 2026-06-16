"""
Recency Bias Module v1.0
=========================
Contrarian signals based on market overreaction to recent performance.

Key findings from 10,774 matched prop bets:
  - The market uses 5-game rolling (r=0.9997) but 10-game is more predictive.
  - After 25+ pt game: line spikes +1.7 pts, player regresses to mean.
    → UNDER hits 74% of the time, +40.4% ROI.
  - After 5+ ast game: UNDER ast hits 71%, +36.2% ROI (143 bets — best volume).
  - After 0-5 pt dud: OVER pts hits 67%, +28.0% ROI.
  - Streaks don't continue: after 2 consecutive overs, next goes under 56.3%.

Integrates into: Agent 3 (Projection Engine), Agent 11 (Market Value Detector)
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("RecencyBias")

# Contrarian rules with empirical performance
CONTRARIAN_RULES = {
    "under_pts_after_30plus": {
        "trigger": "prev_points >= 30",
        "action": "UNDER PTS",
        "win_rate": 1.00,
        "roi": 0.909,
        "sample": 22,
        "confidence": "HIGH",
    },
    "under_pra_after_25plus": {
        "trigger": "prev_points >= 25",
        "action": "UNDER PRA",
        "win_rate": 0.83,
        "roi": 0.580,
        "sample": 29,
        "confidence": "HIGH",
    },
    "under_3pm_after_4plus": {
        "trigger": "prev_threes >= 4",
        "action": "UNDER 3PM",
        "win_rate": 0.80,
        "roi": 0.535,
        "sample": 51,
        "confidence": "HIGH",
    },
    "under_pts_after_25plus": {
        "trigger": "prev_points >= 25",
        "action": "UNDER PTS",
        "win_rate": 0.74,
        "roi": 0.404,
        "sample": 68,
        "confidence": "VERY_HIGH",
    },
    "under_ast_after_5plus": {
        "trigger": "prev_assists >= 5",
        "action": "UNDER AST",
        "win_rate": 0.71,
        "roi": 0.362,
        "sample": 143,
        "confidence": "VERY_HIGH",
    },
    "over_pts_after_dud": {
        "trigger": "prev_points <= 5",
        "action": "OVER PTS",
        "win_rate": 0.67,
        "roi": 0.280,
        "sample": 88,
        "confidence": "HIGH",
    },
}

# Line spike amounts after big games (market overreaction)
LINE_SPIKES = {
    "pts_after_25plus": 1.7,
    "pts_after_30plus": 3.7,
    "ast_after_5plus": 0.5,
    "reb_after_10plus": 0.9,
}

# Streak effects
STREAK_EFFECTS = {
    "after_2_consecutive_overs": {
        "next_under_rate": 0.563,
        "recommendation": "FADE — bet UNDER",
    },
    "after_2_consecutive_unders": {
        "next_under_rate": 0.586,
        "recommendation": "Genuine slump — avoid or bet UNDER",
    },
}

# Props to avoid fading (market handles these correctly)
AVOID_FADE_STATS = {"REB", "BLK"}


def check_contrarian_signals(prev_points: Optional[float] = None,
                             prev_assists: Optional[float] = None,
                             prev_rebounds: Optional[float] = None,
                             prev_threes: Optional[float] = None,
                             consecutive_overs: int = 0,
                             consecutive_unders: int = 0,
                             is_b2b: bool = False) -> List[dict]:
    """Check all contrarian signals for a player.

    Returns list of active signals with action, confidence, and expected edge.
    """
    signals = []

    if prev_points is not None:
        if prev_points >= 30:
            signals.append({
                "rule": "under_pts_after_30plus",
                "action": "UNDER PTS",
                "confidence": "HIGH",
                "edge": 0.909,
                "note": f"Line inflated +{LINE_SPIKES['pts_after_30plus']:.1f} pts after 30+ game",
            })
        elif prev_points >= 25:
            signals.append({
                "rule": "under_pts_after_25plus",
                "action": "UNDER PTS",
                "confidence": "VERY_HIGH",
                "edge": 0.404,
                "note": f"Line inflated +{LINE_SPIKES['pts_after_25plus']:.1f} pts after 25+ game",
            })
        elif prev_points <= 5:
            signals.append({
                "rule": "over_pts_after_dud",
                "action": "OVER PTS",
                "confidence": "HIGH",
                "edge": 0.280,
                "note": "Bounce-back after dud game",
            })

    if prev_assists is not None and prev_assists >= 5:
        signals.append({
            "rule": "under_ast_after_5plus",
            "action": "UNDER AST",
            "confidence": "VERY_HIGH",
            "edge": 0.362,
            "note": f"Line inflated +{LINE_SPIKES['ast_after_5plus']:.1f} ast after 5+ ast game",
        })

    if prev_threes is not None and prev_threes >= 4:
        signals.append({
            "rule": "under_3pm_after_4plus",
            "action": "UNDER 3PM",
            "confidence": "HIGH",
            "edge": 0.535,
            "note": "Regression after hot shooting",
        })

    if consecutive_overs >= 2:
        signals.append({
            "rule": "fade_streak",
            "action": "FADE OVER",
            "confidence": "MODERATE",
            "edge": 0.10,
            "note": f"After {consecutive_overs} consecutive overs, streak regression likely",
        })

    if is_b2b and prev_points is not None and prev_points >= 25:
        signals.append({
            "rule": "b2b_fade",
            "action": "STRONG UNDER",
            "confidence": "HIGH",
            "edge": 0.50,
            "note": "Perfect storm: big game + B2B fatigue + inflated line",
        })

    return sorted(signals, key=lambda s: s["edge"], reverse=True)


def get_line_spike(stat: str, prev_value: float) -> float:
    """Estimate how much the market inflates the line after a big game."""
    if stat == "PTS":
        if prev_value >= 30:
            return LINE_SPIKES["pts_after_30plus"]
        if prev_value >= 25:
            return LINE_SPIKES["pts_after_25plus"]
    elif stat == "AST" and prev_value >= 5:
        return LINE_SPIKES["ast_after_5plus"]
    elif stat == "REB" and prev_value >= 10:
        return LINE_SPIKES["reb_after_10plus"]
    return 0.0


def should_fade(stat: str) -> bool:
    """Return whether a stat is safe to fade after big games."""
    return stat not in AVOID_FADE_STATS


def get_all_rules() -> dict:
    """Return all contrarian rules for reference."""
    return CONTRARIAN_RULES.copy()
