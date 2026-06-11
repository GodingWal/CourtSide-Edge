"""Shared pytest fixtures for agent tests.

Provides a fake Redis server/client and a temporary SQLite database so new
agent tests don't need to stand up real infrastructure.
"""

import os
import sys

import pytest

# Make the repo root importable (so `shared.*` and `agents.*` resolve) no
# matter where pytest is invoked from.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


@pytest.fixture
def fake_redis_server():
    """A fakeredis server instance shared by all clients in a test."""
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis(fake_redis_server, monkeypatch):
    """Patch redis.Redis so shared.redis_client talks to fakeredis.

    Returns a client connected to the same fake server for assertions.
    """
    import fakeredis

    def factory(*args, **kwargs):
        kwargs.pop("host", None)
        kwargs.pop("port", None)
        return fakeredis.FakeRedis(server=fake_redis_server, **kwargs)

    monkeypatch.setattr("redis.Redis", factory)
    return fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)


@pytest.fixture
def temp_db_path(tmp_path):
    """Path to a fresh temporary SQLite database file."""
    return str(tmp_path / "test_agents.db")


@pytest.fixture
def temp_db(temp_db_path):
    """Temporary SQLite db pre-seeded with a simple `bets` table.

    Yields the db path; connections should be opened via shared.base_agent
    helpers.
    """
    from shared.base_agent import db_transaction

    with db_transaction(temp_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT,
                stat TEXT,
                line REAL,
                stake REAL,
                result TEXT
            )
            """
        )
    yield temp_db_path
