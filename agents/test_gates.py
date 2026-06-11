import os
import sys
import unittest
import time
from unittest.mock import MagicMock
from fastapi import HTTPException
import importlib

# Resolve absolute paths and add to sys.path to make modules importable
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# Mock shared.redis_client and shared.audit_logger to avoid dependencies during
# import. The mocks are removed from sys.modules afterwards so other test files
# (e.g. shared/test_redis_client.py) import the real modules; the agent modules
# imported below keep references to the mocks they bound at import time.
from types import ModuleType  # noqa: E402
mock_redis = ModuleType('shared.redis_client')
mock_redis.RedisPubSub = MagicMock()
mock_redis.StreamConsumer = MagicMock()

mock_audit = ModuleType('shared.audit_logger')
mock_audit.AuditLogger = MagicMock()
mock_audit.generate_trace_id = lambda: "test-trace-id"

_saved_modules = {
    name: sys.modules.get(name)
    for name in ('shared.redis_client', 'shared.audit_logger')
}
sys.modules['shared.redis_client'] = mock_redis
sys.modules['shared.audit_logger'] = mock_audit
try:
    # Import components dynamically since directory names start with digits
    agent4 = importlib.import_module("agents.4_execution_oracle.main")
    agent13 = importlib.import_module("agents.13_parlay_generator.main")
finally:
    for name, mod in _saved_modules.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class TestAgent4ExecutionGate(unittest.TestCase):
    def setUp(self):
        agent4.upstream_health = {
            "channel_live_odds": "OK",
            "channel_true_projections": "OK"
        }
        agent4.active_games = {
            "LVA_NYL": "LIVE"
        }
        agent4.current_drawdown = 0.0
        agent4.execution_log = []
        agent4.audit.log_decision = MagicMock()

    def test_gate_open_executes_bet(self):
        message = {
            "trace_id": "test-trace",
            "confidence": 0.8,
            "recommended_bet_amount": 100,
            "edge": {"game_id": "LVA_NYL"}
        }
        agent4.on_execution_order("msg-1", message, None)
        
        # Should execute the bet
        self.assertEqual(len(agent4.execution_log), 1)
        self.assertEqual(agent4.execution_log[0]["status"], "EXECUTED")
        agent4.audit.log_decision.assert_called_with(
            trace_id="test-trace",
            agent_id="Agent_4",
            action="EXECUTE",
            reason="Executed $100 bet. Drawdown: 0.0%, Confidence: 0.80",
            input_payload=message,
            output_payload=agent4.execution_log[0],
            confidence=0.8
        )

    def test_gate_locked_due_to_staleness(self):
        agent4.upstream_health["channel_live_odds"] = "STALE"
        
        message = {
            "trace_id": "test-trace",
            "confidence": 0.8,
            "recommended_bet_amount": 100,
            "edge": {"game_id": "LVA_NYL"}
        }
        agent4.on_execution_order("msg-1", message, None)
        
        # Should reject
        self.assertEqual(len(agent4.execution_log), 0)
        agent4.audit.log_decision.assert_called_with(
            trace_id="test-trace",
            agent_id="Agent_4",
            action="REJECT",
            reason="Blocked execution: Upstream channels are unhealthy/stale: ['channel_live_odds']",
            input_payload=message,
            confidence=0.8
        )

    def test_gate_locked_due_to_game_not_live(self):
        agent4.active_games["LVA_NYL"] = "PRE"
        
        message = {
            "trace_id": "test-trace",
            "confidence": 0.8,
            "recommended_bet_amount": 100,
            "edge": {"game_id": "LVA_NYL"}
        }
        agent4.on_execution_order("msg-1", message, None)
        
        # Should reject
        self.assertEqual(len(agent4.execution_log), 0)
        agent4.audit.log_decision.assert_called_with(
            trace_id="test-trace",
            agent_id="Agent_4",
            action="REJECT",
            reason="Blocked execution: Game LVA_NYL status is PRE (expected LIVE)",
            input_payload=message,
            confidence=0.8
        )


class TestAgent13ParlayWindow(unittest.TestCase):
    def setUp(self):
        agent13.active_games = {}
        # Live player props cached by Agent 1 (The Odds API feed) in Redis.
        # Entries can't mix platforms, so the pool is one pick'em book.
        props = {
            "A'ja Wilson|PTS|PrizePicks": '{"player": "A\'ja Wilson", "stat": "PTS", "line": 22.5, "odds": -110, "book": "PrizePicks", "game": "LVA @ NYL"}',
            "Breanna Stewart|REB|PrizePicks": '{"player": "Breanna Stewart", "stat": "REB", "line": 9.5, "odds": -115, "book": "PrizePicks", "game": "LVA @ NYL"}',
            "Caitlin Clark|AST|PrizePicks": '{"player": "Caitlin Clark", "stat": "AST", "line": 8.5, "odds": -110, "book": "PrizePicks", "game": "IND @ CHI"}',
        }
        # Agent 3's cached projections (real-edge ranking input).
        projections = {
            "A'ja Wilson|PTS": '{"player": "A\'ja Wilson", "stat": "PTS", "projected_value": 26.0, "market_line": 22.5, "edge_vs_line": 3.5}',
            "Breanna Stewart|REB": '{"player": "Breanna Stewart", "stat": "REB", "projected_value": 8.0, "market_line": 9.5, "edge_vs_line": -1.5}',
        }
        pubsub_instance = MagicMock()
        pubsub_instance.client.hgetall.side_effect = lambda key: (
            props if key == "props:lines" else projections if key == "props:projections" else {}
        )
        agent13.RedisPubSub = MagicMock(return_value=pubsub_instance)

    def _set_game_in_window(self):
        now = time.time()
        agent13.active_games["LVA_NYL"] = {
            "gameId": "LVA_NYL",
            "tipoff": now + 900,
            "status": "PRE"
        }

    def test_parlay_synthesis_succeeds_in_window(self):
        # Game tipping off in 15 minutes (inside 30-min window)
        self._set_game_in_window()

        # Should succeed with the default 2 legs
        result = agent13.generate_parlay()
        self.assertIn("legs", result)
        self.assertEqual(len(result["legs"]), 2)
        self.assertEqual(result["platform"], "PrizePicks")
        self.assertEqual(result["payout_multiplier"], 3.0)
        # Legs are ranked by real projected edge: Wilson (+15.6%) first, and
        # Stewart's negative edge flips her side to UNDER.
        wilson = next(l for l in result["legs"] if l["player"] == "A'ja Wilson")
        stewart = next(l for l in result["legs"] if l["player"] == "Breanna Stewart")
        self.assertEqual(wilson["over_under"], "OVER")
        self.assertEqual(stewart["over_under"], "UNDER")
        self.assertGreater(wilson["edge_pct"], 0)

    def test_parlay_respects_requested_leg_count(self):
        self._set_game_in_window()

        result = agent13.generate_parlay({"legs": 3})
        self.assertEqual(len(result["legs"]), 3)
        self.assertEqual(result["payout_multiplier"], 5.0)  # PrizePicks 3-pick

    def test_parlay_rejects_invalid_leg_count(self):
        self._set_game_in_window()

        for bad in (1, 7, "ten"):
            with self.assertRaises(HTTPException) as ctx:
                agent13.generate_parlay({"legs": bad})
            self.assertEqual(ctx.exception.status_code, 400)

    def test_parlay_refuses_when_pool_too_small(self):
        self._set_game_in_window()

        with self.assertRaises(HTTPException) as ctx:
            agent13.generate_parlay({"legs": 5})
        self.assertEqual(ctx.exception.status_code, 503)

    def test_parlay_synthesis_blocked_outside_window(self):
        now = time.time()
        # Game tipping off in 45 minutes (outside 30-min window)
        agent13.active_games["LVA_NYL"] = {
            "gameId": "LVA_NYL",
            "tipoff": now + 2700,
            "status": "PRE"
        }
        
        # Should raise HTTP 400 Exception
        with self.assertRaises(HTTPException) as ctx:
            agent13.generate_parlay()
        
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Parlay synthesis blocked", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
