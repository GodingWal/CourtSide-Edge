"""Forecast objects.

The unit of output is a **distribution**, never a number. A projection of 23.4 points invites
the reader to treat a decimal as revelation; the distribution it came from admits that 17 and
30 were both entirely ordinary outcomes. Everything downstream -- pricing, correlation, entry
construction, calibration scoring -- consumes the distribution.

Countable stats (points, rebounds, assists, threes) are modelled as an exact PMF over the
non-negative integers. That is not an approximation: those outcomes genuinely are integers, and
keeping the PMF exact means push probability on a whole-number line is computed rather than
assumed away.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import FrozenModel, Probability
from wnba_domain.enums import ModelStage, PropType
from wnba_domain.identity import GameId, PlayerId

__all__ = [
    "ContinuousDistribution",
    "DiscreteOutcomeDistribution",
    "LineProbabilities",
    "ModelRun",
    "ModelVersion",
    "Projection",
    "StatForecast",
]

PMF_TOLERANCE = 1e-6
MAX_STAT_VALUE = 200


class LineProbabilities(FrozenModel):
    """Outcome probabilities against a specific line, including push.

    Push is not a rounding detail. A whole-number line with a 12% push probability priced as if
    it were a coin flip is simply mispriced.
    """

    line: Decimal
    over: Probability
    push: Probability
    under: Probability

    @model_validator(mode="after")
    def _sums_to_one(self) -> Self:
        total = self.over + self.push + self.under
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"over/push/under must sum to 1, got {total}")
        return self


class DiscreteOutcomeDistribution(FrozenModel):
    """Exact PMF over non-negative integer outcomes. ``pmf[k]`` is P(stat == k)."""

    prop_type: PropType
    pmf: tuple[Annotated[float, Field(ge=0.0, le=1.0)], ...] = Field(
        min_length=1, max_length=MAX_STAT_VALUE + 1
    )
    simulation_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _normalised(self) -> Self:
        total = math.fsum(self.pmf)
        if abs(total - 1.0) > PMF_TOLERANCE:
            raise ValueError(f"pmf must sum to 1 within {PMF_TOLERANCE}, got {total}")
        return self

    @property
    def mean(self) -> float:
        return math.fsum(k * p for k, p in enumerate(self.pmf))

    @property
    def variance(self) -> float:
        mu = self.mean
        return math.fsum(p * (k - mu) ** 2 for k, p in enumerate(self.pmf))

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def quantile(self, q: float) -> int:
        """Smallest ``k`` with CDF(k) >= q. ``quantile(0.5)`` is the median."""
        if not 0.0 < q <= 1.0:
            raise ValueError("quantile requires 0 < q <= 1")
        cumulative = 0.0
        for k, p in enumerate(self.pmf):
            cumulative += p
            if cumulative >= q:
                return k
        return len(self.pmf) - 1

    @property
    def median(self) -> int:
        return self.quantile(0.5)

    def probabilities_for(self, line: Decimal) -> LineProbabilities:
        """Split the distribution around ``line``.

        A half-point line cannot push, so ``push`` is exactly zero there -- computed, not
        special-cased.
        """
        push = 0.0
        if line == line.to_integral_value():
            index = int(line)
            if 0 <= index < len(self.pmf):
                push = self.pmf[index]
        over = math.fsum(p for k, p in enumerate(self.pmf) if Decimal(k) > line)
        under = max(0.0, 1.0 - over - push)
        return LineProbabilities(line=line, over=over, push=push, under=under)


class ContinuousDistribution(FrozenModel):
    """Quantile-represented continuous distribution. Used for minutes and rate quantities."""

    mean: Annotated[float, Field(ge=0.0)]
    std: Annotated[float, Field(ge=0.0)]
    quantile_levels: tuple[Annotated[float, Field(gt=0.0, lt=1.0)], ...] = Field(min_length=3)
    quantile_values: tuple[Annotated[float, Field(ge=0.0)], ...] = Field(min_length=3)
    simulation_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _quantiles_coherent(self) -> Self:
        if len(self.quantile_levels) != len(self.quantile_values):
            raise ValueError("quantile levels and values must be the same length")
        if list(self.quantile_levels) != sorted(self.quantile_levels):
            raise ValueError("quantile levels must be ascending")
        if list(self.quantile_values) != sorted(self.quantile_values):
            raise ValueError("quantile values must be non-decreasing")
        return self


# --------------------------------------------------------------------------------------
# Model provenance
# --------------------------------------------------------------------------------------
class ModelVersion(FrozenModel):
    """An immutable, promotable artifact. Champion/challenger status lives here."""

    model_version_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=100)]
    semver: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    target: PropType
    stage: ModelStage
    trained_at: AwareDatetime
    training_window_start: AwareDatetime
    training_window_end: AwareDatetime
    feature_definition_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    git_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")] | None = None

    @model_validator(mode="after")
    def _training_window_ordered(self) -> Self:
        if self.training_window_end <= self.training_window_start:
            raise ValueError("training window end must be after start")
        if self.trained_at < self.training_window_end:
            raise ValueError("a model cannot be trained before its own training data existed")
        return self

    @property
    def label(self) -> str:
        return f"{self.name}-v{self.semver}"


class ModelRun(FrozenModel):
    """One execution of one model version. The join point for lineage."""

    model_run_id: UUID
    model_version_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    feature_snapshot_id: UUID
    source_snapshot_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    random_seed: Annotated[int, Field(ge=0)] | None = None
    """Recorded so a simulation is reproducible. An unreproducible forecast cannot be audited,
    and an unauditable forecast cannot be learned from."""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("model run completed before it started")
        return self


# --------------------------------------------------------------------------------------
# Forecasts
# --------------------------------------------------------------------------------------
class StatForecast(FrozenModel):
    """The distribution for one player, one game, one stat."""

    player_id: PlayerId
    game_id: GameId
    prop_type: PropType
    distribution: DiscreteOutcomeDistribution
    minutes: ContinuousDistribution
    availability_probability: Probability

    @property
    def mean(self) -> float:
        return self.distribution.mean

    @property
    def median(self) -> int:
        return self.distribution.median


class Projection(FrozenModel):
    """A forecast plus everything needed to audit, gate and later diagnose it."""

    projection_id: UUID
    forecast: StatForecast
    model_run_id: UUID
    model_version_ids: tuple[UUID, ...] = Field(min_length=1)
    generated_at: AwareDatetime
    expires_at: AwareDatetime

    data_quality_score: Probability
    """Gate input. Below the configured floor, no recommendation is produced at any edge."""

    model_disagreement: Annotated[float, Field(ge=0.0)]
    """Spread across ensemble members. High disagreement should widen intervals and shrink
    stakes, never be averaged into false confidence."""

    confidence: Probability
    primary_driver_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    risk_flag_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    research_claim_ids: tuple[UUID, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _expiry_after_generation(self) -> Self:
        if self.expires_at <= self.generated_at:
            raise ValueError("a projection must expire after it is generated")
        return self

    def is_fresh_at(self, moment: AwareDatetime) -> bool:
        return self.generated_at <= moment < self.expires_at
