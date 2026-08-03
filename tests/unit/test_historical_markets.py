from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wnba_services.ingestion.historical_markets import GameCandidate, choose_game


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
