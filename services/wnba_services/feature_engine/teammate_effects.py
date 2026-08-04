"""Shrunk historical teammate-absence effects, estimated jointly rather than one at a time.

The previous estimator compared the player's rate in games with a teammate against games
without them, once per teammate, and the forecast then multiplied the resulting multipliers
together. That double-counts, and it does so in the worst possible direction.

Absences are correlated. Two players on the same team are frequently out together -- the same
illness, the same rest night, the same collapse of a season already lost. A marginal comparison
credits *each* of them with the entire lift the player got on those nights, because from each
comparison's point of view the other absence is invisible. Multiply two such estimates together
and the model claims a 1.20 x 1.20 = 1.44 boost for what the data only ever showed as 1.20. The
old ``[0.75, 1.35]`` clamp in the forecast was treating this symptom.

The fix is to estimate every candidate teammate's effect in one weighted ridge regression, with
an absence indicator per teammate. Each coefficient is then the effect of *that* absence holding
the others fixed, which is the quantity the forecast was multiplying all along -- so the schema
is unchanged, and the multipliers it stores are finally the kind that may be multiplied.

Rows are weighted by minutes because a rate from four minutes is not evidence on par with a rate
from thirty-four, and the ridge penalty is expressed in games so its strength is arguable rather
than arbitrary: it is worth roughly eight average games of evidence saying there is no effect.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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
METHOD = "joint-absence-ridge-0.2.0"

_MIN_GAMES_WITH = 5
_MIN_GAMES_WITHOUT = 3
_RIDGE_PRIOR_GAMES = 8.0
_RATE_FLOOR = 1e-4
_MIN_MULTIPLIER = 0.7
_MAX_MULTIPLIER = 1.3
_MAX_MINUTES_DELTA = 5.0


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


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. ``None`` when the system is singular.

    Returning ``None`` rather than a pseudo-inverse: a singular design means two teammates were
    absent for exactly the same games, so their individual effects are genuinely unidentified.
    Inventing a split between them would be fabricating a distinction the season never drew.
    """
    size = len(vector)
    augmented = [[*row, value] for row, value in zip(matrix, vector, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for position in range(column, size + 1):
                augmented[row][position] -= factor * augmented[column][position]

    solution = [0.0] * size
    for column in reversed(range(size)):
        total = augmented[column][size] - math.fsum(
            augmented[column][position] * solution[position] for position in range(column + 1, size)
        )
        solution[column] = total / augmented[column][column]
    return solution


def _weighted_ridge(
    design: list[list[float]],
    response: list[float],
    weights: list[float],
    penalty: float,
) -> list[float] | None:
    """Solve ``(X'WX + penalty*I) b = X'Wy``, leaving the intercept unpenalised."""
    width = len(design[0])
    normal = [[0.0] * width for _ in range(width)]
    target = [0.0] * width
    for row, value, weight in zip(design, response, weights, strict=True):
        for i in range(width):
            target[i] += weight * row[i] * value
            for j in range(width):
                normal[i][j] += weight * row[i] * row[j]
    for i in range(1, width):
        normal[i][i] += penalty
    return _solve(normal, target)


def estimate_joint_effects(
    player_games: Sequence[Mapping[str, Any]],
    presence: Mapping[UUID, set[UUID]],
    columns: tuple[str, ...],
) -> dict[UUID, EffectEstimate]:
    """Estimate every candidate teammate's absence effect in one regression.

    ``presence`` maps each teammate to the set of games in which they appeared. Teammates whose
    absence the player's history cannot speak to -- too few games either side -- are dropped
    before fitting rather than estimated badly, so a thin case produces no row instead of a
    confident-looking one.
    """
    played = [row for row in player_games if float(str(row["minutes"])) > 0.0]
    if len(played) < _MIN_GAMES_WITH + _MIN_GAMES_WITHOUT:
        return {}

    game_ids = [UUID(str(row["game_id"])) for row in played]
    candidates: list[UUID] = []
    counts: dict[UUID, tuple[int, int]] = {}
    for teammate, present_games in presence.items():
        absent = sum(1 for game_id in game_ids if game_id not in present_games)
        together = len(game_ids) - absent
        if together < _MIN_GAMES_WITH or absent < _MIN_GAMES_WITHOUT:
            continue
        candidates.append(teammate)
        counts[teammate] = (together, absent)
    if not candidates:
        return {}

    design = [
        [1.0] + [0.0 if game_id in presence[teammate] else 1.0 for teammate in candidates]
        for game_id in game_ids
    ]
    minutes = [float(str(row["minutes"])) for row in played]
    log_rates = [
        math.log(
            max(
                _RATE_FLOOR,
                sum(float(str(row[column])) for column in columns) / float(str(row["minutes"])),
            )
        )
        for row in played
    ]

    mean_minutes = math.fsum(minutes) / len(minutes)
    penalty = _RIDGE_PRIOR_GAMES * mean_minutes
    rate_fit = _weighted_ridge(design, log_rates, minutes, penalty)
    # Minutes are modelled on their own scale with uniform weights: the response *is* the
    # quantity of interest here, so weighting by it would double-count long games.
    minutes_fit = _weighted_ridge(design, minutes, [1.0] * len(minutes), _RIDGE_PRIOR_GAMES)
    if rate_fit is None or minutes_fit is None:
        return {}

    effects: dict[UUID, EffectEstimate] = {}
    for index, teammate in enumerate(candidates, start=1):
        together, absent = counts[teammate]
        reliability = min(1.0, together / 15) * min(1.0, absent / 10)
        raw_multiplier = math.exp(max(-1.0, min(1.0, rate_fit[index])))
        multiplier = (
            1.0 + (max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, raw_multiplier)) - 1.0) * reliability
        )
        minutes_delta = (
            max(-_MAX_MINUTES_DELTA, min(_MAX_MINUTES_DELTA, minutes_fit[index])) * reliability
        )
        effects[teammate] = EffectEstimate(
            rate_multiplier=multiplier,
            minutes_delta=minutes_delta,
            games_with=together,
            games_without=absent,
            confidence=min(0.8, reliability * 0.8),
        )
    return effects


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
            presence: dict[UUID, set[UUID]] = {}
            for teammate_id in unavailable:
                cur.execute(
                    """SELECT l.game_id FROM wnba.player_game_lines l
                       JOIN wnba.games g ON g.game_id=l.game_id
                       WHERE l.player_id=%s AND l.team_id=%s AND l.system_to IS NULL
                         AND l.minutes>0 AND g.scheduled_tipoff<(
                           SELECT scheduled_tipoff FROM wnba.games WHERE game_id=%s)""",
                    (teammate_id, team_id, game_id),
                )
                presence[teammate_id] = {UUID(str(row["game_id"])) for row in cur.fetchall()}

            # One fit for all absent teammates at once. Estimating them separately and letting
            # the forecast multiply the results is the bug this method version exists to fix.
            estimates = estimate_joint_effects(player_games, presence, MARKETS[prop_type])
            insufficient += len(unavailable) - len(estimates)
            for teammate_id, estimate in estimates.items():
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
