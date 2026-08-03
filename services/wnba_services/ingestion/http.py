"""A deliberately slow, honest HTTP client.

Every source here is free and undocumented. That makes politeness a hard requirement rather
than a courtesy: one client, an honest user agent, a floor on the interval between requests,
and exponential backoff whenever a source signals it wants less traffic.

It also refuses to fight bot protection. If a source answers with a CAPTCHA challenge, that
is the operator declining automated access, and :class:`BotDefenceError` is raised so the
caller stops rather than escalating. Working around a CAPTCHA would be both a breach of the
source's terms and a decision this software is not entitled to make on anyone's behalf.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = [
    "BotDefenceError",
    "PoliteClient",
    "SourceUnavailableError",
]

DEFAULT_USER_AGENT = "wnba-prop-intelligence/0.1 (personal research; contact via repository)"

# Fingerprints of the common bot-defence vendors. Seeing any of these means stop.
_BOT_DEFENCE_MARKERS = (
    "captcha-delivery.com",
    "geo.captcha",
    "datadome",
    "cf-chl",
    "/cdn-cgi/challenge",
    "px-captcha",
    "perimeterx",
)


class SourceUnavailableError(RuntimeError):
    """The source could not be reached or returned an unusable response."""


class BotDefenceError(SourceUnavailableError):
    """The source served a bot-detection challenge.

    Raised so that a caller stops and reports, never so that it retries harder. The correct
    response is to stop using that source programmatically.
    """


@dataclass
class PoliteClient:
    """Rate-limited HTTP client with backoff. One instance per source."""

    source: str
    min_interval_seconds: float = field(
        default_factory=lambda: float(os.environ.get("WNBA_MIN_POLL_INTERVAL_SECONDS", "60"))
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("WNBA_HTTP_TIMEOUT_SECONDS", "20"))
    )
    user_agent: str = field(
        default_factory=lambda: os.environ.get("WNBA_HTTP_USER_AGENT", DEFAULT_USER_AGENT)
    )
    max_attempts: int = 4
    _last_request_at: float = field(default=0.0, init=False, repr=False)

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        """Fetch JSON, respecting the interval floor and backing off when asked to."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        delay = 2.0
        last_error: str = ""

        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_slot()
            try:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    response = client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
            else:
                self._last_request_at = time.monotonic()
                body = response.text

                if any(marker in body.lower() for marker in _BOT_DEFENCE_MARKERS):
                    raise BotDefenceError(
                        f"{self.source} served a bot-detection challenge ({response.status_code}). "
                        "This source has declined automated access; it will not be retried or "
                        "worked around."
                    )

                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        last_error = f"response was not JSON: {exc}"
                elif response.status_code in (403, 401):
                    raise SourceUnavailableError(
                        f"{self.source} refused access ({response.status_code}); "
                        "credentials or terms may have changed."
                    )
                elif response.status_code == 429 or response.status_code >= 500:
                    # Explicitly asked to slow down, or the source is struggling. Back off.
                    retry_after = response.headers.get("retry-after")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    last_error = f"http {response.status_code}"
                    if attempt < self.max_attempts:
                        time.sleep(wait)
                        delay *= 2
                        continue
                else:
                    last_error = f"http {response.status_code}"

            if attempt < self.max_attempts:
                time.sleep(delay)
                delay *= 2

        raise SourceUnavailableError(
            f"{self.source} unavailable after {self.max_attempts} attempts: {last_error}"
        )

    def get_bytes(self, url: str, *, accept: str = "application/octet-stream") -> bytes:
        """Fetch a binary public document once; callers own document-level idempotency."""
        self._wait_for_slot()
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.get(
                    url, headers={"User-Agent": self.user_agent, "Accept": accept}
                )
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"{self.source} transport error: {exc}") from exc
        self._last_request_at = time.monotonic()
        if response.status_code != 200:
            raise SourceUnavailableError(f"{self.source} returned http {response.status_code}")
        if any(marker.encode() in response.content.lower() for marker in _BOT_DEFENCE_MARKERS):
            raise BotDefenceError(f"{self.source} served a bot-detection challenge")
        return response.content
