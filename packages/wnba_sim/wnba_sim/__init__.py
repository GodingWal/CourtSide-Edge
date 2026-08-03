"""Correlated simulation of entry outcomes.

The single authority on what "joint" means. Every pricing function in ``wnba_marketmath``
takes a correct-count PMF as an input and refuses to invent one, so that the correlation
assumption is always explicit, always seeded, and always auditable.
"""

from __future__ import annotations

from wnba_sim.copula import (
    DEFAULT_SIMULATIONS,
    correct_count_pmf,
    correlation_from_matrix,
    effective_leg_correlation,
)

__all__ = [
    "DEFAULT_SIMULATIONS",
    "correct_count_pmf",
    "correlation_from_matrix",
    "effective_leg_correlation",
]

__version__ = "0.1.0"
