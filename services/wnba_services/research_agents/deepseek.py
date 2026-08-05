"""Fail-closed DeepSeek JSON client for evidence-grounded research agents."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Backoff is capped so a provider that answers "retry in an hour" cannot hold a research run --
# and the frozen evidence inside it -- open past the market lock it was built for.
MAX_RETRY_AFTER_SECONDS = 30.0
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


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


@dataclass(frozen=True)
class TokenUsage:
    """What one call cost. Absent when the provider declines to report it."""

    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class AgentResponse:
    """One validated analysis plus the audit trail it has to leave behind."""

    analysis: AgentAnalysis
    prompt_sha256: str
    response_sha256: str
    usage: TokenUsage | None
    attempts: int


def _parse_usage(envelope: dict[str, Any]) -> TokenUsage | None:
    """Accounting, not evidence: an unreported count is recorded as unknown, not as zero."""
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    if prompt < 0 or completion < 0:
        return None
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)


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


class DeepSeekResearchClient:
    """Small provider adapter; no model response bypasses strict validation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        backoff_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        configured_base_url = (
            base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        )
        self.base_url = configured_base_url.rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.timeout_seconds = timeout_seconds or float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        self.max_attempts = max(1, max_attempts or int(os.getenv("DEEPSEEK_MAX_ATTEMPTS", "3")))
        self.backoff_seconds = backoff_seconds or float(
            os.getenv("DEEPSEEK_BACKOFF_SECONDS", "2.0")
        )
        self.transport = transport
        self._sleep = sleep or time.sleep

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "max_tokens": 4000,
            "stream": False,
        }
        envelope, attempts = self._post(payload)
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
        analysis = AgentAnalysis.model_validate_json(content)
        allowed = set(evidence)
        cited = {
            evidence_id
            for claim in analysis.claims
            for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
        }
        unknown = cited - allowed
        if unknown:
            raise ValueError(f"DeepSeek cited unknown evidence ids: {sorted(map(str, unknown))}")
        response_hash = hashlib.sha256(content.encode()).hexdigest()
        return AgentResponse(
            analysis=analysis,
            prompt_sha256=prompt_hash,
            response_sha256=response_hash,
            usage=_parse_usage(envelope),
            attempts=attempts,
        )

    def _post(self, payload: dict[str, Any]) -> tuple[Any, int]:
        """Retry congestion and transport faults only.

        A rejected response -- malformed JSON, a hallucinated citation, a truncated answer -- is
        never retried. Those are not bad luck; asking again would either burn the budget on the
        same refusal or, worse, roll the dice until a compliant-looking answer appears.
        """
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            delay = self.backoff_seconds * (2 ** (attempt - 1))
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as client:
                    response = client.post(f"{self.base_url}/chat/completions", json=payload)
                    if response.status_code in RETRYABLE_STATUS:
                        delay = _retry_after(response, delay)
                    response.raise_for_status()
                    return response.json(), attempt
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_STATUS:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < self.max_attempts:
                self._sleep(delay)
        raise RuntimeError(
            f"DeepSeek did not respond after {self.max_attempts} attempts: {last_error}"
        ) from last_error
