"""Blowout / minutes-risk adjustment (P1-3).

Heavy favorites sit their stars early; projected minutes (and therefore the
projection mean/std) are haircut pre-distribution. Extreme spreads or win
probabilities also flag the pick BLOWOUT_RISK, which escalates the publish
threshold for Buys on counting stats for favored-team starters. Underdog-team
players get no automatic haircut (garbage-time effects are ambiguous) but are
flagged for review.
"""
from shared.picks.config import load_config
from shared.picks.reason_codes import FLAG_BLOWOUT_RISK, FLAG_UNDERDOG_GARBAGE_TIME


def minutes_haircut(win_probability: float, config: dict | None = None) -> float:
    """Fractional minutes haircut for a player on a team with this win prob.

    0 below the start threshold, then linear from haircut_at_start to
    haircut_at_max between the start and max win probabilities.
    """
    cfg = (config or load_config())["blowout"]
    start, top = cfg["haircut_win_prob_start"], cfg["haircut_win_prob_max"]
    h_start, h_max = cfg["haircut_at_start"], cfg["haircut_at_max"]
    if win_probability < start:
        return 0.0
    if win_probability >= top:
        return h_max
    frac = (win_probability - start) / (top - start)
    return h_start + frac * (h_max - h_start)


def apply_haircut(
    mean: float, std: float, win_probability: float, config: dict | None = None
) -> tuple[float, float]:
    """Scale a projection distribution for blowout minutes risk.

    Applied pre-distribution in the projection layer: both the mean and the
    spread shrink proportionally with expected minutes.
    """
    haircut = minutes_haircut(win_probability, config)
    scale = 1.0 - haircut
    return mean * scale, std * scale


def is_blowout_game(spread: float, win_probability: float, config: dict | None = None) -> bool:
    cfg = (config or load_config())["blowout"]
    return abs(spread) >= cfg["flag_spread"] or win_probability >= cfg["flag_win_prob"]


def blowout_flags(
    spread: float,
    team_win_probability: float,
    config: dict | None = None,
) -> tuple[str, ...]:
    """Flags for a pick given its game script. win prob is the player's team's."""
    cfg = config or load_config()
    favorite_win_prob = max(team_win_probability, 1.0 - team_win_probability)
    if not is_blowout_game(spread, favorite_win_prob, cfg):
        return ()
    flags = [FLAG_BLOWOUT_RISK]
    if team_win_probability < 0.5:
        # Player is on the underdog side of a likely blowout: no automatic
        # haircut (garbage-time effects are ambiguous), flag for review.
        flags.append(FLAG_UNDERDOG_GARBAGE_TIME)
    return tuple(flags)
