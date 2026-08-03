import json
from uuid import uuid4

import httpx
import pytest
from wnba_services.research_agents.deepseek import DeepSeekResearchClient


def response(content: dict[str, object], *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": finish_reason, "message": {"content": json.dumps(content)}}
            ]
        },
    )


def test_deepseek_analysis_accepts_only_supplied_evidence() -> None:
    evidence_id = uuid4()
    transport = httpx.MockTransport(
        lambda request: response(
            {
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
        )
    )
    client = DeepSeekResearchClient(api_key="test", transport=transport)
    analysis, prompt_hash, response_hash = client.analyze(
        role="availability",
        question="What is known?",
        evidence={evidence_id: "Official report: questionable."},
    )
    assert analysis.claims[0].evidence_ids == [evidence_id]
    assert len(prompt_hash) == len(response_hash) == 64


def test_deepseek_analysis_rejects_hallucinated_evidence_id() -> None:
    supplied = uuid4()
    hallucinated = uuid4()
    transport = httpx.MockTransport(
        lambda request: response(
            {
                "conclusion": "Unsupported.",
                "confidence": 0.5,
                "claims": [
                    {
                        "predicate": "role",
                        "value": "starter",
                        "confidence": 0.5,
                        "evidence_ids": [str(hallucinated)],
                        "contradicting_evidence_ids": [],
                    }
                ],
                "risk_flags": [],
            }
        )
    )
    client = DeepSeekResearchClient(api_key="test", transport=transport)
    with pytest.raises(ValueError, match="unknown evidence"):
        client.analyze(role="rotation", question="Role?", evidence={supplied: "Bench usage."})


def test_deepseek_analysis_fails_without_key() -> None:
    client = DeepSeekResearchClient(api_key="")
    with pytest.raises(RuntimeError, match="not configured"):
        client.analyze(role="skeptic", question="Challenge", evidence={})
