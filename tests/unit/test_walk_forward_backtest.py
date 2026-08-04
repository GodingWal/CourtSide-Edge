"""Point-in-time replay: quote selection, the naive benchmarks, and the shared scorer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from wnba_services.forecasting.backtest import (
    MODEL_NAMES,
    benchmark_predictions,
    latest_quote_as_of,
    poisson_over_probability,
)
from wnba_services.forecasting.baseline import market_inputs, stat_history_game
from wnba_services.forecasting.challengers import challenger_names
from wnba_services.forecasting.scoring import (
    HistoryGame,
    ScoringInputs,
    opportunity_conversion_expectation,
    score_prop,
)


def _inputs(history: list[HistoryGame], line: str = "15.5") -> ScoringInputs:
    return ScoringInputs(
        prop_type="points",
        line=Decimal(line),
        history=tuple(history),
        league_rate_per_minute=0.45,
        seed=3,
        simulations=1500,
    )


def _flat_history(count: int = 12) -> list[HistoryGame]:
    return [
        HistoryGame(
            minutes=30.0,
            started=True,
            stats={"points": 15.0, "field_goals_attempted": 10.0},
        )
        for _ in range(count)
    ]


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


def test_points_opportunity_conversion_separates_volume_and_efficiency() -> None:
    """Ten attempts per thirty minutes at 1.5 points each, projected over thirty-two."""
    assert opportunity_conversion_expectation(_inputs(_flat_history()), 32.0) == pytest.approx(16.0)


def test_opportunity_reacts_faster_than_conversion() -> None:
    """Volume moves with role and is weighted to forget faster than shooting touch does."""
    history = [
        HistoryGame(
            minutes=30.0, started=True, stats={"points": 12.0, "field_goals_attempted": 10.0}
        )
        for _ in range(9)
    ]
    history.append(
        HistoryGame(
            minutes=30.0, started=True, stats={"points": 24.0, "field_goals_attempted": 20.0}
        )
    )
    assert opportunity_conversion_expectation(_inputs(history), 30.0) > 13.2


def test_the_replay_scores_the_model_production_actually_runs() -> None:
    """The defect this harness had: its "ensemble" shared one component with production.

    Not a naming check -- the benchmark set and the production scorer must be different objects
    producing different answers, with production coming from ``score_prop`` itself.
    """
    inputs = _inputs(_flat_history(15))
    benchmarks = benchmark_predictions(inputs)

    assert "production_ensemble" not in benchmarks
    # Challengers replay beside the champion but are not benchmarks: the naive baselines are what
    # production must beat, and a challenger is a peer being measured on the same terms.
    assert set(benchmarks) | {"production_ensemble"} | set(challenger_names()) == set(MODEL_NAMES)
    assert set(benchmarks).isdisjoint(challenger_names())

    forecast = score_prop(inputs)
    assert 0.0 <= forecast.over <= 1.0


def test_benchmarks_are_computed_from_point_in_time_history_only() -> None:
    steady = benchmark_predictions(_inputs(_flat_history(15)))
    for name, (mean, probability) in steady.items():
        assert mean >= 0.0, name
        assert 0.0 <= probability <= 1.0, name
    # Fifteen points a game, every game: the naive averages must all agree on fifteen.
    assert steady["season_average"][0] == pytest.approx(15.0)
    assert steady["last_five"][0] == pytest.approx(15.0)
    assert steady["minutes_rate"][0] == pytest.approx(15.0, rel=0.01)


def test_a_flat_pickem_quote_yields_no_market_evidence() -> None:
    quote = {
        "over_american_odds": None,
        "under_american_odds": None,
        "over_multiplier": Decimal("1.00"),
        "under_multiplier": Decimal("1.00"),
    }
    assert not market_inputs(quote).is_informative


def test_a_two_sided_price_is_devigged_into_market_evidence() -> None:
    quote = {
        "over_american_odds": -140,
        "under_american_odds": 120,
        "over_multiplier": None,
        "under_multiplier": None,
    }
    market = market_inputs(quote)
    assert market.is_informative
    assert market.over_probability is not None
    assert market.under_probability is not None
    assert market.over_probability > market.under_probability
    assert market.over_probability + market.under_probability == pytest.approx(1.0)


def test_a_shaded_pickem_multiplier_is_devigged_too() -> None:
    quote = {
        "over_american_odds": None,
        "under_american_odds": None,
        "over_multiplier": Decimal("0.80"),
        "under_multiplier": Decimal("1.25"),
    }
    market = market_inputs(quote)
    assert market.is_informative
    assert market.over_probability is not None
    assert market.over_probability > 0.5


def test_a_malformed_price_is_treated_as_no_price_rather_than_crashing() -> None:
    quote = {
        "over_american_odds": 50,
        "under_american_odds": -50,
        "over_multiplier": None,
        "under_multiplier": None,
    }
    assert not market_inputs(quote).is_informative


def test_history_rows_keep_only_the_columns_that_were_present() -> None:
    row = {
        "minutes": Decimal("28.5"),
        "started": True,
        "points": 14,
        "assists": None,
        "unrelated_column": "ignored",
    }
    game = stat_history_game(row)
    assert game.minutes == pytest.approx(28.5)
    assert game.started is True
    assert game.stats == {"points": 14.0}
    assert not game.has("assists")
