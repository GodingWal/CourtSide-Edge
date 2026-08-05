"""The second market source: the gate in front of it, and the synchronisation behind it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from wnba_domain.identity import SourceName
from wnba_services.ingestion.adapters.the_odds_api import (
    PROP_MARKETS,
    TheOddsApiAdapter,
    map_market_to_prop_type,
    parse_events,
    parse_player_props,
)
from wnba_services.ingestion.source_permissions import (
    SourceNotPermitted,
    require_permitted_source,
)
from wnba_services.market_engine.consensus import SourceQuote
from wnba_services.market_engine.synchronise import (
    SYNCHRONISATION_WINDOW,
    designate,
    synchronise,
)

NOW = datetime(2026, 8, 5, 18, tzinfo=UTC)
LOCKS = NOW + timedelta(hours=2)


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def execute(self, query: str, params: Any = ()) -> None:
        return None

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def _verification(**overrides: Any) -> dict[str, Any]:
    row = {
        "source": "the_odds_api",
        "verified_by": "Goding Wal",
        "verified_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=180),
        "conclusion": "permitted",
        "rate_limit": "500 requests/month",
        "attribution_required": True,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------------------
# The permission gate
# --------------------------------------------------------------------------------------
def test_a_source_nobody_has_reviewed_cannot_be_collected() -> None:
    with pytest.raises(SourceNotPermitted, match="no current terms verification"):
        require_permitted_source(_Cursor(None), "the_odds_api", now=NOW)


@pytest.mark.parametrize("conclusion", ["prohibited", "unclear"])
def test_only_permitted_enables_collection(conclusion: str) -> None:
    """'Somebody looked and could not tell' is a real outcome and it is not permission."""
    with pytest.raises(SourceNotPermitted, match="Only 'permitted'"):
        require_permitted_source(
            _Cursor(_verification(conclusion=conclusion)), "the_odds_api", now=NOW
        )


def test_a_current_permitted_verification_allows_collection() -> None:
    verification = require_permitted_source(_Cursor(_verification()), "the_odds_api", now=NOW)

    assert verification.is_current
    assert verification.verified_by == "Goding Wal"
    assert verification.attribution_required is True


def test_the_source_is_a_known_name_but_a_name_is_not_permission() -> None:
    assert SourceName.THE_ODDS_API.value == "the_odds_api"
    with pytest.raises(SourceNotPermitted):
        require_permitted_source(_Cursor(None), SourceName.THE_ODDS_API.value, now=NOW)


def test_the_adapter_refuses_to_run_without_a_key() -> None:
    """Metered and keyed, with no unauthenticated fallback."""
    adapter = TheOddsApiAdapter(api_key="")

    with pytest.raises(RuntimeError, match="ODDS_API_KEY"):
        adapter.fetch_events(object())


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------
def test_over_and_under_siblings_are_paired_into_one_two_sided_quote() -> None:
    payload = {
        "id": "evt-1",
        "commence_time": "2026-08-05T23:00:00Z",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "A Player",
                                "point": 14.5,
                                "price": -115,
                            },
                            {
                                "name": "Under",
                                "description": "A Player",
                                "point": 14.5,
                                "price": -105,
                            },
                        ],
                    }
                ],
            }
        ],
    }

    quotes = parse_player_props(payload, observed_at=NOW)

    assert len(quotes) == 1
    assert quotes[0].is_two_sided
    assert quotes[0].over_american_odds == -115
    assert quotes[0].under_american_odds == -105


def test_a_one_sided_market_is_not_completed_by_inventing_the_other_side() -> None:
    payload = {
        "id": "evt-1",
        "bookmakers": [
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "A Player",
                                "point": 14.5,
                                "price": -120,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    quotes = parse_player_props(payload, observed_at=NOW)

    assert len(quotes) == 1
    assert not quotes[0].is_two_sided
    assert quotes[0].under_american_odds is None


def test_two_books_stay_two_opinions() -> None:
    """Averaging inside the adapter would destroy the dispersion the consensus layer measures."""
    payload = {
        "id": "evt-1",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "A Player",
                                "point": 14.5,
                                "price": -115,
                            },
                            {
                                "name": "Under",
                                "description": "A Player",
                                "point": 14.5,
                                "price": -105,
                            },
                        ],
                    }
                ],
            },
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "A Player",
                                "point": 15.5,
                                "price": -110,
                            },
                            {
                                "name": "Under",
                                "description": "A Player",
                                "point": 15.5,
                                "price": -110,
                            },
                        ],
                    }
                ],
            },
        ],
    }

    quotes = parse_player_props(payload, observed_at=NOW)

    assert {quote.bookmaker for quote in quotes} == {"draftkings", "fanduel"}
    assert {float(quote.line) for quote in quotes} == {14.5, 15.5}


def test_a_line_off_the_half_point_grid_is_a_parse_error_not_something_to_round() -> None:
    payload = {
        "id": "evt-1",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "A Player",
                                "point": 14.3,
                                "price": -115,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    assert parse_player_props(payload, observed_at=NOW) == []


def test_unmapped_markets_are_skipped_rather_than_guessed() -> None:
    assert map_market_to_prop_type("player_blocks") is None
    assert map_market_to_prop_type("player_points") is not None
    assert len(PROP_MARKETS) >= 8


def test_events_parse_to_ids_and_tip_times() -> None:
    events = parse_events(
        [{"id": "evt-1", "commence_time": "2026-08-05T23:00:00Z", "home_team": "LVA"}]
    )

    assert events[0]["event_id"] == "evt-1"
    assert events[0]["commence_time"] == datetime(2026, 8, 5, 23, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# Timestamp synchronisation
# --------------------------------------------------------------------------------------
def _quote(source: str, line: float) -> SourceQuote:
    return SourceQuote(
        source=source,
        line=Decimal(str(line)),
        observed_at=NOW,
        over_multiplier=Decimal("1.90"),
        under_multiplier=Decimal("1.90"),
    )


def test_quotes_an_hour_apart_are_two_moments_not_a_disagreement() -> None:
    """The failure that makes naive multi-source data worse than single-source data."""
    groups = synchronise(
        [
            (NOW - timedelta(hours=1), _quote("underdog", 14.5)),
            (NOW, _quote("the_odds_api", 15.5)),
        ]
    )

    assert len(groups) == 2
    assert all(len(group.sources) == 1 for group in groups)
    assert not any(group.is_corroborated for group in groups)


def test_quotes_inside_the_window_are_one_comparable_moment() -> None:
    groups = synchronise(
        [
            (NOW, _quote("underdog", 14.5)),
            (NOW + timedelta(minutes=2), _quote("the_odds_api", 15.0)),
        ]
    )

    assert len(groups) == 1
    assert groups[0].is_corroborated
    assert groups[0].spread_seconds == 120


def test_a_source_that_reported_twice_contributes_its_latest_view_only() -> None:
    groups = synchronise(
        [
            (NOW, _quote("underdog", 14.5)),
            (NOW + timedelta(minutes=1), _quote("underdog", 15.5)),
        ]
    )

    assert len(groups) == 1
    assert len(groups[0].quotes) == 1
    assert float(groups[0].quotes[0].line) == 15.5


def test_the_synchronisation_window_sits_between_jitter_and_news() -> None:
    assert timedelta(minutes=1) < SYNCHRONISATION_WINDOW < timedelta(minutes=30)


def test_the_closing_designation_is_made_before_the_market_closes() -> None:
    """Chosen afterwards, 'the closing line' is whichever quote flatters the result."""
    assert designate(LOCKS - timedelta(minutes=5), LOCKS, is_first=False) == "closing"
    assert designate(LOCKS - timedelta(hours=5), LOCKS, is_first=False) == "intermediate"
    assert designate(LOCKS - timedelta(hours=5), LOCKS, is_first=True) == "opening"
