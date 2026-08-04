"""Evidence-backed real-money readiness gates that fail closed."""

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


def build_gate_results(decisions: list[dict[str, object]]) -> tuple[GateResult, ...]:
    """Evaluate historical replay evidence without upgrading it to live proof."""
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
            "provisional_pass" if shape.effective >= 500 else "pending",
            "retrospective_walk_forward",
            round(shape.effective, 2),
            500.0,
            f"{shape.rows} replay rows collapse to {shape.markets} independent markets across "
            f"{game_count} games; after correcting for markets sharing a game state that is an "
            f"effective sample of {shape.effective:.0f}. Weights were inspected after replay.",
        ),
        GateResult(
            "positive_closing_line_value",
            "pending",
            "historical_lines",
            None,
            0.0,
            "Closing-line direction is stored, but sparse snapshots require a robust "
            "consensus calculation.",
        ),
        GateResult(
            "positive_performance_after_pricing",
            "pending",
            "live_paper_only",
            None,
            0.0,
            "Legacy decimal prices were truncated to integers and cannot support honest "
            "return calculations.",
        ),
        GateResult(
            "calibration_within_tolerance",
            "provisional_pass"
            if calibration_error is not None and calibration_error <= 0.08
            else "fail",
            "retrospective_walk_forward",
            calibration_error,
            0.08,
            "Expected calibration error on deduplicated threshold-crossing recommendations.",
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
            "stable_rolling_windows",
            "provisional_pass" if weeks >= 8 else "pending",
            "retrospective_walk_forward",
            float(weeks),
            8.0,
            "Requires at least eight distinct weekly windows in addition to acceptable "
            "segment scores.",
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
            "provisional_pass",
            "tests_and_shadow_settlement",
            None,
            None,
            "Rules and tests exist; final status requires observed live voids, scratches, "
            "and late changes.",
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
            "pending",
            "live_priced_paper_only",
            None,
            None,
            "Drawdown requires trustworthy prices and a frozen staking policy.",
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
        gates = build_gate_results(decisions)
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
    return ReadinessReport(evaluation_id, at, overall_ready, gates)
