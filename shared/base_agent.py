"""Shared agent scaffolding for CourtSide-Edge.

Provides:
- setup_logging():   standardized logging configuration for every agent.
- db_connect():      sqlite3.connect wrapper that always applies timeout=5.0.
- db_transaction():  context manager wrapping multi-step writes in a single
                     transaction (commit on success, rollback on error).
- shutdown_event / install_signal_handlers / run_polling_loop:
                     graceful-shutdown polling-loop scaffold.
"""

import logging
import os
import re
import signal
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

DEFAULT_SQLITE_TIMEOUT = 5.0

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


HEARTBEAT_INTERVAL = 30
HEARTBEAT_TTL = 90


def _start_heartbeat(name: str) -> None:
    """Maintain heartbeat:agent:<id> in Redis so the web API reports real
    liveness for every agent. Best-effort: skipped silently when Redis is
    unreachable or the redis package is unavailable (e.g. unit tests).
    """
    match = re.match(r"Agent(\d+(?:\.\d+)?)", name)
    if not match:
        return
    agent_id = match.group(1)

    def _beat():
        client = None
        while True:
            try:
                if client is None:
                    import redis
                    client = redis.Redis(
                        host=os.getenv("REDIS_HOST", "localhost"),
                        port=int(os.getenv("REDIS_PORT", 6379)),
                        password=os.getenv("REDIS_PASSWORD") or None,
                        socket_timeout=5,
                    )
                client.set(f"heartbeat:agent:{agent_id}", str(int(time.time())), ex=HEARTBEAT_TTL)
            except Exception:
                client = None  # reconnect next round
            time.sleep(HEARTBEAT_INTERVAL)

    threading.Thread(target=_beat, daemon=True, name=f"heartbeat-{agent_id}").start()


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Configure root logging once (idempotent) and return a named logger.

    Also starts the agent's liveness heartbeat (once per process).
    """
    logging.basicConfig(level=level, format=LOG_FORMAT)
    if not getattr(setup_logging, "_heartbeat_started", False):
        setup_logging._heartbeat_started = True
        _start_heartbeat(name)
    return logging.getLogger(name)


def db_connect(db_path: str, timeout: float = DEFAULT_SQLITE_TIMEOUT) -> sqlite3.Connection:
    """Open a SQLite connection with a busy timeout always set (default 5.0s)."""
    return sqlite3.connect(db_path, timeout=timeout)


@contextmanager
def db_transaction(db_path: str, timeout: float = DEFAULT_SQLITE_TIMEOUT):
    """Context manager yielding a connection whose work is one transaction.

    Commits on a clean exit, rolls back on any exception, and always closes
    the connection.

        with db_transaction(DB_PATH) as conn:
            conn.execute("DELETE ...")
            conn.execute("INSERT ...")
    """
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Graceful shutdown / polling-loop scaffold ────────────────────────────────

shutdown_event = threading.Event()


def _handle_signal(signum, frame):
    logging.getLogger("base_agent").info(
        "Received signal %s, initiating graceful shutdown...", signum
    )
    shutdown_event.set()


def install_signal_handlers() -> threading.Event:
    """Install SIGTERM/SIGINT handlers that set the shared shutdown event.

    Safe to call from non-main threads (no-op in that case). Returns the
    shared shutdown event.
    """
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except ValueError:
        # signal handlers can only be installed in the main thread
        pass
    return shutdown_event


def run_polling_loop(
    task: Optional[Callable[[], None]] = None,
    interval: float = 30.0,
    initial_delay: float = 0.0,
    stop_event: Optional[threading.Event] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Run `task` every `interval` seconds until shutdown is requested.

    With task=None this acts as an interruptible keepalive (replacement for
    `while True: time.sleep(1)` loops that do no work). Exceptions raised by
    the task are logged and the loop continues.
    """
    stop = stop_event if stop_event is not None else shutdown_event
    log = logger or logging.getLogger("base_agent")

    if initial_delay > 0 and stop.wait(initial_delay):
        return

    while not stop.is_set():
        if task is not None:
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - keep agent loops alive
                log.error("Polling task error: %s", exc)
        stop.wait(interval)
