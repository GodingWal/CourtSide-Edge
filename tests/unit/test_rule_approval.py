from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from wnba_services.learning_loop.rule_lifecycle import approve_rule_with_cursor


class ApprovalCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def test_helpful_backtested_rule_requires_named_audited_approval() -> None:
    cursor = ApprovalCursor(
        {"status": "backtested", "backtest": {"verdict": "helpful", "sample_size": 80}}
    )
    at = datetime(2026, 8, 4, 20, tzinfo=UTC)

    result = approve_rule_with_cursor(
        cursor,
        "shrink_when_minutes_are_uncertain",
        approved_by="Goding Wal",
        reason="Helpful held-out result with conservative downside only.",
        now=at,
    )

    assert result.status == "active"
    assert result.actor == "Goding Wal"
    update, params = cursor.calls[-1]
    assert "approval_reason" in update
    assert params == (
        "Goding Wal",
        at,
        "Helpful held-out result with conservative downside only.",
        "shrink_when_minutes_are_uncertain",
    )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (None, "unknown analyst rule"),
        ({"status": "proposed", "backtest": None}, "must be backtested"),
        (
            {"status": "backtested", "backtest": {"verdict": "harmful"}},
            "does not have a helpful",
        ),
    ],
)
def test_rule_approval_fails_closed(row: dict[str, Any] | None, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        approve_rule_with_cursor(
            ApprovalCursor(row),
            "candidate_rule",
            approved_by="Goding Wal",
            reason="Reviewed the complete backtest evidence.",
        )


def test_rule_approval_rejects_anonymous_or_unexplained_changes() -> None:
    row = {"status": "backtested", "backtest": {"verdict": "helpful"}}
    with pytest.raises(ValueError, match="named human"):
        approve_rule_with_cursor(
            ApprovalCursor(row),
            "candidate_rule",
            approved_by=" ",
            reason="Reviewed the complete backtest evidence.",
        )
    with pytest.raises(ValueError, match="at least 10"):
        approve_rule_with_cursor(
            ApprovalCursor(row),
            "candidate_rule",
            approved_by="Goding Wal",
            reason="because",
        )
