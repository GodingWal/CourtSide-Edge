"""Persistent champion/challenger evaluation and conservative drift detection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

from wnba_services.learning_loop.independence import dedupe_latest_per_market, summarise_sample

MINIMUM_SAMPLE = 100
CALIBRATION_WARNING = 0.08
CALIBRATION_BLOCKING = 0.12


@dataclass(frozen=True)
class EvaluationBatch:
    evaluations: int
    calibration_buckets: int
    attributions: int
    drift_incidents: int


def expected_calibration_error(rows: list[tuple[float, bool]]) -> float | None:
    """Weighted absolute calibration gap over ten fixed probability buckets."""
    if not rows:
        return None
    buckets: dict[int, list[tuple[float, bool]]] = {}
    for probability, outcome in rows:
        buckets.setdefault(min(9, int(probability * 10)), []).append((probability, outcome))
    return math.fsum(
        len(values)
        / len(rows)
        * abs(
            math.fsum(value[0] for value in values) / len(values)
            - math.fsum(float(value[1]) for value in values) / len(values)
        )
        for values in buckets.values()
    )


def classify_error(
    *,
    projected_minutes: float,
    actual_minutes: float,
    projected_stat: float,
    actual_stat: float,
    line_value: float | None,
) -> tuple[str, bool, float]:
    """Assign a deliberately coarse, testable first-pass error category."""
    minutes_error = actual_minutes - projected_minutes
    stat_error = actual_stat - projected_stat
    if abs(minutes_error) >= 5.0:
        return "minutes_projection", True, min(0.95, 0.55 + abs(minutes_error) / 40)
    if line_value is not None and line_value <= -1.0:
        return "market_movement", True, min(0.9, 0.6 + abs(line_value) / 20)
    if abs(stat_error) >= max(4.0, abs(projected_stat) * 0.3):
        return "rate_or_efficiency", True, 0.65
    return "random_variance", False, 0.6


def _probability_for_side(over_probability: float, side: str) -> float:
    return over_probability if side == "over" else 1.0 - over_probability


def evaluate_models(*, now: datetime | None = None) -> EvaluationBatch:
    """Score ensemble/components and append diagnostics; never promote a model."""
    at = now or datetime.now(UTC)
    evaluation_count = bucket_count = attribution_count = incident_count = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.*,o.*,mr.model_version_id
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               JOIN wnba.model_runs mr ON mr.model_run_id=d.model_run_id
               WHERE NOT o.was_voided AND NOT o.was_push"""
        )
        episodes = cur.fetchall()
        for episode in episodes:
            line_value = None
            if episode["closing_line"] is not None:
                direction = 1.0 if episode["side"] == "over" else -1.0
                line_value = direction * (
                    float(str(episode["closing_line"])) - float(str(episode["line"]))
                )
            category, avoidable, confidence = classify_error(
                projected_minutes=float(str(episode["projected_minutes"])),
                actual_minutes=float(str(episode["actual_minutes"])),
                projected_stat=float(str(episode["projected_mean"])),
                actual_stat=float(str(episode["actual_stat"])),
                line_value=line_value,
            )
            cur.execute(
                """INSERT INTO wnba.error_attributions
                   (attribution_id,episode_id,primary_error,avoidable,confidence,minutes_error,
                    stat_error,line_value,detail,attributed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    uuid4(),
                    episode["episode_id"],
                    category,
                    avoidable,
                    confidence,
                    float(str(episode["actual_minutes"]))
                    - float(str(episode["projected_minutes"])),
                    float(str(episode["actual_stat"])) - float(str(episode["projected_mean"])),
                    line_value,
                    Jsonb({"method": "rules-v0.1.0", "human_reviewed": False}),
                    at,
                ),
            )
            attribution_count += cur.rowcount

        model_ids = {UUID(str(row["model_version_id"])) for row in episodes}
        for model_id in model_ids:
            model_episodes = [
                row for row in episodes if UUID(str(row["model_version_id"])) == model_id
            ]
            series: dict[str, list[dict[str, Any]]] = {"ensemble": model_episodes}
            cur.execute(
                """SELECT fc.*,d.side,o.hit,o.brier AS ensemble_brier,o.log_loss AS ensemble_log,
                          d.episode_id,d.line,d.projected_mean,o.actual_stat,o.closing_line,
                          d.player_id,d.game_id,d.prop_type,d.forecast_timestamp
                   FROM wnba.forecast_components fc
                   JOIN wnba.stat_forecasts f ON f.projection_id=fc.projection_id
                   JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                     AND d.model_run_id=f.model_run_id
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   JOIN wnba.model_runs mr ON mr.model_run_id=d.model_run_id
                   WHERE mr.model_version_id=%s AND NOT o.was_voided AND NOT o.was_push""",
                (model_id,),
            )
            for row in cur.fetchall():
                series.setdefault(str(row["component_name"]), []).append(row)
            for component_name, all_rows in series.items():
                # One market resolves once, however many times it was forecast. Scoring every
                # revision separately would inflate the sample by the polling rate and make a
                # handful of nights look like a validated track record.
                shape = summarise_sample(all_rows)
                rows = dedupe_latest_per_market(all_rows)
                scored: list[tuple[float, bool]] = []
                briers: list[float] = []
                logs: list[float] = []
                line_values: list[float] = []
                absolute_errors: list[float] = []
                for row in rows:
                    probability = (
                        float(str(row["predicted_probability"]))
                        if component_name == "ensemble"
                        else _probability_for_side(
                            float(str(row["probability_over"])), str(row["side"])
                        )
                    )
                    hit = bool(row["hit"])
                    scored.append((probability, hit))
                    briers.append(brier_score(probability, int(hit)))
                    logs.append(log_loss(probability, int(hit)))
                    absolute_errors.append(
                        abs(float(str(row["actual_stat"])) - float(str(row["projected_mean"])))
                    )
                    if row["closing_line"] is not None:
                        direction = 1.0 if row["side"] == "over" else -1.0
                        line_values.append(
                            direction * (float(str(row["closing_line"])) - float(str(row["line"])))
                        )
                ece = expected_calibration_error(scored)
                # The gate is the effective sample, not the row count. Markets inside one game
                # share that game's state, so twenty props across three nights carry nowhere near
                # twenty markets' worth of independent evidence.
                sufficient = shape.effective >= MINIMUM_SAMPLE
                status = "insufficient_data"
                if sufficient:
                    status = (
                        "degraded" if ece is not None and ece > CALIBRATION_WARNING else "healthy"
                    )
                evaluation_id = uuid4()
                cur.execute(
                    """INSERT INTO wnba.model_evaluations
                       (evaluation_id,model_version_id,component_name,evaluated_at,sample_size,
                        brier,log_loss,calibration_error,mean_line_value,mean_absolute_error,
                        status,metrics)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        evaluation_id,
                        model_id,
                        component_name,
                        at,
                        len(scored),
                        None if not briers else math.fsum(briers) / len(briers),
                        None if not logs else math.fsum(logs) / len(logs),
                        ece,
                        None if not line_values else math.fsum(line_values) / len(line_values),
                        None
                        if not absolute_errors
                        else math.fsum(absolute_errors) / len(absolute_errors),
                        status,
                        Jsonb(
                            {
                                "minimum_sample": MINIMUM_SAMPLE,
                                "promotion_automatic": False,
                                "sample_shape": shape.to_payload(),
                                "sample_is_sufficient": sufficient,
                            }
                        ),
                    ),
                )
                evaluation_count += 1
                grouped: dict[int, list[tuple[float, bool]]] = {}
                for item in scored:
                    grouped.setdefault(min(9, int(item[0] * 10)), []).append(item)
                for bucket, values in grouped.items():
                    cur.execute(
                        """INSERT INTO wnba.calibration_snapshots
                           (snapshot_id,evaluation_id,probability_bucket,forecasts,
                            mean_predicted,observed_rate,brier,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            uuid4(),
                            evaluation_id,
                            bucket / 10,
                            len(values),
                            math.fsum(value[0] for value in values) / len(values),
                            math.fsum(float(value[1]) for value in values) / len(values),
                            math.fsum(brier_score(value[0], int(value[1])) for value in values)
                            / len(values),
                            at,
                        ),
                    )
                    bucket_count += 1
                if sufficient and ece is not None and ece > CALIBRATION_WARNING:
                    severity = "blocking" if ece > CALIBRATION_BLOCKING else "warning"
                    cur.execute(
                        """SELECT incident_id FROM wnba.drift_incidents
                           WHERE model_version_id=%s AND component_name=%s
                             AND metric='calibration_error' AND resolved_at IS NULL LIMIT 1""",
                        (model_id, component_name),
                    )
                    if cur.fetchone() is not None:
                        continue
                    cur.execute(
                        """INSERT INTO wnba.drift_incidents
                           (incident_id,model_version_id,component_name,metric,observed_value,
                            threshold,severity,automatic_response,opened_at)
                           VALUES (%s,%s,%s,'calibration_error',%s,%s,%s,%s,%s)""",
                        (
                            uuid4(),
                            model_id,
                            component_name,
                            ece,
                            CALIBRATION_WARNING,
                            severity,
                            "require_manual_review"
                            if severity == "blocking"
                            else "reduce_confidence",
                            at,
                        ),
                    )
                    incident_count += 1
                elif status == "healthy":
                    cur.execute(
                        """UPDATE wnba.drift_incidents SET resolved_at=%s
                           WHERE model_version_id=%s AND component_name=%s
                             AND metric='calibration_error' AND resolved_at IS NULL""",
                        (at, model_id, component_name),
                    )
    return EvaluationBatch(evaluation_count, bucket_count, attribution_count, incident_count)
