"""Canonical identity and cross-source alias resolution.

Four sources will call the same athlete four different things. Until every one of those names
resolves to a single canonical id, any "player game log" you build is a rumour. Alias
resolution therefore *blocks* ingestion rather than guessing -- an unresolved name raises,
lands in quarantine, and waits for a human. Fuzzy-matching people into your training data is
how a model learns to forecast a player who does not exist.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl

from wnba_domain.base import BitemporalRecord, FrozenModel, Probability

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

# Canonical identifiers. Plain UUID aliases: the field name carries the meaning and the
# database foreign keys carry the enforcement.
PlayerId = UUID
TeamId = UUID
GameId = UUID


class SourceName(StrEnum):
    """Every external system we read. Free sources only, by design."""

    ESPN = "espn"
    STATS_WNBA = "stats_wnba"
    WEHOOP = "wehoop"
    PBPSTATS = "pbpstats"
    PRIZEPICKS = "prizepicks"
    UNDERDOG = "underdog"
    VIDEO = "video"
    MANUAL = "manual"
    DERIVED = "derived"


class SourceRef(FrozenModel):
    """Provenance for a single ingested fact. Attached to everything we store.

    ``retrieved_at`` is the ``system_from`` of whatever record this backs -- it is when *we*
    learned the fact, which is rarely when the fact became true.
    """

    source: SourceName
    source_entity_id: str = Field(min_length=1, max_length=200)
    retrieved_at: AwareDatetime
    url: HttpUrl | None = None
    payload_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    """Hash of the raw response. Makes ingestion replayable and tampering detectable."""


class PlayerAlias(BitemporalRecord):
    """A binding between one source's name for a player and our canonical id.

    Bitemporal because players change names, and because we sometimes discover a binding was
    wrong. Correcting it must not rewrite what we believed at forecast time.
    """

    player_id: PlayerId
    source: SourceName
    source_player_id: str = Field(min_length=1, max_length=200)
    source_display_name: str = Field(min_length=1, max_length=200)
    normalized_name: str = Field(min_length=1, max_length=200)
    """Case-folded, accent-stripped, suffix-normalised form used for candidate lookup only --
    never for automatic binding."""

    confidence: Probability = 1.0
    verified_by: str | None = None
    """Human who confirmed the binding. ``None`` means it came from an exact source id match."""


class TeamAlias(BitemporalRecord):
    team_id: TeamId
    source: SourceName
    source_team_id: str = Field(min_length=1, max_length=200)
    source_display_name: str = Field(min_length=1, max_length=200)


class EntityResolutionFailure(FrozenModel):
    """Recorded whenever a source name cannot be bound to a canonical entity.

    These are not warnings to be tuned away. Each one is a hole in the data through which a
    forecast can fall.
    """

    source: SourceName
    source_entity_id: str
    source_display_name: str
    entity_kind: str = Field(pattern=r"^(player|team|game)$")
    observed_at: AwareDatetime
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=10)
    candidate_scores: list[Probability] = Field(default_factory=list, max_length=10)
    context: str = ""


class UnresolvedAliasError(LookupError):
    """Raised by the resolver rather than returning a best guess."""

    def __init__(self, failure: EntityResolutionFailure) -> None:
        self.failure = failure
        super().__init__(
            f"unresolved {failure.entity_kind} alias from {failure.source}: "
            f"{failure.source_display_name!r} (id={failure.source_entity_id!r})"
        )


class GameAlias(FrozenModel):
    """A binding between one source's game id and the canonical game.

    The table carries the system time axis; the record asserts no valid-time claim, so it
    is declared non-temporal rather than pretending to a second axis it does not have.
    """

    alias_id: UUID
    game_id: GameId
    source: SourceName
    source_game_id: Annotated[str, Field(min_length=1, max_length=200)]
    system_from: AwareDatetime
    system_to: AwareDatetime | None = None
