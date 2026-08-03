"""Rolling-origin benchmark replay over timestamped historical markets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

from wnba_services.learning_loop.settlement import STAT_COLUMNS, actual_stat

SNAPSHOTS = {
    "previous_evening": timedelta(hours=18),
    "six_hours": timedelta(hours=6),
    "two_hours": timedelta(hours=2),
    "thirty_minutes": timedelta(minutes=30),
    "ten_minutes": timedelta(minutes=10),
}
MODEL_NAMES = ("season_average", "last_five", "minutes_rate", "market_prior", "ensemble")


@dataclass(frozen=True)
class BacktestBatch:
    backtest_run_id: UUID
    markets: int
    results: int
    skipped: int


def poisson_over_probability(expectation: float, line: Decimal) -> float:
    """Exact Poisson over probability for a count prop."""
    expectation = max(0.001, min(150.0, expectation))
    threshold = math.floor(float(line))
    probability = math.exp(-expectation)
    cumulative = probability
    for value in range(1, threshold + 1):
        probability *= expectation / value
        cumulative += probability
    return max(0.0, min(1.0, 1.0 - cumulative))


def latest_quote_as_of(quotes: list[dict[str, Any]], as_of: datetime) -> dict[str, Any] | None:
    """Choose only observations known by the replay timestamp."""
    eligible = [
        row
        for row in quotes
        if isinstance(row["observed_at"], datetime) and row["observed_at"] <= as_of
    ]
    return None if not eligible else max(eligible, key=lambda row: row["observed_at"])


def _stat(row: dict[str, Any], columns: tuple[str, ...]) -> float:
    return sum(float(str(row[column])) for column in columns)


def _weighted_mean(values: list[float]) -> float:
    weights = range(1, len(values) + 1)
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / sum(weights)


def _model_predictions(
    history: list[dict[str, Any]], columns: tuple[str, ...], line: Decimal
) -> dict[str, tuple[float, float]]:
    season_values = [_stat(row, columns) for row in history]
    recent = history[-5:]
    recent_values = [_stat(row, columns) for row in recent]
    season_mean = sum(season_values) / len(season_values)
    last_five = sum(recent_values) / len(recent_values)
    minutes = [float(str(row["minutes"])) for row in history if float(str(row["minutes"])) > 0]
    total_minutes = sum(minutes)
    minutes_rate = _weighted_mean(minutes[-10:]) * sum(season_values) / max(1.0, total_minutes)
    market_prior = float(line) + 0.5
    component_means = {
        "season_average": season_mean,
        "last_five": last_five,
        "minutes_rate": minutes_rate,
        "market_prior": market_prior,
    }
    component_probabilities = {
        name: poisson_over_probability(mean, line) for name, mean in component_means.items()
    }
    weights = {
        "season_average": 0.30,
        "last_five": 0.20,
        "minutes_rate": 0.45,
        "market_prior": 0.05,
    }
    ensemble_mean = math.fsum(weights[name] * component_means[name] for name in weights)
    ensemble_probability = math.fsum(
        weights[name] * component_probabilities[name] for name in weights
    )
    return {
        **{name: (mean, component_probabilities[name]) for name, mean in component_means.items()},
        "ensemble": (ensemble_mean, ensemble_probability),
    }


def run_walk_forward_backtest(*, now: datetime | None = None) -> BacktestBatch:
    """Replay each historical market using only earlier canonical box scores and quotes."""
    at = now or datetime.now(UTC)
    run_id = uuid4()
    markets = results = skipped = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.backtest_runs
               (backtest_run_id,started_at,status,specification)
               VALUES (%s,%s,'running',%s)""",
            (
                run_id,
                at,
                Jsonb(
                    {
                        "snapshots": {
                            name: int(offset.total_seconds()) for name, offset in SNAPSHOTS.items()
                        },
                        "models": MODEL_NAMES,
                        "point_in_time": True,
                    }
                ),
            ),
        )
        cur.execute(
            """SELECT q.* FROM wnba.historical_prop_quotes q
               ORDER BY q.game_id,q.player_id,q.prop_type,q.bookmaker,q.observed_at"""
        )
        grouped: dict[tuple[object, ...], list[dict[str, Any]]] = {}
        for raw in cur.fetchall():
            row = dict(raw)
            key = (row["game_id"], row["player_id"], row["prop_type"], row["bookmaker"])
            grouped.setdefault(key, []).append(row)
        for (game_id, player_id, prop_type, _), quotes in grouped.items():
            columns = STAT_COLUMNS.get(str(prop_type))
            if columns is None:
                skipped += 1
                continue
            tip = quotes[0]["scheduled_tipoff"]
            if not isinstance(tip, datetime):
                skipped += 1
                continue
            cur.execute(
                """SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.game_id=%s AND l.system_to IS NULL""",
                (player_id, game_id),
            )
            outcome = cur.fetchone()
            if outcome is None or float(str(outcome["minutes"])) <= 0:
                skipped += 1
                continue
            for snapshot_label, offset in SNAPSHOTS.items():
                as_of = tip - offset
                quote = latest_quote_as_of(quotes, as_of)
                if quote is None:
                    continue
                cur.execute(
                    """SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines l
                       JOIN wnba.games g ON g.game_id=l.game_id
                       WHERE l.player_id=%s AND l.system_to IS NULL AND g.status='final'
                         AND g.scheduled_tipoff<%s ORDER BY g.scheduled_tipoff DESC LIMIT 30""",
                    (player_id, as_of),
                )
                history = list(reversed(cur.fetchall()))
                if len(history) < 10:
                    skipped += 1
                    continue
                line = Decimal(str(quote["line"]))
                actual = actual_stat(dict(outcome), str(prop_type))
                if actual == float(line):
                    continue
                hit = actual > float(line)
                predictions = _model_predictions(history, columns, line)
                for model_name, (mean, probability) in predictions.items():
                    cur.execute(
                        """INSERT INTO wnba.backtest_results
                           (result_id,backtest_run_id,historical_quote_id,snapshot_label,
                            forecast_as_of,model_name,prop_type,line,projected_mean,
                            predicted_probability,actual_stat,hit,brier,log_loss,history_games,
                            quote_age_seconds)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            uuid4(),
                            run_id,
                            quote["historical_quote_id"],
                            snapshot_label,
                            as_of,
                            model_name,
                            prop_type,
                            line,
                            mean,
                            probability,
                            actual,
                            hit,
                            brier_score(probability, int(hit)),
                            log_loss(probability, int(hit)),
                            len(history),
                            int((as_of - quote["observed_at"]).total_seconds()),
                        ),
                    )
                    results += 1
                markets += 1
        cur.execute(
            """UPDATE wnba.backtest_runs SET status='complete',completed_at=%s,result_count=%s
               WHERE backtest_run_id=%s""",
            (datetime.now(UTC), results, run_id),
        )
    return BacktestBatch(run_id, markets, results, skipped)
