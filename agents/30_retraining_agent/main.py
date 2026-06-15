"""Agent 30: Retraining Orchestrator.

Monitors model drift metrics (from Agent 15 and Agent 28), pick pipeline
quality signals, and backtest results (Agent 29). When degradation exceeds
configured thresholds, triggers a retraining advisory and optionally emits a
retraining request to the pipeline.

Publishes:
  - recent:retraining_advisories  → capped Redis list
  - channel_retraining_advisory   → Pub/Sub for downstream consumers

Writes:
  - agent_context_store (via ContextClient) for retraining state tracking

Env:
  RETRAIN_INTERVAL_MINUTES   sweep cadence (default 120)
  RETRAIN_MAE_THRESHOLD      trigger retraining when MAE > this (default 3.5)
  RETRAIN_BRIER_THRESHOLD    trigger retraining when Brier > this (default 0.25)
  RETRAIN_WIN_RATE_FLOOR     halt advisory when WR < this (default 0.45)
  PICKS_DB_PATH              pick log database (default ./data/hoopstats_wnba.db)
"""
import json
import os
import time
from datetime import datetime, timezone

from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.context_client import ContextClient
from shared.db import db_available, connect as db_connect
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent30_Retraining")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")
INTERVAL_MINUTES = float(os.getenv("RETRAIN_INTERVAL_MINUTES", "120"))
MAE_THRESHOLD = float(os.getenv("RETRAIN_MAE_THRESHOLD", "3.5"))
BRIER_THRESHOLD = float(os.getenv("RETRAIN_BRIER_THRESHOLD", "0.25"))
WIN_RATE_FLOOR = float(os.getenv("RETRAIN_WIN_RATE_FLOOR", "0.45"))

ADVISORIES_KEY = "recent:retraining_advisories"
context = ContextClient()


class RetrainingAgent:
    """Watches drift and performance signals. Recommends retraining when
    the model degrades beyond acceptable thresholds.
    """

    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH):
        self.pubsub = pubsub
        self.db_path = db_path
        self.audit = AuditLogger()

    def _get_drift_status(self) -> dict:
        try:
            if not db_available(self.db_path):
                return {}
            conn = db_connect(self.db_path)
            cursor = conn.execute(
                "SELECT context_value FROM agent_context_store "
                "WHERE agent_id = 'Agent_15' AND context_key = 'projection_calibration' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception as e:
            logger.warning("Could not fetch drift: %s", e)
        return {}

    def _get_meta_confidence(self) -> dict:
        try:
            if not db_available(self.db_path):
                return {}
            conn = db_connect(self.db_path)
            cursor = conn.execute(
                "SELECT context_value FROM agent_context_store "
                "WHERE agent_id = 'Agent_28' AND context_key = 'meta_confidence' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception as e:
            logger.warning("Could not fetch meta confidence: %s", e)
        return {}

    def _get_recent_performance(self, days: int = 14) -> dict:
        try:
            if not db_available(self.db_path):
                return {}
            conn = db_connect(self.db_path)
            cutoff = int(time.time() - days * 86400)
            cursor = conn.execute(
                "SELECT result, profit_loss FROM bets WHERE result IS NOT NULL AND placed_at > ?",
                (cutoff,),
            )
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                return {}
            wins = sum(1 for r, _ in rows if r == "WIN")
            losses = sum(1 for r, _ in rows if r == "LOSS")
            pnl = sum(pl or 0 for _, pl in rows)
            total = wins + losses
            return {
                "win_rate": round(wins / max(total, 1), 3),
                "total": total,
                "wins": wins,
                "losses": losses,
                "pnl": round(pnl, 2),
            }
        except Exception as e:
            logger.warning("Could not fetch recent performance: %s", e)
            return {}

    def _evaluate(self, drift: dict, meta: dict, perf: dict) -> dict:
        mae = drift.get("mae", 0) or 0
        brier = drift.get("brier_score", 0) or 0
        win_rate = perf.get("win_rate", 0) or 0
        overall = meta.get("overall_score", 0) or 0

        triggers = []
        if mae > MAE_THRESHOLD:
            triggers.append(f"MAE {mae:.2f} > threshold {MAE_THRESHOLD}")
        if brier > BRIER_THRESHOLD:
            triggers.append(f"Brier {brier:.4f} > threshold {BRIER_THRESHOLD}")
        if 0 < win_rate < WIN_RATE_FLOOR:
            triggers.append(f"Win rate {win_rate:.0%} < floor {WIN_RATE_FLOOR}")
        if overall < 0.4 and overall > 0:
            triggers.append(f"Meta confidence {overall:.2f} critically low")

        severity = "NONE"
        if triggers:
            severity = "HIGH" if mae > 5.0 or brier > 0.30 or win_rate < 0.40 else "MEDIUM"

        return {
            "triggers": triggers,
            "severity": severity,
            "metrics": {
                "mae": mae,
                "brier": brier,
                "win_rate": win_rate,
                "overall_confidence": overall,
            },
            "recommended_action": "RETRAIN" if triggers else "MONITOR",
        }

    def run_assessment(self) -> dict:
        drift = self._get_drift_status()
        meta = self._get_meta_confidence()
        perf = self._get_recent_performance(days=14)
        evaluation = self._evaluate(drift, meta, perf)

        advisory = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evaluation": evaluation,
            "drift": drift,
            "meta_confidence": meta,
            "recent_performance": perf,
            "ts": time.time(),
        }

        self.pubsub.push_recent(ADVISORIES_KEY, advisory, cap=20)
        self.pubsub.publish("channel_retraining_advisory", advisory)

        context.write_context(
            game_id="system_wide",
            agent_id="Agent_30",
            context_key="retraining_advisory",
            context_value=advisory,
            confidence=0.9 if evaluation["severity"] != "NONE" else 0.7,
            ttl_seconds=86400,
        )

        self.audit.log_decision(
            trace_id=generate_trace_id(),
            agent_id="Agent_30",
            action="ABSTAIN",
            reason=f"retraining assessment: {evaluation['severity']} — {', '.join(evaluation['triggers']) or 'no triggers'}",
            output_payload=advisory,
        )

        logger.info(
            "Retraining assessment: severity=%s, action=%s, triggers=%s",
            evaluation["severity"],
            evaluation["recommended_action"],
            evaluation["triggers"],
        )
        return advisory

    def sweep(self) -> None:
        self.run_assessment()


def main():
    pubsub = RedisPubSub()
    agent = RetrainingAgent(pubsub)
    logger.info("Agent 30 (Retraining) started — interval %s min", INTERVAL_MINUTES)
    try:
        run_polling_loop(task=agent.sweep, interval=INTERVAL_MINUTES * 60, initial_delay=60.0, logger=logger)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
