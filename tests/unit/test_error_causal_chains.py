"""Error contribution shares and causal chains (self-improvement plan sections 2 and 9)."""

from __future__ import annotations

from wnba_services.learning_loop.evaluation import error_contribution


def test_contribution_splits_minutes_from_efficiency() -> None:
    # Projected 40 minutes x 0.5 pts/min = 20; player saw 30 minutes and scored 12.
    shares = error_contribution(
        actual_minutes=30.0, projected_minutes=40.0, actual_stat=12.0, projected_stat=20.0
    )
    # Minutes carry: -10 * 0.5 = -5; residual: -8 - (-5) = -3. Shares: 5/8, 3/8.
    assert shares == {"minutes": 0.625, "efficiency": 0.375}


def test_contribution_with_no_error_is_zero() -> None:
    shares = error_contribution(
        actual_minutes=34.0, projected_minutes=34.0, actual_stat=18.0, projected_stat=18.0
    )
    assert shares == {"minutes": 0.0, "efficiency": 0.0}


def test_contribution_when_minutes_projection_is_perfect() -> None:
    shares = error_contribution(
        actual_minutes=34.0, projected_minutes=34.0, actual_stat=10.0, projected_stat=18.0
    )
    assert shares["minutes"] == 0.0
    assert shares["efficiency"] == 1.0
