"""Decisions, entries, and the immutable episode record that makes learning possible.

A :class:`DecisionEpisode` is written once and never updated. Outcomes, scores and diagnoses
are *appended* as separate records that reference it. This is the difference between a system
that learns and a system that revises its memories to match the result -- convenient for
politicians, useless for science.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import FrozenModel, Probability
from wnba_domain.enums import (
    EntryType,
    ErrorCategory,
    ErrorCode,
    FeedbackType,
    PropType,
    RecommendationStatus,
    Side,
)
from wnba_domain.identity import GameId, PlayerId, SourceName
from wnba_domain.market import QuoteKey

__all__ = [
    "AnalystFeedback",
    "DecisionEpisode",
    "DialogueMessage",
    "EntryCandidate",
    "EntryLeg",
    "EpisodeOutcome",
    "ErrorAttribution",
    "ForecastScore",
    "PickLeg",
    "PickSlip",
    "PickUpload",
    "ProjectionDialogue",
    "brier_score",
    "log_loss",
]

_LOG_LOSS_EPSILON = 1e-6


def brier_score(probability: float, outcome: int) -> float:
    """Squared error of a probabilistic forecast. Lower is better."""
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    return (probability - outcome) ** 2


def log_loss(probability: float, outcome: int) -> float:
    """Negative log likelihood. Punishes confident wrongness far harder than Brier does."""
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    clamped = min(max(probability, _LOG_LOSS_EPSILON), 1.0 - _LOG_LOSS_EPSILON)
    return -(outcome * math.log(clamped) + (1 - outcome) * math.log(1.0 - clamped))


# --------------------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------------------
class EntryLeg(FrozenModel):
    """One selection inside a pick'em entry."""

    quote_id: UUID
    projection_id: UUID
    key: QuoteKey
    side: Side
    line: Decimal
    win_probability: Probability
    """Marginal probability this leg is correct. Entry EV uses the *joint* distribution, not
    the product of these -- legs on the same game are correlated, often strongly."""

    multiplier: Annotated[Decimal, Field(gt=Decimal("0"))] = Decimal("1")


class EntryCandidate(FrozenModel):
    """A proposed entry, priced against a payout rule."""

    entry_id: UUID
    source: SourceName
    entry_type: EntryType
    legs: tuple[EntryLeg, ...] = Field(min_length=2, max_length=8)
    payout_by_correct: dict[int, Decimal]
    joint_probability_by_correct: tuple[Probability, ...] = Field(min_length=3)
    """From the correlated simulation: index ``k`` is P(exactly k legs correct)."""

    expected_value: float
    """Per unit staked. 0.13 means +13%."""

    recommended_stake_fraction: Annotated[float, Field(ge=0.0, le=0.05)]
    status: RecommendationStatus
    generated_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if len(self.joint_probability_by_correct) != len(self.legs) + 1:
            raise ValueError("joint_probability_by_correct must cover 0..leg_count inclusive")
        total = math.fsum(self.joint_probability_by_correct)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"joint correct-leg distribution must sum to 1, got {total}")
        if len({leg.key for leg in self.legs}) != len(self.legs):
            raise ValueError("an entry cannot contain the same market twice")
        if self.expires_at <= self.generated_at:
            raise ValueError("an entry candidate must expire after it is generated")
        if self.status is not RecommendationStatus.RECOMMENDED and (
            self.recommended_stake_fraction > 0.0
        ):
            raise ValueError("only a recommended entry may carry a non-zero stake")
        return self

    @property
    def leg_count(self) -> int:
        return len(self.legs)

    @property
    def naive_independent_probability(self) -> float:
        """P(all legs) assuming independence. Kept only for comparison against the simulated
        joint probability: the gap between them is a direct readout of how much correlation is
        doing, and a large gap in the wrong direction is how naive models die."""
        product = 1.0
        for leg in self.legs:
            product *= leg.win_probability
        return product


# --------------------------------------------------------------------------------------
# Episodes -- the learning unit
# --------------------------------------------------------------------------------------
class DecisionEpisode(FrozenModel):
    """Everything the system knew and believed at one decision moment. Write-once.

    The point is replayability. Months later we must be able to reconstruct, exactly, why this
    call was made -- which sources, which features, which model versions, which claims -- and
    to do so without any contamination from what happened afterwards.
    """

    episode_id: UUID
    forecast_timestamp: AwareDatetime
    player_id: PlayerId
    game_id: GameId
    prop_type: PropType
    side: Side
    line: Decimal
    source: SourceName
    quote_id: UUID
    multiplier: Decimal = Decimal("1")

    projected_mean: float
    projected_median: float
    predicted_probability: Probability
    projected_minutes: Annotated[float, Field(ge=0.0, le=60.0)]
    confidence: Probability
    data_quality_score: Probability
    model_disagreement: Annotated[float, Field(ge=0.0)]

    # Lineage. Without these the episode is an anecdote.
    model_version_ids: tuple[UUID, ...] = Field(min_length=1)
    model_run_id: UUID
    feature_snapshot_id: UUID
    source_snapshot_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    hypothesis_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    risk_flag_ids: tuple[UUID, ...] = Field(default_factory=tuple)

    system_recommendation: RecommendationStatus
    entry_id: UUID | None = None
    analyst_decision: FeedbackType | None = None
    analyst_override_reason: Annotated[str, Field(max_length=2000)] | None = None
    is_paper: bool = True
    """True until the ten pre-real-money criteria are met. Defaults to paper on purpose."""

    @model_validator(mode="after")
    def _override_requires_reason(self) -> Self:
        overrides = {FeedbackType.OVERRIDE_HIGHER, FeedbackType.OVERRIDE_LOWER}
        if self.analyst_decision in overrides and not self.analyst_override_reason:
            raise ValueError("an analyst override must record its reason")
        return self


class EpisodeOutcome(FrozenModel):
    """Appended after settlement. Never merged into the episode."""

    episode_id: UUID
    settled_at: AwareDatetime
    actual_stat: float
    actual_minutes: Annotated[float, Field(ge=0.0, le=70.0)]
    did_play: bool
    did_start: bool
    hit: bool
    """Whether the selected side was correct."""

    was_push: bool = False
    was_voided: bool = False
    """Scratches and late withdrawals. Excluded from scoring rather than counted as losses."""

    closing_line: Decimal | None = None
    closing_multiplier: Decimal | None = None

    @property
    def line_value(self) -> Decimal | None:
        """Our substitute for closing-line value in a pick'em market.

        With no two-sided price to compare against, the honest measure is whether the line moved
        toward our side between forecast and lock. It is a weaker signal than true CLV, and it
        is stated as such rather than dressed up as the real thing.
        """
        return self.closing_line

    def score(self, predicted_probability: float) -> ForecastScore | None:
        """Proper scores for this episode. ``None`` when the event did not resolve."""
        if self.was_voided or self.was_push:
            return None
        outcome = 1 if self.hit else 0
        return ForecastScore(
            episode_id=self.episode_id,
            predicted_probability=predicted_probability,
            outcome=outcome,
            brier=brier_score(predicted_probability, outcome),
            log_loss=log_loss(predicted_probability, outcome),
        )


class ForecastScore(FrozenModel):
    """Proper scoring result. The primary metric, ahead of profit.

    A profit-only feedback loop rewards a lucky 51% and punishes an unlucky 75%, and then
    proudly retrains itself on the lesson.
    """

    episode_id: UUID
    predicted_probability: Probability
    outcome: Annotated[int, Field(ge=0, le=1)]
    brier: Annotated[float, Field(ge=0.0, le=1.0)]
    log_loss: Annotated[float, Field(ge=0.0)]


class ErrorAttribution(FrozenModel):
    """Why the forecast was wrong -- or why it was right and merely unlucky."""

    episode_id: UUID
    diagnosed_at: AwareDatetime
    primary_category: ErrorCategory
    error_codes: tuple[ErrorCode, ...] = Field(default_factory=tuple)
    avoidable: bool
    contribution: dict[ErrorCategory, Probability]
    """Fraction of the total error assigned to each category. Must sum to 1, which forces the
    diagnosis to name irreducible variance explicitly instead of blaming the model for a 39%
    outcome that simply occurred."""

    confidence: Probability
    evidence: tuple[str, ...] = Field(default_factory=tuple)
    diagnosed_by: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _contribution_normalised(self) -> Self:
        total = math.fsum(self.contribution.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"error contributions must sum to 1, got {total}")
        if self.primary_category not in self.contribution:
            raise ValueError("the primary category must appear in the contribution breakdown")
        return self


class AnalystFeedback(FrozenModel):
    """Structured human judgement. Free-text alone is not a training signal."""

    feedback_id: UUID
    episode_id: UUID
    analyst: Annotated[str, Field(min_length=1, max_length=100)]
    submitted_at: AwareDatetime
    feedback_type: FeedbackType
    projection_useful: Probability
    evidence_relevant: Probability
    confidence_appropriate: Probability
    weakest_assumption: Annotated[str, Field(max_length=500)] | None = None
    missing_context: Annotated[str, Field(max_length=1000)] | None = None
    would_repeat: bool
    evidence_ids_marked_useful: tuple[UUID, ...] = Field(default_factory=tuple)
    evidence_ids_marked_misleading: tuple[UUID, ...] = Field(default_factory=tuple)


class PickSlip(FrozenModel):
    """One owner-confirmed entry, recorded for the paper record.

    Always paper: ``is_paper`` is not a default waiting to be flipped but the reason the
    object exists. There is no wager-placement object in this domain, and this is not one.
    """

    pick_slip_id: UUID
    title: Annotated[str, Field(max_length=200)] = ""
    source: Annotated[str, Field(min_length=1, max_length=20)]
    status: Annotated[str, Field(min_length=1, max_length=20)]
    entry_type: Annotated[str, Field(min_length=1, max_length=20)]
    platform: Annotated[str, Field(max_length=100)] = ""
    stake: Decimal | None = None
    potential_payout: Decimal | None = None
    notes: Annotated[str, Field(max_length=2000)] = ""
    is_paper: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    settled_at: AwareDatetime | None = None
    legs_won: int | None = None
    legs_settled: int | None = None
    realised_multiple: Decimal | None = None


class PickLeg(FrozenModel):
    """One leg of an owner pick slip.

    ``player_name`` is what the owner typed; ``player_id`` is filled at entry time, while
    the person who typed the name is present. An unresolved name stays text: the leg is
    recorded but never settles, and nothing fuzzy-binds it after the fact.
    """

    pick_leg_id: UUID
    pick_slip_id: UUID
    projection_id: UUID | None = None
    player_name: Annotated[str, Field(min_length=1, max_length=120)]
    prop_type: Annotated[str, Field(min_length=1, max_length=80)]
    side: Side
    line: Decimal
    offered_odds: int | None = None
    model_probability: Probability | None = None
    result: Annotated[str, Field(min_length=1, max_length=20)]
    extraction_confidence: Probability | None = None
    player_id: UUID | None = None
    episode_id: UUID | None = None
    settled_at: AwareDatetime | None = None
    actual_stat: float | None = None
    created_at: AwareDatetime


class PickUpload(FrozenModel):
    """A screenshot the owner dropped in, retained with its OCR output and parse status.

    The image's hash is kept so a parsed draft can always be checked against the pixels it
    came from.
    """

    upload_id: UUID
    pick_slip_id: UUID | None = None
    original_filename: Annotated[str, Field(min_length=1, max_length=200)]
    content_type: Annotated[str, Field(min_length=1, max_length=50)]
    storage_path: Annotated[str, Field(min_length=1, max_length=500)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ocr_text: str
    parse_status: Annotated[str, Field(min_length=1, max_length=20)]
    error: str | None = None
    created_at: AwareDatetime


class ProjectionDialogue(FrozenModel):
    """The conversation between the owner and the resident analyst about one projection.

    One dialogue per projection per analyst; the messages carry the audit trail.
    """

    dialogue_id: UUID
    projection_id: UUID
    analyst: Annotated[str, Field(min_length=1, max_length=40)]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DialogueMessage(FrozenModel):
    """One message in a projection dialogue -- owner, assistant, or a tool result.

    Write-once: ``payload`` is the exact envelope exchanged with the provider, so the
    transcript replays verbatim and an answer that cites a tool result can be checked
    against the row that produced it.
    """

    message_id: UUID
    dialogue_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=20)]
    content: str
    payload: dict[str, Any] | None = None
    created_at: AwareDatetime
