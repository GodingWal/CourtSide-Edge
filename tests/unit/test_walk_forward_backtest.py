from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from wnba_services.forecasting.backtest import latest_quote_as_of, poisson_over_probability


def test_quote_selection_never_uses_future_observation() -> None:
    now = datetime.now(UTC)
    quotes = [
        {"observed_at": now - timedelta(hours=2), "line": 10.5},
        {"observed_at": now + timedelta(minutes=1), "line": 11.5},
    ]
    selected = latest_quote_as_of(quotes, now)
    assert selected is not None
    assert selected["line"] == 10.5


def test_poisson_over_probability_is_monotonic() -> None:
    low = poisson_over_probability(10.0, Decimal("15.5"))
    high = poisson_over_probability(20.0, Decimal("15.5"))
    assert 0 <= low < high <= 1
    assert poisson_over_probability(16.0, Decimal("15.5")) == pytest.approx(0.533, abs=0.01)
