"""Pydantic schemas for the pick pipeline.

These models are the single source of truth for every numeric value a pick
carries. `Pick` is frozen: `projection`, `line`, `hit_probability` and
`breakeven_probability` are computed exactly once in the projection /
validation layer and can never be restated downstream. `edge` is a computed
field (`projection - line`) — it is not LLM-supplied and is not stored
independently, so an edge that disagrees with its own projection/line pair
cannot exist.
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Recommendation(str, Enum):
    BUY = "Buy"    # over
    SELL = "Sell"  # under


class PickStatus(str, Enum):
    PUBLISHABLE = "PUBLISHABLE"
    LEAN = "LEAN"          # below publish threshold but above 50%: retained, never published
    REJECTED = "REJECTED"


# ── Narrative payload schema (P0-3) ──────────────────────────────────────────
# The narrative agent receives exactly this payload and may only reference
# facts present in it. Required fields are required on purpose: a projection
# without std/hit_probability is a schema violation (P1-1), not a default.

class PlayerInfo(BaseModel):
    name: str
    team: str
    position: str = ""
    career_games: int = 0
    seasons: int = 0


class StatInfo(BaseModel):
    category: str
    line: float
    book: str = "underdog"


class ProjectionInfo(BaseModel):
    mean: float
    std: float
    hit_probability: float = Field(ge=0.0, le=1.0)


class FormInfo(BaseModel):
    l5_avg: float
    l10_avg: float
    minutes_l5: float


class MatchupInfo(BaseModel):
    opponent: str
    opp_def_rank_vs_stat: int = 0
    pace_rank: int = 0


class GameInfo(BaseModel):
    spread: float = 0.0
    total: float = 0.0
    # Win probability of the pick player's team.
    win_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    home: bool = True


class InjuryRecord(BaseModel):
    player: str
    team: str
    status: str
    last_updated: str  # ISO8601
    returning: bool = False  # set when this record marks a return from injury


class NarrativePayload(BaseModel):
    player: PlayerInfo
    stat: StatInfo
    projection: ProjectionInfo
    form: FormInfo
    matchup: MatchupInfo
    game: GameInfo
    injuries: list[InjuryRecord] = Field(default_factory=list)


# ── Pick (P0-2) ──────────────────────────────────────────────────────────────

# Counting stats eligible for blowout-escalation rules.
COUNTING_STATS = {"PTS", "REB", "AST", "3PM", "STL", "BLK"}


class Pick(BaseModel):
    """An immutable pick. All numeric fields are frozen at construction.

    `extra="forbid"` means an upstream agent cannot smuggle in its own
    `edge` value — the only edge that exists is the computed one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pick_id: str
    player: str
    team: str = ""
    stat: str
    book: str = "prizepicks"
    entry_type: str = "power"
    legs: int = 3
    recommendation: Recommendation
    projection: float
    std: float | None = None
    line: float
    hit_probability: float = Field(ge=0.0, le=1.0)  # P(recommended side wins)
    breakeven_probability: float = Field(ge=0.0, le=1.0)
    model_version: str = "unversioned"
    captured_at: str | None = None  # ISO8601 of the line snapshot this pick priced
    flags: tuple[str, ...] = ()
    grade: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def edge(self) -> float:
        return round(self.projection - self.line, 4)


def pick_from_message(data: dict) -> Pick:
    """Rebuild a Pick from a transport dict (e.g. a mesh message).

    `model_dump()` serializes the computed `edge` for observability; it is
    informational only and must not round-trip as input (extra="forbid"
    exists precisely so nobody can supply their own edge).
    """
    cleaned = dict(data)
    cleaned.pop("edge", None)
    return Pick(**cleaned)


class ValidationResult(BaseModel):
    """Outcome of a validation stage for one pick."""

    status: PickStatus
    reason_codes: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    details: dict = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PickStatus.PUBLISHABLE
