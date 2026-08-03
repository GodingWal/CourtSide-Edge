"""Governance: audited actions, data-quality incidents, reliability, calibration, drift.

Palantir's useful idea is that an **action** is an object, not a side effect. Every state
change worth arguing about later is recorded with actor, prior state, new state, reason and
evidence -- so that "why was this disabled in July?" has an answer.
"""

from __future__ import annotations

import math
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import FrozenModel, Probability
from wnba_domain.enums import (
    ActionType,
    DriftResponse,
    ModelStage,
    PropType,
    Severity,
    ValidationLevel,
)
from wnba_domain.identity import SourceName

__all__ = [
    "CalibrationBucket",
    "CalibrationSnapshot",
    "DataQualityIncident",
    "DriftIncident",
    "ModelPromotion",
    "OntologyAction",
    "QuarantineRecord",
    "SourceReliability",
]


class OntologyAction(FrozenModel):
    """A first-class, audited state change."""

    action_id: UUID
    action_type: ActionType
    actor: Annotated[str, Field(min_length=1, max_length=100)]
    occurred_at: AwareDatetime
    subject_id: UUID
    previous_state: Annotated[str, Field(max_length=2000)]
    new_state: Annotated[str, Field(max_length=2000)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    """Mandatory. An action without a stated reason is indistinguishable from an accident."""

    evidence_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    model_version_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    is_automated: bool = False

    @model_validator(mode="after")
    def _automation_may_only_restrict(self) -> Self:
        """The asymmetric-autonomy invariant, enforced at the type level.

        The system may automatically become more cautious. Becoming more aggressive -- promoting
        a model, approving a candidate, raising a limit -- always requires a human. This is
        deliberate: automation multiplies competence, but it multiplies stupidity with equal
        efficiency and considerably more speed.
        """
        human_only = {
            ActionType.PROMOTE_MODEL,
            ActionType.APPROVE_CANDIDATE,
            ActionType.OVERRIDE_MINUTES,
        }
        if self.is_automated and self.action_type in human_only:
            raise ValueError(
                f"{self.action_type.value} expands exposure and cannot be taken automatically"
            )
        return self


class DataQualityIncident(FrozenModel):
    """A validation failure, recorded rather than logged and forgotten."""

    incident_id: UUID
    detected_at: AwareDatetime
    level: ValidationLevel
    severity: Severity
    code: Annotated[str, Field(min_length=1, max_length=100)]
    source: SourceName
    entity_id: UUID | None = None
    detail: Annotated[str, Field(max_length=2000)] = ""
    blocks_recommendations: bool = False
    resolved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _blocking_is_blocking(self) -> Self:
        if self.severity is Severity.BLOCKING and not self.blocks_recommendations:
            raise ValueError("a BLOCKING incident must block recommendations")
        return self


class QuarantineRecord(FrozenModel):
    """A rejected payload, preserved verbatim.

    Quarantined data is kept, not discarded: the shape of what a source gets wrong is itself a
    signal, and it is the only way to fix a parser without waiting for the bug to recur.
    """

    quarantine_id: UUID
    source: SourceName
    received_at: AwareDatetime
    raw_payload: Annotated[str, Field(max_length=200_000)]
    payload_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    validation_errors: tuple[str, ...] = Field(min_length=1)
    level: ValidationLevel
    reprocessed_at: AwareDatetime | None = None


class SourceReliability(FrozenModel):
    """Rolling quality score per source. Feeds evidence weighting and the DQ gate."""

    source: SourceName
    as_of: AwareDatetime
    window_days: Annotated[int, Field(ge=1, le=365)]
    observation_count: Annotated[int, Field(ge=0)]
    parse_success_rate: Probability
    cross_source_agreement_rate: Probability
    median_observation_lag_seconds: Annotated[float, Field(ge=0.0)]
    incident_count: Annotated[int, Field(ge=0)]
    reliability: Probability

    @property
    def is_trusted(self) -> bool:
        return self.reliability >= 0.9 and self.parse_success_rate >= 0.98


class CalibrationBucket(FrozenModel):
    """One row of the calibration table: what we said versus what happened."""

    lower: Probability
    upper: Probability
    predicted_mean: Probability
    observed_rate: Probability
    count: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.upper <= self.lower:
            raise ValueError("calibration bucket upper bound must exceed lower bound")
        return self

    @property
    def overconfidence(self) -> float:
        """Positive means we claimed more than we delivered."""
        return self.predicted_mean - self.observed_rate


class CalibrationSnapshot(FrozenModel):
    """Calibration for one market over one window.

    This is the table that quietly reveals a model saying 65% and hitting 59%. Until it is
    flat, edge estimates are decoration.
    """

    snapshot_id: UUID
    as_of: AwareDatetime
    prop_type: PropType
    window_days: Annotated[int, Field(ge=1, le=365)]
    buckets: tuple[CalibrationBucket, ...] = Field(min_length=1)
    mean_brier: Annotated[float, Field(ge=0.0, le=1.0)]
    mean_log_loss: Annotated[float, Field(ge=0.0)]
    sample_count: Annotated[int, Field(ge=0)]

    @property
    def expected_calibration_error(self) -> float:
        """Sample-weighted mean absolute gap between claimed and realised rates."""
        total = sum(b.count for b in self.buckets)
        if total == 0:
            return 0.0
        return (
            math.fsum(b.count * abs(b.predicted_mean - b.observed_rate) for b in self.buckets)
            / total
        )

    @property
    def is_healthy(self) -> bool:
        return self.expected_calibration_error <= 0.03 and self.sample_count >= 100


class DriftIncident(FrozenModel):
    """Detected drift plus the automatic response taken."""

    incident_id: UUID
    detected_at: AwareDatetime
    prop_type: PropType | None = None
    metric: Annotated[str, Field(min_length=1, max_length=100)]
    baseline_value: float
    observed_value: float
    threshold: float
    response: DriftResponse
    affected_segment: Annotated[str, Field(max_length=500)] = ""
    cleared_at: AwareDatetime | None = None


class ModelPromotion(FrozenModel):
    """A recorded champion/challenger transition. Always human-approved."""

    promotion_id: UUID
    model_version_id: UUID
    from_stage: ModelStage
    to_stage: ModelStage
    experiment_id: UUID | None = None
    approved_by: Annotated[str, Field(min_length=1, max_length=100)]
    occurred_at: AwareDatetime
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def _promotion_to_champion_needs_an_experiment(self) -> Self:
        if self.to_stage is ModelStage.CHAMPION and self.experiment_id is None:
            raise ValueError("promotion to champion requires the experiment that justified it")
        if self.from_stage is self.to_stage:
            raise ValueError("a promotion must change stage")
        return self
