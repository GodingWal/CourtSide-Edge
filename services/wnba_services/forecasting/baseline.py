"""Forecasting the live board. Paper-only, and now on the same scorer the replay uses.

What this module is responsible for is reading the world and writing the record. It no longer
contains any modelling: expectations, distributions, pooling and dispersion all live in
:mod:`wnba_services.forecasting.scoring`, which the walk-forward harness also calls. That is the
whole point of the rewrite -- the previous version implemented one ensemble here and the replay
implemented a different one over different components, so no backtest number ever described the
model that actually ran.

The order of operations at the end is deliberate and every step can only restrict:

1. the scorer produces a raw distribution;
2. the fitted calibration map corrects it for measured bias;
3. active analyst rules may shrink or block it;
4. edge shrinkage discounts what is left for regression to the mean;
5. the gate compares the survivor against the payout table's break-even.

Nothing in that chain can make a forecast more confident than the arithmetic supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_domain.enums import EntryType, RecommendationStatus
from wnba_marketmath.odds import remove_vig, remove_vig_decimal
from wnba_marketmath.pickem import underdog_payout_table
from wnba_store.db import connect

from wnba_services.forecasting.deescalation import load_drift_guard, record_deescalation
from wnba_services.forecasting.parameters import load_fitted_parameters
from wnba_services.forecasting.rules import (
    build_facts,
    evaluate_rules,
    load_rules,
    record_firings,
)
from wnba_services.forecasting.scoring import (
    SCORER_VERSION,
    SUPPORTED_MARKETS,
    HistoryGame,
    MarketInputs,
    MatchupInputs,
    PriorSeasonRate,
    RoleInputs,
    ScoringInputs,
    TeammateInputs,
    score_prop,
)
from wnba_services.forecasting.selection import breakeven_probability, decide_candidate
from wnba_services.learning_loop.experiments import (
    record_shadow_predictions,
    running_experiments,
)

SEMVER = "0.4.0"
MODEL_ID = uuid5(NAMESPACE_URL, f"courtside-edge:auditable-ensemble:{SEMVER}")

# Retained for callers that imported the market map from here.
SUPPORTED = SUPPORTED_MARKETS

# Box-score fields the scorer may read. Anything absent from a row is simply absent from the
# history game, and the opportunity/conversion component falls back accordingly.
STAT_FIELDS: tuple[str, ...] = (
    "points",
    "rebounds_offensive",
    "rebounds_defensive",
    "assists",
    "three_pointers_made",
    "three_pointers_attempted",
    "field_goals_attempted",
    "free_throws_attempted",
    "steals",
    "blocks",
    "turnovers",
)

HISTORY_GAMES = 25
# The gate is measured against the most demanding entry shape on the board -- a two-leg power
# play, which needs 57.7% per leg. Using the least demanding shape would let a forecast qualify
# for an entry the analyst might never build. See PAYOUT_TABLES_ARE_UNVERIFIED: this number is
# only as good as the bundled table, and the readiness gate for verifying it is still pending.
_PAYOUT_REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ForecastBatch:
    model_run_id: UUID
    forecasts: int
    episodes: int
    skipped: int
    candidates: int = 0
    rule_firings: int = 0
    # Forecasts widened because an unresolved calibration incident was open against this model
    # version. Non-zero means the system measured its own drift and acted on it.
    deescalations: int = 0


def default_breakeven() -> float:
    table = underdog_payout_table(_PAYOUT_REFERENCE)
    for rule in table.rules:
        if rule.entry_type is EntryType.POWER and rule.leg_count == 2:
            return breakeven_probability(rule)
    raise RuntimeError("bundled payout table has no two-leg power rule")


def market_inputs(quote: dict[str, Any]) -> MarketInputs:
    """Devig whatever price the source gave us, or report that it gave us none.

    A flat pick'em board carries no directional information and must not be mistaken for a
    market that says fifty-fifty; :class:`MarketInputs` distinguishes the two, and the scorer
    falls back to a line-centred prior when the price is uninformative.
    """
    over_odds, under_odds = quote.get("over_american_odds"), quote.get("under_american_odds")
    if over_odds is not None and under_odds is not None:
        try:
            over, under = remove_vig(int(over_odds), int(under_odds))
        except ValueError:
            return MarketInputs()
        return MarketInputs(over_probability=over, under_probability=under)

    over_multiplier = quote.get("over_multiplier")
    under_multiplier = quote.get("under_multiplier")
    if over_multiplier is None or under_multiplier is None:
        return MarketInputs()
    try:
        fair = remove_vig_decimal(float(str(over_multiplier)), float(str(under_multiplier)))
    except ValueError:
        return MarketInputs()
    if fair is None:
        return MarketInputs()
    return MarketInputs(over_probability=fair[0], under_probability=fair[1])


def stat_history_game(row: dict[str, Any]) -> HistoryGame:
    stats = {field: float(str(row[field])) for field in STAT_FIELDS if row.get(field) is not None}
    return HistoryGame(
        minutes=float(str(row["minutes"])),
        started=bool(row.get("started", False)),
        stats=stats,
    )


def _prior_season_rate(
    cur: Any, player_id: UUID, columns: tuple[str, ...], season_year: int, before: datetime
) -> PriorSeasonRate | None:
    """Per-minute production in earlier seasons, as a shrinkage target for a thin current one.

    Requiring ten current-season games before forecasting anything meant skipping most of the
    board every April, when a returning player's previous season is by far the best evidence
    available about them.
    """
    expression = " + ".join(f"l.{column}" for column in columns)
    cur.execute(
        f"""SELECT coalesce(sum({expression}),0) AS total_stat,
                   coalesce(sum(l.minutes),0) AS total_minutes
            FROM wnba.player_game_lines l
            JOIN wnba.games g ON g.game_id=l.game_id
            WHERE l.player_id=%s AND l.system_to IS NULL AND g.status='final'
              AND g.season_year<%s AND g.scheduled_tipoff<%s AND l.minutes>0""",
        (player_id, season_year, before),
    )
    row = cur.fetchone()
    if row is None:
        return None
    minutes = float(str(row["total_minutes"]))
    if minutes < 60.0:
        return None
    return PriorSeasonRate(rate_per_minute=float(str(row["total_stat"])) / minutes, minutes=minutes)


def run_baseline(*, now: datetime | None = None, seed: int = 20260803) -> ForecastBatch:
    """Forecast the current board. Every recommendation remains shadow/paper-only."""
    at = now or datetime.now(UTC)
    started = datetime.now(UTC)
    run_id = uuid4()
    forecasts = episodes = skipped = candidates = firing_count = deescalations = 0
    league_rates: dict[str, float] = {}
    breakeven = default_breakeven()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.model_versions
               (model_version_id,name,semver,target,stage,specification)
               VALUES (%s,'auditable-ensemble',%s,'multi','champion',%s)
               ON CONFLICT DO NOTHING""",
            (
                MODEL_ID,
                SEMVER,
                Jsonb(
                    {
                        "history_games": HISTORY_GAMES,
                        "simulations": 10_000,
                        "analysis_only": True,
                        "scorer_version": SCORER_VERSION,
                        "shared_with_backtest": True,
                        "count_distribution": "negative_binomial",
                        "pool": "log_linear",
                        "components": [
                            "empirical",
                            "hierarchical",
                            "player_state",
                            "opportunity_conversion",
                            "market_prior",
                        ],
                    }
                ),
            ),
        )
        cur.execute(
            """INSERT INTO wnba.model_runs
               (model_run_id,model_version_id,started_at,completed_at,random_seed,status,detail)
               VALUES (%s,%s,%s,%s,%s,'running','')""",
            (run_id, MODEL_ID, started, started, seed),
        )

        parameters = load_fitted_parameters(cur)
        rules = load_rules(cur)
        # If this model version has an unresolved calibration incident open against it, every
        # probability it emits today is shrunk toward even before it reaches the gate. Loaded
        # once per run: the incident set does not change mid-board, and re-reading it per quote
        # would make the run's behaviour depend on when settlement happened to commit.
        drift_guard = load_drift_guard(cur, MODEL_ID)
        # Challengers with an open experiment score the same board from the same inputs. They
        # write to `challenger_predictions` and to nothing else: no rule reads them, no decision
        # consults them, and the recommendation below would be byte-identical if this list were
        # empty. Shadow means shadow.
        experiments = running_experiments(cur)

        cur.execute(
            """SELECT DISTINCT ON (q.source,q.source_quote_id) q.*,
                      coalesce(m.to_player_id,q.player_id) AS canonical_player_id,
                      g.season_year
               FROM wnba.prop_quotes q
               JOIN wnba.games g ON g.game_id=q.game_id
               LEFT JOIN wnba.player_merges m
                 ON m.from_player_id=q.player_id AND m.system_to IS NULL
               WHERE q.is_available AND q.game_id IS NOT NULL AND q.locks_at>%s
                 AND NOT EXISTS (
                     SELECT 1 FROM wnba.stat_forecasts f
                     JOIN wnba.model_runs mr ON mr.model_run_id=f.model_run_id
                     WHERE f.quote_id=q.quote_id AND f.expires_at=q.locks_at
                       AND mr.model_version_id=%s
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.injury_status i
                           WHERE i.player_id=coalesce(m.to_player_id,q.player_id)
                             AND i.game_id=q.game_id AND i.system_to IS NULL
                             AND i.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.projected_roles r
                           WHERE r.player_id=coalesce(m.to_player_id,q.player_id)
                             AND r.game_id=q.game_id AND r.system_to IS NULL
                             AND r.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.teammate_role_effects e
                           WHERE e.player_id=coalesce(m.to_player_id,q.player_id)
                             AND e.game_id=q.game_id AND e.prop_type=q.prop_type
                             AND e.system_to IS NULL AND e.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.matchup_contexts c
                           WHERE c.game_id=q.game_id AND c.prop_type=q.prop_type
                             AND c.system_to IS NULL AND c.system_from>f.generated_at))
               ORDER BY q.source,q.source_quote_id,q.system_from DESC""",
            (at, MODEL_ID),
        )
        quotes = cur.fetchall()

        for quote in quotes:
            prop = str(quote["prop_type"])
            columns = SUPPORTED_MARKETS.get(prop)
            if columns is None:
                skipped += 1
                continue
            player_id = UUID(str(quote["canonical_player_id"]))

            # `locks_at` is nullable in the schema and is the cutoff every point-in-time query
            # below depends on. A quote without one cannot be forecast without risking leakage.
            locks_at = quote["locks_at"]
            if not isinstance(locks_at, datetime):
                skipped += 1
                continue

            cur.execute(
                """SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL AND g.status='final'
                     AND g.scheduled_tipoff<%s ORDER BY g.scheduled_tipoff DESC LIMIT %s""",
                (player_id, locks_at, HISTORY_GAMES),
            )
            history = tuple(stat_history_game(dict(row)) for row in reversed(cur.fetchall()))

            cur.execute(
                """SELECT designation,detail FROM wnba.injury_status
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL
                   ORDER BY system_from DESC LIMIT 1""",
                (player_id, quote["game_id"]),
            )
            injury = cur.fetchone()
            designation = "available" if injury is None else str(injury["designation"])
            if designation in {"out", "season_ending", "not_with_team"}:
                skipped += 1
                continue

            prior_season = _prior_season_rate(
                cur, player_id, columns, int(str(quote["season_year"])), locks_at
            )

            cur.execute(
                """SELECT availability_probability,start_probability,
                          closing_lineup_probability,expected_minutes,minutes_std,
                          minutes_restriction_probability,model_version
                   FROM wnba.projected_roles
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                (player_id, quote["game_id"]),
            )
            role_row = cur.fetchone()
            role = (
                None
                if role_row is None
                else RoleInputs(
                    expected_minutes=float(str(role_row["expected_minutes"])),
                    minutes_std=float(str(role_row["minutes_std"])),
                    start_probability=float(str(role_row["start_probability"])),
                    availability_probability=float(str(role_row["availability_probability"])),
                    model_version=str(role_row["model_version"]),
                )
            )

            cur.execute(
                """SELECT coalesce(exp(sum(ln(rate_multiplier))),1.0) AS rate_multiplier,
                          coalesce(sum(minutes_delta),0.0) AS minutes_delta,
                          count(*) AS effects,min(confidence) AS confidence,
                          min(method_version) AS method_version
                   FROM wnba.teammate_role_effects
                   WHERE player_id=%s AND game_id=%s AND prop_type=%s AND system_to IS NULL""",
                (player_id, quote["game_id"], prop),
            )
            effect_row = cur.fetchone()
            effect_count = 0 if effect_row is None else int(str(effect_row["effects"]))
            teammate = (
                None
                if effect_row is None or effect_count == 0
                else TeammateInputs(
                    rate_multiplier=float(str(effect_row["rate_multiplier"])),
                    minutes_delta=float(str(effect_row["minutes_delta"])),
                    effect_count=effect_count,
                    confidence=(
                        None
                        if effect_row["confidence"] is None
                        else float(str(effect_row["confidence"]))
                    ),
                    method_version=(
                        None
                        if effect_row["method_version"] is None
                        else str(effect_row["method_version"])
                    ),
                )
            )

            team_id: UUID | None = None
            cur.execute(
                """SELECT l.team_id FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL
                   ORDER BY g.scheduled_tipoff DESC LIMIT 1""",
                (player_id,),
            )
            team_row = cur.fetchone()
            if team_row is not None:
                team_id = UUID(str(team_row["team_id"]))

            matchup = None
            if team_id is not None:
                cur.execute(
                    """SELECT expected_possessions,pace_multiplier,defense_multiplier,
                              expected_margin,blowout_probability,team_rest_days,
                              opponent_rest_days,confidence,method_version
                       FROM wnba.matchup_contexts
                       WHERE game_id=%s AND team_id=%s AND prop_type=%s AND system_to IS NULL""",
                    (quote["game_id"], team_id, prop),
                )
                matchup_row = cur.fetchone()
                if matchup_row is not None:
                    matchup = MatchupInputs(
                        pace_multiplier=float(str(matchup_row["pace_multiplier"])),
                        defense_multiplier=float(str(matchup_row["defense_multiplier"])),
                        blowout_probability=float(str(matchup_row["blowout_probability"])),
                        expected_possessions=float(str(matchup_row["expected_possessions"])),
                        expected_margin=float(str(matchup_row["expected_margin"])),
                        team_rest_days=float(str(matchup_row["team_rest_days"])),
                        opponent_rest_days=float(str(matchup_row["opponent_rest_days"])),
                        method_version=str(matchup_row["method_version"]),
                    )

            if prop not in league_rates:
                expression = " + ".join(f"l.{column}" for column in columns)
                cur.execute(
                    f"""SELECT coalesce(sum({expression}),0) AS total_stat,
                               coalesce(sum(l.minutes),0) AS total_minutes
                        FROM wnba.player_game_lines l
                        JOIN wnba.games g ON g.game_id=l.game_id
                        WHERE l.system_to IS NULL AND g.status='final'
                          AND g.scheduled_tipoff<%s AND l.minutes>0""",
                    (locks_at,),
                )
                league = cur.fetchone()
                if league is None:
                    raise RuntimeError("League-rate aggregate returned no row")
                league_rates[prop] = float(str(league["total_stat"])) / max(
                    1.0, float(str(league["total_minutes"]))
                )

            local_seed = seed ^ int(str(quote["quote_id"]).replace("-", "")[:8], 16)
            inputs = ScoringInputs(
                prop_type=prop,
                line=Decimal(str(quote["line"])),
                history=history,
                league_rate_per_minute=league_rates[prop],
                seed=local_seed,
                injury_designation=designation,
                role=role,
                teammate=teammate,
                matchup=matchup,
                market=market_inputs(dict(quote)),
                prior_season=prior_season,
                weights=parameters.weights_for(prop),
                calibration=parameters.calibration_for(prop),
            )
            if not inputs.has_sufficient_history:
                skipped += 1
                continue

            forecast = score_prop(inputs)
            adjustments = forecast.adjustments
            projected_minutes = adjustments.expected_minutes

            side = "over" if forecast.calibrated_over >= forecast.calibrated_under else "under"
            selected = max(forecast.calibrated_over, forecast.calibrated_under)

            facts = build_facts(
                predicted_probability=selected,
                confidence=selected,
                model_disagreement=forecast.disagreement,
                data_quality_score=forecast.data_quality_score,
                projected_minutes=projected_minutes,
                minutes_std=adjustments.minutes_std,
                line=float(str(quote["line"])),
                prop_type=prop,
                source=str(quote["source"]),
                side=side,
                injury_designation=designation,
                teammate_effect_count=effect_count,
                availability_probability=(None if role is None else role.availability_probability),
                start_probability=adjustments.start_probability,
            )
            rule_outcome = evaluate_rules(rules, facts, selected)
            # Measured drift is applied after the rules and before the gate, so a rule firing and
            # a drift response compose the way two restrictions should: each can only widen.
            guarded = drift_guard.apply(rule_outcome.probability)

            decision = decide_candidate(
                over_probability=(guarded if side == "over" else 1.0 - guarded),
                under_probability=(guarded if side == "under" else 1.0 - guarded),
                shrinkage=parameters.shrinkage_for(prop),
                breakeven=breakeven,
                quality=forecast.data_quality_score,
                disagreement=forecast.disagreement,
                injury_designation=designation,
                rule_blocked=rule_outcome.blocked,
                rule_reason="; ".join(rule_outcome.block_reasons),
            )

            feature_id, projection_id, episode_id = uuid4(), uuid4(), uuid4()
            cur.execute(
                """INSERT INTO wnba.feature_snapshots
                   (feature_snapshot_id,player_id,game_id,as_of,features,source_line_ids)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    feature_id,
                    player_id,
                    quote["game_id"],
                    at,
                    Jsonb(
                        {
                            **forecast.diagnostics,
                            "injury_designation": designation,
                            "injury_detail": None if injury is None else injury["detail"],
                            "role_model": None if role is None else role.model_version,
                            "teammate_effect_count": effect_count,
                            "teammate_method": None
                            if teammate is None
                            else teammate.method_version,
                            "matchup_model": None if matchup is None else matchup.method_version,
                            "expected_possessions": (
                                None if matchup is None else matchup.expected_possessions
                            ),
                            "expected_margin": None if matchup is None else matchup.expected_margin,
                            "team_rest_days": None if matchup is None else matchup.team_rest_days,
                            "opponent_rest_days": (
                                None if matchup is None else matchup.opponent_rest_days
                            ),
                            "rules_evaluated": len(rules),
                            "rules_explanation": rule_outcome.explain(),
                            "decision_reason": decision.reason,
                            "breakeven_probability": breakeven,
                        }
                    ),
                    [],
                ),
            )

            distribution = {
                str(value): probability
                for value, probability in enumerate(forecast.pmf)
                if probability > 0
            }
            cur.execute(
                """INSERT INTO wnba.stat_forecasts
                   (projection_id,model_run_id,feature_snapshot_id,quote_id,player_id,game_id,
                    prop_type,line,mean,median,stddev,probability_over,probability_push,
                    probability_under,projected_minutes,sample_size,data_quality_score,confidence,
                    distribution,generated_at,expires_at,is_shadow,probability_over_raw,
                    dispersion,scorer_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           true,%s,%s,%s)""",
                (
                    projection_id,
                    run_id,
                    feature_id,
                    quote["quote_id"],
                    player_id,
                    quote["game_id"],
                    prop,
                    Decimal(str(quote["line"])),
                    forecast.mean,
                    forecast.median,
                    forecast.stddev,
                    forecast.calibrated_over,
                    forecast.push,
                    forecast.calibrated_under,
                    projected_minutes,
                    len(history),
                    forecast.data_quality_score,
                    selected,
                    Jsonb(distribution),
                    at,
                    locks_at,
                    forecast.over,
                    forecast.dispersion,
                    SCORER_VERSION,
                ),
            )
            for component in forecast.components:
                cur.execute(
                    """INSERT INTO wnba.forecast_components
                       (component_id,projection_id,component_name,component_version,weight,
                        mean,probability_over,probability_push,probability_under,distribution)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        uuid4(),
                        projection_id,
                        component.name,
                        component.version,
                        component.weight,
                        component.mean,
                        component.over,
                        component.push,
                        component.under,
                        Jsonb(
                            {
                                str(value): probability
                                for value, probability in enumerate(component.pmf)
                                if probability > 0
                            }
                        ),
                    ),
                )

            cur.execute(
                """INSERT INTO wnba.decision_episodes
                   (episode_id,forecast_timestamp,player_id,game_id,prop_type,side,line,source,
                    quote_id,multiplier,projected_mean,projected_median,predicted_probability,
                    projected_minutes,confidence,data_quality_score,model_disagreement,
                    model_version_ids,model_run_id,feature_snapshot_id,system_recommendation,
                    is_paper,shrunk_probability,breakeven_probability,decision_reason)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           true,%s,%s,%s)""",
                (
                    episode_id,
                    at,
                    player_id,
                    quote["game_id"],
                    prop,
                    decision.side,
                    Decimal(str(quote["line"])),
                    quote["source"],
                    quote["quote_id"],
                    forecast.mean,
                    forecast.median,
                    decision.raw_probability,
                    projected_minutes,
                    decision.shrunk_probability,
                    forecast.data_quality_score,
                    forecast.disagreement,
                    [MODEL_ID],
                    run_id,
                    feature_id,
                    decision.status.value,
                    decision.shrunk_probability,
                    breakeven,
                    decision.reason,
                ),
            )
            firing_count += record_firings(cur, episode_id, rule_outcome.firings, at=at)
            deescalations += record_deescalation(
                cur,
                drift_guard,
                episode_id=episode_id,
                before=rule_outcome.probability,
                after=guarded,
                at=at,
            )
            if experiments:
                record_shadow_predictions(
                    cur,
                    experiments,
                    episode_id=episode_id,
                    inputs=inputs,
                    side=decision.side,
                    at=at,
                )

            forecasts += 1
            episodes += 1
            candidates += int(decision.status is RecommendationStatus.CANDIDATE)

        completed = datetime.now(UTC)
        cur.execute(
            """UPDATE wnba.model_runs SET completed_at=%s,status='complete',detail=%s
               WHERE model_run_id=%s""",
            (
                completed,
                f"forecasts={forecasts};candidates={candidates};skipped={skipped}",
                run_id,
            ),
        )
    return ForecastBatch(
        run_id, forecasts, episodes, skipped, candidates, firing_count, deescalations
    )
