import os
import unittest
import importlib
from unittest.mock import patch, MagicMock

import infrastructure.nemotron.client as client_module
from infrastructure.nemotron.client import NemotronClient

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

        # Currently the real API call returns None since there's a `pass` in the code
        result = client.analyze_sentiment("Some test text")
        self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()
