"""Fail-closed DeepSeek JSON client for evidence-grounded research agents."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, replace
from typing import Annotated, Any
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


class AgentForecastDraft(BaseModel):
    """Advisory research probability; never replaces the statistical model probability."""

    model_config = ConfigDict(strict=True, extra="forbid")
    advisory_probability: Annotated[float, Field(ge=0, le=1)]
    rationale: Annotated[str, Field(min_length=20, max_length=2000)]
    evidence_ids: Annotated[list[UUID], Field(min_length=1)]
    risk_flags: list[Annotated[str, Field(min_length=1, max_length=300)]]


class FailureMode(BaseModel):
    """One way the forecast could be wrong, stated so it can be checked."""

    model_config = ConfigDict(strict=True, extra="forbid")

    description: Annotated[str, Field(min_length=10, max_length=500)]
    severity: Annotated[str, Field(pattern=r"^(low|moderate|high)$")]
    disconfirming_observation: Annotated[str, Field(min_length=10, max_length=500)]
    """What would show this concern to be unfounded. A criticism that nothing could answer is
    not a criticism, it is a mood, and it costs the reader the same attention as a real one."""

    evidence_ids: Annotated[list[UUID], Field(min_length=1)]


class SkepticReview(BaseModel):
    """The skeptic's output, deliberately without a probability.

    The skeptic used to answer on the same schema as everybody else, which meant its job -- find
    the reasons this is fragile -- was expressed as a number that then went into the consensus
    average. An agent required to produce a probability is pulled toward the other agents'
    probabilities, and the one seat whose value is disagreement was the seat most punished for
    it. It now returns failure modes and nothing that can be averaged.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    summary: Annotated[str, Field(min_length=20, max_length=2000)]
    failure_modes: Annotated[list[FailureMode], Field(max_length=8)]
    fragility: Annotated[str, Field(pattern=r"^(low|moderate|high)$")]
    risk_flags: list[Annotated[str, Field(min_length=1, max_length=300)]]


class RuleProposalDraft(BaseModel):
    """Declarative, non-executable proposal over the closed rule vocabulary."""

    model_config = ConfigDict(strict=True, extra="forbid")
    rule_id: Annotated[str, Field(pattern=r"^[a-z0-9_]+$", max_length=80)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    rationale: Annotated[str, Field(min_length=20, max_length=2000)]
    mechanism: Annotated[str, Field(min_length=20, max_length=2000)]
    confounders: Annotated[list[str], Field(min_length=1, max_length=10)]
    conditions: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=10)]
    combinator: Annotated[str, Field(pattern=r"^(all|any)$")]
    action: dict[str, Any]
    evidence_ids: Annotated[list[UUID], Field(min_length=1)]
    withdrawal_criteria: Annotated[str, Field(min_length=20, max_length=1000)]


@dataclass
class Usage:
    """What the last call cost. Mutable by design: the client fills it in as calls complete.

    Recorded because "what does a research run cost per projection" was previously unanswerable,
    and it is the number that decides how many projections the trigger should select. Latency is
    wall clock including retries, which is what the operator experiences.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    attempts: int = 0
    retry_reasons: list[str] = field(default_factory=list)


# Status codes worth a second attempt. A 400 means the request is wrong and will be wrong again;
# a 429 or a 5xx means the provider is busy or broken, which is a condition that passes.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# A provider may ask for a longer wait than the work can afford. Honour Retry-After, but cap it:
# research is built on evidence frozen for a market that locks, and a run held open for an hour
# outlives the thing it was reasoning about.
MAX_RETRY_AFTER_SECONDS = 30.0


def _retry_after(response: httpx.Response, fallback: float) -> float:
    header = response.headers.get("Retry-After")
    if header is None:
        return fallback
    try:
        requested = float(header)
    except ValueError:
        return fallback
    if requested <= 0:
        return fallback
    return min(requested, MAX_RETRY_AFTER_SECONDS)


@dataclass(frozen=True)
class AgentResponse:
    """One validated analysis plus the audit trail it has to leave behind.

    The usage here is the authoritative per-call record. ``DeepSeekResearchClient.usage`` mirrors
    the most recently *completed* call on the client and is only meaningful on the sequential
    advisory path -- the staged research run and the PAT forecast rounds both drive one client
    from a thread pool, where a single mutable attribute is a race, not a measurement.
    """

    analysis: AgentAnalysis
    prompt_sha256: str
    response_sha256: str
    usage: Usage | None
    attempts: int


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
        max_attempts: int | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        configured_base_url = (
            base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        )
        self.base_url = configured_base_url.rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.timeout_seconds = timeout_seconds or float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        self.transport = transport
        self.max_attempts = max_attempts or int(os.getenv("DEEPSEEK_MAX_ATTEMPTS", "3"))
        self._sleep = sleep
        self.usage = Usage()

    def analyze(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
        directive: str = "",
    ) -> AgentResponse:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        evidence_payload = [
            {"evidence_id": str(evidence_id), "text": text}
            for evidence_id, text in sorted(evidence.items(), key=lambda item: str(item[0]))
        ]
        system = (
            "You are the "
            f"{role} analyst in a WNBA player-prop research system. Return JSON only. "
            "Use only supplied evidence. Every claim must cite one or more supplied evidence_ids. "
            "Do not create a projection, betting recommendation, stake, or probability of a prop "
            "outcome. If evidence is insufficient, return no claims and explain the uncertainty "
            "in risk_flags."
        )
        if directive:
            system = f"{system} {directive}"
        user = json.dumps(
            {
                "question": question,
                "evidence": evidence_payload,
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
            sort_keys=True,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        analysis, response_hash = self._request_json(system, user, AgentAnalysis)
        allowed = set(evidence)
        cited = {
            evidence_id
            for claim in analysis.claims
            for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
        }
        self._reject_unknown(cited, allowed)
        return AgentResponse(
            analysis=analysis,
            prompt_sha256=prompt_hash,
            response_sha256=response_hash,
            usage=replace(self.usage),
            attempts=self.usage.attempts,
        )

    def forecast(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
        peers: list[dict[str, object]] | None = None,
    ) -> tuple[AgentForecastDraft, str, str]:
        """Return an advisory view, independently in round one and peer-aware in round two."""
        system = (
            f"You are the {role} analyst. Return JSON only and cite supplied evidence IDs. "
            "Your probability is advisory research metadata. It cannot alter the statistical "
            "forecast, create a recommendation, set a stake, or activate a rule."
        )
        user = json.dumps(
            {
                "question": question,
                "evidence": [
                    {"evidence_id": str(key), "text": value}
                    for key, value in sorted(evidence.items(), key=lambda item: str(item[0]))
                ],
                "peer_round_one_views": peers or [],
                "json_shape": {
                    "advisory_probability": 0.5,
                    "rationale": "string",
                    "evidence_ids": ["uuid"],
                    "risk_flags": ["string"],
                },
            },
            sort_keys=True,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        result, response_hash = self._request_json(system, user, AgentForecastDraft)
        self._reject_unknown(set(result.evidence_ids), set(evidence))
        return result, prompt_hash, response_hash

    def challenge(
        self,
        *,
        question: str,
        evidence: dict[UUID, str],
        peers: list[dict[str, object]] | None = None,
    ) -> tuple[SkepticReview, str, str]:
        """Ask the skeptic for failure modes rather than for a number it does not believe in."""
        system = (
            "You are the skeptic on a WNBA player-prop research desk. Return JSON only and cite "
            "supplied evidence IDs. Do not produce a probability, projection, recommendation or "
            "stake. Your output is a list of ways this forecast could be wrong, each with what "
            "observation would show the concern to be unfounded. A concern nothing could answer "
            "is not a concern; omit it. If the evidence supports the forecast, say so and return "
            "few or no failure modes."
        )
        user = json.dumps(
            {
                "question": question,
                "evidence": [
                    {"evidence_id": str(key), "text": value}
                    for key, value in sorted(evidence.items(), key=lambda item: str(item[0]))
                ],
                "peer_views": peers or [],
                "json_shape": {
                    "summary": "string",
                    "failure_modes": [
                        {
                            "description": "string",
                            "severity": "low|moderate|high",
                            "disconfirming_observation": "string",
                            "evidence_ids": ["uuid"],
                        }
                    ],
                    "fragility": "low|moderate|high",
                    "risk_flags": ["string"],
                },
            },
            sort_keys=True,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        result, response_hash = self._request_json(system, user, SkepticReview)
        cited = {evidence_id for mode in result.failure_modes for evidence_id in mode.evidence_ids}
        self._reject_unknown(cited, set(evidence))
        return result, prompt_hash, response_hash

    def propose_rule(
        self,
        *,
        measured_failure: str,
        evidence: dict[UUID, str],
    ) -> tuple[RuleProposalDraft, str, str]:
        system = (
            "Propose one conservative WNBA analyst rule as JSON only. Never output code. Use "
            "only the supplied closed fields/operators/actions. A rule may only block, flag, "
            "require evidence, cap confidence, or shrink toward even; it can never increase "
            "confidence or activate itself. Cite evidence and state mechanism, confounders, "
            "withdrawal criteria."
        )
        user = json.dumps(
            {
                "measured_failure": measured_failure,
                "allowed_fields": [
                    "predicted_probability",
                    "confidence",
                    "model_disagreement",
                    "data_quality_score",
                    "projected_minutes",
                    "availability_probability",
                    "start_probability",
                    "closing_lineup_probability",
                    "minutes_std",
                    "quote_age_seconds",
                    "book_count",
                    "line",
                    "prop_type",
                    "source",
                    "side",
                    "injury_designation",
                    "teammate_effect_count",
                ],
                "allowed_operators": ["lt", "lte", "gt", "gte", "eq", "neq", "in", "not_in"],
                "allowed_actions": [
                    "block",
                    "shrink_toward_even",
                    "flag_for_review",
                    "require_evidence",
                ],
                "evidence": [
                    {"evidence_id": str(key), "text": value}
                    for key, value in sorted(evidence.items(), key=lambda item: str(item[0]))
                ],
            },
            sort_keys=True,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        result, response_hash = self._request_json(system, user, RuleProposalDraft)
        self._reject_unknown(set(result.evidence_ids), set(evidence))
        return result, prompt_hash, response_hash

    def complete_structured(
        self, *, system: str, user: str, schema: type[BaseModel]
    ) -> tuple[BaseModel, str]:
        """Validate one arbitrary structured completion against ``schema``.

        The three methods above encode a specific research question and police evidence citation.
        This one is the general form, for the learning-loop tasks that have no evidence corpus to
        cite against -- adjudicating a hypothesis against a settled tally, naming a second error
        cause, drafting the prose of a research proposal. It carries the same guarantee they do:
        the response is parsed into a strict Pydantic model or it raises.
        """
        return self._request_json(system, user, schema)

    @staticmethod
    def _reject_unknown(cited: set[UUID], allowed: set[UUID]) -> None:
        unknown = cited - allowed
        if unknown:
            raise ValueError(f"DeepSeek cited unknown evidence ids: {sorted(map(str, unknown))}")

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        """One request, retried on the failures that pass on their own.

        Exponential backoff, capped attempts, and every retry reason kept on :attr:`usage` so a
        run that succeeded on its third try is distinguishable from one that succeeded outright.
        A provider having a bad minute should not lose an evening's research, and it should also
        not be invisible.
        """
        last: Exception | None = None
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                self.usage.attempts = attempt
                try:
                    response = client.post(f"{self.base_url}/chat/completions", json=payload)
                    if response.status_code in _RETRYABLE_STATUS and attempt < self.max_attempts:
                        self.usage.retry_reasons.append(f"HTTP {response.status_code}")
                        self._sleep(_retry_after(response, 2.0 ** (attempt - 1)))
                        continue
                    response.raise_for_status()
                    return response
                except (httpx.TimeoutException, httpx.TransportError) as error:
                    last = error
                    if attempt >= self.max_attempts:
                        break
                    self.usage.retry_reasons.append(type(error).__name__)
                    self._sleep(2.0 ** (attempt - 1))
        raise last if last is not None else RuntimeError("DeepSeek request failed without a cause")

    def _request_json(self, system: str, user: str, model_type: type[BaseModel]) -> tuple[Any, str]:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            # Zero temperature is not a quality choice, it is a measurement one: agent
            # credibility is scored across runs, and a sampling temperature would put run-to-run
            # variance into a number we intend to read as skill.
            "temperature": 0.0,
            "max_tokens": 4000,
            "stream": False,
        }
        self.usage = Usage()
        started = time.monotonic()
        try:
            response = self._post(payload)
        finally:
            self.usage.latency_ms = int((time.monotonic() - started) * 1000)
        envelope = response.json()
        if not isinstance(envelope, dict):
            raise ValueError("DeepSeek returned a non-object response")
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            prompt_tokens, completion_tokens = (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )
            self.usage.prompt_tokens = prompt_tokens if isinstance(prompt_tokens, int) else None
            self.usage.completion_tokens = (
                completion_tokens if isinstance(completion_tokens, int) else None
            )
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
        response_hash = hashlib.sha256(content.encode()).hexdigest()
        return model_type.model_validate_json(content), response_hash
