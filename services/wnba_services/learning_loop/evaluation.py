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
    data_quality_score: float | None = None,
    predicted_probability: float | None = None,
    hit: bool | None = None,
) -> tuple[str, bool, float, str | None]:
    """Rank the candidate causes of one miss and name the best two.

    This replaces an ordered if-chain whose first branch was ``abs(minutes_error) >= 5``. Because
    every downstream stage -- hypotheses, candidate rules, research proposals -- groups by the
    category chosen here, that branch order was quietly deciding what the system was able to
    learn: a five-minute rotation swing is routine, so a large share of every miss was filed as
    ``minutes_projection`` whatever else had also gone wrong, and the proposal generator dutifully
    kept proposing minutes rules.

    Three changes:

    **Causes are scored, not ordered.** Each candidate produces a score normalised so that 1.0 is
    the old pass/fail threshold. The highest wins; the runner-up is returned as
    ``secondary_error`` so a cause sitting underneath a louder one is still visible to the
    reviewer.

    **The stat error is decomposed.** Minutes enter the forecast multiplicatively, so part of any
    stat miss is just the minutes miss carried through. Scoring efficiency on the *residual* --
    what is left after the minutes error explains what it can -- is what stops a correct
    per-minute rate from being blamed for a rotation change, and stops a genuine efficiency
    collapse from hiding behind one.

    **Two causes that had nowhere to go.** ``data_quality`` for a forecast built on inputs the
    quality gate already distrusted, and ``modeling`` for the case that matters most: a
    confidently wrong forecast with no identifiable input error. That last one used to land in
    ``random_variance`` and be marked unavoidable, which is how a systematic modelling bias gets
    filed as luck and generates nothing, forever.

    Returns ``(primary, avoidable, confidence, secondary)``. The full ranked cause list is
    available to the caller through ``classified_causes`` -- the record of an episode keeps
    every cause that cleared the threshold, with its score, rather than only the loudest two.
    """
    minutes_error = actual_minutes - projected_minutes
    stat_error = actual_stat - projected_stat

    # What the minutes miss alone would have produced, at the rate we projected.
    per_minute = projected_stat / projected_minutes if projected_minutes > 0 else 0.0
    residual = stat_error - minutes_error * per_minute

    # 1.0 == "as large as the old threshold". Anything below 1.0 is not a nameable cause.
    tolerance = max(4.0, abs(projected_stat) * 0.3)
    scores: dict[str, float] = {
        "minutes_projection": abs(minutes_error) / 5.0,
        "rate_or_efficiency": abs(residual) / tolerance,
    }
    if line_value is not None and line_value <= -1.0:
        scores["market_movement"] = abs(line_value) / 1.0
    if data_quality_score is not None and data_quality_score < 0.90:
        # 0.90 is the recommendation gate; below it the forecast was built on inputs the system
        # had already said it did not trust.
        scores["data_quality"] = (0.90 - data_quality_score) / 0.10

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    named = [(cause, score) for cause, score in ranked if score >= 1.0]

    if named:
        primary, score = named[0]
        secondary = named[1][0] if len(named) > 1 else None
        return primary, True, round(min(0.95, 0.55 + min(score, 4.0) / 10), 4), secondary

    # No input error large enough to name. If the forecast was nevertheless sharp and wrong, that
    # is a modelling failure and an avoidable one -- the inputs were fine and the answer was not.
    if predicted_probability is not None and hit is False:
        sharpness = abs(predicted_probability - 0.5) * 2
        if sharpness >= 0.50:
            return "modeling", True, round(min(0.9, 0.5 + sharpness / 4), 4), ranked[0][0]

    return "random_variance", False, 0.6, None


def error_contribution(
    *,
    actual_minutes: float,
    projected_minutes: float,
    actual_stat: float,
    projected_stat: float,
) -> dict[str, float]:
    """Decompose one stat miss into named shares, as the platform plan's attribution report.

    Minutes enter the forecast multiplicatively, so the minutes share is what the minutes
    miss alone would have produced at the projected per-minute rate; the efficiency share is
    the residual. Shares are of absolute error, so they sum to one when both point the same
    way and can exceed one when they offset -- which is itself information.
    """
    minutes_error = actual_minutes - projected_minutes
    per_minute = projected_stat / projected_minutes if projected_minutes > 0 else 0.0
    carried = minutes_error * per_minute
    residual = (actual_stat - projected_stat) - carried
    total = abs(carried) + abs(residual)
    if total == 0:
        return {"minutes": 0.0, "efficiency": 0.0}
    return {
        "minutes": round(abs(carried) / total, 3),
        "efficiency": round(abs(residual) / total, 3),
    }


def _causal_chain(cur: Any, episode: dict[str, Any], *, primary: str | None) -> list[str]:
    """Trace one attribution back through what the system knew, root cause last.

    The plan's example chain -- lost recommendation, projection too high, minutes too high,
    stale lineup source, delayed ingestion -- is the shape this produces. The first nodes
    come from the episode's own numbers; the last comes from the data-quality incidents the
    pipeline recorded around forecast time, which is where an engineering action attaches.
    """
    chain: list[str] = []
    if primary is not None:
        stat_error = float(str(episode["actual_stat"])) - float(str(episode["projected_mean"]))
        direction = "low" if stat_error > 0 else "high"
        chain.append(f"projection too {direction}")
        minutes_error = float(str(episode["actual_minutes"])) - float(
            str(episode["projected_minutes"])
        )
        if primary == "minutes_projection":
            chain.append(f"minutes too {'low' if minutes_error > 0 else 'high'}")
        chain.append(f"cause: {primary}")
    source = episode.get("source")
    forecast_at = episode.get("forecast_timestamp")
    if source is not None and forecast_at is not None:
        cur.execute(
            """SELECT code,detail FROM wnba.dq_incidents
               WHERE detected_at BETWEEN %s - interval '12 hours' AND %s + interval '12 hours'
                 AND (source=%s OR level='critical')
               ORDER BY detected_at DESC LIMIT 3""",
            (forecast_at, forecast_at, str(source)),
        )
        for incident in cur.fetchall():
            chain.append(f"data-quality incident: {incident['code']}")
    return chain


def causal_chain_payload(chain: list[str]) -> Jsonb:
    """Adapt a causal chain as JSONB, never as PostgreSQL's text-array syntax."""
    return Jsonb(chain)


def _probability_for_side(over_probability: float, side: str) -> float:
    return over_probability if side == "over" else 1.0 - over_probability


def evaluate_models(*, now: datetime | None = None) -> EvaluationBatch:
    """Score ensemble/components and append diagnostics; never promote a model."""
    at = now or datetime.now(UTC)
    evaluation_count = bucket_count = attribution_count = incident_count = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.episode_id,d.side,d.line,d.projected_mean,d.projected_minutes,
                      d.predicted_probability,d.data_quality_score,d.source,d.forecast_timestamp,
                      o.actual_stat,o.actual_minutes,o.hit,o.closing_line
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE NOT o.was_voided AND NOT o.was_push
                 AND NOT EXISTS (
                   SELECT 1 FROM wnba.error_attributions ea
                   WHERE ea.episode_id=d.episode_id)
               ORDER BY d.forecast_timestamp
               LIMIT 2000"""
        )
        episodes = cur.fetchall()
        for episode in episodes:
            line_value = None
            if episode["closing_line"] is not None:
                direction = 1.0 if episode["side"] == "over" else -1.0
                line_value = direction * (
                    float(str(episode["closing_line"])) - float(str(episode["line"]))
                )
            episode["error_contribution"] = error_contribution(
                actual_minutes=float(str(episode["actual_minutes"])),
                projected_minutes=float(str(episode["projected_minutes"])),
                actual_stat=float(str(episode["actual_stat"])),
                projected_stat=float(str(episode["projected_mean"])),
            )
            category, avoidable, confidence, secondary = classify_error(
                projected_minutes=float(str(episode["projected_minutes"])),
                actual_minutes=float(str(episode["actual_minutes"])),
                projected_stat=float(str(episode["projected_mean"])),
                actual_stat=float(str(episode["actual_stat"])),
                line_value=line_value,
                data_quality_score=float(str(episode["data_quality_score"])),
                predicted_probability=float(str(episode["predicted_probability"])),
                hit=bool(episode["hit"]),
            )
            chain = _causal_chain(cur, episode, primary=category)
            cur.execute(
                """INSERT INTO wnba.error_attributions
                   (attribution_id,episode_id,primary_error,secondary_error,avoidable,confidence,
                    minutes_error,stat_error,line_value,detail,causal_chain,attributed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    uuid4(),
                    episode["episode_id"],
                    category,
                    secondary,
                    avoidable,
                    confidence,
                    float(str(episode["actual_minutes"]))
                    - float(str(episode["projected_minutes"])),
                    float(str(episode["actual_stat"])) - float(str(episode["projected_mean"])),
                    line_value,
                    Jsonb(
                        {
                            "method": "scored-causes-v2",
                            "human_reviewed": False,
                            "secondary_errors": ([] if secondary is None else [secondary]),
                            "error_contribution": episode.get("error_contribution"),
                            "causal_chain": chain,
                        }
                    ),
                    causal_chain_payload(chain),
                    at,
                ),
            )
            attribution_count += cur.rowcount

        cur.execute(
            """SELECT DISTINCT mr.model_version_id
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               JOIN wnba.model_runs mr ON mr.model_run_id=d.model_run_id
               WHERE NOT o.was_voided AND NOT o.was_push"""
        )
        model_ids = {UUID(str(row["model_version_id"])) for row in cur.fetchall()}
        for model_id in model_ids:
            cur.execute(
                """SELECT DISTINCT ON (d.player_id,d.game_id,d.prop_type)
                          d.predicted_probability,d.side,o.hit,d.episode_id,d.line,
                          d.projected_mean,o.actual_stat,o.closing_line,d.player_id,d.game_id,
                          d.prop_type,d.forecast_timestamp
                   FROM wnba.decision_episodes d
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   JOIN wnba.model_runs mr ON mr.model_run_id=d.model_run_id
                   WHERE mr.model_version_id=%s AND NOT o.was_voided AND NOT o.was_push
                   ORDER BY d.player_id,d.game_id,d.prop_type,d.forecast_timestamp DESC""",
                (model_id,),
            )
            model_episodes = cur.fetchall()
            series: dict[str, list[dict[str, Any]]] = {"ensemble": model_episodes}
            cur.execute(
                """SELECT DISTINCT ON (
                          fc.component_name,d.player_id,d.game_id,d.prop_type)
                          fc.component_name,fc.probability_over,d.side,o.hit,d.episode_id,d.line,
                          d.projected_mean,o.actual_stat,o.closing_line,d.player_id,d.game_id,
                          d.prop_type,d.forecast_timestamp
                   FROM wnba.forecast_components fc
                   JOIN wnba.stat_forecasts f ON f.projection_id=fc.projection_id
                   JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                     AND d.model_run_id=f.model_run_id
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   JOIN wnba.model_runs mr ON mr.model_run_id=d.model_run_id
                   WHERE mr.model_version_id=%s AND NOT o.was_voided AND NOT o.was_push
                   ORDER BY fc.component_name,d.player_id,d.game_id,d.prop_type,
                            d.forecast_timestamp DESC""",
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
