"""Fail-closed DeepSeek JSON client for evidence-grounded research agents."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Backoff is capped so a provider that answers "retry in an hour" cannot hold a research run --
# and the frozen evidence inside it -- open past the market lock it was built for.
MAX_RETRY_AFTER_SECONDS = 30.0
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Completion budgets per task. The skeptic reads every peer view plus the full corpus, so a
# shared budget truncates it first -- and a truncated response fails closed, which made the
# most complex call the most fragile one. Both are env-overridable.
DEFAULT_MAX_TOKENS = 4000
SKEPTIC_MAX_TOKENS = 6000

# A rejected response -- truncated, unparseable, schema-invalid, citing evidence it was never
# given -- earns one bounded re-ask that shows the model exactly why its answer was refused.
# That is not rolling the dice until a compliant-looking answer appears: the rejection reason
# is fed back verbatim, the retry budget is fixed and small, and a second refusal still fails
# closed. What it fixes is the common case in production, where the fallback journal showed
# the model one token budget or one field name away from a valid answer.
DEFAULT_FEEDBACK_RETRIES = 1


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
    """The skeptic's output in the PAT forecast rounds, deliberately without a probability.

    The staged workflow gives the skeptic a genuine review job: it reads the analysts'
    conclusions as citable evidence and contradicts them by id. The *forecast* rounds had no
    such job for it -- every role was polled for an advisory probability, including this one --
    which quietly turned the desk's one dissenting seat into a fifth number to be averaged. An
    agent required to produce a probability is pulled toward the other agents' probabilities.

    So in those rounds the skeptic answers on this schema instead: failure modes with severities
    and, for each, the observation that would settle it. Nothing here can be averaged, and
    nothing here reaches a forecast.
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


@dataclass(frozen=True)
class _Completion:
    """One validated structured response and what it took to get it."""

    value: Any
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


def _evidence_payload(evidence: dict[UUID, str]) -> list[dict[str, str]]:
    """Serialise the corpus in content order, not id order.

    Evidence ids are minted per run, so sorting by them shuffles identical content into a
    different order every run and defeats the provider's prefix cache. Sorting by a digest
    of the text keeps a role's prompt prefix stable whenever the corpus overlaps -- the
    common case across the seats of one run and across runs on one market.
    """
    return [
        {"evidence_id": str(evidence_id), "text": text}
        for evidence_id, text in sorted(
            evidence.items(), key=lambda item: hashlib.sha256(item[1].encode()).hexdigest()
        )
    ]


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
        max_tokens: int | None = None,
        skeptic_max_tokens: int | None = None,
        feedback_retries: int | None = None,
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
        self.max_tokens = max_tokens or int(
            os.getenv("DEEPSEEK_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        )
        self.skeptic_max_tokens = skeptic_max_tokens or int(
            os.getenv("DEEPSEEK_SKEPTIC_MAX_TOKENS", str(SKEPTIC_MAX_TOKENS))
        )
        self.feedback_retries = max(
            0,
            feedback_retries
            if feedback_retries is not None
            else int(os.getenv("DEEPSEEK_FEEDBACK_RETRIES", str(DEFAULT_FEEDBACK_RETRIES))),
        )
        self.transport = transport
        self._sleep = sleep or time.sleep
        # What the most recent call cost. The per-call `_Completion` carries the same figures to
        # callers that want them structurally; these exist for the ones that do not thread a
        # return value all the way to where the audit row is written -- `Advisor`, above all,
        # which records the cost of a call it made through a lambda it was handed.
        self.last_usage: TokenUsage | None = None
        self.last_latency_ms: int | None = None
        self.last_attempts: int = 0

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
        evidence_payload = _evidence_payload(evidence)
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
        allowed = set(evidence)

        def check_citations(analysis: AgentAnalysis) -> None:
            cited = {
                evidence_id
                for claim in analysis.claims
                for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
            }
            self._reject_unknown(cited, allowed)

        completion = self._complete(system, user, AgentAnalysis, validate=check_citations)
        analysis: AgentAnalysis = completion.value
        return AgentResponse(
            analysis=analysis,
            prompt_sha256=prompt_hash,
            response_sha256=completion.response_sha256,
            usage=completion.usage,
            attempts=completion.attempts,
        )

    def forecast(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
        peers: list[dict[str, object]] | None = None,
        precedents: Sequence[str] | None = None,
        track_record: str | None = None,
    ) -> tuple[AgentForecastDraft, str, str]:
        """Return an advisory view, independently in round one and peer-aware in round two."""
        system = (
            f"You are the {role} analyst. Return JSON only and cite supplied evidence IDs. "
            "Your probability is advisory research metadata. It cannot alter the statistical "
            "forecast, create a recommendation, set a stake, or activate a rule. Settled "
            "precedents and your own measured track record may be supplied for calibration; "
            "they are context, not citable evidence."
        )
        user = json.dumps(
            {
                "question": question,
                "evidence": _evidence_payload(evidence),
                "peer_round_one_views": peers or [],
                "precedents_with_outcomes": list(precedents or []),
                "your_recent_track_record": track_record or "",
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
        allowed = set(evidence)

        def check_citations(draft: AgentForecastDraft) -> None:
            self._reject_unknown(set(draft.evidence_ids), allowed)

        completion = self._complete(system, user, AgentForecastDraft, validate=check_citations)
        result: AgentForecastDraft = completion.value
        return result, prompt_hash, completion.response_sha256

    def challenge(
        self,
        *,
        question: str,
        evidence: dict[UUID, str],
        peers: list[dict[str, object]] | None = None,
        precedents: Sequence[str] | None = None,
    ) -> tuple[SkepticReview, str, str]:
        """Ask the skeptic for failure modes rather than for a number it does not believe in."""
        system = (
            "You are the skeptic on a WNBA player-prop research desk. Return JSON only and cite "
            "supplied evidence IDs. Do not produce a probability, projection, recommendation or "
            "stake. Your output is a list of ways this forecast could be wrong, each with what "
            "observation would show the concern to be unfounded. A concern nothing could answer "
            "is not a concern; omit it. If the evidence supports the forecast, say so and return "
            "few or no failure modes. Settled precedents may be supplied for calibration; "
            "they are context, not citable evidence."
        )
        user = json.dumps(
            {
                "question": question,
                "evidence": _evidence_payload(evidence),
                "peer_views": peers or [],
                "precedents_with_outcomes": list(precedents or []),
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
        allowed = set(evidence)

        def check_citations(review: SkepticReview) -> None:
            cited = {
                evidence_id
                for mode in review.failure_modes
                for evidence_id in mode.evidence_ids
            }
            self._reject_unknown(cited, allowed)

        completion = self._complete(
            system, user, SkepticReview, max_tokens=self.skeptic_max_tokens,
            validate=check_citations,
        )
        review: SkepticReview = completion.value
        return review, prompt_hash, completion.response_sha256

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
                "evidence": _evidence_payload(evidence),
            },
            sort_keys=True,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
        allowed = set(evidence)

        def check_citations(draft: RuleProposalDraft) -> None:
            self._reject_unknown(set(draft.evidence_ids), allowed)

        completion = self._complete(system, user, RuleProposalDraft, validate=check_citations)
        result: RuleProposalDraft = completion.value
        return result, prompt_hash, completion.response_sha256

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
        completion = self._complete(system, user, schema)
        return completion.value, completion.response_sha256

    @staticmethod
    def _reject_unknown(cited: set[UUID], allowed: set[UUID]) -> None:
        unknown = cited - allowed
        if unknown:
            raise ValueError(f"DeepSeek cited unknown evidence ids: {sorted(map(str, unknown))}")

    def _complete(
        self,
        system: str,
        user: str,
        model_type: type[BaseModel],
        *,
        max_tokens: int | None = None,
        validate: Callable[[Any], None] | None = None,
    ) -> _Completion:
        """One validated structured response, with a bounded feedback re-ask on rejection.

        Every refusal -- truncation, empty content, unparseable JSON, a schema violation, a
        hallucinated citation raised by ``validate`` -- appends the rejected answer and the
        exact rejection reason to the conversation and asks again, at most
        ``self.feedback_retries`` times. A second refusal raises the original error. Blind
        re-rolls remain forbidden: the model always sees why its answer was refused, and the
        audit trail (``attempts``) counts every round the re-ask cost.
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = 0
        latency_ms = 0
        for feedback_round in range(self.feedback_retries + 1):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                # Zero temperature is not a quality choice, it is a measurement one: agent
                # credibility is scored across runs, and a sampling temperature would put
                # run-to-run variance into a number we intend to read as skill.
                "temperature": 0.0,
                "max_tokens": max_tokens or self.max_tokens,
                "stream": False,
            }
            started = time.monotonic()
            try:
                envelope, round_attempts = self._post(payload)
            finally:
                # Recorded even on the failing path, because "the call that cost us four
                # minutes and then raised" is the one an operator most needs to find later.
                latency_ms += int((time.monotonic() - started) * 1000)
                self.last_latency_ms = latency_ms
            attempts += round_attempts
            self.last_attempts = attempts
            try:
                value, content = self._parse_response(envelope, model_type)
                if validate is not None:
                    validate(value)
            except ValueError as exc:
                if feedback_round >= self.feedback_retries:
                    raise
                rejected = self._response_content(envelope)
                if rejected:
                    messages.append({"role": "assistant", "content": rejected})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response was rejected: {exc}. "
                            "Return a corrected response as JSON only."
                        ),
                    }
                )
                continue
            response_hash = hashlib.sha256(content.encode()).hexdigest()
            self.last_usage = _parse_usage(envelope)
            return _Completion(
                value=value,
                response_sha256=response_hash,
                usage=self.last_usage,
                attempts=attempts,
            )
        raise AssertionError("unreachable: the feedback loop either returns or raises")

    @staticmethod
    def _response_content(envelope: Any) -> str | None:
        """The assistant text of a rejected response, when one exists to show back."""
        if not isinstance(envelope, dict):
            return None
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) and content.strip() else None

    def _parse_response(
        self, envelope: Any, model_type: type[BaseModel]
    ) -> tuple[BaseModel, str]:
        """The validated value and the raw content it was parsed from, or a refusal."""
        if not isinstance(envelope, dict):
            raise ValueError("DeepSeek returned a non-object response")
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("DeepSeek returned no choices")
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            raise ValueError(f"DeepSeek response did not finish cleanly: {reason!r}")
        content = self._response_content(envelope)
        if content is None:
            raise ValueError("DeepSeek returned empty content")
        return model_type.model_validate_json(content), content

    def _post(self, payload: dict[str, Any]) -> tuple[Any, int]:
        """Retry congestion and transport faults only.

        A rejected response -- malformed JSON, a hallucinated citation, a truncated answer -- is
        never blind-retried here. That decision belongs to `_complete`, which may re-ask once
        with the exact rejection reason shown to the model; what must never happen is rolling
        the dice on an identical prompt until a compliant-looking answer appears.
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
