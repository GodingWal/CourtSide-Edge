"""Odds arithmetic, pick'em entry pricing, and risk-policy stake sizing.

The headline number this package exists to make unavoidable: a 2-leg power play at 3x needs
**57.7%** per leg to break even, not 50%. Every pick'em dashboard that displays a 54% win
probability next to a green "value" badge is displaying a loss.
"""

from __future__ import annotations

from wnba_marketmath.odds import (
    american_to_decimal,
    american_to_probability,
    decimal_to_american,
    expected_value,
    remove_vig,
)
from wnba_marketmath.pickem import (
    PAYOUT_TABLES_ARE_UNVERIFIED,
    breakeven_uniform_leg_probability,
    entry_expected_value,
    implied_break_even_american,
    independent_correct_count_pmf,
    prizepicks_payout_table,
    underdog_payout_table,
)
from wnba_marketmath.sizing import RiskPolicy, growth_optimal_fraction, staked_fraction

__all__ = [
    "PAYOUT_TABLES_ARE_UNVERIFIED",
    "RiskPolicy",
    "american_to_decimal",
    "american_to_probability",
    "breakeven_uniform_leg_probability",
    "decimal_to_american",
    "entry_expected_value",
    "expected_value",
    "growth_optimal_fraction",
    "implied_break_even_american",
    "independent_correct_count_pmf",
    "prizepicks_payout_table",
    "remove_vig",
    "staked_fraction",
    "underdog_payout_table",
]

__version__ = "0.1.0"
