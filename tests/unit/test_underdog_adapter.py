from __future__ import annotations

from datetime import UTC, datetime

from wnba_services.ingestion.adapters.underdog import UnderdogAdapter


def test_wnba_quote_carries_game_identity_and_lock_time() -> None:
    payload = {
        "players": [
            {
                "id": "player-1",
                "sport_id": "WNBA",
                "first_name": "A'ja",
                "last_name": "Wilson",
                "team_id": "team-away",
            }
        ],
        "appearances": [{"id": "appearance-1", "player_id": "player-1", "match_id": 154617}],
        "games": [
            {
                "id": 154617,
                "abbreviated_title": "LVA @ ATL",
                "scheduled_at": "2026-08-03T23:00:00Z",
            }
        ],
        "solo_games": [],
        "over_under_lines": [
            {
                "id": "line-1",
                "stat_value": "26.5",
                "status": "active",
                "updated_at": "2026-08-03T16:14:18Z",
                "over_under": {
                    "appearance_stat": {"appearance_id": "appearance-1", "display_stat": "Points"}
                },
                "options": [
                    {"choice": "higher", "american_price": "-107"},
                    {"choice": "lower", "american_price": "-127"},
                ],
            }
        ],
    }
    observed = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    parsed, rejects = UnderdogAdapter().parse(payload, observed_at=observed)

    assert rejects == []
    assert len(parsed) == 1
    assert parsed[0].game.source_game_id == "154617"
    assert parsed[0].game.away_abbreviation == "LVA"
    assert parsed[0].game.home_abbreviation == "ATL"
    assert parsed[0].quote.locks_at == datetime(2026, 8, 3, 23, 0, tzinfo=UTC)


def test_line_without_resolvable_game_is_rejected() -> None:
    payload = {
        "players": [{"id": "p", "sport_id": "WNBA"}],
        "appearances": [{"id": "a", "player_id": "p", "match_id": 9}],
        "games": [],
        "solo_games": [],
        "over_under_lines": [
            {"id": "line", "over_under": {"appearance_stat": {"appearance_id": "a"}}}
        ],
    }
    parsed, rejects = UnderdogAdapter().parse(payload)
    assert parsed == []
    assert rejects == ["line line: appearance has no resolvable game"]
