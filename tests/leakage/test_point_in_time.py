"""The leakage gate.

This suite exists to stop the single most common way a betting backtest becomes fiction:
reading a fact that was *true* before the forecast moment but only *known* afterwards.

The canonical scenario, which happens several times every WNBA game day:

    14:30  player listed QUESTIONABLE   (we observe it at 14:34)
    17:40  player ruled OUT             (we observe it at 17:41)
    19:00  tip-off

A forecast generated at 15:00 must see QUESTIONABLE. A backtest that lets it see OUT will
report magnificent accuracy and lose money in production, because in production 17:40 has not
happened yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from wnba_domain import (
    InjuryDesignation,
    InjuryStatusRecord,
    MarketKind,
    PropQuote,
    PropType,
    SourceName,
    SourceRef,
)
from wnba_store import LeakageError, as_of, assert_no_leakage, latest_known, timeline

PLAYER_ID = uuid4()
GAME_ID = uuid4()

QUESTIONABLE_VALID = datetime(2026, 8, 2, 14, 30, tzinfo=UTC)
QUESTIONABLE_OBSERVED = datetime(2026, 8, 2, 14, 34, tzinfo=UTC)
OUT_VALID = datetime(2026, 8, 2, 17, 40, tzinfo=UTC)
OUT_OBSERVED = datetime(2026, 8, 2, 17, 41, tzinfo=UTC)
TIPOFF = datetime(2026, 8, 2, 19, 0, tzinfo=UTC)


def _ref(retrieved_at: datetime) -> SourceRef:
    return SourceRef(
        source=SourceName.ESPN,
        source_entity_id=f"injury-{retrieved_at.isoformat()}",
        retrieved_at=retrieved_at,
    )


@pytest.fixture
def injury_timeline() -> list[InjuryStatusRecord]:
    """The scenario above, modelled with correct row versioning on **both** axes.

    Closing off the questionable row is itself an event in system time. Until 17:41 we believed
    the questionable status ran indefinitely; only at 17:41 did we learn it had in fact ended at
    17:40. Collapsing that into a single row with ``valid_to`` set at creation would encode
    knowledge we did not have -- the exact error this suite exists to catch, committed by the
    fixture rather than the model.
    """
    believed_open_ended = InjuryStatusRecord(
        player_id=PLAYER_ID,
        game_id=GAME_ID,
        designation=InjuryDesignation.QUESTIONABLE,
        source_ref=_ref(QUESTIONABLE_OBSERVED),
        valid_from=QUESTIONABLE_VALID,
        valid_to=None,
        system_from=QUESTIONABLE_OBSERVED,
        system_to=OUT_OBSERVED,
    )
    retroactively_closed = InjuryStatusRecord(
        player_id=PLAYER_ID,
        game_id=GAME_ID,
        designation=InjuryDesignation.QUESTIONABLE,
        source_ref=_ref(OUT_OBSERVED),
        valid_from=QUESTIONABLE_VALID,
        valid_to=OUT_VALID,
        system_from=OUT_OBSERVED,
    )
    ruled_out = InjuryStatusRecord(
        player_id=PLAYER_ID,
        game_id=GAME_ID,
        designation=InjuryDesignation.OUT,
        source_ref=_ref(OUT_OBSERVED),
        valid_from=OUT_VALID,
        system_from=OUT_OBSERVED,
    )
    return [believed_open_ended, retroactively_closed, ruled_out]


# --------------------------------------------------------------------------------------
# The core gate
# --------------------------------------------------------------------------------------
def test_forecast_before_the_news_cannot_see_the_news(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    forecast_time = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)

    visible = as_of(injury_timeline, valid_time=forecast_time, system_time=forecast_time)

    assert len(visible) == 1
    assert visible[0].designation is InjuryDesignation.QUESTIONABLE, (
        "A 15:00 forecast saw the 17:40 ruling. This is look-ahead leakage, and every "
        "backtest downstream of it is fiction."
    )


def test_forecast_after_the_news_sees_the_news(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    forecast_time = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)

    visible = as_of(injury_timeline, valid_time=forecast_time, system_time=forecast_time)

    assert len(visible) == 1
    assert visible[0].designation is InjuryDesignation.OUT


def test_the_naive_valid_time_only_read_is_the_bug_we_are_preventing(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    """Demonstrates the failure mode explicitly, so the guard's value is not theoretical.

    A source publishes a retroactive correction after the game: valid from 14:30, but observed
    at 22:00. Filtering on the valid axis alone happily hands that correction to a 15:00
    forecast. The system axis is the only thing standing between a backtest and a fantasy.
    """
    backdated = InjuryStatusRecord(
        player_id=PLAYER_ID,
        game_id=GAME_ID,
        designation=InjuryDesignation.OUT,
        detail="retroactive correction published after the game",
        source_ref=_ref(TIPOFF + timedelta(hours=3)),
        valid_from=QUESTIONABLE_VALID,
        system_from=TIPOFF + timedelta(hours=3),
    )
    corrupted = [*injury_timeline, backdated]
    forecast_time = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)

    naive = {r.designation for r in corrupted if r.is_valid_at(forecast_time)}
    assert InjuryDesignation.OUT in naive, "the naive read is contaminated, as advertised"

    honest = {
        r.designation for r in as_of(corrupted, valid_time=forecast_time, system_time=forecast_time)
    }
    assert honest == {InjuryDesignation.QUESTIONABLE}


def test_replaying_a_past_moment_with_todays_knowledge(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    """The two axes answer genuinely different questions, and both are needed.

    *What did we believe at 15:00?* -> questionable, open-ended.
    *What was actually true at 15:00, as best we now know?* -> questionable, and we now know it
    ended at 17:40.

    The first drives backtesting. The second drives error attribution after the fact. A
    single-axis store can express one or the other, never both.
    """
    past = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    believed_then = as_of(injury_timeline, valid_time=past, system_time=past)
    assert len(believed_then) == 1
    assert believed_then[0].valid_to is None

    known_now = as_of(injury_timeline, valid_time=past, system_time=now)
    assert len(known_now) == 1
    assert known_now[0].designation is InjuryDesignation.QUESTIONABLE
    assert known_now[0].valid_to == OUT_VALID


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (QUESTIONABLE_OBSERVED - timedelta(seconds=1), None),
        (QUESTIONABLE_OBSERVED, InjuryDesignation.QUESTIONABLE),
        (OUT_OBSERVED - timedelta(seconds=1), InjuryDesignation.QUESTIONABLE),
        (OUT_OBSERVED, InjuryDesignation.OUT),
        (TIPOFF, InjuryDesignation.OUT),
    ],
)
def test_belief_at_each_boundary(
    injury_timeline: list[InjuryStatusRecord],
    moment: datetime,
    expected: InjuryDesignation | None,
) -> None:
    """Boundaries are inclusive on the lower bound and exclusive on the upper."""
    record = latest_known(injury_timeline, system_time=moment)
    assert (record.designation if record else None) is expected


def test_nothing_is_known_before_the_first_observation(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    early = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
    assert as_of(injury_timeline, valid_time=early, system_time=early) == []
    assert latest_known(injury_timeline, system_time=early) is None


# --------------------------------------------------------------------------------------
# The feature-builder guard
# --------------------------------------------------------------------------------------
def test_assert_no_leakage_rejects_a_future_record(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    forecast_time = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)

    with pytest.raises(LeakageError, match="not known until"):
        assert_no_leakage(injury_timeline, forecast_time=forecast_time)


def test_assert_no_leakage_passes_on_a_clean_set(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    forecast_time = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
    clean = as_of(injury_timeline, valid_time=forecast_time, system_time=forecast_time)
    assert_no_leakage(clean, forecast_time=forecast_time)


# --------------------------------------------------------------------------------------
# The same rules apply to market data
# --------------------------------------------------------------------------------------
def test_quotes_obey_the_same_temporal_rules() -> None:
    """A quote observed at 18:00 must not price a 15:00 forecast.

    This matters more in a pick'em market than a sportsbook one: with no two-sided price to
    sanity-check against, a stale or future-dated line is not obviously wrong when you look at
    it -- it is just a number that produces a confident, meaningless edge.
    """
    early = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    late = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)

    def quote(line: str, observed: datetime) -> PropQuote:
        return PropQuote(
            quote_id=uuid4(),
            source=SourceName.PRIZEPICKS,
            source_quote_id=f"pp-{observed.isoformat()}",
            player_id=PLAYER_ID,
            game_id=GAME_ID,
            prop_type=PropType.POINTS,
            line=Decimal(line),
            market_kind=MarketKind.PICKEM,
            source_ref=_ref(observed),
            valid_from=observed,
            system_from=observed,
        )

    quotes = [quote("21.5", early), quote("18.5", late)]
    forecast_time = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)

    visible = as_of(quotes, valid_time=forecast_time, system_time=forecast_time)
    assert [q.line for q in visible] == [Decimal("21.5")]

    current = latest_known(quotes, system_time=late)
    assert current is not None
    assert current.line == Decimal("18.5")
    assert current.age_seconds_at(late) == 0.0


def test_timeline_replays_in_observation_order(
    injury_timeline: list[InjuryStatusRecord],
) -> None:
    ordered = timeline(list(reversed(injury_timeline)))
    assert [(r.designation, r.system_from) for r in ordered] == [
        (InjuryDesignation.QUESTIONABLE, QUESTIONABLE_OBSERVED),
        (InjuryDesignation.QUESTIONABLE, OUT_OBSERVED),
        (InjuryDesignation.OUT, OUT_OBSERVED),
    ]
