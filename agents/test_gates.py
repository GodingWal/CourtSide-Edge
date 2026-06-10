import os
import sys
import unittest
import time
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
import importlib

# Resolve absolute paths and add to sys.path to make modules importable
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# Mock shared.redis_client and shared.audit_logger to avoid dependencies during import
from types import ModuleType
mock_redis = ModuleType('shared.redis_client')
mock_redis.RedisPubSub = MagicMock()
mock_redis.StreamConsumer = MagicMock()
sys.modules['shared.redis_client'] = mock_redis

mock_audit = ModuleType('shared.audit_logger')
mock_audit.AuditLogger = MagicMock()
mock_audit.generate_trace_id = lambda: "test-trace-id"
sys.modules['shared.audit_logger'] = mock_audit

# Import components dynamically since directory names start with digits
agent4 = importlib.import_module("agents.4_execution_oracle.main")
agent13 = importlib.import_module("agents.13_parlay_generator.main")


class TestAgent4ExecutionGate(unittest.TestCase):
    def setUp(self):
        agent4.upstream_health = {
            "channel_live_odds": "OK",
            "channel_true_projections": "OK",
            "channel_ev_alerts": "OK"
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
        agent13.get_active_players = MagicMock(return_value=[
            {"name": "A'ja Wilson", "team": "LVA"},
            {"name": "Breanna Stewart", "team": "NYL"}
        ])

    def test_parlay_synthesis_succeeds_in_window(self):
        now = time.time()
        # Game tipping off in 15 minutes (inside 30-min window)
        agent13.active_games["LVA_NYL"] = {
            "gameId": "LVA_NYL",
            "tipoff": now + 900,
            "status": "PRE"
        }
        
        # Should succeed
        result = agent13.generate_parlay()
        self.assertIn("legs", result)
        self.assertEqual(len(result["legs"]), 2)

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
