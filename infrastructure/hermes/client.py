import os
import json
import logging
import re

import requests

# Local-first Hermes client. By default it talks to an OpenAI-compatible
# inference server on this host (Ollama, started by
# deploy/scripts/setup-local-llm.sh) serving a NousResearch Hermes-family model.
#
#   HERMES_BASE_URL  default http://localhost:11434/v1  (Ollama)
#   HERMES_MODEL     default hermes3 (8B, ~4.7GB; fits 31GB disk; use
#                    "hermes3:70b" if the instance has ~50GB+ free disk)
#   HERMES_API_KEY   default "local" (Ollama ignores it; set a real key if
#                    pointing BASE_URL at a hosted endpoint instead)
#
# If the server is unreachable or errors, extraction calls return None so
# callers skip the item instead of acting on fabricated data.
HERMES_BASE_URL = os.getenv("HERMES_BASE_URL", "http://localhost:11434/v1")
HERMES_MODEL = os.getenv("HERMES_MODEL", "hermes3")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "local")
# Reply budget for free-form analysis. JSON extraction calls stay small.
HERMES_MAX_TOKENS = int(os.getenv("HERMES_MAX_TOKENS", "1024"))
REQUEST_TIMEOUT = int(os.getenv("HERMES_TIMEOUT_SECONDS", "120"))

logger = logging.getLogger("HermesClient")

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
    '{"team": str (3-letter team code, or "UNKNOWN" if no team is identifiable), '
    '"motivation_score": float 0-1, "fatigue_penalty": float -1 to 0, '
    '"quote_impact": float -1 to 1}'
)

class HermesClient:
    def __init__(self):
        # Probe the local server once at startup so logs make the mode obvious.
        # Per-call fallback still applies either way.
        try:
            requests.get(f"{HERMES_BASE_URL}/models", timeout=3,
                         headers={"Authorization": f"Bearer {HERMES_API_KEY}"})
            self.simulated = False
            logger.info(f"HermesClient using {HERMES_MODEL} at {HERMES_BASE_URL}")
        except Exception:
            self.simulated = True
            logger.warning(
                f"No LLM server at {HERMES_BASE_URL} (run deploy/scripts/setup-local-llm.sh). "
                "LLM extraction is disabled — calls will return None."
            )

    # ── low-level call ────────────────────────────────────────────────────
    def _chat(self, system: str, user: str, temperature: float,
              max_tokens: int | None = None) -> str:
        """One OpenAI-compatible chat completion against the local server."""
        resp = requests.post(
            f"{HERMES_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {HERMES_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": HERMES_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens or HERMES_MAX_TOKENS,
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
    def ask(self, question: str, system: str, temperature: float = 0.4,
            max_tokens: int | None = None) -> str:
        """Free-form chat completion (e.g. Agent 12 sandbox). Raises on failure
        when the server is up; returns an unavailability notice otherwise."""
        if self.simulated:
            return ("No local LLM server is reachable, so I can't run live "
                    "analysis right now.")
        return self._chat(system=system, user=question, temperature=temperature,
                          max_tokens=max_tokens)

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
            logger.error(f"Hermes injury extraction failed, skipping item: {e}")
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
            logger.error(f"Hermes sentiment analysis failed, skipping item: {e}")
            return None
