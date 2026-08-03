"""Core basketball objects.

Two WNBA-specific constants shape everything downstream and are wrong in most code written for
the NBA: regulation is **40 minutes** (4 x 10), not 48, and a team therefore spends **200
player-minutes** in regulation, not 240. Overtime adds 5 minutes, i.e. 25 team-minutes.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from wnba_domain.base import (
    BitemporalRecord,
    Count,
    FrozenModel,
    NonNegativeRate,
    Probability,
    StrictModel,
)
from wnba_domain.enums import GameStatus, InjuryDesignation, Position, Venue
from wnba_domain.identity import GameId, PlayerId, SourceRef, TeamId

__all__ = [
    "OVERTIME_MINUTES",
    "REGULATION_MINUTES",
    "TEAM_MINUTES_PER_REGULATION",
    "DefensiveMatchup",
    "Game",
    "GameContext",
    "InjuryStatusRecord",
    "Player",
    "PlayerGameLine",
    "Possession",
    "ProjectedRole",
    "RosterAssignment",
    "Season",
    "Shot",
    "Team",
    "team_minutes_for",
]

REGULATION_MINUTES: float = 40.0
OVERTIME_MINUTES: float = 5.0
TEAM_MINUTES_PER_REGULATION: float = 200.0


def team_minutes_for(overtime_periods: int) -> float:
    """Total player-minutes a team distributes in a game with ``overtime_periods`` OTs."""
    if overtime_periods < 0:
        raise ValueError("overtime_periods cannot be negative")
    return (REGULATION_MINUTES + OVERTIME_MINUTES * overtime_periods) * 5.0


# --------------------------------------------------------------------------------------
# Reference entities
# --------------------------------------------------------------------------------------
class Season(FrozenModel):
    year: Annotated[int, Field(ge=1997, le=2100)]
    regular_season_start: AwareDatetime
    regular_season_end: AwareDatetime
    games_per_team: Annotated[int, Field(ge=1, le=60)]


class Team(FrozenModel):
    team_id: TeamId
    abbreviation: Annotated[str, Field(min_length=2, max_length=5, pattern=r"^[A-Z]+$")]
    full_name: Annotated[str, Field(min_length=2, max_length=80)]
    market: Annotated[str, Field(min_length=2, max_length=60)]
    time_zone: Annotated[str, Field(min_length=3, max_length=64)]
    """IANA zone of the home arena. Drives travel and body-clock features."""


class Player(FrozenModel):
    player_id: PlayerId
    full_name: Annotated[str, Field(min_length=2, max_length=100)]
    position: Position = Position.UNKNOWN
    height_inches: Annotated[int, Field(ge=54, le=90)] | None = None
    birth_date: AwareDatetime | None = None
    rookie_season: Annotated[int, Field(ge=1997, le=2100)] | None = None


class Game(FrozenModel):
    game_id: GameId
    season_year: Annotated[int, Field(ge=1997, le=2100)]
    scheduled_tipoff: AwareDatetime
    home_team_id: TeamId
    away_team_id: TeamId
    status: GameStatus = GameStatus.SCHEDULED
    overtime_periods: Annotated[int, Field(ge=0, le=6)] = 0
    home_points: Count | None = None
    away_points: Count | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _teams_differ(self) -> Self:
        if self.home_team_id == self.away_team_id:
            raise ValueError("a team cannot play itself")
        return self

    @property
    def is_final(self) -> bool:
        return self.status is GameStatus.FINAL


class GameContext(BitemporalRecord):
    """Situational load for one team in one game. Cheap features, real predictive weight."""

    game_id: GameId
    team_id: TeamId
    venue: Venue
    days_rest: Annotated[int, Field(ge=0, le=60)]
    is_back_to_back: bool
    games_in_last_seven_days: Annotated[int, Field(ge=0, le=7)]
    travel_miles_since_last_game: Annotated[float, Field(ge=0.0, le=12000.0)] = 0.0
    time_zones_crossed: Annotated[int, Field(ge=-12, le=12)] = 0
    previous_game_overtime_periods: Annotated[int, Field(ge=0, le=6)] = 0


# --------------------------------------------------------------------------------------
# Availability and role
# --------------------------------------------------------------------------------------
class InjuryStatusRecord(BitemporalRecord):
    """One availability observation.

    Bitemporal is not optional here. "Questionable at 14:30, out at 17:40" is the single most
    common way a backtest quietly cheats: reading the 17:40 row while pretending to forecast
    at 15:00 produces a system that is extraordinarily good at predicting the past.
    """

    player_id: PlayerId
    game_id: GameId | None = None
    designation: InjuryDesignation
    body_part: Annotated[str, Field(max_length=60)] | None = None
    detail: Annotated[str, Field(max_length=500)] | None = None
    source_ref: SourceRef


class RosterAssignment(BitemporalRecord):
    player_id: PlayerId
    team_id: TeamId
    is_two_way: bool = False
    is_suspended: bool = False


class ProjectedRole(BitemporalRecord):
    """Our belief about a player's role in an upcoming game.

    **There is no ``is_starter`` field, and there never will be.** The WNBA does not require
    teams to submit lineups before tip, so "confirmed starter" is not information that exists
    to be ingested. Anything claiming otherwise is a projection wearing a fact's clothing.
    Encoding it as a probability forces every downstream consumer to carry the uncertainty
    instead of quietly discarding it.
    """

    player_id: PlayerId
    game_id: GameId
    availability_probability: Probability
    """P(plays at all), given everything known at ``system_from``."""

    start_probability: Probability
    """P(in the opening lineup | plays)."""

    closing_lineup_probability: Probability
    """P(on court for the final possessions | plays). Drives the upper tail of minutes."""

    expected_minutes: Annotated[float, Field(ge=0.0, le=60.0)]
    minutes_std: Annotated[float, Field(ge=0.0, le=25.0)]
    minutes_restriction_probability: Probability = 0.0
    expected_usage_rate: Probability | None = None
    depth_chart_rank: Annotated[int, Field(ge=1, le=15)] | None = None

    @model_validator(mode="after")
    def _minutes_consistent_with_availability(self) -> Self:
        if self.availability_probability == 0.0 and self.expected_minutes > 0.0:
            raise ValueError("expected_minutes must be 0 when the player cannot play")
        return self


# --------------------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------------------
class PlayerGameLine(FrozenModel):
    """A settled box-score line. The ground truth every forecast is scored against."""

    player_id: PlayerId
    game_id: GameId
    team_id: TeamId
    minutes: Annotated[float, Field(ge=0.0, le=70.0)]
    started: bool
    """Observed *after the fact*. Legal here precisely because it is an outcome, not an input.
    Feature builders must never read this for a game they are forecasting."""

    points: Count
    rebounds_offensive: Count
    rebounds_defensive: Count
    assists: Count
    steals: Count
    blocks: Count
    turnovers: Count
    personal_fouls: Count
    field_goals_made: Count
    field_goals_attempted: Count
    three_pointers_made: Count
    three_pointers_attempted: Count
    free_throws_made: Count
    free_throws_attempted: Count
    plus_minus: Annotated[int, Field(ge=-100, le=100)] | None = None

    @model_validator(mode="after")
    def _internally_consistent(self) -> Self:
        checks = (
            ("field goals", self.field_goals_made, self.field_goals_attempted),
            ("three pointers", self.three_pointers_made, self.three_pointers_attempted),
            ("free throws", self.free_throws_made, self.free_throws_attempted),
        )
        for label, made, attempted in checks:
            if made > attempted:
                raise ValueError(f"{label}: made ({made}) exceeds attempted ({attempted})")
        if self.three_pointers_made > self.field_goals_made:
            raise ValueError("three pointers made cannot exceed total field goals made")
        if self.three_pointers_attempted > self.field_goals_attempted:
            raise ValueError("three pointers attempted cannot exceed total field goals attempted")

        expected = (
            2 * (self.field_goals_made - self.three_pointers_made)
            + 3 * self.three_pointers_made
            + self.free_throws_made
        )
        if expected != self.points:
            raise ValueError(
                f"points ({self.points}) disagree with shot breakdown ({expected}); "
                "the box score is internally inconsistent"
            )
        return self

    @property
    def rebounds(self) -> int:
        return self.rebounds_offensive + self.rebounds_defensive

    @property
    def points_rebounds_assists(self) -> int:
        return self.points + self.rebounds + self.assists


# --------------------------------------------------------------------------------------
# Fine-grained events
# --------------------------------------------------------------------------------------
class Possession(FrozenModel):
    possession_id: str = Field(min_length=1, max_length=64)
    game_id: GameId
    offense_team_id: TeamId
    period: Annotated[int, Field(ge=1, le=10)]
    start_game_clock_seconds: Annotated[float, Field(ge=0.0, le=600.0)]
    duration_seconds: Annotated[float, Field(ge=0.0, le=100.0)]
    points_scored: Annotated[int, Field(ge=0, le=6)]
    offense_player_ids: list[PlayerId] = Field(min_length=0, max_length=5)
    defense_player_ids: list[PlayerId] = Field(min_length=0, max_length=5)


class Shot(FrozenModel):
    shot_id: str = Field(min_length=1, max_length=64)
    game_id: GameId
    player_id: PlayerId
    possession_id: str | None = None
    period: Annotated[int, Field(ge=1, le=10)]
    game_clock_seconds: Annotated[float, Field(ge=0.0, le=600.0)]
    shot_value: Annotated[int, Field(ge=2, le=3)]
    made: bool
    assisted_by: PlayerId | None = None
    court_x_feet: Annotated[float, Field(ge=-30.0, le=30.0)] | None = None
    court_y_feet: Annotated[float, Field(ge=-5.0, le=50.0)] | None = None
    distance_feet: Annotated[float, Field(ge=0.0, le=60.0)] | None = None
    # Video-derived, never required. Absent for every historically backfilled shot.
    nearest_defender_id: PlayerId | None = None
    nearest_defender_distance_feet: Annotated[float, Field(ge=0.0, le=40.0)] | None = None
    is_video_derived: bool = False

    @model_validator(mode="after")
    def _assist_requires_make(self) -> Self:
        if self.assisted_by is not None and not self.made:
            raise ValueError("a missed shot cannot be assisted")
        if self.assisted_by == self.player_id:
            raise ValueError("a player cannot assist her own shot")
        return self


class DefensiveMatchup(StrictModel):
    """Time one defender spent guarding one offensive player. Video-derived."""

    game_id: GameId
    offensive_player_id: PlayerId
    defensive_player_id: PlayerId
    seconds_matched: NonNegativeRate
    partial_possessions: NonNegativeRate
    confidence: Probability
    is_video_derived: bool = True
