"""What happened the last time the board looked like this.

An analyst's most useful question is rarely "what does the model say" -- it is "have we been here
before, and how did it go". The platform has thousands of settled decisions and no way to ask
them that. This module is the retrieval step: given the situation in front of an investigation,
find the settled episodes that most resemble it and report how they resolved.

**Similarity is over the situation, not the player.** Matching on player id would return four
episodes and a tautology. What generalises is the *shape* of the decision: same market, similar
line, similar stated probability, similar rotation certainty, similar component spread. A rookie
starting for the first time and a veteran starting for the first time are the same precedent.

**The comparison never sees the outcome.** Every field in the distance function is knowable
strictly before tipoff -- the same closed set the rule vocabulary uses, for the same reason.
Ranking precedents by anything settled would find the episodes that resolved like this one, which
is the one thing nobody knows yet.

**A precedent hit rate is not a forecast.** It is the base rate of a reference class, and the
reference class is small, self-selected and drawn from the same model whose calibration is under
question. It is reported with its sample size attached and it is never combined with the model's
probability. The moment those two numbers get averaged, this stops being retrieval and becomes an
unvalidated second model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

__all__ = [
    "MINIMUM_PRECEDENTS",
    "Precedent",
    "PrecedentSet",
    "find_precedents",
    "load_candidate_episodes",
    "similarity",
]

# Below this many precedents the hit rate is anecdote. Reported anyway -- with the count -- but
# `is_informative` is what the synthesizer reads before saying anything about it.
MINIMUM_PRECEDENTS = 12

# How many settled episodes to consider before ranking. Bounded because this runs inside an
# investigation with a lock time approaching.
CANDIDATE_LIMIT = 600

# Distance beyond which two decisions are not the same situation in any useful sense.
MAXIMUM_DISTANCE = 1.0

# Each dimension's scale: the difference that counts as "one unit of dissimilar". Stated
# explicitly rather than standardised from the data, because a z-score over a self-selected
# sample makes the scale depend on which episodes happen to have settled.
_SCALES: dict[str, float] = {
    "line": 6.0,
    "predicted_probability": 0.12,
    "projected_minutes": 8.0,
    "model_disagreement": 0.06,
    "start_probability": 0.30,
    "data_quality_score": 0.25,
}


@dataclass(frozen=True)
class Precedent:
    episode_id: UUID
    player_name: str
    prop_type: str
    line: float
    side: str
    predicted_probability: float
    hit: bool
    distance: float
    forecast_timestamp: Any = None

    @property
    def was_right(self) -> bool:
        """Whether the stated side actually landed. `hit` is already side-resolved upstream."""
        return self.hit

    def to_payload(self) -> dict[str, Any]:
        return {
            "episode_id": str(self.episode_id),
            "player": self.player_name,
            "prop_type": self.prop_type,
            "line": self.line,
            "side": self.side,
            "predicted_probability": round(self.predicted_probability, 4),
            "hit": self.hit,
            "distance": round(self.distance, 4),
        }


@dataclass(frozen=True)
class PrecedentSet:
    precedents: tuple[Precedent, ...]
    mean_predicted: float | None
    hit_rate: float | None
    distinct_players: int
    distinct_games: int

    @property
    def count(self) -> int:
        return len(self.precedents)

    @property
    def is_informative(self) -> bool:
        """Enough precedents, spread over enough distinct games, to be worth mentioning."""
        return self.count >= MINIMUM_PRECEDENTS and self.distinct_games >= 6

    @property
    def calibration_gap(self) -> float | None:
        """Stated minus observed over the reference class. Positive means overconfident here."""
        if self.hit_rate is None or self.mean_predicted is None:
            return None
        return self.mean_predicted - self.hit_rate

    def to_payload(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "distinct_players": self.distinct_players,
            "distinct_games": self.distinct_games,
            "mean_predicted_probability": (
                None if self.mean_predicted is None else round(self.mean_predicted, 4)
            ),
            "observed_hit_rate": None if self.hit_rate is None else round(self.hit_rate, 4),
            "calibration_gap": (
                None if self.calibration_gap is None else round(self.calibration_gap, 4)
            ),
            "is_informative": self.is_informative,
            "minimum_for_inference": MINIMUM_PRECEDENTS,
            "episodes": [precedent.to_payload() for precedent in self.precedents],
            "note": (
                "Base rate of a small self-selected reference class drawn from the same model "
                "under evaluation. Not a forecast and never combined with one."
            ),
        }


def similarity(subject: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    """Normalised distance between two decisions, or ``None`` if they are incomparable.

    A different market is incomparable rather than distant: rebounds at 7.5 and points at 7.5 are
    not near neighbours in any sense that would make one informative about the other.
    """
    if str(subject.get("prop_type")) != str(candidate.get("prop_type")):
        return None

    total = 0.0
    dimensions = 0
    for field, scale in _SCALES.items():
        left, right = subject.get(field), candidate.get(field)
        if left is None or right is None:
            continue
        difference = abs(float(str(left)) - float(str(right))) / scale
        total += difference * difference
        dimensions += 1
    if dimensions < 3:
        # Too few shared dimensions to call this a comparison. Refusing is better than
        # reporting a confident zero distance computed from one field they happen to share.
        return None
    return math.sqrt(total / dimensions)


def find_precedents(
    subject: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
    *,
    limit: int = 25,
    maximum_distance: float = MAXIMUM_DISTANCE,
) -> PrecedentSet:
    """Rank settled episodes by how much they resemble the decision under investigation."""
    scored: list[Precedent] = []
    for row in candidates:
        if str(row.get("episode_id")) == str(subject.get("episode_id")):
            continue
        distance = similarity(subject, row)
        if distance is None or distance > maximum_distance:
            continue
        scored.append(
            Precedent(
                episode_id=UUID(str(row["episode_id"])),
                player_name=str(row.get("full_name") or "unknown"),
                prop_type=str(row["prop_type"]),
                line=float(str(row["line"])),
                side=str(row["side"]),
                predicted_probability=float(str(row["predicted_probability"])),
                hit=bool(row["hit"]),
                distance=distance,
                forecast_timestamp=row.get("forecast_timestamp"),
            )
        )
    scored.sort(key=lambda precedent: precedent.distance)
    chosen = tuple(scored[: max(1, limit)])
    if not chosen:
        return PrecedentSet((), None, None, 0, 0)

    players = {precedent.player_name for precedent in chosen}
    games = {str(row["game_id"]) for row in candidates if _in(chosen, row)}
    return PrecedentSet(
        precedents=chosen,
        mean_predicted=math.fsum(p.predicted_probability for p in chosen) / len(chosen),
        hit_rate=math.fsum(float(p.hit) for p in chosen) / len(chosen),
        distinct_players=len(players),
        distinct_games=len(games),
    )


def _in(precedents: Sequence[Precedent], row: dict[str, Any]) -> bool:
    identifier = str(row.get("episode_id"))
    return any(str(precedent.episode_id) == identifier for precedent in precedents)


def load_candidate_episodes(
    cur: Any, *, prop_type: str, before: Any, limit: int = CANDIDATE_LIMIT
) -> list[dict[str, Any]]:
    """Settled episodes in the same market, forecast strictly before this one.

    ``before`` is the point-in-time cut. Without it the retrieval would happily return an episode
    from tomorrow's slate as a precedent for today's, which is leakage wearing the clothes of
    historical memory.
    """
    cur.execute(
        """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,d.line,
                  d.predicted_probability,d.projected_minutes,d.model_disagreement,
                  d.data_quality_score,d.forecast_timestamp,
                  f.start_probability,
                  o.hit,p.full_name
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           JOIN wnba.players p ON p.player_id=d.player_id
           LEFT JOIN LATERAL (
               SELECT (fs.features->>'start_probability')::double precision AS start_probability
               FROM wnba.feature_snapshots fs
               WHERE fs.feature_snapshot_id=d.feature_snapshot_id
           ) f ON true
           WHERE d.prop_type=%s AND d.forecast_timestamp<%s
             AND NOT o.was_voided AND NOT o.was_push
           ORDER BY d.forecast_timestamp DESC LIMIT %s""",
        (prop_type, before, max(1, limit)),
    )
    return [dict(row) for row in cur.fetchall()]
