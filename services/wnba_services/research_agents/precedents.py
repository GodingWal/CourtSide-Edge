"""Point-in-time retrieval of comparable settled decision episodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Precedent:
    episode_id: UUID
    similarity: float
    summary: str


def retrieve_precedents(cur: Any, projection: dict[str, Any], *, limit: int = 5) -> list[Precedent]:
    """Retrieve only episodes settled before this forecast, preventing outcome leakage."""
    cur.execute(
        """SELECT d.episode_id,d.prop_type,d.line,d.predicted_probability,o.actual_stat,o.hit,
                  abs(d.line-%s) AS line_distance
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           WHERE d.player_id=%s AND d.prop_type=%s
             AND o.settled_at < %s AND NOT o.was_voided AND NOT o.was_push
           ORDER BY abs(d.line-%s),o.settled_at DESC LIMIT %s""",
        (
            projection["line"],
            projection["player_id"],
            projection["prop_type"],
            projection["generated_at"],
            projection["line"],
            limit,
        ),
    )
    found: list[Precedent] = []
    for row in cur.fetchall():
        distance = float(row["line_distance"])
        similarity = max(0.0, 1.0 - distance / max(1.0, float(projection["line"])))
        found.append(
            Precedent(
                UUID(str(row["episode_id"])),
                similarity,
                f"line={row['line']} probability={row['predicted_probability']} "
                f"actual={row['actual_stat']} hit={row['hit']}",
            )
        )
    return found
