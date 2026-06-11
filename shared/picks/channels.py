"""Mesh channel topology for the pick pipeline (P2-1).

    projection agent  --publish-->  picks.raw
    validation agent  --subscribe-> picks.raw
    validation agent  --publish-->  picks.validated   (pass)
    validation agent  --publish-->  picks.rejected    (fail, with reason codes)
    narrative agent   --subscribe-> picks.validated
    narrative agent   --publish-->  picks.narrated
    claim verifier    --subscribe-> picks.narrated
    claim verifier    --publish-->  picks.publishable / picks.rejected
    publisher         --subscribe-> picks.publishable  <- ONLY path to users

Lineage: the validation agent stamps each passing pick_id in Redis; the claim
verifier refuses any picks.narrated message whose pick_id was never stamped,
so a message injected directly onto picks.narrated cannot reach users.
"""
CHANNEL_PICKS_RAW = "picks.raw"
CHANNEL_PICKS_VALIDATED = "picks.validated"
CHANNEL_PICKS_REJECTED = "picks.rejected"
CHANNEL_PICKS_NARRATED = "picks.narrated"
CHANNEL_PICKS_PUBLISHABLE = "picks.publishable"

RECENT_REJECTED_KEY = "recent:picks_rejected"
RECENT_PUBLISHED_KEY = "recent:picks_published"

_LINEAGE_KEY = "picks:lineage:validated:{pick_id}"
LINEAGE_TTL_SECONDS = 6 * 3600  # a slate's picks never outlive the day


def mark_validated(client, pick_id: str) -> None:
    client.set(_LINEAGE_KEY.format(pick_id=pick_id), "1", ex=LINEAGE_TTL_SECONDS)


def has_validated_ancestor(client, pick_id: str) -> bool:
    return bool(client.exists(_LINEAGE_KEY.format(pick_id=pick_id)))
