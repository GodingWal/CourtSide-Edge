"""Agent 27: Rejection Triage Analyst.

A bounded agentic loop as an ANALYST, not a trader: when rejection volume on
the pick pipeline spikes, the local Hermes model investigates with read-only
tools (rejection counts/samples, pick log slices, feed freshness, agent
heartbeats) and writes a markdown diagnosis for humans.

Outputs go to `recent:triage_reports` and ./data/triage_report_latest.md
ONLY. This agent publishes to no picks.* channel and writes nothing the
publisher reads — its conclusions are input to humans, never to bets. With no
LLM reachable it degrades to a deterministic facts-only report.

Env:
  TRIAGE_INTERVAL_MINUTES  sweep cadence (default 30)
  TRIAGE_MAX_STEPS         tool-call budget per investigation (default 6)
  TRIAGE_ALWAYS            "1" to run every sweep even without a spike
  TRIAGE_LLM_TIMEOUT       per-LLM-call timeout seconds (default 30)
  PICKS_DB_PATH            pick log database (default ./data/hoopstats_wnba.db)
"""
import json
import os
import time
from datetime import datetime, timezone

from infrastructure.hermes.client import HermesClient
from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.picks.channels import CHANNEL_PICKS_REJECTED
from shared.picks.triage import (
    TriageLoop,
    TriageToolbox,
    baseline_report,
    detect_spikes,
    record_rejection,
)
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent27_RejectionTriage")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")
INTERVAL_MINUTES = float(os.getenv("TRIAGE_INTERVAL_MINUTES", "30"))
MAX_STEPS = int(os.getenv("TRIAGE_MAX_STEPS", "6"))
TRIAGE_ALWAYS = os.getenv("TRIAGE_ALWAYS", "0") == "1"
LLM_TIMEOUT = int(os.getenv("TRIAGE_LLM_TIMEOUT", "30"))

REPORTS_KEY = "recent:triage_reports"
REPORT_PATH = os.getenv("TRIAGE_REPORT_PATH", "./data/triage_report_latest.md")


class RejectionTriage:
    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH,
                 hermes: HermesClient | None = None, max_steps: int = MAX_STEPS):
        self.pubsub = pubsub
        self.db_path = db_path
        self.hermes = hermes
        self.max_steps = max_steps
        self.audit = AuditLogger()

    def on_rejected(self, event: dict) -> None:
        """picks.rejected listener: accumulate hour buckets for spike math."""
        record_rejection(self.pubsub.client, event)

    def _ask(self, question: str, system: str, temperature: float = 0.2) -> str:
        return self.hermes.ask(question, system=system, temperature=temperature,
                               timeout=LLM_TIMEOUT)

    def run_triage(self, focus: str) -> dict:
        toolbox = TriageToolbox(self.pubsub.client, self.db_path)
        if self.hermes is None or self.hermes.simulated:
            result_report = baseline_report(toolbox, focus)
            steps, completed, fallback = [], False, True
        else:
            result = TriageLoop(self._ask, toolbox,
                                max_steps=self.max_steps).run(focus)
            result_report = result.report
            steps = [{"step": s.step, "tool": s.tool, "args": s.args}
                     for s in result.steps]
            completed, fallback = result.completed, result.fallback_used

        record = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "focus": focus,
            "report": result_report,
            "steps": steps,
            "completed": completed,
            "fallback_used": fallback,
            "ts": time.time(),
        }
        self.pubsub.push_recent(REPORTS_KEY, record, cap=20)
        try:
            os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
            with open(REPORT_PATH, "w", encoding="utf-8") as fh:
                fh.write(result_report)
        except OSError as exc:
            logger.error("Could not write report artifact: %s", exc)

        self.audit.log_decision(
            trace_id=generate_trace_id(),
            agent_id="Agent_27",
            action="ABSTAIN",  # analysis only: this agent never approves bets
            reason=f"triage report ({'llm' if not fallback else 'baseline'}, "
                   f"{len(steps)} tool calls): {focus}",
            output_payload=record,
        )
        logger.info("Triage report generated (%s, %d steps): %s",
                    "fallback" if fallback else "llm", len(steps), focus)
        return record

    def sweep(self) -> None:
        """Periodic task: investigate when rejections spike (or always, if
        configured)."""
        spikes = detect_spikes(self.pubsub.client)
        if not spikes and not TRIAGE_ALWAYS:
            return
        if spikes:
            focus = ("Rejection spike(s) this hour: "
                     + json.dumps(spikes)
                     + ". Diagnose the most likely upstream cause for each.")
        else:
            focus = "Routine sweep: summarize current rejection mix and pipeline health."
        self.run_triage(focus)


def main():
    pubsub = RedisPubSub()
    triage = RejectionTriage(pubsub, hermes=HermesClient())
    logger.info("Agent 27 (Rejection Triage) started — analyst only, "
                "publishes no picks.")
    pubsub.subscribe(CHANNEL_PICKS_REJECTED, triage.on_rejected)
    try:
        run_polling_loop(task=triage.sweep, interval=INTERVAL_MINUTES * 60,
                         initial_delay=60.0, logger=logger)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
