"""Rolling-origin benchmark replay over timestamped historical markets.

The harness used to score a model that did not exist. Its "ensemble" pooled season_average,
last_five, minutes_rate, opportunity_conversion and market_prior; production pooled empirical,
hierarchical, player_state, opportunity_conversion and market_prior. They shared one component
and used different weights, and the replay saw no injuries, no projected roles, no teammate
absences and no matchup context at all -- so it could not have measured whether any of those
features helped even in principle. The model card cited its Brier score anyway, and the
readiness gates read the same table.

Now the ``production_ensemble`` row is produced by :func:`score_prop` -- the same function the
live board calls -- fed only with what was knowable at the replay timestamp, including the
point-in-time role, teammate and matchup rows when they exist. The naive baselines stay, because
the roadmap's exit gate is that production beats minutes x rate on held-out log loss, and that
comparison needs minutes x rate computed honestly rather than remembered fondly.
"""

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

from wnba_services.forecasting.baseline import (
    HISTORY_GAMES,
    market_inputs,
    stat_history_game,
)
from wnba_services.forecasting.challengers import CHALLENGERS, challenger_names
from wnba_services.forecasting.parameters import load_fitted_parameters
from wnba_services.forecasting.recency import MARKET_HALF_LIFE, weighted_mean, weighted_ratio
from wnba_services.forecasting.scoring import (
    SCORER_VERSION,
    SUPPORTED_MARKETS,
    HistoryGame,
    MatchupInputs,
    RoleInputs,
    ScoringInputs,
    TeammateInputs,
    opportunity_conversion_expectation,
    score_prop,
)
from wnba_services.learning_loop.settlement import STAT_COLUMNS, actual_stat

SNAPSHOTS = {
    "previous_evening": timedelta(hours=18),
    "six_hours": timedelta(hours=6),
    "two_hours": timedelta(hours=2),
    "thirty_minutes": timedelta(minutes=30),
    "ten_minutes": timedelta(minutes=10),
}
MODEL_NAMES = (
    "season_average",
    "last_five",
    "minutes_rate",
    "opportunity_conversion",
    "market_prior",
    "production_ensemble",
    *challenger_names(),
)

# Fewer draws than production uses. The replay scores tens of thousands of snapshots, and the
# empirical component's Monte Carlo error at this count is well inside the differences between
# the models being compared; production keeps the full count because it runs once per quote.
REPLAY_SIMULATIONS = 2_000
REPLAY_SEED = 20260803


@dataclass(frozen=True)
class BacktestBatch:
    backtest_run_id: UUID
    markets: int
    results: int
    skipped: int


def poisson_over_probability(expectation: float, line: Decimal) -> float:
    """Exact Poisson over probability for a count prop.

    Retained for the naive baselines only. The production scorer models over-dispersion
    explicitly, and the gap between the two is part of what the replay now measures.
    """
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


def benchmark_predictions(inputs: ScoringInputs) -> dict[str, tuple[float, float]]:
    """The naive baselines production has to beat, from the same point-in-time history."""
    columns = inputs.columns
    line = inputs.line
    played = [game for game in inputs.history if game.minutes > 0.0]
    totals = [game.total(columns) for game in inputs.history]
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)

    season_mean = math.fsum(totals) / len(totals)
    recent = totals[-5:]
    last_five = math.fsum(recent) / len(recent)

    if played:
        minutes = [game.minutes for game in played]
        expected_minutes = weighted_mean(minutes[-10:], half_life)
        minutes_rate = expected_minutes * weighted_ratio(
            [game.total(columns) for game in played], minutes, half_life
        )
    else:
        expected_minutes = 0.0
        minutes_rate = 0.0
    opportunity = opportunity_conversion_expectation(inputs, expected_minutes)

    means = {
        "season_average": season_mean,
        "last_five": last_five,
        "minutes_rate": minutes_rate,
        "opportunity_conversion": opportunity,
        "market_prior": float(line) + 0.5,
    }
    return {name: (mean, poisson_over_probability(mean, line)) for name, mean in means.items()}


def _point_in_time_role(
    cur: Any, player_id: UUID, game_id: UUID, as_of: datetime
) -> RoleInputs | None:
    """The role row as it stood at ``as_of``, or ``None`` if it had not been written yet."""
    cur.execute(
        """SELECT expected_minutes,minutes_std,start_probability,availability_probability,
                  closing_lineup_probability,minutes_restriction_probability,model_version
           FROM wnba.projected_roles
           WHERE player_id=%s AND game_id=%s AND system_from<=%s
             AND (system_to IS NULL OR system_to>%s)
           ORDER BY system_from DESC LIMIT 1""",
        (player_id, game_id, as_of, as_of),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return RoleInputs(
        expected_minutes=float(str(row["expected_minutes"])),
        minutes_std=float(str(row["minutes_std"])),
        start_probability=float(str(row["start_probability"])),
        availability_probability=float(str(row["availability_probability"])),
        closing_lineup_probability=(
            None
            if row.get("closing_lineup_probability") is None
            else float(str(row["closing_lineup_probability"]))
        ),
        minutes_restriction_probability=(
            None
            if row.get("minutes_restriction_probability") is None
            else float(str(row["minutes_restriction_probability"]))
        ),
        model_version=str(row["model_version"]),
    )


def _point_in_time_teammate(
    cur: Any, player_id: UUID, game_id: UUID, prop_type: str, as_of: datetime
) -> TeammateInputs | None:
    cur.execute(
        """SELECT coalesce(exp(sum(ln(rate_multiplier))),1.0) AS rate_multiplier,
                  coalesce(sum(minutes_delta),0.0) AS minutes_delta,
                  count(*) AS effects,min(confidence) AS confidence
           FROM wnba.teammate_role_effects
           WHERE player_id=%s AND game_id=%s AND prop_type=%s AND system_from<=%s
             AND (system_to IS NULL OR system_to>%s)""",
        (player_id, game_id, prop_type, as_of, as_of),
    )
    row = cur.fetchone()
    if row is None or int(str(row["effects"])) == 0:
        return None
    return TeammateInputs(
        rate_multiplier=float(str(row["rate_multiplier"])),
        minutes_delta=float(str(row["minutes_delta"])),
        effect_count=int(str(row["effects"])),
        confidence=None if row["confidence"] is None else float(str(row["confidence"])),
    )


def _point_in_time_matchup(
    cur: Any, game_id: UUID, prop_type: str, as_of: datetime
) -> MatchupInputs | None:
    cur.execute(
        """SELECT pace_multiplier,defense_multiplier,blowout_probability,expected_possessions,
                  expected_margin,team_rest_days,opponent_rest_days,method_version
           FROM wnba.matchup_contexts
           WHERE game_id=%s AND prop_type=%s AND system_from<=%s
             AND (system_to IS NULL OR system_to>%s)
           ORDER BY system_from DESC LIMIT 1""",
        (game_id, prop_type, as_of, as_of),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return MatchupInputs(
        pace_multiplier=float(str(row["pace_multiplier"])),
        defense_multiplier=float(str(row["defense_multiplier"])),
        blowout_probability=float(str(row["blowout_probability"])),
        expected_possessions=float(str(row["expected_possessions"])),
        expected_margin=float(str(row["expected_margin"])),
        team_rest_days=float(str(row["team_rest_days"])),
        opponent_rest_days=float(str(row["opponent_rest_days"])),
        method_version=str(row["method_version"]),
    )


def _league_rate(
    cur: Any, cache: dict[tuple[str, Any], float], prop_type: str, as_of: datetime
) -> float:
    """League per-minute rate using only games completed before ``as_of``'s day.

    Cached by day rather than by timestamp: it bounds the query count, and rounding the cutoff
    backwards can only ever use *less* information than the replay was entitled to.
    """
    day = as_of.date()
    key = (prop_type, day)
    if key in cache:
        return cache[key]
    columns = STAT_COLUMNS[prop_type]
    expression = " + ".join(f"l.{column}" for column in columns)
    cutoff = datetime(day.year, day.month, day.day, tzinfo=UTC)
    cur.execute(
        f"""SELECT coalesce(sum({expression}),0) AS total_stat,
                   coalesce(sum(l.minutes),0) AS total_minutes
            FROM wnba.player_game_lines l
            JOIN wnba.games g ON g.game_id=l.game_id
            WHERE l.system_to IS NULL AND g.status='final'
              AND g.scheduled_tipoff<%s AND l.minutes>0""",
        (cutoff,),
    )
    row = cur.fetchone()
    rate = 0.0
    if row is not None:
        rate = float(str(row["total_stat"])) / max(1.0, float(str(row["total_minutes"])))
    cache[key] = rate
    return rate


def run_walk_forward_backtest(*, now: datetime | None = None) -> BacktestBatch:
    """Replay each historical market using only earlier canonical box scores and quotes."""
    at = now or datetime.now(UTC)
    run_id = uuid4()
    markets = results = skipped = 0
    league_cache: dict[tuple[str, Any], float] = {}

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
                        "scorer_version": SCORER_VERSION,
                        "shares_production_scorer": True,
                        "simulations": REPLAY_SIMULATIONS,
                    }
                ),
            ),
        )

        # Calibration and weights are loaded, but calibration is *not* applied to the replay's
        # scored probability: the maps were fitted on settled episodes that overlap this very
        # period, so applying them here would score the model on its own answers.
        parameters = load_fitted_parameters(cur)

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
            market = str(prop_type)
            columns = STAT_COLUMNS.get(market)
            if columns is None or market not in SUPPORTED_MARKETS:
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
                         AND g.scheduled_tipoff<%s ORDER BY g.scheduled_tipoff DESC LIMIT %s""",
                    (player_id, as_of, HISTORY_GAMES),
                )
                history: tuple[HistoryGame, ...] = tuple(
                    stat_history_game(dict(row)) for row in reversed(cur.fetchall())
                )
                line = Decimal(str(quote["line"]))
                actual = actual_stat(dict(outcome), market)
                if actual == float(line):
                    continue
                hit = actual > float(line)

                inputs = ScoringInputs(
                    prop_type=market,
                    line=line,
                    history=history,
                    league_rate_per_minute=_league_rate(cur, league_cache, market, as_of),
                    seed=REPLAY_SEED
                    ^ hash((str(quote["historical_quote_id"]), snapshot_label)) & 0x7FFFFFFF,
                    role=_point_in_time_role(cur, UUID(str(player_id)), UUID(str(game_id)), as_of),
                    teammate=_point_in_time_teammate(
                        cur, UUID(str(player_id)), UUID(str(game_id)), market, as_of
                    ),
                    matchup=_point_in_time_matchup(cur, UUID(str(game_id)), market, as_of),
                    market=market_inputs(dict(quote)),
                    weights=parameters.weights_for(market),
                    simulations=REPLAY_SIMULATIONS,
                )
                if not inputs.has_sufficient_history:
                    skipped += 1
                    continue

                predictions = dict(benchmark_predictions(inputs))
                forecast = score_prop(inputs)
                predictions["production_ensemble"] = (forecast.mean, forecast.over)
                # Challengers replay alongside the champion on identical inputs. A challenger
                # that raises is left out of this snapshot rather than aborting the run; the
                # live experiment records failures explicitly, which is where failure rate is
                # measured. Here it would only be measuring the historical data's gaps.
                for name, challenger in CHALLENGERS.items():
                    try:
                        prediction = challenger.predict(inputs)
                    except Exception:  # replay must survive one bad prop
                        continue
                    predictions[name] = (prediction.mean, prediction.over)

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
                            market,
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
