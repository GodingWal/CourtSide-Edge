import pytest
from wnba_services.ingestion.adapters.wnba_injuries import OfficialInjury, latest_report
from wnba_services.ingestion.wnba_injuries import _resolve_game


def test_latest_official_report_uses_last_index_link() -> None:
    index = {
        "links": [
            {"href": "https://example.test/early.pdf", "label": "1:00 p.m."},
            {"href": "https://example.test/latest.pdf", "label": "1:15 p.m."},
        ]
    }
    assert latest_report(index) == ("https://example.test/latest.pdf", "1:15 p.m.")


def test_official_report_requires_at_least_one_link() -> None:
    with pytest.raises(ValueError, match="no reports"):
        latest_report({"links": []})


def test_resolve_game_treats_empty_date_as_unresolved() -> None:
    """Regression: an empty game_date must not reach the database as a date parameter."""
    injury = OfficialInjury(
        game_date="",
        matchup="LAS @ NYL",
        team_name="New York Liberty",
        player_name="Jane Doe",
        status="Out",
        reason="Ankle",
    )
    assert _resolve_game(object(), injury) is None


def test_resolve_game_treats_malformed_matchup_as_unresolved() -> None:
    injury = OfficialInjury(
        game_date="2026-08-06",
        matchup="",
        team_name="New York Liberty",
        player_name="Jane Doe",
        status="Out",
        reason="Ankle",
    )
    assert _resolve_game(object(), injury) is None
