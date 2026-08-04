"""Point-in-time pace, opponent, rest, and blowout context.

**Opponent adjustment.** The raw version of this feature divided a team's conceded rate by the
league average and called the result a defensive multiplier. That measures schedule as much as
defence: a team whose last ten games happened to fall against the league's worst offences reads
as elite, and the forecast then shades every prop against them downward for a quality they do
not have. Over a short season the schedule imbalance is large enough to swamp the real signal.

:func:`opponent_adjusted_rates` fits the obvious two-way additive model --
``rate = league + offence(scorer) + defence(conceder)`` -- by alternating means, which is the
Gauss-Seidel solution to the least-squares problem and converges in a handful of passes at this
size. A defence is then credited only with what it conceded *beyond what its opponents score
against everyone else*.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from wnba_store.db import connect

MARKET_COLUMNS: dict[str, tuple[str, ...]] = {
    "points": ("points",),
    "rebounds": ("rebounds_offensive", "rebounds_defensive"),
    "assists": ("assists",),
    "three_pointers": ("three_pointers_made",),
    "points_rebounds_assists": ("points", "rebounds_offensive", "rebounds_defensive", "assists"),
    "points_rebounds": ("points", "rebounds_offensive", "rebounds_defensive"),
    "points_assists": ("points", "assists"),
    "rebounds_assists": ("rebounds_offensive", "rebounds_defensive", "assists"),
}
METHOD = "pace-defense-rest-0.2.0"
_ADJUSTMENT_ITERATIONS = 30
# Games of evidence before a team's adjusted defensive effect is credited in full. Deliberately
# heavier than the raw version used, because an adjusted effect is estimated jointly with every
# other team's offence and so converges more slowly than a simple average.
_DEFENCE_PRIOR_GAMES = 12.0
STAT_COLUMNS = tuple(sorted({column for columns in MARKET_COLUMNS.values() for column in columns}))


@dataclass(frozen=True)
class TeamGame:
    game_id: UUID
    team_id: UUID
    opponent_id: UUID
    tipoff: datetime
    possessions: float
    point_diff: float
    totals: dict[str, float]
    allowed: dict[str, float]


@dataclass(frozen=True)
class MatchupEstimate:
    expected_possessions: float
    pace_multiplier: float
    defense_multiplier: float
    expected_margin: float
    blowout_probability: float
    team_rest_days: float
    opponent_rest_days: float
    opponent_sample: int
    confidence: float


@dataclass(frozen=True)
class MatchupBatch:
    projected: int
    unchanged: int
    skipped: int


@dataclass(frozen=True)
class AdjustedRates:
    """League baseline plus each team's additive offensive and defensive effects."""

    league_rate: float
    offense: dict[UUID, float]
    defense: dict[UUID, float]
    games_by_team: dict[UUID, int]

    def defense_rate(self, team_id: UUID) -> float:
        """Rate this defence concedes against a league-average offence."""
        return max(0.0, self.league_rate + self.defense.get(team_id, 0.0))

    def shrunk_defense_rate(self, team_id: UUID) -> float:
        """The same, pulled toward the league baseline by how little we have seen."""
        games = self.games_by_team.get(team_id, 0)
        weight = games / (games + _DEFENCE_PRIOR_GAMES)
        return weight * self.defense_rate(team_id) + (1.0 - weight) * self.league_rate


def opponent_adjusted_rates(
    games: list[TeamGame], columns: tuple[str, ...], *, iterations: int = _ADJUSTMENT_ITERATIONS
) -> AdjustedRates:
    """Separate defensive quality from strength of schedule.

    Alternating means on ``rate = league + offence(scorer) + defence(conceder)``. Both effect
    vectors are re-centred to mean zero on every pass, because the model is otherwise
    unidentified -- adding a constant to every offence and subtracting it from every defence
    describes the same data -- and an unidentified fit drifts instead of converging.
    """
    scored: dict[UUID, list[tuple[float, UUID]]] = defaultdict(list)
    conceded: dict[UUID, list[tuple[float, UUID]]] = defaultdict(list)
    rates: list[float] = []
    for game in games:
        if game.possessions <= 0.0:
            continue
        own = sum(game.totals.get(column, 0.0) for column in columns) / game.possessions
        against = sum(game.allowed.get(column, 0.0) for column in columns) / game.possessions
        scored[game.team_id].append((own, game.opponent_id))
        conceded[game.team_id].append((against, game.opponent_id))
        rates.append(own)

    league = sum(rates) / len(rates) if rates else 0.0
    teams = set(scored) | set(conceded)
    offense = dict.fromkeys(teams, 0.0)
    defense = dict.fromkeys(teams, 0.0)

    for _ in range(iterations):
        for team in teams:
            rows = scored[team]
            if rows:
                offense[team] = sum(
                    rate - league - defense.get(opponent, 0.0) for rate, opponent in rows
                ) / len(rows)
        _centre(offense)
        for team in teams:
            rows = conceded[team]
            if rows:
                defense[team] = sum(
                    rate - league - offense.get(opponent, 0.0) for rate, opponent in rows
                ) / len(rows)
        _centre(defense)

    return AdjustedRates(
        league_rate=league,
        offense=offense,
        defense=defense,
        games_by_team={team: len(conceded[team]) for team in teams},
    )


def _centre(effects: dict[UUID, float]) -> None:
    if not effects:
        return
    mean = sum(effects.values()) / len(effects)
    for team in effects:
        effects[team] -= mean


def estimate_matchup(
    team_games: list[TeamGame],
    opponent_games: list[TeamGame],
    *,
    league_pace: float,
    league_allowed_rate: float,
    prop_type: str,
    target_tipoff: datetime,
    is_home: bool,
    opponent_defense_rate: float | None = None,
) -> MatchupEstimate | None:
    """Project pace, defence, rest and blowout risk for one team in one game.

    ``opponent_defense_rate`` is the schedule-adjusted rate the opponent concedes. When it is
    omitted the function falls back to the opponent's raw conceded rate over their last ten
    games, which is what the previous version always did and is retained only so a caller
    without a full league fit still gets an answer.
    """
    team_recent = sorted(team_games, key=lambda game: game.tipoff)[-10:]
    opp_recent = sorted(opponent_games, key=lambda game: game.tipoff)[-10:]
    if len(team_recent) < 5 or len(opp_recent) < 5:
        return None
    expected_pace = (
        sum(game.possessions for game in team_recent) + sum(game.possessions for game in opp_recent)
    ) / (len(team_recent) + len(opp_recent))
    pace_multiplier = max(0.9, min(1.1, expected_pace / max(1.0, league_pace)))
    columns = MARKET_COLUMNS[prop_type]
    if opponent_defense_rate is None:
        allowed_total = sum(sum(game.allowed[column] for column in columns) for game in opp_recent)
        opp_possessions = sum(game.possessions for game in opp_recent)
        opponent_defense_rate = allowed_total / max(1.0, opp_possessions)
    raw_defense = opponent_defense_rate / max(0.01, league_allowed_rate)
    reliability = min(1.0, len(opp_recent) / 20)
    defense_multiplier = 1.0 + (max(0.85, min(1.15, raw_defense)) - 1.0) * reliability
    team_strength = sum(game.point_diff for game in team_recent) / len(team_recent)
    opp_strength = sum(game.point_diff for game in opp_recent) / len(opp_recent)
    margin = max(-50.0, min(50.0, team_strength - opp_strength + (2.0 if is_home else -2.0)))
    blowout = 1.0 / (1.0 + math.exp(-(abs(margin) - 12.0) / 4.5))
    team_rest = min(
        30.0, max(0.0, (target_tipoff - team_recent[-1].tipoff).total_seconds() / 86400)
    )
    opp_rest = min(30.0, max(0.0, (target_tipoff - opp_recent[-1].tipoff).total_seconds() / 86400))
    return MatchupEstimate(
        expected_possessions=expected_pace,
        pace_multiplier=pace_multiplier,
        defense_multiplier=defense_multiplier,
        expected_margin=margin,
        blowout_probability=blowout,
        team_rest_days=team_rest,
        opponent_rest_days=opp_rest,
        opponent_sample=len(opp_recent),
        confidence=min(0.8, reliability * 0.8),
    )


def _load_team_games(rows: list[dict[str, Any]]) -> list[TeamGame]:
    grouped: dict[tuple[UUID, UUID], dict[str, Any]] = {}
    game_meta: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        game_id = UUID(str(row["game_id"]))
        team_id = UUID(str(row["team_id"]))
        game_meta[game_id] = row
        bucket = grouped.setdefault(
            (game_id, team_id),
            dict.fromkeys(STAT_COLUMNS, 0.0),
        )
        for column in STAT_COLUMNS:
            bucket[column] += float(str(row[column]))
        for column in ("field_goals_attempted", "free_throws_attempted", "turnovers"):
            bucket[column] = bucket.get(column, 0.0) + float(str(row[column]))
        bucket["rebounds_offensive_poss"] = bucket.get("rebounds_offensive_poss", 0.0) + float(
            str(row["rebounds_offensive"])
        )
    result: list[TeamGame] = []
    for (game_id, team_id), totals in grouped.items():
        meta = game_meta[game_id]
        home = UUID(str(meta["home_team_id"]))
        away = UUID(str(meta["away_team_id"]))
        opponent = away if team_id == home else home
        opponent_totals = grouped.get((game_id, opponent))
        if opponent_totals is None:
            continue
        possessions = (
            totals["field_goals_attempted"]
            + 0.44 * totals["free_throws_attempted"]
            - totals["rebounds_offensive_poss"]
            + totals["turnovers"]
        )
        result.append(
            TeamGame(
                game_id=game_id,
                team_id=team_id,
                opponent_id=opponent,
                tipoff=meta["scheduled_tipoff"],
                possessions=max(50.0, min(130.0, possessions)),
                point_diff=totals["points"] - opponent_totals["points"],
                totals=totals,
                allowed=opponent_totals,
            )
        )
    return result


def project_matchup_contexts(*, now: datetime | None = None) -> MatchupBatch:
    at = now or datetime.now(UTC)
    projected = unchanged = skipped = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT l.*,g.scheduled_tipoff,g.home_team_id,g.away_team_id
               FROM wnba.player_game_lines l JOIN wnba.games g ON g.game_id=l.game_id
               WHERE l.system_to IS NULL AND g.status='final'"""
        )
        team_games = _load_team_games(cur.fetchall())
        by_team: dict[UUID, list[TeamGame]] = defaultdict(list)
        for team_game in team_games:
            by_team[team_game.team_id].append(team_game)
        league_pace = sum(game.possessions for game in team_games) / max(1, len(team_games))
        # One league-wide fit per market, reused across every target: the adjustment is a
        # property of the season so far, not of the prop being priced.
        adjusted: dict[str, AdjustedRates] = {
            prop_type: opponent_adjusted_rates(team_games, columns)
            for prop_type, columns in MARKET_COLUMNS.items()
        }
        cur.execute(
            """SELECT DISTINCT coalesce(m.to_player_id,q.player_id) AS player_id,
                              q.game_id,q.prop_type,g.scheduled_tipoff,
                              g.home_team_id,g.away_team_id
               FROM wnba.prop_quotes q JOIN wnba.games g ON g.game_id=q.game_id
               LEFT JOIN wnba.player_merges m
                 ON m.from_player_id=q.player_id AND m.system_to IS NULL
               WHERE q.is_available AND q.locks_at>%s AND q.prop_type=ANY(%s)""",
            (at, list(MARKET_COLUMNS)),
        )
        targets = cur.fetchall()
        for target in targets:
            player_id = UUID(str(target["player_id"]))
            cur.execute(
                """SELECT team_id FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL
                   ORDER BY g.scheduled_tipoff DESC LIMIT 1""",
                (player_id,),
            )
            latest = cur.fetchone()
            if latest is None:
                skipped += 1
                continue
            team_id = UUID(str(latest["team_id"]))
            home = UUID(str(target["home_team_id"]))
            away = UUID(str(target["away_team_id"]))
            if team_id not in {home, away}:
                skipped += 1
                continue
            opponent_id = away if team_id == home else home
            prop_type = str(target["prop_type"])
            target_tipoff = target["scheduled_tipoff"]
            if not isinstance(target_tipoff, datetime):
                skipped += 1
                continue
            rates = adjusted[prop_type]
            estimate = estimate_matchup(
                by_team[team_id],
                by_team[opponent_id],
                league_pace=league_pace,
                league_allowed_rate=rates.league_rate,
                prop_type=prop_type,
                target_tipoff=target_tipoff,
                is_home=team_id == home,
                opponent_defense_rate=rates.shrunk_defense_rate(opponent_id),
            )
            if estimate is None:
                skipped += 1
                continue
            cur.execute(
                """SELECT expected_possessions,pace_multiplier,defense_multiplier,
                          expected_margin,blowout_probability,team_rest_days,
                          opponent_rest_days,opponent_sample
                   FROM wnba.matchup_contexts
                   WHERE game_id=%s AND team_id=%s AND prop_type=%s AND system_to IS NULL""",
                (target["game_id"], team_id, prop_type),
            )
            current = cur.fetchone()
            values = (
                estimate.expected_possessions,
                estimate.pace_multiplier,
                estimate.defense_multiplier,
                estimate.expected_margin,
                estimate.blowout_probability,
                estimate.team_rest_days,
                estimate.opponent_rest_days,
                estimate.opponent_sample,
            )
            if current and all(
                abs(float(str(current[key])) - float(value)) < 1e-9
                for key, value in zip(
                    (
                        "expected_possessions",
                        "pace_multiplier",
                        "defense_multiplier",
                        "expected_margin",
                        "blowout_probability",
                        "team_rest_days",
                        "opponent_rest_days",
                        "opponent_sample",
                    ),
                    values,
                    strict=True,
                )
            ):
                unchanged += 1
                continue
            if current:
                cur.execute(
                    """UPDATE wnba.matchup_contexts SET system_to=%s
                       WHERE game_id=%s AND team_id=%s AND prop_type=%s AND system_to IS NULL""",
                    (at, target["game_id"], team_id, prop_type),
                )
            cur.execute(
                """INSERT INTO wnba.matchup_contexts
                   (context_id,game_id,team_id,opponent_team_id,prop_type,
                    expected_possessions,pace_multiplier,defense_multiplier,expected_margin,
                    blowout_probability,team_rest_days,opponent_rest_days,opponent_sample,
                    confidence,method_version,valid_from,system_from)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    target["game_id"],
                    team_id,
                    opponent_id,
                    prop_type,
                    *values,
                    estimate.confidence,
                    METHOD,
                    at,
                    at,
                ),
            )
            projected += 1
    return MatchupBatch(projected, unchanged, skipped)
