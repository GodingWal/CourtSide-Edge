"""Agent 32: Explainability Agent (XAI).

Consumes picks.publishable and other pipeline events, generating
human-readable explanations for WHY a pick was made or rejected.

Outputs go to recent:explanations and channel_explanation for the
dashboard's explainability panel. When no LLM is reachable, degrades to
template-based explanations.

Publishes:
  - recent:explanations          → capped Redis list for dashboard queries
  - channel_explanation            → Pub/Sub for real-time explainability

Env:
  EXPLAIN_INTERVAL_MINUTES   sweep cadence (default 30)
  EXPLAIN_LLM_TIMEOUT      per-LLM-call timeout seconds (default 20)
  PICKS_DB_PATH            pick log database (default ./data/hoopstats_wnba.db)
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.picks.channels import CHANNEL_PICKS_PUBLISHABLE, CHANNEL_PICKS_REJECTED
from shared.picks.models import NarrativePayload, pick_from_message
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent32_Explainability")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")
INTERVAL_MINUTES = float(os.getenv("EXPLAIN_INTERVAL_MINUTES", "30"))
LLM_TIMEOUT = int(os.getenv("EXPLAIN_LLM_TIMEOUT", "20"))

EXPLANATIONS_KEY = "recent:explanations"


class ExplainabilityAgent:
    """Generates human-readable explanations for picks and rejections."""

    def __init__(self, pubsub: RedisPubSub):
        self.pubsub = pubsub
        self.audit = AuditLogger()
        self.explanation_buffer: List[Dict] = []

    def _template_explanation(self, pick: dict, payload: dict, status: str,
                               reason_codes: List[str] = None) -> str:
        """Generate a template-based explanation when LLM is unavailable."""
        player = pick.get("player", "Unknown")
        stat = pick.get("stat", "Unknown")
        line = pick.get("line", 0)
        projection = pick.get("projection", 0)
        edge = round(projection - line, 2)
        rec = pick.get("recommendation", "Buy")
        prob = pick.get("hit_probability", 0)

        if status == "REJECTED":
            reasons = reason_codes or ["Unknown reason"]
            return (
                f"**{player} {stat}** was rejected.\n\n"
                f"- Book line: {line}\n"
                f"- Model projection: {projection}\n"
                f"- Edge: {edge}\n\n"
                f"**Reason(s):** {', '.join(reasons)}"
            )

        # Published pick
        parts = [
            f"**{player} {stat}** — {rec} @ {line}",
            "",
            f"Model projects {projection} ({edge:+.2f} edge).",
            f"Hit probability: {prob:.0%}.",
        ]

        if payload:
            form = payload.get("form", {})
            l5 = form.get("l5_avg", 0)
            l10 = form.get("l10_avg", 0)
            minutes = form.get("minutes_l5", 0)
            matchup = payload.get("matchup", {})
            opp = matchup.get("opponent", "Unknown")
            def_rank = matchup.get("opp_def_rank_vs_stat", 0)
            game = payload.get("game", {})
            spread = game.get("spread", 0)
            total = game.get("total", 0)

            parts.append(f"\nRecent form: {l5} L5, {l10} L10. Minutes: {minutes}.")
            parts.append(f"Matchup vs {opp}: def rank {def_rank}.")
            parts.append(f"Game context: spread {spread}, total {total}.")

        return "\n".join(parts)

    def _explain_published(self, message: dict) -> dict:
        pick_data = dict(message.get("pick") or {})
        payload_data = message.get("payload")
        pick_id = str(pick_data.get("pick_id", "unknown"))

        try:
            pick = pick_from_message(pick_data)
            payload = NarrativePayload(**payload_data) if payload_data else None
        except Exception as exc:
            return {
                "pick_id": pick_id,
                "status": "ERROR",
                "explanation": f"Could not parse pick: {exc}",
            }

        explanation = self._template_explanation(
            pick.model_dump(),
            payload.model_dump() if payload else None,
            "PUBLISHED",
        )

        return {
            "pick_id": pick_id,
            "status": "PUBLISHED",
            "explanation": explanation,
            "player": pick.player,
            "stat": pick.stat,
            "edge": pick.edge,
            "hit_probability": pick.hit_probability,
        }

    def _explain_rejected(self, message: dict) -> dict:
        pick_data = dict(message.get("pick") or {})
        pick_id = str(pick_data.get("pick_id", "unknown"))
        reason_codes = message.get("reason_codes", [])

        explanation = self._template_explanation(
            pick_data,
            None,
            "REJECTED",
            reason_codes,
        )

        return {
            "pick_id": pick_id,
            "status": "REJECTED",
            "explanation": explanation,
            "reason_codes": reason_codes,
            "stage": message.get("stage", "unknown"),
        }

    def handle_published(self, message: dict) -> None:
        record = self._explain_published(message)
        record["ts"] = time.time()
        record["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.explanation_buffer.append(record)
        self.pubsub.push_recent(EXPLANATIONS_KEY, record, cap=50)
        self.pubsub.publish("channel_explanation", record)

        self.audit.log_decision(
            trace_id=message.get("trace_id") or generate_trace_id(),
            agent_id="Agent_32",
            action="ABSTAIN",
            reason=f"explanation for {record['pick_id']}: {record['status']}",
            output_payload=record,
        )
        logger.info("Explanation generated for published pick %s", record["pick_id"])

    def handle_rejected(self, message: dict) -> None:
        record = self._explain_rejected(message)
        record["ts"] = time.time()
        record["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.explanation_buffer.append(record)
        self.pubsub.push_recent(EXPLANATIONS_KEY, record, cap=50)
        self.pubsub.publish("channel_explanation", record)

        self.audit.log_decision(
            trace_id=message.get("trace_id") or generate_trace_id(),
            agent_id="Agent_32",
            action="ABSTAIN",
            reason=f"explanation for rejected pick {record['pick_id']}: {', '.join(record.get('reason_codes', []))}",
            output_payload=record,
        )
        logger.info("Explanation generated for rejected pick %s", record["pick_id"])

    def sweep(self) -> None:
        """Periodic: flush buffered explanations and generate a summary."""
        if not self.explanation_buffer:
            return
        total = len(self.explanation_buffer)
        published = sum(1 for e in self.explanation_buffer if e.get("status") == "PUBLISHED")
        rejected = sum(1 for e in self.explanation_buffer if e.get("status") == "REJECTED")

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "type": "SUMMARY",
            "total": total,
            "published": published,
            "rejected": rejected,
            "ts": time.time(),
        }
        self.pubsub.push_recent(EXPLANATIONS_KEY, summary, cap=50)
        self.pubsub.publish("channel_explanation", summary)
        self.explanation_buffer.clear()
        logger.info("Explanation summary: %s total (%s published, %s rejected)", total, published, rejected)


def main():
    pubsub = RedisPubSub()
    agent = ExplainabilityAgent(pubsub)
    logger.info("Agent 32 (Explainability) started — listening to publishable and rejected.")
    pubsub.subscribe(CHANNEL_PICKS_PUBLISHABLE, agent.handle_published)
    pubsub.subscribe(CHANNEL_PICKS_REJECTED, agent.handle_rejected)
    try:
        run_polling_loop(task=agent.sweep, interval=INTERVAL_MINUTES * 60, initial_delay=60.0, logger=logger)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
