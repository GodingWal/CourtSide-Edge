"""P2-1 integration tests for the pick mesh: validation gate -> claim
verifier -> publisher over (fake) Redis, including the lineage check that
rejects messages injected directly onto picks.narrated."""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from shared.picks.channels import (  # noqa: E402
    CHANNEL_PICKS_PUBLISHABLE,
    CHANNEL_PICKS_REJECTED,
    CHANNEL_PICKS_VALIDATED,
    RECENT_PUBLISHED_KEY,
    has_validated_ancestor,
)
from shared.picks.line_tracking import LineHistory  # noqa: E402
from shared.picks.narrative import render_template  # noqa: E402
from shared.picks.models import NarrativePayload, pick_from_message  # noqa: E402
from shared.picks.test_validation import make_payload, make_pick  # noqa: E402

agent24 = importlib.import_module("agents.24_validation_gate.main")
agent25 = importlib.import_module("agents.25_claim_verifier.main")
agent26 = importlib.import_module("agents.26_pick_publisher.main")


@pytest.fixture
def mesh(fake_redis, tmp_path, monkeypatch):
    """The three mesh nodes wired to one fakeredis server, with publishes
    captured per channel."""
    from shared.redis_client import RedisPubSub

    published = []

    def capture(self, channel, message):
        published.append((channel, message))
        self.client.publish(channel, json.dumps(message))

    monkeypatch.setattr(RedisPubSub, "publish", capture)

    db_path = str(tmp_path / "picks.db")
    gate = agent24.ValidationGate(RedisPubSub(), db_path=db_path)
    verifier = agent25.ClaimVerifier(RedisPubSub())
    publisher = agent26.PickPublisher(RedisPubSub(), db_path=db_path)
    for node in (gate, verifier, publisher):
        node.audit = MagicMock()

    class Mesh:
        pass

    m = Mesh()
    m.gate, m.verifier, m.publisher = gate, verifier, publisher
    m.client, m.published = fake_redis, published
    m.on_channel = lambda ch: [msg for c, msg in published if c == ch]
    return m


def raw_message(**pick_overrides):
    return {"pick": make_pick(**pick_overrides).model_dump(),
            "payload": make_payload().model_dump()}


def test_valid_pick_flows_to_validated_with_lineage(mesh):
    mesh.gate.handle_raw(raw_message())
    validated = mesh.on_channel(CHANNEL_PICKS_VALIDATED)
    assert len(validated) == 1
    assert validated[0]["pick"]["pick_id"] == "p1"
    assert has_validated_ancestor(mesh.client, "p1")
    assert not mesh.on_channel(CHANNEL_PICKS_REJECTED)


def test_rejected_pick_carries_reason_code_and_snapshot(mesh):
    mesh.gate.handle_raw(raw_message(projection=18.9))  # Buy below the line
    rejected = mesh.on_channel(CHANNEL_PICKS_REJECTED)
    assert len(rejected) == 1
    assert rejected[0]["reason_code"] == "EDGE_SIGN_MISMATCH"
    assert {"pick_id", "reason_code", "payload_snapshot", "ts"} <= set(rejected[0])
    # Terminal: nothing reached the validated channel.
    assert not mesh.on_channel(CHANNEL_PICKS_VALIDATED)
    assert not has_validated_ancestor(mesh.client, "p1")


def test_injected_narrated_message_without_ancestor_is_rejected(mesh):
    """Spec acceptance: a message placed directly onto picks.narrated, with no
    picks.validated ancestor, must never reach the publishable channel."""
    forged = {"pick": make_pick(pick_id="forged-1").model_dump(),
              "payload": make_payload().model_dump(),
              "narrative": "Totally legitimate pick."}
    mesh.verifier.handle_narrated(forged)
    rejected = mesh.on_channel(CHANNEL_PICKS_REJECTED)
    assert len(rejected) == 1
    assert rejected[0]["reason_code"] == "LINEAGE_VIOLATION"
    assert not mesh.on_channel(CHANNEL_PICKS_PUBLISHABLE)


def test_full_pipeline_raw_to_published(mesh):
    mesh.gate.handle_raw(raw_message())
    validated = mesh.on_channel(CHANNEL_PICKS_VALIDATED)[0]

    pick = pick_from_message(validated["pick"])
    payload = NarrativePayload(**validated["payload"])
    mesh.verifier.handle_narrated({
        "pick": validated["pick"],
        "payload": validated["payload"],
        "narrative": render_template(pick, payload),
    })
    publishable = mesh.on_channel(CHANNEL_PICKS_PUBLISHABLE)
    assert len(publishable) == 1

    mesh.publisher.handle_publishable(publishable[0])
    recent = mesh.client.lrange(RECENT_PUBLISHED_KEY, 0, -1)
    assert len(recent) == 1
    record = json.loads(recent[0])
    assert record["capture_line"] == 19.5
    assert record["pick"]["pick_id"] == "p1"


def test_fabricated_narrative_is_blocked_after_validation(mesh):
    mesh.gate.handle_raw(raw_message())
    validated = mesh.on_channel(CHANNEL_PICKS_VALIDATED)[0]
    mesh.verifier.handle_narrated({
        "pick": validated["pick"],
        "payload": validated["payload"],
        "narrative": "Caitlin Clark has a +9.9 edge in her WNBA debut.",
    })
    rejected = mesh.on_channel(CHANNEL_PICKS_REJECTED)
    assert len(rejected) == 1
    assert set(rejected[0]["reason_codes"]) == {"FABRICATED_NUMERIC",
                                                "UNGROUNDED_CLAIM"}
    assert not mesh.on_channel(CHANNEL_PICKS_PUBLISHABLE)


def test_publisher_demotes_on_adverse_line_move(mesh):
    mesh.gate.handle_raw(raw_message())
    validated = mesh.on_channel(CHANNEL_PICKS_VALIDATED)[0]
    pick = pick_from_message(validated["pick"])
    payload = NarrativePayload(**validated["payload"])
    mesh.verifier.handle_narrated({
        "pick": validated["pick"], "payload": validated["payload"],
        "narrative": render_template(pick, payload),
    })
    publishable = mesh.on_channel(CHANNEL_PICKS_PUBLISHABLE)[0]

    # Line moved 19.5 -> 20.5 against the Buy since capture.
    LineHistory(mesh.client).record("prizepicks", "Caitlin Clark", "PTS", 20.5)
    mesh.publisher.handle_publishable(publishable)

    assert not mesh.client.lrange(RECENT_PUBLISHED_KEY, 0, -1)
    demotions = [m for m in mesh.on_channel(CHANNEL_PICKS_REJECTED)
                 if m.get("stage") == "publication"]
    assert len(demotions) == 1
    assert demotions[0]["reason_code"] == "ADVERSE_LINE_MOVE"


def test_roster_updates_feed_the_gate(mesh):
    mesh.gate.on_roster_update({"player_name": "Caitlin Clark",
                                "team": "Chicago Sky",
                                "injury_status": "ACTIVE"})
    mesh.gate.handle_raw(raw_message())  # payload asserts Indiana Fever
    rejected = mesh.on_channel(CHANNEL_PICKS_REJECTED)
    assert len(rejected) == 1
    assert rejected[0]["reason_code"] == "ROSTER_MISMATCH"
