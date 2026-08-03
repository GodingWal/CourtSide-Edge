from uuid import UUID, uuid4

from wnba_services.feature_engine.teammate_effects import estimate_effect


def games() -> tuple[list[dict[str, object]], set[UUID]]:
    rows: list[dict[str, object]] = []
    present: set[UUID] = set()
    for index in range(20):
        game_id = uuid4()
        if index < 12:
            present.add(game_id)
        rows.append(
            {
                "game_id": game_id,
                "minutes": 30 if index < 12 else 34,
                "points": 15 if index < 12 else 20,
            }
        )
    return rows, present


def test_effect_is_shrunk_and_bounded() -> None:
    rows, present = games()
    result = estimate_effect(rows, present, ("points",))
    assert result is not None
    assert 1.0 < result.rate_multiplier <= 1.3
    assert 0.0 < result.minutes_delta <= 5.0
    assert 0.0 < result.confidence <= 0.8


def test_effect_requires_both_present_and_absent_samples() -> None:
    rows, _ = games()
    all_present = {UUID(str(row["game_id"])) for row in rows}
    assert estimate_effect(rows, all_present, ("points",)) is None
