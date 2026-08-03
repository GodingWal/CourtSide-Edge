from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wnba_services.ingestion.historical_markets import (
    GameCandidate,
    choose_game,
    legacy_american_odds,
)


def test_choose_game_requires_unique_multi_player_overlap() -> None:
    now = datetime.now(UTC)
    winner, confidence = choose_game(
        [
            GameCandidate(uuid4(), 2, now),
            GameCandidate(uuid4(), 5, now + timedelta(hours=2)),
        ],
        6,
    )
    assert winner is not None
    assert confidence == 5 / 6


def test_choose_game_rejects_tied_or_single_player_mapping() -> None:
    now = datetime.now(UTC)
    assert choose_game([GameCandidate(uuid4(), 1, now)], 1)[0] is None
    assert (
        choose_game(
            [GameCandidate(uuid4(), 3, now), GameCandidate(uuid4(), 3, now)],
            4,
        )[0]
        is None
    )


def test_lossy_legacy_decimal_prices_are_not_invented_as_american_odds() -> None:
    assert legacy_american_odds(2) is None
    assert legacy_american_odds(None) is None
    assert legacy_american_odds(-110) == -110
