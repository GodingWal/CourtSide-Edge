"""Transparent baseline rotation and minutes distributions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_store.db import connect

AVAILABILITY = {
    "available": 1.0,
    "probable": 0.9,
    "questionable": 0.6,
    "doubtful": 0.25,
    "out": 0.0,
    "season_ending": 0.0,
    "not_with_team": 0.0,
    "unknown": 0.5,
}


@dataclass(frozen=True)
class RoleBatch:
    projected: int
    unchanged: int
    skipped: int


def _weighted(values: list[float]) -> float:
    weights = list(range(1, len(values) + 1))
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / sum(weights)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def role_projection(history: list[dict[str, Any]], designation: str) -> dict[str, float]:
    """Estimate a conditional-on-playing role while keeping availability separate."""
    minutes = [float(str(row["minutes"])) for row in history]
    starts = [1.0 if bool(row["started"]) else 0.0 for row in history]
    availability = AVAILABILITY.get(designation, AVAILABILITY["unknown"])
    expected = _weighted(minutes)
    if designation == "probable":
        expected *= 0.97
    elif designation in {"questionable", "doubtful", "unknown"}:
        expected *= 0.9
    elif availability == 0.0:
        expected = 0.0
    uncertainty = _std(minutes) + (4.0 if designation in {"questionable", "doubtful"} else 0.0)
    return {
        "availability": availability,
        "start_probability": _weighted(starts),
        "expected_minutes": min(45.0, max(0.0, expected)),
        "minutes_std": min(20.0, max(1.5, uncertainty)),
        "closing_probability": min(0.95, max(0.05, expected / 40.0)),
    }


def project_current_roles(*, now: datetime | None = None) -> RoleBatch:
    at = now or datetime.now(UTC)
    projected = unchanged = skipped = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT coalesce(m.to_player_id,q.player_id) AS player_id,q.game_id
               FROM wnba.prop_quotes q
               LEFT JOIN wnba.player_merges m
                 ON m.from_player_id=q.player_id AND m.system_to IS NULL
               WHERE q.is_available AND q.game_id IS NOT NULL AND q.locks_at>%s""",
            (at,),
        )
        targets = cur.fetchall()
        for target in targets:
            player_id = UUID(str(target["player_id"]))
            game_id = UUID(str(target["game_id"]))
            cur.execute(
                """SELECT l.line_id,l.minutes,l.started,g.scheduled_tipoff
                   FROM wnba.player_game_lines l JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL AND g.status='final'
                     AND g.scheduled_tipoff<(
                       SELECT scheduled_tipoff FROM wnba.games WHERE game_id=%s)
                   ORDER BY g.scheduled_tipoff DESC LIMIT 20""",
                (player_id, game_id),
            )
            history = list(reversed(cur.fetchall()))
            if len(history) < 5:
                skipped += 1
                continue
            cur.execute(
                """SELECT designation FROM wnba.injury_status
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL
                   ORDER BY system_from DESC LIMIT 1""",
                (player_id, game_id),
            )
            injury = cur.fetchone()
            designation = "available" if injury is None else str(injury["designation"])
            role = role_projection(history, designation)
            summary = {
                "history_games": len(history),
                "injury_designation": designation,
                "source_line_ids": [str(row["line_id"]) for row in history],
            }
            cur.execute(
                """SELECT availability_probability,start_probability,
                          closing_lineup_probability,expected_minutes,minutes_std,
                          feature_summary
                   FROM wnba.projected_roles
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                (player_id, game_id),
            )
            current = cur.fetchone()
            values = (
                role["availability"],
                role["start_probability"],
                role["closing_probability"],
                role["expected_minutes"],
                role["minutes_std"],
            )
            if current and all(
                abs(float(str(current[key])) - value) < 1e-9
                for key, value in zip(
                    (
                        "availability_probability",
                        "start_probability",
                        "closing_lineup_probability",
                        "expected_minutes",
                        "minutes_std",
                    ),
                    values,
                    strict=True,
                )
            ):
                unchanged += 1
                continue
            if current:
                cur.execute(
                    """UPDATE wnba.projected_roles SET system_to=%s
                       WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                    (at, player_id, game_id),
                )
            cur.execute(
                """INSERT INTO wnba.projected_roles
                   (record_id,player_id,game_id,availability_probability,start_probability,
                    closing_lineup_probability,expected_minutes,minutes_std,
                    minutes_restriction_probability,valid_from,system_from,model_version,
                    feature_summary)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'role-baseline-0.1.0',%s)""",
                (
                    uuid4(),
                    player_id,
                    game_id,
                    *values,
                    0.35 if designation in {"questionable", "probable"} else 0.0,
                    at,
                    at,
                    Jsonb(summary),
                ),
            )
            projected += 1
    return RoleBatch(projected, unchanged, skipped)
