"""P3-2: golden regression suite.

Every observed production failure becomes a permanent fixture in
agents/golden_fixtures/ and is replayed through the exact validation +
claim-verification path on every commit touching the pick pipeline.

Convention: every production incident adds at least one golden case in the
fix PR (see shared/picks/README.md).
"""
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from shared.picks.claims import verify_claims  # noqa: E402
from shared.picks.models import (  # noqa: E402
    NarrativePayload,
    PickStatus,
    pick_from_message,
)
from shared.picks.numeric_scan import scan_narrative  # noqa: E402
from shared.picks.validation import validate_pick  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "golden_fixtures")
# Frozen reference instant: fixtures express injury ages relative to "now"
# so the suite never rots as wall-clock time passes.
NOW = datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)


def load_cases():
    cases = []
    for path in sorted(glob.glob(os.path.join(FIXTURES_DIR, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            cases.append(json.load(fh))
    assert cases, "golden fixtures directory must not be empty"
    return cases


def materialize_payload(payload_data: dict | None) -> NarrativePayload | None:
    """Resolve relative timestamps (last_updated_hours_ago) against NOW."""
    if payload_data is None:
        return None
    data = json.loads(json.dumps(payload_data))
    for record in data.get("injuries", []):
        hours_ago = record.pop("last_updated_hours_ago", None)
        if hours_ago is not None:
            record["last_updated"] = (NOW - timedelta(hours=hours_ago)).isoformat()
    return NarrativePayload(**data)


def run_pipeline(case: dict):
    """Replay one golden case through validation and, when it survives with a
    narrative attached, through the claim-verification stage."""
    pick = pick_from_message(case["pick"])
    payload = materialize_payload(case["payload"])

    result = validate_pick(pick, payload, now=NOW)
    status = result.status
    codes = set(result.reason_codes)
    flags = set(result.flags)

    narrative = case.get("narrative")
    if narrative and status == PickStatus.PUBLISHABLE:
        violations = scan_narrative(narrative, payload, pick)
        violations += verify_claims(narrative, payload)
        if violations:
            status = PickStatus.REJECTED
            codes |= {v["code"] for v in violations}
    return status, codes, flags


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_golden_case(case):
    status, codes, flags = run_pipeline(case)
    expect = case["expect"]

    assert set(expect["codes"]) <= codes, (
        f"{case['id']}: expected reason codes {expect['codes']}, got {sorted(codes)} "
        f"— {case['description']}"
    )
    assert status.value == expect["status"], (
        f"{case['id']}: expected status {expect['status']}, got {status.value}"
    )
    if expect.get("flags"):
        assert set(expect["flags"]) <= flags
    # The one invariant that matters: none of these ever reaches users.
    assert not expect["published"]
    assert status != PickStatus.PUBLISHABLE
