from wnba_services.forecasting.baseline import SUPPORTED, _simulate


def _history() -> list[dict[str, object]]:
    return [
        {
            "minutes": 28.0 + (index % 5),
            "points": 12 + (index % 7),
            "rebounds_offensive": 1,
            "rebounds_defensive": 4 + (index % 3),
            "assists": 3 + (index % 4),
            "three_pointers_made": index % 3,
        }
        for index in range(20)
    ]


def test_baseline_simulation_is_reproducible_and_count_valued() -> None:
    first = _simulate(_history(), SUPPORTED["points"], 42)
    second = _simulate(_history(), SUPPORTED["points"], 42)

    assert first == second
    assert len(first) == 10_000
    assert all(isinstance(value, int) and value >= 0 for value in first)


def test_combination_market_includes_all_components() -> None:
    points = _simulate(_history(), SUPPORTED["points"], 7)
    pra = _simulate(_history(), SUPPORTED["points_rebounds_assists"], 7)

    assert sum(pra) / len(pra) > sum(points) / len(points)
