"""Fail-closed DeepSeek JSON client for evidence-grounded research agents."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    predicate: Annotated[str, Field(min_length=1, max_length=120)]
    value: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_ids: Annotated[list[UUID], Field(min_length=1)]
    contradicting_evidence_ids: list[UUID] = Field(default_factory=list)


class AgentAnalysis(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    conclusion: Annotated[str, Field(min_length=1, max_length=2000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    claims: list[ClaimDraft]
    risk_flags: list[Annotated[str, Field(min_length=1, max_length=300)]]

    @model_validator(mode="after")
    def citations_are_unique(self) -> AgentAnalysis:
        for claim in self.claims:
            if set(claim.evidence_ids) & set(claim.contradicting_evidence_ids):
                raise ValueError("evidence cannot support and contradict the same claim")
        return self


class AnalysisRevision(BaseModel):
    """A second-round position, after the analyst has read what its peers concluded.

    ``revised_confidence`` is confidence in *this analyst's own conclusion*, not a probability
    that the prop goes over. Nothing in this schema can express a prop outcome probability, which
    is the structural reason a revision cannot become a forecast: there is no field to put one in.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    conclusion: Annotated[str, Field(min_length=1, max_length=2000)]
    revised_confidence: Annotated[float, Field(ge=0, le=1)]
    revision_reason: Annotated[str, Field(min_length=1, max_length=1000)]
    concessions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list
    )
    """Points from a peer this analyst now accepts. Recorded because an adversarial round where
    nobody ever concedes anything is five analysts restating themselves at greater length."""

    unresolved_disagreements: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list
    )
    claims: list[ClaimDraft] = Field(default_factory=list)
    risk_flags: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list
    )


class ProposedCondition(BaseModel):
    """One condition, in the closed rule vocabulary and nothing else.

    ``field`` is a plain string here rather than an enum because the authoritative vocabulary
    lives in ``wnba_rules.dsl.RULE_FIELDS`` and is checked there. Duplicating it would create two
    lists that can disagree, and the one that disagrees silently is always the copy.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    field: Annotated[str, Field(min_length=1, max_length=60)]
    operator: Annotated[str, Field(min_length=1, max_length=10)]
    value: float | int | str | list[str]


class ProposedAction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    kind: Literal["block", "shrink_toward_even", "flag_for_review", "require_evidence"]
    """The complete action vocabulary, restated as a literal so a proposal naming anything else
    fails at parse time rather than at review time. Every member restricts or annotates; there is
    no member that raises a probability, and a model cannot invent one."""

    magnitude: Annotated[float, Field(ge=0, le=1)] = 0.0
    note: Annotated[str, Field(max_length=500)] = ""


class RuleProposalDraft(BaseModel):
    """A rule an agent wants to propose, with the provenance a reviewer needs to interrogate it.

    Every field below is required because each one answers a question a reviewer would otherwise
    have to ask the model a second time -- and by then the model would happily invent an answer.
    A proposal missing its confounders is not a smaller proposal; it is an assertion.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    rule_id: Annotated[str, Field(min_length=3, max_length=80, pattern=r"^[a-z0-9_]+$")]
    title: Annotated[str, Field(min_length=8, max_length=200)]
    rationale: Annotated[str, Field(min_length=40, max_length=2000)]
    mechanism: Annotated[str, Field(min_length=40, max_length=2000)]
    """*Why* the pattern would exist, not that it does. A correlation with no mechanism is the
    thing backtests are worst at rejecting, because it will fit the sample it was found in."""

    confounders: Annotated[
        list[Annotated[str, Field(min_length=5, max_length=300)]], Field(min_length=1)
    ]
    expiry_conditions: Annotated[str, Field(min_length=20, max_length=1000)]
    """What would have to change about the world for this rule to stop being true."""

    withdrawal_criteria: Annotated[str, Field(min_length=20, max_length=1000)]
    """What measured result would mean it should be retired. Stated before the measurement, so
    the bar cannot move once the number arrives."""

    conditions: Annotated[list[ProposedCondition], Field(min_length=1, max_length=6)]
    combinator: Literal["all", "any"] = "all"
    action: ProposedAction
    evidence_ids: Annotated[list[UUID], Field(min_length=1)]
    expected_direction: Literal["increase", "decrease", "conditional"]
    causal_statement: Annotated[str, Field(min_length=30, max_length=1000)]


class DeepSeekResearchClient:
    """Small provider adapter; no model response bypasses strict validation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        configured_base_url = (
            base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        )
        self.base_url = configured_base_url.rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.timeout_seconds = timeout_seconds or float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        self.transport = transport

    _NO_FORECAST_RULE = (
        "Do not create a projection, betting recommendation, stake, or probability of a prop "
        "outcome. You have no field in which to express one and any attempt will be rejected. "
        "If evidence is insufficient, say so rather than reasoning past it."
    )

    def _complete[T: BaseModel](
        self,
        *,
        system: str,
        body: dict[str, Any],
        schema: type[T],
        max_tokens: int = 4000,
    ) -> tuple[T, str, str]:
        """One request, one strict parse. Every call type goes through here.

        Sharing the transport is not just deduplication: it means the finish-reason check, the
        empty-content check and the strict-schema parse cannot be present on one call type and
        forgotten on another. A validation that exists in three copies eventually exists in two.
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        user = json.dumps(body, sort_keys=True, default=str)
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "max_tokens": max_tokens,
            "stream": False,
        }
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as client:
            response = client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict):
            raise ValueError("DeepSeek returned a non-object response")
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("DeepSeek returned no choices")
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            raise ValueError("DeepSeek response did not finish cleanly")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("DeepSeek returned empty content")
        parsed = schema.model_validate_json(content)
        response_hash = hashlib.sha256(content.encode()).hexdigest()
        return parsed, prompt_hash, response_hash

    @staticmethod
    def _evidence_payload(evidence: dict[UUID, str]) -> list[dict[str, str]]:
        return [
            {"evidence_id": str(evidence_id), "text": text}
            for evidence_id, text in sorted(evidence.items(), key=lambda item: str(item[0]))
        ]

    @staticmethod
    def _reject_uncited(cited: set[UUID], allowed: set[UUID]) -> None:
        unknown = cited - allowed
        if unknown:
            raise ValueError(f"DeepSeek cited unknown evidence ids: {sorted(map(str, unknown))}")

    def analyze(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
    ) -> tuple[AgentAnalysis, str, str]:
        """First-round analysis. The analyst sees evidence and no peer opinions."""
        system = (
            f"You are the {role} analyst in a WNBA player-prop research system. Return JSON only. "
            "Use only supplied evidence. Every claim must cite one or more supplied evidence_ids. "
            "You are working independently: you have not been shown any other analyst's view and "
            "must not speculate about one. " + self._NO_FORECAST_RULE
        )
        analysis, prompt_hash, response_hash = self._complete(
            system=system,
            body={
                "question": question,
                "evidence": self._evidence_payload(evidence),
                "json_shape": {
                    "conclusion": "string",
                    "confidence": 0.0,
                    "claims": [
                        {
                            "predicate": "string",
                            "value": "string",
                            "confidence": 0.0,
                            "evidence_ids": ["uuid"],
                            "contradicting_evidence_ids": [],
                        }
                    ],
                    "risk_flags": ["string"],
                },
            },
            schema=AgentAnalysis,
        )
        self._reject_uncited(
            {
                evidence_id
                for claim in analysis.claims
                for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
            },
            set(evidence),
        )
        return analysis, prompt_hash, response_hash

    def revise(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
        own_conclusion: str,
        own_confidence: float,
        peer_positions: list[dict[str, Any]],
    ) -> tuple[AnalysisRevision, str, str]:
        """Second round: the analyst reads its peers and may move.

        The prompt asks for disagreement explicitly. An adversarial round in which every analyst
        politely converges has measured agreeableness rather than truth, and the honest output of
        this call is frequently ``revised_confidence`` going *down* with nothing conceded.
        """
        system = (
            f"You are the {role} analyst in a WNBA player-prop research system, in the second "
            "and adversarial round. Return JSON only. You are now shown what the other analysts "
            "concluded independently. State plainly where you were wrong and concede it, and "
            "state plainly where you still disagree and why. Do not converge for the sake of "
            "agreement: unanimity produced by deference is worse than a recorded disagreement. "
            "Every new claim must still cite supplied evidence_ids. " + self._NO_FORECAST_RULE
        )
        revision, prompt_hash, response_hash = self._complete(
            system=system,
            body={
                "question": question,
                "your_first_round": {
                    "conclusion": own_conclusion,
                    "confidence": own_confidence,
                },
                "peer_positions": peer_positions,
                "evidence": self._evidence_payload(evidence),
                "json_shape": {
                    "conclusion": "string",
                    "revised_confidence": 0.0,
                    "revision_reason": "string",
                    "concessions": ["string"],
                    "unresolved_disagreements": ["string"],
                    "claims": [],
                    "risk_flags": ["string"],
                },
            },
            schema=AnalysisRevision,
        )
        self._reject_uncited(
            {
                evidence_id
                for claim in revision.claims
                for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
            },
            set(evidence),
        )
        return revision, prompt_hash, response_hash

    def propose_rule(
        self,
        *,
        finding: str,
        vocabulary: dict[str, str],
        actions: list[str],
        evidence: dict[UUID, str],
        observed_ranges: dict[str, dict[str, float]],
    ) -> tuple[RuleProposalDraft, str, str]:
        """Turn a measured finding into a structured rule proposal in the closed vocabulary.

        The model is handed the vocabulary rather than trusted to remember it, and handed the
        observed range of each field so a threshold it proposes is anchored to values that
        actually occur. It is told, in the prompt and again by the schema, that it is producing a
        document rather than code -- and the schema is what makes that true, since there is no
        field into which Python could be placed.
        """
        system = (
            "You are the research director of a WNBA player-prop system, proposing one analyst "
            "rule. Return JSON only. You are writing a STRUCTURED DOCUMENT, never code: there is "
            "no field for an expression, a script, or a formula, and any such content will be "
            "rejected. Use only the supplied fields and operators. Every action available to you "
            "restricts or annotates a forecast -- block it, shrink it toward even, flag it for "
            "review, or require live cited evidence. No action can raise a probability or "
            "increase exposure and you must not attempt to express one. Thresholds must fall "
            "inside the supplied observed ranges. Cite the evidence your finding rests on."
        )
        return self._complete(
            system=system,
            body={
                "finding": finding,
                "allowed_fields": vocabulary,
                "allowed_operators": ["lt", "lte", "gt", "gte", "eq", "neq", "in", "not_in"],
                "allowed_actions": actions,
                "observed_field_ranges": observed_ranges,
                "evidence": self._evidence_payload(evidence),
                "json_shape": {
                    "rule_id": "lower_snake_case",
                    "title": "string",
                    "rationale": "string",
                    "mechanism": "why this would be true, not that it is",
                    "confounders": ["string"],
                    "expiry_conditions": "what would have to change for this to stop holding",
                    "withdrawal_criteria": "what measured result would retire this rule",
                    "conditions": [{"field": "string", "operator": "string", "value": 0.0}],
                    "combinator": "all",
                    "action": {"kind": "string", "magnitude": 0.0, "note": "string"},
                    "evidence_ids": ["uuid"],
                    "expected_direction": "increase|decrease|conditional",
                    "causal_statement": "string",
                },
            },
            schema=RuleProposalDraft,
            max_tokens=5000,
        )
