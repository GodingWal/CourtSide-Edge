"""Owner-facing gap coverage: drivers, flags, drawdown, decisions, overrides."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from wnba_apps.api.main import (
    _drivers_and_flags,
    _max_drawdown,
    app,
)

client = TestClient(app)


def test_drivers_describe_teammate_and_pace_effects() -> None:
    row = {
        "teammate_rate_multiplier": 1.06,
        "teammate_minutes_delta": 2.4,
        "pace_multiplier": 1.04,
        "team_rest_days": 1,
    }
    drivers, flags = _drivers_and_flags(row)
    assert any("teammate availability" in d for d in drivers)
    assert any("pace-up" in d for d in drivers)
    assert flags == []


def test_flags_surface_injury_and_uncertainty() -> None:
    row = {
        "injury_designation": "questionable",
        "injury_detail": "left ankle",
        "availability_probability": 0.72,
        "start_probability": 0.55,
        "data_quality_score": 0.81,
    }
    drivers, flags = _drivers_and_flags(row)
    assert any("questionable" in f for f in flags)
    assert any("availability" in f for f in flags)
    assert any("starting status" in f for f in flags)
    assert any("data quality" in f for f in flags)
    assert drivers == []


def test_max_drawdown_measures_the_worst_stretch() -> None:
    assert _max_drawdown([]) == 0.0
    assert _max_drawdown([1.0, 1.0, 1.0]) == 0.0
    # +2, then five straight losses from the peak: drawdown is 5 units.
    assert _max_drawdown([1.0, 1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 3.0]) == 5.0


class _Cursor:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Connection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._cursor = _Cursor(row)

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return self._cursor


def test_recommendation_decision_records_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    episode_id = uuid4()
    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection({"episode_id": episode_id}))
    body = client.post(
        f"/api/recommendations/{episode_id}/decision", json={"decision": "accepted"}
    ).json()
    assert body["analyst_decision"] == "accepted"
    assert body["actor"] == "owner"


def test_override_decision_without_a_reason_is_a_422() -> None:
    response = client.post(
        f"/api/recommendations/{uuid4()}/decision", json={"decision": "override_higher"}
    )
    assert response.status_code == 422


def test_decision_for_an_unknown_episode_is_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection(None))
    response = client.post(
        f"/api/recommendations/{uuid4()}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 404


def test_minutes_override_records_and_supersedes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection(None))
    body = client.post(
        "/api/overrides/minutes",
        json={
            "player_id": str(uuid4()),
            "game_id": str(uuid4()),
            "minutes": 28.5,
            "reason": "coach said restriction lifts tonight",
        },
    ).json()
    assert body["minutes"] == 28.5
    assert body["actor"] == "owner"


def test_minutes_override_rejects_impossible_minutes() -> None:
    response = client.post(
        "/api/overrides/minutes",
        json={
            "player_id": str(uuid4()),
            "game_id": str(uuid4()),
            "minutes": 52.0,
            "reason": "nobody plays 52 minutes in a 40-minute league",
        },
    )
    assert response.status_code == 422
