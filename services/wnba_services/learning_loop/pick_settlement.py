"""Settling the slips the owner actually confirmed.

``pick_legs.result`` has carried a 'pending' default since migration 020 and nothing ever wrote
anything else. That made the entire pick surface -- the parlay builder, the DeepSeek text parse,
the screenshot import -- a write-only feature: it recorded what a human decided and then never
found out whether they were right. Every other decision in this platform settles.

The design follows the paper-episode settler rather than inventing a second one:

**The stat decides, not the episode.** A leg is resolved against *its own* line and side using
:func:`resolve_outcome`, the same pure function the episode settler uses. The linked episode
supplies the observed stat and nothing else. A book that offered 20.5 where our archive saw 21.5
is a different bet, and settling it against the archive's line would quietly rewrite what the
owner took.

**A leg that cannot be identified stays pending forever.** Matching runs on a resolved
``player_id`` -- exact, unique, recorded at confirmation -- or on the projection the leg was
built from. There is deliberately no fuzzy name matching: settling the wrong player is a
corrupted learning signal that looks exactly like a correct one, while an unsettled leg is
visibly unsettled.

**The slip settles only when every leg has.** A partial slip has no realised multiple, because
the payout table is a function of the final correct count and a partial count is not that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from wnba_domain.enums import EntryType
from wnba_domain.market import PayoutTable
from wnba_marketmath import prizepicks_payout_table, underdog_payout_table
from wnba_store.db import connect

from wnba_services.learning_loop.settlement import resolve_outcome

__all__ = [
    "PickSettlementBatch",
    "realised_multiple",
    "resolve_player_id",
    "settle_pick_slips",
]

# How far before a slip's creation an episode may have been forecast and still be considered the
# same game. Slips are built in the hours before tip, so a day of slack covers an evening card
# without reaching back to the previous night's games.
_LOOKBACK = timedelta(days=1)

# How long after creation a leg stays eligible for matching. Beyond this the player has played
# several games and "which one did they mean" stops having an answer.
_LOOKAHEAD = timedelta(days=3)


@dataclass(frozen=True)
class PickSettlementBatch:
    legs_settled: int
    legs_unmatched: int
    slips_settled: int
    voided: int


def resolve_player_id(cur: Any, player_name: str) -> UUID | None:
    """Exact, unique, case-insensitive name match, or ``None``.

    Two players sharing a normalised name return ``None`` rather than the first row. The whole
    point of resolving at confirmation time is that an ambiguous name is caught while the person
    who typed it is still looking at the screen.
    """
    cleaned = " ".join(player_name.split())
    if not cleaned:
        return None
    cur.execute(
        "SELECT player_id FROM wnba.players WHERE lower(full_name)=lower(%s) LIMIT 2",
        (cleaned,),
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        return None
    return UUID(str(rows[0]["player_id"]))


def _table_for(platform: str) -> PayoutTable | None:
    """The bundled payout table for a platform name, where one exists.

    Platform is free text the owner typed, so this matches loosely and returns ``None`` rather
    than guessing. A slip on an unrecognised platform settles its legs and leaves the realised
    multiple empty, which is the honest answer for a payout structure we do not hold.
    """
    at = datetime(2026, 8, 3, tzinfo=UTC)
    normalised = platform.strip().lower().replace(" ", "")
    if "prizepicks" in normalised:
        return prizepicks_payout_table(at)
    if "underdog" in normalised:
        return underdog_payout_table(at)
    return None


def realised_multiple(
    *, platform: str, entry_type: str, leg_count: int, legs_won: int
) -> Decimal | None:
    """What the entry actually paid, per unit staked, under the bundled table.

    ``None`` where the table does not cover the product -- a sportsbook parlay, an unrecognised
    platform, or a leg count the operator does not sell. The bundled tables are unverified
    defaults, so this is a reconstruction for scoring rather than an accounting record.
    """
    if entry_type not in {"power", "flex"}:
        return None
    table = _table_for(platform)
    if table is None:
        return None
    try:
        rule = table.rule_for(EntryType(entry_type), leg_count)
    except LookupError:
        return None
    return rule.payouts_by_correct.get(legs_won, Decimal("0"))


def _matching_outcome(cur: Any, leg: dict[str, Any], created_at: datetime) -> dict[str, Any] | None:
    """The settled episode that tells us what this leg's player actually did.

    Preference order is deliberate. A leg built from the board carries the projection it came
    from, and that projection names one game unambiguously -- no window, no heuristic. Only a
    hand-typed or parsed leg falls through to the time-window match.
    """
    if leg["projection_id"] is not None:
        cur.execute(
            """SELECT d.episode_id,o.actual_stat,o.actual_minutes,o.was_voided
               FROM wnba.stat_forecasts f
               JOIN wnba.decision_episodes d
                 ON d.quote_id=f.quote_id AND d.model_run_id=f.model_run_id
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE f.projection_id=%s LIMIT 1""",
            (leg["projection_id"],),
        )
        found = cur.fetchone()
        if found is not None:
            return dict(found)
    if leg["player_id"] is None:
        return None
    cur.execute(
        """SELECT d.episode_id,o.actual_stat,o.actual_minutes,o.was_voided
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           WHERE d.player_id=%s AND d.prop_type=%s
             AND d.forecast_timestamp BETWEEN %s AND %s
           ORDER BY d.forecast_timestamp LIMIT 1""",
        (
            leg["player_id"],
            leg["prop_type"],
            created_at - _LOOKBACK,
            created_at + _LOOKAHEAD,
        ),
    )
    found = cur.fetchone()
    return None if found is None else dict(found)


def settle_pick_slips(*, now: datetime | None = None) -> PickSettlementBatch:
    """Resolve every pending leg whose game has settled, then close finished slips."""
    at = now or datetime.now(UTC)
    legs_settled = legs_unmatched = slips_settled = voided = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT l.pick_leg_id,l.pick_slip_id,l.projection_id,l.player_id,l.prop_type,
                      l.side,l.line,s.created_at
               FROM wnba.pick_legs l
               JOIN wnba.pick_slips s USING (pick_slip_id)
               WHERE l.result='pending' AND s.status IN ('draft','confirmed')
               ORDER BY s.created_at"""
        )
        pending = [dict(row) for row in cur.fetchall()]
        touched_slips: set[UUID] = set()
        for leg in pending:
            created_at = leg["created_at"]
            if not isinstance(created_at, datetime):
                legs_unmatched += 1
                continue
            outcome = _matching_outcome(cur, leg, created_at)
            if outcome is None:
                legs_unmatched += 1
                continue
            minutes = outcome["actual_minutes"]
            resolution = resolve_outcome(
                side=str(leg["side"]),
                actual=float(str(outcome["actual_stat"])),
                forecast_line=float(str(leg["line"])),
                minutes=None if minutes is None else float(str(minutes)),
            )
            # A void upstream stays a void here: the episode settler already decided the player
            # never took the floor, and a leg cannot be graded on a game its subject did not play.
            is_void = bool(outcome["was_voided"]) or resolution.voided
            result = (
                "void"
                if is_void
                else "push"
                if resolution.pushed
                else "win"
                if resolution.hit
                else "loss"
            )
            cur.execute(
                """UPDATE wnba.pick_legs
                   SET result=%s,settled_at=%s,episode_id=%s,actual_stat=%s
                   WHERE pick_leg_id=%s""",
                (
                    result,
                    at,
                    outcome["episode_id"],
                    float(str(outcome["actual_stat"])),
                    leg["pick_leg_id"],
                ),
            )
            legs_settled += 1
            voided += int(is_void)
            touched_slips.add(UUID(str(leg["pick_slip_id"])))

        for slip_id in sorted(touched_slips, key=str):
            cur.execute(
                """SELECT s.entry_type,s.platform,
                          count(*) AS total,
                          count(*) FILTER (WHERE l.result<>'pending') AS settled,
                          count(*) FILTER (WHERE l.result='win') AS won
                   FROM wnba.pick_slips s JOIN wnba.pick_legs l USING (pick_slip_id)
                   WHERE s.pick_slip_id=%s GROUP BY s.entry_type,s.platform""",
                (slip_id,),
            )
            summary = cur.fetchone()
            if summary is None:
                continue
            total, settled = int(str(summary["total"])), int(str(summary["settled"]))
            won = int(str(summary["won"]))
            if settled < total:
                # Partially settled slips record their progress and keep their status. A realised
                # multiple would be a fiction until the last leg lands.
                cur.execute(
                    """UPDATE wnba.pick_slips SET legs_settled=%s,legs_won=%s,updated_at=%s
                       WHERE pick_slip_id=%s""",
                    (settled, won, at, slip_id),
                )
                continue
            cur.execute(
                """UPDATE wnba.pick_slips
                   SET status='settled',settled_at=%s,updated_at=%s,legs_settled=%s,legs_won=%s,
                       realised_multiple=%s
                   WHERE pick_slip_id=%s""",
                (
                    at,
                    at,
                    settled,
                    won,
                    realised_multiple(
                        platform=str(summary["platform"]),
                        entry_type=str(summary["entry_type"]),
                        leg_count=total,
                        legs_won=won,
                    ),
                    slip_id,
                ),
            )
            slips_settled += 1
    return PickSettlementBatch(legs_settled, legs_unmatched, slips_settled, voided)
