def implied_probability(american_odds: int) -> float:
    """Converts American odds to implied probability."""
    if american_odds < 0:
        return -american_odds / (-american_odds + 100)
    else:
        return 100 / (american_odds + 100)

def calculate_ev(win_prob: float, american_odds: int) -> float:
    """Calculates the Expected Value (EV) of a bet."""
    decimal_odds = (1 / implied_probability(american_odds)) + 1 if american_odds > 0 else (1 / implied_probability(american_odds))
    profit_if_win = decimal_odds - 1
    return (win_prob * profit_if_win) - (1 - win_prob)

def kelly_criterion(win_prob: float, american_odds: int, kelly_multiplier: float = 0.25, max_wager_pct: float = 0.05) -> float:
    """
    Calculates the Kelly Criterion fraction for bankroll sizing.
    
    Args:
        win_prob: The Quant Agent's true probability of winning (0.0 to 1.0).
        american_odds: Sportsbook odds (e.g., -110, +150).
        kelly_multiplier: Fractional Kelly to reduce volatility (default 0.25 / Quarter Kelly).
        max_wager_pct: Hard cap on the maximum bankroll percentage per bet.
        
    Returns: 
        Float representing the percentage of the total bankroll to wager.
    """
    # b is the net fractional odds (Decimal odds - 1)
    b = (1 / implied_probability(american_odds)) - 1 if american_odds < 0 else (american_odds / 100)
    
    p = win_prob
    q = 1 - p
    
    # Standard Kelly Formula: f* = (bp - q) / b
    kelly_pct = (b * p - q) / b
    
    # If the edge is negative, the wager is 0
    if kelly_pct <= 0:
        return 0.0
        
    # Apply fractional multiplier and cap
    adjusted_kelly = kelly_pct * kelly_multiplier
    return min(adjusted_kelly, max_wager_pct)
