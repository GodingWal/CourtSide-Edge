"""Shared odds-conversion helpers.

One canonical implementation for every agent (sizing, hedging, parlay,
CLV) instead of per-agent copies with diverging edge-case behavior.
American odds with |value| < 100 (including 0) are invalid and raise
ValueError so callers fail loudly instead of dividing by zero.
"""


def american_to_decimal(american: float) -> float:
    """Convert American odds to decimal odds (e.g. -110 -> 1.909, +150 -> 2.5)."""
    american = float(american)
    if abs(american) < 100:
        raise ValueError(f"Invalid American odds: {american}")
    if american > 0:
        return american / 100.0 + 1.0
    return 100.0 / abs(american) + 1.0


def decimal_to_american(decimal: float) -> int:
    """Convert decimal odds to (rounded) American odds."""
    decimal = float(decimal)
    if decimal <= 1.0:
        raise ValueError(f"Invalid decimal odds: {decimal}")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def implied_probability(american: float) -> float:
    """Implied win probability of American odds (vig included)."""
    american = float(american)
    if abs(american) < 100:
        raise ValueError(f"Invalid American odds: {american}")
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)
