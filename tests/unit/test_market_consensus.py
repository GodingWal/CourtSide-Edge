"""Cross-source consensus, dispersion, best price, and the closing-line designation.

The single most important property: with one source, nothing here claims corroboration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from wnba_services.market_engine.consensus import (
    DEFAULT_RELIABILITY,
    SourceQuote,
    SourceReliability,
    build_consensus,
    closing_quote,
    line_value,
)

NOW = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)


def _quote(
    source: str,
    line: str,
    *,
    minutes_late: int = 0,
    over: str = "1.00",
    under: str = "1.00",
    available: bool = True,
) -> SourceQuote:
    return SourceQuote(
        source=source,
        line=Decimal(line),
        observed_at=NOW + timedelta(minutes=minutes_late),
        over_multiplier=Decimal(over),
        under_multiplier=Decimal(under),
        is_available=available,
    )


# --------------------------------------------------------------------------------------
# One source is not a consensus
# --------------------------------------------------------------------------------------
def test_a_single_source_is_reported_as_uncorroborated() -> None:
    """The distinction the module exists to preserve."""
    consensus = build_consensus([_quote("underdog", "15.5")])
    assert consensus is not None
    assert consensus.source_count == 1
    assert not consensus.is_corroborated
    assert consensus.line == Decimal("15.5")


def test_a_single_source_has_no_dispersion_rather_than_zero_dispersion() -> None:
    """Zero would read as perfect agreement; the truth is that nothing was compared."""
    consensus = build_consensus([_quote("underdog", "15.5")])
    assert consensus is not None
    assert consensus.dispersion is None
    assert not consensus.sources_disagree


def test_two_sources_are_corroborated_and_measurable() -> None:
    consensus = build_consensus([_quote("underdog", "15.5"), _quote("prizepicks", "16.5")])
    assert consensus is not None
    assert consensus.is_corroborated
    assert consensus.dispersion == pytest.approx(0.5)


def test_an_empty_or_unavailable_board_yields_nothing() -> None:
    assert build_consensus([]) is None
    assert build_consensus([_quote("underdog", "15.5", available=False)]) is None


# --------------------------------------------------------------------------------------
# One opinion per source
# --------------------------------------------------------------------------------------
def test_a_source_that_moved_its_line_still_gets_one_vote() -> None:
    """Otherwise the busiest operator outvotes the rest by polling frequency alone."""
    consensus = build_consensus(
        [
            _quote("underdog", "14.5", minutes_late=0),
            _quote("underdog", "15.5", minutes_late=10),
            _quote("underdog", "16.5", minutes_late=20),
            _quote("prizepicks", "20.5"),
        ]
    )
    assert consensus is not None
    assert consensus.source_count == 2
    assert set(consensus.sources) == {"underdog", "prizepicks"}


def test_the_surviving_quote_from_a_source_is_its_latest() -> None:
    consensus = build_consensus(
        [_quote("underdog", "14.5", minutes_late=0), _quote("underdog", "18.5", minutes_late=30)]
    )
    assert consensus is not None
    assert consensus.line == Decimal("18.5")


# --------------------------------------------------------------------------------------
# The consensus line
# --------------------------------------------------------------------------------------
def test_the_consensus_resists_a_single_outlier() -> None:
    """A median cannot be dragged by one operator posting a wild number; a mean can."""
    consensus = build_consensus(
        [
            _quote("a", "15.5"),
            _quote("b", "15.5"),
            _quote("c", "15.5"),
            _quote("outlier", "40.5"),
        ]
    )
    assert consensus is not None
    assert consensus.line == Decimal("15.5")


def test_the_consensus_stays_on_a_real_line() -> None:
    """Averaging 15.5 and 16.5 gives 16.0, which no operator was offering."""
    consensus = build_consensus([_quote("a", "15.5"), _quote("b", "16.5")])
    assert consensus is not None
    assert consensus.line in {Decimal("15.5"), Decimal("16.5")}


def test_a_more_reliable_source_carries_more_weight() -> None:
    quotes = [_quote("trusted", "14.5"), _quote("unproven", "18.5")]
    reliability = {
        "trusted": SourceReliability("trusted", weight=0.95, sample_size=5000),
        "unproven": SourceReliability("unproven", weight=0.10),
    }
    consensus = build_consensus(quotes, reliability)
    assert consensus is not None
    assert consensus.line == Decimal("14.5")


def test_an_unmeasured_source_does_not_arrive_fully_trusted() -> None:
    assert DEFAULT_RELIABILITY < 1.0
    assert not SourceReliability("brand_new").is_measured


def test_wide_disagreement_is_flagged() -> None:
    calm = build_consensus([_quote("a", "15.5"), _quote("b", "16.0")])
    wild = build_consensus([_quote("a", "12.5"), _quote("b", "19.5")])
    assert calm is not None and wild is not None
    assert not calm.sources_disagree
    assert wild.sources_disagree


# --------------------------------------------------------------------------------------
# Best price
# --------------------------------------------------------------------------------------
def test_the_best_over_is_the_lowest_line() -> None:
    consensus = build_consensus([_quote("cheap", "14.5"), _quote("dear", "17.5")])
    assert consensus is not None
    assert consensus.best_over_source == "cheap"
    assert consensus.best_under_source == "dear"


def test_the_payout_breaks_a_tie_on_the_line() -> None:
    consensus = build_consensus(
        [_quote("flat", "15.5", over="1.00"), _quote("boosted", "15.5", over="1.25")]
    )
    assert consensus is not None
    assert consensus.best_over_source == "boosted"


# --------------------------------------------------------------------------------------
# Closing line
# --------------------------------------------------------------------------------------
def test_the_closing_line_ignores_anything_observed_after_lock() -> None:
    """A line that moved once the market closed was never available to act on."""
    locks_at = NOW + timedelta(minutes=15)
    closing = closing_quote(
        [_quote("underdog", "15.5", minutes_late=10), _quote("underdog", "22.5", minutes_late=30)],
        locks_at,
    )
    assert closing is not None
    assert closing.line == Decimal("15.5")


def test_a_market_with_no_pre_lock_observation_has_no_closing_line() -> None:
    closing = closing_quote([_quote("underdog", "15.5", minutes_late=60)], NOW)
    assert closing is None


def test_the_closing_line_records_how_many_sources_agreed_on_it() -> None:
    closing = closing_quote([_quote("a", "15.5"), _quote("b", "15.5")], NOW + timedelta(minutes=5))
    assert closing is not None
    assert closing.source_count == 2
    assert closing.to_payload()["is_corroborated"] is True


# --------------------------------------------------------------------------------------
# Line value
# --------------------------------------------------------------------------------------
def test_the_line_moving_away_from_our_over_is_positive_value() -> None:
    """Taking over 15.5 when the market closes at 16.5 means we got the better number."""
    assert line_value(
        forecast_line=Decimal("15.5"), closing_line=Decimal("16.5"), side="over"
    ) == pytest.approx(1.0)


def test_the_line_moving_toward_our_over_is_negative_value() -> None:
    assert line_value(
        forecast_line=Decimal("15.5"), closing_line=Decimal("14.5"), side="over"
    ) == pytest.approx(-1.0)


def test_under_line_value_is_symmetric() -> None:
    assert line_value(
        forecast_line=Decimal("15.5"), closing_line=Decimal("14.5"), side="under"
    ) == pytest.approx(1.0)


def test_an_unmoved_line_has_no_value_either_way() -> None:
    for side in ("over", "under"):
        assert (
            line_value(forecast_line=Decimal("15.5"), closing_line=Decimal("15.5"), side=side)
            == 0.0
        )


def test_an_unknown_side_is_refused() -> None:
    with pytest.raises(ValueError, match=r"over.*under"):
        line_value(forecast_line=Decimal("1"), closing_line=Decimal("1"), side="middle")


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------
def test_the_payload_never_hides_that_a_lone_source_was_alone() -> None:
    payload = build_consensus([_quote("underdog", "15.5")])
    assert payload is not None
    body = payload.to_payload()
    assert body["source_count"] == 1
    assert body["is_corroborated"] is False
    assert body["dispersion"] is None


def test_a_flat_pickem_quote_is_not_treated_as_priced() -> None:
    assert not _quote("underdog", "15.5", over="1.00", under="1.00").is_priced
    assert _quote("underdog", "15.5", over="0.80", under="1.25").is_priced
