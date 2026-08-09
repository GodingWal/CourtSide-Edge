"""Owner approval endpoints record named human acts and refuse invalid transitions.

Every button on the learning page wraps the same lifecycle function the CLI exposes: a
successful call returns the recorded actor, and a lifecycle refusal surfaces as a 422 with
the reason intact rather than a generic 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from wnba_apps.api.main import app

client = TestClient(app)

OWNER = "owner"
REASON = {"reason": "reviewed the evidence myself"}


@dataclass(frozen=True)
class _Approval:
    rule_id: str
    status: str
    actor: str


@dataclass(frozen=True)
class _Promotion:
    experiment_id: UUID
    challenger_name: str
    status: str
    actor: str


def test_rule_approval_records_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    def _approve(rule_id: str, *, approved_by: str, reason: str) -> _Approval:
        assert approved_by == OWNER
        assert reason == REASON["reason"]
        return _Approval(rule_id=rule_id, status="active", actor=approved_by)

    monkeypatch.setattr("wnba_services.learning_loop.rule_lifecycle.approve_rule", _approve)
    body = client.post("/api/learning/rules/shrink_when_tired/approve", json=REASON).json()
    assert body == {"rule_id": "shrink_when_tired", "status": "active", "actor": OWNER}


def test_rule_approval_without_a_helpful_backtest_is_a_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(rule_id: str, *, approved_by: str, reason: str) -> _Approval:
        raise ValueError(f"rule {rule_id!r} does not have a helpful backtest verdict")

    monkeypatch.setattr("wnba_services.learning_loop.rule_lifecycle.approve_rule", _refuse)
    response = client.post("/api/learning/rules/any_rule/approve", json=REASON)
    assert response.status_code == 422
    assert "helpful backtest" in response.json()["detail"]


def test_rule_retirement_records_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    def _retire(rule_id: str, *, retired_by: str, reason: str) -> _Approval:
        return _Approval(rule_id=rule_id, status="retired", actor=retired_by)

    monkeypatch.setattr("wnba_services.learning_loop.rule_lifecycle.retire_rule", _retire)
    body = client.post("/api/learning/rules/some_rule/retire", json=REASON).json()
    assert body["status"] == "retired"
    assert body["actor"] == OWNER


def test_opening_an_experiment_names_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment_id = uuid4()

    def _open(
        challenger: str, *, opened_by: str, primary_metric: str, minimum_sample: int
    ) -> UUID:
        assert opened_by == OWNER
        assert primary_metric == "brier"
        assert minimum_sample == 200
        return experiment_id

    monkeypatch.setattr("wnba_services.learning_loop.experiments.open_experiment", _open)
    body = client.post(
        "/api/learning/experiments/open", json={"challenger": "hierarchical-bayes"}
    ).json()
    assert body == {"experiment_id": str(experiment_id), "status": "running"}


def test_promotion_refusal_surfaces_the_gate_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(experiment_id: UUID, *, approved_by: str, reason: str) -> _Promotion:
        raise ValueError("verdict is 'awaiting evidence', not 'challenger_better'")

    monkeypatch.setattr(
        "wnba_services.learning_loop.experiments.promote_challenger", _refuse
    )
    response = client.post(f"/api/learning/experiments/{uuid4()}/promote", json=REASON)
    assert response.status_code == 422
    assert "challenger_better" in response.json()["detail"]


def test_rollback_records_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment_id = uuid4()

    def _rollback(experiment_id: UUID, *, rolled_back_by: str, reason: str) -> _Promotion:
        return _Promotion(experiment_id, "state-space-role", "rolled_back", rolled_back_by)

    monkeypatch.setattr(
        "wnba_services.learning_loop.experiments.rollback_promotion", _rollback
    )
    body = client.post(
        f"/api/learning/experiments/{experiment_id}/rollback", json=REASON
    ).json()
    assert body["status"] == "rolled_back"
    assert body["actor"] == OWNER


def test_abandoning_an_experiment_records_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment_id = uuid4()

    def _abandon(experiment_id: UUID, *, actor: str, reason: str) -> _Promotion:
        return _Promotion(experiment_id, "hierarchical-bayes", "abandoned", actor)

    monkeypatch.setattr(
        "wnba_services.learning_loop.experiments.abandon_experiment", _abandon
    )
    body = client.post(
        f"/api/learning/experiments/{experiment_id}/abandon", json=REASON
    ).json()
    assert body["status"] == "abandoned"
    assert body["actor"] == OWNER


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


def test_proposal_review_marks_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal_id = uuid4()
    monkeypatch.setattr(
        "wnba_store.db.connect", lambda: _Connection({"proposal_id": proposal_id})
    )
    body = client.post(
        f"/api/learning/proposals/{proposal_id}/review", json={"verdict": "approved"}
    ).json()
    assert body == {"proposal_id": str(proposal_id), "status": "approved"}


def test_reviewing_an_already_decided_proposal_is_a_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection(None))
    response = client.post(
        f"/api/learning/proposals/{uuid4()}/review", json={"verdict": "rejected"}
    )
    assert response.status_code == 422
