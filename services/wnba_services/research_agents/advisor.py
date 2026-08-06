"""The learning loop's DeepSeek seat: advisory, audited, and never load-bearing.

Every scheduled learning job in this package can ask the model for judgement. None of them
*depend* on getting an answer. That distinction is the entire design of this module, and it is
enforced structurally rather than by convention:

- :func:`advise` always returns. A provider outage, a timeout, a refusal, a malformed JSON body
  or a schema violation all produce the same thing a missing API key produces -- ``None`` -- and
  the caller runs its deterministic path. A learning loop that stops learning when a vendor has
  an incident is worse than one that never called the vendor.

- Every call writes a ``model_advisories`` row, including the ones that failed. Silent degradation
  to templates is indistinguishable from success unless the failure is recorded, and "how much of
  last week's learning was actually model-authored?" is a question the database should answer.

- Nothing here returns a probability that reaches a forecast. The tasks are classification,
  adjudication, narrative and proposal-drafting. Where a number is involved -- a hypothesis'
  evidence tally, a rule's measured log-loss gain -- it is computed from the settled record and
  handed *to* the model as context, never taken back from it. The house rule that agents may
  explain and propose but never write a number into a forecast holds here without exception.

The prompts live next to their callers rather than here, because the shape of the question is
part of the reviewing logic and hiding it behind a generic helper makes it harder to audit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from wnba_services.research_agents.deepseek import DeepSeekResearchClient

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER_FAILURES",
    "Advisor",
    "AdvisoryOutcome",
    "advisory_summary",
    "deepseek_is_configured",
]

Schema = TypeVar("Schema", bound=BaseModel)
Result = TypeVar("Result")

# Any exception the provider path can legitimately raise. Anything outside this set is a bug in
# our own code and should not be swallowed into a quiet fallback. Public because the research
# workflow catches the same set around its own provider calls, and two definitions of "a provider
# failure" would drift into disagreeing about which errors get quietly swallowed.
PROVIDER_FAILURES = (
    httpx.HTTPError,
    ValidationError,
    ValueError,
    KeyError,
    TypeError,
    RuntimeError,
    json.JSONDecodeError,
)


def deepseek_is_configured() -> bool:
    """Whether a key is present. Absence is a normal state, not an error."""
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


@dataclass(frozen=True)
class AdvisoryOutcome:
    """What the loop got back, and whether it was usable."""

    task: str
    subject: str
    disposition: str
    failure_reason: str | None = None

    @property
    def used(self) -> bool:
        return self.disposition == "used"


class Advisor:
    """A cursor-scoped DeepSeek caller that records every attempt.

    Constructed per job with the open cursor, so advisory rows land in the same transaction as
    the learning writes they informed. If the job rolls back, so does the claim that the model
    contributed to it.
    """

    def __init__(
        self,
        cur: Any,
        *,
        client: DeepSeekResearchClient | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._cur = cur
        self._client = client
        self._enabled = deepseek_is_configured() if enabled is None else enabled
        self.outcomes: list[AdvisoryOutcome] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client(self) -> DeepSeekResearchClient:
        if self._client is None:
            self._client = DeepSeekResearchClient()
        return self._client

    # ----------------------------------------------------------------------------------
    # The one entry point
    # ----------------------------------------------------------------------------------
    def advise(
        self,
        *,
        task: str,
        subject: str,
        system: str,
        question: dict[str, Any],
        schema: type[Schema],
        now: datetime | None = None,
    ) -> Schema | None:
        """Ask for one structured judgement. Returns ``None`` on any failure, having logged it.

        ``question`` is serialised with sorted keys so the prompt hash is stable across runs with
        identical inputs -- that is what makes "did this week's advice actually change?" checkable
        rather than a matter of trust.
        """
        at = now or datetime.now(UTC)
        if not self._enabled:
            self._record(
                task=task,
                subject=subject,
                at=at,
                disposition="fallback",
                failure_reason="DEEPSEEK_API_KEY is not configured",
            )
            return None

        payload = json.dumps(
            {**question, "json_shape": _shape_of(schema)},
            sort_keys=True,
            default=str,
        )
        prompt_hash = hashlib.sha256(f"{system}\n{payload}".encode()).hexdigest()
        try:
            result, response_hash = self.client.complete_structured(
                system=system, user=payload, schema=schema
            )
        except PROVIDER_FAILURES as error:
            reason = f"{type(error).__name__}: {error}"[:500]
            logger.warning("advisory task %s fell back to deterministic path: %s", task, reason)
            self._record(
                task=task,
                subject=subject,
                at=at,
                disposition="fallback",
                prompt_hash=prompt_hash,
                failure_reason=reason,
            )
            return None

        self._record(
            task=task,
            subject=subject,
            at=at,
            disposition="used",
            prompt_hash=prompt_hash,
            response_hash=response_hash,
        )
        # `complete_structured` is typed to the BaseModel base; the value was validated
        # against `schema` itself inside it, so the narrowing is sound.
        assert isinstance(result, schema)
        return result

    def attempt(
        self,
        *,
        task: str,
        subject: str,
        call: Callable[[], tuple[Result, str, str]],
        now: datetime | None = None,
    ) -> Result | None:
        """Run one already-composed provider call under the same fail-open contract.

        :meth:`advise` owns the prompt; this owns nothing but the failure handling, which is what
        the research workflow needs. Its calls encode a specific research question and police
        evidence citation inside the client, so the prompt cannot move here -- but the guarantee
        should be identical, and before this it was the opposite: a single failed role raised and
        took the whole run's other nine calls with it.

        Returns ``None`` on any provider failure, having recorded it. The caller decides what a
        missing agent means; the usual answer is "the run continues with fewer voices, and says
        so".
        """
        at = now or datetime.now(UTC)
        if not self._enabled:
            self._record(
                task=task,
                subject=subject,
                at=at,
                disposition="fallback",
                failure_reason="DEEPSEEK_API_KEY is not configured",
            )
            return None
        try:
            result, prompt_hash, response_hash = call()
        except PROVIDER_FAILURES as error:
            reason = f"{type(error).__name__}: {error}"[:500]
            logger.warning("advisory task %s fell back to deterministic path: %s", task, reason)
            self._record(
                task=task, subject=subject, at=at, disposition="fallback", failure_reason=reason
            )
            return None
        self._record(
            task=task,
            subject=subject,
            at=at,
            disposition="used",
            prompt_hash=prompt_hash,
            response_hash=response_hash,
        )
        return result

    def record_fallback(
        self, *, task: str, subject: str, reason: str, now: datetime | None = None
    ) -> None:
        """Record a provider failure that happened outside this Advisor's own call.

        The research workflow calls the provider with no transaction open -- deliberately, so a
        run that dies mid-flight does not hold a connection -- and reopens a cursor afterwards to
        persist what came back. The failures have therefore already happened by the time there is
        somewhere to write them. This is that write. It carries the same meaning as a fallback
        from :meth:`advise`: the provider did not produce a usable answer and the caller carried
        on without one.
        """
        logger.warning("advisory task %s fell back to the deterministic path: %s", task, reason)
        self._record(
            task=task,
            subject=subject,
            at=now or datetime.now(UTC),
            disposition="fallback",
            failure_reason=reason[:500],
        )

    def reject(self, *, task: str, subject: str, reason: str, now: datetime | None = None) -> None:
        """Record advice that arrived intact but failed a downstream check.

        Distinct from a fallback: the provider answered and the schema validated, but the loop
        declined the content -- an unknown rule field, a verdict the evidence does not support.
        Keeping these separate is how a provider problem stays distinguishable from a prompt
        problem.
        """
        self._record(
            task=task,
            subject=subject,
            at=now or datetime.now(UTC),
            disposition="rejected",
            failure_reason=reason[:500],
        )

    # ----------------------------------------------------------------------------------
    def _record(
        self,
        *,
        task: str,
        subject: str,
        at: datetime,
        disposition: str,
        prompt_hash: str | None = None,
        response_hash: str | None = None,
        failure_reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.outcomes.append(AdvisoryOutcome(task, subject, disposition, failure_reason))
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro") if self._enabled else "none"
        # Cost travels with the call, read off the client's most recent one. A client that was
        # never constructed reports nothing rather than zero: "no key configured" and "a call
        # that cost nothing" are different facts, and a zero would merge them.
        client = self._client
        tokens = None if client is None else client.last_usage
        self._cur.execute(
            """INSERT INTO wnba.model_advisories
               (advisory_id,task,subject,requested_at,model,prompt_hash,response_hash,
                disposition,detail,failure_reason,latency_ms,prompt_tokens,completion_tokens,
                attempts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                task,
                subject[:200],
                at,
                model,
                prompt_hash,
                response_hash,
                disposition,
                Jsonb(detail or {}),
                failure_reason,
                None if client is None else client.last_latency_ms,
                None if tokens is None else tokens.prompt_tokens,
                None if tokens is None else tokens.completion_tokens,
                None if client is None or client.last_attempts == 0 else client.last_attempts,
            ),
        )


def advisory_summary(outcomes: list[AdvisoryOutcome]) -> dict[str, int]:
    """Counts by disposition, for a job's console line."""
    summary = {"used": 0, "fallback": 0, "rejected": 0}
    for outcome in outcomes:
        summary[outcome.disposition] = summary.get(outcome.disposition, 0) + 1
    return summary


def _shape_of(schema: type[BaseModel]) -> dict[str, Any]:
    """A compact field/type sketch for the prompt.

    The full JSON Schema is accurate but long, and the response is validated against the real
    model on the way back regardless -- so the prompt only needs to be clear, not authoritative.
    """
    shape: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        annotation = field.annotation
        shape[name] = getattr(annotation, "__name__", str(annotation))
    return shape
