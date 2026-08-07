"""Canonical identity and cross-source alias resolution.

Every external system has its own name for the same player -- "A. Wilson", "A'ja Wilson",
"Wilson, A'ja". This module binds those names to one canonical id, and it refuses to guess:
an ambiguous or unknown name raises :class:`UnresolvedAliasError` and the ingestion pipeline
quarantines the record, because a silently wrong binding is poison in training data while a
rejected row is merely an inconvenience.
"""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import BitemporalRecord, FrozenModel

__all__ = [
    "EntityResolutionFailure",
    "GameAlias",
    "GameId",
    "PlayerAlias",
    "PlayerId",
    "SourceName",
    "SourceRef",
    "TeamAlias",
    "TeamId",
    "UnresolvedAliasError",
]

# Branded id types. A PlayerId is not interchangeable with a TeamId even though both wrap a
# UUID, which makes an entire class of transposition bugs unrepresentable.
PlayerId = Annotated[UUID, Field(description="Canonical player id")]
TeamId = Annotated[UUID, Field(description="Canonical team id")]
GameId = Annotated[UUID, Field(description="Canonical game id")]


class UnresolvedAliasError(KeyError):
    """A source name could not be bound to a canonical entity."""


class SourceName(FrozenModel):
    """A data provider. Declared here because every other module references it."""

    name: Annotated[str, Field(min_length=1, max_length=50)]
    trust_tier: Annotated[int, Field(ge=1, le=5)]
    """1 = authoritative league feed, 5 = scraped aggregator. Feeds reliability weighting."""


class SourceRef(FrozenModel):
    """Provenance pointer: where exactly did this fact come from."""

    source: SourceName
    source_id: Annotated[str, Field(min_length=1, max_length=200)]
    retrieved_at: AwareDatetime
    payload_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PlayerAlias(BitemporalRecord):
    """One source's spelling of one player's name, valid over a time range."""

    alias_id: UUID
    player_id: PlayerId
    source: SourceName
    source_player_id: Annotated[str, Field(min_length=1, max_length=100)]
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    confirmed_by: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    """Who verified the binding. ``None`` means heuristic -- allowed, but it caps the
    confidence of anything derived from this row."""


class TeamAlias(BitemporalRecord):
    """One source's code for one team (LV vs LVA vs Las Vegas)."""

    alias_id: UUID
    team_id: TeamId
    source: SourceName
    source_team_id: Annotated[str, Field(min_length=1, max_length=100)]
    display_name: Annotated[str, Field(min_length=1, max_length=100)]


class GameAlias(FrozenModel):
    """One source's identifier for one game.

    Not bitemporal: a source's game id never changes, so there is no valid-time history to
    keep, and the system axis alone adds nothing we would ever query. The alias is write-once
    like the game it names.
    """

    alias_id: UUID
    game_id: GameId
    source: SourceName
    source_game_id: Annotated[str, Field(min_length=1, max_length=100)]
    system_from: AwareDatetime
    system_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def _system_window_ordered(self) -> Self:
        if self.system_to is not None and self.system_to <= self.system_from:
            raise ValueError("system_to must follow system_from")
        return self


class EntityResolutionFailure(FrozenModel):
    """A recorded failure to resolve a name. Kept, not logged and forgotten.

    The count of these per source is a direct data-quality signal, and the specific names are
    the work queue for adding aliases.
    """

    failure_id: UUID
    source: SourceName
    raw_name: Annotated[str, Field(min_length=1, max_length=200)]
    attempted_at: AwareDatetime
    reason: Annotated[str, Field(min_length=1, max_length=500)]
