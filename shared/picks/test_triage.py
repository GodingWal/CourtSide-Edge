"""Tests for the rejection-triage analyst: read-only toolbox, spike
detection, and the bounded agentic loop's guardrails (iteration cap, tool
whitelist, malformed-response abort, deterministic fallback)."""
import json
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from shared import db as shared_db
from shared.picks.calibration import log_pick
from shared.picks.channels import RECENT_REJECTED_KEY
from shared.picks.test_validation import make_pick
from shared.picks.triage import (
    TriageLoop,
    TriageToolbox,
    baseline_report,
    detect_spikes,
    record_rejection,
)

NOW = datetime(2026, 6, 12, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "picks.db")
    with shared_db.transaction(path) as conn:
        log_pick(conn, make_pick(pick_id="a"), "REJECTED",
                 reason_codes=("EDGE_SIGN_MISMATCH",), logged_at=NOW.isoformat())
        log_pick(conn, make_pick(pick_id="b"), "REJECTED",
                 reason_codes=("STALE_INJURY_DATA",), logged_at=NOW.isoformat())
        log_pick(conn, make_pick(pick_id="c"), "PUBLISHED",
                 logged_at=NOW.isoformat())
        log_pick(conn, make_pick(pick_id="old"), "REJECTED",
                 reason_codes=("EDGE_SIGN_MISMATCH",),
                 logged_at=(NOW - timedelta(hours=48)).isoformat())
    return path


@pytest.fixture
def toolbox(redis_client, db_path):
    return TriageToolbox(redis_client, db_path, now=NOW)


# ── Toolbox ──────────────────────────────────────────────────────────────────

def test_rejection_counts_respects_window(toolbox):
    out = toolbox.call("rejection_counts", {"hours": 24})
    assert out["counts"] == {"EDGE_SIGN_MISMATCH": 1, "STALE_INJURY_DATA": 1}
    out = toolbox.call("rejection_counts", {"hours": 72})
    assert out["counts"]["EDGE_SIGN_MISMATCH"] == 2


def test_pick_status_counts(toolbox):
    out = toolbox.call("pick_status_counts", {})
    assert out["counts"] == {"REJECTED": 2, "PUBLISHED": 1}


def test_sample_rejections_filters_by_code(toolbox, redis_client):
    for code in ("EDGE_SIGN_MISMATCH", "STALE_INJURY_DATA", "EDGE_SIGN_MISMATCH"):
        redis_client.lpush(RECENT_REJECTED_KEY, json.dumps(
            {"pick_id": "x", "reason_codes": [code]}))
    out = toolbox.call("sample_rejections", {"reason_code": "EDGE_SIGN_MISMATCH"})
    assert len(out["samples"]) == 2
    assert all("EDGE_SIGN_MISMATCH" in s["reason_codes"] for s in out["samples"])


def test_agent_heartbeats_report_age(toolbox, redis_client):
    redis_client.set("heartbeat:agent:24", str(int(NOW.timestamp() - 45)))
    out = toolbox.call("agent_heartbeats", {})
    assert out["seconds_since_heartbeat"]["24"] == 45


def test_injury_feed_freshness(toolbox, redis_client):
    redis_client.hset("roster:players", "caitlin clark", json.dumps(
        {"team": "Indiana Fever", "ts": NOW.timestamp() - 120}))
    out = toolbox.call("injury_feed_freshness", {})
    assert out["injury_report_cached"] is False
    assert out["roster_record_age_seconds"]["caitlin clark"] == 120


def test_unknown_tool_and_bad_args_are_observations_not_crashes(toolbox):
    assert "unknown tool" in toolbox.call("rm_rf", {})["error"]
    assert "bad arguments" in toolbox.call("rejection_counts",
                                           {"bogus_kwarg": 1})["error"]


# ── Spike detection ──────────────────────────────────────────────────────────

def test_spike_detection_against_trailing_baseline(redis_client):
    for offset in range(1, 25):  # steady baseline: 2/hour
        past = NOW - timedelta(hours=offset)
        for _ in range(2):
            record_rejection(redis_client,
                             {"reason_codes": ["STALE_INJURY_DATA"]}, now=past)
    for _ in range(10):  # current hour: 5x baseline
        record_rejection(redis_client,
                         {"reason_codes": ["STALE_INJURY_DATA"]}, now=NOW)
    spikes = detect_spikes(redis_client, now=NOW)
    assert spikes == [{"reason_code": "STALE_INJURY_DATA",
                       "current_hour": 10, "baseline_per_hour": 2.0}]


def test_no_spike_when_volume_matches_baseline(redis_client):
    for offset in range(0, 25):
        for _ in range(6):
            record_rejection(redis_client, {"reason_codes": ["BELOW_THRESHOLD"]},
                             now=NOW - timedelta(hours=offset))
    assert detect_spikes(redis_client, now=NOW) == []


# ── Agentic loop guardrails ──────────────────────────────────────────────────

def scripted_ask(responses):
    """Fake LLM that replays canned responses (HermesClient.ask signature)."""
    queue = list(responses)

    def ask(question, system, temperature=0.2):
        ask.prompts.append(question)
        assert queue, "loop asked more times than scripted"
        return queue.pop(0)

    ask.prompts = []
    return ask


def test_loop_investigates_then_concludes(toolbox):
    ask = scripted_ask([
        '{"action": "tool", "tool": "rejection_counts", "args": {"hours": 24}}',
        '{"action": "final", "report": "STALE_INJURY_DATA dominates; check Agent 2."}',
    ])
    result = TriageLoop(ask, toolbox, max_steps=4).run("why the spike?")
    assert result.completed and not result.fallback_used
    assert len(result.steps) == 1
    assert result.steps[0].tool == "rejection_counts"
    assert "STALE_INJURY_DATA" in result.steps[0].observation
    assert "Agent 2" in result.report
    # The observation was fed back into the next prompt.
    assert "rejection_counts" in ask.prompts[-1]


def test_loop_iteration_cap_forces_deterministic_conclusion(toolbox):
    never_concludes = scripted_ask(
        ['{"action": "tool", "tool": "agent_heartbeats", "args": {}}'] * 10)
    result = TriageLoop(never_concludes, toolbox, max_steps=3).run("focus")
    assert not result.completed and result.fallback_used
    assert len(result.steps) == 3  # hard cap on tool calls
    assert "deterministic baseline" in result.report


def test_loop_recovers_from_one_malformed_response(toolbox):
    ask = scripted_ask([
        "Sure! I think the problem is...",  # malformed: prose, no JSON
        '{"action": "final", "report": "Diagnosis after nudge."}',
    ])
    result = TriageLoop(ask, toolbox, max_steps=4).run("focus")
    assert result.completed
    assert "previous response was invalid" in ask.prompts[-1]


def test_loop_aborts_to_baseline_after_two_malformed_responses(toolbox):
    ask = scripted_ask(["not json", "still not json"])
    result = TriageLoop(ask, toolbox, max_steps=4).run("focus")
    assert result.fallback_used and not result.completed
    assert "deterministic baseline" in result.report


def test_loop_unknown_tool_is_an_observation(toolbox):
    ask = scripted_ask([
        '{"action": "tool", "tool": "place_bet", "args": {}}',
        '{"action": "final", "report": "done"}',
    ])
    result = TriageLoop(ask, toolbox, max_steps=4).run("focus")
    assert result.completed
    assert "unknown tool" in result.steps[0].observation


def test_baseline_report_contains_facts(toolbox):
    report = baseline_report(toolbox, "routine sweep")
    assert "EDGE_SIGN_MISMATCH: 1" in report
    assert "PUBLISHED: 1" in report
    assert "No LLM diagnosis" in report
