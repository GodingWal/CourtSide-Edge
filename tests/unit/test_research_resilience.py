"""The research desk under failure, and the measurement of the agents on it.

Three things used to be true and are no longer. A single provider error discarded an entire run's
work. Every agent counted equally in the consensus no matter how it had scored. And the score
itself rewarded hedging, so the safest way to look credible was to answer 0.5 forever.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from wnba_services.research_agents.advisor import Advisor
from wnba_services.research_agents.deepseek import (
    AgentForecastDraft,
    DeepSeekResearchClient,
    SkepticReview,
)
from wnba_services.research_agents.organization import MINIMUM_CONSENSUS_WEIGHT, synthesize
from wnba_services.research_agents.pat_workflow import _blind_role

from tests.fixtures.fake_db import FakeDatabase, statements_matching

RUN = UUID("11111111-2222-3333-4444-555555555555")


def draft(probability: float, *flags: str) -> AgentForecastDraft:
    return AgentForecastDraft(
        advisory_probability=probability,
        rationale="a rationale long enough to satisfy the schema",
        evidence_ids=[uuid4()],
        risk_flags=list(flags),
    )


def _cursor(database: FakeDatabase) -> Any:
    return database.connect().cursor()


# --------------------------------------------------------------------------------------
# One failed agent is a missing voice, not a lost run
# --------------------------------------------------------------------------------------
def test_a_failed_call_returns_none_and_is_recorded_as_a_fallback() -> None:
    database = FakeDatabase()
    advisor = Advisor(_cursor(database), enabled=True)

    def explode() -> tuple[Any, str, str]:
        raise httpx.ReadTimeout("provider timed out")

    assert advisor.attempt(task="agent_forecast_round_one", subject="x", call=explode) is None
    recorded = statements_matching(database.statements, "INSERT INTO wnba.model_advisories")
    assert len(recorded) == 1
    assert "fallback" in recorded[0][1]
    assert advisor.outcomes[0].disposition == "fallback"


def test_a_successful_call_returns_its_result_and_records_the_hashes() -> None:
    database = FakeDatabase()
    advisor = Advisor(_cursor(database), enabled=True)

    result = advisor.attempt(
        task="agent_forecast_round_one",
        subject="x",
        call=lambda: (draft(0.6), "prompt-hash", "response-hash"),
    )

    assert result is not None
    assert result.advisory_probability == 0.6
    params = statements_matching(database.statements, "INSERT INTO wnba.model_advisories")[0][1]
    assert "prompt-hash" in params and "response-hash" in params


def test_a_schema_violation_is_a_fallback_rather_than_a_crash() -> None:
    database = FakeDatabase()
    advisor = Advisor(_cursor(database), enabled=True)

    def invalid() -> tuple[Any, str, str]:
        return (
            AgentForecastDraft(
                advisory_probability=2.0, rationale="", evidence_ids=[], risk_flags=[]
            ),
            "",
            "",
        )

    with pytest.raises(ValidationError):
        invalid()
    assert advisor.attempt(task="t", subject="s", call=invalid) is None


def test_the_blind_seat_rotates_but_is_reproducible() -> None:
    """A seat that is always blind is not a control, it is a differently-configured agent."""
    roles = ["availability", "market", "matchup", "rotation"]
    chosen = {_blind_role(uuid4(), roles) for _ in range(200)}

    assert len(chosen) > 1, "the control condition must not land on one role forever"
    assert _blind_role(RUN, roles) == _blind_role(RUN, roles), (
        "and must be recoverable from the run"
    )


# --------------------------------------------------------------------------------------
# The consensus listens to measured agents more than unmeasured ones
# --------------------------------------------------------------------------------------
def test_credibility_weights_the_consensus_without_silencing_anyone() -> None:
    roles = ["availability", "market"]
    views = [draft(0.80), draft(0.40)]

    unweighted = synthesize(
        model_probability=0.6, round_two=views, audit_blocked=False, data_quality_score=0.9
    )
    weighted = synthesize(
        model_probability=0.6,
        round_two=views,
        audit_blocked=False,
        data_quality_score=0.9,
        credibility={"availability": 0.9, "market": 0.3},
        roles=roles,
    )

    assert unweighted.advisory_probability == pytest.approx(0.60)
    assert weighted.advisory_probability is not None
    assert weighted.advisory_probability > 0.60, "the better-scored agent should carry more"
    assert MINIMUM_CONSENSUS_WEIGHT > 0, "a poor score quiets an agent; it never deletes it"


def test_a_worthless_agent_is_quieted_not_removed() -> None:
    weighted = synthesize(
        model_probability=0.6,
        round_two=[draft(0.90), draft(0.30)],
        audit_blocked=False,
        data_quality_score=0.9,
        credibility={"a": 0.95, "b": 0.0},
        roles=["a", "b"],
    )

    assert weighted.advisory_probability is not None
    assert weighted.advisory_probability < 0.90, "b still has a fifth of a voice"


def test_dispersion_stays_unweighted_so_the_desk_cannot_be_talked_into_agreement() -> None:
    divided = [draft(0.85), draft(0.35)]

    weighted = synthesize(
        model_probability=0.6,
        round_two=divided,
        audit_blocked=False,
        data_quality_score=0.9,
        credibility={"a": 0.95, "b": 0.25},
        roles=["a", "b"],
    )

    assert weighted.disagreement == pytest.approx(0.25)
    assert weighted.disposition == "caution"


def test_a_highly_fragile_skeptic_review_forces_caution_without_moving_the_number() -> None:
    result = synthesize(
        model_probability=0.62,
        round_two=[draft(0.62), draft(0.63)],
        audit_blocked=False,
        data_quality_score=0.95,
        skeptic_fragility="high",
        skeptic_flags=("minutes evidence is two days stale",),
    )

    assert result.disposition == "caution"
    assert result.model_probability == 0.62, "the statistical probability is never rewritten"
    assert "minutes evidence is two days stale" in result.risk_flags


# --------------------------------------------------------------------------------------
# The skeptic answers on its own schema
# --------------------------------------------------------------------------------------
def test_the_skeptic_cannot_return_a_probability() -> None:
    """It used to answer on the forecast schema, which pulled it toward the consensus."""
    with pytest.raises(ValidationError):
        SkepticReview.model_validate(
            {
                "summary": "a summary long enough to pass validation",
                "failure_modes": [],
                "fragility": "high",
                "risk_flags": [],
                "advisory_probability": 0.3,
            }
        )


def test_a_failure_mode_must_state_what_would_disconfirm_it() -> None:
    with pytest.raises(ValidationError):
        SkepticReview.model_validate(
            {
                "summary": "a summary long enough to pass validation",
                "failure_modes": [
                    {
                        "description": "the minutes projection looks fragile",
                        "severity": "high",
                        "evidence_ids": [str(uuid4())],
                    }
                ],
                "fragility": "high",
                "risk_flags": [],
            }
        )


# --------------------------------------------------------------------------------------
# The client survives a provider having a bad minute, and says that it did
# --------------------------------------------------------------------------------------
def _envelope(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    }


def test_a_retryable_status_is_retried_and_the_reason_kept() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "a summary long enough to pass validation here",
                    "failure_modes": [],
                    "fragility": "low",
                    "risk_flags": [],
                }
            ),
        )

    client = DeepSeekResearchClient(
        api_key="k", transport=httpx.MockTransport(handler), sleep=lambda _seconds: None
    )
    review, _, _ = client.challenge(question="q", evidence={})

    assert isinstance(review, SkepticReview)
    assert len(attempts) == 2
    # The cost of the call that eventually worked, including the attempt that did not. An
    # advisory row that reported one attempt here would understate what the desk spends when the
    # provider is busy, which is exactly when the number matters.
    assert client.last_attempts == 2
    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 120
    assert client.last_latency_ms is not None


def test_a_client_error_is_not_retried() -> None:
    """A 400 will be a 400 again. Retrying it wastes the window before the market locks."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    client = DeepSeekResearchClient(
        api_key="k", transport=httpx.MockTransport(handler), sleep=lambda _seconds: None
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.challenge(question="q", evidence={})

    assert len(attempts) == 1
