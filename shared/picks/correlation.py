"""Same-game correlation handling (P1-4).

Legs from the same game are not independent. Latent stat outcomes are tied
through a static correlation matrix by relationship type (config-driven), and
entry EV is computed from the joint hit probability via a Gaussian-copula
Monte Carlo — never the product of marginals when >= 2 legs share a game.

The latent correlations are over raw stat outcomes and are positive for
team-total / pace channels; hit-space effects fall out of the leg directions
(e.g. star-over + teammate-under on the same team becomes negatively
correlated in hit space because both hits need the shared latent factor to
move opposite ways).
"""
from math import sqrt

import numpy as np
from scipy.special import ndtri

from shared.picks.config import load_config
from shared.picks.models import Recommendation
from shared.picks.reason_codes import ReasonCode

DEFAULT_DRAWS = 20_000
_SEED = 20260611  # deterministic joint estimates run-to-run


def same_game(leg_a: dict, leg_b: dict) -> bool:
    if leg_a.get("game_id") and leg_b.get("game_id"):
        return leg_a["game_id"] == leg_b["game_id"]
    teams_a = {leg_a.get("team", ""), leg_a.get("opponent", "")} - {""}
    teams_b = {leg_b.get("team", ""), leg_b.get("opponent", "")} - {""}
    return bool(teams_a) and teams_a == teams_b


def latent_rho(leg_a: dict, leg_b: dict, config: dict | None = None) -> float:
    """Static latent correlation between two legs' stat outcomes."""
    cfg = (config or load_config())["correlation"]
    if not same_game(leg_a, leg_b):
        return 0.0
    if leg_a.get("team") and leg_a.get("team") == leg_b.get("team"):
        if leg_a.get("stat", "").upper() == leg_b.get("stat", "").upper():
            return cfg["same_team_same_stat"]
        return cfg["same_team_other_stat"]
    if leg_a.get("team") and leg_b.get("team"):
        return cfg["cross_team"]
    return cfg["default_same_game"]


def _correlation_matrix(legs: list[dict], config: dict | None = None) -> np.ndarray:
    n = len(legs)
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i, j] = matrix[j, i] = latent_rho(legs[i], legs[j], config)
    # Shrink toward identity until positive definite (static pairwise values
    # can be jointly inconsistent for larger entries).
    shrink = 1.0
    while shrink > 0.05:
        try:
            np.linalg.cholesky(matrix)
            return matrix
        except np.linalg.LinAlgError:
            shrink *= 0.9
            off = matrix - np.eye(n)
            matrix = np.eye(n) + off * shrink
    return np.eye(n)


def _leg_direction(leg: dict) -> str:
    rec = leg.get("recommendation", leg.get("direction", "Buy"))
    if isinstance(rec, Recommendation):
        return "over" if rec == Recommendation.BUY else "under"
    return "over" if str(rec).lower() in ("buy", "over") else "under"


def simulate_hits(legs: list[dict], config: dict | None = None,
                  draws: int = DEFAULT_DRAWS) -> np.ndarray:
    """(draws, n_legs) boolean matrix of correlated leg outcomes.

    Each leg needs `hit_probability` plus team/opponent/stat (or game_id)
    fields for relationship classification.
    """
    matrix = _correlation_matrix(legs, config)
    rng = np.random.default_rng(_SEED)
    z = rng.multivariate_normal(np.zeros(len(legs)), matrix, size=draws,
                                method="cholesky")
    hits = np.empty_like(z, dtype=bool)
    for i, leg in enumerate(legs):
        p = float(leg["hit_probability"])
        p = min(max(p, 1e-9), 1 - 1e-9)
        if _leg_direction(leg) == "over":
            hits[:, i] = z[:, i] > ndtri(1.0 - p)
        else:
            hits[:, i] = z[:, i] < ndtri(p)
    return hits


def joint_hit_probability(legs: list[dict], config: dict | None = None,
                          draws: int = DEFAULT_DRAWS) -> float:
    """P(all legs hit) under the correlated joint, not the marginal product."""
    if len(legs) == 1:
        return float(legs[0]["hit_probability"])
    hits = simulate_hits(legs, config, draws)
    p = float(hits.all(axis=1).mean())
    # Monte Carlo standard error bound, useful for tests/diagnostics.
    return round(p, 4)


def entry_ev(legs: list[dict], payout_map: dict, config: dict | None = None,
             draws: int = DEFAULT_DRAWS) -> float:
    """Expected payout multiple for an entry, correlation-adjusted.

    `payout_map` is {hits: multiplier} from the payouts config (power plays
    have a single all-legs entry; flex maps cover partial hits).
    """
    if any(same_game(a, b) for i, a in enumerate(legs) for b in legs[i + 1:]):
        hit_counts = simulate_hits(legs, config, draws).sum(axis=1)
        payouts = np.zeros(len(hit_counts))
        for hits_str, multiplier in payout_map.items():
            payouts[hit_counts == int(hits_str)] = multiplier
        return float(payouts.mean())
    # Independent legs: exact binomial-style EV over marginals.
    probs = [float(leg["hit_probability"]) for leg in legs]
    ev = 0.0
    n = len(legs)
    for outcome in range(2**n):
        hit_mask = [(outcome >> i) & 1 for i in range(n)]
        weight = 1.0
        for hit, p in zip(hit_mask, probs):
            weight *= p if hit else (1 - p)
        ev += weight * payout_map.get(str(sum(hit_mask)), 0.0)
    return ev


def check_entry_ev(legs: list[dict], payout_map: dict,
                   config: dict | None = None) -> dict:
    """Entry-level gate: reject entries whose correlation-adjusted EV is
    below breakeven (EV < 1.0 stake returned)."""
    ev = entry_ev(legs, payout_map, config)
    result = {"ev": round(ev, 4), "approved": ev >= 1.0}
    if not result["approved"]:
        result["reason_code"] = ReasonCode.CORRELATION_EV_FAIL.value
    return result


def mc_standard_error(p: float, draws: int = DEFAULT_DRAWS) -> float:
    return sqrt(p * (1 - p) / draws)
