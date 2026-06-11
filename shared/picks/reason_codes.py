"""Reason-code registry for the pick validation pipeline.

Every rejection or demotion anywhere in the pipeline carries exactly one of
these codes so per-code counts can be tracked over time (validator drift /
upstream regressions surface as a moving distribution of codes).
"""
from enum import Enum


class ReasonCode(str, Enum):
    # validation stage
    EDGE_SIGN_MISMATCH = "EDGE_SIGN_MISMATCH"    # recommendation contradicts edge sign
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"        # message fails the Pick/payload schema
    STALE_INJURY_DATA = "STALE_INJURY_DATA"      # injury record older than the staleness window
    ROSTER_MISMATCH = "ROSTER_MISMATCH"          # player/team assertion contradicts roster table
    BELOW_THRESHOLD = "BELOW_THRESHOLD"          # hit probability under breakeven + margin (-> LEAN)
    BLOWOUT_RISK = "BLOWOUT_RISK"                # spread/win-prob escalated threshold not met

    # claim-verification stage
    FABRICATED_NUMERIC = "FABRICATED_NUMERIC"    # narrative contains a number absent from payload
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"        # narrative factual claim unmappable to payload
    LINEAGE_VIOLATION = "LINEAGE_VIOLATION"      # narrated pick has no picks.validated ancestor

    # publication stage
    ADVERSE_LINE_MOVE = "ADVERSE_LINE_MOVE"      # line moved against pick >= 0.5 since capture

    # entry build stage
    CORRELATION_EV_FAIL = "CORRELATION_EV_FAIL"  # joint EV below breakeven after correlation


# Flags that can ride along on a pick without rejecting it.
FLAG_BLOWOUT_RISK = ReasonCode.BLOWOUT_RISK.value
FLAG_ADVERSE_LINE_MOVE = ReasonCode.ADVERSE_LINE_MOVE.value
FLAG_UNDERDOG_GARBAGE_TIME = "UNDERDOG_GARBAGE_TIME_REVIEW"
