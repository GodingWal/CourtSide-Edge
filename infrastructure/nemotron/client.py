import os
import json
import logging
import re

import requests

# Set NVIDIA_API_KEY in .env to use the real endpoint
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
REQUEST_TIMEOUT = 30

logger = logging.getLogger("NemotronClient")

_INJURY_SCHEMA_HINT = (
    'Respond with ONLY a JSON object, no prose, matching exactly: '
    '{"player_name": str, "team": str (3-letter code), '
    '"injury_status": "OUT"|"DOUBTFUL"|"QUESTIONABLE"|"PROBABLE"|"ACTIVE", '
    '"confidence_score": float 0-1, '
    '"source_credibility": "OFFICIAL"|"BEAT_WRITER"|"AGGREGATOR"|"RUMOR", '
    '"game_impact": "NONE"|"MINOR"|"MAJOR", '
    '"motivation_flag": "NONE"|"REVENGE"|"MILESTONE"|"CONTRACT", '
    '"sentiment_score": float -1 to 1}'
)

_SENTIMENT_SCHEMA_HINT = (
    'Respond with ONLY a JSON object, no prose, matching exactly: '
    '{"motivation_score": float 0-1, "fatigue_penalty": float -1 to 0, '
    '"quote_impact": float -1 to 1}'
)

_SIMULATED_INJURY = {
    "player_name": "Breanna Stewart",
    "team": "NYL",
    "injury_status": "PROBABLE",
    "confidence_score": 0.9,
    "source_credibility": "BEAT_WRITER",
    "game_impact": "NONE",
    "motivation_flag": "NONE",
    "sentiment_score": 0.2,
}

_SIMULATED_SENTIMENT = {
    "motivation_score": 0.8,
    "fatigue_penalty": -0.1,
    "quote_impact": 0.5,
}


class NemotronClient:
    def __init__(self):
        if not NVIDIA_API_KEY:
            logger.warning("NVIDIA_API_KEY not found. Using simulated Nemotron 70B responses.")
            self.simulated = True
        else:
            self.simulated = False
            logger.info(f"NemotronClient using {NEMOTRON_MODEL} at {NVIDIA_BASE_URL}")

    # ── low-level call ────────────────────────────────────────────────────
    def _chat(self, system: str, user: str, temperature: float) -> str:
        """One OpenAI-compatible chat completion against build.nvidia.com."""
        resp = requests.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": NEMOTRON_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": 512,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(text: str):
        """Parse a JSON object out of a model response (tolerates code fences)."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object in model response: {text[:200]}")
        return json.loads(match.group(0))

    # ── public API ────────────────────────────────────────────────────────
    def extract_injury_json(self, text: str) -> dict:
        """Agent 2: strict JSON injury extraction (temp=0)."""
        if self.simulated:
            return dict(_SIMULATED_INJURY)
        try:
            raw = self._chat(
                system=(
                    "You extract structured WNBA injury/news data from social media posts. "
                    + _INJURY_SCHEMA_HINT
                ),
                user=text,
                temperature=0,
            )
            return self._parse_json(raw)
        except Exception as e:
            logger.error(f"Nemotron injury extraction failed, using simulated fallback: {e}")
            return dict(_SIMULATED_INJURY)

    def analyze_sentiment(self, text: str) -> dict:
        """Agent 9: coach/player sentiment scoring (temp=0.3)."""
        if self.simulated:
            return dict(_SIMULATED_SENTIMENT)
        try:
            raw = self._chat(
                system=(
                    "You score WNBA coach and player quotes for betting-relevant sentiment. "
                    + _SENTIMENT_SCHEMA_HINT
                ),
                user=text,
                temperature=0.3,
            )
            return self._parse_json(raw)
        except Exception as e:
            logger.error(f"Nemotron sentiment analysis failed, using simulated fallback: {e}")
            return dict(_SIMULATED_SENTIMENT)
