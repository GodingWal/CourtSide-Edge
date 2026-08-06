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


class _GameCursor:
    """A cursor that plays back one game row and remembers what it was asked."""

    def __init__(self, game_id: str) -> None:
        self._row = {"game_id": game_id}
        self.params: tuple[object, ...] | None = None

    def execute(self, _sql: str, params: tuple[object, ...]) -> None:
        self.params = params

    def fetchall(self) -> list[dict[str, str]]:
        return [self._row]


def test_resolve_game_maps_report_abbreviations_to_schedule_abbreviations() -> None:
    """The report says LVA/LAS/PDX where the schedule says LV/LA/POR; unmapped, every
    row fails game resolution and the injury feed lands nothing."""
    game_id = "11111111-2222-3333-4444-555555555555"
    injury = OfficialInjury(
        game_date="08/06/2026",
        matchup="LVA@IND",
        team_name="Las Vegas Aces",
        player_name="Jane Doe",
        status="Out",
        reason="Ankle",
    )
    cursor = _GameCursor(game_id)
    assert _resolve_game(cursor, injury) is not None  # type: ignore[arg-type]
    assert cursor.params == ("08/06/2026", "LV", "IND")


def test_resolve_game_tolerates_whitespace_around_matchup_teams() -> None:
    game_id = "11111111-2222-3333-4444-555555555555"
    injury = OfficialInjury(
        game_date="08/06/2026",
        matchup="LAS @ NYL",
        team_name="New York Liberty",
        player_name="Jane Doe",
        status="Out",
        reason="Ankle",
    )
    cursor = _GameCursor(game_id)
    assert _resolve_game(cursor, injury) is not None  # type: ignore[arg-type]
    assert cursor.params == ("08/06/2026", "LA", "NY")
