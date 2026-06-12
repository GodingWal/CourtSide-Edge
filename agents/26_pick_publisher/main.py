"""Agent 26: Pick Publisher (P2-1, P2-2).

The ONLY subscription that reaches users is picks.publishable — this agent
subscribes to nothing else. Before publishing it re-checks the line against
the freshest snapshot (<= 15 min old, from the line history or Agent 1's
props:lines cache): an adverse move >= 0.5 re-runs the threshold gate against
the new line and demotes to LEAN when the pick no longer clears it. Every
published pick records both the capture line and the publication line so CLV
is computable per pick.
"""
import json
import os
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from shared import db as shared_db
from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.picks.calibration import log_pick
from shared.picks.channels import (
    CHANNEL_PICKS_PUBLISHABLE,
    CHANNEL_PICKS_REJECTED,
    RECENT_PUBLISHED_KEY,
)
from shared.picks.config import load_config
from shared.picks.line_tracking import LineHistory, revalidate_at_publish
from shared.picks.models import NarrativePayload, PickStatus, pick_from_message
from shared.picks.reason_codes import ReasonCode
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent26_PickPublisher")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")


class PickPublisher:
    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH,
                 config: dict | None = None):
        self.pubsub = pubsub
        self.db_path = db_path
        self.config = config or load_config()
        self.lines = LineHistory(pubsub.client, self.config)
        self.audit = AuditLogger()

    def _fresh_line(self, pick) -> float | None:
        max_age = self.config["adverse_line_move"]["snapshot_max_age_minutes"]
        snapshot = self.lines.latest(pick.book, pick.player, pick.stat,
                                     max_age_minutes=max_age)
        if snapshot:
            return float(snapshot["line"])
        # Agent 1 caches the latest scraped prop line per market.
        try:
            raw = self.pubsub.client.hget(
                "props:lines", f"{pick.player}|{pick.stat}|{pick.book}")
            if raw:
                return float(json.loads(raw).get("line"))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return None

    def _log(self, pick, payload, status: str, reason_codes=()):
        try:
            with shared_db.transaction(self.db_path) as conn:
                log_pick(conn, pick, status, payload=payload,
                         reason_codes=tuple(reason_codes))
        except Exception as exc:
            logger.error("pick_log write failed for %s: %s", pick.pick_id, exc)

    def handle_publishable(self, message: dict) -> None:
        trace_id = message.get("trace_id") or generate_trace_id()
        try:
            pick = pick_from_message(message["pick"])
            payload = (NarrativePayload(**message["payload"])
                       if message.get("payload") else None)
            narrative = message.get("narrative", "")
        except (ValidationError, KeyError, TypeError) as exc:
            logger.error("Malformed publishable message dropped: %s", exc)
            return

        capture_line = pick.line
        fresh_line = self._fresh_line(pick)
        revalidation = None
        if fresh_line is not None and fresh_line != capture_line:
            revalidation = revalidate_at_publish(pick, fresh_line, self.config)
            pick = revalidation["pick"]

        if revalidation and revalidation["demoted"]:
            self._log(pick, payload, PickStatus.LEAN.value,
                      (ReasonCode.ADVERSE_LINE_MOVE.value,))
            self.pubsub.publish(CHANNEL_PICKS_REJECTED, {
                "pick_id": pick.pick_id,
                "reason_code": ReasonCode.ADVERSE_LINE_MOVE.value,
                "reason_codes": [ReasonCode.ADVERSE_LINE_MOVE.value],
                "payload_snapshot": {"capture_line": capture_line,
                                     "publication_line": fresh_line},
                "stage": "publication",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            self.audit.log_decision(
                trace_id=trace_id, agent_id="Agent_26", action="REJECT",
                reason=f"Adverse line move {capture_line} -> {fresh_line}, "
                       "demoted to LEAN",
                input_payload=message,
                output_payload=revalidation["pick"].model_dump(),
            )
            logger.warning("Demoted pick %s on adverse move %s -> %s",
                           pick.pick_id, capture_line, fresh_line)
            return

        published = {
            "pick": pick.model_dump(),
            "narrative": narrative,
            "capture_line": capture_line,
            "publication_line": fresh_line if fresh_line is not None else capture_line,
            "flags": list(pick.flags),
            "grade": pick.grade,
            "trace_id": trace_id,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
        }
        self._log(pick, payload, "PUBLISHED")
        self.pubsub.push_recent(RECENT_PUBLISHED_KEY, published)
        self.audit.log_decision(
            trace_id=trace_id, agent_id="Agent_26", action="EXECUTE",
            reason="published", input_payload=message, output_payload=published,
            confidence=pick.hit_probability,
        )
        logger.info("Published pick %s (%s %s %s @ %s)", pick.pick_id,
                    pick.recommendation.value, pick.player, pick.stat, pick.line)


def main():
    pubsub = RedisPubSub()
    publisher = PickPublisher(pubsub)
    logger.info("Agent 26 (Pick Publisher) started — subscribed exclusively "
                "to %s.", CHANNEL_PICKS_PUBLISHABLE)
    pubsub.subscribe(CHANNEL_PICKS_PUBLISHABLE, publisher.handle_publishable)
    try:
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
