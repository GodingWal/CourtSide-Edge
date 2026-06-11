"""Outbound alert notifications (Telegram / Discord) for the execution path.

Configured entirely by environment variables — when none are set, notify()
is a silent no-op so agents run unchanged without credentials:

  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   Telegram bot sendMessage
  DISCORD_WEBHOOK_URL                     Discord channel webhook

Failures are logged and swallowed: a notification outage must never block
or crash the execution pipeline.
"""
import logging
import os
import threading

import requests

logger = logging.getLogger("Notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TIMEOUT = 10


def enabled() -> bool:
    return bool((TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or DISCORD_WEBHOOK_URL)


def notify(text: str) -> bool:
    """Dispatch one alert to every configured channel.

    Sends from a background thread so callers inside Redis listener
    callbacks are never blocked on notification network I/O. Returns True
    when at least one channel is configured (i.e. a send was dispatched).
    """
    if not enabled():
        return False
    threading.Thread(target=_send, args=(text,), daemon=True).start()
    return True


def _send(text: str) -> bool:
    sent = False
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            sent = True
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")
    if DISCORD_WEBHOOK_URL:
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL, json={"content": text[:1900]}, timeout=TIMEOUT
            )
            resp.raise_for_status()
            sent = True
        except Exception as e:
            logger.warning(f"Discord notification failed: {e}")
    return sent
