from decimal import Decimal

import pytest
from wnba_services.forecasting.ensemble import build_ensemble


def history() -> list[dict[str, object]]:
    return [{"minutes": 30.0, "points": 14 + index % 5} for index in range(20)]


def test_ensemble_is_normalized_and_exposes_disagreement() -> None:
    result = build_ensemble(
        outcomes=[12, 15, 18, 21] * 250,
        history=history(),
        columns=("points",),
        expected_minutes=31.0,
        rate_multiplier=1.0,
        league_rate_per_minute=0.45,
        line=Decimal("16.5"),
    )
    assert sum(result.pmf) == pytest.approx(1.0)
    assert result.over + result.push + result.under == pytest.approx(1.0)
    assert len(result.components) == 4
    assert result.disagreement >= 0.0


def test_whole_number_line_preserves_push_probability() -> None:
    result = build_ensemble(
        outcomes=[16] * 1000,
        history=history(),
        columns=("points",),
        expected_minutes=30.0,
        rate_multiplier=1.0,
        league_rate_per_minute=0.45,
        line=Decimal("16"),
    )
    assert result.push > 0.4
