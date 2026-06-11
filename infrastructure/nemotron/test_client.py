import os
import unittest
import importlib
from unittest.mock import patch, MagicMock

import infrastructure.nemotron.client as client_module

class TestNemotronClient(unittest.TestCase):

    @patch.dict(os.environ, clear=True) # Ensure NVIDIA_API_KEY is not set
    def test_analyze_sentiment_simulated(self):
        # Reload module to ensure NVIDIA_API_KEY is evaluated from the mocked environment
        importlib.reload(client_module)
        client = client_module.NemotronClient()
        self.assertTrue(client.simulated)

        result = client.analyze_sentiment("Some test text")

        expected_result = {
            "motivation_score": 0.8,
            "fatigue_penalty": -0.1,
            "quote_impact": 0.5
        }
        self.assertEqual(result, expected_result)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "fake_api_key"})
    def test_analyze_sentiment_real_api(self):
        # Reload module to ensure NVIDIA_API_KEY is evaluated from the mocked environment
        importlib.reload(client_module)
        client = client_module.NemotronClient()
        self.assertFalse(client.simulated)

        api_payload = {
            "choices": [{"message": {"content":
                '{"motivation_score": 0.6, "fatigue_penalty": -0.3, "quote_impact": 0.1}'}}]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_payload
        with patch.object(client_module.requests, "post", return_value=mock_resp) as mock_post:
            result = client.analyze_sentiment("Some test text")

        mock_post.assert_called_once()
        self.assertEqual(result, {
            "motivation_score": 0.6,
            "fatigue_penalty": -0.3,
            "quote_impact": 0.1,
        })

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "fake_api_key"})
    def test_injury_extraction_parses_code_fences(self):
        importlib.reload(client_module)
        client = client_module.NemotronClient()

        fenced = ('```json\n{"player_name": "A", "team": "LVA", "injury_status": "OUT", '
                  '"confidence_score": 1.0, "source_credibility": "OFFICIAL", '
                  '"game_impact": "MAJOR", "motivation_flag": "NONE", "sentiment_score": -0.5}\n```')
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": fenced}}]}
        with patch.object(client_module.requests, "post", return_value=mock_resp):
            result = client.extract_injury_json("some tweet")

        self.assertEqual(result["player_name"], "A")
        self.assertEqual(result["injury_status"], "OUT")

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "fake_api_key"})
    def test_api_error_falls_back_to_simulated(self):
        importlib.reload(client_module)
        client = client_module.NemotronClient()

        with patch.object(client_module.requests, "post", side_effect=Exception("boom")):
            result = client.analyze_sentiment("Some test text")

        # Falls back to the simulated values rather than raising
        self.assertEqual(result, {
            "motivation_score": 0.8,
            "fatigue_penalty": -0.1,
            "quote_impact": 0.5,
        })

if __name__ == '__main__':
    unittest.main()
