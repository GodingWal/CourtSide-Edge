import os
import sys
import unittest
from unittest.mock import patch
import importlib

# Resolve absolute paths and add to sys.path to make modules importable
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, ROOT_DIR)

from infrastructure.nemotron import client

class TestNemotronClient(unittest.TestCase):
    def setUp(self):
        # We need to reload the module because NVIDIA_API_KEY is evaluated at module load time
        pass

    def tearDown(self):
        importlib.reload(client)

    @patch.dict(os.environ, {})
    def test_analyze_sentiment_simulated(self):
        # Explicitly remove NVIDIA_API_KEY from environment to force simulated mode
        if "NVIDIA_API_KEY" in os.environ:
            del os.environ["NVIDIA_API_KEY"]

        importlib.reload(client)
        nemotron_client = client.NemotronClient()
        self.assertTrue(nemotron_client.simulated)

        result = nemotron_client.analyze_sentiment("Coach said the team is tired.")
        self.assertEqual(result, {
            "motivation_score": 0.8,
            "fatigue_penalty": -0.1,
            "quote_impact": 0.5
        })

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "fake-api-key"})
    def test_analyze_sentiment_real_api(self):
        importlib.reload(client)
        nemotron_client = client.NemotronClient()
        self.assertFalse(nemotron_client.simulated)

        # Currently, the real API call branch just has `pass` and returns None.
        result = nemotron_client.analyze_sentiment("Coach said the team is tired.")
        self.assertIsNone(result)

    @patch.dict(os.environ, {})
    def test_extract_injury_json_simulated(self):
        if "NVIDIA_API_KEY" in os.environ:
            del os.environ["NVIDIA_API_KEY"]

        importlib.reload(client)
        nemotron_client = client.NemotronClient()
        self.assertTrue(nemotron_client.simulated)

        result = nemotron_client.extract_injury_json("Breanna Stewart is probable to play.")
        self.assertEqual(result, {
            "player_name": "Breanna Stewart",
            "team": "NYL",
            "injury_status": "PROBABLE",
            "confidence_score": 0.9,
            "source_credibility": "BEAT_WRITER",
            "game_impact": "NONE",
            "motivation_flag": "NONE",
            "sentiment_score": 0.2
        })

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "fake-api-key"})
    def test_extract_injury_json_real_api(self):
        importlib.reload(client)
        nemotron_client = client.NemotronClient()
        self.assertFalse(nemotron_client.simulated)

        result = nemotron_client.extract_injury_json("Breanna Stewart is probable to play.")
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
