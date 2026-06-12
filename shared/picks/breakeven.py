"""Payout-implied breakeven math (P1-2).

A pick'em entry with payout map {hits: multiplier} over n legs has, assuming
one shared per-leg hit probability p:

    EV(p) = sum_k C(n, k) * p^k * (1-p)^(n-k) * multiplier(k)

The per-leg breakeven is the p where EV(p) = 1 (stake returned). For a power
play (all-or-nothing multiplier m) this reduces to (1/m)^(1/n); flex payout
structures are solved numerically with the same function, so config-table
edits are the only thing required when a book changes its payouts.
"""
from math import comb

from shared.picks.config import load_config


def entry_ev(per_leg_p: float, legs: int, payout_map: dict) -> float:
    """Expected payout multiple of stake for an entry, legs independent."""
    ev = 0.0
    for hits_str, multiplier in payout_map.items():
        k = int(hits_str)
        ev += comb(legs, k) * per_leg_p**k * (1 - per_leg_p) ** (legs - k) * multiplier
    return ev


def breakeven_from_payout(legs: int, payout_map: dict) -> float:
    """Per-leg p where entry EV equals stake, via bisection on the monotone EV."""
    lo, hi = 1e-6, 1.0 - 1e-6
    for _ in range(80):
        mid = (lo + hi) / 2
        if entry_ev(mid, legs, payout_map) < 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def payout_map(book: str, entry_type: str, legs: int, config: dict | None = None) -> dict:
    cfg = config or load_config()
    try:
        return cfg["payouts"][book.lower()][entry_type.lower()][str(legs)]
    except KeyError as exc:
        raise KeyError(
            f"No payout table configured for ({book}, {entry_type}, {legs} legs)"
        ) from exc


def breakeven_probability(
    book: str, entry_type: str, legs: int, config: dict | None = None
) -> float:
    """Per-leg breakeven hit probability for (book, entry_type, legs)."""
    return breakeven_from_payout(legs, payout_map(book, entry_type, legs, config))


def publish_threshold(
    book: str,
    entry_type: str,
    legs: int,
    config: dict | None = None,
    escalation_pp: float = 0.0,
) -> float:
    """Breakeven + safety margin (+ any escalation), as a probability."""
    cfg = config or load_config()
    margin = cfg.get("publish_margin_pp", 2.0)
    return breakeven_probability(book, entry_type, legs, cfg) + (margin + escalation_pp) / 100.0
