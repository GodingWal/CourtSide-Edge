"""Dialogue service tests: web-search parsing, tool discipline, and one full turn."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from wnba_services.dialogue import service
from wnba_services.dialogue.web_search import parse_results

LITE_PAGE = """
<html><body><table>
<tr><td><a class="result-link"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnews&amp;rut=abc">
A'ja Wilson listed <b>questionable</b> with ankle</a></td></tr>
<tr><td class="result-snippet">Wilson was limited at shootaround on Wednesday, per the team.
</td></tr>
<tr><td><a class="result-link" href="https://direct.example.net/story">Line moves to 22.5
at noon</a></td></tr>
<tr><td class="result-snippet">The points prop ticked up half a point across the market.
</td></tr>
</table></body></html>
"""


def test_parse_results_extracts_triples_and_unwraps_redirects() -> None:
    results = parse_results(LITE_PAGE)
    assert [r.title for r in results] == [
        "A'ja Wilson listed questionable with ankle",
        "Line moves to 22.5 at noon",
    ]
    assert results[0].url == "https://example.com/news"
    assert results[1].url == "https://direct.example.net/story"
    assert "shootaround" in results[0].snippet


def test_parse_results_is_empty_on_unrelated_markup() -> None:
    assert parse_results("<html><body>nothing here</body></html>") == []
    assert parse_results("") == []


class _FakeCursor:
    """Answers the handful of fixed statements the dialogue service issues."""

    def __init__(self, projection: dict[str, Any]) -> None:
        self.projection = projection
        self.inserted: list[tuple[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self._one: dict[str, Any] | None = None
        self._all: list[dict[str, Any]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._one, self._all = None, []
        if "FROM wnba.stat_forecasts" in sql:
            self._one = self.projection
        elif "FROM wnba.projection_dialogues" in sql and "SELECT" in sql:
            self._one = {"dialogue_id": DIALOGUE_ID}
        elif "FROM wnba.dialogue_messages" in sql and "SELECT" in sql:
            rows = self.messages[-params[1] :] if len(params) > 1 else self.messages
            self._all = list(reversed(rows)) if "DESC" in sql else list(rows)
        elif "INSERT INTO wnba.dialogue_messages" in sql:
            self.inserted.append((params[2], params[4]))
            self.messages.append(
                {
                    "role": params[2],
                    "content": params[3],
                    "payload": json.loads(params[4]),
                    "created_at": params[5],
                }
            )
        elif "INSERT INTO wnba.projection_dialogues" in sql:
            self.inserted.append(("dialogue", params))
        elif "UPDATE wnba.projection_dialogues" in sql:
            pass
        else:  # pragma: no cover - a new query must extend this fake deliberately
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def fetchone(self) -> dict[str, Any] | None:
        return self._one

    def fetchall(self) -> list[dict[str, Any]]:
        return self._all


class _FakeConnection:
    def __init__(self, projection: dict[str, Any]) -> None:
        self.cursor_obj = _FakeCursor(projection)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj


class _FakeClient:
    """One tool-calling reply, then a grounded final answer."""

    api_key = "test-key"
    model = "deepseek-v4-pro"

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_web",
                                        "arguments": json.dumps(
                                            {"query": "A'ja Wilson ankle injury status"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }, 1
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Web results say she was limited at shootaround; treat "
                        "the minutes projection as fragile until confirmed.",
                    }
                }
            ]
        }, 1


DIALOGUE_ID = uuid4()
PROJECTION_ID = uuid4()


def _projection() -> dict[str, Any]:
    return {
        "projection_id": PROJECTION_ID,
        "player_id": uuid4(),
        "game_id": uuid4(),
        "quote_id": uuid4(),
        "model_run_id": uuid4(),
        "prop_type": "points",
        "line": 21.5,
        "full_name": "A'ja Wilson",
        "scheduled_tipoff": "2026-08-06 23:00:00+00",
        "source": "prizepicks",
    }


def test_send_message_validates_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "connect", lambda: _FakeConnection(_projection()))
    with pytest.raises(ValueError, match="empty"):
        service.send_message(PROJECTION_ID, "   ")
    with pytest.raises(ValueError, match="exceeds"):
        service.send_message(PROJECTION_ID, "x" * 4001)


def test_run_tool_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        service._run_tool("drop_tables", {}, _projection())


def test_full_turn_persists_verbatim_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    from wnba_services.dialogue.web_search import SearchResult

    connection = _FakeConnection(_projection())
    monkeypatch.setattr(service, "connect", lambda: connection)
    monkeypatch.setattr(
        service,
        "search_web",
        lambda query, limit=5: [
            SearchResult(
                title="A'ja Wilson limited at shootaround",
                url="https://example.com/news",
                snippet="Wilson was limited at shootaround on Wednesday.",
            )
        ],
    )

    view = service.send_message(
        PROJECTION_ID, "Should I worry about the ankle?", client=_FakeClient()
    )

    roles = [role for role, _payload in connection.cursor_obj.inserted]
    assert roles[0] == "owner"
    assert "assistant" in roles and "tool" in roles
    # The tool transcript carries the exact provider envelope pieces needed for replay.
    tool_payloads = [
        json.loads(payload)
        for role, payload in connection.cursor_obj.inserted
        if role == "tool"
    ]
    assert tool_payloads[0]["tool_call_id"] == "call_1"
    assert view.messages[-1].role == "assistant"
    assert "shootaround" in view.messages[-1].content


def test_tool_definitions_have_valid_schema_shape() -> None:
    names = {tool["function"]["name"] for tool in service._TOOL_DEFINITIONS}
    assert names == {
        "projection_detail",
        "player_recent_games",
        "quote_history",
        "research_findings",
        "search_web",
    }
    for tool in service._TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["type"] == "object"
        assert tool["function"]["description"]
