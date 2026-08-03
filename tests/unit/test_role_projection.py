import pytest
from wnba_services.feature_engine.roles import role_projection


def history() -> list[dict[str, object]]:
    return [{"minutes": 24 + index, "started": index >= 5} for index in range(10)]


def test_available_role_separates_start_and_minutes_probabilities() -> None:
    role = role_projection(history(), "available")
    assert role["availability"] == 1.0
    assert 0.0 < role["start_probability"] < 1.0
    assert 24.0 < role["expected_minutes"] < 34.0


def test_questionable_role_widens_minutes_and_reduces_availability() -> None:
    healthy = role_projection(history(), "available")
    questionable = role_projection(history(), "questionable")
    assert questionable["availability"] == pytest.approx(0.6)
    assert questionable["minutes_std"] > healthy["minutes_std"]
    assert questionable["expected_minutes"] < healthy["expected_minutes"]


def test_out_player_projects_zero_minutes() -> None:
    role = role_projection(history(), "out")
    assert role["availability"] == 0.0
    assert role["expected_minutes"] == 0.0
