"""Production performance validation, computed from the live record rather than the replay.

The readiness gates have always read ``backtest_results`` -- historical replay over rescued
markets. That is the right evidence for "does the model beat minutes x rate out of sample", and
it is the wrong evidence for every gate that asks what happened when the system ran for real.
Six gates were therefore permanently ``pending`` with prose explaining that the evidence did not
exist, and they would have stayed ``pending`` after five hundred live recommendations arrived,
because nothing in the path ever looked at a settled episode.

This module looks at settled episodes. Each requirement becomes a computed number with a stated
threshold, so the gates flip when the evidence arrives instead of when somebody remembers to
rewrite the prose.

WHAT COUNTS AS ONE RECOMMENDATION
---------------------------------
One market -- one player, one game, one prop type -- resolves exactly once, however many times
the board was re-scored. The count that matters is independent markets, further corrected for
the fact that markets inside one game share that game's state. "500 out-of-sample
recommendations" is a claim about 500 *events*, and the polling schedule must not be able to
manufacture them.

WHAT MAKES A WEEKLY WINDOW STABLE
---------------------------------
Not merely that a calendar week contains rows. A window with four decisions is a week the system
happened to be quiet, and eight of those are not eight windows of evidence. A window counts when
it carries enough independent markets to say anything, and the gate reads the number of
qualifying windows rather than the number of distinct dates.

WHY RETURNS ARE COMPUTED TWICE
------------------------------
Once at the operator's real payout structure and once at the flat break-even the decision gate
used. The two disagree whenever the payout table is non-linear, and reporting only the flat
version is how a system convinces itself a 54% hit rate is profitable on a product that needs
57.7%.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

from wnba_services.learning_loop.evaluation import expected_calibration_error
from wnba_services.learning_loop.independence import (
    dedupe_latest_per_market,
    summarise_sample,
)

__all__ = [
    "CALIBRATION_THRESHOLD",
    "MINIMUM_INDEPENDENT_RECOMMENDATIONS",
    "MINIMUM_STABLE_WEEKS",
    "MINIMUM_WEEK_MARKETS",
    "LiveValidation",
    "SubgroupResult",
    "WeeklyWindow",
    "drawdown_series",
    "load_settled_recommendations",
    "validate_live_performance",
]

# The roadmap's gate, in independent markets.
MINIMUM_INDEPENDENT_RECOMMENDATIONS = 500

# Eight *stable* windows. A week with four decisions is a quiet week, not a window of evidence.
MINIMUM_STABLE_WEEKS = 8
MINIMUM_WEEK_MARKETS = 15

# Expected calibration error the platform is prepared to call calibrated.
CALIBRATION_THRESHOLD = 0.08

# A subgroup needs this many independent markets before its result is allowed to say anything.
MINIMUM_SUBGROUP_MARKETS = 30

# How far a subgroup's calibration may drift past the global threshold before it is degradation
# rather than noise.
SUBGROUP_TOLERANCE = 0.04

_LINE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("line_lt_6", 0.0, 6.0),
    ("line_6_to_12", 6.0, 12.0),
    ("line_12_to_20", 12.0, 20.0),
    ("line_gte_20", 20.0, math.inf),
)


def _line_bucket(line: float) -> str:
    for name, low, high in _LINE_BUCKETS:
        if low <= line < high:
            return name
    return "line_gte_20"


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


@dataclass(frozen=True)
class WeeklyWindow:
    week: str
    markets: int
    hit_rate: float
    calibration_error: float | None
    mean_line_value: float | None

    @property
    def is_stable(self) -> bool:
        return self.markets >= MINIMUM_WEEK_MARKETS

    def to_payload(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "markets": self.markets,
            "hit_rate": round(self.hit_rate, 4),
            "calibration_error": (
                None if self.calibration_error is None else round(self.calibration_error, 4)
            ),
            "mean_line_value": (
                None if self.mean_line_value is None else round(self.mean_line_value, 4)
            ),
            "is_stable": self.is_stable,
        }


@dataclass(frozen=True)
class SubgroupResult:
    dimension: str
    value: str
    markets: int
    hit_rate: float
    calibration_error: float | None

    @property
    def is_conclusive(self) -> bool:
        return self.markets >= MINIMUM_SUBGROUP_MARKETS

    @property
    def is_degraded(self) -> bool:
        """Materially worse calibration than the platform's own threshold, on enough sample."""
        if not self.is_conclusive or self.calibration_error is None:
            return False
        return self.calibration_error > CALIBRATION_THRESHOLD + SUBGROUP_TOLERANCE

    def to_payload(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "markets": self.markets,
            "hit_rate": round(self.hit_rate, 4),
            "calibration_error": (
                None if self.calibration_error is None else round(self.calibration_error, 4)
            ),
            "conclusive": self.is_conclusive,
            "degraded": self.is_degraded,
        }


@dataclass(frozen=True)
class LiveValidation:
    """Everything the production gates need, measured from settled live decisions."""

    rows: int
    independent_markets: int
    effective_sample: float
    games: int
    recommendations: int
    calibration_error: float | None
    brier: float | None
    log_loss: float | None
    hit_rate: float | None
    mean_line_value: float | None
    line_value_sample: int
    positive_line_value_share: float | None
    payout_return: float | None
    flat_return: float | None
    breakeven: float | None
    max_drawdown: float | None
    max_drawdown_units: float | None
    peak_exposure: float | None
    mean_daily_exposure: float | None
    voids: int
    pushes: int
    late_line_moves: int
    weekly: tuple[WeeklyWindow, ...] = ()
    subgroups: tuple[SubgroupResult, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def stable_weeks(self) -> int:
        return sum(1 for window in self.weekly if window.is_stable)

    @property
    def degraded_subgroups(self) -> tuple[SubgroupResult, ...]:
        return tuple(group for group in self.subgroups if group.is_degraded)

    @property
    def has_priced_evidence(self) -> bool:
        """Whether returns were computed from real prices rather than assumed ones."""
        return self.payout_return is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "independent_markets": self.independent_markets,
            "effective_sample": round(self.effective_sample, 2),
            "games": self.games,
            "recommendations": self.recommendations,
            "calibration_error": (
                None if self.calibration_error is None else round(self.calibration_error, 4)
            ),
            "brier": None if self.brier is None else round(self.brier, 4),
            "log_loss": None if self.log_loss is None else round(self.log_loss, 4),
            "hit_rate": None if self.hit_rate is None else round(self.hit_rate, 4),
            "mean_line_value": (
                None if self.mean_line_value is None else round(self.mean_line_value, 4)
            ),
            "line_value_sample": self.line_value_sample,
            "positive_line_value_share": (
                None
                if self.positive_line_value_share is None
                else round(self.positive_line_value_share, 4)
            ),
            "payout_return": None if self.payout_return is None else round(self.payout_return, 4),
            "flat_return": None if self.flat_return is None else round(self.flat_return, 4),
            "breakeven": None if self.breakeven is None else round(self.breakeven, 4),
            "max_drawdown": None if self.max_drawdown is None else round(self.max_drawdown, 4),
            "max_drawdown_units": (
                None if self.max_drawdown_units is None else round(self.max_drawdown_units, 3)
            ),
            "peak_exposure": None if self.peak_exposure is None else round(self.peak_exposure, 3),
            "mean_daily_exposure": (
                None if self.mean_daily_exposure is None else round(self.mean_daily_exposure, 3)
            ),
            "voids": self.voids,
            "pushes": self.pushes,
            "late_line_moves": self.late_line_moves,
            "stable_weeks": self.stable_weeks,
            "weekly": [window.to_payload() for window in self.weekly],
            "subgroups": [group.to_payload() for group in self.subgroups],
            "degraded_subgroups": [group.to_payload() for group in self.degraded_subgroups],
            "notes": list(self.notes),
            "evidence_class": "live_settled",
        }


def drawdown_series(returns: Sequence[float]) -> tuple[float, float]:
    """``(max drawdown as a fraction of peak, max drawdown in units)`` over an equity curve.

    Computed on the cumulative sum of per-decision returns in settlement order. Reported in both
    forms deliberately: a fraction is comparable across bankroll sizes and a unit count is what an
    analyst actually feels, and the two diverge badly when the curve spends time near zero.
    """
    if not returns:
        return 0.0, 0.0
    equity = 0.0
    peak = 0.0
    worst_fraction = 0.0
    worst_units = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drop = peak - equity
        worst_units = max(worst_units, drop)
        if peak > 0:
            worst_fraction = max(worst_fraction, drop / peak)
    return worst_fraction, worst_units


def _payout_multiple(row: Mapping[str, Any]) -> float | None:
    """Net return per unit staked for one settled leg, from the recorded price.

    Returns ``None`` when the price is unusable rather than assuming even money. The legacy
    import truncated decimal odds to integers, and quietly substituting 2.0 for a missing price
    is exactly how an unprofitable record turns into a profitable one on paper.
    """
    multiplier = row.get("multiplier")
    if multiplier is None:
        return None
    try:
        value = float(str(multiplier))
    except (TypeError, ValueError):
        return None
    # A multiplier of exactly 1 is the schema default, not a priced market.
    if value <= 1.0:
        return None
    return value - 1.0


def load_settled_recommendations(cur: Any, *, limit: int = 20000) -> list[dict[str, Any]]:
    """Every settled live decision, with the outcome, the closing line and the price.

    Voids and pushes are *included* here and filtered per-metric, because "how many voids has the
    system handled" is one of the requirements and a query that drops them can never answer it.
    """
    cur.execute(
        """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,d.line,
                  d.predicted_probability,d.shrunk_probability,d.breakeven_probability,
                  d.system_recommendation,d.forecast_timestamp,d.multiplier,d.source,
                  d.projected_mean,
                  o.hit,o.was_voided,o.was_push,o.closing_line,o.closing_multiplier,
                  o.actual_stat,o.settled_at,
                  g.scheduled_tipoff,
                  t.abbreviation AS team,
                  date_trunc('week',d.forecast_timestamp)::date::text AS forecast_week
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           JOIN wnba.games g ON g.game_id=d.game_id
           LEFT JOIN LATERAL (
               SELECT tm.abbreviation FROM wnba.player_game_lines l
               JOIN wnba.teams tm ON tm.team_id=l.team_id
               WHERE l.player_id=d.player_id AND l.game_id=d.game_id AND l.system_to IS NULL
               LIMIT 1
           ) t ON true
           ORDER BY o.settled_at
           LIMIT %s""",
        (max(1, limit),),
    )
    return [dict(row) for row in cur.fetchall()]


def _scored_pairs(rows: Sequence[Mapping[str, Any]]) -> list[tuple[float, bool]]:
    """Stated probability of the side taken, against whether that side landed."""
    return [
        (float(str(row["predicted_probability"])), bool(row["hit"]))
        for row in rows
        if row["predicted_probability"] is not None
    ]


def _weekly_windows(rows: Sequence[Mapping[str, Any]]) -> tuple[WeeklyWindow, ...]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["forecast_week"]), []).append(row)
    windows = []
    for week, members in sorted(grouped.items()):
        pairs = _scored_pairs(members)
        hits = [float(bool(row["hit"])) for row in members]
        values = _line_values(members)
        windows.append(
            WeeklyWindow(
                week=week,
                markets=len(members),
                hit_rate=math.fsum(hits) / len(hits) if hits else 0.0,
                calibration_error=expected_calibration_error(pairs),
                mean_line_value=_mean(values),
            )
        )
    return tuple(windows)


def _line_values(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    """Closing-line value in points of line, signed so positive is always favourable.

    Taking the over at 14.5 that closes at 15.5 is +1: the market moved toward the position after
    it was taken. Taking the under at the same line is -1 by the same move.
    """
    values: list[float] = []
    for row in rows:
        closing = row.get("closing_line")
        if closing is None or row.get("line") is None:
            continue
        direction = 1.0 if str(row["side"]) == "over" else -1.0
        values.append(direction * (float(str(closing)) - float(str(row["line"]))))
    return values


def _subgroups(rows: Sequence[Mapping[str, Any]]) -> tuple[SubgroupResult, ...]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(("market", str(row["prop_type"])), []).append(row)
        groups.setdefault(("line_range", _line_bucket(float(str(row["line"])))), []).append(row)
        groups.setdefault(("player", str(row["player_id"])), []).append(row)
        team = row.get("team")
        if team is not None:
            groups.setdefault(("team", str(team)), []).append(row)

    results = []
    for (dimension, value), members in sorted(groups.items()):
        pairs = _scored_pairs(members)
        hits = [float(bool(row["hit"])) for row in members]
        results.append(
            SubgroupResult(
                dimension=dimension,
                value=value,
                markets=len(members),
                hit_rate=math.fsum(hits) / len(hits) if hits else 0.0,
                calibration_error=expected_calibration_error(pairs),
            )
        )
    return tuple(results)


def validate_live_performance(*, now: datetime | None = None) -> LiveValidation:
    """Measure every production-validation requirement against the settled live record."""
    _ = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        raw = load_settled_recommendations(cur)
    return summarise_live_performance(raw)


def summarise_live_performance(raw: Sequence[Mapping[str, Any]]) -> LiveValidation:
    """Pure core, so every threshold in this file is testable without a database."""
    notes: list[str] = []
    voids = sum(1 for row in raw if bool(row["was_voided"]))
    pushes = sum(1 for row in raw if bool(row["was_push"]))

    # Voids and pushes are handled events, not scored ones. A void is the market vanishing; a
    # push is a tie. Neither is a forecast that was right or wrong.
    scoreable = [row for row in raw if not bool(row["was_voided"]) and not bool(row["was_push"])]
    # Only decisions the system was prepared to act on are recommendations. Scoring the whole
    # board would measure the model; this gate is about the recommendations.
    acted = [row for row in scoreable if str(row["system_recommendation"]) == "candidate"]

    shape = summarise_sample(acted)
    deduped = dedupe_latest_per_market(list(acted))
    pairs = _scored_pairs(deduped)
    hits = [float(bool(row["hit"])) for row in deduped]

    values = _line_values(deduped)
    positive_share = (
        math.fsum(1.0 for value in values if value > 0) / len(values) if values else None
    )
    if not values and deduped:
        notes.append(
            "No settled recommendation carries a closing line, so closing-line value cannot be "
            "computed at all. A second market source is what makes a closing line meaningful."
        )

    # Returns at the real payout structure. Unpriced legs are excluded rather than assumed even
    # money, and the count of excluded legs is reported so the gap is visible.
    priced = [(row, _payout_multiple(row)) for row in deduped]
    usable = [(row, multiple) for row, multiple in priced if multiple is not None]
    payout_return: float | None = None
    flat_return: float | None = None
    returns: list[float] = []
    if usable:
        returns = [(multiple if bool(row["hit"]) else -1.0) for row, multiple in usable]
        payout_return = math.fsum(returns) / len(returns)
        if len(usable) < len(deduped):
            notes.append(
                f"{len(deduped) - len(usable)} of {len(deduped)} settled recommendations carry no "
                "usable price and are excluded from the priced return rather than assumed to be "
                "even money."
            )
    else:
        if deduped:
            notes.append(
                "No settled recommendation carries a usable price, so return after real pricing "
                "cannot be computed. Legacy decimal odds were truncated to integers on import."
            )

    breakevens = [
        float(str(row["breakeven_probability"]))
        for row in deduped
        if row.get("breakeven_probability") is not None
    ]
    breakeven = _mean(breakevens)
    if hits and breakeven is not None:
        # The flat comparison: hit rate against the per-leg rate the payout table demanded.
        flat_return = (math.fsum(hits) / len(hits)) - breakeven

    max_drawdown, max_drawdown_units = drawdown_series(returns)

    # Exposure: how many open recommendations shared one day, and one game. Paper exposure, but
    # the shape of it is what a staking policy would have to survive.
    per_day: dict[str, int] = {}
    per_game: dict[str, int] = {}
    for row in deduped:
        tip = row.get("scheduled_tipoff")
        day = str(tip)[:10] if tip is not None else "unknown"
        per_day[day] = per_day.get(day, 0) + 1
        per_game[str(row["game_id"])] = per_game.get(str(row["game_id"]), 0) + 1
    peak_exposure = float(max(per_day.values())) if per_day else None
    mean_daily = _mean([float(value) for value in per_day.values()])

    # Late line movement: the forecast line differing from the closing line at all. The count is
    # the observation the void/scratch/late-news gate needs alongside the void count.
    late_moves = sum(
        1
        for row in deduped
        if row.get("closing_line") is not None
        and float(str(row["closing_line"])) != float(str(row["line"]))
    )

    return LiveValidation(
        rows=len(raw),
        independent_markets=shape.markets,
        effective_sample=shape.effective,
        games=shape.games,
        recommendations=len(deduped),
        calibration_error=expected_calibration_error(pairs),
        brier=_mean([brier_score(p, int(h)) for p, h in pairs]),
        log_loss=_mean([log_loss(p, int(h)) for p, h in pairs]),
        hit_rate=_mean(hits),
        mean_line_value=_mean(values),
        line_value_sample=len(values),
        positive_line_value_share=positive_share,
        payout_return=payout_return,
        flat_return=flat_return,
        breakeven=breakeven,
        max_drawdown=max_drawdown if returns else None,
        max_drawdown_units=max_drawdown_units if returns else None,
        peak_exposure=peak_exposure,
        mean_daily_exposure=mean_daily,
        voids=voids,
        pushes=pushes,
        late_line_moves=late_moves,
        weekly=_weekly_windows(deduped),
        subgroups=_subgroups(deduped),
        notes=tuple(notes),
    )
