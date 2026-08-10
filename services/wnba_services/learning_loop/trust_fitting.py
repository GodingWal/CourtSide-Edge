"""Persist the trust artifacts measured from settled episodes and current game states."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_domain.decision import log_loss
from wnba_store.db import connect

from wnba_services.forecasting.game_simulation import PlayerStatScenario, simulate_joint_game
from wnba_services.learning_loop.independence import dedupe_latest_per_market
from wnba_services.learning_loop.trust import (
    FeatureObservation,
    adaptive_conformal_band,
    feature_ablation,
    fit_selective_policy,
    fit_source_reliability,
    risk_coverage_curve,
)

__all__ = ["TrustFitBatch", "fit_trust_artifacts", "refresh_joint_game_simulations"]


@dataclass(frozen=True)
class TrustFitBatch:
    episodes: int
    selective_policies: int
    conformal_intervals: int
    source_reliability: int
    feature_ablations: int


def _logit(probability: float) -> float:
    value = min(1.0 - 1e-6, max(1e-6, probability))
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _ablated_probability(components: list[dict[str, Any]], removed: str) -> float | None:
    retained = [row for row in components if str(row["component_name"]) != removed]
    total = math.fsum(float(str(row["weight"])) for row in retained)
    if total <= 0.0 or len(retained) == len(components):
        return None
    stacked = math.fsum(
        float(str(row["weight"])) / total * _logit(float(str(row["probability_over"])))
        for row in retained
    )
    return _sigmoid(stacked)


def fit_trust_artifacts(*, now: datetime | None = None) -> TrustFitBatch:
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,d.source,d.line,
                      d.predicted_probability,d.confidence,d.data_quality_score,
                      d.model_disagreement,d.forecast_timestamp,d.projected_mean,
                      o.actual_stat,o.hit,q.system_from AS quote_seen_at,
                      coalesce(fs.features->>'role_state','unknown') AS role_state
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o USING(episode_id)
               JOIN wnba.prop_quotes q USING(quote_id)
               LEFT JOIN wnba.feature_snapshots fs USING(feature_snapshot_id)
               WHERE NOT o.was_voided AND NOT o.was_push
               ORDER BY d.forecast_timestamp"""
        )
        rows = dedupe_latest_per_market(cur.fetchall())

        segments: dict[str, list[FeatureObservation]] = defaultdict(list)
        residuals: dict[str, list[float]] = defaultdict(list)
        source_rows: list[tuple[str, float, float, bool]] = []
        for row in rows:
            forecast_timestamp = row["forecast_timestamp"]
            if not isinstance(forecast_timestamp, datetime):
                continue
            predicted_probability = float(str(row["predicted_probability"]))
            quality = float(str(row["data_quality_score"]))
            disagreement = float(str(row["model_disagreement"]))
            confidence = min(
                float(str(row["confidence"])),
                quality,
                max(0.0, 1.0 - disagreement / 0.25),
            )
            observation = FeatureObservation(
                predicted_probability,
                int(bool(row["hit"])),
                confidence,
                forecast_timestamp,
                str(row["prop_type"]),
                str(row["role_state"]),
            )
            keys = (
                "all",
                f"prop:{row['prop_type']}",
                f"role:{row['role_state']}",
                f"prop_role:{row['prop_type']}:{row['role_state']}",
            )
            error = float(str(row["actual_stat"])) - float(str(row["projected_mean"]))
            for key in keys:
                segments[key].append(observation)
                residuals[key].append(error)
            quote_seen_at = row["quote_seen_at"]
            quote_age = (
                (forecast_timestamp - quote_seen_at).total_seconds()
                if isinstance(quote_seen_at, datetime)
                else math.inf
            )
            fresh = 0.0 <= quote_age <= 1800.0
            source_rows.append(
                (
                    str(row["source"]),
                    float(str(row["line"])),
                    float(str(row["actual_stat"])),
                    fresh,
                )
            )

        policies = 0
        intervals = 0
        for segment, observations in sorted(segments.items()):
            policy = fit_selective_policy(observations)
            curve = risk_coverage_curve(observations)
            cur.execute(
                """INSERT INTO wnba.selective_policy_snapshots
                   (policy_id,segment,calculated_at,sample_size,minimum_confidence,coverage,
                    validation_log_loss,is_fitted,reason,risk_coverage)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    segment,
                    at,
                    policy.sample_size,
                    policy.minimum_confidence,
                    policy.coverage,
                    None
                    if not math.isfinite(policy.validation_log_loss)
                    else policy.validation_log_loss,
                    policy.is_fitted,
                    policy.reason,
                    Jsonb([point.to_payload() for point in curve]),
                ),
            )
            policies += 1
            band = adaptive_conformal_band(
                0.0,
                residuals,
                segment=segment,
                fallback_segment="all",
            )
            cur.execute(
                """INSERT INTO wnba.conformal_interval_snapshots
                   (interval_id,segment,calculated_at,sample_size,target_coverage,
                    empirical_coverage,radius,used_fallback,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    segment,
                    at,
                    band.sample_size,
                    band.target_coverage,
                    band.empirical_coverage,
                    band.radius,
                    band.used_fallback,
                    Jsonb({"method": "absolute_residual_finite_sample"}),
                ),
            )
            intervals += 1

        reliability = fit_source_reliability(source_rows)
        for fit in reliability:
            cur.execute(
                """INSERT INTO wnba.source_reliability_snapshots
                   (snapshot_id,source,calculated_at,sample_size,reliability_weight,
                    mean_absolute_error,median_absolute_error,freshness_rate,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    fit.source,
                    at,
                    fit.sample_size,
                    fit.weight,
                    fit.mean_absolute_error,
                    fit.median_absolute_error,
                    fit.freshness_rate,
                    Jsonb({"prior_error": 2.0}),
                ),
            )

        cur.execute(
            """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.forecast_timestamp,
                      d.side,o.hit,fc.component_name,fc.weight,fc.probability_over
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o USING(episode_id)
               JOIN wnba.stat_forecasts f
                 ON f.quote_id=d.quote_id AND f.model_run_id=d.model_run_id
               JOIN wnba.forecast_components fc USING(projection_id)
               WHERE NOT o.was_voided AND NOT o.was_push
               ORDER BY d.forecast_timestamp"""
        )
        components: dict[str, list[dict[str, Any]]] = defaultdict(list)
        outcomes: dict[str, tuple[str, bool]] = {}
        episode_metadata: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            episode = str(row["episode_id"])
            components[episode].append(dict(row))
            outcomes[episode] = (str(row["side"]), bool(row["hit"]))
            episode_metadata[episode] = {
                "episode_id": episode,
                "player_id": row["player_id"],
                "game_id": row["game_id"],
                "prop_type": row["prop_type"],
                "forecast_timestamp": row["forecast_timestamp"],
            }
        names = sorted(
            {str(row["component_name"]) for values in components.values() for row in values}
        )
        champion_losses: list[float] = []
        ablated_losses: dict[str, list[float]] = {name: [] for name in names}
        independent_episodes = {
            str(row["episode_id"])
            for row in dedupe_latest_per_market(list(episode_metadata.values()))
        }
        for episode in independent_episodes:
            values = components[episode]
            side, hit = outcomes[episode]
            over_outcome = int(hit if side == "over" else not hit)
            full_total = math.fsum(float(str(row["weight"])) for row in values)
            if full_total <= 0.0:
                continue
            full_probability = _sigmoid(
                math.fsum(
                    float(str(row["weight"]))
                    / full_total
                    * _logit(float(str(row["probability_over"])))
                    for row in values
                )
            )
            removed = {name: _ablated_probability(values, name) for name in names}
            if any(value is None for value in removed.values()):
                continue
            champion_losses.append(log_loss(full_probability, over_outcome))
            for name, ablated_probability in removed.items():
                if ablated_probability is not None:
                    ablated_losses[name].append(log_loss(ablated_probability, over_outcome))
        ablations = feature_ablation(champion_losses, ablated_losses)
        for result in ablations:
            cur.execute(
                """INSERT INTO wnba.feature_ablation_results
                   (ablation_id,feature_name,prop_type,calculated_at,sample_size,
                    mean_log_loss_gain,standard_error,confidence_lower,confidence_upper,
                    adjusted_alpha,verdict,detail)
                   VALUES (%s,%s,'all',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    result.feature_name,
                    at,
                    result.sample_size,
                    result.mean_log_loss_gain,
                    result.standard_error,
                    result.confidence_lower,
                    result.confidence_upper,
                    result.adjusted_alpha,
                    result.verdict,
                    Jsonb({"paired": True, "multiple_comparison": "bonferroni"}),
                ),
            )

    return TrustFitBatch(len(rows), policies, intervals, len(reliability), len(ablations))


def refresh_joint_game_simulations(*, now: datetime | None = None, seed: int = 20260810) -> int:
    at = now or datetime.now(UTC)
    written = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.game_id,f.model_run_id,f.projection_id,f.player_id,f.prop_type,
                      f.mean,f.stddev,
                      f.projected_minutes,f.line,d.side,p.full_name,
                      coalesce(t.abbreviation,t.full_name,'unknown') AS team,
                      coalesce((fs.features->>'minutes_std')::double precision,3.0) AS minutes_std,
                      coalesce((fs.features->>'start_probability')::double precision,0.5)
                        AS start_probability,
                      coalesce((fs.features->>'expected_possessions')::double precision,80.0)
                        AS expected_pace,
                      coalesce((fs.features->>'blowout_probability')::double precision,0.15)
                        AS blowout_probability
               FROM wnba.stat_forecasts f
               JOIN wnba.decision_episodes d
                 ON d.quote_id=f.quote_id AND d.model_run_id=f.model_run_id
               JOIN wnba.players p USING(player_id)
               LEFT JOIN wnba.feature_snapshots fs USING(feature_snapshot_id)
               LEFT JOIN LATERAL (
                 SELECT l.team_id FROM wnba.player_game_lines l
                 JOIN wnba.games g USING(game_id)
                 WHERE l.player_id=f.player_id AND l.system_to IS NULL
                 ORDER BY g.scheduled_tipoff DESC LIMIT 1
               ) latest_team ON true
               LEFT JOIN wnba.teams t ON t.team_id=latest_team.team_id
               WHERE f.expires_at>%s
               ORDER BY f.game_id,f.model_run_id,p.full_name,f.prop_type""",
            (at,),
        )
        grouped: dict[tuple[UUID, UUID], list[dict[str, Any]]] = defaultdict(list)
        for row in cur.fetchall():
            grouped[(UUID(str(row["game_id"])), UUID(str(row["model_run_id"])))].append(dict(row))
        for (game_id, run_id), rows in grouped.items():
            scenarios = [
                PlayerStatScenario(
                    key=str(row["projection_id"]),
                    team=str(row["team"]),
                    prop_type=str(row["prop_type"]),
                    mean=float(str(row["mean"])),
                    stddev=float(str(row["stddev"])),
                    projected_minutes=float(str(row["projected_minutes"])),
                    minutes_std=float(str(row["minutes_std"])),
                    line=float(str(row["line"])),
                    side=str(row["side"]),
                    starter_probability=float(str(row["start_probability"])),
                    player_id=str(row["player_id"]),
                )
                for row in rows
            ]
            result = simulate_joint_game(
                scenarios,
                expected_pace=float(str(rows[0]["expected_pace"])),
                blowout_probability=float(str(rows[0]["blowout_probability"])),
                simulations=10_000,
                seed=seed ^ (game_id.int & 0x7FFFFFFF),
            )
            cur.execute(
                """INSERT INTO wnba.joint_game_simulations
                   (simulation_id,game_id,model_run_id,simulated_at,random_seed,simulations,
                    player_keys,covariance,correlation,scenario_summary)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (game_id,model_run_id) DO NOTHING""",
                (
                    uuid4(),
                    game_id,
                    run_id,
                    at,
                    result.seed,
                    result.simulations,
                    list(result.keys),
                    Jsonb(result.covariance),
                    Jsonb(result.correlation),
                    Jsonb(result.scenario_summary),
                ),
            )
            written += int(cur.rowcount > 0)
    return written
