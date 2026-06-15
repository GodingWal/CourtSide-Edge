"""Agent 28: Meta-Agent (WNBA Prefrontal Cortex Layer)

Consumes outputs from the full agent mesh and produces:
  1. meta_confidence_score — How much should we trust the system right now?
  2. system_health_report — Which agents are drifting, stale, or failing?
  3. final_narrative — Human-readable synthesis of WHY we're betting (or not)
  4. calibration_advisory — Suggested adjustments to Agent 8 (Bankroll Sizer)

Publishes to: channel_meta_analysis
Writes to: agent_context_store (via ContextClient)
"""

import json
import os
import time
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import FastAPI
import uvicorn

from shared.base_agent import setup_logging, db_connect, db_available
from shared.context_client import ContextClient
from shared.redis_client import RedisPubSub, StreamConsumer

logger = setup_logging("Agent28_MetaAgent")

app = FastAPI(title="Agent 28: Meta-Agent & System Cortex")
context = ContextClient()

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

META_ANALYSIS_INTERVAL = int(os.getenv("META_ANALYSIS_INTERVAL", "30"))  # seconds
META_CONFIDENCE_THRESHOLD_HALT = float(os.getenv("META_HALT_THRESHOLD", "0.35"))
META_CONFIDENCE_THRESHOLD_HOT = float(os.getenv("META_HOT_THRESHOLD", "0.80"))
META_DRIFT_MAE_HALT = float(os.getenv("META_DRIFT_HALT", "5.0"))
META_DRIFT_MAE_HOT = float(os.getenv("META_DRIFT_HOT", "2.5"))


class SystemMode(Enum):
    HOT_STREAK = "hot_streak"
    NORMAL = "normal"
    COLD_STREAK = "cold_streak"
    HALT = "halt"


class MetaAgent:
    """The Meta-Agent sits above the agent mesh and asks:
    - Given what Agent 3 projected, Agent 5 observed about refs, Agent 11
      found as market edge, and Agent 15 detected as drift... should we
      actually pull the trigger?
    - Is the entire system operating in a regime where we should be more
      conservative?
    """

    def __init__(self):
        self.pubsub = RedisPubSub()
        self.stream = StreamConsumer(group_name="agent_28_group", consumer_name="agent_28_worker")
        self.recent_edges: List[Dict] = []
        self.recent_projections: List[Dict] = []
        self.recent_context: List[Dict] = []
        self.current_mode = SystemMode.NORMAL
        self.mode_since = datetime.now(timezone.utc).isoformat()
        self._running = True

    def start(self):
        """Subscribe to all relevant channels and start the stream consumer."""
        # Pub/Sub channels
        self.pubsub.subscribe("channel_true_projections", self._on_projection)
        self.pubsub.subscribe("channel_steam_alerts", self._on_steam_alert)
        self.pubsub.subscribe("channel_referee_context", self._on_context)
        self.pubsub.subscribe("channel_sentiment_context", self._on_context)
        self.pubsub.subscribe("channel_live_odds", self._on_context)
        logger.info("Meta-Agent subscribed to Pub/Sub channels")

        # Redis Stream — approved edges (consume non-destructively, just observe)
        self.stream.consume(
            "stream_approved_edges",
            self._on_approved_edge,
            block_ms=2000,
            batch_size=5
        )
        logger.info("Meta-Agent started consuming stream_approved_edges")

        # Start periodic meta-analysis loop
        analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        analysis_thread.start()
        logger.info("Meta-Agent analysis loop started")

    def _on_projection(self, payload: Dict):
        self.recent_projections.append({"ts": time.time(), "data": payload})
        self.recent_projections = self.recent_projections[-50:]

    def _on_approved_edge(self, msg_id: str, payload: Dict):
        self.recent_edges.append({"ts": time.time(), "data": payload})
        self.recent_edges = self.recent_edges[-50:]

    def _on_steam_alert(self, payload: Dict):
        logger.info("STEAM ALERT detected — triggering meta-analysis")
        self._run_meta_analysis(trigger="steam_alert")

    def _on_context(self, payload: Dict):
        self.recent_context.append({"ts": time.time(), "data": payload})
        self.recent_context = self.recent_context[-100:]

    def _analysis_loop(self):
        """Run meta-analysis periodically."""
        while self._running:
            try:
                self._run_meta_analysis(trigger="periodic")
            except Exception as e:
                logger.error(f"Meta-analysis loop error: {e}", exc_info=True)
            time.sleep(META_ANALYSIS_INTERVAL)

    def _run_meta_analysis(self, trigger: str = "periodic"):
        logger.info(f"Running meta-analysis [trigger={trigger}]")

        drift_status = self._get_drift_status()
        recent_bets = self._get_recent_bets()
        clv_summary = self._get_clv_summary()

        projection_trust = self._assess_projection_trust()
        market_trust = self._assess_market_trust()
        context_trust = self._assess_context_trust()
        execution_trust = self._assess_execution_trust(recent_bets, clv_summary)

        weights = {"projection": 0.35, "market": 0.25, "context": 0.20, "execution": 0.20}
        overall = (
            projection_trust * weights["projection"] +
            market_trust * weights["market"] +
            context_trust * weights["context"] +
            execution_trust * weights["execution"]
        )

        mode, reason = self._determine_mode(
            overall, projection_trust, market_trust,
            context_trust, execution_trust, drift_status
        )

        if mode != self.current_mode:
            logger.warning(f"MODE CHANGE: {self.current_mode.value} -> {mode.value} | {reason}")
            self.current_mode = mode
            self.mode_since = datetime.now(timezone.utc).isoformat()
            self._alert_mode_change(mode, reason)

        confidence = {
            "overall_score": round(overall, 3),
            "projection_trust": round(projection_trust, 3),
            "market_trust": round(market_trust, 3),
            "context_trust": round(context_trust, 3),
            "execution_trust": round(execution_trust, 3),
            "mode": mode.value,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        narrative = self._generate_narrative(confidence, drift_status, recent_bets)
        self._publish_meta_analysis(confidence, narrative)
        self._write_calibration_advisory(confidence, drift_status)

        logger.info(f"Meta-analysis complete: overall={confidence['overall_score']}, mode={mode.value}")

    def _assess_projection_trust(self) -> float:
        if not self.recent_projections:
            return 0.5
        lines = [p["data"].get("line", 0) for p in self.recent_projections[-10:]]
        if len(lines) < 2:
            return 0.7
        mean = sum(lines) / len(lines)
        variance = sum((l - mean) ** 2 for l in lines) / len(lines)
        if variance > 4.0:
            return 0.4
        elif variance > 2.0:
            return 0.6
        return 0.85

    def _assess_market_trust(self) -> float:
        if not self.recent_edges:
            return 0.5
        edges = [e["data"].get("edge_pct", 0) for e in self.recent_edges[-20:]]
        avg_edge = sum(edges) / len(edges) if edges else 0
        if avg_edge > 15:
            return 0.5
        elif avg_edge > 8:
            return 0.75
        return 0.65

    def _assess_context_trust(self) -> float:
        if not self.recent_context:
            return 0.4
        now = time.time()
        recent = [c for c in self.recent_context if now - c["ts"] < 3600]
        has_referee = any("referee" in str(c["data"]) for c in recent)
        has_sentiment = any("sentiment" in str(c["data"]) for c in recent)
        score = 0.5
        if has_referee:
            score += 0.2
        if has_sentiment:
            score += 0.15
        if len(recent) > 10:
            score += 0.15
        return min(score, 1.0)

    def _assess_execution_trust(self, recent_bets: List[Dict], clv_summary: Dict) -> float:
        score = 0.7
        avg_clv = clv_summary.get("avg_clv_pct", 0)
        if avg_clv > 5:
            score += 0.15
        elif avg_clv < -2:
            score -= 0.2
        if recent_bets:
            settled = [b for b in recent_bets if b.get("result")]
            if settled:
                wins = sum(1 for b in settled if b["result"] == "win")
                win_rate = wins / len(settled)
                if win_rate < 0.45:
                    score -= 0.15
                elif win_rate > 0.55:
                    score += 0.1
        return max(0.0, min(1.0, score))

    def _determine_mode(self, overall, proj, market, context, execution, drift):
        if overall < META_CONFIDENCE_THRESHOLD_HALT:
            return SystemMode.HALT, "Overall confidence critically low."
        if drift.get("mae", 0) > META_DRIFT_MAE_HALT:
            return SystemMode.HALT, f"Projection drift critical (MAE={drift['mae']})."
        if execution < 0.4:
            return SystemMode.HALT, "Execution track record degraded."
        if overall < 0.55 or execution < 0.55:
            return SystemMode.COLD_STREAK, "Reducing exposure."
        if overall > META_CONFIDENCE_THRESHOLD_HOT and execution > 0.75 and drift.get("mae", 10) < META_DRIFT_MAE_HOT:
            return SystemMode.HOT_STREAK, "Peak confidence."
        return SystemMode.NORMAL, "Normal parameters."

    def _generate_narrative(self, confidence, drift, recent_bets):
        parts = [
            f"## Meta-Analysis Report ({confidence['timestamp']})",
            f"**System Mode:** {confidence['mode'].upper()}",
            f"**Overall Confidence:** {confidence['overall_score']:.0%}",
            "",
            "### Component Scores",
            f"- Projection Trust: {confidence['projection_trust']:.0%}",
            f"- Market Trust: {confidence['market_trust']:.0%}",
            f"- Context Trust: {confidence['context_trust']:.0%}",
            f"- Execution Trust: {confidence['execution_trust']:.0%}",
            "",
            f"### Assessment",
            confidence["reason"],
        ]
        if drift.get("mae"):
            parts.append(f"\n**Drift:** MAE={drift['mae']:.2f}")
        settled = [b for b in recent_bets if b.get("result")]
        if settled:
            wins = sum(1 for b in settled if b["result"] == "win")
            parts.append(f"\n**Recent:** {wins}/{len(settled)} wins ({wins / len(settled):.0%})")
        return "\n".join(parts)

    def _get_drift_status(self) -> Dict:
        try:
            if not db_available(DB_PATH):
                return {}
            conn = db_connect(DB_PATH)
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
            logger.warning(f"Could not fetch drift: {e}")
        return {}

    def _get_recent_bets(self, limit=50) -> List[Dict]:
        try:
            if not db_available(DB_PATH):
                return []
            conn = db_connect(DB_PATH)
            cursor = conn.execute(
                "SELECT * FROM bets ORDER BY placed_at DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning(f"Could not fetch bets: {e}")
        return []

    def _get_clv_summary(self) -> Dict:
        try:
            if not db_available(DB_PATH):
                return {"avg_clv_pct": 0}
            conn = db_connect(DB_PATH)
            cursor = conn.execute(
                "SELECT AVG(clv_pct) as avg_clv FROM bets WHERE clv_pct IS NOT NULL"
            )
            row = cursor.fetchone()
            conn.close()
            return {"avg_clv_pct": row["avg_clv"] if row and row["avg_clv"] else 0}
        except Exception as e:
            logger.warning(f"Could not fetch CLV: {e}")
        return {"avg_clv_pct": 0}

    def _publish_meta_analysis(self, confidence: Dict, narrative: str):
        payload = {
            "agent_id": "Agent_28",
            "type": "meta_analysis",
            "confidence": confidence,
            "narrative": narrative,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.pubsub.publish("channel_meta_analysis", payload)

        # Also push to recent list for dashboard queries
        self.pubsub.push_recent("recent:meta_analysis", payload, cap=50)

    def _write_calibration_advisory(self, confidence: Dict, drift: Dict):
        advisory = {
            "kelly_adjustment": 1.0,
            "exposure_cap": 0.03,
            "reason": "Normal operation"
        }
        if confidence["mode"] == SystemMode.HOT_STREAK.value:
            advisory["kelly_adjustment"] = 1.0
            advisory["exposure_cap"] = 0.03
        elif confidence["mode"] == SystemMode.COLD_STREAK.value:
            advisory["kelly_adjustment"] = 0.5
            advisory["exposure_cap"] = 0.015
        elif confidence["mode"] == SystemMode.HALT.value:
            advisory["kelly_adjustment"] = 0.0
            advisory["exposure_cap"] = 0.0
            advisory["reason"] = "System halted by Meta-Agent"

        context.write_context(
            game_id="system_wide",
            agent_id="Agent_28",
            context_key="calibration_advisory",
            context_value=advisory,
            confidence=confidence["overall_score"],
            ttl_seconds=86400
        )

        # Also write the confidence score itself
        context.write_context(
            game_id="system_wide",
            agent_id="Agent_28",
            context_key="meta_confidence",
            context_value=confidence,
            confidence=confidence["overall_score"],
            ttl_seconds=86400
        )

    def _alert_mode_change(self, mode: SystemMode, reason: str):
        webhook_url = os.getenv("META_WEBHOOK_URL")
        if not webhook_url:
            return
        try:
            import urllib.request
            import urllib.error
            payload = json.dumps({
                "level": "warning" if mode in (SystemMode.COLD_STREAK, SystemMode.HALT) else "info",
                "mode": mode.value,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.error(f"Webhook alert failed: {e}")

    def close(self):
        self._running = False
        self.pubsub.close()
        self.stream.close()


# FastAPI health endpoint
meta_agent: Optional[MetaAgent] = None

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "mode": meta_agent.current_mode.value if meta_agent else "unknown",
        "mode_since": meta_agent.mode_since if meta_agent else None
    }

@app.get("/status")
def status_check():
    """Return current meta-analysis status for dashboard queries."""
    if not meta_agent:
        return {"status": "not_initialized"}
    return {
        "status": "healthy",
        "mode": meta_agent.current_mode.value,
        "mode_since": meta_agent.mode_since,
        "recent_projections": len(meta_agent.recent_projections),
        "recent_edges": len(meta_agent.recent_edges),
        "recent_context": len(meta_agent.recent_context)
    }


def main():
    global meta_agent
    meta_agent = MetaAgent()
    meta_agent.start()
    logger.info("Agent 28 (Meta-Agent) started on port 8020")
    try:
        uvicorn.run(app, host="0.0.0.0", port=8020)
    except KeyboardInterrupt:
        logger.info("Shutting down Meta-Agent...")
        meta_agent.close()


if __name__ == "__main__":
    main()
