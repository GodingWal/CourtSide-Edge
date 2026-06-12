"""Agent 24: Validation Gate (P2-1).

Dedicated mesh node between projection and narrative — no path to
publication bypasses it. Subscribes picks.raw; publishes picks.validated on
pass and picks.rejected (with reason codes) on fail. Passing pick_ids are
stamped in Redis so the claim verifier can enforce lineage. LEAN picks are
retained in the pick log for calibration but never travel downstream.

Also maintains the roster store (P0-4) from channel_roster_updates so the
pure validation function always sees a fresh player→team mapping.
"""
import os
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from shared import db as shared_db
from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.picks.breakeven import breakeven_probability
from shared.picks.calibration import log_pick
from shared.picks.channels import (
    CHANNEL_PICKS_RAW,
    CHANNEL_PICKS_REJECTED,
    CHANNEL_PICKS_VALIDATED,
    RECENT_REJECTED_KEY,
    mark_validated,
)
from shared.picks.config import load_config
from shared.picks.models import NarrativePayload, PickStatus, pick_from_message
from shared.picks.reason_codes import ReasonCode
from shared.picks.roster import RosterStore
from shared.picks.validation import validate_pick
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent24_ValidationGate")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")


class ValidationGate:
    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH,
                 config: dict | None = None):
        self.pubsub = pubsub
        self.db_path = db_path
        self.config = config or load_config()
        self.roster = RosterStore(pubsub.client)
        self.audit = AuditLogger()

    def on_roster_update(self, message: dict) -> None:
        player = message.get("player_name") or message.get("player")
        team = message.get("team")
        if player and team:
            self.roster.update(player, team,
                               status=message.get("injury_status", "ACTIVE"))

    def _reject(self, pick_id: str, reason_codes: tuple[str, ...],
                payload_snapshot: dict, details: dict | None = None) -> dict:
        rejection = {
            "pick_id": pick_id,
            "reason_code": reason_codes[0] if reason_codes else None,
            "reason_codes": list(reason_codes),
            "payload_snapshot": payload_snapshot,
            "details": details or {},
            "stage": "validation",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.pubsub.publish(CHANNEL_PICKS_REJECTED, rejection)
        self.pubsub.push_recent(RECENT_REJECTED_KEY, rejection)
        logger.warning("Rejected pick %s: %s", pick_id, reason_codes)
        return rejection

    def _log(self, pick, payload, status: str, reason_codes=()):
        try:
            with shared_db.transaction(self.db_path) as conn:
                log_pick(conn, pick, status, payload=payload,
                         reason_codes=tuple(reason_codes))
        except Exception as exc:  # logging must never block the gate
            logger.error("pick_log write failed for %s: %s", pick.pick_id, exc)

    def handle_raw(self, message: dict) -> None:
        pick_data = dict(message.get("pick") or {})
        payload_data = message.get("payload")
        pick_id = str(pick_data.get("pick_id", "unknown"))
        trace_id = message.get("trace_id") or generate_trace_id()

        try:
            if "breakeven_probability" not in pick_data:
                pick_data["breakeven_probability"] = breakeven_probability(
                    pick_data.get("book", "prizepicks"),
                    pick_data.get("entry_type", "power"),
                    int(pick_data.get("legs", 3)),
                    self.config,
                )
            pick = pick_from_message(pick_data)
            payload = NarrativePayload(**payload_data) if payload_data else None
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            self._reject(pick_id, (ReasonCode.SCHEMA_VIOLATION.value,),
                         message, {"error": str(exc)})
            return

        result = validate_pick(pick, payload, config=self.config,
                               roster=self.roster.team_mapping())
        self._log(pick, payload, result.status.value, result.reason_codes)
        self.audit.log_decision(
            trace_id=trace_id,
            agent_id="Agent_24",
            action="APPROVE" if result.passed else "REJECT",
            reason=",".join(result.reason_codes) or "all gates passed",
            input_payload=message,
            output_payload=result.model_dump(),
            confidence=pick.hit_probability,
        )

        if result.status == PickStatus.PUBLISHABLE:
            mark_validated(self.pubsub.client, pick.pick_id)
            self.pubsub.publish(CHANNEL_PICKS_VALIDATED, {
                "pick": pick.model_dump(),
                "payload": payload.model_dump() if payload else None,
                "flags": list(result.flags),
                "trace_id": trace_id,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "ts": time.time(),
            })
            logger.info("Validated pick %s (%s %s %s)", pick.pick_id,
                        pick.recommendation.value, pick.player, pick.stat)
        else:
            # LEAN is terminal for this run too: retained in the pick log for
            # calibration, never delivered to any downstream agent.
            self._reject(pick.pick_id, result.reason_codes,
                         {"pick": pick.model_dump(),
                          "payload": payload.model_dump() if payload else None,
                          "status": result.status.value},
                         result.details)


def main():
    pubsub = RedisPubSub()
    gate = ValidationGate(pubsub)
    logger.info("Agent 24 (Validation Gate) started.")
    pubsub.subscribe(CHANNEL_PICKS_RAW, gate.handle_raw)
    pubsub.subscribe("channel_roster_updates", gate.on_roster_update)
    try:
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
