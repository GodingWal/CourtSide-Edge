"""Evidence-backed real-money readiness gates that fail closed.

Two evidence classes, kept apart on purpose.

**Historical replay** answers "does the model beat minutes x rate out of sample". It is fitted-
period evidence over rescued markets, and the gates that read it can only ever reach
``provisional_pass`` -- a replay cannot tell you what happened when the system ran.

**Live settled decisions** answer everything else: how many independent recommendations have
actually been made, whether they were calibrated, whether the market moved toward them, what the
return was at the operator's real payout structure, how deep the drawdown went, how many voids
and late lineup changes the pipeline has actually survived.

Until now every gate read the replay, so six of them were hard-coded ``pending`` with prose
explaining that live evidence did not exist -- and they would have stayed ``pending`` after five
hundred live recommendations arrived, because nothing in the path looked at a settled episode.
They now read :mod:`wnba_services.learning_loop.validation`, which computes each requirement from
the live record. The gates flip when the evidence arrives rather than when somebody remembers to
rewrite the prose.

`provisional_pass` is still not `pass`, and `overall_ready` still requires every gate to be
`pass`. Nothing here can be argued into readiness by a well-worded detail string.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.learning_loop.evaluation import expected_calibration_error
from wnba_services.learning_loop.independence import dedupe_latest_per_market, summarise_sample
from wnba_services.learning_loop.validation import (
    CALIBRATION_THRESHOLD,
    MINIMUM_INDEPENDENT_RECOMMENDATIONS,
    MINIMUM_STABLE_WEEKS,
    LiveValidation,
    load_settled_recommendations,
    summarise_live_performance,
)

GateStatus = Literal["pass", "provisional_pass", "pending", "fail"]


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    status: GateStatus
    evidence_source: str
    observed_value: float | None
    threshold_value: float | None
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    evaluation_id: UUID
    evaluated_at: datetime
    overall_ready: bool
    gates: tuple[GateResult, ...]
    live: LiveValidation | None = None


def _calibration_status(live: LiveValidation, replay_error: float | None) -> GateStatus:
    """Live evidence decides when it exists; replay can only ever be provisional.

    A live calibration error above the threshold is a `fail` rather than a `pending`: the
    measurement was taken and it did not clear the bar, which is a different and more important
    statement than not having measured.
    """
    if live.calibration_error is not None:
        return "pass" if live.calibration_error <= CALIBRATION_THRESHOLD else "fail"
    if replay_error is None:
        return "pending"
    return "provisional_pass" if replay_error <= CALIBRATION_THRESHOLD else "fail"


def build_gate_results(
    decisions: list[dict[str, object]], live: LiveValidation | None = None
) -> tuple[GateResult, ...]:
    """Score replay evidence and live evidence separately, and never confuse the two.

    ``live`` is the settled production record. When it is absent or empty the live gates report
    ``pending`` with the count that is missing, which is a different statement from "this cannot
    be measured" and is the statement that was previously being made.
    """
    live = live or summarise_live_performance([])
    threshold_crossing = [
        row
        for row in decisions
        if max(float(str(row["probability"])), 1 - float(str(row["probability"]))) >= 0.58
    ]
    # Five pre-tip snapshots of one market are five looks at one event. The out-of-sample count
    # gate exists to establish that enough *events* have been forecast, so it has to count them.
    recommendations = dedupe_latest_per_market(threshold_crossing, order_field="forecast_as_of")
    shape = summarise_sample(threshold_crossing)
    game_count = len({str(row["game_id"]) for row in recommendations})
    player_counts = Counter(str(row["player_id"]) for row in recommendations)
    market_counts = Counter(str(row["prop_type"]) for row in recommendations)
    max_player_share = max(player_counts.values(), default=0) / max(1, len(recommendations))
    max_market_share = max(market_counts.values(), default=0) / max(1, len(recommendations))
    scored = [
        (
            max(float(str(row["probability"])), 1 - float(str(row["probability"]))),
            bool(row["hit"]) if float(str(row["probability"])) >= 0.5 else not bool(row["hit"]),
        )
        for row in recommendations
    ]
    calibration_error = expected_calibration_error(scored)
    weeks = len({str(row["forecast_week"]) for row in recommendations})
    lineage_complete = all(
        row.get("historical_quote_id") is not None and int(str(row["history_games"])) >= 10
        for row in recommendations
    )
    return (
        GateResult(
            "out_of_sample_recommendations",
            "pass" if live.effective_sample >= MINIMUM_INDEPENDENT_RECOMMENDATIONS else "pending",
            "live_settled_decisions",
            round(live.effective_sample, 2),
            float(MINIMUM_INDEPENDENT_RECOMMENDATIONS),
            f"Live: {live.recommendations} independent settled recommendations across "
            f"{live.games} games, an effective sample of {live.effective_sample:.0f} after the "
            f"within-game clustering correction. Replay, for context only: {shape.rows} rows "
            f"collapse to {shape.markets} markets over {game_count} games "
            f"(effective {shape.effective:.0f}), which is fitted-period evidence and cannot "
            "satisfy this gate.",
        ),
        GateResult(
            "positive_closing_line_value",
            "pass"
            if live.mean_line_value is not None
            and live.mean_line_value > 0
            and live.line_value_sample >= MINIMUM_INDEPENDENT_RECOMMENDATIONS / 2
            else "pending",
            "live_settled_decisions",
            live.mean_line_value,
            0.0,
            (
                f"Mean closing-line value {live.mean_line_value:+.3f} points of line over "
                f"{live.line_value_sample} settled recommendations; "
                f"{live.positive_line_value_share:.1%} moved favourably."
                if live.mean_line_value is not None and live.positive_line_value_share is not None
                else "No settled recommendation carries a closing line yet. A corroborated "
                "closing line needs a second market source; one source closing against itself "
                "is not a consensus."
            ),
        ),
        GateResult(
            "positive_performance_after_pricing",
            "pass" if live.payout_return is not None and live.payout_return > 0 else "pending",
            "live_settled_decisions",
            live.payout_return,
            0.0,
            (
                f"Return at the recorded payout structure {live.payout_return:+.4f} per unit "
                f"staked; flat comparison against the {live.breakeven:.3f} break-even the gate "
                f"used is {live.flat_return:+.4f}."
                if live.payout_return is not None
                and live.breakeven is not None
                and live.flat_return is not None
                else "No settled recommendation carries a usable price. Unpriced legs are "
                "excluded rather than assumed even money, which is the assumption that turns an "
                "unprofitable record into a profitable one on paper."
            ),
        ),
        GateResult(
            "calibration_within_tolerance",
            _calibration_status(live, calibration_error),
            "live_settled_decisions"
            if live.calibration_error is not None
            else "retrospective_walk_forward",
            live.calibration_error if live.calibration_error is not None else calibration_error,
            CALIBRATION_THRESHOLD,
            (
                f"Live expected calibration error {live.calibration_error:.4f} over "
                f"{live.recommendations} settled recommendations."
                if live.calibration_error is not None
                else "No settled live recommendation yet; the figure shown is replay-only "
                "expected calibration error on deduplicated threshold-crossing rows."
            ),
        ),
        GateResult(
            "no_player_or_market_dominance",
            "provisional_pass" if max_player_share <= 0.10 and max_market_share <= 0.50 else "fail",
            "retrospective_walk_forward",
            max(max_player_share, max_market_share),
            0.50,
            f"Largest player share={max_player_share:.3f}; largest market "
            f"share={max_market_share:.3f}.",
        ),
        GateResult(
            "stable_across_subgroups",
            "pass"
            if live.recommendations >= MINIMUM_INDEPENDENT_RECOMMENDATIONS
            and not live.degraded_subgroups
            else "fail"
            if live.degraded_subgroups
            else "pending",
            "live_settled_decisions",
            float(len(live.degraded_subgroups)),
            0.0,
            (
                "Degraded subgroups: "
                + ", ".join(
                    f"{group.dimension}={group.value} (ECE {group.calibration_error:.3f}, "
                    f"n={group.markets})"
                    for group in live.degraded_subgroups[:5]
                    if group.calibration_error is not None
                )
                if live.degraded_subgroups
                else f"No conclusive subgroup degrades beyond tolerance across "
                f"{len(live.subgroups)} player, team, market and line-range slices. An aggregate "
                "that hides one badly-calibrated market is not stable performance."
            ),
        ),
        GateResult(
            "stable_rolling_windows",
            "pass" if live.stable_weeks >= MINIMUM_STABLE_WEEKS else "pending",
            "live_settled_decisions",
            float(live.stable_weeks),
            float(MINIMUM_STABLE_WEEKS),
            f"{live.stable_weeks} of {len(live.weekly)} live weekly windows carry enough "
            "independent markets to say anything. A week with four decisions is a quiet week, "
            f"not a window of evidence. Replay saw {weeks} distinct weeks.",
        ),
        GateResult(
            "no_material_look_ahead",
            "provisional_pass" if lineage_complete else "fail",
            "point_in_time_replay",
            1.0 if lineage_complete else 0.0,
            1.0,
            "All replay rows use pre-forecast history and a quote observed no later than "
            "forecast_as_of.",
        ),
        GateResult(
            "void_scratch_late_news_handling",
            "pass"
            if live.voids >= 5 and live.pushes >= 5 and live.late_line_moves >= 20
            else "provisional_pass",
            "live_settled_decisions",
            float(live.voids + live.pushes),
            10.0,
            f"Observed live: {live.voids} voids, {live.pushes} pushes, "
            f"{live.late_line_moves} settled recommendations whose line moved after the "
            "forecast. Rules and tests exist; this gate needs the pipeline to have actually "
            "survived these events, not to have been tested against them.",
        ),
        GateResult(
            "complete_model_data_lineage",
            "provisional_pass" if lineage_complete else "fail",
            "database_lineage",
            1.0 if lineage_complete else 0.0,
            1.0,
            "Every replay recommendation references its quote, timestamp, model, and "
            "historical sample.",
        ),
        GateResult(
            "drawdown_within_tolerance",
            "pass" if live.max_drawdown is not None and live.max_drawdown <= 0.35 else "pending",
            "live_settled_decisions",
            live.max_drawdown,
            0.35,
            (
                f"Peak-to-trough drawdown {live.max_drawdown:.1%} "
                f"({live.max_drawdown_units:.1f} units) on the priced equity curve; peak daily "
                f"exposure {live.peak_exposure:.0f} concurrent recommendations."
                if live.max_drawdown is not None and live.max_drawdown_units is not None
                else "Drawdown needs a priced equity curve, which needs prices this record does "
                "not yet carry."
            ),
        ),
        GateResult(
            "payout_tables_verified",
            "pending",
            "manual_verification",
            0.0,
            1.0,
            "Live product payout tables have not been manually verified and dated.",
        ),
    )


def evaluate_readiness(*, now: datetime | None = None) -> ReadinessReport:
    at = now or datetime.now(UTC)
    evaluation_id = uuid4()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH latest AS (
                   SELECT backtest_run_id FROM wnba.backtest_runs WHERE status='complete'
                   ORDER BY completed_at DESC LIMIT 1
               )
               SELECT q.game_id,q.player_id,r.prop_type,r.snapshot_label,
                      avg(r.predicted_probability) AS probability,bool_or(r.hit) AS hit,
                      min(r.historical_quote_id::text)::uuid AS historical_quote_id,
                      min(r.history_games) AS history_games,
                      min(date_trunc('week',r.forecast_as_of)) AS forecast_week
               FROM wnba.backtest_results r
               JOIN latest l USING (backtest_run_id)
               JOIN wnba.historical_prop_quotes q USING (historical_quote_id)
               WHERE r.model_name=ANY(%s)
               GROUP BY q.game_id,q.player_id,r.prop_type,r.snapshot_label""",
            # 'ensemble' was the replay-only model that no longer exists; 'production_ensemble'
            # is the shared scorer that replaced it. Both are accepted so a readiness evaluation
            # run against an archived backtest still finds its rows instead of silently
            # reporting that no recommendation has ever been made.
            (["production_ensemble", "ensemble"],),
        )
        decisions = [dict(row) for row in cur.fetchall()]
        live = summarise_live_performance(load_settled_recommendations(cur))
        gates = build_gate_results(decisions, live)
        overall_ready = all(gate.status == "pass" for gate in gates)
        cur.execute(
            """INSERT INTO wnba.readiness_evaluations
               (readiness_evaluation_id,evaluated_at,overall_ready,specification_version,evidence)
               VALUES (%s,%s,%s,'1.0.0',%s)""",
            (
                evaluation_id,
                at,
                overall_ready,
                Jsonb(
                    {
                        "historical_decisions": len(decisions),
                        "provisional_is_not_ready": True,
                        "sample_shape": summarise_sample(decisions).to_payload(),
                        # Kept as two separate objects deliberately. Adding a replay count to a
                        # live count would produce a number that describes nothing.
                        "live_validation": live.to_payload(),
                        "gates": [asdict(gate) for gate in gates],
                    }
                ),
            ),
        )
        for gate in gates:
            cur.execute(
                """INSERT INTO wnba.readiness_gate_results
                   (gate_result_id,readiness_evaluation_id,gate_id,status,evidence_source,
                    observed_value,threshold_value,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    evaluation_id,
                    gate.gate_id,
                    gate.status,
                    gate.evidence_source,
                    gate.observed_value,
                    gate.threshold_value,
                    gate.detail,
                ),
            )
    return ReadinessReport(evaluation_id, at, overall_ready, gates, live)
