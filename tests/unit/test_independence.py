"""Counting independent events, which is not the same as counting rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from wnba_services.learning_loop.independence import (
    DEFAULT_INTRA_GAME_CORRELATION,
    dedupe_latest_per_market,
    design_effect,
    effective_sample_size,
    market_key,
    summarise_sample,
)

BASE = datetime(2026, 8, 1, tzinfo=UTC)


def _row(
    player: str, game: str, prop: str = "points", minutes_late: int = 0, **extra: object
) -> dict[str, object]:
    return {
        "player_id": player,
        "game_id": game,
        "prop_type": prop,
        "forecast_timestamp": BASE + timedelta(minutes=minutes_late),
        **extra,
    }


# --------------------------------------------------------------------------------------
# Collapsing repeats
# --------------------------------------------------------------------------------------
def test_the_same_market_forecast_many_times_is_one_observation() -> None:
    rows = [_row("a", "g1", minutes_late=step) for step in range(40)]
    assert len(dedupe_latest_per_market(rows)) == 1


def test_the_surviving_row_is_the_last_one_issued() -> None:
    """The latest forecast is the one an analyst would have acted on."""
    rows = [
        _row("a", "g1", minutes_late=0, probability=0.51),
        _row("a", "g1", minutes_late=90, probability=0.67),
        _row("a", "g1", minutes_late=45, probability=0.58),
    ]
    assert dedupe_latest_per_market(rows)[0]["probability"] == 0.67


def test_different_markets_are_kept_apart() -> None:
    rows = [
        _row("a", "g1", "points"),
        _row("a", "g1", "rebounds"),
        _row("b", "g1", "points"),
        _row("a", "g2", "points"),
    ]
    assert len(dedupe_latest_per_market(rows)) == 4


def test_side_is_not_part_of_a_market_identity() -> None:
    """Over and under are two descriptions of one event, not two events."""
    rows = [_row("a", "g1", side="over"), _row("a", "g1", side="under")]
    assert len(dedupe_latest_per_market(rows)) == 1
    assert market_key(rows[0]) == market_key(rows[1])


def test_rows_without_timestamps_still_collapse() -> None:
    rows: list[dict[str, object]] = [
        {"player_id": "a", "game_id": "g1", "prop_type": "points", "n": 1},
        {"player_id": "a", "game_id": "g1", "prop_type": "points", "n": 2},
    ]
    deduped = dedupe_latest_per_market(rows)
    assert len(deduped) == 1
    assert deduped[0]["n"] == 2


def test_a_custom_ordering_field_is_honoured() -> None:
    rows = [
        {"player_id": "a", "game_id": "g1", "prop_type": "points", "as_of": 5, "keep": False},
        {"player_id": "a", "game_id": "g1", "prop_type": "points", "as_of": 9, "keep": True},
    ]
    assert dedupe_latest_per_market(rows, order_field="as_of")[0]["keep"] is True


def test_deduplication_preserves_input_order() -> None:
    rows = [_row("b", "g1"), _row("a", "g1"), _row("c", "g1")]
    assert [row["player_id"] for row in dedupe_latest_per_market(rows)] == ["b", "a", "c"]


def test_an_empty_input_is_an_empty_output() -> None:
    assert dedupe_latest_per_market([]) == []


# --------------------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------------------
def test_singleton_clusters_carry_no_penalty() -> None:
    assert design_effect([1, 1, 1], correlation=0.3) == pytest.approx(1.0)


def test_bigger_clusters_cost_more_information() -> None:
    small = design_effect([5, 5], correlation=0.3)
    large = design_effect([50, 50], correlation=0.3)
    assert 1.0 < small < large


def test_zero_correlation_recovers_independence() -> None:
    assert design_effect([20, 20], correlation=0.0) == pytest.approx(1.0)


def test_effective_sample_is_never_larger_than_the_raw_count() -> None:
    for sizes in ([1], [3, 4], [30, 30, 30]):
        assert effective_sample_size(100, sizes) <= 100.0


def test_effective_sample_of_nothing_is_nothing() -> None:
    assert effective_sample_size(0, [5]) == 0.0


def test_the_design_effect_matches_the_closed_form() -> None:
    assert design_effect([10, 10], correlation=0.3) == pytest.approx(1.0 + 9 * 0.3)


# --------------------------------------------------------------------------------------
# The whole picture
# --------------------------------------------------------------------------------------
def test_a_sample_summary_separates_rows_from_markets_from_games() -> None:
    """The shape the audit found: many rows, fewer markets, very few games."""
    rows = [
        _row(f"player-{market}", f"game-{market // 8}", minutes_late=step)
        for market in range(24)
        for step in range(12)
    ]
    shape = summarise_sample(rows)

    assert shape.rows == 288
    assert shape.markets == 24
    assert shape.games == 3
    assert shape.effective < shape.markets
    assert shape.rows_per_market == pytest.approx(12.0)
    assert shape.markets_per_game == pytest.approx(8.0)


def test_the_summary_payload_is_json_ready() -> None:
    payload = summarise_sample([_row("a", "g1")]).to_payload()
    assert payload["independent_markets"] == 1
    assert payload["independent_games"] == 1
    assert set(payload) == {
        "rows",
        "independent_markets",
        "independent_games",
        "effective_sample_size",
        "rows_per_market",
        "markets_per_game",
    }


def test_an_empty_sample_reports_zeroes_rather_than_dividing_by_them() -> None:
    shape = summarise_sample([])
    assert (shape.rows, shape.markets, shape.games, shape.effective) == (0, 0, 0, 0.0)
    assert shape.rows_per_market == 0.0
    assert shape.markets_per_game == 0.0


def test_the_stated_correlation_prior_is_not_accidentally_zero() -> None:
    """Independence is the one value we can be sure is wrong for props sharing a game."""
    assert 0.0 < DEFAULT_INTRA_GAME_CORRELATION < 1.0
