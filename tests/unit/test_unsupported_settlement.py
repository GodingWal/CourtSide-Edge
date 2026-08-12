from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from wnba_services.learning_loop import unsupported_settlement
from wnba_services.learning_loop.unsupported_settlement import void_unsupported_episodes

from tests.fixtures.fake_db import FakeDatabase


def test_unsupported_final_market_is_audited_terminal_void(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = uuid4()
    database = FakeDatabase().when(
        "FROM wnba.decision_episodes d",
        [{"episode_id": episode_id, "prop_type": "double_doubles"}],
    )
    database.when("INSERT INTO wnba.episode_outcomes", rowcount=1)
    monkeypatch.setattr(unsupported_settlement, "connect", database.connect)

    result = void_unsupported_episodes(now=datetime(2026, 8, 12, tzinfo=UTC))

    assert result.voided == 1
    _, outcome_params = database.executed("INSERT INTO wnba.episode_outcomes")[0]
    assert outcome_params[0] == episode_id
    _, action_params = database.executed("INSERT INTO wnba.ontology_actions")[0]
    assert action_params[1] == episode_id
    assert "double_doubles" in action_params[2]


def test_cleanup_query_excludes_every_supported_market(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(unsupported_settlement, "connect", database.connect)

    void_unsupported_episodes()

    statement, params = database.executed("FROM wnba.decision_episodes d")[0]
    assert "NOT (d.prop_type=ANY(%s))" in statement
    assert "points" in params[0]
    assert "points_rebounds_assists" in params[0]
