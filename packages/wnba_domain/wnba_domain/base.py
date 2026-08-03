"""Base model configuration and the bitemporal record contract.

Everything in the domain inherits from :class:`StrictModel`. Strictness is not decoration --
it is the mechanism by which malformed source data is forced into quarantine instead of
silently becoming a feature.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BitemporalRecord",
    "Count",
    "FrozenModel",
    "NonNegativeRate",
    "Probability",
    "StrictModel",
    "TemporalError",
    "utc_now",
]


def utc_now() -> datetime:
    """The only sanctioned clock read. Always timezone-aware."""
    return datetime.now(UTC)


class TemporalError(ValueError):
    """Raised when a record's temporal envelope is internally incoherent."""


# --------------------------------------------------------------------------------------
# Shared scalar contracts
# --------------------------------------------------------------------------------------
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
"""A probability. Bounded at construction so an out-of-range value can never propagate."""

NonNegativeRate = Annotated[float, Field(ge=0.0)]
"""A per-minute or per-possession rate."""

Count = Annotated[int, Field(ge=0)]
"""A countable basketball quantity: attempts, makes, rebounds."""


class StrictModel(BaseModel):
    """Mutable strict model. Use for working objects that are legitimately revised."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        use_enum_values=False,
        ser_json_timedelta="float",
        # This domain is about statistical models, so `model_run_id`, `model_version_id` and
        # friends are the correct names. Pydantic's default protection of the `model_` prefix
        # would rename our vocabulary to suit the library, which is the wrong way round.
        protected_namespaces=(),
    )


class FrozenModel(StrictModel):
    """Immutable strict model.

    Used for anything that forms part of the audit record -- forecasts, decisions, outcomes,
    evidence. A learning system whose history can be edited is not a learning system, it is a
    collection of revised memories.
    """

    model_config = ConfigDict(frozen=True)


# --------------------------------------------------------------------------------------
# Bitemporality
# --------------------------------------------------------------------------------------
class BitemporalRecord(FrozenModel):
    """A fact with two independent time axes.

    ``valid_*``  -- when the fact was true in the basketball world.
    ``system_*`` -- when this platform learned it.

    Both are required on every stored fact. The gap between them is exactly the information a
    backtest must respect: a player ruled out at 17:40 and observed by us at 17:41 was *not*
    knowable at 17:00, however tempting the hindsight.

    A ``None`` upper bound means "still open": still true, or still current belief.
    """

    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    system_from: AwareDatetime
    system_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_temporal_envelope(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise TemporalError(
                f"valid_to ({self.valid_to.isoformat()}) must be after "
                f"valid_from ({self.valid_from.isoformat()})"
            )
        if self.system_to is not None and self.system_to <= self.system_from:
            raise TemporalError(
                f"system_to ({self.system_to.isoformat()}) must be after "
                f"system_from ({self.system_from.isoformat()})"
            )
        return self

    def is_valid_at(self, moment: datetime) -> bool:
        """Was this fact true in the world at ``moment``?"""
        if moment < self.valid_from:
            return False
        return self.valid_to is None or moment < self.valid_to

    def is_known_at(self, moment: datetime) -> bool:
        """Had we learned this fact by ``moment``?

        This is the leakage-critical predicate. A feature builder assembling inputs for a
        forecast at time *T* may only consider records where ``is_known_at(T)`` is true.
        """
        if moment < self.system_from:
            return False
        return self.system_to is None or moment < self.system_to

    def is_observable_at(self, valid_time: datetime, system_time: datetime) -> bool:
        """The full bitemporal predicate used by ``as_of`` reads."""
        return self.is_valid_at(valid_time) and self.is_known_at(system_time)

    @property
    def observation_lag_seconds(self) -> float:
        """How long it took us to learn this fact. A per-source quality signal."""
        return (self.system_from - self.valid_from).total_seconds()
