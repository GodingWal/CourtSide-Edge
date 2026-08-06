"""The provider boundary: what is accepted, what is rejected, and what is worth asking twice.

The retry tests carry most of the weight here. A client that retries everything eventually turns
a model that keeps citing evidence it was never given into a model that gets away with it once,
and that single lucky response is the one that lands in the evidence file. What is worth asking
twice is a rejection the model can see and repair: the feedback re-ask shows it exactly why its
answer was refused, once, and a repeated refusal still fails closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from wnba_services.research_agents.deepseek import (
    DEFAULT_MAX_TOKENS,
    MAX_RETRY_AFTER_SECONDS,
    SKEPTIC_MAX_TOKENS,
    DeepSeekResearchClient,
)


def response(
    content: dict[str, object],
    *,
    finish_reason: str = "stop",
    usage: dict[str, object] | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {
        "choices": [{"finish_reason": finish_reason, "message": {"content": json.dumps(content)}}]
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def analysis_body(evidence_id: object) -> dict[str, object]:
    return {
        "conclusion": "Minutes remain uncertain.",
        "confidence": 0.7,
        "claims": [
            {
                "predicate": "availability",
                "value": "questionable",
                "confidence": 0.8,
                "evidence_ids": [str(evidence_id)],
                "contradicting_evidence_ids": [],
            }
        ],
        "risk_flags": ["status may change before tip"],
    }


class Recorder:
    """A transport that answers from a script and counts how often it was asked."""

    def __init__(self, *replies: httpx.Response | Exception) -> None:
        self._replies = list(replies)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        reply = self._replies[min(self.calls - 1, len(self._replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def build(recorder: Recorder, **kwargs: Any) -> tuple[DeepSeekResearchClient, list[float]]:
    """A client wired to the recorder, plus the backoff delays it would have slept."""
    slept: list[float] = []
    provider = DeepSeekResearchClient(
        api_key="test",
        transport=httpx.MockTransport(recorder),
        backoff_seconds=0.0,
        sleep=slept.append,
        **kwargs,
    )
    return provider, slept


def client(recorder: Recorder, **kwargs: Any) -> DeepSeekResearchClient:
    return build(recorder, **kwargs)[0]


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------
def test_deepseek_analysis_accepts_only_supplied_evidence() -> None:
    evidence_id = uuid4()
    recorder = Recorder(response(analysis_body(evidence_id)))
    result = client(recorder).analyze(
        role="availability",
        question="What is known?",
        evidence={evidence_id: "Official report: questionable."},
    )
    assert result.analysis.claims[0].evidence_ids == [evidence_id]
    assert len(result.prompt_sha256) == len(result.response_sha256) == 64
    assert result.attempts == 1


def test_deepseek_analysis_rejects_hallucinated_evidence_id() -> None:
    supplied = uuid4()
    recorder = Recorder(response(analysis_body(uuid4())))
    with pytest.raises(ValueError, match="unknown evidence"):
        client(recorder).analyze(
            role="rotation", question="Role?", evidence={supplied: "Bench usage."}
        )


def test_deepseek_analysis_fails_without_key() -> None:
    provider = DeepSeekResearchClient(api_key="")
    with pytest.raises(RuntimeError, match="not configured"):
        provider.analyze(role="skeptic", question="Challenge", evidence={})


def test_the_skeptic_directive_changes_the_prompt_hash() -> None:
    """Otherwise two agents given different instructions would leave the same audit record."""
    evidence_id = uuid4()
    evidence = {evidence_id: "Bench usage."}
    plain = client(Recorder(response(analysis_body(evidence_id)))).analyze(
        role="skeptic", question="Challenge", evidence=evidence
    )
    directed = client(Recorder(response(analysis_body(evidence_id)))).analyze(
        role="skeptic", question="Challenge", evidence=evidence, directive="Contradict explicitly."
    )
    assert plain.prompt_sha256 != directed.prompt_sha256


# --------------------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------------------
def test_congestion_is_retried_and_then_succeeds() -> None:
    evidence_id = uuid4()
    recorder = Recorder(
        httpx.Response(429, json={"error": "rate limited"}),
        response(analysis_body(evidence_id)),
    )
    result = client(recorder).analyze(
        role="market", question="Fresh?", evidence={evidence_id: "Quote observed."}
    )
    assert recorder.calls == 2
    assert result.attempts == 2


def test_transport_failure_is_retried() -> None:
    evidence_id = uuid4()
    recorder = Recorder(
        httpx.ConnectError("connection reset"),
        response(analysis_body(evidence_id)),
    )
    result = client(recorder).analyze(
        role="matchup", question="Pace?", evidence={evidence_id: "Pace observed."}
    )
    assert recorder.calls == 2
    assert result.attempts == 2


def test_retries_stop_at_the_configured_limit() -> None:
    recorder = Recorder(httpx.Response(503, json={"error": "unavailable"}))
    provider, slept = build(recorder, max_attempts=3)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        provider.analyze(role="market", question="Fresh?", evidence={uuid4(): "Quote."})
    assert recorder.calls == 3
    # Backoff is applied between attempts, never after the last one.
    assert len(slept) == 2


def test_a_client_error_is_not_retried() -> None:
    """A malformed request or a rejected key will be just as wrong the second time."""
    recorder = Recorder(httpx.Response(401, json={"error": "invalid key"}))
    with pytest.raises(httpx.HTTPStatusError):
        client(recorder).analyze(role="market", question="Fresh?", evidence={uuid4(): "Quote."})
    assert recorder.calls == 1


def test_a_rejected_response_earns_one_feedback_reask_then_fails_closed() -> None:
    """The re-ask shows the model exactly why it was refused; repeating the sin still fails."""
    supplied = uuid4()
    recorder = Recorder(response(analysis_body(uuid4())))
    with pytest.raises(ValueError, match="unknown evidence"):
        client(recorder).analyze(role="rotation", question="Role?", evidence={supplied: "Bench."})
    assert recorder.calls == 2


def test_a_truncated_response_earns_one_feedback_reask_then_fails_closed() -> None:
    evidence_id = uuid4()
    recorder = Recorder(response(analysis_body(evidence_id), finish_reason="length"))
    with pytest.raises(ValueError, match="did not finish cleanly"):
        client(recorder).analyze(role="rotation", question="Role?", evidence={evidence_id: "x"})
    assert recorder.calls == 2


def test_feedback_retries_can_be_disabled() -> None:
    """The old fail-closed-once behaviour remains available per deployment."""
    evidence_id = uuid4()
    recorder = Recorder(response(analysis_body(evidence_id), finish_reason="length"))
    with pytest.raises(ValueError, match="did not finish cleanly"):
        client(recorder, feedback_retries=0).analyze(
            role="rotation", question="Role?", evidence={evidence_id: "x"}
        )
    assert recorder.calls == 1


def test_a_corrected_answer_after_feedback_is_accepted() -> None:
    """The common production case: one hallucinated citation, fixed when it is pointed out."""
    supplied = uuid4()
    recorder = CapturingRecorder(
        response(analysis_body(uuid4())),
        response(analysis_body(supplied)),
    )
    result = client(recorder).analyze(
        role="rotation", question="Role?", evidence={supplied: "Bench usage."}
    )
    assert recorder.calls == 2
    assert result.attempts == 2
    assert result.analysis.claims[0].evidence_ids == [supplied]


def test_the_feedback_reask_shows_the_rejection_and_its_reason() -> None:
    """Without the reason in the prompt the re-ask would be a blind re-roll, which is forbidden."""
    supplied = uuid4()
    rejected = analysis_body(uuid4())
    recorder = CapturingRecorder(
        response(rejected),
        response(analysis_body(supplied)),
    )
    client(recorder).analyze(role="rotation", question="Role?", evidence={supplied: "Bench."})
    assert len(recorder.payloads) == 2
    messages = recorder.payloads[1]["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert json.loads(messages[2]["content"]) == rejected
    assert "rejected" in messages[3]["content"]
    assert "unknown evidence ids" in messages[3]["content"]


def test_a_schema_violation_is_fed_back_with_the_validation_error() -> None:
    """A draft one field away from valid should not cost the desk a deterministic fallback."""
    evidence_id = uuid4()
    broken = analysis_body(evidence_id)
    broken["confidence"] = 1.7
    recorder = CapturingRecorder(
        response(broken),
        response(analysis_body(evidence_id)),
    )
    result = client(recorder).analyze(
        role="availability", question="Known?", evidence={evidence_id: "Report."}
    )
    assert result.analysis.confidence == 0.7
    messages = recorder.payloads[1]["messages"]
    assert "rejected" in messages[3]["content"]


def test_a_truncation_then_a_finished_answer_is_accepted() -> None:
    evidence_id = uuid4()
    recorder = Recorder(
        response(analysis_body(evidence_id), finish_reason="length"),
        response(analysis_body(evidence_id)),
    )
    result = client(recorder).analyze(
        role="rotation", question="Role?", evidence={evidence_id: "x"}
    )
    assert recorder.calls == 2
    assert result.attempts == 2


def test_retry_after_is_honoured_but_capped() -> None:
    """A provider asking for an hour cannot hold frozen evidence past the market lock."""
    evidence_id = uuid4()
    recorder = Recorder(
        httpx.Response(429, headers={"Retry-After": "3600"}),
        response(analysis_body(evidence_id)),
    )
    provider, slept = build(recorder)
    provider.analyze(role="market", question="Fresh?", evidence={evidence_id: "Quote."})
    assert slept == [MAX_RETRY_AFTER_SECONDS]


# --------------------------------------------------------------------------------------
# Accounting
# --------------------------------------------------------------------------------------
def test_reported_token_usage_is_captured() -> None:
    evidence_id = uuid4()
    recorder = Recorder(
        response(
            analysis_body(evidence_id),
            usage={"prompt_tokens": 1200, "completion_tokens": 340},
        )
    )
    result = client(recorder).analyze(
        role="availability", question="Known?", evidence={evidence_id: "Report."}
    )
    assert result.usage is not None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (1200, 340)


def test_unreported_token_usage_is_unknown_rather_than_zero() -> None:
    evidence_id = uuid4()
    recorder = Recorder(response(analysis_body(evidence_id)))
    result = client(recorder).analyze(
        role="availability", question="Known?", evidence={evidence_id: "Report."}
    )
    assert result.usage is None


# --------------------------------------------------------------------------------------
# Research context: precedents, track record, token budgets, evidence ordering
# --------------------------------------------------------------------------------------
class CapturingRecorder(Recorder):
    """A recorder that also keeps the request payloads it was given."""

    def __init__(self, *replies: httpx.Response | Exception) -> None:
        super().__init__(*replies)
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        return super().__call__(request)


def forecast_body(evidence_id: object) -> dict[str, object]:
    return {
        "advisory_probability": 0.55,
        "rationale": "Pace profile supports the over.",
        "evidence_ids": [str(evidence_id)],
        "risk_flags": [],
    }


def skeptic_body() -> dict[str, object]:
    return {
        "summary": "Concern is answerable.",
        "failure_modes": [],
        "fragility": "low",
        "risk_flags": [],
    }


def user_message(payload: dict[str, Any]) -> dict[str, Any]:
    """The decoded user prompt from a captured chat request."""
    return json.loads(payload["messages"][1]["content"])


def test_forecast_prompt_carries_precedents_and_track_record() -> None:
    """Settled precedents and the seat's own measured record must reach the analyst."""
    evidence_id = uuid4()
    recorder = CapturingRecorder(response(forecast_body(evidence_id)))
    client(recorder).forecast(
        role="availability",
        question="Over or under?",
        evidence={evidence_id: "Official report."},
        precedents=["line=10.5 probability=0.6 actual=12 hit=True"],
        track_record="8 settled advisory views; Brier 0.210; skill vs model +0.030",
    )
    prompt = user_message(recorder.payloads[0])
    assert prompt["precedents_with_outcomes"] == ["line=10.5 probability=0.6 actual=12 hit=True"]
    assert (
        prompt["your_recent_track_record"]
        == "8 settled advisory views; Brier 0.210; skill vs model +0.030"
    )


def test_forecast_context_defaults_to_empty_rather_than_missing() -> None:
    evidence_id = uuid4()
    recorder = CapturingRecorder(response(forecast_body(evidence_id)))
    client(recorder).forecast(
        role="rotation", question="Over or under?", evidence={evidence_id: "Bench usage."}
    )
    prompt = user_message(recorder.payloads[0])
    assert prompt["precedents_with_outcomes"] == []
    assert prompt["your_recent_track_record"] == ""


def test_skeptic_prompt_carries_precedents_but_no_track_record() -> None:
    """The skeptic is calibrated by precedents; a per-seat record would be meaningless to it."""
    evidence_id = uuid4()
    recorder = CapturingRecorder(response(skeptic_body()))
    client(recorder).challenge(
        question="How could this be wrong?",
        evidence={evidence_id: "Official report."},
        precedents=["line=10.5 probability=0.6 actual=12 hit=True"],
    )
    prompt = user_message(recorder.payloads[0])
    assert prompt["precedents_with_outcomes"] == ["line=10.5 probability=0.6 actual=12 hit=True"]
    assert "your_recent_track_record" not in prompt


def test_the_skeptic_gets_the_larger_token_budget() -> None:
    """Failure-mode prose plus thinking needs more room than a terse analyst draft."""
    evidence_id = uuid4()
    forecaster = CapturingRecorder(response(forecast_body(evidence_id)))
    client(forecaster).forecast(
        role="rotation", question="Over or under?", evidence={evidence_id: "Bench usage."}
    )
    skeptic = CapturingRecorder(response(skeptic_body()))
    client(skeptic).challenge(question="How could this be wrong?", evidence={evidence_id: "Same."})
    assert forecaster.payloads[0]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert skeptic.payloads[0]["max_tokens"] == SKEPTIC_MAX_TOKENS
    assert SKEPTIC_MAX_TOKENS > DEFAULT_MAX_TOKENS


def test_token_budgets_are_configurable_per_deployment() -> None:
    evidence_id = uuid4()
    recorder = CapturingRecorder(response(skeptic_body()))
    client(recorder, skeptic_max_tokens=1234).challenge(
        question="How could this be wrong?", evidence={evidence_id: "Report."}
    )
    assert recorder.payloads[0]["max_tokens"] == 1234


def test_evidence_is_serialised_in_content_order() -> None:
    """Id order is random per run, which would defeat the provider's prefix cache; the
    corpus must arrive in a stable order derived from the text itself."""
    first, second = "AAA first text", "ZZZ second text"
    expected = sorted((first, second), key=lambda t: hashlib.sha256(t.encode()).hexdigest())
    evidence = {uuid4(): first, uuid4(): second}
    recorder = CapturingRecorder(response(analysis_body(next(iter(evidence)))))
    client(recorder).analyze(role="availability", question="What is known?", evidence=evidence)
    serialised = user_message(recorder.payloads[0])["evidence"]
    assert [item["text"] for item in serialised] == expected
    assert {item["evidence_id"] for item in serialised} == {str(key) for key in evidence}
