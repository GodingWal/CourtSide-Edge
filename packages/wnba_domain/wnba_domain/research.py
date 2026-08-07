"""PAT-style research objects: evidence, claims, hypotheses, agent forecasts, proposals.

Two rules give this layer its value, and both are enforced by the types rather than by good
intentions:

1. **Every claim cites internal evidence ids.** A claim with no citations is not a weak claim,
   it is not a claim at all -- it is rejected. An agent that cannot show its work does not get
   to influence a forecast.
2. **Agents never write numbers into forecasts.** They produce validated features, claims and
   objections. The models produce the probabilities. Letting eloquent autocomplete set a
   projection is how a language model becomes a donation mechanism.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from wnba_domain.base import FrozenModel, Probability
from wnba_domain.enums import AgentRole, ClaimStatus, HypothesisStatus, PropType
from wnba_domain.identity import SourceName

__all__ = [
    "AgentAnalysis",
    "AgentForecast",
    "DecisionSynthesis",
    "Evidence",
    "Experiment",
    "Hypothesis",
    "ResearchAudit",
    "ResearchClaim",
    "ResearchProposal",
    "ResearchRun",
    "ResearchVerdict",
    "SourceDocument",
    "UncitedClaimError",
]


class UncitedClaimError(ValueError):
    """Raised when a claim reaches the store without supporting evidence."""


class SourceDocument(FrozenModel):
    """A retrieved artifact -- injury note, beat report, box score page, transcript."""

    document_id: UUID
    source: SourceName
    retrieved_at: AwareDatetime
    published_at: AwareDatetime | None = None
    url: HttpUrl | None = None
    title: Annotated[str, Field(max_length=300)] = ""
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    content_excerpt: Annotated[str, Field(max_length=4000)] = ""


class Evidence(FrozenModel):
    """A specific, quotable fragment of a document, bound to what it supports."""

    evidence_id: UUID
    document_id: UUID
    excerpt: Annotated[str, Field(min_length=1, max_length=2000)]
    observed_at: AwareDatetime
    reliability: Probability
    """Inherited from the source's rolling reliability score, not asserted by the agent."""


class ResearchClaim(FrozenModel):
    """A structured, falsifiable, cited assertion about the world."""

    claim_id: UUID
    subject_id: UUID
    predicate: Annotated[str, Field(min_length=1, max_length=120)]
    value: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Probability
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    contradicting_evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    valid_from: AwareDatetime
    expires_at: AwareDatetime
    """Claims rot. An injury note from three hours ago is not evidence about tonight's
    rotation, and an unexpiring claim will happily be cited forever."""

    generated_by: AgentRole
    status: ClaimStatus = ClaimStatus.PROPOSED
    reviewed_by: Annotated[str, Field(max_length=100)] | None = None

    @model_validator(mode="after")
    def _cited_and_bounded(self) -> Self:
        if not self.evidence_ids:
            raise UncitedClaimError(f"claim {self.claim_id} cites no evidence")
        if self.expires_at <= self.valid_from:
            raise ValueError("a claim must expire after it becomes valid")
        overlap = set(self.evidence_ids) & set(self.contradicting_evidence_ids)
        if overlap:
            raise ValueError(
                f"evidence cannot both support and contradict a claim: {sorted(map(str, overlap))}"
            )
        return self

    def is_live_at(self, moment: AwareDatetime) -> bool:
        return self.valid_from <= moment < self.expires_at


class Hypothesis(FrozenModel):
    """A causal statement with a stated mechanism and named confounders.

    The mechanism field is the point. ``starter_out = 1`` fed to a gradient booster is not a
    hypothesis; it is a column, and the model will commune privately with it and tell us
    nothing about why. Requiring a mechanism and a confounder list forces the reasoning into
    the open where it can be tested and, ideally, refuted.
    """

    hypothesis_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=150)]
    causal_statement: Annotated[str, Field(min_length=10, max_length=1000)]
    mechanism: Annotated[str, Field(min_length=10, max_length=2000)]
    target_markets: tuple[PropType, ...] = Field(min_length=1)
    expected_direction: Annotated[str, Field(pattern=r"^(increase|decrease|conditional)$")]
    required_features: tuple[str, ...] = Field(default_factory=tuple)
    confounders: tuple[str, ...] = Field(min_length=1)
    supporting_episode_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    contradicting_episode_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: Probability = 0.5
    created_by: Annotated[str, Field(min_length=1, max_length=100)]
    created_at: AwareDatetime
    reviewed_by: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def evidence_balance(self) -> float:
        """Supporting minus contradicting, normalised. Near zero means undecided, not neutral."""
        support = len(self.supporting_episode_ids)
        against = len(self.contradicting_episode_ids)
        total = support + against
        return 0.0 if total == 0 else (support - against) / total


class AgentForecast(FrozenModel):
    """One agent's answer to one question, before and after seeing the others.

    Storing both rounds is what turns disagreement into a measurable signal. If an agent's
    post-discussion number always collapses onto the loudest peer, that is anchoring, and it is
    detectable precisely because the pre-discussion number was recorded first.
    """

    agent_forecast_id: UUID
    question_id: UUID
    role: AgentRole
    asked_at: AwareDatetime
    pre_discussion_probability: Probability
    post_discussion_probability: Probability | None = None
    pre_discussion_rationale: Annotated[str, Field(min_length=1, max_length=4000)]
    post_discussion_rationale: Annotated[str, Field(max_length=4000)] | None = None
    claim_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    saw_peer_forecast_ids: tuple[UUID, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _revision_requires_exposure(self) -> Self:
        if self.post_discussion_probability is not None and not self.saw_peer_forecast_ids:
            raise ValueError("an agent cannot revise after a discussion it did not see")
        return self

    @property
    def revision_magnitude(self) -> float | None:
        if self.post_discussion_probability is None:
            return None
        return abs(self.post_discussion_probability - self.pre_discussion_probability)


class ResearchProposal(FrozenModel):
    """A ranked, testable improvement generated from measured failure -- not from a hunch.

    Proposals are generated by an agent and approved by a human. Software does not get to grade
    its own homework.
    """

    proposal_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    proposed_at: AwareDatetime
    proposed_by: AgentRole
    motivating_episode_ids: tuple[UUID, ...] = Field(min_length=1)
    hypothesis_id: UUID | None = None
    estimated_value: Probability
    estimated_failure_reduction: Probability
    implementation_cost: Annotated[str, Field(pattern=r"^(low|medium|high)$")]
    affected_markets: tuple[PropType, ...] = Field(min_length=1)
    experiment_design: Annotated[str, Field(min_length=10, max_length=3000)]
    approved_by: Annotated[str, Field(max_length=100)] | None = None


class Experiment(FrozenModel):
    """A champion-challenger comparison with its objective fixed in advance.

    ``primary_metric`` is declared before the run so the result cannot be reinterpreted
    afterwards into whichever metric happened to improve.
    """

    experiment_id: UUID
    proposal_id: UUID | None = None
    champion_model_version_id: UUID
    challenger_model_version_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    primary_metric: Annotated[str, Field(pattern=r"^(log_loss|brier|mae|line_value)$")]
    evaluation: Annotated[str, Field(pattern=r"^(rolling_origin|walk_forward|shadow)$")]
    champion_score: float | None = None
    challenger_score: float | None = None
    promoted: bool = False
    notes: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def _promotion_requires_completion(self) -> Self:
        if self.promoted and self.completed_at is None:
            raise ValueError("a challenger cannot be promoted from an unfinished experiment")
        if self.champion_model_version_id == self.challenger_model_version_id:
            raise ValueError("champion and challenger must be different model versions")
        return self


class ResearchRun(FrozenModel):
    """One audited research engagement against one immutable projection.

    Owner-triggered, never automatic: the run records provider, model, token spend and the
    prompt hash so cost and content are both reconstructable after the fact.
    """

    research_run_id: UUID
    projection_id: UUID
    provider: Annotated[str, Field(min_length=1, max_length=40)]
    model: Annotated[str, Field(min_length=1, max_length=80)]
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    status: Annotated[str, Field(min_length=1, max_length=20)]
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    error: str | None = None
    agent_calls: Annotated[int, Field(ge=0)]
    provider_calls: Annotated[int, Field(ge=0)]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class AgentAnalysis(FrozenModel):
    """One agent's cited position inside a research run."""

    analysis_id: UUID
    research_run_id: UUID
    agent_role: AgentRole
    conclusion: Annotated[str, Field(min_length=1)]
    confidence: Probability
    risk_flags: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    response_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: AwareDatetime


class ResearchAudit(FrozenModel):
    """The data auditor's gate on one research run.

    Freshness and blocking issues are recorded before any agent output is trusted; a
    ``blocked`` verdict is the system refusing to reason over data it cannot stand behind.
    """

    audit_id: UUID
    research_run_id: UUID
    projection_id: UUID
    audited_at: AwareDatetime
    status: Literal["pass", "warn", "blocked"]
    issues: tuple[dict[str, Any], ...] = ()
    freshness_seconds: Annotated[int, Field(ge=0)]


class ResearchVerdict(FrozenModel):
    """The skeptic's scored verdict on a research run's claims."""

    verdict_id: UUID
    research_run_id: UUID
    caution: Literal["low", "elevated", "high"]
    agreement: Probability
    total_claims: Annotated[int, Field(ge=0)]
    contested_claims: Annotated[int, Field(ge=0)]
    contested_predicates: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    skeptic_confidence: Probability
    rationale: Annotated[str, Field(min_length=1)]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _contested_within_total(self) -> Self:
        if self.contested_claims > self.total_claims:
            raise ValueError("contested_claims cannot exceed total_claims")
        return self


class DecisionSynthesis(FrozenModel):
    """A research run's closing position.

    The model's probability set against the advisory probability, with the disagreement and
    the policy reasons preserved rather than averaged away.
    """

    synthesis_id: UUID
    research_run_id: UUID
    created_at: AwareDatetime
    disposition: Annotated[str, Field(min_length=1, max_length=40)]
    model_probability: Probability
    advisory_probability: Probability | None = None
    disagreement: Annotated[float, Field(ge=0.0)]
    summary: Annotated[str, Field(min_length=1)]
    risk_flags: tuple[str, ...] = ()
    policy_reasons: tuple[str, ...] = ()
