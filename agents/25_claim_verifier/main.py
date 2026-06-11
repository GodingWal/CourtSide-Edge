"""Agent 25: Claim Verifier (P0-2, P0-3, P2-1).

Subscribes picks.narrated and gates every narrative before it can reach the
publisher:

1. Lineage: the pick_id must have been stamped by the validation gate — a
   message injected directly onto picks.narrated has no validated ancestor
   and is rejected (LINEAGE_VIOLATION).
2. Numeric scan: any digit sequence in the narrative absent from the input
   payload (within rounding tolerance) rejects with FABRICATED_NUMERIC.
3. Claim verification: factual claims (debut/rookie/return/injury/entities)
   must map to payload fields, else UNGROUNDED_CLAIM with the offending span.

Pass → picks.publishable. Fail → picks.rejected. Nothing else.
"""
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.picks.channels import (
    CHANNEL_PICKS_NARRATED,
    CHANNEL_PICKS_PUBLISHABLE,
    CHANNEL_PICKS_REJECTED,
    RECENT_REJECTED_KEY,
    has_validated_ancestor,
)
from shared.picks.claims import verify_claims
from shared.picks.models import NarrativePayload, pick_from_message
from shared.picks.numeric_scan import scan_narrative
from shared.picks.reason_codes import ReasonCode
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent25_ClaimVerifier")


class ClaimVerifier:
    def __init__(self, pubsub: RedisPubSub):
        self.pubsub = pubsub
        self.audit = AuditLogger()

    def _reject(self, pick_id: str, reason_codes: list[str], message: dict,
                violations: list[dict]) -> None:
        rejection = {
            "pick_id": pick_id,
            "reason_code": reason_codes[0] if reason_codes else None,
            "reason_codes": reason_codes,
            "violations": violations,
            "payload_snapshot": message,
            "stage": "claim_verification",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.pubsub.publish(CHANNEL_PICKS_REJECTED, rejection)
        self.pubsub.push_recent(RECENT_REJECTED_KEY, rejection)
        logger.warning("Rejected narrated pick %s: %s", pick_id, reason_codes)

    def handle_narrated(self, message: dict) -> None:
        pick_id = str((message.get("pick") or {}).get("pick_id", "unknown"))
        trace_id = message.get("trace_id") or generate_trace_id()

        if not has_validated_ancestor(self.pubsub.client, pick_id):
            self._reject(pick_id, [ReasonCode.LINEAGE_VIOLATION.value], message, [])
            return

        try:
            pick = pick_from_message(message["pick"])
            payload = NarrativePayload(**message["payload"])
            narrative = str(message["narrative"])
        except (ValidationError, KeyError, TypeError) as exc:
            self._reject(pick_id, [ReasonCode.SCHEMA_VIOLATION.value], message,
                         [{"error": str(exc)}])
            return

        violations = scan_narrative(narrative, payload, pick)
        violations += verify_claims(narrative, payload)
        codes = sorted({v["code"] for v in violations})

        self.audit.log_decision(
            trace_id=trace_id,
            agent_id="Agent_25",
            action="REJECT" if violations else "APPROVE",
            reason=",".join(codes) or "narrative grounded",
            input_payload={"pick_id": pick_id, "narrative": narrative},
            output_payload={"violations": violations},
            confidence=pick.hit_probability,
        )

        if violations:
            self._reject(pick_id, codes, message, violations)
            return

        self.pubsub.publish(CHANNEL_PICKS_PUBLISHABLE, {
            "pick": pick.model_dump(),
            "payload": payload.model_dump(),
            "narrative": narrative,
            "trace_id": trace_id,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
        })
        logger.info("Narrative verified for pick %s", pick_id)


def main():
    pubsub = RedisPubSub()
    verifier = ClaimVerifier(pubsub)
    logger.info("Agent 25 (Claim Verifier) started.")
    pubsub.subscribe(CHANNEL_PICKS_NARRATED, verifier.handle_narrated)
    try:
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
