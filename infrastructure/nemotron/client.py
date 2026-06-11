import os
import json
import logging
import re

import requests

# Local-first Nemotron client. By default it talks to an OpenAI-compatible
# inference server on this host (Ollama, started by
# deploy/scripts/setup-local-llm.sh) serving an NVIDIA Nemotron-family model.
#
#   NEMOTRON_BASE_URL  default http://localhost:11434/v1  (Ollama)
#   NEMOTRON_MODEL     default nemotron-mini              (fits 31GB disk; use
#                      "nemotron:70b" if the instance has ~50GB+ free disk)
#   NEMOTRON_API_KEY   default "local" (Ollama ignores it; set a real key if
#                      pointing BASE_URL at a hosted endpoint instead)
#
# If the server is unreachable or errors, extraction calls return None so
# callers skip the item instead of acting on fabricated data.
NEMOTRON_BASE_URL = os.getenv("NEMOTRON_BASE_URL", "http://localhost:11434/v1")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nemotron-mini")
NEMOTRON_API_KEY = os.getenv("NEMOTRON_API_KEY", "local")
REQUEST_TIMEOUT = 60

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

class NemotronClient:
    def __init__(self):
        # Probe the local server once at startup so logs make the mode obvious.
        # Per-call fallback still applies either way.
        try:
            requests.get(f"{NEMOTRON_BASE_URL}/models", timeout=3,
                         headers={"Authorization": f"Bearer {NEMOTRON_API_KEY}"})
            self.simulated = False
            logger.info(f"NemotronClient using {NEMOTRON_MODEL} at {NEMOTRON_BASE_URL}")
        except Exception:
            self.simulated = True
            logger.warning(
                f"No LLM server at {NEMOTRON_BASE_URL} (run deploy/scripts/setup-local-llm.sh). "
                "LLM extraction is disabled — calls will return None."
            )

    # ── low-level call ────────────────────────────────────────────────────
    def _chat(self, system: str, user: str, temperature: float) -> str:
        """One OpenAI-compatible chat completion against the local server."""
        resp = requests.post(
            f"{NEMOTRON_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {NEMOTRON_API_KEY}",
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
    def ask(self, question: str, system: str, temperature: float = 0.4) -> str:
        """Free-form chat completion (e.g. Agent 12 sandbox). Raises on failure
        when the server is up; returns an unavailability notice otherwise."""
        if self.simulated:
            return ("No local LLM server is reachable, so I can't run live "
                    "analysis right now.")
        return self._chat(system=system, user=question, temperature=temperature)

    def extract_injury_json(self, text: str) -> dict | None:
        """Agent 2: strict JSON injury extraction (temp=0).

        Returns None when no LLM is reachable or the call fails — callers must
        skip the item rather than publish fabricated intel.
        """
        if self.simulated:
            return None
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
            logger.error(f"Nemotron injury extraction failed, skipping item: {e}")
            return None

    def analyze_sentiment(self, text: str) -> dict | None:
        """Agent 9: coach/player sentiment scoring (temp=0.3).

        Returns None when no LLM is reachable or the call fails — callers must
        skip the item rather than publish fabricated scores.
        """
        if self.simulated:
            return None
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
            logger.error(f"Nemotron sentiment analysis failed, skipping item: {e}")
            return None
