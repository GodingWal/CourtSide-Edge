"""The historical box-score endpoints tell the truth about the archive.

Coverage reports exactly what the database holds per season, player search demands a real
needle before it touches the database, and an unknown player is a 404 rather than an empty
page dressed up as data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from wnba_apps.api.main import app

client = TestClient(app)


class _Cursor:
    def __init__(self, results: list[object]) -> None:
        self._results = results

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchall(self) -> list[dict[str, object]]:
        rows = self._results.pop(0)
        assert isinstance(rows, list)
        return rows

    def fetchone(self) -> dict[str, object] | None:
        row = self._results.pop(0)
        assert row is None or isinstance(row, dict)
        return row


class _Connection:
    def __init__(self, results: list[object]) -> None:
        self._cursor = _Cursor(results)

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return self._cursor


def _install(monkeypatch: pytest.MonkeyPatch, results: list[object]) -> None:
    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection(results))


def test_history_coverage_reports_each_season_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    seasons = [
        {"season_year": 2024, "games": 273, "games_with_lines": 273,
         "player_lines": 5201, "players": 158},
        {"season_year": 2025, "games": 322, "games_with_lines": 322,
         "player_lines": 6153, "players": 171},
    ]
    _install(monkeypatch, [seasons])
    body = client.get("/api/history").json()
    assert body["available"] is True
    assert body["seasons"] == seasons


def test_player_search_refuses_a_one_letter_needle(monkeypatch: pytest.MonkeyPatch) -> None:
    def _should_not_be_called() -> None:
        raise AssertionError("the database must not be touched for a one-letter search")

    monkeypatch.setattr("wnba_store.db.connect", _should_not_be_called)
    assert client.get("/api/history/players?q=w").json()["players"] == []


def test_player_search_matches_on_partial_names(monkeypatch: pytest.MonkeyPatch) -> None:
    players = [
        {"player_id": "p1", "full_name": "A'ja Wilson", "position": "F",
         "games": 120, "first_game": "2023-05-19", "last_game": "2026-08-05"},
    ]
    _install(monkeypatch, [players])
    body = client.get("/api/history/players?q=wilson").json()
    assert body["players"] == players


def test_an_unknown_player_is_a_404_not_an_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [None])
    assert client.get("/api/history/player/00000000-0000-0000-0000-000000000000").status_code == 404


def test_the_game_log_returns_season_averages_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        [
            {"player_id": "p1", "full_name": "A'ja Wilson", "position": "F"},
            [{"season_year": 2026, "games": 30, "minutes": 33.1, "points": 22.4,
              "rebounds": 9.8, "assists": 2.6, "threes": 0.4}],
            [{"game_date": "2026-08-05", "season_year": 2026, "team": "LV",
              "opponent": "NY", "is_home": True, "minutes": 34.0, "points": 27,
              "rebounds": 11, "assists": 3, "threes": 1, "steals": 2, "blocks": 1,
              "turnovers": 2}],
        ],
    )
    body = client.get("/api/history/player/p1").json()
    assert body["player"]["full_name"] == "A'ja Wilson"
    assert body["seasons"][0]["points"] == 22.4
    assert body["games"][0]["opponent"] == "NY"
    assert body["games"][0]["is_home"] is True
