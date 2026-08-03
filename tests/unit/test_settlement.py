import pytest
from wnba_services.learning_loop.settlement import actual_stat

LINE = {
    "points": 21,
    "rebounds_offensive": 2,
    "rebounds_defensive": 7,
    "assists": 6,
    "steals": 1,
    "blocks": 2,
    "turnovers": 3,
    "three_pointers_made": 4,
}


def test_actual_stat_supports_atomic_and_combination_markets() -> None:
    assert actual_stat(LINE, "points") == 21
    assert actual_stat(LINE, "rebounds") == 9
    assert actual_stat(LINE, "points_rebounds_assists") == 36
    assert actual_stat(LINE, "steals_blocks") == 3


def test_actual_stat_rejects_unsupported_market() -> None:
    with pytest.raises(ValueError, match="unsupported settlement market"):
        actual_stat(LINE, "fantasy_points")
