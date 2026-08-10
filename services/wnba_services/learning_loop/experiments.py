"""Champion/challenger experiments: run a second model live, score it, never promote it.

The shape of this module is copied deliberately from the rule lifecycle, because the problem is
the same one. A rule proposal can be generated, backtested and marked ``backtested`` by scheduled
jobs, and then stops dead until a named human types their own name. An experiment can be opened,
run in shadow, evaluated and given a verdict by scheduled jobs, and then stops dead in exactly
the same place. :func:`evaluate_experiments` has no code path that writes ``promoted``; the
database will not accept one without an approver and a reason, and the only caller that supplies
those is a CLI command that takes a human name as a required argument.

Three properties make the comparison mean something:

**Paired.** Champion and challenger are scored on the same episodes -- the same player, game,
market, line and moment -- and an episode the challenger failed to score is dropped from *both*
sides. Comparing a challenger's average over the props it found easy against a champion's average
over everything is the oldest way to manufacture an improvement.

**Deduplicated.** One market resolves once however many times it was forecast, so the promotion
gate reads independent markets and an effective sample corrected for within-game clustering, not
the row count. A challenger that looks better across 3,000 rows from three nights has not shown
anything.

**Subgroup-checked.** An aggregate improvement that hides a market where the challenger is much
worse is not an improvement worth taking. Subgroups are reported per market, per line range and
per time-before-tip bucket, and any subgroup with enough sample where the challenger is materially
worse sets the verdict to ``subgroup_degradation`` regardless of how good the aggregate looks.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

from wnba_services.forecasting.challengers import (
    CHALLENGERS,
    ChallengerPrediction,
    challenger_by_name,
    challenger_names,
)
from wnba_services.forecasting.scoring import ScoredForecast, ScoringInputs
from wnba_services.learning_loop.evaluation import expected_calibration_error
from wnba_services.learning_loop.independence import (
    dedupe_latest_per_market,
    summarise_sample,
)

__all__ = [
    "MINIMUM_INDEPENDENT_SAMPLE",
    "ExperimentEvaluation",
    "ExperimentPromotion",
    "PairedMetrics",
    "abandon_experiment",
    "evaluate_experiments",
    "list_experiments",
    "open_experiment",
    "promote_challenger",
    "record_shadow_predictions",
    "rollback_promotion",
    "running_experiments",
]

PRIMARY_METRICS = ("log_loss", "brier", "mae", "line_value")

# The promotion gate, in independent markets rather than rows. Deliberately the same number the
# roadmap's paper-performance gate uses: a challenger should not be held to a looser standard
# than the champion was.
MINIMUM_INDEPENDENT_SAMPLE = 200

# A subgroup needs this many independent markets before its result is allowed to block anything.
# Below it, a "degradation" is one bad night.
MINIMUM_SUBGROUP_SAMPLE = 40

# Log-loss gaps below this are reported as no difference. Two models separated by 0.002 nats are
# separated by the seed.
MATERIAL_LOG_LOSS_GAIN = 0.005
SUBGROUP_DEGRADATION = 0.02

_LINE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("line_lt_6", 0.0, 6.0),
    ("line_6_to_12", 6.0, 12.0),
    ("line_12_to_20", 12.0, 20.0),
    ("line_gte_20", 20.0, math.inf),
)


# --------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PairedMetrics:
    """One side of a paired comparison, over the deduplicated episode set."""

    sample_size: int
    log_loss: float | None
    brier: float | None
    calibration_error: float | None
    mean_absolute_error: float | None
    mean_line_value: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "log_loss": _rounded(self.log_loss),
            "brier": _rounded(self.brier),
            "calibration_error": _rounded(self.calibration_error),
            "mean_absolute_error": _rounded(self.mean_absolute_error),
            "mean_line_value": _rounded(self.mean_line_value),
        }


@dataclass(frozen=True)
class ExperimentEvaluation:
    experiment_id: UUID
    challenger_name: str
    status: str
    verdict: str
    sample_size: int
    independent_sample_size: int
    champion: PairedMetrics
    challenger: PairedMetrics
    subgroups: list[dict[str, Any]]


@dataclass(frozen=True)
class ExperimentPromotion:
    experiment_id: UUID
    challenger_name: str
    status: str
    actor: str
    changed_at: datetime


def _rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else math.fsum(values) / len(values)


def _named_actor(value: str) -> str:
    actor = value.strip()
    if len(actor) < 2:
        raise ValueError("a named human actor is required")
    if len(actor) > 100:
        raise ValueError("actor must be at most 100 characters")
    return actor


def _required_reason(value: str) -> str:
    reason = value.strip()
    if len(reason) < 10:
        raise ValueError("an audit reason of at least 10 characters is required")
    if len(reason) > 1000:
        raise ValueError("audit reason must be at most 1000 characters")
    return reason


# --------------------------------------------------------------------------------------
# Opening an experiment
# --------------------------------------------------------------------------------------
def _challenger_version_id(name: str, version: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"courtside-edge:challenger:{name}:{version}")


def ensure_challenger_version(cur: Any, name: str) -> UUID:
    """Insert the challenger's ``model_versions`` row if it is not already there.

    Stage is ``shadow`` and stays ``shadow``: the only thing that moves a version to ``champion``
    is :func:`promote_challenger`, and the only thing that calls that is a human.
    """
    challenger = challenger_by_name(name)
    version_id = _challenger_version_id(challenger.name, challenger.version)
    cur.execute(
        """INSERT INTO wnba.model_versions
           (model_version_id,name,semver,target,stage,specification)
           VALUES (%s,%s,%s,'multi','shadow',%s) ON CONFLICT DO NOTHING""",
        (version_id, challenger.name, challenger.version, Jsonb(challenger.specification())),
    )
    return version_id


def open_experiment(
    challenger_name: str,
    *,
    opened_by: str,
    primary_metric: str = "log_loss",
    minimum_sample: int = MINIMUM_INDEPENDENT_SAMPLE,
    now: datetime | None = None,
) -> UUID:
    """Start a live shadow comparison against the current champion.

    Opening is not a privileged act -- it starts a model that writes to a table nothing else
    reads. It still records who opened it, because an experiment nobody remembers requesting is
    an experiment nobody will remember to close.
    """
    if challenger_name not in CHALLENGERS:
        raise ValueError(
            f"unknown challenger {challenger_name!r}; known: {', '.join(challenger_names())}"
        )
    if primary_metric not in PRIMARY_METRICS:
        raise ValueError(f"primary metric must be one of {', '.join(PRIMARY_METRICS)}")
    actor = _named_actor(opened_by)
    at = now or datetime.now(UTC)

    from wnba_services.forecasting.baseline import MODEL_ID as CHAMPION_ID

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM wnba.model_versions WHERE model_version_id=%s",
            (CHAMPION_ID,),
        )
        if cur.fetchone() is None:
            raise ValueError(
                "the champion model version has not been registered yet; run a forecast first"
            )
        challenger_id = ensure_challenger_version(cur, challenger_name)
        cur.execute(
            """SELECT experiment_id FROM wnba.experiments
               WHERE challenger_model_version_id=%s AND status='running'""",
            (challenger_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            raise ValueError(
                f"{challenger_name} already has a running experiment "
                f"({existing['experiment_id']}); evaluate or abandon it first"
            )
        experiment_id = uuid4()
        cur.execute(
            """INSERT INTO wnba.experiments
               (experiment_id,champion_model_version_id,challenger_model_version_id,started_at,
                primary_metric,evaluation,challenger_name,status,opened_by,minimum_sample,notes)
               VALUES (%s,%s,%s,%s,%s,'shadow',%s,'running',%s,%s,%s)""",
            (
                experiment_id,
                CHAMPION_ID,
                challenger_id,
                at,
                primary_metric,
                challenger_name,
                actor,
                max(1, minimum_sample),
                "live shadow comparison; promotion requires named human approval",
            ),
        )
    return experiment_id


def abandon_experiment(experiment_id: UUID, *, actor: str, reason: str) -> ExperimentPromotion:
    """Close a running experiment without a verdict, keeping every prediction it collected."""
    named = _named_actor(actor)
    audit = _required_reason(reason)
    at = datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE wnba.experiments
               SET status='abandoned',completed_at=%s,notes=notes||%s
               WHERE experiment_id=%s AND status='running'
               RETURNING challenger_name""",
            (at, f"; abandoned by {named}: {audit}", experiment_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"no running experiment {experiment_id}")
        name = str(row["challenger_name"])
    return ExperimentPromotion(experiment_id, name, "abandoned", named, at)


def list_experiments(*, limit: int = 50) -> list[dict[str, Any]]:
    """Every experiment, newest first, in the shape the CLI and the console both render.

    Model *names* rather than only version ids, because "0.4.0 versus 0.1.0" tells a reader
    nothing about which two ideas were compared.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.experiment_id,e.challenger_name,e.status,e.verdict,e.primary_metric,
                      e.started_at,e.evaluated_at,e.completed_at,e.sample_size,
                      e.independent_sample_size,e.minimum_sample,e.promoted,e.approved_by,
                      e.approved_at,e.promotion_reason,e.rolled_back_at,e.rolled_back_by,
                      e.rollback_reason,e.opened_by,e.metrics,e.subgroups,
                      e.champion_score,e.challenger_score,
                      champion.name AS champion_model,champion.semver AS champion_semver,
                      challenger.name AS challenger_model,challenger.semver AS challenger_semver
               FROM wnba.experiments e
               JOIN wnba.model_versions champion
                 ON champion.model_version_id=e.champion_model_version_id
               JOIN wnba.model_versions challenger
                 ON challenger.model_version_id=e.challenger_model_version_id
               ORDER BY e.started_at DESC LIMIT %s""",
            (max(1, limit),),
        )
        return [dict(row) for row in cur.fetchall()]


def running_experiments(cur: Any) -> list[dict[str, Any]]:
    """Experiments currently collecting shadow predictions."""
    cur.execute(
        """SELECT experiment_id,challenger_name,challenger_model_version_id
           FROM wnba.experiments WHERE status='running' ORDER BY started_at"""
    )
    return [dict(row) for row in cur.fetchall()]


# --------------------------------------------------------------------------------------
# Live shadow execution
# --------------------------------------------------------------------------------------
def record_shadow_predictions(
    cur: Any,
    experiments: Sequence[Mapping[str, Any]],
    *,
    episode_id: UUID,
    inputs: ScoringInputs,
    side: str,
    at: datetime,
    champion: ScoredForecast | None = None,
) -> int:
    """Score one episode with every running challenger and store the results.

    Called from inside the forecast run, with the same :class:`ScoringInputs` the champion was
    given. A challenger that raised is recorded as a failure with its exception text rather than
    skipped, because failure rate is one of the things the comparison is measuring -- a model
    that answers 60% of the board is not a drop-in replacement however good those answers are.

    Latency is measured around the prediction alone, excluding the database write, because the
    number that matters operationally is whether this family could run on the live board.
    """
    written = 0
    for experiment in experiments:
        name = str(experiment["challenger_name"])
        prediction: ChallengerPrediction | None = None
        failure: str | None = None
        started = time.perf_counter()
        try:
            prediction = challenger_by_name(name).predict(inputs, champion)
        except Exception as error:  # the failure is itself the measurement
            failure = f"{type(error).__name__}: {error}"[:500]
        latency_ms = (time.perf_counter() - started) * 1000.0

        probability_over = None if prediction is None else prediction.over
        probability_side = None if prediction is None else prediction.probability_for(side)
        cur.execute(
            """INSERT INTO wnba.challenger_predictions
               (prediction_id,experiment_id,model_version_id,episode_id,generated_at,prop_type,
                line,side,projected_mean,probability_over,probability_side,latency_ms,failed,
                failure_reason,diagnostics)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (experiment_id,episode_id,model_version_id) DO NOTHING""",
            (
                uuid4(),
                experiment["experiment_id"],
                experiment["challenger_model_version_id"],
                episode_id,
                at,
                inputs.prop_type,
                Decimal(str(inputs.line)),
                side,
                None if prediction is None else prediction.mean,
                probability_over,
                probability_side,
                latency_ms,
                prediction is None,
                failure,
                Jsonb({} if prediction is None else prediction.diagnostics),
            ),
        )
        written += cur.rowcount
    return written


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------
def _line_bucket(line: float) -> str:
    for name, low, high in _LINE_BUCKETS:
        if low <= line < high:
            return name
    return "line_gte_20"


def _lead_bucket(row: Mapping[str, Any]) -> str:
    tip = row.get("scheduled_tipoff")
    forecast_at = row.get("forecast_timestamp")
    if not isinstance(tip, datetime) or not isinstance(forecast_at, datetime):
        return "lead_unknown"
    hours = (tip - forecast_at).total_seconds() / 3600.0
    if hours < 1.0:
        return "lead_under_1h"
    if hours < 6.0:
        return "lead_1h_to_6h"
    return "lead_over_6h"


def _score(rows: Sequence[Mapping[str, Any]], key: str) -> PairedMetrics:
    scored: list[tuple[float, bool]] = []
    briers: list[float] = []
    logs: list[float] = []
    errors: list[float] = []
    line_values: list[float] = []
    for row in rows:
        probability = float(str(row[key]))
        hit = bool(row["hit"])
        scored.append((probability, hit))
        briers.append(brier_score(probability, int(hit)))
        logs.append(log_loss(probability, int(hit)))
        mean_key = "challenger_mean" if key == "challenger_probability" else "projected_mean"
        projected = row.get(mean_key)
        if projected is not None:
            errors.append(abs(float(str(row["actual_stat"])) - float(str(projected))))
        if row["closing_line"] is not None:
            direction = 1.0 if str(row["side"]) == "over" else -1.0
            line_values.append(
                direction * (float(str(row["closing_line"])) - float(str(row["line"])))
            )
    return PairedMetrics(
        sample_size=len(scored),
        log_loss=_mean(logs),
        brier=_mean(briers),
        calibration_error=expected_calibration_error(scored),
        mean_absolute_error=_mean(errors),
        mean_line_value=_mean(line_values),
    )


def _paired_log_loss_gain(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float | None]:
    """``(mean champion-minus-challenger log loss, t statistic)``.

    Paired rather than two independent means, because the two models saw the same props: the
    variance of the difference is far smaller than the variance of either series, and using the
    unpaired form would call every real improvement inconclusive.
    """
    differences = [
        log_loss(float(str(row["predicted_probability"])), int(bool(row["hit"])))
        - log_loss(float(str(row["challenger_probability"])), int(bool(row["hit"])))
        for row in rows
    ]
    if not differences:
        return 0.0, None
    mean = math.fsum(differences) / len(differences)
    if len(differences) < 3:
        return mean, None
    variance = math.fsum((value - mean) ** 2 for value in differences) / (len(differences) - 1)
    if variance <= 0.0:
        return mean, None
    return mean, mean / math.sqrt(variance / len(differences))


def _subgroup_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(("market", str(row["prop_type"])), []).append(row)
        groups.setdefault(("line_range", _line_bucket(float(str(row["line"])))), []).append(row)
        groups.setdefault(("time_before_tip", _lead_bucket(row)), []).append(row)

    reported: list[dict[str, Any]] = []
    for (dimension, value), members in sorted(groups.items()):
        gain, _ = _paired_log_loss_gain(members)
        champion = _score(members, "predicted_probability")
        challenger = _score(members, "challenger_probability")
        degraded = len(members) >= MINIMUM_SUBGROUP_SAMPLE and gain <= -SUBGROUP_DEGRADATION
        reported.append(
            {
                "dimension": dimension,
                "value": value,
                "sample_size": len(members),
                "log_loss_gain": _rounded(gain),
                "champion_log_loss": _rounded(champion.log_loss),
                "challenger_log_loss": _rounded(challenger.log_loss),
                "champion_calibration_error": _rounded(champion.calibration_error),
                "challenger_calibration_error": _rounded(challenger.calibration_error),
                "conclusive": len(members) >= MINIMUM_SUBGROUP_SAMPLE,
                "degraded": degraded,
            }
        )
    return reported


def _operational_metrics(cur: Any, experiment_id: UUID) -> dict[str, Any]:
    """Latency and failure rate over every prediction attempt, settled or not."""
    cur.execute(
        """SELECT latency_ms,failed,failure_reason FROM wnba.challenger_predictions
           WHERE experiment_id=%s""",
        (experiment_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return {"attempts": 0, "failures": 0, "failure_rate": None}
    latencies = sorted(float(str(row["latency_ms"])) for row in rows)
    failures = [row for row in rows if bool(row["failed"])]
    reasons: dict[str, int] = {}
    for row in failures:
        key = str(row["failure_reason"] or "unknown").split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1

    def percentile(fraction: float) -> float:
        index = min(len(latencies) - 1, max(0, round(fraction * (len(latencies) - 1))))
        return round(latencies[index], 3)

    return {
        "attempts": len(rows),
        "failures": len(failures),
        "failure_rate": round(len(failures) / len(rows), 4),
        "latency_ms_p50": percentile(0.50),
        "latency_ms_p95": percentile(0.95),
        "latency_ms_max": round(latencies[-1], 3),
        "failure_reasons": reasons,
    }


def _verdict(
    *,
    independent: float,
    minimum_sample: int,
    gain: float,
    statistic: float | None,
    subgroups: Sequence[Mapping[str, Any]],
) -> str:
    if independent < minimum_sample:
        return "insufficient_sample"
    if any(bool(group["degraded"]) for group in subgroups):
        return "subgroup_degradation"
    if abs(gain) < MATERIAL_LOG_LOSS_GAIN or statistic is None or abs(statistic) < 2.0:
        return "no_difference"
    return "challenger_better" if gain > 0 else "challenger_worse"


def evaluate_experiments(*, now: datetime | None = None) -> list[ExperimentEvaluation]:
    """Score every open experiment against settled outcomes. Promotes nothing, ever.

    The strongest verdict this function can reach is ``challenger_better``, which is a finding,
    not a decision. Everything that would follow from it -- swapping the stage of two model
    versions -- lives in :func:`promote_challenger` behind a named human.
    """
    at = now or datetime.now(UTC)
    evaluations: list[ExperimentEvaluation] = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT experiment_id,challenger_name,challenger_model_version_id,minimum_sample,
                      status
               FROM wnba.experiments
               WHERE status IN ('running','evaluated') ORDER BY started_at"""
        )
        for experiment in [dict(row) for row in cur.fetchall()]:
            experiment_id = UUID(str(experiment["experiment_id"]))
            cur.execute(
                """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,d.line,
                          d.predicted_probability,d.projected_mean,d.forecast_timestamp,
                          g.scheduled_tipoff,
                          o.hit,o.actual_stat,o.closing_line,
                          c.probability_side AS challenger_probability,
                          c.projected_mean AS challenger_mean
                   FROM wnba.challenger_predictions c
                   JOIN wnba.decision_episodes d ON d.episode_id=c.episode_id
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   JOIN wnba.games g ON g.game_id=d.game_id
                   WHERE c.experiment_id=%s AND NOT c.failed
                     AND NOT o.was_voided AND NOT o.was_push""",
                (experiment_id,),
            )
            paired = [dict(row) for row in cur.fetchall()]
            shape = summarise_sample(paired)
            rows = dedupe_latest_per_market(paired)
            champion = _score(rows, "predicted_probability")
            challenger = _score(rows, "challenger_probability")
            gain, statistic = _paired_log_loss_gain(rows)
            subgroups = _subgroup_rows(rows)
            minimum_sample = int(str(experiment["minimum_sample"]))
            verdict = _verdict(
                independent=shape.effective,
                minimum_sample=minimum_sample,
                gain=gain,
                statistic=statistic,
                subgroups=subgroups,
            )
            metrics = {
                "champion": champion.to_payload(),
                "challenger": challenger.to_payload(),
                "paired_log_loss_gain": _rounded(gain),
                "paired_t_statistic": _rounded(statistic),
                "material_gain_threshold": MATERIAL_LOG_LOSS_GAIN,
                "sample_shape": shape.to_payload(),
                "minimum_sample": minimum_sample,
                "operational": _operational_metrics(cur, experiment_id),
                "promotion_automatic": False,
            }
            # `status` stays 'evaluated' rather than becoming a decision. An experiment with a
            # `challenger_worse` verdict is still open for a human to close as rejected, because
            # "this model lost" is a conclusion somebody should sign.
            cur.execute(
                """UPDATE wnba.experiments
                   SET status='evaluated',evaluated_at=%s,sample_size=%s,
                       independent_sample_size=%s,metrics=%s,subgroups=%s,verdict=%s,
                       champion_score=%s,challenger_score=%s
                   WHERE experiment_id=%s""",
                (
                    at,
                    len(paired),
                    shape.markets,
                    Jsonb(metrics),
                    Jsonb(subgroups),
                    verdict,
                    champion.log_loss,
                    challenger.log_loss,
                    experiment_id,
                ),
            )
            evaluations.append(
                ExperimentEvaluation(
                    experiment_id=experiment_id,
                    challenger_name=str(experiment["challenger_name"]),
                    status="evaluated",
                    verdict=verdict,
                    sample_size=len(paired),
                    independent_sample_size=shape.markets,
                    champion=champion,
                    challenger=challenger,
                    subgroups=subgroups,
                )
            )
    return evaluations


# --------------------------------------------------------------------------------------
# Promotion and rollback -- humans only
# --------------------------------------------------------------------------------------
def promote_challenger_with_cursor(
    cur: Any,
    experiment_id: UUID,
    *,
    approved_by: str,
    reason: str,
    now: datetime | None = None,
) -> ExperimentPromotion:
    """Swap champion and challenger stages under a named human identity.

    Every precondition is checked against the stored evaluation rather than against an argument,
    so the caller cannot assert its way past one: the experiment must have been evaluated, the
    verdict must be ``challenger_better``, the independent sample must clear the experiment's own
    gate, and no subgroup may be flagged as degraded. The row lock makes the check atomic with
    the write, which matters because two approvers reading a stale verdict is exactly the race a
    promotion should not lose.
    """
    actor = _named_actor(approved_by)
    audit = _required_reason(reason)
    at = now or datetime.now(UTC)
    cur.execute(
        """SELECT status,verdict,promoted,independent_sample_size,minimum_sample,subgroups,
                  challenger_name,champion_model_version_id,challenger_model_version_id
           FROM wnba.experiments WHERE experiment_id=%s FOR UPDATE""",
        (experiment_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown experiment {experiment_id}")
    if bool(row["promoted"]):
        raise ValueError(f"experiment {experiment_id} has already been promoted")
    if str(row["status"]) != "evaluated":
        raise ValueError(f"experiment {experiment_id} must be evaluated before promotion")
    if str(row["verdict"]) != "challenger_better":
        raise ValueError(
            f"experiment {experiment_id} verdict is {row['verdict']!r}, not 'challenger_better'"
        )
    if int(row["independent_sample_size"]) < int(row["minimum_sample"]):
        raise ValueError(
            f"experiment {experiment_id} has {row['independent_sample_size']} independent "
            f"markets, below its gate of {row['minimum_sample']}"
        )
    degraded = [group for group in list(row["subgroups"] or []) if group.get("degraded")]
    if degraded:
        names = ", ".join(f"{group['dimension']}={group['value']}" for group in degraded)
        raise ValueError(f"experiment {experiment_id} shows subgroup degradation in {names}")

    cur.execute(
        """UPDATE wnba.experiments
           SET promoted=true,status='promoted',completed_at=%s,approved_by=%s,approved_at=%s,
               promotion_reason=%s
           WHERE experiment_id=%s AND status='evaluated' AND NOT promoted""",
        (at, actor, at, audit, experiment_id),
    )
    if cur.rowcount != 1:
        raise ValueError(f"experiment {experiment_id} changed under the approval; re-evaluate")
    cur.execute(
        "UPDATE wnba.model_versions SET stage='deprecated' WHERE model_version_id=%s",
        (row["champion_model_version_id"],),
    )
    cur.execute(
        "UPDATE wnba.model_versions SET stage='champion' WHERE model_version_id=%s",
        (row["challenger_model_version_id"],),
    )
    return ExperimentPromotion(
        experiment_id=experiment_id,
        challenger_name=str(row["challenger_name"]),
        status="promoted",
        actor=actor,
        changed_at=at,
    )


def promote_challenger(
    experiment_id: UUID,
    *,
    approved_by: str,
    reason: str,
    now: datetime | None = None,
) -> ExperimentPromotion:
    with connect() as conn, conn.cursor() as cur:
        return promote_challenger_with_cursor(
            cur, experiment_id, approved_by=approved_by, reason=reason, now=now
        )


def rollback_promotion(
    experiment_id: UUID,
    *,
    rolled_back_by: str,
    reason: str,
    now: datetime | None = None,
) -> ExperimentPromotion:
    """Restore the previous champion. The promotion stays in the record as history.

    Rollback is the reason promotion is survivable. Nothing here deletes the promotion row or
    edits its approver: a model that was promoted and then withdrawn is a different and more
    useful fact than a model that was never promoted.
    """
    actor = _named_actor(rolled_back_by)
    audit = _required_reason(reason)
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT status,promoted,challenger_name,champion_model_version_id,
                      challenger_model_version_id
               FROM wnba.experiments WHERE experiment_id=%s FOR UPDATE""",
            (experiment_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown experiment {experiment_id}")
        if str(row["status"]) != "promoted" or not bool(row["promoted"]):
            raise ValueError(f"experiment {experiment_id} is not promoted")
        cur.execute(
            """UPDATE wnba.experiments
               SET status='rolled_back',rolled_back_at=%s,rolled_back_by=%s,rollback_reason=%s
               WHERE experiment_id=%s AND status='promoted'""",
            (at, actor, audit, experiment_id),
        )
        cur.execute(
            "UPDATE wnba.model_versions SET stage='champion' WHERE model_version_id=%s",
            (row["champion_model_version_id"],),
        )
        cur.execute(
            "UPDATE wnba.model_versions SET stage='shadow' WHERE model_version_id=%s",
            (row["challenger_model_version_id"],),
        )
        name = str(row["challenger_name"])
    return ExperimentPromotion(experiment_id, name, "rolled_back", actor, at)
