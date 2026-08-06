"""Settling the owner's own slips.

The failure this closes was not a bug that broke something; it was a feature that silently
produced no evidence. `pick_legs.result` defaulted to 'pending' and nothing ever wrote anything
else, so a year of confirmed picks would have taught the platform exactly nothing.

The properties worth pinning are the ones that decide whether the resulting signal is real: a leg
settles against its own line, an unidentifiable player never settles at all, and a slip stays
open until its last leg lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from wnba_services.learning_loop import pick_settlement
from wnba_services.learning_loop.pick_settlement import (
    realised_multiple,
    resolve_player_id,
    settle_pick_slips,
)

from tests.fixtures.fake_db import FakeDatabase, statements_matching

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
SLIP = uuid4()
LEG = uuid4()
EPISODE = uuid4()
PLAYER = uuid4()


def _database(
    *,
    line: float = 20.5,
    side: str = "over",
    actual: float = 24.0,
    minutes: float | None = 31.0,
    voided: bool = False,
    player_id: object = PLAYER,
) -> FakeDatabase:
    database = FakeDatabase()
    database.when(
        "FROM wnba.pick_legs l JOIN wnba.pick_slips s USING (pick_slip_id)",
        [
            {
                "pick_leg_id": LEG,
                "pick_slip_id": SLIP,
                "projection_id": None,
                "player_id": player_id,
                "prop_type": "points",
                "side": side,
                "line": line,
                "created_at": datetime(2026, 8, 4, 22, 0, tzinfo=UTC),
            }
        ],
    )
    database.when(
        "FROM wnba.decision_episodes d JOIN wnba.episode_outcomes o",
        [
            {
                "episode_id": EPISODE,
                "actual_stat": actual,
                "actual_minutes": minutes,
                "was_voided": voided,
            }
        ],
    )
    database.when(
        "FROM wnba.pick_slips s JOIN wnba.pick_legs l USING (pick_slip_id)",
        [{"entry_type": "power", "platform": "PrizePicks", "total": 1, "settled": 1, "won": 1}],
    )
    return database


def _result(database: FakeDatabase) -> str:
    update = statements_matching(database.statements, "UPDATE wnba.pick_legs")[0]
    return str(update[1][0])


# --------------------------------------------------------------------------------------
# A leg settles against the bet that was actually taken
# --------------------------------------------------------------------------------------
def test_a_leg_settles_against_its_own_line_not_the_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The book offered 20.5 and our archive saw something else. The owner bet 20.5."""
    database = _database(line=25.5, actual=24.0)
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    batch = settle_pick_slips(now=NOW)

    assert batch.legs_settled == 1
    assert _result(database) == "loss", "24 does not clear 25.5, whatever the episode's line was"


def test_an_under_is_graded_in_the_direction_it_was_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(side="under", line=25.5, actual=24.0)
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    settle_pick_slips(now=NOW)

    assert _result(database) == "win"


def test_a_player_who_never_took_the_floor_voids_rather_than_loses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(minutes=0.0)
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    batch = settle_pick_slips(now=NOW)

    assert _result(database) == "void"
    assert batch.voided == 1


def test_an_exact_landing_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    database = _database(line=24.0, actual=24.0)
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    settle_pick_slips(now=NOW)

    assert _result(database) == "push"


# --------------------------------------------------------------------------------------
# What cannot be identified is never guessed
# --------------------------------------------------------------------------------------
def test_an_unresolved_player_stays_pending_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settling the wrong player looks exactly like settling the right one. Nothing is guessed."""
    database = _database(player_id=None)
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    batch = settle_pick_slips(now=NOW)

    assert batch.legs_settled == 0
    assert batch.legs_unmatched == 1
    assert not statements_matching(database.statements, "UPDATE wnba.pick_legs")


def test_an_ambiguous_name_resolves_to_nothing() -> None:
    database = FakeDatabase().when(
        "FROM wnba.players WHERE lower(full_name)",
        [{"player_id": uuid4()}, {"player_id": uuid4()}],
    )
    with database.connect() as conn, conn.cursor() as cur:
        assert resolve_player_id(cur, "A Shared Name") is None


def test_a_unique_name_resolves() -> None:
    database = FakeDatabase().when(
        "FROM wnba.players WHERE lower(full_name)", [{"player_id": PLAYER}]
    )
    with database.connect() as conn, conn.cursor() as cur:
        assert resolve_player_id(cur, "  A'ja  Wilson ") == PLAYER


def test_an_empty_name_is_not_a_lookup() -> None:
    database = FakeDatabase()
    with database.connect() as conn, conn.cursor() as cur:
        assert resolve_player_id(cur, "   ") is None
    assert not database.statements


# --------------------------------------------------------------------------------------
# The slip closes only when it is finished
# --------------------------------------------------------------------------------------
def test_a_finished_slip_records_its_realised_multiple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database()
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    batch = settle_pick_slips(now=NOW)

    assert batch.slips_settled == 1
    closing = statements_matching(
        database.statements, "UPDATE wnba.pick_slips SET status='settled'"
    )
    assert closing, "a slip with every leg settled must close"


def test_a_partial_slip_records_progress_and_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database()
    database.responses = [
        response
        for response in database.responses
        if "FROM wnba.pick_slips s JOIN wnba.pick_legs l" not in response.match
    ]
    database.when(
        "FROM wnba.pick_slips s JOIN wnba.pick_legs l USING (pick_slip_id)",
        [{"entry_type": "power", "platform": "PrizePicks", "total": 3, "settled": 1, "won": 1}],
    )
    monkeypatch.setattr(pick_settlement, "connect", database.connect)

    batch = settle_pick_slips(now=NOW)

    assert batch.slips_settled == 0
    assert not statements_matching(
        database.statements, "UPDATE wnba.pick_slips SET status='settled'"
    )
    assert statements_matching(database.statements, "UPDATE wnba.pick_slips SET legs_settled")


# --------------------------------------------------------------------------------------
# Realised payout is a reconstruction, and knows where it stops
# --------------------------------------------------------------------------------------
def test_a_known_product_reconstructs_its_payout() -> None:
    paid = realised_multiple(platform="PrizePicks", entry_type="power", leg_count=2, legs_won=2)
    lost = realised_multiple(platform="PrizePicks", entry_type="power", leg_count=2, legs_won=1)

    assert paid is not None and paid > Decimal("1")
    assert lost == Decimal("0")


def test_an_unknown_platform_or_sportsbook_slip_reports_nothing() -> None:
    assert (
        realised_multiple(platform="SomeBook", entry_type="power", leg_count=2, legs_won=2) is None
    )
    assert (
        realised_multiple(platform="PrizePicks", entry_type="sportsbook", leg_count=2, legs_won=2)
        is None
    )
