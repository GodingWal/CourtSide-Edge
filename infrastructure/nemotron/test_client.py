import unittest
from unittest.mock import patch, MagicMock

import infrastructure.nemotron.client as client_module


def _client_with_server():
    """NemotronClient whose startup probe sees a live local server."""
    with patch.object(client_module.requests, "get", return_value=MagicMock()):
        return client_module.NemotronClient()


def _client_without_server():
    """NemotronClient whose startup probe finds no local server."""
    with patch.object(client_module.requests, "get", side_effect=Exception("no server")):
        return client_module.NemotronClient()


class TestNemotronClient(unittest.TestCase):

    def test_no_server_falls_back_to_simulated(self):
        client = _client_without_server()
        self.assertTrue(client.simulated)

        result = client.analyze_sentiment("Some test text")
        self.assertEqual(result, {
            "motivation_score": 0.8,
            "fatigue_penalty": -0.1,
            "quote_impact": 0.5,
        })

    def test_analyze_sentiment_local_server(self):
        client = _client_with_server()
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

    def test_injury_extraction_parses_code_fences(self):
        client = _client_with_server()

        fenced = ('```json\n{"player_name": "A", "team": "LVA", "injury_status": "OUT", '
                  '"confidence_score": 1.0, "source_credibility": "OFFICIAL", '
                  '"game_impact": "MAJOR", "motivation_flag": "NONE", "sentiment_score": -0.5}\n```')
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": fenced}}]}
        with patch.object(client_module.requests, "post", return_value=mock_resp):
            result = client.extract_injury_json("some tweet")

        self.assertEqual(result["player_name"], "A")
        self.assertEqual(result["injury_status"], "OUT")

    def test_call_error_falls_back_to_simulated(self):
        client = _client_with_server()

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
