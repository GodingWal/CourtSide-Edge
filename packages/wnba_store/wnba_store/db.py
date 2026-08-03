"""Postgres connection and migration runner.

Runs against the Postgres already on the VPS rather than a managed service: no 500 MB
ceiling, no idle-pausing, and the database sits next to the data it serves.

Migrations are plain ``.sql`` files applied in filename order and recorded with a checksum.
If a file changes after being applied, the runner refuses rather than silently diverging --
a schema you cannot reconstruct from the repository is a schema nobody can reason about.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "MIGRATIONS_DIR",
    "MigrationError",
    "applied_migrations",
    "connect",
    "database_url",
    "migrate",
]

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infrastructure" / "migrations"


class MigrationError(RuntimeError):
    """Raised when the schema on disk and the schema in the database disagree."""


def database_url() -> str:
    url = os.environ.get("WNBA_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "WNBA_DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return url


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection[dict[str, object]]]:
    """A connection with dict rows and an explicit transaction boundary."""
    with psycopg.connect(url or database_url(), row_factory=dict_row) as conn:
        yield conn


def _checksum(sql: str) -> str:
    # Normalise line endings so a Windows checkout and a Linux server agree.
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _migration_files(directory: Path | None = None) -> list[Path]:
    root = directory or MIGRATIONS_DIR
    return sorted(root.glob("*.sql"))


def applied_migrations(conn: psycopg.Connection[dict[str, object]]) -> dict[str, str]:
    """Version -> checksum for everything already applied. Empty if the table is absent."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('wnba.schema_migrations') AS t")
        row = cur.fetchone()
        if row is None or row["t"] is None:
            return {}
        cur.execute("SELECT version, checksum FROM wnba.schema_migrations")
        return {str(r["version"]): str(r["checksum"]) for r in cur.fetchall()}


def migrate(
    url: str | None = None,
    directory: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Apply pending migrations in order. Returns the versions applied."""
    files = _migration_files(directory)
    if not files:
        raise MigrationError(f"no migrations found in {directory or MIGRATIONS_DIR}")

    applied_now: list[str] = []
    with connect(url) as conn:
        already = applied_migrations(conn)

        for path in files:
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)

            if version in already:
                if already[version] != checksum:
                    raise MigrationError(
                        f"{version} was already applied but its file has changed since. "
                        "Write a new migration instead of editing an applied one -- the "
                        "database and the repository must stay reconstructible from each other."
                    )
                continue

            if dry_run:
                applied_now.append(version)
                continue

            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO wnba.schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
            conn.commit()
            applied_now.append(version)

    return applied_now
