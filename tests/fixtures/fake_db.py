"""A scriptable stand-in for the database, so pipeline behaviour can be tested without one.

The unit suite covers the pure functions well and the live pipeline not at all, which leaves the
most expensive class of bug -- the one where every component works and the loop still produces
nothing -- outside the tests entirely. Three such failures had already happened in production and
each was caught by a human noticing an empty screen.

Running real PostgreSQL in CI would test the SQL too, and would be better. This is the cheaper
thing that can exist today: it verifies the *contract* between the pipeline and its store -- which
statements run, in what order, guarded by what, and what the code does with the rows it gets back.
That is enough to catch a stage that silently skips everything, which is the failure mode that
actually occurred.

What it deliberately does not do is interpret SQL. Responses are matched by substring, so a test
says "when something selects from prop_quotes, hand back these two rows" and stays readable. The
cost is that a query can be wrong in ways this cannot see; the leakage and data-quality suites
exist for that, and real integration coverage remains worth adding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

__all__ = ["FakeConnection", "FakeCursor", "FakeDatabase", "Response", "statements_matching"]


@dataclass
class Response:
    """Rows to return when a statement contains ``match``."""

    match: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    rowcount: int | None = None
    """Reported ``rowcount``. Defaults to ``len(rows)``; set explicitly for UPDATE/INSERT."""

    once: bool = False
    """Consume after a single use, so successive identical queries can differ."""

    consumed: bool = False

    def matches(self, sql: str) -> bool:
        return not self.consumed and self.match in " ".join(sql.split())


class FakeCursor:
    """Records every statement and answers from the first matching scripted response."""

    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._rows: list[dict[str, Any]] = []
        self.rowcount: int = 0

    def execute(self, query: str, params: Any = None, /) -> None:
        normalised = " ".join(query.split())
        self._database.statements.append((normalised, params))
        for response in self._database.responses:
            if response.matches(normalised):
                if response.once:
                    response.consumed = True
                self._rows = [dict(row) for row in response.rows]
                self.rowcount = (
                    response.rowcount if response.rowcount is not None else len(response.rows)
                )
                return
        self._rows = []
        self.rowcount = 0

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._database)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class FakeDatabase:
    """Scripted responses plus the transcript of everything that was asked."""

    responses: list[Response] = field(default_factory=list)
    statements: list[tuple[str, Any]] = field(default_factory=list)
    connections: list[FakeConnection] = field(default_factory=list)

    def when(
        self,
        match: str,
        rows: list[dict[str, Any]] | None = None,
        *,
        rowcount: int | None = None,
        once: bool = False,
    ) -> FakeDatabase:
        """Script a response. Returns self so calls chain."""
        self.responses.append(Response(match=match, rows=rows or [], rowcount=rowcount, once=once))
        return self

    def connect(self, *_args: Any, **_kwargs: Any) -> FakeConnection:
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    # ----------------------------------------------------------------------------------
    # Assertions
    # ----------------------------------------------------------------------------------
    def executed(self, fragment: str) -> list[tuple[str, Any]]:
        return statements_matching(self.statements, fragment)

    def ran(self, fragment: str) -> bool:
        return bool(self.executed(fragment))

    def count(self, fragment: str) -> int:
        return len(self.executed(fragment))

    def params_for(self, fragment: str) -> list[Any]:
        return [params for _, params in self.executed(fragment)]

    def order_of(self, *fragments: str) -> list[int]:
        """First index at which each fragment appears, for asserting stage ordering."""
        found: list[int] = []
        for fragment in fragments:
            index = next(
                (position for position, (sql, _) in enumerate(self.statements) if fragment in sql),
                -1,
            )
            found.append(index)
        return found


def statements_matching(statements: list[tuple[str, Any]], fragment: str) -> list[tuple[str, Any]]:
    """Statements containing ``fragment``, whitespace-insensitively."""
    needle = " ".join(fragment.split())
    pattern = re.compile(re.escape(needle))
    return [(sql, params) for sql, params in statements if pattern.search(sql)]
