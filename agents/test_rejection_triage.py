"""Agent 27 integration tests: rejection accumulation from picks.rejected,
report generation, and the structural guarantee that the analyst never
publishes to any picks.* channel."""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from shared.picks.triage import REJECTION_BUCKET_PREFIX  # noqa: E402

agent27 = importlib.import_module("agents.27_rejection_triage.main")


@pytest.fixture
def triage(fake_redis, tmp_path, monkeypatch):
    from shared.redis_client import RedisPubSub

    published = []

    def capture(self, channel, message):
        published.append((channel, message))
        self.client.publish(channel, json.dumps(message))

    monkeypatch.setattr(RedisPubSub, "publish", capture)
    monkeypatch.setattr(agent27, "REPORT_PATH",
                        str(tmp_path / "triage_report_latest.md"))

    simulated_hermes = MagicMock()
    simulated_hermes.simulated = True
    node = agent27.RejectionTriage(RedisPubSub(),
                                   db_path=str(tmp_path / "picks.db"),
                                   hermes=simulated_hermes)
    node.audit = MagicMock()
    node.published = published
    node.client = fake_redis
    return node


def test_rejected_events_accumulate_in_hour_buckets(triage):
    for _ in range(3):
        triage.on_rejected({"pick_id": "x",
                            "reason_codes": ["EDGE_SIGN_MISMATCH"]})
    keys = triage.client.keys(f"{REJECTION_BUCKET_PREFIX}*")
    assert len(keys) == 1
    assert triage.client.hgetall(keys[0]) == {"EDGE_SIGN_MISMATCH": "3"}
    assert triage.client.ttl(keys[0]) > 0


def test_sweep_is_quiet_without_a_spike(triage):
    triage.sweep()
    assert triage.client.lrange(agent27.REPORTS_KEY, 0, -1) == []


def test_spike_triggers_a_report(triage):
    for _ in range(10):
        triage.on_rejected({"pick_id": "x",
                            "reason_codes": ["STALE_INJURY_DATA"]})
    triage.sweep()
    reports = triage.client.lrange(agent27.REPORTS_KEY, 0, -1)
    assert len(reports) == 1
    record = json.loads(reports[0])
    assert "STALE_INJURY_DATA" in record["focus"]
    assert record["fallback_used"] is True  # no LLM reachable in tests
    assert "STALE_INJURY_DATA" in record["report"]
    with open(agent27.REPORT_PATH, encoding="utf-8") as fh:
        assert "Rejection triage" in fh.read()


def test_triage_never_publishes_to_picks_channels(triage):
    """The analyst/trader boundary: a full triage run touches no picks.*
    channel — its only outputs are the reports list and the file artifact."""
    for _ in range(10):
        triage.on_rejected({"pick_id": "x",
                            "reason_codes": ["FABRICATED_NUMERIC"]})
    triage.sweep()
    triage.run_triage("manual investigation")
    picks_channels = [ch for ch, _ in triage.published if ch.startswith("picks.")]
    assert picks_channels == []
    assert triage.published == []  # no pub/sub at all: push_recent only


def test_llm_path_records_steps(triage):
    hermes = MagicMock()
    hermes.simulated = False
    hermes.ask.side_effect = [
        '{"action": "tool", "tool": "rejection_counts", "args": {}}',
        '{"action": "final", "report": "Feed outage upstream of Agent 24."}',
    ]
    triage.hermes = hermes
    record = triage.run_triage("manual investigation")
    assert record["completed"] is True
    assert record["steps"] == [{"step": 1, "tool": "rejection_counts", "args": {}}]
    assert record["report"] == "Feed outage upstream of Agent 24."
