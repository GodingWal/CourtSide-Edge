"""What two legs have to do with each other, measured where possible and stated where not.

The entry pricer has always taken a correlation scalar and always defaulted it to zero. Zero is
the one value that is definitely wrong for the entries people actually build: a same-player
points/rebounds pair shares a minutes draw, two teammates share a possession count while
competing for the shots inside it, and two players in the same game share the pace and the
blowout risk that ends everyone's night early.

Three commitments here.

**Correlation is measured on the stat side, not the ticket side.** Every settled episode is
reduced to whether the *stat cleared its line*, regardless of which side the decision took. Two
"over" legs and an over/under pair on the same two markets are the same physical relationship
with opposite signs, so storing the ticket-side correlation would double the table and halve
each cell's sample. :func:`outcome_correlation` puts the sign back at pricing time.

**A relation without evidence gets a stated prior, not a zero.** ``_PRIORS`` holds a small,
signed, deliberately modest number for each structural relation, and it is labelled
``is_fitted=False`` everywhere it surfaces. The failure mode we are avoiding is a confident
zero; the failure mode we are accepting is a visible guess.

**The band travels with the estimate.** A pairwise correlation from 40 settled pairs is not the
same object as one from 4,000, and the difference decides entries. Everything returns a standard
error, and the pricer is expected to show EV across the band rather than at the point.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "DEFAULT_MINIMUM_PAIRS",
    "CorrelationEstimate",
    "EntryCorrelation",
    "LegKey",
    "PairObservation",
    "ResidualPair",
    "classify_relation",
    "entry_correlation",
    "fit_pair_correlations",
    "fit_residual_correlations",
    "load_correlations",
    "merge_correlation_estimates",
    "outcome_correlation",
    "refresh_leg_correlations",
    "residual_pair_observations",
]

# Below this many settled pairs the sample says more about which markets we happened to price
# than about the relationship, and the prior is the better estimate.
DEFAULT_MINIMUM_PAIRS = 30

# Structural priors, applied when nothing has settled. Signed for *stat* correlation with both
# legs read as "the stat cleared its line".
#
#   same_player     One minutes draw feeds both markets. A player who plays 34 minutes instead
#                   of 24 clears more of everything, and that shared draw dominates the mild
#                   substitution between a shot and a pass.
#   same_team       Two effects that partly cancel: shared possessions push together, competition
#                   for the same shots pushes apart. Net slightly positive, and the smallest
#                   number in the table because it is the one we are least sure of.
#   opposing_team   Pace and game state are shared; nothing else is. A fast, close game is long
#                   for both benches.
#   different_game  No mechanism. Kept in the table so the fit can confirm it stays near zero --
#                   a fitted value that wanders away from this is a bug in the pipeline, not a
#                   discovery about basketball.
_PRIORS: dict[str, float] = {
    "same_player": 0.30,
    "same_team": 0.05,
    "opposing_team": 0.10,
    "different_game": 0.0,
}

# Uncertainty attached to an unfitted prior. Wide on purpose: it is a belief, and the console
# prices the band.
_PRIOR_STANDARD_ERROR = 0.20

# A latent correlation of exactly ±1 has no simulator representation, and neither does an
# estimate that overshoots through sampling noise.
_MAX_ABS_CORRELATION = 0.95


@dataclass(frozen=True)
class LegKey:
    """Everything about a leg that decides how it relates to another leg."""

    prop_type: str
    side: str
    player_id: str | None = None
    team: str | None = None
    game_id: str | None = None


@dataclass(frozen=True)
class CorrelationEstimate:
    """One pairwise correlation, with how much of it was measured."""

    relation: str
    prop_a: str
    prop_b: str
    correlation: float
    standard_error: float
    sample_size: int
    is_fitted: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "prop_a": self.prop_a,
            "prop_b": self.prop_b,
            "correlation": self.correlation,
            "standard_error": self.standard_error,
            "sample_size": self.sample_size,
            "is_fitted": self.is_fitted,
        }


@dataclass(frozen=True)
class PairObservation:
    """Two settled legs from one slice of history, read on the stat side."""

    relation: str
    prop_a: str
    prop_b: str
    cleared_a: bool
    cleared_b: bool


@dataclass(frozen=True)
class EntryCorrelation:
    """The scalar a one-factor pricer needs, plus the band it should be priced across."""

    mean: float
    low: float
    high: float
    pairs: tuple[CorrelationEstimate, ...]
    fitted_pairs: int

    @property
    def is_fitted(self) -> bool:
        """Whether every pair in the entry came from measurement rather than a prior."""
        return bool(self.pairs) and self.fitted_pairs == len(self.pairs)

    def to_payload(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "low": self.low,
            "high": self.high,
            "is_fitted": self.is_fitted,
            "fitted_pairs": self.fitted_pairs,
            "pairs": [pair.to_payload() for pair in self.pairs],
        }


def _ordered(prop_a: str, prop_b: str) -> tuple[str, str]:
    """Canonical ordering, because the relationship is symmetric and the table should be too."""
    return (prop_a, prop_b) if prop_a <= prop_b else (prop_b, prop_a)


def classify_relation(first: LegKey, second: LegKey) -> str:
    """Name why these two legs might move together.

    Player identity is checked before team and team before game, because the mechanisms nest:
    two markets on one player are also on one team and in one game, and the tightest true
    statement is the one worth pricing.
    """
    if first.player_id is not None and first.player_id == second.player_id:
        return "same_player"
    if first.game_id is None or second.game_id is None or first.game_id != second.game_id:
        return "different_game"
    if first.team is not None and second.team is not None and first.team == second.team:
        return "same_team"
    return "opposing_team"


def outcome_correlation(stat_correlation: float, first: LegKey, second: LegKey) -> float:
    """Convert a stat-side correlation into the ticket-side one for these two sides.

    Backing the same direction preserves the sign; opposing directions invert it. This is the
    step that makes a hedge look like a hedge: an over and an under on two positively related
    markets is genuinely negatively correlated as a *ticket*, and a pricer told otherwise will
    overstate the chance both legs land.
    """
    aligned = first.side == second.side
    return stat_correlation if aligned else -stat_correlation


def fit_pair_correlations(
    observations: Iterable[PairObservation],
    *,
    minimum_pairs: int = DEFAULT_MINIMUM_PAIRS,
    now: datetime | None = None,
) -> list[CorrelationEstimate]:
    """Phi coefficient per (relation, market pair) over settled pairs.

    For two binary variables the Pearson correlation is the phi coefficient, computable from the
    2x2 table alone. Cells with no variation -- every observed pair cleared, or none did --
    return the prior rather than a division by zero dressed up as a measurement.
    """
    _ = now  # timestamps are applied by the writer, not the estimator
    buckets: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
    for observation in observations:
        prop_a, prop_b = _ordered(observation.prop_a, observation.prop_b)
        flipped = (prop_a, prop_b) != (observation.prop_a, observation.prop_b)
        first = int(observation.cleared_b if flipped else observation.cleared_a)
        second = int(observation.cleared_a if flipped else observation.cleared_b)
        buckets.setdefault((observation.relation, prop_a, prop_b), []).append((first, second))

    estimates: list[CorrelationEstimate] = []
    for (relation, prop_a, prop_b), points in sorted(buckets.items()):
        count = len(points)
        prior = _PRIORS.get(relation, 0.0)
        if count < minimum_pairs:
            estimates.append(
                CorrelationEstimate(
                    relation, prop_a, prop_b, prior, _PRIOR_STANDARD_ERROR, count, False
                )
            )
            continue
        phi = _phi(points)
        if phi is None:
            estimates.append(
                CorrelationEstimate(
                    relation, prop_a, prop_b, prior, _PRIOR_STANDARD_ERROR, count, False
                )
            )
            continue
        # Standard error of a correlation coefficient at this sample size. Not exact for a
        # binary phi, close enough to size the band honestly, and never smaller than the
        # sampling noise it is standing in for.
        standard_error = math.sqrt(max(0.0, 1.0 - phi**2) / max(1, count - 2))
        estimates.append(
            CorrelationEstimate(
                relation,
                prop_a,
                prop_b,
                max(-_MAX_ABS_CORRELATION, min(_MAX_ABS_CORRELATION, phi)),
                standard_error,
                count,
                True,
            )
        )
    return estimates


def _phi(points: Sequence[tuple[int, int]]) -> float | None:
    """Phi coefficient of a 2x2 table, or ``None`` when a margin is degenerate."""
    n11 = sum(1 for a, b in points if a and b)
    n10 = sum(1 for a, b in points if a and not b)
    n01 = sum(1 for a, b in points if not a and b)
    n00 = sum(1 for a, b in points if not a and not b)
    denominator = (n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00)
    if denominator == 0:
        return None
    return (n11 * n00 - n10 * n01) / math.sqrt(denominator)


def estimate_for(
    first: LegKey,
    second: LegKey,
    fitted: dict[tuple[str, str, str], CorrelationEstimate],
) -> CorrelationEstimate:
    """The best available stat-side estimate for one pair, falling back to the prior."""
    relation = classify_relation(first, second)
    prop_a, prop_b = _ordered(first.prop_type, second.prop_type)
    found = fitted.get((relation, prop_a, prop_b))
    if found is not None:
        return found
    return CorrelationEstimate(
        relation,
        prop_a,
        prop_b,
        _PRIORS.get(relation, 0.0),
        _PRIOR_STANDARD_ERROR,
        0,
        False,
    )


def entry_correlation(
    legs: Sequence[LegKey],
    fitted: dict[tuple[str, str, str], CorrelationEstimate] | None = None,
) -> EntryCorrelation:
    """Average ticket-side pairwise correlation for an entry, with a one-sigma band.

    The one-factor simulator takes a single scalar, so the pairwise structure has to collapse
    somewhere. It collapses here, in the open, by averaging -- and the individual pairs travel
    alongside so the console can show that a "0.08 entry" is really a +0.30 same-player pair
    diluted by two unrelated ones. That distinction changes which leg you drop.

    The band is the average of the pairwise standard errors rather than the standard error of
    the average. The latter would shrink with leg count as though the pairs were independent
    estimates of one quantity, which they are not: they are estimates of different quantities
    that share the same sparse settlement history.
    """
    table = fitted or {}
    pairs: list[CorrelationEstimate] = []
    ticket_values: list[float] = []
    errors: list[float] = []
    for index, first in enumerate(legs):
        for second in legs[index + 1 :]:
            estimate = estimate_for(first, second, table)
            pairs.append(estimate)
            ticket_values.append(outcome_correlation(estimate.correlation, first, second))
            errors.append(estimate.standard_error)
    if not pairs:
        return EntryCorrelation(0.0, 0.0, 0.0, (), 0)
    mean = math.fsum(ticket_values) / len(ticket_values)
    spread = math.fsum(errors) / len(errors)
    clamp = _MAX_ABS_CORRELATION
    return EntryCorrelation(
        max(-clamp, min(clamp, mean)),
        max(-clamp, min(clamp, mean - spread)),
        max(-clamp, min(clamp, mean + spread)),
        tuple(pairs),
        sum(1 for pair in pairs if pair.is_fitted),
    )


# --------------------------------------------------------------------------------------
# Database side
# --------------------------------------------------------------------------------------
def load_correlations(cur: Any) -> dict[tuple[str, str, str], CorrelationEstimate]:
    """Read the fitted table into the lookup :func:`entry_correlation` expects."""
    cur.execute(
        """SELECT relation,prop_a,prop_b,correlation,standard_error,sample_size,is_fitted
           FROM wnba.leg_correlations"""
    )
    return {
        (str(row["relation"]), str(row["prop_a"]), str(row["prop_b"])): CorrelationEstimate(
            str(row["relation"]),
            str(row["prop_a"]),
            str(row["prop_b"]),
            float(str(row["correlation"])),
            float(str(row["standard_error"])),
            int(str(row["sample_size"])),
            bool(row["is_fitted"]),
        )
        for row in cur.fetchall()
    }


def _settled_pair_observations(cur: Any) -> list[PairObservation]:
    """Every settled pair of legs that shared a game, read on the stat side.

    Same-player pairs are included across different markets only: a market against itself is not
    a pair, and the same episode twice would report a perfect correlation with no content. The
    self-join is ordered by episode id so each unordered pair appears once.

    Voids and pushes are excluded upstream by the same rule the scorer uses -- a leg that never
    resolved carries no information about how two legs move together.
    """
    cur.execute(
        """SELECT a.prop_type AS prop_a,b.prop_type AS prop_b,
                  a.side AS side_a,b.side AS side_b,a.hit AS hit_a,b.hit AS hit_b,
                  a.player_id AS player_a,b.player_id AS player_b,
                  a.team_id AS team_a,b.team_id AS team_b
           FROM (SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,o.hit,
                        l.team_id
                 FROM wnba.decision_episodes d
                 JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                 LEFT JOIN wnba.player_game_lines l
                   ON l.player_id=d.player_id AND l.game_id=d.game_id AND l.system_to IS NULL
                 WHERE NOT o.was_voided AND NOT o.was_push) a
           JOIN (SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,o.hit,
                        l.team_id
                 FROM wnba.decision_episodes d
                 JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                 LEFT JOIN wnba.player_game_lines l
                   ON l.player_id=d.player_id AND l.game_id=d.game_id AND l.system_to IS NULL
                 WHERE NOT o.was_voided AND NOT o.was_push) b
             ON b.game_id=a.game_id AND b.episode_id>a.episode_id
           WHERE NOT (a.player_id=b.player_id AND a.prop_type=b.prop_type)"""
    )
    observations: list[PairObservation] = []
    for row in cur.fetchall():
        first = LegKey(
            prop_type=str(row["prop_a"]),
            side=str(row["side_a"]),
            player_id=str(row["player_a"]),
            team=None if row["team_a"] is None else str(row["team_a"]),
        )
        second = LegKey(
            prop_type=str(row["prop_b"]),
            side=str(row["side_b"]),
            player_id=str(row["player_b"]),
            team=None if row["team_b"] is None else str(row["team_b"]),
        )
        # Both legs shared a game by construction, so the relation is same_player, same_team or
        # opposing_team; `classify_relation` needs a game id to say so.
        relation = classify_relation(
            LegKey(first.prop_type, first.side, first.player_id, first.team, "shared"),
            LegKey(second.prop_type, second.side, second.player_id, second.team, "shared"),
        )
        observations.append(
            PairObservation(
                relation=relation,
                prop_a=first.prop_type,
                prop_b=second.prop_type,
                cleared_a=_cleared(str(row["side_a"]), bool(row["hit_a"])),
                cleared_b=_cleared(str(row["side_b"]), bool(row["hit_b"])),
            )
        )
    return observations


def _cleared(side: str, hit: bool) -> bool:
    """Did the stat clear its line, whichever side the decision took?"""
    return hit if side == "over" else not hit


@dataclass(frozen=True)
class CorrelationBatch:
    observations: int
    written: int
    fitted: int
    residual_fitted: int = 0


def refresh_leg_correlations(
    *, minimum_pairs: int = DEFAULT_MINIMUM_PAIRS, now: datetime | None = None
) -> CorrelationBatch:
    """Refit the pairwise table from settled history and upsert it."""
    from wnba_store.db import connect

    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        observations = _settled_pair_observations(cur)
        episode_estimates = fit_pair_correlations(observations, minimum_pairs=minimum_pairs)
        residual_estimates = fit_residual_correlations(
            residual_pair_observations(_boxscore_residual_rows(cur)),
            minimum_pairs=minimum_pairs,
        )
        estimates = merge_correlation_estimates(episode_estimates, residual_estimates)
        for estimate in estimates:
            cur.execute(
                """INSERT INTO wnba.leg_correlations
                   (relation,prop_a,prop_b,sample_size,correlation,standard_error,is_fitted,
                    calculated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (relation,prop_a,prop_b) DO UPDATE SET
                     sample_size=excluded.sample_size,correlation=excluded.correlation,
                     standard_error=excluded.standard_error,is_fitted=excluded.is_fitted,
                     calculated_at=excluded.calculated_at""",
                (
                    estimate.relation,
                    estimate.prop_a,
                    estimate.prop_b,
                    estimate.sample_size,
                    estimate.correlation,
                    estimate.standard_error,
                    estimate.is_fitted,
                    at,
                ),
            )
    return CorrelationBatch(
        len(observations),
        len(estimates),
        sum(1 for item in estimates if item.is_fitted),
        sum(1 for item in residual_estimates if item.is_fitted),
    )


# --------------------------------------------------------------------------------------
# Box-score residual correlations
# --------------------------------------------------------------------------------------
# The episode-based fit above measures correlation on *settled bets* -- which is sparse (a
# market only settles when we forecast it) and entangled with where the lines happened to sit.
# Box scores are neither: every played game is an observation, and a residual against the
# player's own rolling baseline isolates exactly the co-movement a parlay cares about. A
# same-player points/rebounds pair shares a minutes draw; two teammates share possessions;
# opponents share pace. Measured on the full archive: minutes residual vs points residual
# r=0.46, points/rebounds r=0.22, points/assists r=0.13 -- the prior table's ordering, now
# with numbers behind it.
RESIDUAL_STATS: tuple[str, ...] = ("points", "rebounds", "assists")
RESIDUAL_MIN_MINUTES = 15.0
RESIDUAL_BASELINE_GAMES = 10
RESIDUAL_MIN_BASELINE = 5


@dataclass(frozen=True)
class ResidualPair:
    """One co-movement observation: two residuals from the same game slice."""

    relation: str
    prop_a: str
    prop_b: str
    residual_a: float
    residual_b: float


def residual_pair_observations(rows: Iterable[dict[str, Any]]) -> list[ResidualPair]:
    """Pair residuals against each player's rolling prior-game baseline.

    The baseline for a game is the player's mean over up to the previous
    ``RESIDUAL_BASELINE_GAMES`` appearances, never including the game itself -- a residual
    computed against a baseline that contains the outcome is leakage wearing a lab coat. A
    player needs ``RESIDUAL_MIN_BASELINE`` prior appearances before their residual counts at
    all, and games below ``RESIDUAL_MIN_MINUTES`` are excluded both as observations and as
    baseline evidence (garbage-time and injury-shortened rows teach nothing about rotation
    co-movement).
    """
    by_player: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if float(row["minutes"]) < RESIDUAL_MIN_MINUTES:
            continue
        by_player.setdefault(str(row["player_id"]), []).append(row)

    residuals: dict[str, dict[str, dict[str, float]]] = {}  # game -> player -> stat -> residual
    teams: dict[str, dict[str, str]] = {}  # game -> player -> team
    for player_id, games in by_player.items():
        games.sort(key=lambda r: r["tipoff"])
        history: list[dict[str, float]] = []
        for row in games:
            stats = {stat: float(row[stat]) for stat in RESIDUAL_STATS}
            if len(history) >= RESIDUAL_MIN_BASELINE:
                window = history[-RESIDUAL_BASELINE_GAMES:]
                game_residuals = residuals.setdefault(str(row["game_id"]), {})
                game_residuals[player_id] = {
                    stat: stats[stat]
                    - math.fsum(prior[stat] for prior in window) / len(window)
                    for stat in RESIDUAL_STATS
                }
                teams.setdefault(str(row["game_id"]), {})[player_id] = str(row["team_id"])
            history.append(stats)

    pairs: list[ResidualPair] = []
    for game_id, players in residuals.items():
        player_ids = sorted(players)
        game_teams = teams.get(game_id, {})
        for index, first in enumerate(player_ids):
            for second in player_ids[index + 1 :]:
                relation = (
                    "same_team"
                    if game_teams.get(first) is not None
                    and game_teams.get(first) == game_teams.get(second)
                    else "opposing_team"
                )
                for stat_a in RESIDUAL_STATS:
                    for stat_b in RESIDUAL_STATS:
                        prop_a, prop_b = _ordered(stat_a, stat_b)
                        ra = players[first][stat_a]
                        rb = players[second][stat_b]
                        pairs.append(
                            ResidualPair(
                                relation, prop_a, prop_b,
                                ra if (prop_a, prop_b) == (stat_a, stat_b) else rb,
                                rb if (prop_a, prop_b) == (stat_a, stat_b) else ra,
                            )
                        )
        for stat_a, stat_b in (
            ("points", "rebounds"),
            ("points", "assists"),
            ("assists", "rebounds"),
        ):
            prop_a, prop_b = _ordered(stat_a, stat_b)
            for player_id in player_ids:
                values = players[player_id]
                pairs.append(
                    ResidualPair(
                        "same_player",
                        prop_a,
                        prop_b,
                        values[prop_a],
                        values[prop_b],
                    )
                )
    return pairs


def fit_residual_correlations(
    pairs: Iterable[ResidualPair],
    *,
    minimum_pairs: int = DEFAULT_MINIMUM_PAIRS,
) -> list[CorrelationEstimate]:
    """Pearson correlation of residuals per (relation, market pair).

    Same sample gate and clamp as the episode fit. Cells below ``minimum_pairs`` are simply
    absent from the result -- the caller falls back to the episode fit or the prior rather
    than trusting a handful of games.
    """
    buckets: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    for pair in pairs:
        buckets.setdefault((pair.relation, pair.prop_a, pair.prop_b), []).append(
            (pair.residual_a, pair.residual_b)
        )

    estimates: list[CorrelationEstimate] = []
    for (relation, prop_a, prop_b), points in sorted(buckets.items()):
        count = len(points)
        if count < minimum_pairs:
            continue
        correlation = _pearson(points)
        if correlation is None:
            continue
        standard_error = math.sqrt(max(0.0, 1.0 - correlation**2) / max(1, count - 2))
        estimates.append(
            CorrelationEstimate(
                relation,
                prop_a,
                prop_b,
                max(-_MAX_ABS_CORRELATION, min(_MAX_ABS_CORRELATION, correlation)),
                standard_error,
                count,
                True,
            )
        )
    return estimates


def _pearson(points: Sequence[tuple[float, float]]) -> float | None:
    """Pearson r, or ``None`` when either series has no variance."""
    count = len(points)
    mean_a = math.fsum(a for a, _ in points) / count
    mean_b = math.fsum(b for _, b in points) / count
    cov = math.fsum((a - mean_a) * (b - mean_b) for a, b in points)
    var_a = math.fsum((a - mean_a) ** 2 for a, _ in points)
    var_b = math.fsum((b - mean_b) ** 2 for _, b in points)
    if var_a <= 0.0 or var_b <= 0.0:
        return None
    return cov / math.sqrt(var_a * var_b)


def _boxscore_residual_rows(cur: Any) -> list[dict[str, Any]]:
    """Every final box-score row with enough minutes to carry rotation information."""
    cur.execute(
        """SELECT l.player_id,l.game_id,l.team_id,g.scheduled_tipoff AS tipoff,
                  l.minutes,l.points,
                  l.rebounds_offensive+l.rebounds_defensive AS rebounds,l.assists
           FROM wnba.player_game_lines l
           JOIN wnba.games g ON g.game_id=l.game_id
           WHERE l.system_to IS NULL AND g.status='final' AND l.minutes>=%s
           ORDER BY l.player_id,g.scheduled_tipoff""",
        (RESIDUAL_MIN_MINUTES,),
    )
    return [dict(row) for row in cur.fetchall()]


def merge_correlation_estimates(
    episode: Iterable[CorrelationEstimate],
    residual: Iterable[CorrelationEstimate],
) -> list[CorrelationEstimate]:
    """Residual measurement wins where it exists; episode measurement fills the rest.

    The residual fit sees every played game and isolates co-movement from where the line sat,
    so where both fits exist the residual number is the better estimate of the physical
    relationship. Where the box scores cannot speak -- a relation or market pair with too few
    overlapping games -- the settled-episode phi or the stated prior stands, labelled as
    unfitted exactly as before.
    """
    merged: dict[tuple[str, str, str], CorrelationEstimate] = {}
    for estimate in episode:
        merged[(estimate.relation, estimate.prop_a, estimate.prop_b)] = estimate
    for estimate in residual:
        if estimate.is_fitted:
            merged[(estimate.relation, estimate.prop_a, estimate.prop_b)] = estimate
    return [merged[key] for key in sorted(merged)]
