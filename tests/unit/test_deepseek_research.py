"""The provider boundary: what is accepted, what is rejected, and what is worth asking twice.

The retry tests carry most of the weight here. A client that retries everything eventually turns
a model that keeps citing evidence it was never given into a model that gets away with it once,
and that single lucky response is the one that lands in the evidence file.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from wnba_services.research_agents.deepseek import (
    MAX_RETRY_AFTER_SECONDS,
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


def test_a_rejected_response_is_not_retried() -> None:
    """Asking again after a hallucinated citation is rolling dice until one comes up compliant."""
    supplied = uuid4()
    recorder = Recorder(response(analysis_body(uuid4())))
    with pytest.raises(ValueError, match="unknown evidence"):
        client(recorder).analyze(role="rotation", question="Role?", evidence={supplied: "Bench."})
    assert recorder.calls == 1


def test_a_truncated_response_is_not_retried() -> None:
    evidence_id = uuid4()
    recorder = Recorder(response(analysis_body(evidence_id), finish_reason="length"))
    with pytest.raises(ValueError, match="did not finish cleanly"):
        client(recorder).analyze(role="rotation", question="Role?", evidence={evidence_id: "x"})
    assert recorder.calls == 1


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
