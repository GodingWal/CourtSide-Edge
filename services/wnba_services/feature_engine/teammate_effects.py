"""Shrunk historical teammate-absence effects for the current board."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from wnba_store.db import connect

MARKETS: dict[str, tuple[str, ...]] = {
    "points": ("points",),
    "rebounds": ("rebounds_offensive", "rebounds_defensive"),
    "assists": ("assists",),
    "three_pointers": ("three_pointers_made",),
    "points_rebounds_assists": (
        "points",
        "rebounds_offensive",
        "rebounds_defensive",
        "assists",
    ),
    "points_rebounds": ("points", "rebounds_offensive", "rebounds_defensive"),
    "points_assists": ("points", "assists"),
    "rebounds_assists": ("rebounds_offensive", "rebounds_defensive", "assists"),
}
METHOD = "paired-absence-shrunk-0.1.0"


@dataclass(frozen=True)
class EffectEstimate:
    rate_multiplier: float
    minutes_delta: float
    games_with: int
    games_without: int
    confidence: float


@dataclass(frozen=True)
class EffectBatch:
    projected: int
    unchanged: int
    insufficient: int


def estimate_effect(
    player_games: list[dict[str, Any]], present_game_ids: set[UUID], columns: tuple[str, ...]
) -> EffectEstimate | None:
    with_teammate: list[dict[str, Any]] = []
    without_teammate: list[dict[str, Any]] = []
    for row in player_games:
        bucket = (
            with_teammate if UUID(str(row["game_id"])) in present_game_ids else without_teammate
        )
        bucket.append(row)
    if len(with_teammate) < 5 or len(without_teammate) < 3:
        return None

    def rate(rows: list[dict[str, Any]]) -> float:
        total_stat = sum(sum(float(str(row[column])) for column in columns) for row in rows)
        total_minutes = sum(float(str(row["minutes"])) for row in rows)
        return total_stat / max(1.0, total_minutes)

    with_rate = rate(with_teammate)
    without_rate = rate(without_teammate)
    raw_multiplier = 1.0 if with_rate <= 0 else without_rate / with_rate
    reliability = min(1.0, len(with_teammate) / 15) * min(1.0, len(without_teammate) / 10)
    multiplier = 1.0 + (max(0.7, min(1.3, raw_multiplier)) - 1.0) * reliability
    with_minutes = sum(float(str(row["minutes"])) for row in with_teammate) / len(with_teammate)
    without_minutes = sum(float(str(row["minutes"])) for row in without_teammate) / len(
        without_teammate
    )
    minutes_delta = max(-5.0, min(5.0, without_minutes - with_minutes)) * reliability
    return EffectEstimate(
        rate_multiplier=multiplier,
        minutes_delta=minutes_delta,
        games_with=len(with_teammate),
        games_without=len(without_teammate),
        confidence=min(0.8, reliability * 0.8),
    )


def project_teammate_effects(*, now: datetime | None = None) -> EffectBatch:
    at = now or datetime.now(UTC)
    projected = unchanged = insufficient = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT coalesce(m.to_player_id,q.player_id) AS player_id,q.game_id,
                              q.prop_type
               FROM wnba.prop_quotes q
               LEFT JOIN wnba.player_merges m
                 ON m.from_player_id=q.player_id AND m.system_to IS NULL
               WHERE q.is_available AND q.game_id IS NOT NULL AND q.locks_at>%s
                 AND q.prop_type=ANY(%s)""",
            (at, list(MARKETS)),
        )
        targets = cur.fetchall()
        for target in targets:
            player_id = UUID(str(target["player_id"]))
            game_id = UUID(str(target["game_id"]))
            prop_type = str(target["prop_type"])
            cur.execute(
                """SELECT l.* FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL AND l.minutes>0
                     AND g.status='final' AND g.scheduled_tipoff<(
                       SELECT scheduled_tipoff FROM wnba.games WHERE game_id=%s)
                   ORDER BY g.scheduled_tipoff DESC LIMIT 80""",
                (player_id, game_id),
            )
            player_games = cur.fetchall()
            if len(player_games) < 8:
                insufficient += 1
                continue
            team_id = player_games[0]["team_id"]
            cur.execute(
                """SELECT i.player_id FROM wnba.injury_status i
                   WHERE i.game_id=%s AND i.designation IN ('out','season_ending','not_with_team')
                     AND i.system_to IS NULL AND i.player_id<>%s
                     AND EXISTS (
                       SELECT 1 FROM wnba.player_game_lines l
                       WHERE l.player_id=i.player_id AND l.team_id=%s AND l.system_to IS NULL)""",
                (game_id, player_id, team_id),
            )
            unavailable = [UUID(str(row["player_id"])) for row in cur.fetchall()]
            for teammate_id in unavailable:
                cur.execute(
                    """SELECT game_id FROM wnba.player_game_lines
                       WHERE player_id=%s AND team_id=%s AND system_to IS NULL AND minutes>0""",
                    (teammate_id, team_id),
                )
                present = {UUID(str(row["game_id"])) for row in cur.fetchall()}
                estimate = estimate_effect(player_games, present, MARKETS[prop_type])
                if estimate is None:
                    insufficient += 1
                    continue
                cur.execute(
                    """SELECT rate_multiplier,minutes_delta,games_with,games_without
                       FROM wnba.teammate_role_effects
                       WHERE player_id=%s AND teammate_id=%s AND game_id=%s AND prop_type=%s
                         AND system_to IS NULL""",
                    (player_id, teammate_id, game_id, prop_type),
                )
                current = cur.fetchone()
                if current and (
                    abs(float(str(current["rate_multiplier"])) - estimate.rate_multiplier) < 1e-9
                    and abs(float(str(current["minutes_delta"])) - estimate.minutes_delta) < 1e-9
                    and int(str(current["games_with"])) == estimate.games_with
                    and int(str(current["games_without"])) == estimate.games_without
                ):
                    unchanged += 1
                    continue
                if current:
                    cur.execute(
                        """UPDATE wnba.teammate_role_effects SET system_to=%s
                           WHERE player_id=%s AND teammate_id=%s AND game_id=%s
                             AND prop_type=%s AND system_to IS NULL""",
                        (at, player_id, teammate_id, game_id, prop_type),
                    )
                cur.execute(
                    """INSERT INTO wnba.teammate_role_effects
                       (effect_id,player_id,teammate_id,game_id,prop_type,rate_multiplier,
                        minutes_delta,games_with,games_without,confidence,method_version,
                        valid_from,system_from)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        uuid4(),
                        player_id,
                        teammate_id,
                        game_id,
                        prop_type,
                        estimate.rate_multiplier,
                        estimate.minutes_delta,
                        estimate.games_with,
                        estimate.games_without,
                        estimate.confidence,
                        METHOD,
                        at,
                        at,
                    ),
                )
                projected += 1
    return EffectBatch(projected, unchanged, insufficient)
