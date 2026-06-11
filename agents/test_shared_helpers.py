"""Smoke tests for the shared agent helpers (shared/base_agent.py)."""

import logging
import sqlite3
import threading
import time

import pytest

from shared.base_agent import (
    db_connect,
    db_transaction,
    run_polling_loop,
    setup_logging,
)


def test_setup_logging_returns_named_logger():
    logger = setup_logging("TestAgent")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "TestAgent"


def test_db_connect_creates_usable_connection(temp_db):
    conn = db_connect(temp_db)
    try:
        conn.execute(
            "INSERT INTO bets (player, stat, line, stake) VALUES (?, ?, ?, ?)",
            ("A'ja Wilson", "PTS", 22.5, 50.0),
        )
        conn.commit()
        rows = conn.execute("SELECT player FROM bets").fetchall()
        assert rows == [("A'ja Wilson",)]
    finally:
        conn.close()


def test_db_transaction_commits_on_success(temp_db):
    with db_transaction(temp_db) as conn:
        conn.execute(
            "INSERT INTO bets (player, stat, line, stake) VALUES (?, ?, ?, ?)",
            ("Caitlin Clark", "AST", 8.5, 25.0),
        )

    conn2 = db_connect(temp_db)
    try:
        count = conn2.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
        assert count == 1
    finally:
        conn2.close()


def test_db_transaction_rolls_back_on_error(temp_db):
    # Seed one row.
    with db_transaction(temp_db) as conn:
        conn.execute(
            "INSERT INTO bets (player, stat, line, stake) VALUES (?, ?, ?, ?)",
            ("Breanna Stewart", "REB", 9.5, 40.0),
        )

    # A multi-step write that fails midway must roll back entirely.
    with pytest.raises(sqlite3.OperationalError):
        with db_transaction(temp_db) as conn:
            conn.execute("DELETE FROM bets")
            conn.execute("INSERT INTO no_such_table VALUES (1)")

    conn2 = db_connect(temp_db)
    try:
        count = conn2.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
        assert count == 1, "DELETE should have been rolled back"
    finally:
        conn2.close()


def test_run_polling_loop_runs_task_and_stops():
    stop = threading.Event()
    calls = []

    def task():
        calls.append(time.time())
        if len(calls) >= 3:
            stop.set()

    run_polling_loop(task, interval=0.01, stop_event=stop)
    assert len(calls) == 3


def test_run_polling_loop_keepalive_exits_on_stop():
    stop = threading.Event()
    t = threading.Thread(
        target=run_polling_loop, kwargs={"interval": 30.0, "stop_event": stop}
    )
    t.start()
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()


def test_run_polling_loop_survives_task_exceptions():
    stop = threading.Event()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("boom")

    run_polling_loop(flaky, interval=0.01, stop_event=stop)
    assert len(calls) == 2
